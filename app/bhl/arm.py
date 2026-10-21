"""机械臂：把总线、电机表、限位、上下电流程收在一起。

一条臂（或两条臂共用一条总线的情形）在这里被当成"一组关节"来对待，
对外只暴露数组，上层不需要知道哪个电机在哪条总线上。

一个控制周期的顺序是：**每条总线先全部下发，再一次性收齐状态**。
这样 16 个电机的往返只需要一次等待，而不是 16 次，50Hz 才有余量。

上下电是强约束的：
    arm.begin(...)   设力矩上限 + 使能
    arm.end()        先阻尼再失能（一定会松手）
    arm.close()      关总线（前面已经失能过，不会再被库逐个 disable 卡住）
"""

from __future__ import annotations

import time

import numpy as np
from robstride_dynamics import Motor

from . import config
from .bus import ArmBus, MotorTimeout
from .util import RateLimiter, quiet


class Arm:
    def __init__(
        self,
        setup: dict[str, list[str]],
        calibration: dict | None = None,
        *,
        interface: dict[str, str] | None = None,
        options: dict[str, dict] | None = None,
        timeout: float = 0.1,
    ) -> None:
        """setup 形如 {"can0": ["left_shoulder_pitch", ...], "can1": [...]}，
        通常直接来自 `detect.scan()`。

        interface 是每条总线用哪个 CAN 驱动；options 是打开总线时的额外参数
        （Windows 上 gs_usb 的整数通道号就放在这里），都按总线标签索引。
        """
        self.calibration = calibration
        self.timeout = timeout
        # 每个总线用哪个 CAN 驱动：Linux 是 socketcan，Windows 看适配器
        self._interface = dict(interface or {})
        self._options = {k: dict(v) for k, v in (options or {}).items()}

        # 按电机表顺序排列，保证录制/回放每一列的含义固定
        self._names = config.sort_names([name for names in setup.values() for name in names])
        self._index = {name: index for index, name in enumerate(self._names)}
        count = len(self._names)

        self.pos = np.zeros(count, dtype=np.float64)
        self.vel = np.zeros(count, dtype=np.float64)
        self.tau = np.zeros(count, dtype=np.float64)
        self.temp = np.zeros(count, dtype=np.float64)

        self._gains: tuple[float, float] = (0.0, 0.0)  # 当前生效的 (kp, kd)
        self._miss = {name: 0 for name in self._names}
        self.dropped_cycles = 0
        self.missing_now: set[str] = set()  # 本周期没回帧的关节，供诊断/标定使用

        self._buses: dict[str, ArmBus] = {}
        self._names_on: dict[str, list[str]] = {}
        self._bus_of: dict[str, ArmBus] = {}
        self._enabled = False

        self._connect(setup)

    # ------------------------------------------------------------------
    # 基本属性
    # ------------------------------------------------------------------
    @property
    def joint_names(self) -> list[str]:
        return list(self._names)

    @property
    def num_joints(self) -> int:
        return len(self._names)

    @property
    def channels(self) -> list[str]:
        return list(self._buses)

    @property
    def has_gripper(self) -> bool:
        return any(config.MOTORS_BY_NAME[name].is_gripper for name in self._names)

    def channel_of(self, name: str) -> str:
        return self._bus_of[name].channel

    @property
    def interfaces(self) -> dict[str, str]:
        return {channel: bus.interface for channel, bus in self._buses.items()}

    # ------------------------------------------------------------------
    # 连接 / 断开
    # ------------------------------------------------------------------
    def _connect(self, setup: dict[str, list[str]]) -> None:
        try:
            for channel, names in setup.items():
                if not names:
                    continue
                motors = {
                    name: Motor(id=config.MOTORS_BY_NAME[name].can_id, model=config.MOTORS_BY_NAME[name].model)
                    for name in names
                }
                # 库的构造和 connect() 会往 stdout 打大段标定字典，这里统一静音
                driver = self._interface.get(channel) or config.default_can_interface()
                open_kwargs = self._options.get(channel) or {"channel": channel}
                with quiet():
                    bus = ArmBus(
                        channel, motors, self.calibration,
                        interface=driver, open_kwargs=open_kwargs,
                    )
                    bus.connect()
                self._buses[channel] = bus
                self._names_on[channel] = list(names)
                for name in names:
                    self._bus_of[name] = bus
        except Exception:
            self.close()  # 连一半失败也要把已经打开的口关掉
            raise

    def close(self) -> None:
        """关程序时调用：先安全落一下，再关总线。可以重复调用。"""
        try:
            self.end()
        except Exception:
            pass

        for bus in self._buses.values():
            try:
                # 前面已经逐个失能过了，这里不再让库去 disable，
                # 否则任何一台电机没响应都会让 disconnect 抛异常、口关不掉。
                bus.disconnect(disable_torque=False)
            except Exception:
                pass

        self._buses.clear()
        self._names_on.clear()
        self._bus_of.clear()
        self._enabled = False

    def __enter__(self) -> "Arm":
        self.begin()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 上电 / 失能
    # ------------------------------------------------------------------
    def begin(self, torque_limit: float | None = config.RECORD_TORQUE_LIMIT) -> None:
        """设力矩上限并逐台使能。torque_limit=None 表示用各型号自己的上限。"""
        problems = []
        for name, bus in self._bus_of.items():
            spec = config.MOTORS_BY_NAME[name]
            limit = config.torque_limit(spec.model) if torque_limit is None else float(torque_limit)
            try:
                bus.set_torque_limit(name, limit)
            except Exception as exc:  # 力矩上限设不上不致命，只提示
                problems.append(f"{name} 力矩上限设置失败：{exc}")
            try:
                bus.enable_motor(name)
            except Exception as exc:
                problems.append(f"{name} 使能失败：{exc}")

        self._enabled = True
        if problems:
            raise RuntimeError("；".join(problems))

        # 读一次当前姿态。不读的话 self.pos 还是全 0，回放的"软启动"就会从
        # 0 位置的插值开始（而不是从机械臂现在的位置），上电后第一次回放会突然大动。
        # kp = kd = 0，这一步不会让电机出力。
        self.step(kp=0.0, kd=0.0)

    def end(self) -> None:
        """一次动作结束：先阻尼 0.3 秒，再全部失能（手臂松手）。"""
        if not self._bus_of:
            return

        if self._enabled:
            try:
                rate = RateLimiter(config.REPLAY_HZ)
                for _ in range(max(1, int(config.END_DAMPING_S * config.REPLAY_HZ))):
                    self.step(kp=0.0, kd=config.DAMPING_KD)
                    rate.sleep()
            except Exception:
                pass  # 阻尼失败也要继续往下失能

        for name, bus in self._bus_of.items():
            try:
                bus.disable_motor(name)
            except Exception:
                pass  # 单台失败不影响其它电机

        self._enabled = False

    # ------------------------------------------------------------------
    # 控制周期
    # ------------------------------------------------------------------
    def step(
        self,
        targets=None,
        *,
        kp: float = 0.0,
        kd: float = 0.0,
        velocities=None,
        timeout: float | None = None,
    ):
        """跑一个控制周期，返回 (pos, vel, tau)。

        targets=None 表示"以当前实测位置为目标"，用于拖动示教和保持姿态。
        """
        timeout = self.timeout if timeout is None else timeout
        self._gains = (kp, kd)
        missing: list[str] = []

        for channel, bus in self._buses.items():
            names = self._names_on[channel]
            bus.flush()

            # 1) 先把这条总线上所有电机的新指令一次性发完（不阻塞）
            for name in names:
                index = self._index[name]
                goal = self.pos[index] if targets is None else float(targets[index])
                feed = 0.0 if velocities is None else float(velocities[index])
                bus.write_operation_frame(name, goal, kp, kd, velocity=feed)

            # 2) 再一次性把状态收齐（只等一次）
            expected = {bus.motors[name].id: name for name in names}
            started = time.perf_counter()
            got = bus.pump(expected, timeout)
            bus.max_round_trip = max(bus.max_round_trip, time.perf_counter() - started)

            for name, (position, velocity, torque, temperature) in got.items():
                index = self._index[name]
                self.pos[index] = position
                self.vel[index] = velocity
                self.tau[index] = torque
                self.temp[index] = temperature
                self._miss[name] = 0

            for name in names:
                if name not in got:
                    self._miss[name] += 1
                    missing.append(name)

        if missing:
            self.dropped_cycles += 1
            worst = max(missing, key=lambda name: self._miss[name])
            if self._miss[worst] >= config.MAX_CONSECUTIVE_MISS:
                raise MotorTimeout(
                    f"{worst} 连续 {self._miss[worst]} 个周期没有回帧，已停机保护"
                )

        self.missing_now = set(missing)
        return self.pos, self.vel, self.tau

    # ------------------------------------------------------------------
    # 姿态工具
    # ------------------------------------------------------------------
    def hold(self, seconds: float, *, kp: float = 0.0, kd: float = 0.0) -> None:
        """原地保持（kp=0 时就是软的）。"""
        rate = RateLimiter(config.REPLAY_HZ)
        for _ in range(max(1, int(seconds * config.REPLAY_HZ))):
            self.step(kp=kp, kd=kd)
            rate.sleep()

    def ramp_to(self, target, *, kp: float, kd: float, duration: float, stop=None) -> bool:
        """从当前姿态平滑地走到 target。

        分两段：
        1. 先在原地把刚度从 0 升到目标值 —— 否则使能后前一两秒机械臂是软的，
           会先被重力拽下去再被拉起来，看起来就是"突然大动"；
        2. 再按这个刚度走到 target，速度由 duration 决定。

        刚度已经是目标值时不重复第 1 段（多次回放之间的衔接就用不到它）。
        返回 True 表示走完了，False 表示中途被 stop 打断。
        """
        rate = RateLimiter(config.REPLAY_HZ)

        if self._gains != (kp, kd):
            stiffen_steps = max(1, int(min(0.5, max(0.2, duration * 0.25)) * config.REPLAY_HZ))
            for step_index in range(1, stiffen_steps + 1):
                if stop is not None and stop.is_set():
                    return False
                alpha = step_index / stiffen_steps
                self.step(kp=kp * alpha, kd=kd * alpha)   # 目标 = 当前姿态，只涨刚度
                rate.sleep()

        start = self.pos.copy()
        goal = np.asarray(target, dtype=np.float64)
        steps = max(1, int(duration * config.REPLAY_HZ))

        for step_index in range(1, steps + 1):
            if stop is not None and stop.is_set():
                return False
            alpha = step_index / steps
            self.step(start + (goal - start) * alpha, kp=kp, kd=kd)
            rate.sleep()

        return True

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------
    def summary(self) -> str:
        parts = [f"{bus.channel} {len(self._names_on[channel])} 个电机" for channel, bus in self._buses.items()]
        gripper = "含夹爪" if self.has_gripper else "无夹爪"
        return f"{self.num_joints} 关节（{gripper}）：" + "，".join(parts)

    def bus_stats(self) -> dict[str, dict]:
        return {channel: bus.stats() for channel, bus in self._buses.items()}
