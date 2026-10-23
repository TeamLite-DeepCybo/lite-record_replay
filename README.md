# Prime Lite 动作录制回放工具 — macOS 版

> **这是 macOS 版分支。** Windows 版 → [https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/windows](https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/windows)　｜　版本索引 → [https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/main](https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/main)

## 快速开始

**不需要预先安装任何东西**（不用 Homebrew、不用 Python、不用 libusb）。
只要 Mac 能联网，安装脚本会自己下载需要的组件。

1. 解压压缩包到任意目录（例如 `~/Documents/prime_lite_macos`）
2. **双击 `install_mac.command`** 安装（约 3–5 分钟，只需一次）
3. **双击 `start_mac.command`** 启动

也可以在终端里运行：

```bash
cd prime_lite_macos              # ← 解压后的文件夹
bash install_mac.sh              # 安装（只需一次）
bash start_mac.sh                # 启动（有假爪）
bash start_mac.sh no_gripper     # 启动（无假爪）
```

> **第一次双击 `.command` 文件如果被 macOS 拦住**（提示"无法打开，因为
> 来自身份不明的开发者"），右键点该文件 → **打开** → 再点"打开"即可。
> 只需要做一次。

安装脚本会做这些事：

| 步骤 | 说明 |
|---|---|
| 下载 uv | 一个约 15 MB 的 Python 工具，装在 `~/.local/bin`，不需要 sudo |
| 下载 Python 3.12 | uv 自带的独立 Python，内置 tkinter |
| 创建 `.venv` | 项目专用虚拟环境，不污染系统 Python |
| 安装依赖 | numpy、python-can、robstride-dynamics、pyusb、libusb 等 |
| 自检 | 检查 tkinter / libusb / 适配器，并把结果打印出来 |

## 使用流程

启动后 GUI 会先在后台自动识别左右臂（按电机 ID 判断，不依赖插在哪个口），
再扫描两个通道上的电机。换板子、换机器、换插口都不需要改配置。扫描完成后：

1. **开始录制** — 手动拖动机械臂做动作
2. **结束录制** — 动作保存为 `arm/recorded_motion_<名字>.npy`
3. 在「回放动作编号」里填入编号，点 **执行回放**

录制数据保存在 `have_gripper/arm/`（或 `no_gripper/arm/`）下，编号自增。

## 系统要求

| 项目 | 说明 |
|------|------|
| macOS 版本 | 12 (Monterey) 或更高 |
| 芯片 | Apple Silicon（M 系列）与 Intel 均可 |
| USB-CAN 适配器 | candleLight 固件的 USB-CAN 适配器（RobStride / CANable 系列） |
| Python | 无需手动安装，`install_mac.sh` 自动下载 3.12 |
| Homebrew | **不需要** |
| 网络 | 首次安装需要联网（之后可离线运行） |

## USB-CAN 适配器

### 工作方式

程序通过 **python-can + gs_usb** 直接访问 USB 适配器，**不需要任何虚拟网卡**，
也不需要装驱动：适配器插上就能枚举到，`robstride_compat.py` 负责把
RobStride 的通信协议桥接到 gs_usb 后端上。

### 你的适配器是否支持

满足以下条件之一即可：

- 板上烧录了 candleLight 固件（常见于 CANable、RobStride 官方适配器）
- 在「系统信息 → 系统报告 → USB」里能看到这个适配器

### 检测步骤

```bash
# 插上 USB-CAN 适配器后运行：
ioreg -p IOUSB | grep -i "can"
```

有输出（包含 `candleLight`）说明系统已经识别到，可以直接用。
没有输出说明 Mac 没认到设备，通常是：

1. **物理连接问题**：需要 USB-C 转 USB-A 转接头
2. **适配器不兼容**：换一个已知可用的适配器再试

### 推荐的适配器

| 型号 | 价格 | 购买渠道 |
|------|------|---------|
| CANable 2.0 (PikaLab) | ¥60-80 | [pikalab.cc](https://pikalab.cc) |
| RobStride 官方 USB-CAN V03 | ¥80+ | Prime Robotics 客服 |

## 左臂 / 右臂 怎么认出来

macOS 上没有固定的 `can0` / `can1`：python-can 按 USB 枚举顺序给适配器编号，
而这个顺序**每次插拔都会变**，用编号绑定会随机把左右臂插反。

所以程序默认走 **按电机 ID 自动识别**：

1. 把每个 USB-CAN 适配器都打开扫一遍
2. 哪个适配器上应答的是左臂 ID（11–18），就判定为左臂
3. 应答右臂 ID（21–28）的，判定为右臂

识别结果在同一次运行里缓存，不会反复扫描。

### 手动指定（可选）

如果自动识别不适用（例如某台机器的电机 ID 与默认配置不同），可以在
`arm/config.py` 里写死适配器序列号：

```python
LEFT_ADAPTER_SERIAL  = "0038003E5547570820383634"
RIGHT_ADAPTER_SERIAL = "003800205547570820383634"
```

两个都留空（`""`，默认）就走自动识别。

### 排查命令

```bash
cd have_gripper

# 列出当前接上的适配器及其序列号
python discover_actuators.py --list

# 每个适配器都扫全部 ID，看哪个口上有什么
python discover_actuators.py --full

# 正常扫描（先自动识别再扫）
python discover_actuators.py

# 只扫一侧
python discover_actuators.py --side=left
```

`--full` 的输出长这样，可以直接看出哪个口上接了哪条臂：

```
0038002C5547570820383634: 无响应
003800205547570820383634: [21, 22, 23, 24, 25, 26, 27, 28]
0038003E5547570820383634: [11, 12, 13, 14, 15, 16, 17, 18]
```

## 常见问题

### Q: 双击 install_mac.command 没反应 / 提示"无法打开"？
A: macOS 的安全限制。右键点文件 → **打开** → 在弹窗里再点 **打开**。
之后就不会再拦了。

### Q: 窗口不弹出？
A: 先看终端里的输出。如果是 tkinter 相关报错，删除 `.venv` 后重新运行
`bash install_mac.sh`。

### Q: 扫描电机时报错 "无法打开 USB-CAN 适配器"？
A: 依次检查：
1. 适配器是否被系统识别：`ioreg -p IOUSB | grep -i candleLight`
2. 适配器插好没有、是不是 candleLight 固件
3. 机器人是否已通电
4. 只接了一侧手臂时，另一侧提示扫描失败是正常的
5. 偶发 `[Errno 13] Access denied`：本项目已处理（gs_usb 会去 detach 一个
   并不存在的内核驱动）。如果还遇到，重新点一次即可。

### Q: 报「无法识别 left/right 臂」？
A: 说明没有任何适配器上扫到该侧配置里的电机 ID。运行
`python discover_actuators.py --full`，看每个口上实际有什么 ID：

- 某个口有 11–18 / 21–28 → 硬件正常，但 ID 可能和 `arm/config.py` 不一致
- 所有口都「无响应」→ 机械臂没通电，或者 CAN 线没接好

如果这台机器的电机 ID 确实和默认配置不同，改 `arm/config.py` 里的
`motor_with_gripper_configs`（或 `motor_without_gripper_configs`）。

### Q: 安装时 robstride_dynamics 报错？
A: 重新运行 `bash install_mac.sh` 即可，它会重装依赖。
手动安装的话（注意方括号要加引号，zsh 会把 `python-can[gs-usb]` 当成通配符）：

```bash
.venv/bin/python -m pip install robstride-dynamics "python-can[gs-usb]" pyusb libusb-package
```

### Q: 报 `cannot import name 'RobStrideBus'`？
A: PyPI 上的 `robstride-dynamics 0.0.1` 导出的类名是 `RobstrideBus`
（小写 s）。本项目已经按这个类名适配，请勿改回 `RobStrideBus`。

## 文件说明

```
prime_lite_macos/
├── install_mac.command       ← 双击安装
├── start_mac.command         ← 双击启动
├── install_mac.sh            ← 安装脚本（终端用）
├── start_mac.sh              ← 启动脚本（终端用）
├── have_gripper/             ← 有假爪版本
│   ├── gui.py                ← GUI 主程序
│   ├── _entry_point_init.py  ← macOS CAN shim 入口（勿删）
│   ├── discover_actuators.py ← 扫描电机
│   └── arm/                  ← 机械臂控制模块
│       ├── _entry_point_init.py  ← macOS CAN shim（勿删）
│       ├── robstride_compat.py   ← gs_usb 桥接（勿删）
│       ├── ude_calibration.json  ← 标定文件
│       └── *.py              ← record / replay / arm / api / config
├── no_gripper/               ← 无假爪版本（结构同上）
└── README.md                 ← 本文件
```

安装完成后会在项目根目录生成 `.venv/`（约 200 MB），删掉它等于卸载环境，
下次双击 `start_mac.command` 会提示重新安装。

---

如有问题，请联系 Prime Robotics 技术支持。
