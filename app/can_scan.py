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


def main_worker() -> int:
    """读 stdin 的请求、干活、把结果写 stdout。"""
    try:
        request = json.loads(sys.stdin.read())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"results": {}, "worker_error": f"bad request: {exc}"}))
        return 2

    # 自杀定时器：即使父进程被杀/失去联系，这个 worker 也必须在 hard_limit 内退出。
    # 否则（PyInstaller 单文件 exe 的孙进程）会攥着 stdout 管道不撒手，
    # 父进程的 communicate() 永远等不到 EOF —— 这就是"扫描卡死"的最后一环。
    hard_limit = float(request.get("hard_limit", 0) or 0)
    if hard_limit > 0:
        import os as _os
        import threading as _threading

        timer = _threading.Timer(hard_limit, lambda: _os._exit(3))
        timer.daemon = True   # 注意：Timer.__init__ 不接收 daemon 参数，要后设
        timer.start()

    from bhl import detect
    from bhl.detect import Target

    detect._IN_CAN_WORKER = True        # 防止子进程里再递归派发子进程

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
                found, error = detect._probe(target, ids, timeout)
                results[target.label] = {"found": sorted(found), "error": error}
                # 同一只 candle 设备快速开/关容易把固件打懵（表现为下次枚举
                # 漏掉这只设备），每条总线之间缓一口气。
                time.sleep(0.15)
        elif action == "sniff":
            targets = [rebuild(t) for t in request.get("targets") or []]
            seconds = float(request.get("seconds", 1.5))
            for target in targets:
                counts, errors = detect.sniff(target, seconds=seconds)
                results[target.label] = {
                    "frames": {str(arb): count for arb, count in counts.items()},
                    "errors": errors,
                }
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
    return 0


if __name__ == "__main__":
    # 源码模式下父进程直接跑这个文件；把 app/ 加进 import 路径
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    sys.exit(main_worker())
