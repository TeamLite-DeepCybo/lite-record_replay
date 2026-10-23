#!/bin/bash
# macOS 一键启动脚本
# 用法:
#   bash start_mac.sh              # 有假爪（默认）
#   bash start_mac.sh no_gripper   # 无假爪
#   bash start_mac.sh have_gripper # 有假爪

set -u

cd "$(dirname "$0")" || exit 1

VARIANT="${1:-have_gripper}"
case "$VARIANT" in
    have|gripper|true)   VARIANT="have_gripper" ;;
    no|nogripper|false)  VARIANT="no_gripper" ;;
    have_gripper|no_gripper) ;;
    *) VARIANT="have_gripper" ;;
esac

VENV=".venv"
VPY="$VENV/bin/python"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

if [ ! -x "$VPY" ] || ! "$VPY" -V >/dev/null 2>&1; then
    printf "${RED}还没有安装（或 .venv 不可用）。${NC}\n"
    echo "请先运行：bash install_mac.sh"
    echo ""
    read -r -p "按回车键关闭..." _
    exit 1
fi

# 端口释放：上一个进程异常退出时可能还占着适配器。
# （macOS 上残留的 python 进程会让下一次打开报 Access denied。）

printf "${GREEN}启动 Prime Lite 动作录制回放（%s）...${NC}\n" "$VARIANT"
echo "关闭这个窗口或按 Ctrl+C 即可退出。"
echo ""

cd "$VARIANT" || exit 1
"../$VPY" gui.py
STATUS=$?

# GUI 正常关闭时保持窗口，方便看最后的输出/报错。
if [ $STATUS -ne 0 ]; then
    echo ""
    printf "${YELLOW}GUI 退出，状态码 %s。${NC}\n" "$STATUS"
    read -r -p "按回车键关闭..." _
fi
