# Windows 版使用说明　`v2.0.5-win`

界面、功能、快捷键与 Linux 版**完全一致**，差异只在 CAN 驱动层。

## 怎么用

### 方式 A：直接用 exe（不需要 Python）

到 [Releases](https://github.com/TeamLite-DeepCybo/lite-record_replay/releases) 下载
`LiteRecordReplay-*-x64.zip`（arm64 机器下 `-arm64`），解压后双击 **`LiteRecordReplay.exe`**。

解压出来应该是这样（**这几个要放在一起**）：

```
LiteRecordReplay.exe     单文件程序
calibration.json         标定文件（exe 找不到时会用内置副本自动生成）
data\motions\           录制文件存在这里
README.md                本文件
```

exe 由 GitHub Actions 在 Windows runner 上构建（x64 + arm64），构建后会做启动冒烟测试。

### 方式 B：从源码跑（需要 Python 3.10+）

| 步骤 | 双击 | 说明 |
|---|---|---|
| 1 | `1-安装.bat` | 装运行环境（`.venv`）+ 建桌面快捷方式。首次约 1~3 分钟 |
| 2 | `2-启动.bat` | 打开界面。没装过会自动先装 |

装好之后，平时直接双击桌面的 **Lite Record Replay** 图标即可。

- 想看日志/报错：`bin\run.bat`（带控制台）
- 想打包成单文件 exe：`bin\build_exe.bat`

## 目录

```
windows/
├── 1-安装.bat            安装入口（调用 bin\install.bat）
├── 2-启动.bat            启动入口（调用 bin\start_gui.bat）
├── README.md             本文件
├── RELEASE.md            发布说明
└── bin/
    ├── find_python.ps1       找 Python 3.10+（找不到可用 winget 自动装）
    ├── install_windows.ps1   真正的安装逻辑（venv / 依赖 / 自检 / 快捷方式）
    ├── install.bat           install_windows.ps1 的批处理壳
    ├── start_gui.bat         启动（pythonw，无控制台）
    ├── run.bat               启动（python，带控制台，排查用）
    ├── build_exe.bat         PyInstaller 单文件打包
    ├── requirements-win.txt  Windows 依赖清单
    └── selfcheck.py          安装后自检（依赖 / Tk / CAN 适配器 / 程序本体）
```

运行环境装在仓库根的 `.venv\`（独立虚拟环境，不污染系统 Python）。**卸载 = 删掉整个文件夹。**

## 系统要求

- Windows 10 / 11，**64 位**（x64 或 arm64）
- Python **3.10+**（`robstride_dynamics` 自身要求 3.10+）。
  没装 Python 时，安装脚本会尝试用 `winget` 自动装 3.12；没有 winget 会打开官网下载页。

> 32 位（x86）Python 不支持：新版 numpy 已不再提供 32 位 wheel，安装脚本检测到会直接提示。

## CAN 适配器

Windows 没有 socketcan，改用 python-can 的厂商驱动。程序启动时会自动枚举已插好的适配器，
并自动认出哪条是左臂、哪条是右臂（逻辑与 Linux 相同：ping 电机 ID，看谁响应）。

| 适配器 | 驱动名 | 备注 |
|---|---|---|
| PEAK PCAN-USB | `pcan` | 装 PCAN-Basic 驱动 |
| 周立功 CANalyst-II | `canalystii` | 装厂家驱动 |
| CANable / slcan 串口 | `slcan` | 需要指定 COM 口 |
| gs_usb / candlelight | `gs_usb` | 免驱（WinUSB） |
| Kvaser / Vector / SYSTEC / IXXAT | 同名 | 装厂家驱动 |

手动指定（多数情况不需要）：

```bat
2-启动.bat --interface pcan
2-启动.bat --interface slcan --channels COM3,COM4
```

看一眼当前认出了什么：

```bat
.venv\Scripts\python.exe app\discover.py --interface pcan
```

## 常见问题

**双击 2-启动.bat 后窗口闪一下就没了**

改用 `bin\run.bat`，它会保留控制台并显示完整报错。

**提示 `Python 3.10 or newer is required`**

到 https://www.python.org/downloads/windows/ 装 64 位 Python 3.12，
安装时**务必勾选 Add Python to PATH**，然后再跑一次 `1-安装.bat`。

**提示 `A 32-bit (x86) Python was found`**

装的是 32 位 Python。改装 64 位（x64 / arm64 都行）。

**杀毒软件报毒**

`.venv\Scripts\python.exe` 是标准 Python 虚拟环境里的解释器，部分杀软会误报，
加白名单即可。

**扫描不到电机**

1. 确认 USB-CAN 适配器在设备管理器里没有黄色感叹号（驱动装好）；
2. 跑 `.venv\Scripts\python.exe app\discover.py`，看有没有列出适配器；
3. 总线波特率必须是 **1 Mbps**（RobStride 电机默认），部分适配器软件里要单独设；
4. 确认机器人已经上电。

**想要一个真正的 .exe（单文件、双击即用）**

在 Windows 上跑 `bin\build_exe.bat`，产物在 `dist\LiteRecordReplay.exe`。
注意 exe 与架构相关，x64 / arm64 需要各自打一次。
