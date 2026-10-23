# Windows 版使用说明　`v2.0.5-win`

界面、功能、快捷键与 Ubuntu 版一致（同一套 `app/` 代码），差异只在 CAN 驱动层。

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
并自动认出哪条是左臂、哪条是右臂（ping 电机 ID，看谁响应）。

**认出一次之后就会记住**：左右臂的适配器序列号会写进程序目录的 `.robot-buses.json`，
之后启动、点「扫描电机」都只打开这两只，其余口不枚举、不打开、不嗅探。

> 为什么要这样：接在**没上电的线**上的适配器会持续刷错误帧，`candle` 的原生 `open()` 在这种口上有概率卡死，
> 而且卡住后 Windows 杀不掉进程、USB 句柄也不释放。只碰健康的两只，就从源头避开了这个问题。
> 换过适配器 / 换过 USB 口时，删掉 `.robot-buses.json`，或在扫描时让它自动回退全量即可。

如果检测到「USB 上插着的适配器比程序能认到的多」，说明有设备被别的进程（常见是上一次卡死的旧进程）占着，
程序会**弹框提示拔插**并停止扫描，不会硬扫。

| 适配器 | 驱动名 | 备注 |
|---|---|---|
| **candleLight / CANable / canable2** | `candle` | 开源固件 USB-CAN，**首选**（`python-can-candle` 插件） |
| 同上的兜底 | `gs_usb` | 靠 pyusb + libusb，Windows 上发帧会超时，只在没有 candle 时用 |
| PEAK PCAN-USB | `pcan` | 装 PCAN-Basic 驱动 |
| 周立功 CANalyst-II | `canalystii` | 装厂家驱动 |
| CANable / slcan 串口 | `slcan` | 需要指定 COM 口（要 pyserial） |
| Kvaser / Vector / SYSTEC / IXXAT | 同名 | 装厂家驱动 |

### candleLight 这类棒子：为什么必须用 `candle`

同一根棒子有两条软件路可走，差别很大：

| 后端 | 枚举 | 发帧 | 结论 |
|---|---|---|---|
| `candle`（`python-can-candle`） | 按**序列号**列出设备 | 正常，实测 16/16 电机全在 | ✅ 默认走这条 |
| `gs_usb`（pyusb + libusb） | python-can 根本没实现枚举 | Windows 上每帧都 1 秒 USB 超时 | ⚠ 只能兜底 |

`candle` 的通道长这样：`"0038003E5547570820383634:0"`（**序列号:通道号**），
所以换 USB 口、换枚举顺序都不会认错设备。程序会自动把这些整理成好认的标签：

```
candleLight#0 (00380020)   驱动 candle   channel='003800205547570820383634:0'
candleLight#1 (0038003E)   驱动 candle   channel='0038003E5547570820383634:0'
```

需要哪几条就用 `--channels` 点名（推荐用**序列号**，标签里的 `candleLight#N` 会随枚举顺序变）：

```bat
LiteRecordReplay.exe --channels "0024001E4248571220363631:0","0024001C4248571220363631:0"
```

### 先跑一次体检

```bat
.venv\Scripts\python.exe app\main.py --diagnose
```

它会列出：依赖版本、`candle` / `libusb` 是否可用、枚举到的每个适配器（标签 / 驱动 / 通道号）、
扫描电机时每条总线是"发现 N 个""可以打开但没有应答"还是"用不了"，
并对没有应答的总线**静默监听 1.5 秒**。exe 版对应：

```bat
LiteRecordReplay.exe --diagnose          :: 结果同时写到 exe 旁边的 can-diagnose.txt
```

这份报告能直接把问题定位到某一层：

| 报告里看到 | 说明 |
|---|---|
| `没有检测到 CAN 适配器` | 驱动 / 依赖这一层（缺 `python-can-candle` 最常见） |
| `打开超时` / `Cannot open device` / `Access denied` | 适配器被**别的程序或僵尸进程**占着（USB 句柄是独占的） |
| `用不了：The message could not be sent` | 这一帧发出去没人 ACK（机器人没上电 / 线没接 / 没终端电阻）；`candle` 和 `gs_usb` 都会这样 |
| `可以打开，但没有电机应答` + 监听全静音 | 机械臂没上电 / CAN 线没接 / 没终端电阻 |
| 有帧、但扫描不到电机 | 电机 ID 与 `config.py` 不一致，或不是 Robstride 电机 |

手动指定（多数情况不需要）：

```bat
2-启动.bat --interface pcan
2-启动.bat --interface slcan --channels COM3,COM4
2-启动.bat --channels "candleLight#0","candleLight#3"
```

看一眼当前认出了什么（会顺带试开每一条总线）：

```bat
.venv\Scripts\python.exe app\discover.py
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

**日志里说"没有检测到可用的 CAN 适配器"**

跑 `--diagnose`，看 `== 依赖 ==` 里 `usb` / `gs_usb` / `libusb_package` 三项和
`libusb:` 那一行：

- 少了 `usb` 或 `gs_usb` → 重跑 `1-安装.bat`（依赖清单已经补上这两个包）；
- `libusb: 找不到 libusb-1.0.dll` → `pip install libusb-package`，或把 DLL 放到程序目录；
- 三项都在但还是没有 → 设备没绑 WinUSB。用 [Zadig](https://zadig.akeo.ie/) 把
  `candleLight USB to CAN adapter` 的驱动换成 WinUSB。

**能识别适配器，但扫描不到电机**

1. 先看 `--diagnose` 里 `== 试开每条总线 ==` 是不是每条"可以打开"；
2. 再看 `== 监听每条总线 1.5 秒 ==`：
   - **全部"一点动静都没有"** → 机械臂没上电，或者 CAN 线 / 供电没接上（软件已经没问题了）；
   - **有的总线有帧，有的没有** → 线接在"有帧"的那条上，用 `--channels` 只扫它；
3. 确认机器人已上电、CAN 线（含 120Ω 终端电阻）接好；
4. 总线波特率必须是 **1 Mbps**（RobStride 电机默认）；
5. 日志里出现 `CanOperationError: The message could not be sent` 通常表示
   **这一帧发出去没人应答**（机器人没上电 / 线没接 / 没终端电阻）；偶发一次不会中止回放，
   连续 6 帧才会判定掉线。

**弹框「CAN 适配器被占用」**

USB 上插着的适配器比程序能认到的多，说明有设备被别的进程（最常见是上一次卡死的旧进程）占着。
把 USB-CAN 拔下来再插回去，然后点「重新连接」。**不要**在提示状态下继续扫描——那会卡住并再留下一个占用的进程。

**扫描时卡住不动**

程序会自己超时中止（不会把界面冻死），日志里会写 `探测超过 N 秒没有返回`。
按上面的「拔插一次」处理；反复出现的话，把程序目录下的 `worker-watch-<pid>.txt` 和 `worker-boot.txt` 发给开发。

**拔插 USB-CAN 后日志提示「当前连接已失效」**

这是保护：拔插会让旧句柄失效，继续发帧会让驱动直接崩掉进程。点「重新连接」即可恢复；
失效状态下录制/回放按钮会被拦住，不会带着坏句柄动作。

**想要一个真正的 .exe（单文件、双击即用）**

在 Windows 上跑 `bin\build_exe.bat`，产物在 `dist\LiteRecordReplay.exe`。
注意 exe 与架构相关，x64 / arm64 需要各自打一次。
