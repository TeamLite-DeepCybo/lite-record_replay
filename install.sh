#!/bin/bash
# 一键安装：把运行这个程序需要的依赖装好（新机器跑一次就够）
#   ./install.sh
set -e

echo "== 系统依赖 =="
sudo apt --fix-broken install -y
sudo apt update
sudo apt install -y python3-pip python3-tk iproute2

echo
echo "== Python 依赖（清华源加速）=="
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple || true
pip install --break-system-packages -r "$(dirname "$(readlink -f "$0")")/app/requirements.txt" \
  || pip install -r "$(dirname "$(readlink -f "$0")")/app/requirements.txt"

echo
echo "== 检查 =="
python3 - <<'PY'
import numpy, robstride_dynamics          # noqa: F401
print("numpy", numpy.__version__)
print("robstride_dynamics OK")
try:
    import tkinter
    print("tkinter OK")
except Exception as exc:
    print("tkinter 不可用：", exc)
PY

echo
echo "安装完成。接下来运行：  ./start.sh"
