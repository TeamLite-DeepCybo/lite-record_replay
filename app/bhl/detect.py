"""扫描 CAN 总线：自动判断每条臂接在哪个 CAN 口、哪些电机在线、有没有夹爪。

有了这个，换 USB 顺序、临时少接一个电机、加/减夹爪，都不需要改代码：
能扫到谁就操作谁，扫不到的关节自动忽略（并在日志里说明）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import can

from . import config, paths
from .bus import ArmBus
from .util import quiet

ROOT = paths.app_root()   # 打包成 exe 后是 exe 所在目录


class DetectError(RuntimeError):
    """没找到任何电机 / CAN 口打不开。"""


@dataclass
class Setup:
    """扫描结果。"""

    channels: dict[str, list[str]]  # CAN 口 -> 该口上的电机名
    mapping: dict[str, str]  # 侧 -> CAN 口
    online: dict[str, set[int]] = field(default_factory=dict)  # 侧 -> 在线电机 ID
    expected: list[str] = field(default_factory=list)  # 按所选机型"应该"有的关节
    interface: dict[str, str] = field(default_factory=dict)  # 总线标签 -> CAN 驱动名

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
            return False, [
                f"没有检测到可用的 CAN 适配器{hint}",
                "请确认：USB-CAN 已插好、厂商驱动已安装；"
                "也可用 --interface 指定驱动（pcan / canalystii / slcan / gs_usb / kvaser …）",
            ]
        say("检测到 CAN 适配器：" + "，".join(label for label, _, _ in targets))
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
# 电机扫描
# ----------------------------------------------------------------------
def _probe(channel: str, ids: list[int], timeout: float, interface: str = "socketcan") -> set[int]:
    """ping 一个通道上的若干 ID，返回真正响应的 ID。"""
    try:
        with quiet():  # 构造和 connect 都会往 stdout 打无关信息
            bus = ArmBus(channel=channel, motors={}, interface=interface)
            bus.connect(handshake=False)
    except Exception:
        return set()  # 该 CAN 口不存在 / 打不开

    found: set[int] = set()
    try:
        for device_id in ids:
            try:
                with quiet():
                    response = bus.ping_by_id(device_id, timeout=timeout)
            except Exception:
                break
            # 空闲/回环的 CAN 口会对任何 ping 用 device_id=0 回应，
            # 所以必须校验回帧里的 ID 和所 ping 的一致，否则一律不算命中。
            if response is not None and response[0] == device_id:
                found.add(device_id)
    finally:
        try:
            with quiet():
                bus.disconnect(disable_torque=False)
        except Exception:
            pass

    return found


def list_targets(interface: str | None = None) -> list[tuple[str, str, str]]:
    """列出可用的 CAN 总线，返回 [(标签, 通道, 驱动)]。

    - Linux：走 socketcan，标签就是 can0 / can1 …
    - Windows / macOS：问 python-can 有哪些硬件适配器
      （pcan / canalystii / slcan / gs_usb / kvaser / vector …）
    """
    wanted = (interface or "auto").strip()
    explicit = wanted.lower() not in ("", "auto")

    # Linux 上 socketcan 直接用固定候选口（这样还能顺便查 ip link 状态）
    if sys.platform.startswith("linux") and wanted.lower() in ("", "auto", "socketcan"):
        return [(ch, ch, "socketcan") for ch in config.CANDIDATE_CHANNELS]

    drivers = [wanted] if explicit else None
    try:
        with quiet():   # 枚举会尝试加载所有驱动，往 stderr 刷一堆"不可用"的警告
            found = (
                can.detect_available_configs(interfaces=drivers)
                if drivers
                else can.detect_available_configs()
            )
    except Exception:
        found = []

    targets: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in found or []:
        driver = str(item.get("interface", "")).lower()
        channel = item.get("channel")
        if not driver or channel is None:
            continue
        if driver in config.VIRTUAL_CAN_DRIVERS:
            continue
        if driver == "socketcan":
            label = str(channel)
        else:
            label = f"{channel} ({driver})"
        key = (label, driver)
        if key in seen:
            continue
        seen.add(key)
        targets.append((label, str(channel), driver))
    return targets


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


def scan(
    candidates=None,
    *,
    forced: dict[str, str] | None = None,
    expect: str | None = None,
    interface: str | None = None,
    timeout: float = 0.04,
    on_progress=None,
) -> Setup:
    """扫描总线并给出可直接交给 Arm 的配置。

    forced 不为空时用它指定左右臂的通道（仍会扫描，只把没响应的电机剔除）。
    expect 是机器人类型（"gripper" / "no_gripper" / None）。
    interface 是 CAN 驱动（Linux 默认 socketcan；Windows 见 list_targets）。
    """
    say = on_progress or (lambda text: None)

    available = list_targets(interface)
    by_label = {label: (label, channel, driver) for label, channel, driver in available}

    if forced:
        mapping = dict(forced)
        targets = []
        for label in dict.fromkeys(mapping.values()):
            targets.append(by_label.get(label) or (label, label, config.default_can_interface()))
    else:
        if candidates:
            targets = [by_label.get(name) or (name, name, config.default_can_interface()) for name in candidates]
        else:
            targets = available
        mapping = {}

    if not targets:
        raise DetectError(
            "没有可用的 CAN 总线。Linux 请确认已执行 bash app/canup.sh；"
            "Windows 请确认 USB-CAN 适配器驱动已安装，或用 --interface 指定驱动。"
        )

    id_of_side = {
        side: [spec.can_id for spec in config.MOTORS if spec.side == side] for side in config.SIDES
    }
    probe_ids = sorted({can_id for ids in id_of_side.values() for can_id in ids})

    hits: dict[str, set[int]] = {}
    drivers: dict[str, str] = {}
    for label, channel, driver in targets:
        say(f"扫描 {label} ...")
        hits[label] = _probe(channel, probe_ids, timeout, driver)
        drivers[label] = driver
        if hits[label]:
            say(f"  {label} 上发现 {len(hits[label])} 个电机：{sorted(hits[label])}")

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
    return Setup(
        channels=channels_clean,
        mapping=mapping,
        online=online,
        expected=expected,
        interface={label: drivers.get(label, config.default_can_interface()) for label in channels_clean},
    )
