import json
import os
import argparse
import numpy as np

from loop_rate_limiters import RateLimiter

from config import motor_configs
from arm import Arm
from api import prepare_arm, shutdown_arm
from bus_autodetect import autodetect_arm_setup

parser = argparse.ArgumentParser()
parser.add_argument("--name", "-n", type=str, default="", help="replay name")
args = parser.parse_args()
replay_name = args.name

np.set_printoptions(precision=4, linewidth=100)

recorded_motion = np.load(f"recorded_motion_{replay_name}.npy")
print(recorded_motion.shape)

# 录制时保存的关节名单；老录制文件没有名单时，按完整配置的顺序处理
joints_file = f"recorded_motion_{replay_name}.joints.json"
if os.path.exists(joints_file):
    recorded_joint_names = json.load(open(joints_file))
else:
    recorded_joint_names = list(motor_configs.keys())

if len(recorded_joint_names) != recorded_motion.shape[2]:
    raise RuntimeError(
        f"录制数据有 {recorded_motion.shape[2]} 个关节，但关节名单有 {len(recorded_joint_names)} 个，"
        f"无法对齐。请检查 {joints_file}。"
    )

# 自动检测通道 + 在线电机
bus_configs, active_motor_configs = autodetect_arm_setup()

# 只回放"录制里有 且 当前在线"的关节，其余忽略
replayable_motor_configs = {
    name: motor for name, motor in active_motor_configs.items() if name in recorded_joint_names
}

skipped_offline = [name for name in recorded_joint_names if name not in active_motor_configs]
if skipped_offline:
    print(f"警告: 以下已录制关节当前离线，将跳过回放: {skipped_offline}")

if not replayable_motor_configs:
    raise RuntimeError("没有任何可回放的关节（录制关节与在线电机没有交集）。")

calibration = json.load(open("ude_calibration.json"))
arm = Arm(bus_configs, replayable_motor_configs, calibration)

print(f"本次回放的关节 ({arm.num_joints} 个): {arm.joint_names}")

# 每个回放关节在录制数据中的列号
recorded_index = {name: recorded_joint_names.index(name) for name in arm.joint_names}

rate = RateLimiter(frequency=50.0)

# tested up to kp 60, kd 2, 6 Nm
kp = 80.0
kd = 4.0

prepare_arm(arm, strict=True)

frame_index = 0

measured_torques = np.zeros(arm.num_joints, dtype=np.float32)

try:
    while True:
        positions, velocities = recorded_motion[frame_index]

        for motor_name, entry in arm.motors.items():
            bus, motor = entry
            index = arm.joint_names.index(motor_name)
            rec_index = recorded_index[motor_name]
            bus.write_operation_frame(motor_name, positions[rec_index], kp, kd, velocity=velocities[rec_index])
            _, _, torque, _ = bus.read_operation_frame(motor_name)
            measured_torques[index] = torque

        frame_index += 1

        if frame_index >= recorded_motion.shape[0]:
            break

        print(f"max torque: {np.abs(measured_torques).max():.2f} Nm")

        rate.sleep()

except KeyboardInterrupt:
    pass

shutdown_arm(arm)

print("Program terminated.")
