#!/bin/bash
# 一键启动：录制 / 回放界面
#   ./start.sh            正常启动
#   ./start.sh --version  查看版本
#   ./start.sh --channels can2,can1   手动指定左右臂 CAN 口
set -u
cd "$(dirname "$(readlink -f "$0")")/app" || {
    echo "找不到 app 目录，请确认脚本和 app 在同一个文件夹里"; exit 1; }
exec python3 main.py "$@"
