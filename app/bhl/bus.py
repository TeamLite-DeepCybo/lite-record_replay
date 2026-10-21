"""CAN 总线封装。

`robstride_dynamics.RobstrideBus` 已经把协议编解码做好了，直接用就行。
这里只补它缺的三件事：

1. **不清理接收缓冲**：上周期没读走的陈旧帧会被当成本次读数（位置/力矩悄悄串位）；
2. **不按电机 ID 过滤**：一条总线上有多台电机时，别的电机的帧会被拿来做解码；
3. **超时按"每来一帧算一次"**：连续收到无效帧时，实际等待会远超预期。

做法是重写 `receive()`（库内部所有读操作都会经过它）并新增 `pump()`
（一个控制周期内把整条总线的状态帧一次收齐）。这样库自带的 enable/disable/
write 参数等路径也自动获得了保护，不需要再另写一套协议代码。
"""

from __future__ import annotations

import contextlib
import struct
import time

from robstride_dynamics import ParameterType, RobstrideBus
from robstride_dynamics.protocol import CommunicationType
from robstride_dynamics.table import (
    MODEL_MIT_POSITION_TABLE,
    MODEL_MIT_TORQUE_TABLE,
    MODEL_MIT_VELOCITY_TABLE,
)

Status = tuple[float, float, float, float]  # (位置 rad, 速度 rad/s, 力矩 Nm, 温度 ℃)


class MotorTimeout(RuntimeError):
    """电机没有在超时时间内回帧。"""


class MotorFault(RuntimeError):
    """电机回报了故障帧。"""


def _comm_type(arbitration_id: int) -> int:
    """扩展帧 ID 的 bit 28~24：通信类型。"""
    return (arbitration_id >> 24) & 0x1F


def _source_id(arbitration_id: int) -> int:
    """扩展帧里电机自己回报的 ID。

    注意：状态帧里电机 ID 在 bit 15~8（extra_data 的低字节），
    bit 7~0 是主机 ID（0xFF），这一点和发送帧相反，容易搞错。
    """
    return (arbitration_id >> 8) & 0xFF


def _decode(model: str, data: bytes, calibration: dict | None, name: str) -> Status:
    """把一帧 MIT 状态数据解成物理量（公式与 robstride_dynamics 完全一致）。

    只抄了这 10 行解码，是为了能自己控制超时、自己判断故障帧，
    而不是再复制一整套 320 行的协议层。
    """
    position_u16, velocity_u16, torque_i16, temperature_u16 = struct.unpack(">HHHH", data)

    position = (position_u16 / 0x7FFF - 1.0) * MODEL_MIT_POSITION_TABLE[model]
    velocity = (velocity_u16 / 0x7FFF - 1.0) * MODEL_MIT_VELOCITY_TABLE[model]
    torque = (torque_i16 / 0x7FFF - 1.0) * MODEL_MIT_TORQUE_TABLE[model]
    temperature = temperature_u16 * 0.1

    if calibration and name in calibration:
        entry = calibration[name]
        position = (position - entry["homing_offset"]) * entry["direction"]
        velocity *= entry["direction"]
        torque *= entry["direction"]

    return position, velocity, torque, temperature


class ArmBus(RobstrideBus):
    """一条 CAN 总线（一条臂，或两条臂共用一条）。"""

    def __init__(self, channel: str, motors: dict, calibration: dict | None = None, bitrate: int = 1_000_000):
        super().__init__(channel=channel, motors=motors, calibration=calibration, bitrate=bitrate)
        self._expect_id: int | None = None
        self.skipped = 0  # 丢掉的陈旧帧 / 串扰帧
        self.dropped = 0  # 没有按时回帧的次数
        self.max_round_trip = 0.0

    # ------------------------------------------------------------------
    # 底层收发
    # ------------------------------------------------------------------
    def flush(self) -> int:
        """清空接收缓冲，返回丢掉多少帧。"""
        handler = self.channel_handler
        if handler is None:
            return 0
        count = 0
        while handler.recv(timeout=0) is not None:
            count += 1
        self.skipped += count
        return count

    def receive(self, timeout: float | None = None):
        """重写库的 receive：按**剩余**时间算超时，并按需过滤电机 ID。

        返回值布局与库保持一致：(通信类型, extra_data, 主机ID, 数据)，
        因此库内部的 receive_status_frame / ping_by_id 都能继续正常工作。
        """
        deadline = None if timeout is None else time.perf_counter() + timeout
        while True:
            if deadline is None:
                remain = None
            else:
                remain = deadline - time.perf_counter()
                if remain <= 0:
                    return None

            frame = self.channel_handler.recv(timeout=remain)
            if frame is None:
                return None
            if not frame.is_extended_id:
                self.skipped += 1  # 电机掉线重连时会出现这种帧
                continue

            arbitration_id = frame.arbitration_id
            if self._expect_id is not None and _source_id(arbitration_id) != self._expect_id:
                self.skipped += 1  # 总线上别的电机的帧，丢掉，不能拿来当自己的读数
                continue

            return (
                _comm_type(arbitration_id),
                (arbitration_id >> 8) & 0xFFFF,
                arbitration_id & 0xFF,
                frame.data,
            )

    def pump(self, expected: dict[int, str], timeout: float) -> dict[str, Status]:
        """一次收齐 `expected`（电机 ID -> 名字）里的状态帧。

        超时还没回的电机不会出现在结果里，由调用方决定怎么处理。
        """
        result: dict[str, Status] = {}
        deadline = time.perf_counter() + timeout
        while len(result) < len(expected):
            remain = deadline - time.perf_counter()
            if remain <= 0:
                break

            frame = self.channel_handler.recv(timeout=remain)
            if frame is None:
                break
            if not frame.is_extended_id:
                self.skipped += 1
                continue

            arbitration_id = frame.arbitration_id
            name = expected.get(_source_id(arbitration_id))
            if name is None:
                self.skipped += 1
                continue

            comm_type = _comm_type(arbitration_id)
            if comm_type == CommunicationType.FAULT_REPORT:
                raise MotorFault(f"{name} 回报故障帧，请检查该关节")
            if comm_type != CommunicationType.OPERATION_STATUS:
                self.skipped += 1
                continue

            result[name] = _decode(self.motors[name].model, frame.data, self.calibration, name)

        if len(result) < len(expected):
            self.dropped += len(expected) - len(result)
        return result

    # ------------------------------------------------------------------
    # 上电 / 失能 / 参数
    # ------------------------------------------------------------------
    @contextlib.contextmanager
    def expecting(self, motor: str):
        """在这段代码里只接受该电机的帧，挡掉总线上的串扰。"""
        self.flush()
        self._expect_id = self.motors[motor].id
        try:
            yield
        finally:
            self._expect_id = None

    def enable_motor(self, motor: str) -> None:
        with self.expecting(motor):
            self.transmit(CommunicationType.ENABLE, self.host_id, self.motors[motor].id)
            self.receive_status_frame(motor)

    def disable_motor(self, motor: str) -> None:
        with self.expecting(motor):
            self.transmit(CommunicationType.DISABLE, self.host_id, self.motors[motor].id)
            self.receive_status_frame(motor)

    def set_torque_limit(self, motor: str, value: float) -> None:
        # 走库的 write()，它内部用 receive_status_frame 收确认，
        # 而 receive() 已被我们重写，所以这里自动带上了 ID 过滤。
        with self.expecting(motor):
            self.write(motor, ParameterType.TORQUE_LIMIT, float(value))

    def stats(self) -> dict[str, float]:
        return {
            "skipped": self.skipped,
            "dropped": self.dropped,
            "max_round_trip": round(self.max_round_trip, 4),
        }
