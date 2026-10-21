# v2.0.5-win 发布说明

Windows 版首次发布，功能与 Linux 版 `v2.0.5` 一致。

## 下载与安装

Release 上有三个包，按需选一个：

| 包 | 适用 | 用法 |
|---|---|---|
| `LiteRecordReplay-2.0.5-win-x64.zip` | 绝大多数 Windows（x64） | 解压 → 双击 `LiteRecordReplay.exe`，**不需要装 Python** |
| `LiteRecordReplay-2.0.5-win-arm64.zip` | Windows on ARM（骁龙本等） | 同上 |
| `lite-record_replay-v2.0.5-win.zip` | 想跑源码 / 要改代码 | 解压 → 双击 `windows\1-安装.bat` |

exe 包解压后保持这些文件在同一目录：`LiteRecordReplay.exe`、`calibration.json`、`data\motions\`、`README.md`。
`data\motions\` 是录制文件存放处，可以先删掉让它自动创建。

源码包的用法：双击 `windows\1-安装.bat` → 双击 `windows\2-启动.bat`（或桌面快捷方式）。

## exe 是怎么构建的

`.github/workflows/build-exe.yml`：打 `v*` tag 或手动触发时，在 GitHub 的
`windows-latest`（x64）与 `windows-11-arm`（arm64）runner 上：

1. 装依赖 → 导入自检
2. `PyInstaller --onefile --windowed` 打包（内置 `assets/` 与默认 `calibration.json`）
3. 冒烟测试：体积下限 + `--version` 退出码（硬校验）+ GUI 启动存活 8 秒（软校验）
4. 打成 zip 并自动挂到对应 Release

所以**本地不需要 Windows 工具链**，打 tag 就有 exe。

## 本版内容

**CAN 驱动层跨平台**

- 覆盖 `RobstrideBus.connect()`：上游库把 `interface="socketcan"` 写死，Windows 打不开总线
- 总线枚举：Windows 走 `can.detect_available_configs()`，自动过滤 `virtual` / `udp_multicast` / `serial` 等非硬件通道
- `ensure_can()` 在 Windows 上不再执行 `ip link` / `canup.sh`，只确认厂商驱动可用
- 自动识别左右臂的逻辑两个平台共用
- 新增 `--interface`（`main` / `discover` / `calibrate`）

**安装与启动**

- 编号入口 `1-安装.bat` / `2-启动.bat`，真正的逻辑在 `windows\bin\`（与团队其它 Windows 交付物一致）
- `find_python.ps1`：py 启动器 → PATH → 常见安装目录逐级查找 3.10+，缺失时可 `winget` 自动安装
- `install_windows.ps1`：`.venv` 独立环境 → 依赖（清华源，失败回退官方源）→ 自检 → 桌面快捷方式
- `selfcheck.py`：Python 版本/位数、numpy、python-can、robstride-dynamics、tkinter、Tk、电机表、CAN 适配器
- `start_gui.bat` 用 `pythonw` 启动（无控制台），自动带 `PYTHONUTF8=1`（中文路径/日志不乱码）
- `run.bat` 带控制台启动，方便排查 CAN 问题
- `build_exe.bat` 可打包单文件 exe

**其它**

- 版本号：Windows 显示 `v2.0.5-win`，Linux 仍为 `v2.0.5`（同一份代码，`display_version()` 自动判断）
- 字体候选补 `Microsoft YaHei UI` / `Cascadia Mono` / `Consolas`
- 打开录制目录在 Windows 上改用 `os.startfile`
- 图标补 `assets/logo-emblem.ico`（快捷方式与 exe 用）

## 平台支持

| 架构 | 支持 | 说明 |
|---|---|---|
| x64 | ✅ | 主要验证目标 |
| arm64 | ✅ | 依赖均为纯 Python 或官方 wheel |
| x86 (32 位) | ❌ | numpy 已无 32 位 wheel，安装脚本会提示改装 64 位 |

## v2.0.5-win 修订（验收问题修复）

首次交付后在 Windows 上验收，暴露出"**GUI 能开，但提示没有检测到可用的 CAN 适配器**"。

### 真正的原因：走错了后端

同一批 candleLight 棒子有两条软件路，`gs_usb` 那条在 Windows 上是**死路**：

| 后端 | 枚举 | 发帧 | 结论 |
|---|---|---|---|
| `gs_usb`（pyusb + libusb） | python-can 根本没实现 candleLight 的探测 | 每帧 1 秒 USB 超时（`The message could not be sent`） | ❌ |
| `candle`（`python-can-candle` 插件，C 库） | 按序列号列出，稳定 | 实测 16/16 电机在线 | ✅ 改成默认 |

别人的能跑、我们的不能跑，差别就在这里 —— `Prime_Lite_HW_Platform` 用的正是
`interface=candle` + `channel="<序列号>:0"`。

现在的做法：

- 依赖里加 `python-can-candle`（Windows），自动装 `candle_api`；
- `detect` 优先枚举/使用 `candle`，按**序列号**寻址（换 USB 口不会串）；没有 candle 时才退到 `gs_usb`；
- 程序里还会手工把 `candle` 注册进 `can.interfaces.BACKENDS`，保证 exe 里也认得；
- exe 打包带 `--collect-all candle --collect-all candle_api --copy-metadata python-can-candle`。

### 顺手修掉的其它坑

| # | 问题 | 现象 | 修复 |
|---|---|---|---|
| 1 | `gs_usb` 适配器枚举不到 | 日志"没有检测到可用的 CAN 适配器" | python-can 的 `detect_available_configs()` **没实现** gs_usb 的探测，改为自己用 pyusb 扫 USB（只是兜底） |
| 2 | 缺 `pyusb` / `gs_usb` 两个包 | `--interface gs_usb` 报 `No module named 'usb'` | 写进 `requirements*.txt` |
| 3 | 缺 `libusb-1.0.dll` | pyusb `NoBackendError` | 依赖 `libusb-package`；`bhl/libusb.py` 逐级查找并预热后端 |
| 4 | exe 里没有 `can.interfaces.*` | exe 版报 `Cannot import module can.interfaces.gs_usb` | PyInstaller 显式 `--hidden-import` 各后端（CI 的 `--diagnose` 冒烟测试会硬校验） |
| 5 | 适配器回显自己的发送帧 | 会把自己的发送帧当成电机应答 | `ArmBus.receive/pump` 丢掉 `is_rx=False` 的帧（candle 也会回显） |
| 6 | USB 句柄不释放 | 同一个适配器第二次打开报 `Access denied` / `Cannot open device` | gs_usb 走 `dispose_resources`；candle 补调 `CandleDevice.close()`（它自己的 `shutdown()` 不关设备，实测开 8~10 次就开不动了） |
| 7 | 驱动调用没有超时 | 适配器被别的进程占住时，`list_device()` 会**永久阻塞**，界面卡死在"正在连接/扫描中" | 所有要碰 candle 硬件的操作（枚举 / 探测 / 监听）都挪进**子进程**（`app/can_scan.py`；exe 里是 `--can-scan-worker`），父进程 `subprocess.run(timeout=…)` 卡点即杀 —— 进程一死，操作系统自动回收它攥着的 USB 句柄；正式连接/断开再用 6 秒软超时兜底 |
| 8 | 界面严重变形 | 高分屏（200% 缩放）下 exe 没声明 DPI 感知，Windows 把窗口按缩放比做**位图拉伸**；Tk 又按显示器 DPI 放大字号，形成"字大框小" | exe 清单加 `PerMonitorV2`（`windows/bin/app.manifest`）+ 启动时 `SetProcessDpiAwareness(2)`；`tk scaling` 固定 96dpi，字、间距、控件同比例缩放，与 Ubuntu 版一致；窗口按"物理分辨率 ÷ 缩放倍数"钳制，绝不顶出屏幕 |

还加了两个排障入口：

- `--diagnose`（GUI 版也有）：依赖版本、libusb 状态、枚举到的适配器、逐条试开、扫描电机，
  结果同时写到程序目录的 `can-diagnose.txt`（exe 没有控制台，靠这个文件带出信息）；
- `app\discover.py` 增加"试开每条总线"，直接把打不开的适配器和原因列出来。

## 已知限制

- **本机验收只做到"适配器能枚举 / 能打开 / 监听"**：验收时 5 条总线**全部静音**
  （`--diagnose` 监听 1.5 秒收到 0 帧），4 条连发帧都拿不到 ACK
  （`CanOperationError: The message could not be sent` = 发出去没人应答），
  所以"扫描到电机 → 录一段 → 放一段"仍需接上电的机械臂再走一遍。
  这一层已经是电/线/供电的问题，软件侧该做的都做了。
- 源码包（`.bat` / `.ps1` 安装器）已在真实 Windows 11 + Python 3.13 上跑通
  （`1-安装.bat` 一次通过，`2-启动.bat` 能拉起界面并连上 candleLight 适配器）。
- Windows 下不使用 `socketcan`，因此 `canup.sh` 和 `calibrate.py` 里的 `ip link` 检查不生效
  （已按平台跳过）。
- 自定义打包可用 `windows\bin\build_exe.bat`（本地 PyInstaller），或直接用 Release 里的现成 exe。

## 校验

解压后可直接运行 `windows\bin\run.bat`，程序启动时会在日志里打印：

- 检测到的 CAN 适配器列表
- 每条臂挂在哪条总线上（如 `左臂 → PCAN_USBBUS1 (pcan)`）
- 扫到的电机数与关节清单

排障首选 `--diagnose`：依赖 → libusb → 适配器枚举 → 逐条试开 → **逐条监听 1.5 秒** → 扫描电机，
一眼能看出卡在哪一层；exe 版结果同时写到同目录的 `can-diagnose.txt`。
