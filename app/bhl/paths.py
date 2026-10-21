"""程序目录与资源目录——把同一个程序跑成源码或打包成 exe 时都正确。

- 直接跑源码：程序目录 = `app/`，资源目录也是 `app/`
- 打包成单文件 exe（PyInstaller）：`__file__` 会指向临时解包目录 `_MEIPASS`，
  而用户写出来的数据（录制文件、标定）必须落在 **exe 旁边**，不能落在临时目录里，
  否则程序一退出数据就没了。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是不是被打包成了 exe。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """可写目录：exe 所在目录，或者源码的 app/ 目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """只读资源目录（图标 / 预览图）：打包后在解包目录，源码运行时就是 app/。"""
    if is_frozen():
        bundled = getattr(sys, "_MEIPASS", None)
        if bundled:
            return Path(bundled)
    return Path(__file__).resolve().parent.parent
