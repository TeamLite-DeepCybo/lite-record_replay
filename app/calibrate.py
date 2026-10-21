#!/usr/bin/env python3
"""标定：把每个关节推到机械上下限，算出 home 偏移量，写入 calibration.json。

用法：
    python3 calibrate.py                 # 自动识别 CAN 口
    python3 calibrate.py -c can2,can1    # 手动指定左右臂的 CAN 口

步骤：
    1. 机械臂会先软掉（力矩很小），可以直接用手拖动；
    2. 把**每一个**关节缓慢推到上限、再推到下限（推到顶停一下），
       夹爪同样推到全开和全闭；
    3. 按 Ctrl-C 结束，程序会显示每个关节的实际行程和算出的偏移量，
       确认后写入 calibration.json。

程序只把"行程走够了"的关节设为新标定值，没走到位的关节保留原来的值，
不会因为漏推某个关节把整车标定弄坏。
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from bhl import config, detect, storage
from bhl.arm import Arm
from bhl.util import RateLimiter

COVERAGE = 0.8  # 行程至少要走到理论行程的 80% 才算"推到位了"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prime Lite 双臂标定")
    parser.add_argument("--channels", "-c", default="", help="手动指定左右臂 CAN 口，例如 can2,can1")
    parser.add_argument("--yes", "-y", action="store_true", help="不询问，直接写入")
    args = parser.parse_args()

    forced = detect.parse_channels(args.channels) if args.channels else None
    channels = list(dict.fromkeys(forced.values())) if forced else None

    ready, messages = detect.ensure_can(channels, on_progress=print)
    for message in messages:
        print(f"  · {message}")
    if not ready:
        return 1

    print("扫描电机 …")
    setup = detect.scan(forced=forced, on_progress=print)
    print(setup.describe())

    arm = Arm(setup.channels, None)  # 标定时不能用旧标定
    names = arm.joint_names
    count = arm.num_joints

    directions = np.array([config.MOTORS_BY_NAME[name].direction for name in names], dtype=float)
    lower = np.array([config.MOTORS_BY_NAME[name].lower for name in names], dtype=float)
    upper = np.array([config.MOTORS_BY_NAME[name].upper for name in names], dtype=float)

    low_seen = np.full(count, np.inf)
    high_seen = np.full(count, -np.inf)
    ever_seen = np.zeros(count, dtype=bool)

    rate = RateLimiter(config.RECORD_HZ)

    print()
    print(f"每个关节都要推到上限和下限各一次（夹爪推到全开/全闭）。按 Ctrl-C 结束。")
    print()

    arm.begin(config.RECORD_TORQUE_LIMIT)
    last_print = 0.0
    try:
        while True:
            measured, _, _ = arm.step(kp=0.0, kd=0.0)
            adjusted = measured * directions

            valid = np.array([name not in arm.missing_now for name in names])
            ever_seen |= valid
            low_seen = np.where(valid, np.minimum(low_seen, adjusted), low_seen)
            high_seen = np.where(valid, np.maximum(high_seen, adjusted), high_seen)

            now = time.perf_counter()
            if now - last_print > 0.4:
                last_print = now
                span = np.where(ever_seen, high_seen - low_seen, 0.0)
                done = int(np.count_nonzero(span >= COVERAGE * (upper - lower)))
                print(f"\r已推到位 {done}/{count} 个关节 …", end="", flush=True)

            rate.sleep()
    except KeyboardInterrupt:
        print()
    finally:
        arm.end()
        arm.close()

    # ---- 计算 --------------------------------------------------------
    try:
        existing, source, _ = storage.load_calibration()
        previous = {name: entry["homing_offset"] for name, entry in existing.items()}
    except Exception:
        previous = {}
        source = "（无）"

    offsets: dict[str, float] = dict(previous)
    updated: list[str] = []
    skipped: list[str] = []

    for index, name in enumerate(names):
        if not ever_seen[index]:
            skipped.append(f"{name}：全程没有读到数据")
            continue

        span = high_seen[index] - low_seen[index]
        expected = upper[index] - lower[index]
        if span < COVERAGE * expected:
            skipped.append(
                f"{name}：只走了 {span:.2f} rad（理论 {expected:.2f} rad），保留原标定"
            )
            continue

        lower_offset = low_seen[index] - lower[index]
        upper_offset = high_seen[index] - upper[index]
        offsets[name] = float(((lower_offset + upper_offset) / 2) * directions[index])
        updated.append(name)

    print()
    print(f"原标定文件：{source}")
    print(f"本次更新 {len(updated)} 个关节：")
    for index, name in enumerate(names):
        if name not in updated:
            continue
        limit_low = config.MOTORS_BY_NAME[name].lower
        limit_high = config.MOTORS_BY_NAME[name].upper
        print(
            f"  {name:24s} 行程 {high_seen[index] - low_seen[index]:6.3f} rad"
            f" / 理论 {limit_high - limit_low:6.3f}"
            f"   偏移 {offsets[name]:+.4f}"
        )

    if skipped:
        print()
        print(f"以下 {len(skipped)} 个关节没有更新：")
        for item in skipped:
            print(f"  · {item}")

    if not updated:
        print()
        print("没有任何关节推到位，未写入。")
        return 1

    print()
    if not args.yes:
        answer = input("写入 calibration.json 吗？(y/N) ").strip().lower()
        if answer not in ("y", "yes"):
            print("已取消，未写入。")
            return 0

    path = storage.save_calibration(offsets)
    print(f"已写入 {path}")
    print("标定变了之后，之前录的动作位置基准也会变，建议重新录一遍。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
