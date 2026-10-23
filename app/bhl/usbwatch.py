"""监测 candleLight 的 USB 插拔 —— 只做枚举，不打开设备。

为什么需要这一层：USB-CAN 是独占句柄。拔掉再插上之后，Python 侧那个旧句柄
其实已经失效，但对象还活着；此时再发帧，candle_api 的 C 扩展会直接 fail-fast
（异常码 0xc0000409，BEX64），整个进程瞬间消失 —— try/except 完全拦不住。

所以连接时记一份设备快照，之后比对：一旦发现变过，就把连接标记为失效，
拦住录制/回放，让用户走「重新连接」重开句柄，而不是拿死句柄去发帧。
"""

from __future__ import annotations

VID = 0x1D50
PID = 0x606F


def snapshot() -> frozenset[tuple[int, int]] | None:
    """当前在线 candleLight 的 (bus, address) 集合；查询失败返回 None。

    只做描述符枚举，不 claim 接口、不打开数据端点，因此设备正被本进程或
    别的进程占用时也能安全调用（这正是用它、而不用 candle_api 枚举的原因）。
    """
    try:
        import libusb_package
        import usb.core

        backend = libusb_package.get_libusb1_backend()
        devices = usb.core.find(
            find_all=True, idVendor=VID, idProduct=PID, backend=backend
        )
        return frozenset((int(d.bus), int(d.address)) for d in (devices or []))
    except Exception:  # noqa: BLE001  查不了就当"不知道"
        return None
