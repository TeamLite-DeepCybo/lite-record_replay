#!/bin/bash
# macOS 一键安装脚本
# 用法: bash install_mac.sh
#
# 不需要预先安装 Homebrew、Python 或 libusb：
#   - Python 3.12 由 uv 自动下载（自带 tkinter，可离线复用）
#   - libusb 由 libusb-package 这个 pip 包自带（含签名 dylib）
# 只需要一个能联网的 Mac 和系统自带的 curl。

set -u

cd "$(dirname "$0")" || exit 1

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { printf "${GREEN}  ✅ %s${NC}\n" "$*"; }
warn() { printf "${YELLOW}  ⚠️  %s${NC}\n" "$*"; }
fail() { printf "${RED}  ❌ %s${NC}\n" "$*"; }
step() { printf "\n${BOLD}[%s] %s${NC}\n" "$1" "$2"; }

PYTHON_VERSION="3.12"
VENV=".venv"

printf "${BOLD}=== Prime Lite 动作录制回放 — macOS 一键安装 ===${NC}\n"

# ── Step 1: 找到（或安装）uv ────────────────────────────
step "1/5" "准备 Python 运行环境"
UV=""
for candidate in "$HOME/.local/bin/uv" "/opt/homebrew/bin/uv" "/usr/local/bin/uv" "$(command -v uv 2>/dev/null || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        UV="$candidate"
        break
    fi
done

if [ -z "$UV" ]; then
    echo "  未找到 uv，正在自动下载（约 15 MB，只需一次）..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
    fi
    if [ -x "$HOME/.local/bin/uv" ]; then
        UV="$HOME/.local/bin/uv"
    fi
fi

if [ -z "$UV" ]; then
    # 最后再试一次 Homebrew（如果机器上有）
    if command -v brew >/dev/null 2>&1; then
        warn "自动下载 uv 失败，尝试用 Homebrew 安装..."
        brew install uv >/dev/null 2>&1 || true
        command -v uv >/dev/null 2>&1 && UV="$(command -v uv)"
    fi
fi

if [ -z "$UV" ]; then
    fail "无法安装 uv（需要联网）。"
    echo ""
    echo "  请手动执行下面任一方式后，重新运行本脚本："
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "    或：brew install uv"
    echo ""
    echo "  如果这台 Mac 完全不能联网，请在一台能联网的 Mac 上执行"
    echo "  'uv python install $PYTHON_VERSION'，然后把"
    echo "  ~/.local/share/uv/python/ 整个目录拷贝到这台机器的相同位置。"
    exit 1
fi
ok "uv: $UV"

# ── Step 2: Python 3.12 ────────────────────────────────
# 系统自带的 python3 是 3.9，robstride-dynamics 要求 >=3.10，不能用。
# 必须用 uv 自己下载的 3.12：它带 tkinter，而 conda/系统的解释器不能保证。
# uv python find 会挑到 PATH 上任意 3.12（比如 anaconda），所以这里直接
# 用 uv python dir 拼出托管解释器的路径。
if ! "$UV" python install "$PYTHON_VERSION" >/dev/null 2>&1; then
    # 已经装过时会返回非 0，再确认一次是否真的没有
    if ! "$UV" python find "$PYTHON_VERSION" >/dev/null 2>&1; then
        fail "下载 Python $PYTHON_VERSION 失败（需要联网）。"
        exit 1
    fi
fi

PY_BIN=""
for candidate in "$HOME/.local/share/uv/python/cpython-${PYTHON_VERSION}-macos-"*"/bin/python${PYTHON_VERSION}"; do
    if [ -x "$candidate" ]; then
        PY_BIN="$candidate"
        break
    fi
done
# 退路：任何 uv 能看到的 3.12
if [ -z "$PY_BIN" ]; then
    PY_BIN="$("$UV" python find "$PYTHON_VERSION" 2>/dev/null)"
fi

if [ -z "$PY_BIN" ] || [ ! -x "$PY_BIN" ]; then
    fail "找不到可用的 Python $PYTHON_VERSION。"
    exit 1
fi

# tkinter 是这个项目硬依赖（GUI），缺了就别往下走了。
if ! "$PY_BIN" -c "import tkinter" >/dev/null 2>&1; then
    fail "Python $PY_BIN 里没有 tkinter，无法运行 GUI。"
    echo "  请运行下面命令后重试："
    echo "    \"$UV\" python install --reinstall $PYTHON_VERSION"
    exit 1
fi
ok "Python $("$PY_BIN" -V 2>&1)  ($PY_BIN)"

# ── Step 3: venv ───────────────────────────────────────
# 旧的 .venv 可能软链到别的机器/conda 的 Python（打包发给别人时就是坏的），
# 所以要验证它真的能跑，不能只看目录存不存在。
if [ -d "$VENV" ] && ! "$VENV/bin/python" -V >/dev/null 2>&1; then
    warn "检测到无法使用的 $VENV（可能来自别的机器），正在重建..."
    rm -rf "$VENV"
fi

if [ ! -d "$VENV" ]; then
    echo "  创建虚拟环境 $VENV ..."
    if ! "$UV" venv --python "$PY_BIN" --seed "$VENV" >/dev/null 2>&1; then
        rm -rf "$VENV"
        "$PY_BIN" -m venv "$VENV" || {
            fail "虚拟环境创建失败。"
            exit 1
        }
    fi
fi

VPY="$VENV/bin/python"
if ! "$VPY" -V >/dev/null 2>&1; then
    fail "虚拟环境创建失败：$VENV/bin/python 无法运行。"
    exit 1
fi
if ! "$VPY" -c "import tkinter" >/dev/null 2>&1; then
    fail "$VENV 里没有 tkinter（虚拟环境指向了错误的 Python）。"
    echo "  请删除 .venv 后重新运行：rm -rf .venv && bash install_mac.sh"
    exit 1
fi
ok "虚拟环境: $("$VPY" -V 2>&1)"

# ── Step 4: 依赖 ───────────────────────────────────────
step "2/5" "安装 Python 依赖（第一次需要几分钟）"
# "python-can[gs-usb]" 必须加引号：zsh 会把方括号当成通配符，
# 直接写 python-can[gs-usb] 会报 "no matches found"。
#
# libusb-package: 自带签名过的 libusb dylib，免掉 brew install libusb。
# _entry_point_init.py 会把 pyusb 指到这个 dylib。
# 用 uv pip 而不是 python -m pip：uv 建的 venv 默认不带 pip。
if ! "$UV" pip install --python "$VPY" -q \
    numpy \
    loop_rate_limiters \
    "python-can[gs-usb]" \
    pyusb \
    libusb-package \
    robstride-dynamics \
    tqdm; then
    fail "依赖安装失败。请检查网络后重新运行：bash install_mac.sh"
    exit 1
fi
ok "依赖安装完成"

# ── Step 5: 自检 ───────────────────────────────────────
step "3/5" "自检"
SELF_TEST_PASSED=1
"$VPY" - <<'PY' || SELF_TEST_PASSED=0
import sys

failures = []

def check(label, fn):
    try:
        detail = fn()
        print(f"  ✅ {label}{('  ' + detail) if detail else ''}")
    except Exception as exc:
        print(f"  ❌ {label}: {exc}")
        failures.append(label)

import tkinter
check("tkinter", lambda: f"Tk {tkinter.TkVersion}")

import numpy
check("numpy", lambda: numpy.__version__)

import can
check("python-can", lambda: can.__version__)

def _libusb():
    import libusb_package
    from usb.backend import libusb1
    backend = libusb1.get_backend(find_library=libusb_package.find_library)
    if backend is None:
        raise RuntimeError("pyusb 找不到 libusb")
    return "bundled dylib"

check("libusb (pyusb)", _libusb)

def _sdk():
    from robstride_dynamics import Motor, RobstrideBus
    return "RobstrideBus OK"

check("robstride-dynamics", _sdk)

sys.exit(1 if failures else 0)
PY

if [ "$SELF_TEST_PASSED" -eq 0 ]; then
    fail "自检未通过，请把上面的输出发给技术支持。"
    exit 1
fi

# 真正的适配器检测（走项目自己的入口，能验证整条链路）
step "4/5" "检测 USB-CAN 适配器"
ADAPTER_OUT="$(cd have_gripper && "../$VENV/bin/python" discover_actuators.py --list 2>/dev/null)"
if [ -n "$ADAPTER_OUT" ]; then
    ok "已连接适配器："
    echo "$ADAPTER_OUT" | sed 's/^/     /'
else
    warn "没有检测到 USB-CAN 适配器。"
    echo "     插上适配器后运行：cd have_gripper && ../.venv/bin/python discover_actuators.py --list"
    echo "     这不影响安装，只是现在还不能控制机械臂。"
fi

step "5/5" "完成"
printf "${GREEN}${BOLD}=== 安装完成 ✅ ===${NC}\n\n"
echo "  启动方式（任选其一）："
echo ""
echo "    bash start_mac.sh              # 有假爪（默认）"
echo "    bash start_mac.sh no_gripper   # 无假爪"
echo ""
echo "  也可以直接双击 start_mac.command。"
echo ""
