#!/usr/bin/env python3
"""看一下当前的接线情况：CAN 总线、每条臂挂在哪、哪些电机在线。

排查"为什么少了一个关节""为什么连不上"的时候先跑这个。

    python3 app/discover.py                 # Linux：扫描 can0~can4
    python3 app/discover.py -c can2,can1    # 只扫指定口
    python3 app/discover.py --interface pcan   # Windows：指定 CAN 驱动
"""

from __future__ import annotations

import argparse
import sys

from bhl import config, detect


def show_buses(interface: str) -> None:
    """Linux 看 ip link；Windows / macOS 看 python-can 能枚举到什么适配器。"""
    if not sys.platform.startswith("linux"):
        print("== CAN 适配器 ==")
        targets = detect.list_targets(interface)
        if not targets:
            print("  没有检测到适配器（检查 USB-CAN 驱动，或用 --interface 指定驱动）")
            for item in detect.missing_components():
                print(f"    - {item}")
        for target in targets:
            print(f"  {target.label:26s} 驱动 {target.driver:12s} channel={target.channel!r}")
        return

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


def report(setup) -> None:
    print()
    print(setup.describe())
    print()
    print("== 关节清单 ==")
    for name in setup.motor_names:
        spec = config.MOTORS_BY_NAME[name]
        print(f"  {name:24s} ID {spec.can_id:2d}  {spec.model}  -> {channel_of(setup, name)}")

    if setup.missing:
        print()
        print("== 按所选机型缺少 ==")
        for name in setup.missing:
            spec = config.MOTORS_BY_NAME[name]
            print(f"  {name:24s} ID {spec.can_id:2d}  {spec.model}")

    offline = [
        name for name in config.all_names()
        if name not in setup.motor_names and name not in setup.missing
    ]
    if offline:
        print()
        print("== 未在线 ==")
        for name in offline:
            spec = config.MOTORS_BY_NAME[name]
            print(f"  {name:24s} ID {spec.can_id:2d}  {spec.model}")


def channel_of(setup, name: str) -> str:
    for label, names in setup.channels.items():
        if name in names:
            return label
    return "?"


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描 CAN 总线，查看电机在线情况")
    parser.add_argument(
        "--interface",
        default="auto",
        help="CAN 驱动：Linux 默认 socketcan；Windows 可填 pcan / canalystii / slcan / gs_usb 等",
    )
    parser.add_argument("--channels", "-c", default="", help="只扫指定的总线，例如 can2,can1")
    parser.add_argument(
        "--robot",
        "-r",
        default="auto",
        choices=config.ROBOT_TYPES,
        help="按哪种机型检查：auto 自动（默认）/ gripper 有夹爪 / no_gripper 无夹爪",
    )
    args = parser.parse_args()

    show_buses(args.interface)

    forced = detect.parse_channels(args.channels) if args.channels else None
    channels = list(dict.fromkeys(forced.values())) if forced else None

    print()
    ready, messages = detect.ensure_can(channels, on_progress=print, interface=args.interface)
    for message in messages:
        print(f"  · {message}")
    if not ready:
        return 1

    print()
    print("== 扫描电机 ==")
    try:
        setup = detect.scan(
            channels,
            forced=forced,
            expect=args.robot,
            interface=args.interface,
            on_progress=print,
        )
    except detect.DetectError as exc:
        print(f"失败：{exc}")
        return 1

    report(setup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
