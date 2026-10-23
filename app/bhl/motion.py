"""录制与回放（纯逻辑，不含界面）。

两个关键点：

**录制带时间戳。** 老版本只存位置/速度，回放时按固定 50Hz 播放，
一旦录制时循环被拖慢，回放速度就会整体变快，动作走形且没有任何提示。
现在每一帧都记下真实采样时刻，回放时按时间重采样，速度永远是对的。

**回放原封不动。** 关节按名字对齐（不是按列号），然后逐帧把录制时的位置/速度发下去，
**不做任何关节限位检查或裁剪** —— 录了什么就放什么。出发前用 1.5 秒从当前姿态
平滑走到首帧，不会出现"一按回放就猛地弹过去"。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np

from . import config
from .util import RateLimiter


class PoseMismatch(ValueError):
    """当前姿态和录制首帧差得离谱（比关节整个行程还大），多半是零位/标定对不上。"""


@dataclass
class Motion:
    """一段录制好的动作。"""

    positions: np.ndarray  # (帧数, 关节数) 弧度
    velocities: np.ndarray  # (帧数, 关节数) rad/s
    timestamps: np.ndarray  # (帧数,) 秒，相对首帧
    joint_names: list[str]
    meta: dict = field(default_factory=dict)

    @property
    def num_frames(self) -> int:
        return int(self.positions.shape[0])

    @property
    def num_joints(self) -> int:
        return int(self.positions.shape[1])

    @property
    def duration(self) -> float:
        if self.num_frames < 2:
            return 0.0
        return float(self.timestamps[-1] - self.timestamps[0])

    @property
    def label(self) -> str:
        return str(self.meta.get("label") or "")

    def describe(self) -> str:
        text = f"{self.duration:.1f} 秒 / {self.num_frames} 帧 / {self.num_joints} 关节"
        if self.label:
            text += f" / {self.label}"
        return text


# ----------------------------------------------------------------------
# 录制
# ----------------------------------------------------------------------
def record(arm, *, hz: float = config.RECORD_HZ, label: str = "", stop=None, on_status=None) -> tuple[Motion, Exception | None]:
    """拖动示教录制。

    返回 (录制结果, 出错信息)。即使中途出错，已经录到的帧也会保留下来，
    调用方可以先存盘再报错，不会白录一趟。
    """
    stop = stop or threading.Event()
    say = on_status or (lambda info: None)

    positions: list[np.ndarray] = []
    velocities: list[np.ndarray] = []
    times: list[float] = []
    error: Exception | None = None

    rate = RateLimiter(hz)
    throttle = max(1, int(hz / 10))  # 状态回调限到 10Hz，别把界面刷爆

    arm.step(kp=0.0, kd=0.0)  # 先跑一拍，拿到当前姿态
    started = time.perf_counter()

    try:
        while not stop.is_set():
            pos, vel, tau = arm.step(kp=0.0, kd=0.0)
            positions.append(pos.copy())
            velocities.append(vel.copy())
            times.append(time.perf_counter() - started)

            if len(positions) % throttle == 0:
                elapsed = times[-1]
                say(
                    {
                        "frames": len(positions),
                        "seconds": elapsed,
                        "hz": len(positions) / elapsed if elapsed > 0 else 0.0,
                        "torque": float(np.abs(tau).max()) if tau.size else 0.0,
                    }
                )
            rate.sleep()
    except BaseException as exc:  # 包括 Ctrl-C：都要把已录到的存下来
        error = exc if isinstance(exc, Exception) else None

    if positions:
        motion = Motion(
            positions=np.asarray(positions, dtype=np.float64),
            velocities=np.asarray(velocities, dtype=np.float64),
            timestamps=np.asarray(times, dtype=np.float64),
            joint_names=arm.joint_names,
            meta={"label": label, "rate_hz": hz},
        )
    else:
        motion = Motion(
            positions=np.zeros((0, arm.num_joints)),
            velocities=np.zeros((0, arm.num_joints)),
            timestamps=np.zeros(0),
            joint_names=arm.joint_names,
            meta={"label": label, "rate_hz": hz},
        )

    return motion, error


def smooth_velocity(
    velocities: np.ndarray, hz: float, tau: float = config.REPLAY_VELOCITY_TAU
) -> np.ndarray:
    """对速度通道做零相位一阶低通（前向 + 后向各一遍再平均）。

    电机低速时的速度反馈基本是噪声（实测静止关节能读到 ±0.2 rad/s，相邻帧跳 0.25），
    它作为 MIT 前馈发下去后会被 kd 直接放大成能看见的抖动。
    位置通道不需要处理：实测相邻帧差只有 0.0004 rad。
    """
    if tau <= 0 or velocities.shape[0] < 3:
        return velocities

    alpha = (1.0 / hz) / (tau + 1.0 / hz)
    forward = np.empty_like(velocities)
    forward[0] = velocities[0]
    for index in range(1, len(velocities)):
        forward[index] = forward[index - 1] + alpha * (velocities[index] - forward[index - 1])

    backward = np.empty_like(velocities)
    backward[-1] = velocities[-1]
    for index in range(len(velocities) - 2, -1, -1):
        backward[index] = backward[index + 1] + alpha * (velocities[index] - backward[index + 1])

    return 0.5 * (forward + backward)


def derive_velocity(
    positions: np.ndarray, hz: float, tau: float = config.REPLAY_VELOCITY_TAU
) -> np.ndarray:
    """用位置序列求速度，作为 MIT 前馈。

    不用文件里记录的电机速度：实测那一路既抖（±0.2 rad/s）又不准
    （同一个肘部动作，位置推算 2.3 rad/s，电机反馈 3.6 rad/s）。
    位置通道则很干净（相邻帧差只有 0.0004 rad），求导后再轻滤一下噪声。
    """
    if positions.shape[0] < 3:
        return np.zeros_like(positions)
    return smooth_velocity(np.gradient(positions, axis=0, edge_order=1) * hz, hz, tau)


def resample(motion: Motion, joint_names: list[str], hz: float = config.REPLAY_HZ):
    """把录制按**关节名**对齐到当前机器人，并重采样到固定的控制周期。

    返回 (位置帧, 速度帧, 缺失关节名)。缺失关节的那一列全是 NaN，
    由回放时用当前实测位置顶住（也就是"这个关节不动"）。
    """
    if motion.num_frames < 2:
        raise ValueError("这段录制只有不到 2 帧，无法回放")

    source = {name: index for index, name in enumerate(motion.joint_names)}
    times = np.maximum.accumulate(motion.timestamps - motion.timestamps[0])
    duration = float(times[-1])
    if duration <= 0:
        raise ValueError("这段录制的时间戳无效，无法回放")

    steps = max(1, int(round(duration * hz)))
    grid = np.linspace(0.0, duration, steps + 1)

    positions = np.full((len(grid), len(joint_names)), np.nan)
    velocities = np.zeros((len(grid), len(joint_names)))
    missing: list[str] = []

    for column, name in enumerate(joint_names):
        if name not in source:
            missing.append(name)
            continue
        index = source[name]
        positions[:, column] = np.interp(grid, times, motion.positions[:, index])
        velocities[:, column] = np.interp(grid, times, motion.velocities[:, index])

    return positions, velocities, missing


def _status_reporter(on_status, expected_hz: float):
    """生成一个限频到 10Hz 的状态回调。"""
    if on_status is None:
        return lambda info: None
    last = [0.0]

    def report(info):
        now = time.perf_counter()
        if now - last[0] >= 0.1:
            last[0] = now
            on_status(info)

    return report


# ----------------------------------------------------------------------
# 回放
# ----------------------------------------------------------------------
def replay(
    arm,
    motion: Motion,
    *,
    repeat: int = 1,
    speed: float = 1.0,
    kp: float = config.DEFAULT_KP,
    kd: float = config.DEFAULT_KD,
    soft_start: float = config.REPLAY_SOFT_START_S,
    stop=None,
    on_status=None,
) -> dict:
    """回放一段录制。返回统计信息。"""
    stop = stop or threading.Event()
    say = _status_reporter(on_status, config.REPLAY_HZ)
    notes: list[str] = []

    repeat = max(1, int(repeat))
    speed = max(0.1, float(speed))

    # 1) 按名字对齐 + 按时间重采样
    positions, velocities, missing = resample(motion, arm.joint_names, config.REPLAY_HZ)
    if missing:
        notes.append(f"录制里没有这些关节，回放时将保持不动：{', '.join(missing)}")
        positions = np.where(np.isnan(positions), arm.pos.copy(), positions)

    # 说明：这里**不做任何关节限位处理** —— 录制了什么就原封不动回放什么。
    # （config.py 里的限位只用于标定和显示，不参与回放。）

    # 位置原封不动；速度前馈改用位置推算（不信任电机测速）
    velocities = derive_velocity(positions, config.REPLAY_HZ)

    # 2) 软启动：从当前姿态平滑走到第一帧
    #
    # 注意：这里**不做整圈归一化**。RobStride 报的是多圈绝对位置，录制里存的
    # 就是电机当时的真实读数，差一个 2π 就是真的差了一整圈（比如夹爪从闭合
    # 转到张开）。曾经试过"差接近整圈就平移轨迹"，结果把夹爪的目标改到差一圈
    # 的位置，动作方向整个反掉 —— 绝对不能这么干。
    gaps = {
        name: float(positions[0, index] - arm.pos[index])
        for index, name in enumerate(arm.joint_names)
    }
    gap = max((abs(value) for value in gaps.values()), default=0.0)

    # 姿态差比关节整个行程还大 —— 不可能是真实姿态差，
    # 典型原因是断电重上电后电机零位/标定偏移对不上（差出 2π 之类）
    #
    # 夹爪例外：丝杠/齿条结构开合一次要转好几圈（实测 18.7 rad ≈ 3 圈），
    # config 里写的 114°/380° 是夹爪开口行程、不是电机圈数，用它当判据会把
    # 每一段正常的夹爪录制都拒掉。夹爪不做这个检查，按绝对位置正常靠近。
    impossible = {}
    for name, value in gaps.items():
        spec = config.MOTORS_BY_NAME[name]
        if spec.is_gripper:
            continue
        if abs(value) > (spec.upper - spec.lower) * 1.05:
            impossible[name] = value
    if impossible:
        detail = "；".join(
            f"{name} 差 {value:+.2f} rad" for name, value in sorted(
                impossible.items(), key=lambda item: -abs(item[1])
            )[:5]
        )
        raise PoseMismatch(
            "当前姿态和这段录制的首帧差得离谱（差值超过了关节的整个行程），"
            "这不像是真实姿态差，更像是断电重上电后电机零位/标定偏移对不上：\n"
            f"  {detail}\n\n"
            "请先回放一段刚录的、或者在界面上点「扫描电机」确认读数是否合理；"
            "必要时重新标定（python3 calibrate.py）。"
        )

    finished = True
    if gap > config.APPROACH_MIN_GAP and not stop.is_set():
        # 慢一点更安全：不管界面里「出发前靠近」填多少，速度都不超过 APPROACH_SPEED
        duration = min(max(soft_start, gap / config.APPROACH_SPEED), config.APPROACH_MAX_S)
        approach_speed = gap / duration
        notes.append(
            f"首帧与当前姿态相差 {gap:.2f} rad，先缓慢靠近 {duration:.1f} 秒"
            f"（{approach_speed:.2f} rad/s）"
        )
        if soft_start <= 0:
            notes.append("「出发前靠近」填了 0，已自动按安全速度靠近，避免突然大幅运动")

        finished = arm.ramp_to(positions[0], kp=kp, kd=kd, duration=duration, stop=stop)
        if not finished:
            return {"completed": 0, "frames": 0, "notes": notes + ["靠近阶段被中止"]}

    # 4) 逐帧播放
    playing_hz = config.REPLAY_HZ * speed
    rate = RateLimiter(playing_hz)
    total_frames = len(positions)
    played = 0
    completed = 0
    peak_torque = 0.0
    play_seconds = 0.0  # 只统计真正播放的时间，不含两次之间的过渡
    send_fails = 0      # 当前已连续多少帧没发出去
    skipped = 0         # 累计跳过的帧数（瞬时发帧失败）
    started = time.perf_counter()

    for loop_index in range(repeat):
        if stop.is_set():
            break

        # 两次之间也要平滑接上，否则末帧到首帧会猛拉一下
        if loop_index > 0 and config.REPLAY_BRIDGE_S > 0:
            if not arm.ramp_to(positions[0], kp=kp, kd=kd, duration=config.REPLAY_BRIDGE_S, stop=stop):
                break

        loop_started = time.perf_counter()
        finished = True
        for frame_index in range(total_frames):
            if stop.is_set():
                finished = False
                break
            try:
                _pos, _vel, tau = arm.step(
                    positions[frame_index], kp=kp, kd=kd, velocities=velocities[frame_index]
                )
                send_fails = 0
            except Exception as exc:  # noqa: BLE001
                # USB-CAN 偶发一次超时（"The message could not be sent"）很常见：
                # 这一帧没发出去，机械臂只是少更新 20 ms 的目标位置，
                # 因为丢一次就中止整段回放代价太大，所以连续失败够多才真停。
                send_fails += 1
                skipped += 1
                if send_fails >= config.MAX_CONSECUTIVE_SEND_FAIL:
                    raise RuntimeError(
                        f"连续 {send_fails} 帧发送失败（{exc}）——"
                        "USB-CAN 可能已掉线，请点「重新连接」"
                    ) from exc
                rate.sleep()
                continue
            if tau.size:
                peak_torque = max(peak_torque, float(np.abs(tau).max()))
            played += 1
            rate.sleep()

            if frame_index % 5 == 0:
                say(
                    {
                        "loop": loop_index + 1,
                        "of_loops": repeat,
                        "frame": frame_index + 1,
                        "of_frames": total_frames,
                        "torque": peak_torque,
                    }
                )

        play_seconds += time.perf_counter() - loop_started
        if not finished:
            break
        completed += 1

    elapsed = time.perf_counter() - started
    expected = completed * motion.duration / speed
    actual_hz = played / play_seconds if play_seconds > 0 else 0.0

    if completed and expected > 0 and play_seconds > expected * 1.2:
        notes.append(
            f"回放偏慢（实际 {play_seconds:.1f}s vs 预期 {expected:.1f}s），CAN 或主机可能负载偏高"
        )

    if skipped:
        notes.append(f"有 {skipped} 帧因 CAN 瞬时超时被跳过（不影响动作连续性）")

    return {
        "completed": completed,
        "frames": played,
        "actual_hz": actual_hz,
        "peak_torque": peak_torque,
        "elapsed": elapsed,
        "notes": notes,
    }
