#!/bin/bash
# 上电并配置 CAN 口（1 Mbps）。
#
#   bash canup.sh              # 需要时会自己用 sudo 提权（GUI 启动时就是这么调的）
#   sudo bash canup.sh         # 手动提权也行
#   bash canup.sh can0 can1    # 只配置指定的口
#
# 每次机器人上电、或者重新插拔 USB-CAN 之后都要跑一次。
set -u

# 不是 root 就带着 sudo 重新执行自己，这样调用方永远可以简单地写 `bash canup.sh`
if [ "$(id -u)" -ne 0 ]; then
    echo "CAN 口配置需要管理员权限"
    exec sudo -p "[sudo] 请输入密码: " bash "$0" "$@"
fi

CHANNELS=("$@")
if [ ${#CHANNELS[@]} -eq 0 ]; then
    CHANNELS=(can0 can1 can2 can3 can4)
fi

failed=0
found=0

for ch in "${CHANNELS[@]}"; do
    if ! ip link show "$ch" >/dev/null 2>&1; then
        echo "  $ch      不存在（跳过）"
        continue
    fi
    found=$((found + 1))

    ip link set "$ch" down 2>/dev/null
    if ip link set "$ch" up type can bitrate 1000000 2>/dev/null; then
        echo "  $ch      OK (1 Mbps)"
    else
        echo "  $ch      配置失败"
        failed=$((failed + 1))
    fi
done

if [ "$found" -eq 0 ]; then
    echo
    echo "没有找到任何 CAN 口（can0~can4 都不存在）。"
    echo "请检查 USB-CAN 适配器是否插好，必要时重新插拔后再试。"
    exit 1
fi

if [ "$failed" -ne 0 ]; then
    echo
    echo "有 CAN 口配置失败。请检查 USB-CAN 是否插好、是否被别的程序占用。"
    exit 1
fi

exit 0
