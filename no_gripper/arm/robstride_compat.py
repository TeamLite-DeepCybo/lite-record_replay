"""
robstride_compat — macOS shim for robstride_dynamics.

Patches RobstrideBus.connect/disconnect/scan_channel to use python-can
with the gs_usb backend (candleLight USB-CAN adapter) instead of Linux
SocketCAN.

Install dependencies first:
    pip install python-can[gs-usb] pyusb
    brew install libusb

Usage (call FIRST before any robstride_dynamics code runs):
    import robstride_compat; robstride_compat.install()
"""

import sys
import platform
import time

__all__ = [
    "install",
    "uninstall",
    "adapter_serials",
    "list_adapters",
    "probe_ids",
    "scan_all_adapters",
    "discover_arm_channels",
    "scan_cache",
    "clear_scan_cache",
]


# ---------------------------------------------------------------------------
# Adapter discovery
# ---------------------------------------------------------------------------
# The rest of this module patches RobstrideBus, so these helpers only mean
# anything after install() has run (macOS). They are used by config.py to
# work out which physical adapter carries which arm.


def list_adapters():
    """Return [{"serial", "bus", "address", "index"}] for every gs_usb device."""
    from gs_usb.gs_usb import GsUsb

    adapters = []
    for index, device in enumerate(GsUsb.scan()):
        serial = ""
        # Reading the descriptor right after enumeration occasionally fails
        # (libusb returns a handle that is not fully ready yet). Retry a few
        # times before giving up, otherwise the adapter shows an empty serial
        # and cannot be addressed by name.
        for _attempt in range(3):
            try:
                serial = device.serial_number or ""
            except Exception:
                serial = ""
            if serial:
                break
            time.sleep(0.05)
        adapters.append(
            {
                "index": index,
                "serial": serial,
                "bus": getattr(device, "bus", None),
                "address": getattr(device, "address", None),
            }
        )
    return adapters


def adapter_serials():
    return [a["serial"] for a in list_adapters()]


def probe_ids(channel, ids, timeout=0.05):
    """Open `channel` and return {id: response} for the IDs that answered.

    Never raises: an adapter that cannot be opened or that answers nothing
    simply returns an empty dict.
    """
    from robstride_dynamics import RobstrideBus

    ids = list(ids)
    if not ids:
        return {}

    # A channel may be a serial (looked up per attempt) or an index. Opening
    # one adapter re-enumerates the USB tree, so retry the *whole* open+probe
    # before concluding that nothing answered.
    for attempt in range(3):
        bus = RobstrideBus(channel=channel, motors={})
        bus._quiet = True
        try:
            bus.connect(handshake=False)
        except Exception:
            time.sleep(0.3 * (attempt + 1))
            continue

        found = {}
        try:
            for device_id in ids:
                try:
                    response = bus.ping_by_id(device_id, timeout=timeout)
                except Exception:
                    continue
                if response is not None:
                    found[device_id] = list(response)
        finally:
            try:
                bus.disconnect(disable_torque=False)
            except Exception:
                pass

        if found:
            return found
        # An adapter that opened but answered nothing may just need another
        # round after re-enumeration settles.
        time.sleep(0.3 * (attempt + 1))
    return {}


def scan_all_adapters(ids=None, start_id=1, end_id=50, timeout=0.05, verbose=True):
    """Probe every attached adapter for `ids` (default: 1..end_id-1).

    Returns {serial: sorted([ids that answered])}.
    """
    if ids is None:
        ids = list(range(start_id, end_id))

    results = {}
    for adapter in list_adapters():
        serial = adapter["serial"]
        if verbose:
            print(f"[scan] 适配器 {serial} (bus {adapter['bus']}, addr {adapter['address']}) ...", flush=True)
        hits = probe_ids(serial, ids, timeout=timeout)
        results[serial] = hits
        if verbose:
            print(f"[scan]   -> {sorted(hits) or '无响应'}", flush=True)
    return results


def _side_scores(hits, left_ids, right_ids):
    answered = set(hits)
    left_hits = len(answered & left_ids)
    right_hits = len(answered & right_ids)
    return left_hits, right_hits


# Serial -> {motor_id: response}. Filled while auto-detecting, so a caller that
# immediately wants the full scan does not have to reopen every adapter.
_SCAN_CACHE: dict[str, dict] = {}


def scan_cache():
    """Results collected during auto-detection: {serial: {id: response}}."""
    return {serial: dict(hits) for serial, hits in _SCAN_CACHE.items()}


def clear_scan_cache():
    _SCAN_CACHE.clear()


def discover_arm_channels(
    motor_configs,
    *,
    min_hits=2,
    timeout=0.05,
    verbose=True,
):
    """Work out which adapter carries the left arm and which the right.

    A side is identified by the motor IDs it answers, not by USB enumeration
    order or by serial number — so the same code works on any board, any
    machine, and whichever port the cable happens to be plugged into.

    Returns {"left": serial, "right": serial}.
    """
    left_ids = {m.id for name, m in motor_configs.items() if name.startswith("left")}
    right_ids = {m.id for name, m in motor_configs.items() if name.startswith("right")}
    wanted = sorted(left_ids | right_ids)

    if not wanted:
        raise RuntimeError("config.py 里没有任何电机配置，无法识别左右臂。")

    if verbose:
        print(
            f"[auto] 按电机 ID 识别左右臂（左 {sorted(left_ids)}，右 {sorted(right_ids)}）",
            flush=True,
        )

    found = {}
    report = []
    clear_scan_cache()

    for adapter in list_adapters():
        serial = adapter["serial"]
        hits = probe_ids(serial, wanted, timeout=timeout)
        if hits:
            _SCAN_CACHE[serial] = dict(hits)

        left_hits, right_hits = _side_scores(hits, left_ids, right_ids)
        report.append({"serial": serial, "hits": sorted(hits), "left": left_hits, "right": right_hits})

        if verbose:
            print(f"[auto]   适配器 {serial}: 命中 {sorted(hits) or '无'}", flush=True)

        side = None
        if left_hits >= min_hits and left_hits > right_hits:
            side = "left"
        elif right_hits >= min_hits and right_hits > left_hits:
            side = "right"

        if side and side not in found:
            found[side] = serial
            if verbose:
                print(f"[auto]   -> 判定为 {side} 臂", flush=True)

        # Both arms located: no need to keep opening the remaining adapters.
        if "left" in found and "right" in found:
            break

    if "left" not in found or "right" not in found:
        missing = [s for s in ("left", "right") if s not in found]
        details = "\n".join(
            f"  {r['serial']}: 命中 {r['hits'] or '无'}" for r in report
        ) or "  （没有检测到任何 USB-CAN 适配器）"
        raise RuntimeError(
            f"无法识别 {' 和 '.join(missing)} 臂。\n"
            f"期望的电机 ID：左 {sorted(left_ids)}，右 {sorted(right_ids)}（至少命中 {min_hits} 个）。\n"
            f"已扫描的适配器：\n{details}\n"
            f"请检查：\n"
            f"  1. 左右臂都已接好并通电\n"
            f"  2. 机械臂的电机 ID 与 arm/config.py 中的配置一致\n"
            f"  3. 单独运行 `python discover_actuators.py --full` 查看每个适配器上的实际 ID"
        )

    return {"left": found["left"], "right": found["right"]}


def _should_patch():
    """Only patch on macOS when not already installed."""
    if platform.system() != "Darwin":
        return False
    try:
        __import__("rob_stride_compat_marker")  # type: ignore[name-defined]
        return False
    except ImportError:
        return True


def install():
    """Patch RobstrideBus to use gs_usb on macOS. Call BEFORE any robstride_dynamics code."""
    if not _should_patch():
        return

    import builtins
    if getattr(builtins, "__robstride_compat_installed__", False):
        return  # Already installed

    try:
        from robstride_dynamics.bus import RobstrideBus, Motor
        from robstride_dynamics.protocol import CommunicationType as CT, ParameterType as PT
        from robstride_dynamics.table import (  # noqa: F401
            MODEL_MIT_POSITION_TABLE,
            MODEL_MIT_VELOCITY_TABLE,
            MODEL_MIT_TORQUE_TABLE,
            MODEL_MIT_KP_TABLE,
            MODEL_MIT_KD_TABLE,
        )

        # Keep originals for potential uninstall
        orig_connect = RobstrideBus.connect
        orig_disconnect = RobstrideBus.disconnect
        orig_scan_channel = RobstrideBus.scan_channel

        import can
        import struct
        import numpy as np

        # macOS has no in-kernel gs_usb driver, but the library's start() still
        # calls detach_kernel_driver() whenever is_kernel_driver_active() says
        # yes — and after reset() libusb on macOS reports the interface as
        # held by a kernel driver. The detach then fails with
        # "[Errno 13] Access denied", intermittently, on whichever adapter
        # libusb happened to reopen. reset() itself is needed (it clears the
        # device between opens), so keep it and drop only the detach.
        try:
            import gs_usb.gs_usb as _gs

            if not getattr(_gs.GsUsb, "__robstride_mac_start__", False):
                _orig_gs_start = _gs.GsUsb.start

                def _mac_start(
                    self,
                    flags=(_gs.GS_CAN_MODE_NORMAL | _gs.GS_CAN_MODE_HW_TIMESTAMP),
                ):
                    self.gs_usb.reset()

                    flags &= self.device_capability.feature
                    flags &= (
                        _gs.GS_CAN_MODE_LISTEN_ONLY
                        | _gs.GS_CAN_MODE_LOOP_BACK
                        | _gs.GS_CAN_MODE_ONE_SHOT
                        | _gs.GS_CAN_MODE_HW_TIMESTAMP
                    )
                    self.device_flags = flags
                    mode = _gs.DeviceMode(_gs.GS_CAN_MODE_START, flags)
                    self.gs_usb.ctrl_transfer(0x41, _gs._GS_USB_BREQ_MODE, 0, 0, mode.pack())

                _gs.GsUsb.start = _mac_start
                _gs.GsUsb.__robstride_mac_start__ = True
                _gs.GsUsb.__robstride_orig_start__ = _orig_gs_start
        except Exception as e:
            print(f"WARNING: could not patch gs_usb.GsUsb.start ({e}); "
                  f"adapter opens may fail intermittently with 'Access denied'.")

        @property
        def _is_connected(self):
            return getattr(self, "_can_bus", None) is not None

        def _resolve_device_index(channel):
            """Resolve a channel name to a gs_usb adapter index.

            Accepts three forms:
              - an int: used as the gs_usb index directly
              - "canN": mapped to gs_usb index N
              - a USB serial string (recommended): looked up among the attached
                adapters, so the binding survives USB re-enumeration.

            On Linux the adapters appear as SocketCAN interfaces can0/can1. On
            macOS there is no SocketCAN, so python-can enumerates them as gs_usb
            devices 0, 1, ... — but that order is not stable across re-plugs,
            which is why serial binding exists.
            """
            if isinstance(channel, int):
                return channel
            text = str(channel)
            if text.startswith("can") and text[3:].isdigit():
                return int(text[3:])

            from gs_usb.gs_usb import GsUsb

            attached = GsUsb.scan()
            for index, device in enumerate(attached):
                serial = ""
                for _attempt in range(3):
                    try:
                        serial = device.serial_number or ""
                    except Exception:
                        serial = ""
                    if serial:
                        break
                    time.sleep(0.05)
                if serial == text:
                    # Return the index, not bus/address: USB addresses are
                    # reassigned every time the device re-enumerates (and the
                    # adapter does re-enumerate when we open and close it),
                    # which produced "No such device" on the next call.
                    return index
            serials = []
            for device in attached:
                try:
                    serials.append(device.serial_number)
                except Exception:
                    serials.append("<unreadable>")
            raise RuntimeError(
                f"找不到序列号为 '{text}' 的 USB-CAN 适配器。\n"
                f"当前已连接 {len(serials)} 个适配器: {serials}\n"
                f"请检查适配器是否插好；如果换过适配器，请更新 "
                f"arm/config.py 里的 LEFT_ADAPTER_SERIAL / RIGHT_ADAPTER_SERIAL。"
            )

        # macOS releases a USB handle slightly after shutdown(); immediately
        # reopening the same adapter can fail with "Access denied". Retry with
        # backoff before giving up.
        _OPEN_ATTEMPTS = 8
        _OPEN_BACKOFF = 0.7

        def _open_can_bus(channel, bitrate):
            """Open one adapter, retrying while macOS releases the old handle.

            The index is re-resolved on every attempt on purpose: opening and
            closing an adapter makes macOS re-enumerate the USB tree, so an
            index captured before the previous handle was released can already
            point at a different device (or at nothing) by the time we use it.
            """
            last_error = None
            target = channel
            for attempt in range(_OPEN_ATTEMPTS):
                try:
                    target = _resolve_device_index(channel)
                    return can.interface.Bus(
                        interface="gs_usb",
                        channel=target,
                        bitrate=bitrate,
                    )
                except Exception as e:
                    last_error = e
                    if attempt < _OPEN_ATTEMPTS - 1:
                        # Give the device time to finish re-enumerating, then
                        # look the index up again.
                        time.sleep(_OPEN_BACKOFF * (attempt + 1))
            raise last_error

        def gs_connect(self, handshake=True):
            if self.is_connected:
                raise Exception(
                    f"{self.__class__.__name__}('{self.channel}') is already connected. "
                    f"Do not call connect() twice."
                )
            try:
                self._can_bus = _open_can_bus(self.channel, self.bitrate)
            except RuntimeError:
                raise  # adapter not found / couldn't be opened; message is useful
            except Exception as e:
                raise RuntimeError(
                    f"无法打开 USB-CAN 适配器 '{self.channel}'。\n"
                    f"请检查：\n"
                    f"  1. 适配器已插好（candleLight / CANable 固件）\n"
                    f"  2. 设备序列号与 arm/config.py 中配置一致\n"
                    f"  3. ioreg -p IOUSB | grep -i candleLight 能看到设备\n"
                    f"原始错误: {e}"
                ) from e
            self.channel_handler = self._can_bus  # compat shim
            print(f"{self.__class__.__name__} connected ({self.channel}).")

        def gs_disconnect(self, disable_torque=True):
            if not self.is_connected:
                raise Exception(
                    f"{self.__class__.__name__}('{self.channel}') is not connected. "
                    f"Try running connect() first."
                )
            if disable_torque:
                for motor_name in list(self.motors.keys()):
                    try:
                        self.disable(motor_name)
                    except Exception:
                        pass
                print("Torque disabled for all motors.")
            self._can_bus.shutdown()
            self._can_bus = None
            self.channel_handler = None
            print(f"{self.__class__.__name__} disconnected.")

        @classmethod
        def gs_scan_channel(cls, channel: str, start_id: int = 1, end_id: int = 255):
            """Scan a channel for responding motor IDs, opening it once.

            Call this on the class: `RobstrideBus.scan_channel(channel, ...)`.
            Do NOT connect a bus to the same channel first — macOS allows a
            second gs_usb handle on one adapter, but the two handles steal each
            other's frames and every motor then reads as "no response".
            """
            bus = cls(channel, {})
            # Scanning probes every ID; most of them are absent, so suppress
            # the per-ID "no response" warning or the log becomes unreadable.
            bus._quiet = True
            bus.connect(handshake=False)
            device_ids = {}
            try:
                for device_id in range(start_id, end_id):
                    response = bus.ping_by_id(device_id, timeout=0.05)
                    if response is not None:
                        print(f"Motors found for {device_id=}: {response}")
                        device_ids[device_id] = list(response)
            finally:
                try:
                    bus.disconnect()
                except Exception:
                    pass
            return device_ids

        def gs_receive(self, timeout=None):
            # The gs_usb backend echoes our own transmitted frames back. They
            # carry the same device ID as the replies we are waiting for, so
            # they must be dropped or a "reply" could be our own request.
            import time as _time
            deadline = None if timeout is None else _time.time() + timeout
            frame = None
            while deadline is None or _time.time() < deadline:
                remaining = None if deadline is None else max(0.0, deadline - _time.time())
                candidate = self._can_bus.recv(timeout=remaining)
                if candidate is None:
                    break
                if getattr(candidate, "is_rx", True) is False:
                    continue  # echo of a frame we sent
                if not candidate.is_extended_id:
                    continue
                frame = candidate
                break
            if frame is None:
                if not getattr(self, "_quiet", False):
                    print("WARNING: Received no response from the motor")
                return None
            ct = (frame.arbitration_id >> 24) & 0x1F
            ed = (frame.arbitration_id >> 8) & 0xFFFF
            hid = frame.arbitration_id & 0xFF
            return ct, ed, hid, frame.data

        def gs_receive_status_frame(self, motor):
            received = self.receive(timeout=0.1)
            if not received:
                raise RuntimeError(f"No response from the motor {motor}")
            ct, ed, host_id, data = received
            model = self.motors[motor].model

            device_id = (ed >> 0) & 0xFF
            if device_id != self.motors[motor].id:
                print(f"WARNING: Invalid device ID, got {device_id}, expected {self.motors[motor].id}")

            assert ct in [CT.OPERATION_STATUS, CT.FAULT_REPORT], f"Invalid comm type {ct}"

            if ct == CT.FAULT_REPORT:
                fault_value, warning_value = struct.unpack("<LL", data)
                if fault_value & 0x01:
                    print(f"FAULT: {motor} overtemperature")
                if (fault_value >> 1) & 0x01:
                    print(f"FAULT: {motor} drive gate fault")
                if (fault_value >> 2) & 0x01:
                    print(f"FAULT: {motor} undervoltage")
                if (fault_value >> 3) & 0x01:
                    print(f"FAULT: {motor} overvoltage")
                raise RuntimeError(f"Received fault frame from {motor}")

            pos_u, vel_u, tq_u, tmp_u = struct.unpack(">HHHH", data)
            position = (float(pos_u) / 0x7FFF - 1.) * MODEL_MIT_POSITION_TABLE[model]
            velocity = (float(vel_u) / 0x7FFF - 1.) * MODEL_MIT_VELOCITY_TABLE[model]
            torque = (float(tq_u) / 0x7FFF - 1.) * MODEL_MIT_TORQUE_TABLE[model]
            temperature = float(tmp_u) * 0.1
            return position, velocity, torque, temperature

        def gs_receive_read_frame(self):
            ct, ed, hid, data = self.receive()
            assert ct == CT.READ_PARAMETER, f"Invalid comm type {ct}"
            return data[4:]

        def gs_transmit(self, comm_type, extra_data, device_id, data=b"\x00\x00\x00\x00\x00\x00\x00\x00"):
            ext_id = (comm_type << 24) | (extra_data << 8) | device_id
            frame = can.Message(arbitration_id=ext_id, is_extended_id=True, dlc=len(data), data=data)
            self._can_bus.send(frame)

        def gs_read(self, motor, parameter_type):
            device_id = self.motors[motor].id
            param_id, param_dtype, param_name = parameter_type
            data = struct.pack("<HHL", param_id, 0x00, 0x00)
            self.transmit(CT.READ_PARAMETER, self.host_id, device_id, data)
            resp = self.receive_read_frame()
            if param_dtype == np.uint8:
                value, _, _ = struct.unpack("<BBH", resp)
            elif param_dtype == np.int8:
                value, _, _ = struct.unpack("<bBH", resp)
            elif param_dtype == np.uint16:
                value, _ = struct.unpack("<H", resp)
            elif param_dtype == np.int16:
                value, _ = struct.unpack("<h", resp)
            elif param_dtype == np.uint32:
                value, = struct.unpack("<I", resp)
            elif param_dtype == np.int32:
                value, = struct.unpack("<i", resp)
            elif param_dtype == np.float32:
                value, = struct.unpack("<f", resp)
            else:
                raise ValueError(f"Unsupported dtype {param_name}: {param_dtype}")
            return value

        def gs_write(self, motor, parameter_type, value):
            device_id = self.motors[motor].id
            param_id, param_dtype, param_name = parameter_type
            if param_dtype == np.uint8:
                vb = struct.pack("<BBH", int(value), 0, 0)
            elif param_dtype == np.int8:
                vb = struct.pack("<bBH", int(value), 0, 0)
            elif param_dtype == np.uint16:
                vb = struct.pack("<HH", int(value), 0)
            elif param_dtype == np.int16:
                vb = struct.pack("<hH", int(value), 0)
            elif param_dtype == np.uint32:
                vb = struct.pack("<I", int(value))
            elif param_dtype == np.int32:
                vb = struct.pack("<i", int(value))
            elif param_dtype == np.float32:
                vb = struct.pack("<f", float(value))
            else:
                raise ValueError(f"Unsupported dtype {param_name}: {param_dtype}")
            pd = struct.pack("<HH", param_id, 0x00) + vb
            self.transmit(CT.WRITE_PARAMETER, self.host_id, device_id, pd)
            self.receive_status_frame(motor)

        def gs_ping_by_id(self, device_id, timeout=None):
            self.transmit(CT.GET_DEVICE_ID, self.host_id, device_id)
            resp = self.receive(timeout)
            if not resp:
                return None
            ct, did, check, uuid = resp
            return did, uuid

        def gs_enable(self, motor):
            device_id = self.motors[motor].id
            self.transmit(CT.ENABLE, self.host_id, device_id)
            self.receive_status_frame(motor)

        def gs_disable(self, motor):
            device_id = self.motors[motor].id
            self.transmit(CT.DISABLE, self.host_id, device_id)
            self.receive_status_frame(motor)

        def gs_write_operation_frame(self, motor, position, kp, kd, velocity=0, torque=0):
            device_id = self.motors[motor].id
            model = self.motors[motor].model
            if self.calibration:
                cal = self.calibration[motor]
                position = position * cal["direction"] + cal["homing_offset"]
                velocity = velocity * cal["direction"]
                torque = torque * cal["direction"]

            from robstride_dynamics.table import (
                MODEL_MIT_POSITION_TABLE as MP,
                MODEL_MIT_VELOCITY_TABLE as MV,
                MODEL_MIT_KP_TABLE as MKP,
                MODEL_MIT_KD_TABLE as MKD,
                MODEL_MIT_TORQUE_TABLE as MT,
            )
            p_max = MP[model]; v_max = MV[model]
            kp_max = MKP[model]; kd_max = MKD[model]; tq_max = MT[model]

            pos_u16 = int(((np.clip(position, -p_max, p_max) / p_max + 1.) * 0x7FFF))
            vel_u16 = int(((np.clip(velocity, -v_max, v_max) / v_max + 1.) * 0x7FFF))
            kp_u16 = int((np.clip(kp, 0., kp_max) / kp_max) * 0xFFFF)
            kd_u16 = int((np.clip(kd, 0., kd_max) / kd_max) * 0xFFFF)
            tq_u16 = np.clip(int((torque / tq_max + 1.) * 0x7FFF), 0, 0xFFFF)

            data = struct.pack(">HHHH", pos_u16, vel_u16, kp_u16, kd_u16)
            self.transmit(CT.OPERATION_CONTROL, tq_u16, device_id, data)

        def gs_read_operation_frame(self, motor):
            status = self.receive_status_frame(motor)
            position, velocity, torque, temperature = status
            if self.calibration:
                cal = self.calibration[motor]
                position = (position - cal["homing_offset"]) * cal["direction"]
                velocity *= cal["direction"]
                torque *= cal["direction"]
            return position, velocity, torque, temperature

        # Keep a reference to the patched class; the SDK's own __del__ calls
        # self.is_connected and would blow up on a half-constructed instance.
        _orig_del = RobstrideBus.__del__

        def _safe_del(self):
            try:
                _orig_del(self)
            except Exception:
                pass

        RobstrideBus.__del__ = _safe_del

        # Apply all patches
        RobstrideBus.connect = gs_connect
        RobstrideBus.disconnect = gs_disconnect
        RobstrideBus.scan_channel = classmethod(gs_scan_channel)
        RobstrideBus.receive = gs_receive
        RobstrideBus.receive_status_frame = gs_receive_status_frame
        RobstrideBus.receive_read_frame = gs_receive_read_frame
        RobstrideBus.transmit = gs_transmit
        RobstrideBus.read = gs_read
        RobstrideBus.write = gs_write
        RobstrideBus.ping_by_id = gs_ping_by_id
        RobstrideBus.enable = gs_enable
        RobstrideBus.disable = gs_disable
        RobstrideBus.write_operation_frame = gs_write_operation_frame
        RobstrideBus.read_operation_frame = gs_read_operation_frame
        RobstrideBus.is_connected = _is_connected  # type: ignore[assignment]

        # Mark as installed
        import builtins
        builtins.__robstride_compat_installed__ = True  # type: ignore[attr-defined]

    except ImportError as e:
        raise RuntimeError(
            f"robstride_compat.install() failed: robstride_dynamics or python-can/gs_usb not available.\n\n"
            f"Please install all dependencies first:\n"
            f"  pip install python-can[gs-usb] pyusb\n"
            f"  brew install libusb\n\n"
            f"Original error: {e}"
        )


def uninstall():
    """Restore original RobstrideBus methods (if originals are still available)."""
    import builtins
    if not getattr(builtins, "__robstride_compat_installed__", False):  # type: ignore[attr-defined]
        return
    try:
        from robstride_dynamics.bus import RobstrideBus
        for name in ["connect", "disconnect"]:
            orig = getattr(RobstrideBus, f"_orig_{name}", None)
            if orig:
                setattr(RobstrideBus, name, orig)
    except Exception:
        pass
