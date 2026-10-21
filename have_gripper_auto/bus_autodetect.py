"""
bus_autodetect.py

自动检测左/右臂各自所在的 CAN 通道，以及每个电机是否在线。

原理：向每个候选通道逐个 ping 左臂电机 ID（11~18）和右臂电机 ID（21~28），
哪个通道命中的 ID 多，哪个通道就是那条臂。这样无论 USB-CAN 适配器
枚举成 can0 还是 can1（顺序随插入顺序变化），程序都能找对总线。

同时支持"降级模式"：如果由于接线问题只有部分电机在线（例如只扫到
前四个电机），仍返回这些在线电机组成的配置，录制/回放只操作它们，
离线电机自动忽略。
"""

from robstride_dynamics import RobstrideBus

from config import motor_configs
from api import _ids_for_side


def _probe_channel_ids(channel: str, ids: list[int], *, timeout: float = 0.03) -> set[int]:
    """ping 指定通道上的一组电机 ID，返回真正命中的 ID 集合。

    某些空闲/回环的 CAN 口会对任何 ping 用 device_id=0 回应，
    所以必须校验回应中的 ID 与所 ping 的 ID 一致，否则视为未命中。
    """
    bus = RobstrideBus(channel=channel, motors={})
    try:
        bus.connect(handshake=False)
    except Exception:
        return set()

    found = set()
    try:
        for device_id in ids:
            try:
                response = bus.ping_by_id(device_id, timeout=timeout)
            except Exception:
                break
            if response is not None and response[0] == device_id:
                found.add(device_id)
    finally:
        try:
            bus.disconnect(disable_torque=False)
        except Exception:
            pass
    return found


def autodetect_arm_setup(
    candidate_channels: list[str] | None = None,
    *,
    verbose: bool = True,
) -> tuple[dict[str, str], dict]:
    """检测各臂所在通道及在线电机。

    返回 (bus_configs, active_motor_configs)：
    - bus_configs: 例如 {"left": "can0", "right": "can1"}，
      一条臂完全离线时其条目会被省略（只警告，不报错）。
    - active_motor_configs: 只包含实际有响应的电机，
      直接传给 Arm 使用，离线电机自动忽略。
    """
    channels = candidate_channels or [f"can{i}" for i in range(0, 5)]

    side_ids = {
        "left": _ids_for_side(motor_configs, "left"),
        "right": _ids_for_side(motor_configs, "right"),
    }

    # 每个通道对每一侧的命中集合
    hits: dict[str, dict[str, set[int]]] = {}
    for channel in channels:
        hits[channel] = {side: _probe_channel_ids(channel, ids) for side, ids in side_ids.items()}
        if verbose:
            print(
                f"[autodetect] {channel}: "
                f"左臂电机命中 {len(hits[channel]['left'])}/{len(side_ids['left'])}, "
                f"右臂电机命中 {len(hits[channel]['right'])}/{len(side_ids['right'])}"
            )

    bus_configs: dict[str, str] = {}
    active_ids: dict[str, set[int]] = {}
    used_channels: set[str] = set()

    for side in ["left", "right"]:
        best_channel = None
        best_found: set[int] = set()
        for channel in channels:
            if channel in used_channels:
                continue
            found = hits[channel][side]
            if len(found) > len(best_found):
                best_channel = channel
                best_found = found

        if best_channel is None or not best_found:
            print(f"[autodetect] 警告: 未找到{'左' if side == 'left' else '右'}臂的任何电机，本次将忽略该臂。")
            continue

        bus_configs[side] = best_channel
        active_ids[side] = best_found
        used_channels.add(best_channel)

        missing = sorted(set(side_ids[side]) - best_found)
        if missing:
            print(
                f"[autodetect] 警告: {'左' if side == 'left' else '右'}臂({best_channel}) "
                f"以下电机 ID 无响应，将被忽略: {missing}"
            )

    if not bus_configs:
        raise RuntimeError(
            "两条臂都没有检测到任何电机。请检查 CAN 线缆/供电，并确认已运行 canup.sh。"
        )

    # 只保留在线电机
    active_motor_configs = {}
    for name, motor in motor_configs.items():
        side = "left" if name.startswith("left") else "right"
        if side in active_ids and motor.id in active_ids[side]:
            active_motor_configs[name] = motor

    if verbose:
        detected = {side: channel for side, channel in bus_configs.items()}
        print(f"[autodetect] 检测结果: {detected}, 在线电机 {len(active_motor_configs)}/{len(motor_configs)}")
        print(f"[autodetect] 在线关节: {list(active_motor_configs.keys())}")

    return bus_configs, active_motor_configs


def autodetect_bus_configs(
    candidate_channels: list[str] | None = None,
    *,
    min_hits_per_side: int = 2,
    verbose: bool = True,
) -> dict[str, str]:
    """兼容旧接口：只返回通道配置，要求左右臂都在线。"""
    bus_configs, active_motor_configs = autodetect_arm_setup(candidate_channels, verbose=verbose)
    for side in ["left", "right"]:
        active_count = sum(1 for name in active_motor_configs if name.startswith(side))
        if active_count < min_hits_per_side:
            raise RuntimeError(
                f"{'左' if side == 'left' else '右'}臂在线电机不足（{active_count} 个）。"
            )
    return bus_configs


if __name__ == "__main__":
    bus_configs, active_motor_configs = autodetect_arm_setup()
    print({"buses": bus_configs, "active_joints": list(active_motor_configs.keys())})
