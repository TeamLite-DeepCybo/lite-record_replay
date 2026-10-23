# v2.0.5-win 发布说明

Windows 版，功能与 Ubuntu 版 `v2.0.5` 一致（同一套 `app/` 代码，差异只在 CAN 驱动层）。

## 下载与安装

Release 上有两个包，按机器架构选一个：

| 包 | 适用 | 用法 |
|---|---|---|
| `LiteRecordReplay-2.0.5-win-x64.zip` | 绝大多数 Windows（x64） | 解压 → 双击 `LiteRecordReplay.exe`，**不需要装 Python** |
| `LiteRecordReplay-2.0.5-win-arm64.zip` | Windows on ARM（骁龙本等） | 同上 |

解压后保持这些文件在同一目录：

```
LiteRecordReplay.exe     单文件程序
calibration.json         标定文件（找不到时用内置副本自动生成）
data\motions\            录制文件存放处（可以先删掉让它自动创建）
README.md                使用说明
```

想跑源码 / 改代码：取本仓库的 `windows` 分支，双击 `windows\1-安装.bat` → `windows\2-启动.bat`
（Release 里不再附带源码包，源码始终以分支为准）。

## exe 是怎么构建的

`.github/workflows/build-exe.yml`：打 `v*` tag 或手动触发时，在 GitHub 的
`windows-latest`（x64）与 `windows-11-arm`（arm64）runner 上：

1. 装依赖 → 导入自检 → USB-CAN 依赖自检（`usb` / `gs_usb` / `libusb_package` / `bhl.libusb`）
2. `PyInstaller --onefile --windowed` 打包（内置 `assets/` 与默认 `calibration.json`）
3. 冒烟测试（硬校验）：
   - exe 体积 ≥ 5 MB
   - `--version` 退出码 0
   - `--diagnose` 能写出报告，且报告里含 `gs_usb` / `libusb` / `CAN 适配器`，且没有 `No module named 'can.interfaces...'`
   - GUI 启动后存活 8 秒（软校验：CI 无交互桌面时可能起不来，不判失败）
4. 打包成 `LiteRecordReplay-<版本>-<架构>.zip`（exe + `calibration.json` + `data\motions\` + `README.md`）
5. 自动挂到对应 Release（`gh release upload --clobber`）

所以**本地不需要 Windows 工具链**，打 tag 就有 exe。

## 平台支持

| 架构 | 支持 | 说明 |
|---|---|---|
| x64 | ✅ | 真机验证过 |
| arm64 | ✅ | 依赖均为纯 Python 或官方 wheel；**未在 arm64 实机上验证** |
| x86 (32 位) | ❌ | numpy 已无 32 位 wheel，安装脚本会提示改装 64 位 |

## 本版内容

**CAN 驱动层**

- 覆盖 `RobstrideBus.connect()`：上游库把 `interface="socketcan"` 写死，Windows 打不开总线
- candleLight / CANable 走 **`candle`** 后端（`python-can-candle`），按**设备序列号**寻址
- 自动识别左右臂的逻辑两个平台共用：ping 电机 ID，看哪条总线有应答

**安装与启动**

- 编号入口 `windows\1-安装.bat` / `2-启动.bat`，真正的逻辑在 `windows\bin\`
- `find_python.ps1`：py 启动器 → PATH → 常见安装目录逐级查找 3.10+，缺失时可 `winget` 自动装
- `install_windows.ps1`：`.venv` 独立环境 → 依赖 → 自检 → 桌面快捷方式
- `selfcheck.py`：Python 版本/位数、numpy、python-can、robstride-dynamics、tkinter、Tk、电机表、CAN 适配器
- `start_gui.bat` 用 `pythonw` 启动（无控制台），带 `PYTHONUTF8=1`（中文路径/日志不乱码）
- `run.bat` 带控制台启动，方便排查 CAN 问题
- `build_exe.bat` 可本地打包单文件 exe

**其它**

- 版本号：Windows 显示 `v2.0.5-win`，Ubuntu 仍为 `v2.0.5`（同一份代码，`display_version()` 自动判断）
- 字体候选补 `Microsoft YaHei UI` / `Cascadia Mono` / `Consolas`
- 打开录制目录在 Windows 上改用 `os.startfile`
- 图标补 `assets/logo-emblem.ico`（快捷方式与 exe 用）

## 验收与稳定性修复（这一版实际交付的内容）

首次交付验收时暴露的问题，以及后续在真机上逐条定位、修掉的问题：

| # | 问题 | 现象 | 处理 |
|---|---|---|---|
| 1 | 走错后端 | GUI 能开但"没有检测到可用的 CAN 适配器" | candleLight 改用 `candle`（`gs_usb` 在 Windows 上发帧必超时，只留作兜底） |
| 2 | `gs_usb` 枚举不到 | 同上 | python-can 没实现它的探测，改为自己用 pyusb 扫（兜底路径） |
| 3 | 缺 `pyusb` / `gs_usb` / `libusb-1.0.dll` | `No module named 'usb'` / `NoBackendError` | 写进依赖清单；`bhl/libusb.py` 逐级查找并预热后端 |
| 4 | exe 里缺 `can.interfaces.*` | exe 版报 `Cannot import module can.interfaces.gs_usb` | PyInstaller 显式 `--hidden-import` 各后端；CI 用 `--diagnose` 硬校验 |
| 5 | 适配器回显自己的发送帧 | 把自己的帧当成电机应答 | `ArmBus.receive/pump` 丢掉 `is_rx=False` 的帧 |
| 6 | USB 句柄不释放 | 同一适配器第二次打开报 `Access denied` | gs_usb 走 `dispose_resources`；candle 补 `CandleDevice.close()` |
| 7 | 驱动调用会永久阻塞 | 适配器被别的进程占住时界面卡死 | 所有碰 candle 硬件的操作挪进子进程（`app/can_scan.py`，exe 里是 `--can-scan-worker`） |
| 8 | 扫描偶发卡 30~80 秒 | 卡在一条总线后整批超时 | worker 每扫完一条就回传一条结果；父进程超时也保留已完成部分；超时上限 31 秒 |
| 9 | 卡死的 worker 变成僵尸 | 进程杀不掉、一直攥着 USB，后续扫描全挂 | worker 退出前 `shutdown()` 复位通道，然后 `os._exit` 硬退出，句柄交系统回收 |
| 10 | 悬空总线上的适配器会让 candle 原生层卡死 | 偶发 `open()` 不返回 | **记住左右臂的适配器序列号**，之后只打开这两只，其它口不枚举、不打开、不嗅探 |
| 11 | 有设备被占用时仍继续扫描 | 又卡一次、又留一个僵尸 | 物理在线数 > candle 认出数时弹框提示拔插，直接拦下不扫 |
| 12 | 拔插 USB 后点回放会闪退 | `candle_api` fail-fast（0xc0000409），窗口瞬间消失 | `usbwatch` 每 0.9 秒比对设备快照，拔插后立即标记失效并拦住录制/回放 |
| 13 | 一次发帧超时中止整段回放 | `The message could not be sent` → 回放中止 | 容忍偶发失败（连续 6 帧才判定掉线），并在备注里说明跳过多少帧 |
| 14 | 曾尝试把"接近整圈"的差值当绕圈、平移整段轨迹 | **反效果**：夹爪回放方向反了、没回到初始位置 | 已撤销：RobStride 是多圈绝对位置，差一圈就是真的差一圈，必须按录制原样回放 |
| 15 | 扫描完还要手动点「重新连接」 | 多一步操作 | 扫描结束后自动按结果重新连接 |
| 16 | 高分屏字号偏小、初始窗口过大 | 200% 缩放下字只有一半大；窗口铺满屏幕 | 字号跟随系统 DPI；窗口按内容自适应并记住上次大小 |
| 17 | 扫描死线时逐帧白等 1 秒 | 扫描慢 | 收/发超时 1.0s → 0.2s |

## 验证状态（真机）

**已验证**（Windows 11 `10.0.26200` x64 + 5× candleLight + RobStride 双 7 轴 + 夹爪 = 16 关节）：

- 枚举 → 扫描：认出两条手臂总线（`0024001C` 右 / `0024001E` 左），16 个电机，2~3 秒完成
- 录制：7.5 秒 / 376 帧、13.6 秒 / 682 帧，均正常存盘并出现在动作列表
- 回放：实测 **50.0 Hz**，峰值力矩 7~12 Nm，出发前靠近按 0.4 rad/s 限速执行
- 热拔：拔掉 USB-CAN 后 1 秒内检测到并禁用录制/回放（不再闪退）；重新连接后恢复正常
- 偶发发帧超时：回放继续跑完，备注说明跳过帧数
- 200% 显示缩放下字号与系统一致、窗口可记忆
- 源码模式与 CI 产出的 exe 都跑通了 `--diagnose`

**未验证**（沿用代码路径，未上机）：

- arm64 实机
- `pcan` / `canalystii` / `slcan` / `kvaser` / `vector` 等其它厂商后端
- 长时间连续运行（> 数小时）与多台机器人同时使用

## 已知限制

- **candle 原生层仍可能在异常适配器上卡死**：Windows 无法终止已阻塞在内核态 USB I/O 的进程，句柄也收不回。
  程序的对策是"记住左右臂两只、只碰它们"+"检测到占用就拦下并提示拔插"，从源头避免踩到；
  万一真的踩到，需要拔插一次 USB-CAN（或重启）恢复。
- **只支持 64 位**；32 位 Python 装不上 numpy。
- **exe 未做代码签名**：首次运行时 Windows SmartScreen / 杀软可能提示"未知发布者"，属正常现象。
- **固定 1 Mbps**：RobStride 电机默认波特率，程序里写死；改了电机波特率需要改 `app/bhl/config.py`。
- Windows 下不使用 `socketcan`，所以 `calibrate.py` 里的 `ip link` 检查按平台跳过。

## 排障入口

- `LiteRecordReplay.exe --diagnose`（GUI 也有）：依赖 → libusb → 适配器枚举 → 逐条试开 → 逐条监听 1.5 秒 → 扫描电机，
  结果同时写到程序目录的 `can-diagnose.txt`（exe 没有控制台，靠这个文件带出信息）。
- `app\discover.py`：列出当前认到的适配器与每条总线的试开结果。
- `windows\bin\run.bat`：带控制台启动，直接看报错。
- 扫描卡住时，程序目录会留下 `worker-watch-<pid>.txt`（最后一行是卡住的位置）和 `worker-boot.txt`（worker 启动阶段）。
