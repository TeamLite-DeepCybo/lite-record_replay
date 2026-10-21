import json
import argparse
import numpy as np
import os

from loop_rate_limiters import RateLimiter

from arm import Arm
from api import prepare_arm, shutdown_arm
from bus_autodetect import autodetect_arm_setup

parser = argparse.ArgumentParser()
parser.add_argument("--name", "-n", type=str, default="", help="replay name")
args = parser.parse_args()
replay_name = args.name

# 自动检测通道 + 在线电机；离线电机（例如接线问题只有前几个能扫到）自动忽略
bus_configs, active_motor_configs = autodetect_arm_setup()

np.set_printoptions(precision=4, linewidth=100)

calibration = json.load(open("ude_calibration.json"))
arm = Arm(bus_configs, active_motor_configs, calibration)

print(f"本次录制的关节 ({arm.num_joints} 个): {arm.joint_names}")

rate = RateLimiter(frequency=50.0)

kp = 0.0
kd = 0.0
# kd = 0.25

recorded_motion = []

prepare_arm(arm, torque_limits={"default": 1.0}, strict=True)

stop_file = "/tmp/stop_record.flag"
if os.path.exists(stop_file):
    os.remove(stop_file)

try:
    while not os.path.exists(stop_file):
        positions = np.zeros(arm.num_joints, dtype=np.float32)
        velocities = np.zeros(arm.num_joints, dtype=np.float32)

        for motor_name, entry in arm.motors.items():
            bus, motor = entry
            index = arm.joint_names.index(motor_name)
            bus.write_operation_frame(motor_name, 0.0, kp, kd)
            position, velocity, _, _ = bus.read_operation_frame(motor_name)
            positions[index] = position
            velocities[index] = velocity

        print(f"positions: {positions} \t velocities: {velocities}")

        recorded_motion.append([positions, velocities])

        rate.sleep()

except KeyboardInterrupt:
    pass

shutdown_arm(arm)

recorded_motion = np.array(recorded_motion)

np.save(f"recorded_motion_{replay_name}.npy", recorded_motion)

# 同时保存关节名单，回放时按名字对齐（即使当时在线的电机集合不同）
with open(f"recorded_motion_{replay_name}.joints.json", "w") as f:
    json.dump(arm.joint_names, f, indent=4)

print(f"Saved {recorded_motion.shape[0]} frames of {arm.num_joints} joints to recorded_motion_{replay_name}.npy")
print("Program terminated.")
