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
import threading
import time

import can
from robstride_dynamics import ParameterType, RobstrideBus
from robstride_dynamics.protocol import CommunicationType
from robstride_dynamics.table import (
    MODEL_MIT_POSITION_TABLE,
    MODEL_MIT_TORQUE_TABLE,
    MODEL_MIT_VELOCITY_TABLE,
)

from . import config

Status = tuple[float, float, float, float]  # (位置 rad, 速度 rad/s, 力矩 Nm, 温度 ℃)


def _bounded(func, timeout: float = 6.0, what: str = "CAN 调用"):
    """给底层打开/关闭套一个"软"超时，防止界面被永远卡住。

    注：超时后调用线程还挂在原生库里 —— 所以扫描/监听这种高危操作都走子进程
    （detect.run_worker），这里只兜正式连接/断开这些硬件健康时的路径。
    """
    box: dict = {}

    def runner() -> None:
        try:
            box["value"] = func()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True, name="can-bus-call")
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise RuntimeError(f"{what}超过 {timeout:.0f} 秒没有返回（设备可能被别的程序占用）")
    error = box.get("error")
    if error is not None:
        raise error
    return box.get("value")


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


# gs_usb 预热用的仲裁号：扩展帧里通信类型 31 在 Robstride 协议里不存在，
# 电机不会响应，总线上其它节点也只会忽略 —— 只为把 USB 端点"点着"。
_GS_USB_WARMUP_ID = 0x1FFF_FFFF


def _prime_gs_usb(handler) -> None:
    """把 gs_usb 的发送端点"点着"。

    实测（candleLight_fw + WinUSB）：刚 start() 完的第一帧 bulk OUT 会被固件丢掉，
    表现为 1 秒的 USB 超时（`CanOperationError: The message could not be sent`），
    从第二帧起才正常。不预热的话，扫描电机时的**第一个 ping 必定超时**，
    于是这条总线被判定成"没有电机"，看起来就像"CAN 用不了"。

    这里先发一帧没人会理的扩展帧把这一次超时吃掉；失败也无所谓，目的已经达到。
    """
    try:
        handler.send(can.Message(arbitration_id=_GS_USB_WARMUP_ID, is_extended_id=True, data=[]))
    except Exception:
        pass


def ensure_candle_backend() -> bool:
    """确保 python-can 认识 `candle` 这个后端名。

    `python-can-candle` 平时是靠**包的 entry point 元数据**注册进
    `can.interfaces.BACKENDS` 的；打包成 exe 后，PyInstaller 默认不收集
    `.dist-info`，元数据丢失 → `can.interface.Bus(interface="candle")` 直接抛
    `CanInterfaceNotImplementedError: Unknown interface type "candle"`。

    父进程里 `detect` 会手工注册一次，但**扫描是在子进程里跑的**
    （`--can-scan-worker`），子进程没人调那段代码，于是整批扫描全报这个错。
    所以把兜底注册放到真正打开总线的地方，覆盖 GUI / 子进程 / discover /
    calibrate 所有路径。
    """
    try:
        import can.interfaces as interfaces

        if "candle" in interfaces.BACKENDS:
            return True
        from candle.candle_bus import CandleBus  # noqa: F401

        interfaces.BACKENDS["candle"] = ("candle.candle_bus", "CandleBus")
        return True
    except Exception:  # noqa: BLE001  没装 python-can-candle
        return False


def _apply_send_timeout(handler, limit: float) -> None:
    """把 handler.send 的默认超时收紧到 limit 秒。

    candle 后端的 `CandleBus.send(msg, timeout=None)` 默认超时是 **1.0 秒**：
    总线上没有节点 ACK 时，每发一帧都要白等满 1 秒才抛
    `CanOperationError: The message could not be sent`。扫一条没接设备的
    死线要十几秒、回放遇到抖动也会卡满一秒，都是这个默认值造成的。
    正常电机的 ACK 在微秒级，所以收紧到 config.CAN_SEND_TIMEOUT 完全够用。
    """
    original = handler.send

    def send(message, timeout=None, *args, **kwargs):
        if timeout is None:
            timeout = limit
        return original(message, timeout, *args, **kwargs)

    try:
        handler.send = send          # 绑定实例属性，覆盖类方法
    except Exception:                # noqa: BLE001  某些后端实例不允许赋值，保持原样
        pass


def release_usb(handler) -> None:
    """把适配器的底层句柄还给系统。

    Windows 上这些是**独占句柄**：不主动释放，同一个适配器第二次打开就会失败 ——
    而"扫描电机"和"正式连接"正好各要开一次。

    覆盖两种后端：
      - `gs_usb`（python-can 自带）：GsUsbBus.shutdown() 只发"停止"命令，
        libusb 句柄要靠 `usb.util.dispose_resources` 收；
      - `candle`（python-can-candle 插件）：CandleBus.shutdown() 只 reset 通道，
        没调 `CandleDevice.close()`，句柄要等 GC —— 实测开 8~10 次之后就开始
        `Cannot open device`，所以这里显式关掉。
    """
    # 1) gs_usb：python-can 的 GsUsbBus → gs_usb 包 → usb.core.Device
    gs_usb = getattr(handler, "gs_usb", None)
    if gs_usb is not None:
        inner = getattr(gs_usb, "gs_usb", gs_usb)
        try:
            import usb.util

            usb.util.dispose_resources(inner)
        except Exception:
            pass
        return

    # 2) candle：CandleBus 内部持有 CandleDevice
    device = getattr(handler, "_device", None)
    close = getattr(device, "close", None)
    if callable(close):
        # shutdown() 里的 ch.reset() 是异步的：紧接着 close 可能在固件还没
        # 消化 reset 时就把设备掐了，偶尔会把设备从 USB 上打掉。等一小下再关。
        time.sleep(0.25)
        try:
            close()
        except Exception:
            pass


class ArmBus(RobstrideBus):
    """一条 CAN 总线（一条臂，或两条臂共用一条）。"""

    def __init__(
        self,
        channel: str,
        motors: dict,
        calibration: dict | None = None,
        bitrate: int = 1_000_000,
        interface: str = "socketcan",
        open_kwargs: dict | None = None,
    ):
        super().__init__(channel=channel, motors=motors, calibration=calibration, bitrate=bitrate)
        # 库把 interface 写死成 socketcan（Linux），Windows/macOS 必须换驱动
        self.interface = interface
        # 打开总线时要用的**真正通道值**和额外参数。
        # Windows 的适配器标签（"candleLight#0"）和 python-can 要的 channel（整数下标）
        # 不是一回事，所以由 detect.py 提前算好放在这里。
        self.open_kwargs = dict(open_kwargs or {})
        self._expect_id: int | None = None
        self.skipped = 0  # 丢掉的陈旧帧 / 串扰帧
        self.dropped = 0  # 没有按时回帧的次数
        self.max_round_trip = 0.0

    @property
    def open_channel(self):
        """交给 python-can 的通道值；没显式给就是标签本身（Linux 的 can0/can1）。"""
        return self.open_kwargs.get("channel", self.channel)

    def connect(self, handshake: bool = True) -> None:
        """覆盖库的实现：用可配置的 CAN 接口打开总线。

        robstride_dynamics 里写死了 interface="socketcan"，
        在 Windows 上要换成 pcan / canalystii / slcan / gs_usb 之类。
        """
        if self.is_connected:
            raise RuntimeError(f"{self.channel} 已经打开了，不要重复 connect()")
        if str(self.interface).lower() == "candle":
            ensure_candle_backend()
        kwargs = dict(self.open_kwargs)
        kwargs.setdefault("channel", self.channel)
        kwargs.setdefault("bitrate", self.bitrate)
        handler = _bounded(
            lambda: can.interface.Bus(interface=self.interface, **kwargs),
            what=f"打开 {self.channel}",
        )
        if str(self.interface).lower() == "gs_usb":
            _prime_gs_usb(handler)
        _apply_send_timeout(handler, config.CAN_SEND_TIMEOUT)
        self.channel_handler = handler

    def disconnect(self, disable_torque: bool = True) -> None:
        """关总线。

        除了库原本的 shutdown()，gs_usb（candleLight / CANable）还要把
        **libusb 的设备句柄还回去** —— Python 的垃圾回收不管这个，Windows 上
        句柄不放掉，同一个适配器第二次打开就会报 `Access denied`；
        而"扫描电机"和"正式连接"正好会各开一次。
        """
        handler = self.channel_handler
        try:
            super().disconnect(disable_torque=disable_torque)
        finally:
            if handler is not None:
                try:
                    _bounded(handler.shutdown, what=f"关闭 {self.channel}")   # 幂等
                except Exception:
                    pass
                try:
                    _bounded(lambda: release_usb(handler), what="释放 USB 句柄")
                except Exception:
                    pass

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
            if not getattr(frame, "is_rx", True):
                self.skipped += 1  # 自己刚发出去的帧被适配器回显了，不能当应答
                continue
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
            if not getattr(frame, "is_rx", True):
                self.skipped += 1  # 自己刚发出去的帧被适配器回显了
                continue
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
