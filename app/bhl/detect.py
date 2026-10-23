"""扫描 CAN 总线：自动判断每条臂接在哪个 CAN 口、哪些电机在线、有没有夹爪。

有了这个，换 USB 顺序、临时少接一个电机、加/减夹爪，都不需要改代码：
能扫到谁就操作谁，扫不到的关节自动忽略（并在日志里说明）。

**Linux** 走 socketcan（can0~can4，由 canup.sh 配置）。
**Windows / macOS** 没有 socketcan，改用 python-can 的厂商驱动，逐个枚举已经插好的
USB-CAN 适配器（pcan / canalystii / kvaser / vector / gs_usb …），再 ping 电机 ID
认出左右臂 —— 逻辑和 Linux 完全一样。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import can

from . import config, libusb, paths
from .bus import ArmBus, release_usb
from .util import quiet

# 排查钩子：worker 会把它设成"写一行里程碑到文件"。之所以要在调用**之前**写，
# 是因为 candle 卡死时会一直攥着 GIL，卡住之后 Python 里任何东西都跑不了
# （faulthandler 的看门狗也救不了），只能靠"最后一条里程碑"反推卡在哪一步。
_MARK = None


def _mark(text: str) -> None:
    if _MARK is None:
        return
    try:
        _MARK(text)
    except Exception:  # noqa: BLE001  排查钩子绝不能影响正常流程
        pass

ROOT = paths.app_root()   # 打包成 exe 后是 exe 所在目录

# 打开/枚举一个适配器最多等多久。Windows 上 USB-CAN 的驱动调用是阻塞式的，
# 设备被别的进程攥住时会**一直不返回**（实测能卡死整个进程），所以统一套个超时。
CALL_TIMEOUT_S = 6.0


class _CallTimeout(RuntimeError):
    """底层驱动调用超时（通常是被别的程序占用了）。"""


def call_with_timeout(func, timeout: float = CALL_TIMEOUT_S):
    """在守护线程里跑 func，最多等 timeout 秒。

    超时抛 `_CallTimeout`（调用本身还在那个线程里挂着，但至少界面不会被冻住）。

    ⚠ 这种超时**救不了原生库死锁**：卡住的线程还攥着 USB 句柄，可能把后续调用
    一起拖死。所以真正要碰 candle 硬件的探测/监听都走 `run_worker()` 子进程，
    这个函数只留给"枚举设备列表"这种一次性调用。
    """
    box: dict = {}

    def runner() -> None:
        try:
            box["value"] = func()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True, name="can-call")
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise _CallTimeout(f"底层 CAN 调用超过 {timeout:.0f} 秒没有返回")
    error = box.get("error")
    if error is not None:
        raise error
    return box.get("value")


def _parse_worker_lines(text: str) -> tuple[dict | None, dict]:
    """从 worker 的 stdout 里挑出 JSON 行，返回 (整批结果, 逐条结果)。

    worker 每扫完一条总线就发一行 {"result": {label: {...}}}，
    全部扫完再发一行 {"results": {...}}。中间可能混着 candle 原生层的
    调试输出（"Found candle usb device ..."），所以不能整段 json.loads。
    """
    payload: dict | None = None
    partial: dict = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(obj, dict):
            continue
        if isinstance(obj.get("result"), dict):
            partial.update(obj["result"])
        elif isinstance(obj.get("results"), dict):
            payload = obj
    return payload, partial


def run_worker(action: str, targets: list["Target"], *, ids=None, timeout=0.05,
               seconds=1.5, limit=60.0) -> tuple[dict | None, str]:
    """把要碰 candle 硬件的活丢给**子进程**干。

    原生库的 `open()` / `reset()` / `list_device()` 卡住时线程杀不掉，
    而子进程可以直接杀掉 —— 进程一死，操作系统自动回收它攥着的 USB 句柄，
    界面、后续扫描都不会被拖死。

    两个防卡死保险：
      1. worker 自己带 `hard_limit` 自杀定时器（exe 的孙进程攥着管道时，
         父进程杀不到它，它到点自行 `os._exit`，管道才能闭合）；
      2. 超时后父进程用 `taskkill /T` 杀整棵进程树，双保险。

    返回 (结果字典, 出错信息)；结果字典形如 {"<label>": {...}}，
    出错信息非空时结果可能不完整。
    """
    request = {
        "action": action,
        "hard_limit": float(limit),
        "targets": [
            {
                "label": t.label,
                "channel": t.channel,
                "driver": t.driver,
                "options": dict(t.options or {}),
            }
            for t in targets
        ],
    }
    if ids is not None:
        request["ids"] = [int(i) for i in ids]
        request["timeout"] = float(timeout)
    if action == "sniff":
        request["seconds"] = float(seconds)

    if getattr(sys, "frozen", False):
        command = [sys.executable, "--can-scan-worker"]
    else:
        script = ROOT / "can_scan.py"
        if not script.exists():
            return None, f"找不到扫描子进程脚本 {script}"
        command = [sys.executable, str(script)]

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def kill_tree(proc) -> None:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10, creationflags=flags,
            )
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            env=env,
            creationflags=flags,
        )
        # worker 到点自杀会先于这里多给的 8 秒，管道能正常闭合
        out, err = process.communicate(
            input=json.dumps(request, ensure_ascii=False),
            timeout=limit + 8.0,
        )
    except subprocess.TimeoutExpired as exc:
        try:
            kill_tree(process)
        except Exception:  # noqa: BLE001
            pass
        raw = exc.output or ""
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        _payload, partial = _parse_worker_lines(raw)
        if partial:
            return partial, f"部分总线超过 {limit:.0f} 秒未返回，已采用已完成的结果"
        return None, f"探测超过 {limit:.0f} 秒没有返回（已强杀子进程树）"
    except OSError as exc:  # noqa: BLE001
        return None, f"起不了扫描子进程：{exc}"

    payload: dict | None = None
    partial: dict = {}
    if out.strip():
        # 不能整段 json.loads：candle 的原生层会往 stdout 打调试信息
        # （比如 "Found candle usb device 1d50:606f, but access denied."），
        # 混在 JSON 前面/后面都会让整段解析失败。逐行取，只认 JSON 行。
        payload, partial = _parse_worker_lines(out)

    worker_error = ""
    if isinstance(payload, dict):
        worker_error = str(payload.get("worker_error") or "")
    if process.returncode != 0:
        merged = dict(partial)
        merged.update((payload or {}).get("results") or {})
        if merged:
            return merged, worker_error or f"子进程异常退出（退出码 {process.returncode}），只拿到部分结果"
        return None, worker_error or (
            f"子进程退出码 {process.returncode}：{(err or out or '')[-300:]}"
        )
    if not isinstance(payload, dict):
        if partial:                      # 只拿到部分结果也算有收获
            return partial, "子进程没有返回完整结果，只拿到部分总线"
        return None, (
            f"子进程输出无法解析：{out[:200]!r} …（共 {len(out)} 字节，"
            f"stderr={((err or '')[:120])!r}）"
        )
    merged = dict(partial)
    merged.update(payload.get("results") or {})
    return merged, worker_error


class DetectError(RuntimeError):
    """没找到任何电机 / CAN 口打不开。"""


@dataclass(frozen=True)
class Target:
    """一个可以打开的 CAN 通道。

    - label 是给人看的名字（"can2" / "PCAN_USBBUS1 (pcan)" / "candleLight#0"）；
    - channel 是真正交给 python-can 的通道值（gs_usb 要的是整数下标）；
    - driver 是 python-can 的 interface 名；
    - options 是额外参数，会原样传给 `can.interface.Bus()`。
    """

    label: str
    channel: object
    driver: str
    options: dict = field(default_factory=dict)
    aliases: tuple[str, ...] = ()

    @property
    def connect_kwargs(self) -> dict:
        """打开这条总线时传给 `can.interface.Bus()` 的参数。"""
        return {**self.options, "channel": self.channel}

    def __iter__(self):
        """兼容老的 `for label, channel, driver in targets` 写法。"""
        return iter((self.label, self.channel, self.driver))


@dataclass
class Setup:
    """扫描结果。"""

    channels: dict[str, list[str]]  # CAN 口 -> 该口上的电机名
    mapping: dict[str, str]  # 侧 -> CAN 口
    online: dict[str, set[int]] = field(default_factory=dict)  # 侧 -> 在线电机 ID
    expected: list[str] = field(default_factory=list)  # 按所选机型"应该"有的关节
    interface: dict[str, str] = field(default_factory=dict)  # 总线标签 -> CAN 驱动名
    open_options: dict[str, dict] = field(default_factory=dict)  # 总线标签 -> 打开总线用的参数

    @property
    def motor_names(self) -> list[str]:
        return config.sort_names([name for names in self.channels.values() for name in names])

    @property
    def has_gripper(self) -> bool:
        return any(config.MOTORS_BY_NAME[name].is_gripper for name in self.motor_names)

    @property
    def missing(self) -> list[str]:
        """按所选机型应该有、但这次没扫到的关节。"""
        if not self.expected:
            return []
        have = set(self.motor_names)
        return [name for name in self.expected if name not in have]

    @property
    def missing_grippers(self) -> list[str]:
        """配置里有、但这次没扫到的夹爪（有就说明夹爪没接或没上电）。"""
        return [name for name in config.all_names() if config.MOTORS_BY_NAME[name].is_gripper and name not in self.motor_names]

    def describe(self) -> str:
        lines = []
        for channel, names in self.channels.items():
            left = sum(1 for n in names if config.MOTORS_BY_NAME[n].side == "left")
            right = len(names) - left
            lines.append(f"{channel}: 左 {left} 个 / 右 {right} 个")

        text = f"检测到 {len(self.motor_names)} 个电机（{'含夹爪' if self.has_gripper else '无夹爪'}）—— " + "；".join(lines)

        offline = [name for name in config.all_names() if name not in self.motor_names]
        if offline:
            text += f"\n未在线、本次将忽略：{', '.join(offline)}"

        if self.missing:
            text += f"\n⚠ 按所选机型应该有但没有响应：{', '.join(self.missing)}"
        return text


# ----------------------------------------------------------------------
# CAN 口状态与初始化
# ----------------------------------------------------------------------
BITRATE = 1_000_000  # 所有 Robstride 电机都是 1 Mbps


def can_status(channel: str) -> dict:
    """返回 {'state': 'up'/'down'/'missing', 'bitrate': int|None}。"""
    try:
        result = subprocess.run(
            ["ip", "-details", "-o", "link", "show", channel],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return {"state": "missing", "bitrate": None}

    if result.returncode != 0:
        return {"state": "missing", "bitrate": None}

    # 形如： 2: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 ... bitrate 1000000 sample-point 0.875
    text = result.stdout
    _, _, rest = text.partition("<")
    flags, _, _ = rest.partition(">")
    state = "up" if "UP" in flags.split(",") else "down"

    bitrate = None
    match = re.search(r"bitrate\s+(\d+)", text)
    if match:
        bitrate = int(match.group(1))

    return {"state": state, "bitrate": bitrate}


def can_state(channel: str) -> str:
    """返回 'up' / 'down' / 'missing'。"""
    return can_status(channel)["state"]


def _can_problem(channel: str) -> str:
    """这个 CAN 口需要重新配置吗？返回空字符串表示没问题，"不存在"表示没有这个口。"""
    status = can_status(channel)
    if status["state"] == "missing":
        return "不存在"
    if status["state"] == "down":
        return "未启动"
    if status["bitrate"] is not None and status["bitrate"] != BITRATE:
        return f"波特率 {status['bitrate']}（应为 {BITRATE}）"
    return ""


def bring_up_can(on_progress=None, channels=None) -> tuple[bool, str]:
    """执行 canup.sh 配置 CAN 口。返回 (是否成功, 输出内容)。

    channels 为空时脚本会配置 can0~can4；指定了就只动这几条。
    脚本内部会自动 sudo：有终端时密码就在终端里输入，免密 sudo 则直接通过。
    """
    say = on_progress or (lambda text: None)
    if not sys.platform.startswith("linux"):
        # Windows/macOS 没有 socketcan，也没有 canup.sh 这一套
        return False, "当前平台不需要 canup.sh（Windows 由适配器驱动自己管理）"

    script = ROOT / "canup.sh"
    if not script.exists():
        return False, f"找不到 {script.name}（应该和 main.py 在同一个目录）"

    say("执行 canup.sh 配置 CAN 口（若提示密码，请在启动本程序的终端里输入）…")
    command = ["bash", str(script), *([str(ch) for ch in channels] if channels else [])]
    try:
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=90,
        )
    except Exception as exc:
        return False, f"执行 {script.name} 失败：{exc}"

    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    return result.returncode == 0, output


def missing_components() -> list[str]:
    """Windows 上枚举不到适配器时，缺哪个组件就说哪个（比"装好驱动"有用）。"""
    if sys.platform.startswith("linux"):
        return []

    problems: list[str] = []
    if not ensure_candle_backend():
        # candleLight / CANable 这类棒子：candle 是唯一实测能发帧的路子，
        # 没有它就只能退到 gs_usb（Windows 上发不出去），所以缺了必须明说。
        if sys.platform.startswith("win"):
            problems.append(
                "缺少 python-can-candle（pip install python-can-candle）——"
                "candleLight / CANable 这类适配器需要它才能收发"
            )

    try:
        import usb  # noqa: F401
    except Exception:  # noqa: BLE001
        problems.append("缺少 pyusb（pip install pyusb），gs_usb 兜底需要它")
    else:
        if not libusb.ensure():
            problems.append(libusb.describe())

    try:
        import gs_usb  # noqa: F401
    except Exception:  # noqa: BLE001
        problems.append("缺少 gs_usb 包（pip install gs_usb），gs_usb 兜底需要它")

    # 注意：这里**不要**再去枚举 candle 设备。枚举要点开 USB 句柄读序列号，
    # 一旦有卡死的旧进程攥着设备，这个调用会把调用方一起阻塞住
    # （历史 bug：诊断报告写完、进程却再也退不出去）。
    # "被别的程序独占"的判断放在 ensure_can() 里——那里已经知道 candle 一只都没认到。
    return problems


def ensure_can(channels=None, on_progress=None, interface: str | None = None):
    """确保 CAN 总线可用。返回 (是否可用, 提示信息)。

    Linux 上会检查并在需要时执行 canup.sh；Windows / macOS 上不需要这一步，
    只确认 python-can 能枚举到适配器（驱动是否装好）。
    """
    say = on_progress or (lambda text: None)

    if not sys.platform.startswith("linux"):
        targets = list_targets(interface)
        if not targets:
            driver = (interface or "auto").strip()
            hint = f"（当前指定驱动：{driver}）" if driver and driver != "auto" else ""
            messages = [
                f"没有检测到可用的 CAN 适配器{hint}",
                "请确认：USB-CAN 已插好、厂商驱动已安装；"
                "也可用 --interface 指定驱动（pcan / canalystii / slcan / gs_usb / kvaser …）",
            ]
            messages += [f"  - {item}" for item in missing_components()]
            # USB 上明明插着 candleLight，candle 却一只都没认到 → 被别的进程独占了。
            # GsUsb.scan() 只做 libusb 描述符枚举，不打开设备，不会被卡死的句柄拖住。
            if sys.platform.startswith("win"):
                try:
                    from gs_usb.gs_usb import GsUsb

                    plugged = len(GsUsb.scan()) if libusb.ensure() else 0
                except Exception:  # noqa: BLE001
                    plugged = 0
                if plugged:
                    messages.append(
                        f"USB 上插着 {plugged} 只 candleLight，candle 却一只都没认到 —— "
                        "多半被别的程序占着（另一个实例 / 别人的上位机 / 卡死的旧进程）。"
                        "把它们都关掉；还不行就把适配器拔插一次（卡在驱动里的进程 Windows 杀不掉）"
                    )
            return False, messages
        say("检测到 CAN 适配器：" + "，".join(target.label for target in targets))
        return True, []

    targets = list(dict.fromkeys(channels or config.CANDIDATE_CHANNELS))

    def survey() -> dict[str, str]:
        return {channel: _can_problem(channel) for channel in targets}

    problems = survey()
    actionable = {ch: why for ch, why in problems.items() if why and why != "不存在"}

    if actionable:
        detail = "，".join(f"{ch} {why}" for ch, why in actionable.items())
        say(f"CAN 口需要配置（{detail}）")
        ok, output = bring_up_can(on_progress=say, channels=targets)
        for line in output.splitlines():
            if line.strip():
                say("  " + line.strip())
        if not ok:
            say("canup.sh 没有成功执行完")
        problems = survey()

    remaining = [f"{ch} {why}" for ch, why in problems.items() if why and why != "不存在"]

    if not any(can_status(channel)["state"] == "up" for channel in targets):
        return False, ["没有可用的 CAN 口，请手动执行：bash app/canup.sh"] + remaining

    return True, remaining


# ----------------------------------------------------------------------
# 枚举适配器
# ----------------------------------------------------------------------
def ensure_candle_backend() -> bool:
    """确保 python-can 认识 `candle` 这个后端。

    candleLight / CANable 这类开源固件棒有两条路可走：
      - `gs_usb`（pyusb + libusb）：实测在 Windows 上发帧会 1 秒超时，基本不可用；
      - `candle`（C 库，python-can 的独立插件包 `python-can-candle`）：实测稳定。

    正常情况下 python-can 通过 entry point 自动注册 `candle`；但打包成 exe 后
    元数据可能没带上，所以这里再手工兜一层，让 exe 里也一定能用。
    """
    try:
        import can.interfaces as interfaces

        if "candle" in interfaces.BACKENDS:
            return True
        from candle.candle_bus import CandleBus  # noqa: F401

        interfaces.BACKENDS["candle"] = ("candle.candle_bus", "CandleBus")
        return True
    except Exception:  # noqa: BLE001 - 没装 python-can-candle
        return False


# 当前进程是不是 can_scan 子进程（防止子进程里再递归派发子进程）
_IN_CAN_WORKER = False


def _candle_targets() -> list[Target]:
    """枚举 candle 后端能看到的设备（candleLight / CANable）。

    返回的 channel 形如 `"0038003E5547570820383634:0"`（序列号:通道号），
    **按序列号寻址**，所以换 USB 口、换枚举顺序都不会找错设备。
    """
    if not ensure_candle_backend():
        return []

    # 枚举会调原生 `candle_api.list_device()`，它卡住时线程杀不掉；
    # 所以父进程里把枚举丢给子进程做（超时直接杀），子进程里才直接调。
    if not _IN_CAN_WORKER and not sys.platform.startswith("linux"):
        raw, _error = run_worker("list", [], limit=20.0)
        if isinstance(raw, dict) and isinstance(raw.get("targets"), list):
            targets: list[Target] = []
            for item in raw["targets"]:
                if isinstance(item, dict) and item.get("label"):
                    channel = item.get("channel")
                    serial = str(channel).split(":")[0] if channel else ""
                    targets.append(
                        Target(
                            label=str(item["label"]),
                            channel=channel,
                            driver="candle",
                            options=dict(item.get("options") or {}),
                            # 必须带上别名：上层会用"序列号:通道"去点名要扫哪几只
                            # （记住的左右臂适配器就是按通道存的），
                            # 没有别名就会查不到而退回默认驱动（Windows 上是 pcan）。
                            aliases=tuple(
                                alias
                                for alias in (serial, channel)
                                if alias
                            ),
                        )
                    )
            return targets
        # 子进程这条路没通（比如源码模式找不到脚本）：退回进程内枚举，有超时兜底

    try:
        found = call_with_timeout(
            lambda: can.detect_available_configs(interfaces=["candle"])
        )
    except Exception:  # noqa: BLE001 - 超时 / 驱动报错都当作"这台机器上没有"
        return []

    channels = sorted(
        {str(item["channel"]) for item in (found or []) if item.get("channel")}
    )
    targets = []
    for index, channel in enumerate(channels):
        serial = channel.split(":")[0]
        label = f"candleLight#{index} ({serial[:8] or '?'})"
        targets.append(
            Target(
                label=label,
                channel=channel,
                driver="candle",
                options={"bitrate": BITRATE},
                aliases=(serial, channel, f"candle:{index}", str(index)),
            )
        )
    return targets


def _gs_usb_targets() -> list[Target]:
    """枚举 gs_usb 设备（candleLight / CANable / canable2 …）。

    python-can 的 `gs_usb` 后端**没有实现** `detect_available_configs()`，
    所以 `can.detect_available_configs()` 永远看不到这类适配器 ——
    而这恰恰是 Windows 上最便宜、用得最多的一种 USB-CAN。
    这里直接问 pyusb，顺序与 `GsUsb.scan()` 完全一致，下标可以直接当 channel。

    ⚠ 实测这条件在 Windows 上**发帧会一直 USB 超时**（`The message could not be
    sent`），只在没有 candle 后端时作为兜底。
    """
    try:
        from gs_usb.gs_usb import GsUsb
    except Exception:  # noqa: BLE001 - 没装 gs_usb 包
        return []

    if not libusb.ensure():
        return []

    try:
        devices = GsUsb.scan()
    except Exception:  # noqa: BLE001 - 没插 / 驱动没绑 WinUSB
        return []

    targets: list[Target] = []
    for index, device in enumerate(devices):
        try:
            serial = str(device.serial_number or "").strip()
        except Exception:  # noqa: BLE001
            serial = ""
        try:
            where = f"{device.bus}:{device.address}"
        except Exception:  # noqa: BLE001
            where = ""
        label = f"gs_usb#{index}" + (f" [{where}]" if where else "")
        aliases = [str(index), f"gs_usb:{index}"]
        if serial:
            aliases.append(serial)
        targets.append(
            Target(
                label=label,
                channel=index,
                driver="gs_usb",
                options={"bitrate": BITRATE},
                aliases=tuple(aliases),
            )
        )
    return targets


_TARGETS_CACHE: dict[str, tuple[float, list[Target]]] = {}
TARGETS_TTL_S = 30.0


def list_targets(interface: str | None = None, *, refresh: bool = False) -> list[Target]:
    """列出可用的 CAN 总线（带 30 秒缓存）。

    USB 设备枚举在 Windows 上不算便宜（candle 要逐个打开设备读序列号），
    而且界面启动时会连着问好几次（检查 → 扫描 → 出错时再解释一遍）。
    缓存能明显减少"正在连接…"的等待。refresh=True 强制重查（拔插后用）。
    """
    key = (interface or "auto").strip().lower()
    cached = _TARGETS_CACHE.get(key)
    if not refresh and cached is not None and (time.perf_counter() - cached[0]) < TARGETS_TTL_S:
        return list(cached[1])

    targets = _enumerate_targets(interface)
    _TARGETS_CACHE[key] = (time.perf_counter(), targets)
    return list(targets)


def _enumerate_targets(interface: str | None) -> list[Target]:
    """真正去问驱动有哪些适配器。"""
    wanted = (interface or "auto").strip()
    explicit = wanted.lower() not in ("", "auto")

    # Linux 上 socketcan 直接用固定候选口（这样还能顺便查 ip link 状态）
    if sys.platform.startswith("linux") and wanted.lower() in ("", "auto", "socketcan"):
        return [Target(ch, ch, "socketcan") for ch in config.CANDIDATE_CHANNELS]

    targets: list[Target] = []
    seen: set[tuple[str, str]] = set()

    def add(target: Target) -> None:
        driver = str(target.driver or "").lower()
        if not driver:
            return
        if driver in config.VIRTUAL_CAN_DRIVERS:
            return
        key = (target.label, driver)
        if key in seen:
            return
        seen.add(key)
        targets.append(target)

    # 1) candle：先手工注册后端，保证 exe 里也认识
    candle_ready = ensure_candle_backend() and (not explicit or "candle" in [wanted.lower()])

    # 通用枚举会把所有已注册后端挨个试一遍，其中 candle 的原生调用会阻塞 ——
    # 它在上面单独走子进程，这里把它从名单里剔掉，父进程绝不直接碰它。
    drivers = [wanted] if explicit else None
    try:
        with quiet():   # 枚举会尝试加载所有驱动，往 stderr 刷一堆"不可用"的警告
            if drivers:
                probe_drivers = [d for d in drivers if d.lower() != "candle"]
            else:
                probe_drivers = [
                    name for name in can.interfaces.BACKENDS if name != "candle"
                ]
            found = call_with_timeout(
                lambda: can.detect_available_configs(interfaces=probe_drivers)
            )
    except Exception:
        found = []

    for item in found or []:
        driver = str(item.get("interface", "")).lower()
        channel = item.get("channel")
        if not driver or channel is None:
            continue
        if driver == "candle":
            continue        # 下面用统一的标签格式重建（这里保持顺序用的旧名）
        label = str(channel) if driver == "socketcan" else f"{channel} ({driver})"
        add(Target(label=label, channel=channel, driver=driver))

    candle_targets: list[Target] = []
    if candle_ready:
        candle_targets = _candle_targets()
        for target in candle_targets:
            add(target)

    # 2) gs_usb：python-can 自己枚举不到，单独扫 USB。
    #    只有在没有 candle 可用（或用户显式点名 gs_usb）时才补，避免同一批
    #    硬件出现两遍、而且挑中那条走不通的路。
    if explicit:
        if "gs_usb" in drivers:
            for target in _gs_usb_targets():
                add(target)
    elif not candle_targets:
        for target in _gs_usb_targets():
            add(target)

    return targets


def _target_index(targets: list[Target]) -> dict[str, Target]:
    """标签 + 别名 -> Target（`--channels` 可以用任一名字）。"""
    index: dict[str, Target] = {}
    for target in targets:
        index[target.label] = target
        for alias in target.aliases:
            index.setdefault(alias, target)
    return index


def parse_channels(spec: str) -> dict[str, str]:
    """把 --channels can2,can1 解析成 {"left": "can2", "right": "can1"}。

    只给一个口表示两条臂共用同一条总线。
    """
    parts = [part.strip() for part in spec.replace(";", ",").split(",") if part.strip()]
    if not parts:
        raise ValueError("--channels 至少要给一个 CAN 口，例如 can0,can1")
    if len(parts) == 1:
        return {"left": parts[0], "right": parts[0]}
    if len(parts) == 2:
        return {"left": parts[0], "right": parts[1]}
    raise ValueError("--channels 只支持 1 个或 2 个 CAN 口")


# ----------------------------------------------------------------------
# 只收不发
# ----------------------------------------------------------------------
def sniff(target: Target, seconds: float = 1.5) -> tuple[dict[int, int], int]:
    """在这条总线上**只听不说**，返回 ({仲裁号: 帧数}, 错误帧数)。

    这是排查"扫不到电机"最有用的一步：发不出去（总线上没人 ACK）时，
    收还是能收的 —— 只要机械臂上电、线接好，它的状态帧就会一直往外冒。

    三种典型结果：
      - 全静音：电缆没插 / 对面没上电；
      - 全是**错误帧**：电缆插了，但对面没有节点应答（没上电、缺 120Ω 终端、
        或者 CAN_H/CAN_L 反了）—— 适配器自己在反复报错；
      - 有扩展帧：总线活着，能看到电机在说话。
    """
    counts: dict[int, int] = {}
    errors = 0

    def note(msg) -> None:
        nonlocal errors
        if msg is None or not getattr(msg, "is_rx", True):
            return  # 适配器会把自己发出去的帧回显回来，不算数
        if getattr(msg, "is_error_frame", False):
            errors += 1
            return
        counts[msg.arbitration_id] = counts.get(msg.arbitration_id, 0) + 1

    try:
        with quiet():
            handler = call_with_timeout(
                lambda: can.interface.Bus(interface=target.driver, **target.connect_kwargs)
            )
    except Exception:  # noqa: BLE001 - 打不开 / 超时，就当它一声不吭
        return counts, errors

    try:
        deadline = time.perf_counter() + seconds
        while True:
            remain = deadline - time.perf_counter()
            if remain <= 0:
                break
            note(handler.recv(timeout=min(0.2, remain)))
    finally:
        # 子进程里**不做**优雅关闭：candle 的 shutdown()/libusb_close() 在总线
        # 持续报错时会阻塞在原生代码里，而且阻塞期间攥着 GIL —— 连 _bounded 的
        # 超时线程都醒不过来，整个 worker 就被冻死了（界面看到的"扫描卡 80 秒"）。
        # worker 马上要退出，句柄由操作系统回收，不需要这一步。
        if not _IN_CAN_WORKER:
            try:
                handler.shutdown()
            except Exception:
                pass
            release_usb(handler)

    return counts, errors


def sniff_many(targets: list[Target], seconds: float = 1.5) -> dict[str, tuple[dict[int, int], int]]:
    """对一批总线做静默监听，返回 {标签: ({仲裁号: 帧数}, 错误帧数)}。

    有 candle 参与时整批丢给子进程（一次启动），卡死就杀，不会拖死界面；
    其它后端本进程内循环即可。
    """
    out: dict[str, tuple[dict[int, int], int]] = {}
    if not targets:
        return out

    use_worker = (
        not sys.platform.startswith("linux")
        and any(str(getattr(t, "driver", "")) == "candle" for t in targets)
    )
    if use_worker:
        results, _error = run_worker(
            "sniff", targets, seconds=seconds, limit=max(20.0, seconds * len(targets) + 10.0)
        )
        if isinstance(results, dict):
            for target in targets:
                entry = results.get(target.label)
                frames: dict[int, int] = {}
                errors = 0
                if isinstance(entry, dict):
                    try:
                        frames = {int(k): int(v) for k, v in (entry.get("frames") or {}).items()}
                        errors = int(entry.get("errors") or 0)
                    except (TypeError, ValueError):
                        frames, errors = {}, 0
                out[target.label] = (frames, errors)
            return out

    for target in targets:
        out[target.label] = sniff(target, seconds=seconds)
    return out


# ----------------------------------------------------------------------
# 电机扫描
# ----------------------------------------------------------------------
def _probe(target: Target, ids: list[int], timeout: float) -> tuple[set[int], str]:
    """ping 一个通道上的若干 ID。

    返回 (真正响应的 ID 集合, 打不开的原因)；第二个元素为空字符串表示打开成功。
    """
    try:
        with quiet():  # 构造和 connect 都会往 stdout 打无关信息
            bus = ArmBus(
                channel=target.label,
                motors={},
                interface=target.driver,
                open_kwargs=target.connect_kwargs,
            )
            # 探测阶段在子进程里跑，单条总线开不起来就别等太久（3 秒够了）
            _mark(f"open {target.label}")
            call_with_timeout(lambda: bus.connect(handshake=False), timeout=3.0)
            _mark(f"opened {target.label}")
    except _CallTimeout:
        return set(), "打开超时（这个适配器可能被别的程序占用了）"
    except Exception as exc:  # noqa: BLE001 - 该 CAN 口不存在 / 打不开
        return set(), f"{type(exc).__name__}: {exc}"

    found: set[int] = set()
    misses = 0
    stalls = 0
    error = ""
    try:
        _mark(f"ping {target.label}")
        for device_id in ids:
            hit = False
            for _attempt in range(2):      # USB-CAN 偶尔丢一帧，重试一次再判"没有"
                try:
                    with quiet():
                        started = time.perf_counter()
                        response = bus.ping_by_id(device_id, timeout=timeout)
                        spent = time.perf_counter() - started
                except Exception as exc:   # 发不出去就不用再试别的 ID 了
                    error = f"{type(exc).__name__}: {exc}"
                    break
                # 总线上没人 ACK 时，candle 固件会把这一帧重发到 USB 超时才返回（约 1 秒），
                # 而有节点 ACK 时发一帧只要几十微秒。连续两次卡住且一无所获 = 这条线是死的，
                # 不必再把剩下的 ID 挨个试一遍（16 个 ID × 1 秒会让扫描拖到一分半）。
                # 总线上没人 ACK 时，candle 发一帧要等满 CAN_SEND_TIMEOUT 才返回
                # （默认 1 秒，已收到 0.2 秒），而有节点 ACK 时只要几十微秒。
                # 连续两次卡住且一无所获 = 这条线是死的，不必再把剩下的 ID 挨个试。
                if spent > 0.15:
                    stalls += 1
                    if not found and stalls >= 2:
                        error = "发帧超时（总线上没有节点应答：没上电 / 线没接 / 缺终端电阻）"
                        break
                # 空闲/回环的 CAN 口会对任何 ping 用 device_id=0 回应，
                # 所以必须校验回帧里的 ID 和所 ping 的一致，否则一律不算命中。
                if response is not None and response[0] == device_id:
                    hit = True
                    break
            if error:
                break
            if hit:
                found.add(device_id)
                misses = 0
            else:
                misses += 1
                # 注意：只有"这条总线已经有收获"时才允许提前放弃。
                # 右臂电机 ID 是 21-28，而探测顺序是 11-18 再 21-28：
                # 在右臂那条总线上，前 6 个（11-16）必然全都不响，
                # 若无条件 break，永远走不到 21-28，右臂会被判成"没有电机应答"。
                if found and misses >= 6:
                    break
    finally:
        # worker 里分两步区别对待：
        #   · shutdown() 只做通道 reset，把固件放回干净状态 —— 必须做，
        #     否则上一轮留下的半开状态会累积，几次之后连 open 都会卡住；
        #   · release_usb()（libusb_close）在报错总线上会永久阻塞且攥着 GIL，
        #     谁等谁死，所以跳过；进程马上 _exit，句柄由系统回收。
        handler = getattr(bus, "channel_handler", None)
        if handler is not None and _IN_CAN_WORKER:
            try:
                handler.shutdown()
            except Exception:
                pass
        elif not _IN_CAN_WORKER:
            try:
                with quiet():
                    bus.disconnect(disable_torque=False)
            except Exception:
                pass

    return found, error


_LAST_GOOD_FILE = ".last-good-buses.json"


def _load_good_channels() -> list[str]:
    """上次扫描时真正有电机的总线通道（"序列号:0"）。

    用通道而不是标签：标签里的 candleLight#N 是枚举序号，插拔后会变，
    通道（序列号）才是稳定的。
    """
    try:
        raw = json.loads((paths.app_root() / _LAST_GOOD_FILE).read_text(encoding="utf-8-sig"))
        return [str(item) for item in (raw.get("channels") or [])]
    except Exception:  # noqa: BLE001
        return []


def _save_good_channels(channels) -> None:
    try:
        (paths.app_root() / _LAST_GOOD_FILE).write_text(
            json.dumps({"channels": list(channels)}, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


def scan(
    candidates=None,
    *,
    forced: dict[str, str] | None = None,
    expect: str | None = None,
    interface: str | None = None,
    timeout: float = 0.04,
    refresh: bool = False,
    on_progress=None,
    on_step=None,
) -> Setup:
    """扫描总线并给出可直接交给 Arm 的配置。

    forced 不为空时用它指定左右臂的通道（仍会扫描，只把没响应的电机剔除）。
    expect 是机器人类型（"gripper" / "no_gripper" / None）。
    interface 是 CAN 驱动（Linux 默认 socketcan；Windows 见 list_targets）。

    on_progress(text)        文字进度（写进日志）
    on_step(done, total)     结构化进度（界面进度条）：每扫完一条总线回调一次
    """
    say = on_progress or (lambda text: None)
    step = on_step or (lambda done, total: None)

    available = list_targets(interface, refresh=refresh)
    by_label = _target_index(available)

    if forced:
        mapping = dict(forced)
        targets = []
        for label in dict.fromkeys(mapping.values()):
            targets.append(
                by_label.get(label)
                or Target(label=label, channel=label, driver=config.default_can_interface())
            )
    else:
        if candidates:
            # candidates 既可以是标签（candleLight#0 (序列号)），也可以是通道
            # （"序列号:0"）—— "记住左右臂插在哪两只" 就是按通道存的。
            by_channel = {str(item.channel): item for item in available}
            targets = []
            for name in candidates:
                found_target = by_label.get(name) or by_channel.get(str(name))
                if found_target is None:
                    found_target = Target(
                        label=str(name),
                        channel=name,
                        driver=config.default_can_interface(),
                    )
                targets.append(found_target)
        else:
            targets = available
        mapping = {}

    # 上次扫到电机的总线排前面：某条坏线把 candle 卡死时，手臂那两条
    # 已经先出结果了（worker 是逐条回传的），不会一起陪葬。
    good = _load_good_channels()
    if good and len(targets) > 1:
        targets = sorted(
            targets, key=lambda item: 0 if str(item.channel) in good else 1
        )

    if not targets:
        raise DetectError(
            "没有可用的 CAN 总线。Linux 请确认已执行 bash app/canup.sh；"
            "Windows 请确认 USB-CAN 适配器驱动已安装，或用 --interface 指定驱动。"
            + "".join(f"\n  - {item}" for item in missing_components())
        )

    id_of_side = {
        side: [spec.can_id for spec in config.MOTORS if spec.side == side] for side in config.SIDES
    }
    probe_ids = sorted({can_id for ids in id_of_side.values() for can_id in ids})

    hits: dict[str, set[int]] = {}
    drivers: dict[str, str] = {}
    options: dict[str, dict] = {}
    problems: list[str] = []

    step(0, len(targets))

    # candle 硬件的探测放到子进程里做：原生库卡死时直接杀进程，界面不会被拖死
    use_worker = (
        not sys.platform.startswith("linux")
        and any(str(getattr(t, "driver", "")) == "candle" for t in targets)
    )
    worker_results: dict | None = None
    worker_error = ""
    if use_worker:
        say("candle 硬件在子进程里探测（卡住会自动杀掉重来，不会拖死界面）…")
        # 每条总线最坏情况：打开 + ping 16 个 ID + 关闭。卡在原生调用上时，
        # 父进程只能等超时；worker 内部还有 faulthandler 看门狗会在 limit-5 秒
        # 自己硬退出并留下栈，所以这里不用给太长。
        worker_limit = max(20.0, 5.0 * len(targets) + 6.0)
        worker_results, worker_error = run_worker(
            "probe", targets, ids=probe_ids, timeout=timeout, limit=worker_limit,
        )

    for index, target in enumerate(targets, start=1):
        say(f"扫描 {target.label} ...")
        if use_worker:
            entry = worker_results.get(target.label) if isinstance(worker_results, dict) else None
            if isinstance(entry, dict):
                found = set(entry.get("found") or [])
                error = entry.get("error") or ""
            else:
                found = set()
                error = worker_error or "子进程没有返回这条总线的结果"
        else:
            found, error = _probe(target, probe_ids, timeout)
        hits[target.label] = found
        drivers[target.label] = target.driver
        options[target.label] = target.connect_kwargs
        if error:
            problems.append(f"{target.label} 用不了：{error}")
            say(f"  {target.label} 用不了：{error}")
        elif found:
            say(f"  {target.label} 上发现 {len(found)} 个电机：{sorted(found)}")
        else:
            say(f"  {target.label} 可以打开，但没有电机应答")
        step(index, len(targets))

    if not forced:
        # 每条臂各挑命中最多的那条总线；两条臂可能落在同一条上
        for side in config.SIDES:
            best_label, best_hits = None, -1
            for label in hits:
                count = len(hits[label] & set(id_of_side[side]))
                if count > best_hits:
                    best_label, best_hits = label, count
            if best_label is not None and best_hits > 0:
                mapping[side] = best_label

    if not mapping:
        raise DetectError(
            "没有扫到任何电机。请检查：CAN 线是否插好、机器人是否上电、"
            "是否已执行 bash app/canup.sh（Linux）或装好适配器驱动（Windows）。"
        )

    expected = config.names_for(expect)
    wanted = set(expected)

    online: dict[str, set[int]] = {}
    groups: dict[str, list[str]] = {}
    for side in config.SIDES:
        label = mapping.get(side)
        if label is None:
            continue
        found = hits.get(label, set()) & set(id_of_side[side])
        online[side] = found
        for spec in config.MOTORS:
            if spec.side == side and spec.can_id in found and spec.name in wanted:
                groups.setdefault(label, []).append(spec.name)

    if not groups:
        raise DetectError("找到了总线，但没有任何电机响应。请确认机器人已上电。")

    channels_clean = {label: config.sort_names(names) for label, names in groups.items()}
    _save_good_channels(
        [str(options[label].get("channel")) for label in channels_clean if options.get(label)]
    )  # 下次优先扫这几条
    return Setup(
        channels=channels_clean,
        mapping=mapping,
        online=online,
        expected=expected,
        interface={label: drivers.get(label, config.default_can_interface()) for label in channels_clean},
        open_options={label: options.get(label, {"channel": label}) for label in channels_clean},
    )
