# Lite 动作录制 / 回放　`v2.0.5-win`

> **这是 Windows 版分支。** Ubuntu / Linux 版 → [https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/ubuntu](https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/ubuntu)　｜　版本索引 → [https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/main](https://github.com/TeamLite-DeepCybo/lite-record_replay/tree/main)

Prime Lite 半身双臂机器人的**拖动示教录制 / 动作回放**工具：一个窗口、双击即用。

![界面预览](app/docs/ui-preview.png)

- **零配置**：启动时自动认出左右臂接在哪两只 USB-CAN 上、有没有夹爪、哪些电机在线
- **认一次就记住**：之后只打开左右臂那两只适配器，其它口一律不碰（避开了 candle 驱动在悬空总线上卡死的问题）
- **录制带时间戳**：回放按时间重采样，速度永远是对的
- **关节按名字对齐**：14 / 16 关节的录制可以互相回放，缺的关节保持不动
- **回放原封不动**：录制了什么位置就发什么，不做任何限位裁剪
- **重上电安全**：先原地变硬、再以 ≤0.4 rad/s 靠近首帧；姿态读数离谱时直接拒绝
- **异常必松手**：任何异常都会先阻尼 0.3 秒再失能，不会把电机留在使能状态

---

## 快速开始

### 方式 A：直接用 exe（推荐，不需要 Python）

到 [Releases](https://github.com/TeamLite-DeepCybo/lite-record_replay/releases) 下载
`LiteRecordReplay-2.0.5-win-x64.zip`（arm64 机器下 `-arm64`），解压后**双击 `LiteRecordReplay.exe`**。

单文件、带图标、无黑框，机器上不需要装 Python。

### 方式 B：从源码跑（便于改代码）

| 步骤 | 双击 | 说明 |
|---|---|---|
| 1 | `windows\1-安装.bat` | 装运行环境（`.venv`）+ 建桌面快捷方式，首次约 1~3 分钟 |
| 2 | `windows\2-启动.bat` | 打开界面；没装过会自动先装 |

启动后等它扫完电机，直接点「开始录制」即可，**不需要改任何代码**。

也可以带参数启动（参数会透传给程序）：

```bat
windows\2-启动.bat --channels "candleLight#0","candleLight#1"   :: 手动指定左右臂适配器
windows\2-启动.bat --robot no_gripper                            :: 强制按"无夹爪"（默认 auto）
windows\2-启动.bat --version                                     :: 查看版本
```

> 两种方式的界面、功能、CAN 驱动完全一样。方式 A 的 exe 由
> [GitHub Actions](.github/workflows/build-exe.yml) 在 Windows runner 上自动构建（x64 + arm64），
> 每次打 tag 都会重新生成并挂到 Release。

---

## 界面

| 区域 | 内容 |
|---|---|
| **标题栏** | 状态圆点（灰=未连接 / 绿=就绪 / 橙=录制中 / 蓝=回放中）+ 标题 + 右上角公司 logo 与版本号 |
| **机器人** | 机型三选一（自动识别 / 有夹爪 / 无夹爪）+ 右侧「重新连接」「扫描电机」；下一行是状态摘要 |
| **录制** | 动作名（可留空）+「开始录制」+「结束并保存」 |
| **回放** | 第一行：动作下拉框 +「刷新 / 打开文件夹 / 删除」；第二行：次数、速度、出发前靠近 + 右对齐的「执行回放」「停止」 |
| **状态 + 日志** | 实时帧数/频率/峰值力矩、进度条，以及带时间戳、分颜色的日志（绿=成功、橙=警告、红=错误） |

动作按钮按用途配色，一眼能分清：

| 按钮 | 颜色 | 色值 |
|---|---|---|
| 扫描电机 | 青 | `#0f766e` |
| 开始录制 | 绿 | `#15803d` |
| 结束并保存 | 琥珀 | `#b45309` |
| 执行回放 | 蓝 | `#2563eb` |
| 停止 | 红 | `#c62828` |

快捷键：`Esc` 随时停止，`F5` 刷新动作列表。

字号跟随系统显示缩放（125% / 150% / 200% 下与其它 Windows 程序一致），窗口大小会记住你上次关掉时的尺寸。

### 录一段动作

1. 手臂会先软下来（力矩上限 1 Nm），可以直接用手拖；
2. 摆好后点「开始录制」，用手把动作做一遍；
3. 点「结束并保存」，自动存盘并出现在动作下拉框里。

> 录制时**没被扶住的关节会因重力缓慢下垂**，这段下垂同样会被录进去。
> 只想动一条胳膊时，请把另一条扶住或用支架托住。

### 放一段动作

1. 在下拉框选中动作（显示 时间 · 时长 · 关节数 · 动作名）；
2. 需要的话改「次数」「速度」「出发前靠近」；
3. 点「执行回放」。

**出发前靠近**：默认 0.5 秒、最小 0.5 秒。回放前用这段时间从当前姿态平缓走到动作第一帧；
姿态差得多时会自动按 0.4 rad/s 以内的安全速度延长。

回放中偶发一次 USB 超时不会中止整段动作（最多连续 6 帧才会判定掉线并停机）。

---

## CAN 适配器

Windows 没有 socketcan，走 python-can 的厂商驱动。**candleLight / CANable 这类开源固件棒用 `candle` 后端**，
按**设备序列号**寻址（`"0024001E...:0"`），所以换 USB 口、换枚举顺序都不会认错设备。

| 适配器 | 驱动名 | 备注 |
|---|---|---|
| **candleLight / CANable / canable2** | `candle` | 开源固件 USB-CAN，**首选**（`python-can-candle`） |
| 同上的兜底 | `gs_usb` | pyusb + libusb；该后端在 Windows 上发帧会超时，只在没有 candle 时用 |
| PEAK PCAN-USB | `pcan` | 装 PCAN-Basic 驱动 |
| 周立功 CANalyst-II | `canalystii` | 装厂家驱动 |
| CANable / slcan 串口 | `slcan` | 需要指定 COM 口（要 pyserial） |
| Kvaser / Vector / SYSTEC / IXXAT | 同名 | 装厂家驱动 |

### 认一次，之后只碰这两只

首次扫描成功后，程序会把左右臂的**适配器序列号**记在程序目录的 `.robot-buses.json` 里，
之后启动、点「扫描电机」都只打开这两只 —— 其余口不枚举、不打开、不嗅探。

> 这一条很关键：接在**没上电的线**上的适配器会持续刷错误帧，candle 的原生 `open()` 在这种口上有概率卡死，
> 而且卡住后 Windows 杀不掉进程、USB 句柄也不释放。只碰健康的两只，就从源头避开了这个问题。

换过适配器或换过 USB 口时，把 `.robot-buses.json` 删掉（或点「扫描电机」时它认不到电机会自动全量重扫）。

手动指定：

```bat
windows\2-启动.bat --interface candle --channels "candleLight#0","candleLight#1"
```

### 先跑一次体检

```bat
LiteRecordReplay.exe --diagnose
```

结果同时写到 exe 旁边的 `can-diagnose.txt`，会列出依赖版本、枚举到的每个适配器（标签 / 驱动 / 通道号）、
每条总线是"发现 N 个""可以打开但没有应答"还是"用不了"，并对没有应答的总线静默监听 1.5 秒。

---

## 常见问题（Windows）

**双击没反应 / 闪一下就没了**

改用 `windows\bin\run.bat`（带控制台），它会保留窗口并显示完整报错。

**弹框「CAN 适配器被占用」**

USB 上插着的适配器比程序能认到的多，说明有设备被别的进程（最常见是上一次卡死的旧进程）占着。
把 USB-CAN 拔下来再插回去，然后点「重新连接」。**不要**在提示状态下继续扫描——那会卡住并再留一个占用的进程。

**扫描时卡住不动**

程序会自己超时中止（不会把界面冻死），日志里会有 `探测超过 N 秒没有返回`。
按上面的「拔插一次」处理；如果反复出现，把程序目录下的 `worker-watch-<pid>.txt` 和 `worker-boot.txt` 发给开发。

**少了一个关节 / 夹爪没检测到**

机型选「有夹爪」再点「扫描电机」，弹窗会直接标出哪个电机未响应。

**两台机器人的 CAN 口顺序不一样**

```bat
windows\2-启动.bat --channels "candleLight#0","candleLight#1"
```

**提示 32 位 Python**

新版 numpy 不再提供 32 位（x86）安装包。请改装 64 位 Python，`windows\1-安装.bat` 检测到 32 位会直接提示并退出。

**杀毒软件拦截**

`.venv\` 里的 python 是标准虚拟环境，部分杀软会对新生成的 exe 报警，加白名单即可。

**想要一个真正的 .exe（单文件，双击即用）**

运行 `windows\bin\build_exe.bat`，会用 PyInstaller 打出 `dist\LiteRecordReplay.exe`。
exe 与架构相关，x64 / arm64 需要各自打一次（CI 里两个架构都会打）。

**卸载**

删掉整个文件夹即可（运行环境都在 `.venv\` 里，注册表无残留）。

---

## 机型与电机扫描

**选机型**：标题栏下面一行「机器人类型」三选一，选中的是实心蓝。

| 机型 | 含义 |
|---|---|
| **自动识别**（默认） | 扫到谁就用谁 |
| **有夹爪（16 关节）** | 按 16 个关节去扫；哪个夹爪没响应会在日志里点名 |
| **无夹爪（14 关节）** | 只认 14 个关节，不会去找夹爪 |

选定后自动重连。选了「有夹爪」但某个夹爪没接时，程序会明确列出"应该有但没响应"的关节名。

**扫描电机**：点一下弹窗列出每个电机的 ID / 型号 / 挂在哪条总线 / 在线还是未响应；
扫描完成后会自动按结果重新连接，不需要再点「重新连接」。

**重新标定**：

```bat
.venv\Scripts\python.exe app\calibrate.py
```

把每个关节缓慢推到上下限各一次（夹爪推到全开/全闭），按 Ctrl-C 结束，
程序算出偏移量并显示行程，确认后写入 `app\calibration.json`。
只有真正走够行程的关节才会被更新。

**回放时机械臂突然大幅运动**

看日志里这两行：

- `首帧与当前姿态相差 X.XX rad，先缓慢靠近 Y.Y 秒` —— 正常，就是靠近过程；
- 弹窗 `当前姿态和这段录制的首帧差得离谱` —— 姿态读数与标定对不上，先查读数/重新标定。
  关节读数绕圈（差出整圈 2π）时会自动按同一角度回放，不算异常。

**旧版本录制的动作还在吗？**

```bat
.venv\Scripts\python.exe app\main.py --import-legacy <旧录制目录>
```

会写进同一个动作列表（保留原始录制时间）。第一次启动时若列表为空、且程序旁边有旧录制，也会自动问一次。

**看看现有动作**

```bat
.venv\Scripts\python.exe app\main.py --list
```

---

## 目录结构

```
.
├── README.md              本文件（Windows 版）
├── LICENSE                Apache-2.0
├── windows/               Windows 入口（1-安装 / 2-启动）+ bin/ 实现 + README/RELEASE
└── app/                   程序本体
    ├── main.py            界面入口
    ├── can_scan.py        扫描子进程（candle 操作隔离在这里跑）
    ├── calibrate.py       标定
    ├── discover.py        查看 CAN 口 / 电机在线情况
    ├── calibration.json   标定偏移量
    ├── requirements.txt   运行时依赖
    ├── bhl/
    │   ├── config.py      ★ 电机表（ID / 型号 / 方向 / 限位）
    │   ├── bus.py         CAN 总线封装（冲洗 + ID 过滤 + 超时预算）
    │   ├── arm.py         机械臂：批量读写、软启动、安全上下电
    │   ├── detect.py      自动识别通道 / 在线电机 / 夹爪
    │   ├── usbwatch.py    监测 USB 插拔（拔插后拦住失效连接）
    │   ├── storage.py     录制文件与标定文件读写
    │   ├── motion.py      录制 / 回放纯逻辑（不含界面）
    │   └── util.py        限频器等小工具
    ├── assets/            公司 logo
    ├── docs/              界面预览图
    └── data/motions/      录制文件（*.npz，属于使用者数据，不入库）
```

录制文件是自描述的 npz：**关节名、时间戳、速度、标签、时长** 都在里面。
想改电机 ID、型号、方向或关节限位，只改 `app\bhl\config.py` 里的 `MOTORS` 表。

程序目录里还会生成几个运行期文件（不入库）：

| 文件 | 用途 |
|---|---|
| `calibration.json` | 标定偏移量 |
| `.robot-buses.json` | 记住左右臂插在哪两只适配器上 |
| `.last-good-buses.json` | 上次扫到电机的总线（用于排序） |
| `window.json` | 上次关窗口时的窗口大小 |
| `can-diagnose.txt` / `worker-*.txt` | 体检与排障日志 |

---

## 版本历史

| 版本 | 内容 |
|---|---|
| **v2.0.5-win** | Windows 版：candleLight 走 candle 后端；按序列号记住左右臂适配器，只探这两只；扫描子进程逐条回传、超时保留部分结果、退出不留僵尸；占用检测；热拔保护；回放容错与 2π 绕圈处理；字号跟随 DPI、窗口记忆 |
| **v2.0.5** | 界面细化（五种按钮配色、公司 logo、版本号、布局对齐）；新增机型选择与电机扫描；修复重上电后首次回放突然大动、速度前馈抖动；整理目录、补 LICENSE |
| **v2.0.0** | 重构：4 份重复代码合成一份 + `app/bhl` 模块化；录制带时间戳、关节按名字对齐、自描述录制文件、命令行参数化 |
| **v1.2.0** | 无夹爪版本支持重复回放 |
| **v1.1.0** | 加入 CAN 通道自动识别与在线电机降级 |
| **v1.0.0** | 原始录制 / 回放版本，来自上游 [Prime-Lite `record_replay`](https://github.com/pengyichen2026/Prime-Lite/tree/main/record_replay) |

各版本对应仓库里的 tag：`v1.0.0` / `v1.1.0` / `v1.2.0` / `v2.0.0` / `v2.0.5` / `v2.0.5-win`。
v1.0.0 的原始代码由 [pengyichen2026/Prime-Lite](https://github.com/pengyichen2026/Prime-Lite) 提供，
本仓库在此基础上演进，特此致谢。

---

## 许可

本项目采用 **Apache License 2.0**，全文见 [LICENSE](LICENSE)。

- 可以自由使用、修改、商用、再分发，需保留版权与许可声明；
- 公司 logo（`app/assets/`）与"深度机智 DeepCybo"名称属商标性资产，不在代码许可范围内，二次分发请替换为你自己的品牌资源；
- 第三方依赖沿用其原许可：`robstride_dynamics`（MIT）、`numpy`（BSD-3-Clause）、`python-can`（LGPL-3.0，仅作为依赖安装，不随本仓库分发）。
