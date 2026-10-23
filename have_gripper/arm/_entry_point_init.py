"""
_entry_point_init — macOS shim installer for robstride_dynamics.

When running on macOS, patches RobstrideBus to use python-can/gs_usb
instead of Linux SocketCAN (which doesn't exist on macOS).

On Linux this is a no-op — robstride_dynamics works natively.

Usage (put FIRST in any Python file that:
  - imports robstride_dynamics directly, OR
  - spawns child processes that import it):
    import _entry_point_init  # noqa: F401,F811
"""

import os as _os
import sys as _sys

# This file exists both in <package>/ and <package>/arm/. Either way we want
# the directory holding robstride_compat.py (this one, or ./arm next to it)
# importable before anything else runs.
_here = _os.path.dirname(_os.path.abspath(__file__))
_arm = _os.path.dirname(_here) if _os.path.basename(_here) == "arm" else _os.path.join(_here, "arm")
for _d in (_here, _arm):
    if _os.path.isdir(_d) and _d not in _sys.path:
        _sys.path.insert(0, _d)

import platform as _pl

if _pl.system() == "Darwin":
    # pyusb looks for libusb with ctypes.util.find_library(), which on a stock
    # Mac (no Homebrew, no DYLD_LIBRARY_PATH) finds nothing -> the gs_usb
    # backend comes back as None and no adapter is ever detected.
    # The libusb-package wheel ships a signed dylib inside site-packages, so
    # point pyusb at that instead. Installed by install_mac.sh; if it is
    # missing we fall through to whatever the system provides.
    try:
        import libusb_package
        import usb.backend.libusb1 as _usb_libusb1

        _orig_get_backend = _usb_libusb1.get_backend
        _usb_libusb1.get_backend = (
            lambda find_library=None: _orig_get_backend(
                find_library=libusb_package.find_library
            )
        )
    except ImportError:
        pass
    except Exception as _e:  # pragma: no cover - defensive
        print(f"WARNING: could not hook libusb_package ({_e})")

    import robstride_compat  # noqa: F401 — patches RobstrideBus before use

    robstride_compat.install()
