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

## 已知限制

- **exe 已通过 CI 冒烟测试**（Windows runner 上能启动 GUI 并存活），但**未接真机 CAN 做端到端验证**：
  请首次上机时按「扫描电机 → 录一段 → 放一段」的顺序走一遍。
- 源码包（`.bat` / `.ps1` 安装器）为静态审查，尚未在真实 Windows 上跑过。
- Windows 下不使用 `socketcan`，因此 `canup.sh` 和 `calibrate.py` 里的 `ip link` 检查不生效
  （已按平台跳过）。
- 自定义打包可用 `windows\bin\build_exe.bat`（本地 PyInstaller），或直接用 Release 里的现成 exe。

## 校验

解压后可直接运行 `windows\bin\run.bat`，程序启动时会在日志里打印：

- 检测到的 CAN 适配器列表
- 每条臂挂在哪条总线上（如 `左臂 → PCAN_USBBUS1 (pcan)`）
- 扫到的电机数与关节清单
