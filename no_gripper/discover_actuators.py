import _entry_point_init  # noqa: F401,F811 -- macOS shim: must be FIRST import
"""
discover_actuators.py

检测 CAN 总线 / USB-CAN 适配器上存在的电机

用法:
    python discover_actuators.py              # 自动识别左右臂并扫描
    python discover_actuators.py --list       # 列出所有适配器及其序列号
    python discover_actuators.py --full       # 扫遍每个适配器的全部 ID（排查用）
    python discover_actuators.py --side=left  # 只扫左臂
    python discover_actuators.py -c=can0      # 直接指定通道（Linux 用法）
"""

import argparse
import sys

from robstride_dynamics import RobstrideBus

import robstride_compat
from config import generate_bus_config, reset_bus_config_cache

parser = argparse.ArgumentParser()
parser.add_argument("--channel", "-c", type=str, default="", help="单独扫描某个 CAN 通道，例如 can2")
parser.add_argument("--side", "-s", choices=["left", "right"], default="", help="只扫描某一侧手臂")
parser.add_argument("--list", "-l", action="store_true", help="列出所有 USB-CAN 适配器的序列号")
parser.add_argument("--full", "-f", action="store_true", help="每个适配器都扫全部 ID（排查用）")
parser.add_argument("--start-can", type=int, default=1, help="扫描起始 CAN 编号（默认 1）")
parser.add_argument("--end-can", type=int, default=4, help="扫描结束 CAN 编号（默认 4）")
parser.add_argument("--start-id", type=int, default=1, help="电机 ID 起始值")
parser.add_argument("--end-id", type=int, default=50, help="电机 ID 结束值")
args = parser.parse_args()


def scan_channel(channel: str, start_id: int, end_id: int):
	"""扫描一个通道。

	macOS 上必须调用 RobstrideBus.scan_channel —— 它自己打开适配器、扫描、
	再关闭，全程只有一个句柄。如果先 connect() 一个 bus 再调 scan_channel，
	同一个适配器会被打开两次，两个句柄互抢数据，所有电机都会扫不到。
	Linux 上 scan_channel 是实例方法，保持原来的 connect/scan/disconnect 流程。
	"""
	if sys.platform == "darwin":
		return RobstrideBus.scan_channel(channel, start_id=start_id, end_id=end_id)

	bus = RobstrideBus(channel=channel, motors={})
	bus.connect()
	try:
		return bus.scan_channel(channel, start_id=start_id, end_id=end_id)
	finally:
		try:
			bus.disconnect()
		except Exception:
			pass


# --- --list: 只列适配器，不扫描 -------------------------------------------
if args.list:
	if sys.platform != "darwin":
		print("--list 只适用于 macOS（Linux 上适配器就是 can0/can1）。")
		sys.exit(0)
	adapters = robstride_compat.list_adapters()
	if not adapters:
		print("没有检测到任何 USB-CAN 适配器。")
		sys.exit(1)
	for a in adapters:
		print(f"  index {a['index']}: {a['serial']}  (bus {a['bus']}, addr {a['address']})")
	sys.exit(0)

# --- --full: 每个适配器扫全部 ID ------------------------------------------
if args.full:
	if sys.platform != "darwin":
		print("--full 只适用于 macOS（Linux 上适配器就是 can0/can1）。")
		sys.exit(0)
	results = robstride_compat.scan_all_adapters(
		start_id=args.start_id,
		end_id=args.end_id,
	)
	print()
	for serial, ids in results.items():
		print(f"{serial}: {ids or '无响应'}")
	sys.exit(0)

# --- 常规扫描 --------------------------------------------------------------
results = {}
failed = False

if args.channel:
	targets = [(args.channel, args.channel)]
elif args.side:
	# 只识别这一侧；自动识别失败时给出完整报错。
	targets = [(args.side, generate_bus_config(log=True)[args.side])]
else:
	try:
		bus_config = generate_bus_config(log=True)
	except Exception as e:
		print(f"自动识别左右臂失败: {e}")
		sys.exit(1)
	targets = [("left", bus_config["left"]), ("right", bus_config["right"])]

# 自动识别时已经扫过这些口了，结果就在缓存里。再扫一遍会让每个适配器
# 多开关一次（macOS 上每次开关都会重新枚举 USB），既慢又容易失败。
cached = {}
if sys.platform == "darwin" and not args.channel:
	cached = robstride_compat.scan_cache()

for side, channel in targets:
	if channel in cached:
		ids = cached[channel]
		source = "（自动识别时已扫描）"
	else:
		try:
			ids = scan_channel(channel, args.start_id, args.end_id)
		except Exception as e:
			print(f"[{side}] 通道 {channel} 扫描失败: {e}")
			failed = True
			ids = {}
		source = ""
	results[side] = ids
	print(f"[{side}] 通道 {channel}{source}: {ids}")

print(results)

if failed:
	sys.exit(1)

print("Program terminated.")
