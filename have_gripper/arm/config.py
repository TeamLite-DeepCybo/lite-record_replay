import _entry_point_init  # noqa: F401 -- macOS shim

import sys

import numpy as np

from robstride_dynamics import Motor

# ---------------------------------------------------------------------------
# 适配器绑定
# ---------------------------------------------------------------------------
# Ubuntu 上左臂固定接 can0、右臂接 can1，通道名就是物理接线。
#
# macOS 没有 SocketCAN，python-can 按 USB 枚举顺序给适配器编号，这个顺序
# 每次插拔都会变；序列号虽然稳定，但换一块板子 / 换一台机器就全变了。
#
# 所以默认走「按电机 ID 自动识别」：把每个适配器都扫一遍，哪个口上应答的是
# 左臂的 ID 就是左臂，是右臂的就是右臂。换板子、换机器、换插口都不用改代码。
#
# 想强制指定某个口时，把下面两个填成适配器序列号即可（留空 = 自动识别）。
# 查看当前接上的序列号：
#     .venv/bin/python discover_actuators.py --list
LEFT_ADAPTER_SERIAL = ""
RIGHT_ADAPTER_SERIAL = ""

# 自动识别时，一侧至少要有几个电机应答才判定成功（防止误判到别的设备）。
AUTODETECT_MIN_HITS = 2

# 已经识别过一次就缓存住，避免同一次运行里反复扫描。
_bus_config_cache: dict[str, str] | None = None


def _autodetect(log: bool = False) -> dict[str, str]:
    """按电机 ID 识别左右臂通道。仅 macOS 可用。"""
    import robstride_compat

    configs = motor_with_gripper_configs if is_arm_with_gripper else motor_without_gripper_configs
    return robstride_compat.discover_arm_channels(
        configs,
        min_hits=AUTODETECT_MIN_HITS,
        verbose=log,
    )


def generate_bus_config(left_can_bus: int = 0, right_can_bus: int = 1, *, log: bool = False):
    """返回 {side: channel}。

    channel 的取值按优先级：
      1. 模块顶部的 LEFT_ADAPTER_SERIAL / RIGHT_ADAPTER_SERIAL（手动指定）
      2. macOS：自动识别（按电机 ID，结果缓存）
      3. canN（Linux 行为）
    """
    global _bus_config_cache

    if LEFT_ADAPTER_SERIAL and RIGHT_ADAPTER_SERIAL:
        return {"left": LEFT_ADAPTER_SERIAL, "right": RIGHT_ADAPTER_SERIAL}

    if sys.platform == "darwin":
        if _bus_config_cache is None:
            _bus_config_cache = _autodetect(log=log)
        return dict(_bus_config_cache)

    return {"left": f"can{left_can_bus}", "right": f"can{right_can_bus}"}


def reset_bus_config_cache():
    """丢弃自动识别的缓存（换线 / 换板子后调用）。"""
    global _bus_config_cache
    _bus_config_cache = None

is_arm_with_gripper = True

motor_without_gripper_configs = {
    "left_shoulder_pitch": Motor(id=11, model="rs-02"),
    "left_shoulder_roll": Motor(id=12, model="rs-00"),
    "left_shoulder_yaw": Motor(id=13, model="rs-00"),
    "left_elbow_pitch": Motor(id=14, model="rs-00"),
    "left_wrist_yaw": Motor(id=15, model="rs-05"),
    "left_wrist_roll": Motor(id=16, model="rs-05"),
    "left_wrist_pitch": Motor(id=17, model="rs-05"),
    "right_shoulder_pitch": Motor(id=21, model="rs-02"),
    "right_shoulder_roll": Motor(id=22, model="rs-00"),
    "right_shoulder_yaw": Motor(id=23, model="rs-00"),
    "right_elbow_pitch": Motor(id=24, model="rs-00"),
    "right_wrist_yaw": Motor(id=25, model="rs-05"),
    "right_wrist_roll": Motor(id=26, model="rs-05"),
    "right_wrist_pitch": Motor(id=27, model="rs-05"),
}

motor_with_gripper_configs = {
    "left_shoulder_pitch": Motor(id=11, model="rs-02"),
    "left_shoulder_roll": Motor(id=12, model="rs-00"),
    "left_shoulder_yaw": Motor(id=13, model="rs-00"),
    "left_elbow_pitch": Motor(id=14, model="rs-00"),
    "left_wrist_yaw": Motor(id=15, model="rs-05"),
    "left_wrist_roll": Motor(id=16, model="rs-05"),
    "left_wrist_pitch": Motor(id=17, model="rs-05"),
    "left_gripper": Motor(id=18, model="rs-05"),
    "right_shoulder_pitch": Motor(id=21, model="rs-02"),
    "right_shoulder_roll": Motor(id=22, model="rs-00"),
    "right_shoulder_yaw": Motor(id=23, model="rs-00"),
    "right_elbow_pitch": Motor(id=24, model="rs-00"),
    "right_wrist_yaw": Motor(id=25, model="rs-05"),
    "right_wrist_roll": Motor(id=26, model="rs-05"),
    "right_wrist_pitch": Motor(id=27, model="rs-05"),
    "right_gripper": Motor(id=28, model="rs-05"),
}

motor_without_gripper_master_configs = {
    "left_shoulder_pitch": Motor(id=11, model="rs-02"),
    "left_shoulder_roll": Motor(id=12, model="rs-00"),
    "left_shoulder_yaw": Motor(id=13, model="rs-00"),
    "left_elbow_pitch": Motor(id=14, model="rs-00"),
    "left_wrist_yaw": Motor(id=15, model="rs-05"),
    "left_wrist_roll": Motor(id=16, model="rs-05"),
    "left_wrist_pitch": Motor(id=17, model="rs-05"),
    "right_shoulder_pitch": Motor(id=21, model="rs-02"),
    "right_shoulder_roll": Motor(id=22, model="rs-00"),
    "right_shoulder_yaw": Motor(id=23, model="rs-00"),
    "right_elbow_pitch": Motor(id=24, model="rs-00"),
    "right_wrist_yaw": Motor(id=25, model="rs-05"),
    "right_wrist_roll": Motor(id=26, model="rs-05"),
    "right_wrist_pitch": Motor(id=27, model="rs-05"),
}

motor_without_gripper_slave_configs = {
    "left_shoulder_pitch": Motor(id=31, model="rs-02"),
    "left_shoulder_roll": Motor(id=32, model="rs-00"),
    "left_shoulder_yaw": Motor(id=33, model="rs-00"),
    "left_elbow_pitch": Motor(id=34, model="rs-00"),
    "left_wrist_yaw": Motor(id=35, model="rs-05"),
    "left_wrist_roll": Motor(id=36, model="rs-05"),
    "left_wrist_pitch": Motor(id=37, model="rs-05"),
    "right_shoulder_pitch": Motor(id=41, model="rs-02"),
    "right_shoulder_roll": Motor(id=42, model="rs-00"),
    "right_shoulder_yaw": Motor(id=43, model="rs-00"),
    "right_elbow_pitch": Motor(id=44, model="rs-00"),
    "right_wrist_yaw": Motor(id=45, model="rs-05"),
    "right_wrist_roll": Motor(id=46, model="rs-05"),
    "right_wrist_pitch": Motor(id=47, model="rs-05"),
}

motor_without_gripper_limits = {
    "left_shoulder_pitch":  (np.deg2rad(-180),  np.deg2rad(45)),
    "left_shoulder_roll":   (np.deg2rad(-10),  np.deg2rad(190)),
    "left_shoulder_yaw":    (np.deg2rad(-90),   np.deg2rad(90)),
    "left_elbow_pitch":     (np.deg2rad(-135),  np.deg2rad(0)),
    "left_wrist_yaw":       (np.deg2rad(-90),   np.deg2rad(90)),
    "left_wrist_roll":      (np.deg2rad(-40),   np.deg2rad(40)),
    "left_wrist_pitch":     (np.deg2rad(-45),   np.deg2rad(45)),
    "right_shoulder_pitch": (np.deg2rad(-180),  np.deg2rad(45)),
    "right_shoulder_roll":  (np.deg2rad(-190),  np.deg2rad(10)),
    "right_shoulder_yaw":   (np.deg2rad(-90),   np.deg2rad(90)),
    "right_elbow_pitch":    (np.deg2rad(-135),  np.deg2rad(0)),
    "right_wrist_yaw":      (np.deg2rad(-90),   np.deg2rad(90)),
    "right_wrist_roll":     (np.deg2rad(-40),   np.deg2rad(40)),
    "right_wrist_pitch":    (np.deg2rad(-45),   np.deg2rad(45)),
}

motor_with_gripper_limits = {
    "left_shoulder_pitch":  (np.deg2rad(-180),  np.deg2rad(45)),
    "left_shoulder_roll":   (np.deg2rad(-10),  np.deg2rad(190)),
    "left_shoulder_yaw":    (np.deg2rad(-90),   np.deg2rad(90)),
    "left_elbow_pitch":     (np.deg2rad(-135),  np.deg2rad(0)),
    "left_wrist_yaw":       (np.deg2rad(-90),   np.deg2rad(90)),
    "left_wrist_roll":      (np.deg2rad(-40),   np.deg2rad(40)),
    "left_wrist_pitch":     (np.deg2rad(-45),   np.deg2rad(45)),
    "left_gripper":         (np.deg2rad(0),     np.deg2rad(114)),
    "right_shoulder_pitch": (np.deg2rad(-180),  np.deg2rad(45)),
    "right_shoulder_roll":  (np.deg2rad(-190),  np.deg2rad(10)),
    "right_shoulder_yaw":   (np.deg2rad(-90),   np.deg2rad(90)),
    "right_elbow_pitch":    (np.deg2rad(-135),  np.deg2rad(0)),
    "right_wrist_yaw":      (np.deg2rad(-90),   np.deg2rad(90)),
    "right_wrist_roll":     (np.deg2rad(-40),   np.deg2rad(40)),
    "right_wrist_pitch":    (np.deg2rad(-45),   np.deg2rad(45)),
    "right_gripper":        (np.deg2rad(0),     np.deg2rad(380)),
}

motor_configs = motor_with_gripper_configs if is_arm_with_gripper else motor_without_gripper_configs
motor_limits = motor_with_gripper_limits if is_arm_with_gripper else motor_without_gripper_limits
motor_master_configs = motor_without_gripper_master_configs
motor_slave_configs = motor_without_gripper_slave_configs
