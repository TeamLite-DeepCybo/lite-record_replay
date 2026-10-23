#!/usr/bin/env python3
"""CAN 扫描/监听的子进程入口。

`candle_api` 是原生库，`list_device()` / `open()` / `reset()` 在设备状态不对时会
**永久阻塞** —— Python 线程杀不掉，卡住的线程还会攥着 USB 句柄把整个进程拖死。

所以所有要碰 candle 硬件的操作（探电机 / 静默监听）都放进这个子进程：
父进程用 `subprocess.run(..., timeout=…)` 调用，超时就杀进程 —— 操作系统会替我们
把设备句柄收回去，界面永远不会被冻住。

协议（stdin 一行 JSON → stdout 一行 JSON）：

    {"action":"probe","targets":[{"label","channel","driver","options"}],
     "ids":[11,12,...],"timeout":0.05}
    → {"results":{"<label>":{"found":[11,...],"error":""}}}

    {"action":"sniff","targets":[...],"seconds":1.5}
    → {"results":{"<label>":{"<仲裁号十进制字符串>":帧数}}}

打包成 exe 后不直接跑这个文件，而是 `LiteRecordReplay.exe --can-scan-worker`，
逻辑一样（main.py 里转调到这里）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def _app_dir() -> Path:
    """可写目录：exe 旁边，或源码模式的 app/ 目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _emit(obj: dict) -> None:
    """立刻把一条结果写出去。

    逐条输出而不是最后一次性输出：某条总线把 candle 卡死时，父进程仍然能
    拿到它前面已经扫完的结果（以前是全军覆没）。
    """
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _make_marker():
    """返回一个"写里程碑"的函数，用来定位卡死在哪个原生调用里。

    每条里程碑都立刻 flush + fsync 落盘：卡死之后进程里什么都跑不了，
    只有已经落盘的内容能留下来。
    """
    import os as _os

    # 每个 worker 一个文件：父进程会连着起好几个 worker（枚举 → 探测 → 监听），
    # 共用一个文件的话，后一个 worker 会把前一个留下的现场截断（这个坑踩过）。
    path = _app_dir() / f"worker-watch-{_os.getpid()}.txt"
    try:
        handle = open(path, "w", encoding="utf-8", buffering=1)
    except Exception:  # noqa: BLE001
        return None

    def mark(text: str) -> None:
        try:
            handle.write(f"{time.strftime('%H:%M:%S')}  {text}\n")
            handle.flush()
            _os.fsync(handle.fileno())
        except Exception:  # noqa: BLE001
            pass

    return mark


def _boot_mark(text: str) -> None:
    """worker 启动阶段的里程碑（追加写，带 PID 区分是哪个 worker）。"""
    import os as _os

    try:
        with open(_app_dir() / "worker-boot.txt", "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%H:%M:%S')}  pid={_os.getpid()}  {text}\n")
            handle.flush()
            _os.fsync(handle.fileno())
    except Exception:  # noqa: BLE001
        pass


def main_worker() -> int:
    """读 stdin 的请求、干活、把结果写 stdout。"""
    _boot_mark("worker 启动")
    try:
        request = json.loads(sys.stdin.read())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"results": {}, "worker_error": f"bad request: {exc}"}))
        return 2
    _boot_mark("请求已读取")

    # 自杀定时器 + 栈转储：即使父进程杀不到，这个 worker 也必须在 hard_limit 内退出。
    # 用 faulthandler 而不是普通 Timer：candle 卡在原生代码里时，普通 Timer 线程
    # 可能拿不到 GIL；faulthandler 的看门狗是 C 级线程，能把卡死的位置写进文件，
    # 顺便 os._exit 掉，免得留下"杀不掉又占着设备"的僵尸。
    hard_limit = float(request.get("hard_limit", 0) or 0)
    if hard_limit > 0:
        import faulthandler

        hang_log = _app_dir() / "worker-hang.txt"
        try:
            _dump = open(hang_log, "w", encoding="utf-8")
        except Exception:  # noqa: BLE001
            _dump = None
        try:
            faulthandler.dump_traceback_later(
                max(5.0, hard_limit - 5.0), exit=True, file=_dump, repeat=False
            )
        except Exception:  # noqa: BLE001
            pass

    from bhl import detect
    from bhl.detect import Target
    _boot_mark("bhl.detect 已导入（candle_api 初始化完成）")

    detect._IN_CAN_WORKER = True        # 防止子进程里再递归派发子进程
    detect._MARK = _make_marker()       # 卡死定位用，扫完就留在 worker-watch.txt

    def rebuild(item: dict) -> Target:
        return Target(
            label=str(item.get("label")),
            channel=item.get("channel"),
            driver=str(item.get("driver", "")),
            options=dict(item.get("options") or {}),
            aliases=tuple(item.get("aliases") or ()),
        )

    results: dict = {}
    action = request.get("action")

    try:
        if action == "probe":
            targets = [rebuild(t) for t in request.get("targets") or []]
            ids = [int(i) for i in request.get("ids") or []]
            timeout = float(request.get("timeout", 0.05))
            for target in targets:
                _boot_mark(f"开始探测 {target.label}")
                found, error = detect._probe(target, ids, timeout)
                _boot_mark(f"探测完成 {target.label} found={sorted(found)} error={error!r}")
                entry = {"found": sorted(found), "error": error}
                results[target.label] = entry
                _emit({"result": {target.label: entry}})   # 逐条送出，卡住也不丢前面的
                # 同一只 candle 设备快速开/关容易把固件打懵（表现为下次枚举
                # 漏掉这只设备），每条总线之间缓一口气。
                time.sleep(0.15)
        elif action == "sniff":
            targets = [rebuild(t) for t in request.get("targets") or []]
            seconds = float(request.get("seconds", 1.5))
            for target in targets:
                counts, errors = detect.sniff(target, seconds=seconds)
                entry = {
                    "frames": {str(arb): count for arb, count in counts.items()},
                    "errors": errors,
                }
                results[target.label] = entry
                _emit({"result": {target.label: entry}})
        elif action == "list":
            items = []
            for target in detect._candle_targets():
                items.append({
                    "label": target.label,
                    "channel": target.channel,
                    "driver": target.driver,
                    "options": dict(target.options or {}),
                })
            results = {"targets": items}
        else:
            print(json.dumps({"results": {}, "worker_error": f"unknown action {action}"}))
            return 2
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"results": results, "worker_error": f"{type(exc).__name__}: {exc}"}))
        return 1

    print(json.dumps({"results": results}, ensure_ascii=False))
    _exit_hard()
    return 0


def _exit_hard() -> None:
    """把结果交出去以后直接硬退出，不跑 atexit、不关 USB 句柄。

    原因：candle 的关闭路径（shutdown / libusb_close）在总线持续报错时会
    阻塞在原生代码里，而且阻塞期间攥着 GIL —— 进程随后既不能正常退出，
    Windows 的 TerminateProcess 也收不回它（线程卡在内核态 I/O 里），
    最后变成"杀不掉、又占着设备"的僵尸，把后面每一次扫描都拖死。
    结果已经 flush 出去了，直接 _exit 让系统回收句柄是最干净的。
    """
    import os as _os

    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    _os._exit(0)


if __name__ == "__main__":
    # 源码模式下父进程直接跑这个文件；把 app/ 加进 import 路径
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    sys.exit(main_worker())
