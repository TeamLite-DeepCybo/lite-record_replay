# Lite 动作录制 / 回放

Prime Lite 半身双臂机器人的**拖动示教录制 / 动作回放**工具。

本仓库按发行版分成三个分支，`main` 只作为版本索引：

| 版本 | 分支 | 说明 |
|---|---|---|
| **Ubuntu / Linux 版** | [`ubuntu`](https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/ubuntu) | v2.0.5，SocketCAN（can0 / can1），含 `install.sh` / `start.sh` |
| **Windows 版** | [`windows`](https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/windows) | v2.0.5-win，candleLight / USB-CAN，x64 / arm64，免解压目录版（exe + `_internal`） |
| **macOS 版** | [`macos`](https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/macos) | 双击 `install_mac.command` 安装（自动下载 Python 3.12，不需要 Homebrew），双击 `start_mac.command` 启动 |

## 下载

- **发行包（exe / zip）**：[Releases](https://github.com/TeamLite-DeepCybo/lite-record_replay/releases)
- **历史版本（tag）**：[Tags](https://github.com/TeamLite-DeepCybo/lite-record_replay/tags)

Windows 用户直接下载 `LiteRecordReplay-2.0.5-win-x64.zip`（arm64 机器选 `-arm64`），解压后双击 `LiteRecordReplay.exe`，不需要装 Python。

macOS 用户不需要下载 Release：取 [`macos`](https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/macos) 分支（或 GitHub 上的 *Download ZIP*），解压后双击 `install_mac.command` 安装一次，之后双击 `start_mac.command` 启动。

## 版本历史

| tag | 说明 |
|---|---|
| [`v1.0.0`](https://github.com/TeamLite-DeepCybo/lite-record_replay/releases/tag/v1.0.0) | 原始录制 / 回放版本 |
| [`v1.1.0`](https://github.com/TeamLite-DeepCybo/lite-record_replay/releases/tag/v1.1.0) | 加入 CAN 通道自动识别与在线电机降级 |
| [`v1.2.0`](https://github.com/TeamLite-DeepCybo/lite-record_replay/releases/tag/v1.2.0) | 无夹爪版本支持重复回放 |
| [`v2.0.0`](https://github.com/TeamLite-DeepCybo/lite-record_replay/releases/tag/v2.0.0) | 重构为单份代码 + 模块化 |
| [`v2.0.5`](https://github.com/TeamLite-DeepCybo/lite-record_replay/releases/tag/v2.0.5) | Ubuntu 版当前版本 |
| [`v2.0.5-win`](https://github.com/TeamLite-DeepCybo/lite-record_replay/releases/tag/v2.0.5-win) | Windows 版当前版本 |

## 怎么选

- 机器人主控是 **Ubuntu**、走 SocketCAN：用 [`ubuntu`](https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/ubuntu) 分支。
- 用 **Windows 电脑**直连 USB-CAN（candleLight / CANable）控制机械臂：用 [`windows`](https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/windows) 分支，或直接下 Release 里的 exe。
- 用 **Mac** 控制机械臂：用 [`macos`](https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/macos) 分支，第一次双击 `install_mac.command`，之后双击 `start_mac.command`。

## 许可

Apache License 2.0，全文见 [LICENSE](https://github.com/TeamLite-DeepCybo/lite-record_replay/blob/main/LICENSE)。
