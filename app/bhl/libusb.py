"""Windows 上的 libusb：让 pyusb 找到 `libusb-1.0.dll`。

python-can 的 `gs_usb` 后端（candleLight / CANable / canable2 这类 USB-CAN）
要靠 pyusb → libusb 打开设备，而 **Windows 系统本身不带 libusb-1.0.dll**。
找不到这个 DLL 时，pyusb 只会抛 `NoBackendError`，表现就是"没有检测到适配器"。

DLL 按这个顺序找：

1. 打包成 exe 后的解包目录 `_MEIPASS`（PyInstaller `--collect-all libusb_package`）；
2. exe 所在目录（有人喜欢把 DLL 放旁边）；
3. 源码目录 / `windows\\bin\\libusb\\`（手工放的）；
4. pip 包 `libusb-package` 自带的（`pip install libusb-package`）。

找到以后立刻把 pyusb 的 libusb1 后端"预热"好：`libusb1.get_backend()` 会把后端
缓存在模块级变量里，之后 python-can / gs_usb 包再调 `get_backend()` 就能直接拿到。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# os.add_dll_directory 返回的句柄必须一直活着，否则目录会立刻被移出搜索路径
_DLL_DIR_HANDLES: list = []
_READY: bool | None = None


def _search_dirs() -> list[Path]:
    """可能放着 libusb-1.0.dll 的目录，按优先级排列。"""
    dirs: list[Path] = []

    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        base = Path(bundled)
        dirs += [base, base / "libusb_package", base / "libusb"]

    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)

    here = Path(__file__).resolve().parent          # app/bhl
    app_dir = here.parent                           # app
    root = app_dir.parent                           # 仓库根
    dirs += [
        app_dir,
        root,
        root / "windows" / "bin" / "libusb",
        app_dir / "libusb",
    ]

    seen: set[Path] = set()
    unique: list[Path] = []
    for item in dirs:
        try:
            key = item.resolve()
        except OSError:
            key = item
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _dll_candidates() -> list[Path]:
    names = ("libusb-1.0.dll", "libusb-1.0.x64.dll", "libusb-1.0.dll.64")
    found: list[Path] = []
    for folder in _search_dirs():
        for name in names:
            candidate = folder / name
            try:
                if candidate.is_file():
                    found.append(candidate)
            except OSError:
                continue
    return found


def _pip_package_dll() -> Path | None:
    """`libusb-package` 里带的 DLL（Windows 上是 1.0.x 的 amd64 版本）。"""
    try:
        import libusb_package
    except Exception:  # noqa: BLE001 - 没装这个包就往下走
        return None
    try:
        path = libusb_package.find_library("libusb-1.0")
    except Exception:  # noqa: BLE001
        return None
    return Path(path) if path else None


def _remember_dir(dll: Path) -> None:
    """把 DLL 所在目录加进 Windows 的 DLL 搜索路径（Python 3.8+ 必须显式加）。"""
    add = getattr(os, "add_dll_directory", None)
    if add is None:
        return
    try:
        _DLL_DIR_HANDLES.append(add(str(dll.parent)))
    except OSError:
        pass


def _prime_backend(dll: Path) -> bool:
    """让 pyusb 用这个 DLL 建好后端并缓存起来。"""
    try:
        from usb.backend import libusb1
    except Exception:  # noqa: BLE001 - 没装 pyusb
        return False

    _remember_dir(dll)
    try:
        backend = libusb1.get_backend(find_library=lambda _name, target=dll: str(target))
    except Exception:  # noqa: BLE001
        return False
    return backend is not None


def ensure() -> bool:
    """确保 pyusb 的 libusb1 后端可用；返回是否可用。可以重复调用，只算一次。"""
    global _READY

    if _READY is not None:
        return _READY

    if not sys.platform.startswith("win"):
        _READY = True           # Linux/macOS 系统自带 libusb
        return _READY

    try:
        from usb.backend import libusb1

        if libusb1.get_backend() is not None:   # 系统里已经有（比如装了 Zadig 的 libusb）
            _READY = True
            return _READY
    except Exception:  # noqa: BLE001
        _READY = False
        return _READY

    for dll in _dll_candidates():
        if _prime_backend(dll):
            _READY = True
            return _READY

    package_dll = _pip_package_dll()
    if package_dll is not None and _prime_backend(package_dll):
        _READY = True
        return _READY

    _READY = False
    return _READY


def describe() -> str:
    """给日志/自检用的一句话说明。"""
    try:
        from usb.backend import libusb1

        backend = libusb1.get_backend()
    except Exception:  # noqa: BLE001
        return "未安装 pyusb（pip install pyusb）"
    if backend is None:
        return "找不到 libusb-1.0.dll（pip install libusb-package，或把 DLL 放到程序目录）"
    return "libusb 后端可用"
