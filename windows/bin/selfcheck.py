#!/usr/bin/env python3
"""Windows 安装后的自检：依赖、Tk、CAN 适配器、程序本体。

由 windows/bin/install_windows.ps1 在装完依赖后调用，也可以手动跑：

    runtime 虚拟环境里的 python windows\\bin\\selfcheck.py
"""

from __future__ import annotations

import platform
import struct
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent.parent / "app"
sys.path.insert(0, str(APP))          # 让 import bhl 生效

failures: list[str] = []


def ok(text: str) -> None:
    print(f"  [ok]   {text}")


def bad(text: str) -> None:
    failures.append(text)
    print(f"  [FAIL] {text}")


def info(text: str) -> None:
    print(f"         {text}")


def main() -> int:
    print("== 环境 ==")
    info(f"Python {platform.python_version()}  ({platform.machine()}, {struct.calcsize('P') * 8} bit)")
    info(f"可执行文件 {sys.executable}")
    if struct.calcsize("P") * 8 != 64:
        bad("需要 64 位 Python（32 位 numpy 已无官方 wheel）")
    if sys.version_info < (3, 10):
        bad(f"需要 Python 3.10+，当前 {platform.python_version()}")

    print("== 依赖 ==")
    for module, label in (
        ("numpy", "numpy"),
        ("can", "python-can"),
        ("robstride_dynamics", "robstride-dynamics"),
        ("tkinter", "tkinter"),
    ):
        try:
            imported = __import__(module)
            version = getattr(imported, "__version__", "")
            ok(f"{label} {version}".strip())
        except Exception as exc:                       # noqa: BLE001
            bad(f"{label} 导入失败：{exc}")

    print("== Tk 界面 ==")
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        info(f"Tk {root.tk.call('info', 'patchlevel')}")
        root.destroy()
        ok("能创建窗口（图形环境正常）")
    except Exception as exc:                           # noqa: BLE001
        bad(f"Tk 初始化失败：{exc}")

    print("== 程序本体 ==")
    try:
        from bhl import config, detect  # noqa: PLC0415

        ok(f"bhl 模块可用，电机表 {len(config.MOTORS)} 个")
        drivers = [
            ("pcan", "PEAK PCAN-USB"),
            ("canalystii", "周立功 CANalyst-II"),
            ("slcan", "CANable / slcan 串口"),
            ("gs_usb", "gs_usb / candlelight"),
        ]
        available = []
        if not sys.platform.startswith("win"):
            targets = detect.list_targets()
            if targets:
                ok("检测到 CAN 总线：" + "，".join(label for label, _, _ in targets[:6]))
                return _finish()

        for driver, label in drivers:
            try:
                import can

                found = can.detect_available_configs(interfaces=[driver])
            except Exception:                          # noqa: BLE001
                found = []
            if found:
                available.append(f"{label}（{driver}）")
        if available:
            ok("检测到 CAN 适配器：" + "，".join(available))
        else:
            info("暂未检测到 CAN 适配器 —— 装好 USB-CAN 驱动后插上即可；")
            info("  python app\\discover.py --interface pcan 可查看具体驱动")
    except Exception as exc:                           # noqa: BLE001
        bad(f"bhl 导入失败：{exc}")

    return _finish()


def _finish() -> int:
    print()
    if failures:
        print(f"自检未通过：{len(failures)} 项")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("自检通过 / self check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
