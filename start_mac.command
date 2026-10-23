#!/bin/bash
# 双击这个文件即可启动（Finder 里）。
# 它只是 start_mac.sh 的包装：双击时终端打开的目录就是本文件所在目录。

cd "$(dirname "$0")" || exit 1
bash start_mac.sh "${1:-have_gripper}"
