#!/bin/bash
# 双击这个文件即可安装（Finder 里）。

cd "$(dirname "$0")" || exit 1
bash install_mac.sh
STATUS=$?
echo ""
if [ $STATUS -eq 0 ]; then
    echo "安装完成。现在可以双击 start_mac.command 启动程序。"
else
    echo "安装未完成（状态码 $STATUS）。请把上面的输出发给技术支持。"
fi
read -r -p "按回车键关闭..." _
