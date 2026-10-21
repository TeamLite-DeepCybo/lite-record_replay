#!/usr/bin/env python3
"""看一下当前的接线情况：CAN 口状态、每条臂在哪个口、哪些电机在线。

排查"为什么少了一个关节""为什么连不上"的时候先跑这个。

    python3 discover.py
    python3 discover.py -c can2,can1
"""

from __future__ import annotations

import argparse
import sys

from bhl import config, detect


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描 CAN 总线，查看电机在线情况")
    parser.add_argument("--channels", "-c", default="", help="只扫指定的 CAN 口，例如 can2,can1")
    parser.add_argument(
        "--robot",
        "-r",
        default="auto",
        choices=config.ROBOT_TYPES,
        help="按哪种机型检查：auto 自动（默认）/ gripper 有夹爪 / no_gripper 无夹爪",
    )
    args = parser.parse_args()

    print("== CAN 口状态 ==")
    for channel in config.CANDIDATE_CHANNELS:
        status = detect.can_status(channel)
        if status["state"] == "missing":
            print(f"  {channel:6s} 不存在")
        elif status["state"] == "down":
            print(f"  {channel:6s} 存在但未启动")
        else:
            bitrate = status["bitrate"]
            hint = "" if bitrate in (None, detect.BITRATE) else f"   ⚠ 波特率应为 {detect.BITRATE}"
            print(f"  {channel:6s} 正常（{bitrate or '?'} bps）{hint}")

    forced = detect.parse_channels(args.channels) if args.channels else None
    channels = list(dict.fromkeys(forced.values())) if forced else None

    print()
    ready, messages = detect.ensure_can(channels, on_progress=print)
    for message in messages:
        print(f"  · {message}")
    if not ready:
        return 1

    print()
    print("== 扫描电机 ==")
    try:
        setup = detect.scan(channels, forced=forced, expect=args.robot, on_progress=print)
    except detect.DetectError as exc:
        print(f"失败：{exc}")
        return 1

    print()
    print(setup.describe())
    print()
    print("== 关节清单 ==")
    for name in setup.motor_names:
        spec = config.MOTORS_BY_NAME[name]
        print(f"  {name:24s} ID {spec.can_id:2d}  {spec.model}  -> {detect_channel(setup, name)}")

    if setup.missing:
        print()
        print("== 按所选机型缺少 ==")
        for name in setup.missing:
            spec = config.MOTORS_BY_NAME[name]
            print(f"  {name:24s} ID {spec.can_id:2d}  {spec.model}")

    offline = [
        name
        for name in config.all_names()
        if name not in setup.motor_names and name not in setup.missing
    ]
    if offline:
        print()
        print("== 未在线 ==")
        for name in offline:
            spec = config.MOTORS_BY_NAME[name]
            print(f"  {name:24s} ID {spec.can_id:2d}  {spec.model}")

    return 0


def detect_channel(setup, name: str) -> str:
    for channel, names in setup.channels.items():
        if name in names:
            return channel
    return "?"


if __name__ == "__main__":
    sys.exit(main())
