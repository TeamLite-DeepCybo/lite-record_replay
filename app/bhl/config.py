"""电机配置：整个程序唯一的电机表。

要改机器人（加/减夹爪、换电机型号、调关节限位），只改这个文件。
CAN 通道**不用**在这里改：程序启动时会自动识别每条臂接在哪个 CAN 口，
也可以临时用 `python3 main.py --channels can2,can1` 手动指定。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _deg(value: float) -> float:
    """角度转弧度，写电机表时读起来直观。"""
    return math.radians(value)


@dataclass(frozen=True)
class MotorSpec:
    """一个电机（关节）的固定属性。"""

    name: str  # 关节名；录制文件里存的就是它，改名会导致旧录制对不上
    side: str  # "left" / "right"
    can_id: int  # 电机拨码 ID
    model: str  # 型号：rs-00 / rs-02 / rs-05
    direction: int  # 关节正方向与电机正方向一致为 +1，相反为 -1
    lower: float  # 机械限位下限（弧度）
    upper: float  # 机械限位上限（弧度）
    kind: str = "joint"  # "joint" 关节 / "gripper" 夹爪（可以不存在）

    @property
    def is_gripper(self) -> bool:
        return self.kind == "gripper"


# ==========================================================================
# 电机表（唯一事实来源）
# ==========================================================================
MOTORS: tuple[MotorSpec, ...] = (
    # ---------------- 左臂 ----------------
    MotorSpec("left_shoulder_pitch", "left", 11, "rs-02", -1, _deg(-180), _deg(45)),
    MotorSpec("left_shoulder_roll", "left", 12, "rs-00", -1, _deg(-10), _deg(190)),
    MotorSpec("left_shoulder_yaw", "left", 13, "rs-00", +1, _deg(-90), _deg(90)),
    MotorSpec("left_elbow_pitch", "left", 14, "rs-00", -1, _deg(-135), _deg(0)),
    MotorSpec("left_wrist_yaw", "left", 15, "rs-05", +1, _deg(-90), _deg(90)),
    MotorSpec("left_wrist_roll", "left", 16, "rs-05", -1, _deg(-40), _deg(40)),
    MotorSpec("left_wrist_pitch", "left", 17, "rs-05", -1, _deg(-45), _deg(45)),
    MotorSpec("left_gripper", "left", 18, "rs-05", -1, _deg(0), _deg(114), "gripper"),
    # ---------------- 右臂 ----------------
    MotorSpec("right_shoulder_pitch", "right", 21, "rs-02", +1, _deg(-180), _deg(45)),
    MotorSpec("right_shoulder_roll", "right", 22, "rs-00", -1, _deg(-190), _deg(10)),
    MotorSpec("right_shoulder_yaw", "right", 23, "rs-00", +1, _deg(-90), _deg(90)),
    MotorSpec("right_elbow_pitch", "right", 24, "rs-00", +1, _deg(-135), _deg(0)),
    MotorSpec("right_wrist_yaw", "right", 25, "rs-05", +1, _deg(-90), _deg(90)),
    MotorSpec("right_wrist_roll", "right", 26, "rs-05", +1, _deg(-40), _deg(40)),
    MotorSpec("right_wrist_pitch", "right", 27, "rs-05", -1, _deg(-45), _deg(45)),
    MotorSpec("right_gripper", "right", 28, "rs-05", -1, _deg(0), _deg(380), "gripper"),
)

MOTORS_BY_NAME: dict[str, MotorSpec] = {motor.name: motor for motor in MOTORS}

_ORDER: dict[str, int] = {motor.name: index for index, motor in enumerate(MOTORS)}


def sort_names(names) -> list[str]:
    """按电机表顺序排列关节名，保证录制/回放的列顺序永远一致。"""
    return sorted(names, key=lambda name: _ORDER[name])


# ==========================================================================
# 运行参数
# ==========================================================================
SIDES = ("left", "right")

# 自动识别时扫描哪些 CAN 口（和 canup.sh 里启用的保持一致）
CANDIDATE_CHANNELS = ("can0", "can1", "can2", "can3", "can4")

# 自动识别失败 / 手动模式下的兜底映射
DEFAULT_CHANNELS = {"left": "can0", "right": "can1"}

RECORD_HZ = 50.0  # 录制采样率
REPLAY_HZ = 50.0  # 回放控制周期
# 回放时对"速度前馈"做零相位低通的时间常数（秒）。0 = 不平滑。
# 位置通道不用平滑：实测相邻帧差只有 0.0004 rad，很干净；
# 而电机低速时的速度反馈基本是噪声（±0.2 rad/s），直接当前馈会被 kd 放大成可见抖动。
REPLAY_VELOCITY_TAU = 0.02

DEFAULT_KP = 80.0  # 回放刚度（原参数：kp=80 / kd=4 已实测稳定）
DEFAULT_KD = 4.0
DAMPING_KD = 0.5  # 结束时阻尼落用的 kd

RECORD_TORQUE_LIMIT = 1.0  # 拖动示教时的力矩上限（越小越软）
MAX_CONSECUTIVE_MISS = 20  # 连续多少个周期没回帧就判定掉线并停机（50Hz 下约 0.4 秒）

REPLAY_SOFT_START_S = 0.5  # 回放前从当前姿态平滑过渡到首帧的最短时间（秒）
APPROACH_SPEED = 0.4  # 靠近速度上限（rad/s）—— 断开重上电后姿态可能差很远，慢一点更安全
APPROACH_MAX_S = 25.0  # 靠近最长用时（秒）
APPROACH_MIN_GAP = 0.05  # 姿态差小于这个值就不做靠近，直接开始
REPLAY_BRIDGE_S = 0.5  # 多次回放时，两次之间过渡的时间（秒）
END_DAMPING_S = 0.3  # 结束动作时先阻尼多久再失能（秒）

# 注意：下面每个电机里的 lower/upper 只用于标定和界面上显示，
# **不参与回放** —— 回放时录制了什么就原封不动发什么，不做裁剪。

# 各型号力矩上限（Nm）
TORQUE_LIMITS = {"rs-00": 10.0, "rs-02": 10.0, "rs-05": 5.0, "default": 10.0}

# 标定文件（放在程序目录下）
CALIBRATION_FILE = "calibration.json"
CALIBRATION_FALLBACKS = ("ude_calibration.json",)

# 机器人类型：界面上可选，决定"应该有哪些关节"
ROBOT_TYPES = ("auto", "gripper", "no_gripper")
ROBOT_TYPE_LABELS = {
    "auto": "自动识别",
    "gripper": "有夹爪",
    "no_gripper": "无夹爪",
}


def specs(names) -> list[MotorSpec]:
    """关节名列表 -> 电机属性列表。"""
    return [MOTORS_BY_NAME[name] for name in names]


def all_names() -> list[str]:
    """含夹爪的完整关节名。"""
    return [motor.name for motor in MOTORS]


def names_without_gripper() -> list[str]:
    """不含夹爪的关节名（旧录制文件 14 列时按这个顺序解释）。"""
    return [motor.name for motor in MOTORS if not motor.is_gripper]


def names_for(robot_type: str | None = None) -> list[str]:
    """该机器人类型应有的关节名。auto / None 表示不做筛选。"""
    if robot_type == "no_gripper":
        return names_without_gripper()
    return all_names()


def torque_limit(model: str) -> float:
    return TORQUE_LIMITS.get(model, TORQUE_LIMITS["default"])
