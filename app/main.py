#!/usr/bin/env python3
"""Prime Lite 双臂机器人：动作录制 / 回放（主程序）。

一条命令就能用：

    python3 main.py

启动时会先把 CAN 口配置好（没起来就自动执行 canup.sh），然后自动识别
左右臂接在哪个 CAN 口、有没有夹爪、哪些电机在线，
扫完直接在界面上点"开始录制"即可，不需要改任何代码。

常用参数：
    --channels can2,can1   手动指定左右臂的 CAN 口（默认自动识别）
    --import-legacy DIR    把旧版 recorded_motion_record_*.npy 批量导入后退出
    --list                 在终端里列出现有录制后退出
"""

from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from bhl import config, detect, libusb, paths, motion as motion_mod, storage
from bhl.arm import Arm
from bhl.bus import release_usb


def _enable_dpi_awareness() -> None:
    """Windows：把进程设为 DPI 感知，必须在创建任何 Tk 窗口之前调用。

    PyInstaller 打出来的 exe 清单里默认**没有** dpiAware，Windows 会把整个窗口
    按显示缩放比例（125% / 150%）做位图拉伸 —— 模糊、发虚、控件尺寸错乱，
    就是"界面严重变形"的来源。改成 DPI 感知后窗口按真实像素渲染。
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
    except Exception:  # noqa: BLE001
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def _normalize_font_scale(root: tk.Tk) -> None:
    """把 Tk 的"点→像素"换算固定成 96dpi，让 Windows 界面和 Linux 版逐像素一致。

    DPI 感知打开后，Windows 的 125%/150% 系统缩放会让 Tk 按 120/144dpi 放大字号，
    整个界面比 Ubuntu 版胖一圈；固定回 96dpi 后两边的字号、间距完全一样。
    """
    if not sys.platform.startswith("win"):
        return
    try:
        root.tk.call("tk", "scaling", 96.0 / 72.0)
    except tk.TclError:
        pass


_SINGLE_INSTANCE_MUTEX = None


def acquire_single_instance() -> bool:
    """Windows：同一时间只允许一个本程序实例。

    candleLight 这类 USB-CAN 是**独占设备**：两个实例（比如 GUI + 另开的
    `--diagnose`，或者本程序 + 别人的上位机）同时去开同一只棒子，会把
    candle 原生库卡在驱动调用里 —— 进程既不能退出也杀不掉，USB 句柄一直被攥着，
    之后所有程序都报 `access denied`，只能拔插/重启才能恢复。
    所以这里加一个全局互斥量，第二个实例直接退出。
    """
    global _SINGLE_INSTANCE_MUTEX
    if not sys.platform.startswith("win"):
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # 名字带 Global\ 才能跨会话/跨用户互斥
        handle = kernel32.CreateMutexW(None, False, "Global\\LiteRecordReplay-USB-CAN")
        if not handle:
            return True                     # 建不出来就放行，别把程序锁死
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return False
        _SINGLE_INSTANCE_MUTEX = handle     # 持有到进程退出
        return True
    except Exception:  # noqa: BLE001
        return True


# ======================================================================
# 外观
# ======================================================================
C = {
    "bg": "#f3f5f8",
    "card": "#ffffff",
    "border": "#dde1e7",
    "text": "#1f2328",
    "muted": "#6b7280",
    "faint": "#9aa0a6",
    "accent": "#2563eb",
    "accent_hi": "#1d4ed8",
    "accent_soft": "#eaf1ff",
    "ok": "#15803d",
    "warn": "#a15c00",
    "danger": "#c62828",
    "danger_hi": "#a51c1c",
    # 四个动作按钮各用一色，方便一眼区分
    "record": "#15803d",      # 开始录制 - 绿
    "record_hi": "#12693a",
    "save": "#b45309",        # 结束并保存 - 琥珀
    "save_hi": "#96450a",
    "scan": "#0f766e",        # 扫描电机 - 青
    "scan_hi": "#0b5f58",
}

UI_FAMILIES = (
    "Noto Sans CJK SC",      # Linux（本机）
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Microsoft YaHei UI",    # Windows
    "Microsoft YaHei",
    "PingFang SC",           # macOS
    "Noto Sans",
    "DejaVu Sans",
)
MONO_FAMILIES = (
    "Noto Sans Mono CJK SC",
    "DejaVu Sans Mono",
    "Cascadia Mono",         # Windows
    "Consolas",
    "Noto Sans CJK SC",
)

VERSION = "2.0.5"


def display_version() -> str:
    """Windows 版在版本号后面加 -win（同一个代码库，两个平台的发布号）。"""
    return f"{VERSION}-win" if sys.platform.startswith("win") else VERSION


ROOT_DIR = paths.app_root()          # 可写目录（数据、标定）
ASSETS = paths.resource_dir() / "assets"   # 只读资源（打包后在解包目录）

# 窗口：默认大小（实测用着舒服的尺寸）与内边距
WINDOW_SIZE = (1540, 990)
PAD = 18

# 字号 = 系统默认字号 + 下面的偏移（单位：点）。
# 只有三档：标题 / 正文 / 小字（小字只用于日志）。
# 标签、输入框、按钮、小节标题、单选都用同一档正文，避免大小混杂。
SIZE_TITLE = 4
SIZE_BODY = 1
SIZE_SMALL = 0


def _pick_font(root: tk.Misc, candidates, fallback: str | None = None) -> str:
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return name
    return fallback or tkfont.nametofont("TkDefaultFont").actual()["family"]


def _load_image(path: Path) -> "tk.PhotoImage | None":
    """读 PNG；文件不在或解码失败就返回 None（界面照常可用）。"""
    try:
        if not path.exists():
            return None
        return tk.PhotoImage(file=str(path))
    except tk.TclError:
        return None


def _make_icon(root: tk.Tk, color: str = "#2563eb", size: int = 32) -> tk.PhotoImage:
    """画一个圆点当窗口图标，省得顶栏挂个 Tk 默认羽毛。"""
    icon = tk.PhotoImage(width=size, height=size)
    radius = size / 2 - 1.5
    centre = size / 2 - 0.5
    for y in range(size):
        for x in range(size):
            if (x - centre) ** 2 + (y - centre) ** 2 <= radius ** 2:
                icon.put(color, (x, y, x + 1, y + 1))
    try:
        root.iconphoto(True, icon)
    except tk.TclError:
        pass
    return icon


class Fonts:
    """界面用到的几档字号，全部以系统默认字号为基准。"""

    def __init__(self, root: tk.Tk) -> None:
        self.ui = _pick_font(root, UI_FAMILIES)
        self.mono = _pick_font(root, MONO_FAMILIES, self.ui)
        try:
            base = abs(int(tkfont.nametofont("TkDefaultFont").cget("size"))) or 10
        except Exception:
            base = 10
        self.base = base
        self.title = (self.ui, base + SIZE_TITLE, "bold")
        self.body = (self.ui, base + SIZE_BODY)
        self.body_bold = (self.ui, base + SIZE_BODY, "bold")
        self.log = (self.mono, base + SIZE_SMALL)


def _setup_theme(root: tk.Tk) -> Fonts:
    """统一字体和 ttk 样式。"""
    fonts = Fonts(root)
    ui = fonts.ui

    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=ui, size=fonts.body[1])
        except tk.TclError:
            pass

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    style.configure(".", background=C["bg"], foreground=C["text"], font=fonts.body)
    style.configure("TFrame", background=C["bg"])
    style.configure("TLabel", background=C["bg"], foreground=C["text"])
    style.configure("Title.TLabel", font=fonts.title)
    style.configure("Muted.TLabel", foreground=C["muted"], font=fonts.body)
    style.configure("Sect.TLabel", foreground=C["muted"], font=fonts.body_bold)
    style.configure("Status.TLabel", foreground=C["text"], font=fonts.body)
    style.configure("TSeparator", background=C["border"])

    for name in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(
            name,
            fieldbackground=C["card"],
            background=C["card"],
            foreground=C["text"],
            bordercolor=C["border"],
            lightcolor=C["border"],
            darkcolor=C["border"],
            insertcolor=C["text"],
            arrowcolor=C["muted"],
            padding=8,
        )
    # clam 默认的上下箭头只有几像素、还是淡灰色，几乎看不见；放大并加深
    style.configure("TSpinbox", arrowsize=22, arrowcolor=C["text"])
    style.configure("TCombobox", arrowsize=24, arrowcolor=C["text"])

    style.map(
        "TCombobox",
        fieldbackground=[("readonly", C["card"]), ("disabled", "#f0f1f3")],
        foreground=[("readonly", C["text"]), ("disabled", C["faint"])],
        selectbackground=[("readonly", C["card"])],
        selectforeground=[("readonly", C["text"])],
    )
    style.map(
        "TSpinbox",
        fieldbackground=[("disabled", "#f0f1f3")],
        foreground=[("disabled", C["faint"])],
    )

    # ---- 实心按钮：文字白色、底色调各自不同 --------------------------
    def filled(style_name: str, color: str, hover: str, off_bg: str, off_fg: str) -> None:
        style.configure(
            style_name,
            background=color,
            foreground="#ffffff",
            bordercolor=color,
            lightcolor=color,
            darkcolor=color,
            borderwidth=1,
            focusthickness=0,
            width=-11,  # 统一宽度：同类按钮一样大，边缘能对齐
            font=fonts.body_bold,
            padding=(14, 8),
        )
        style.map(
            style_name,
            background=[("disabled", off_bg), ("pressed", hover), ("active", hover)],
            foreground=[("disabled", off_fg)],
            bordercolor=[("disabled", off_bg)],
        )

    filled("Primary.TButton", C["accent"], C["accent_hi"], "#e4e7ec", "#8b9199")   # 执行回放
    filled("Record.TButton", C["record"], C["record_hi"], "#e2eae4", "#8b9990")    # 开始录制
    filled("Save.TButton", C["save"], C["save_hi"], "#efe4d6", "#a1917f")          # 结束并保存
    filled("Scan.TButton", C["scan"], C["scan_hi"], "#e0eae8", "#869a97")          # 扫描电机
    filled("Danger.TButton", C["danger"], C["danger_hi"], "#efe0e0", "#a18686")    # 停止

    # 次要操作（描边）
    style.configure(
        "Ghost.TButton",
        background=C["bg"],
        foreground=C["muted"],
        bordercolor=C["border"],
        lightcolor=C["bg"],
        darkcolor=C["bg"],
        borderwidth=1,
        focusthickness=0,
        width=-9,  # 统一宽度，三个次要按钮一样大
        font=fonts.body,
        padding=(14, 8),
    )
    style.map(
        "Ghost.TButton",
        background=[("disabled", "#f0f1f4"), ("active", "#e8edf5")],
        foreground=[("disabled", "#9aa0a8")],
        bordercolor=[("disabled", "#e6e9ee")],
    )

    # 机型选择：选中那个是实心蓝，其余描边，一眼能看出当前机型
    style.configure(
        "SegmentOn.TButton",
        background=C["accent"],
        foreground="#ffffff",
        bordercolor=C["accent"],
        lightcolor=C["accent"],
        darkcolor=C["accent"],
        borderwidth=1,
        focusthickness=0,
        width=-9,  # 和 SegmentOff 一样宽，切换机型时按钮不会跳
        font=fonts.body_bold,
        padding=(14, 8),
    )
    style.map(
        "SegmentOn.TButton",
        background=[("disabled", "#e4e7ec"), ("pressed", C["accent_hi"]), ("active", C["accent_hi"])],
        foreground=[("disabled", "#8b9199")],
        bordercolor=[("disabled", "#e4e7ec")],
    )
    style.configure(
        "SegmentOff.TButton",
        background=C["card"],
        foreground=C["muted"],
        bordercolor=C["border"],
        lightcolor=C["card"],
        darkcolor=C["card"],
        borderwidth=1,
        focusthickness=0,
        width=-9,
        font=fonts.body,
        padding=(14, 8),
    )
    style.map(
        "SegmentOff.TButton",
        background=[("disabled", "#f0f1f4"), ("active", "#eef2f8")],
        foreground=[("disabled", "#9aa0a8")],
        bordercolor=[("disabled", "#e6e9ee")],
    )

    style.configure("TProgressbar", background=C["accent"], troughcolor="#e4e8ee", borderwidth=0, thickness=6)
    style.configure(
        "TScrollbar",
        background="#d3d8df",
        troughcolor=C["card"],
        bordercolor=C["bg"],
        arrowcolor=C["muted"],
        borderwidth=0,
    )
    return fonts


class App:
    def __init__(
        self,
        root: tk.Tk,
        forced_channels: dict[str, str] | None = None,
        robot_type: str = "auto",
        interface: str = "auto",
    ) -> None:
        self.root = root
        self.forced_channels = forced_channels
        self.interface = interface
        self.robot_type = tk.StringVar(value=robot_type)
        self.last_setup = None

        self.arm: Arm | None = None
        self.worker: threading.Thread | None = None
        self.stop = threading.Event()
        self.ui: queue.Queue = queue.Queue()

        self.mode = "connecting"
        self.motions: list[Path] = []
        self._offered_import = False
        self._frame_hint = 0
        self._progress_mode = "determinate"

        self._build()
        self._apply_mode("connecting")
        self._refresh_motions()
        self.root.after(80, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Esc 随时停机，F5 刷新动作列表
        self.root.bind("<Escape>", lambda _event: self._stop_clicked())
        self.root.bind("<F5>", lambda _event: self._refresh_motions())

        initial_type = self.robot_type.get()
        self._start(lambda: self._connect(forced_channels, initial_type))

    # ==================================================================
    # 界面
    # ==================================================================
    def _build(self) -> None:
        self.root.title(f"Prime Lite · 动作录制 / 回放　v{display_version()}")
        _normalize_font_scale(self.root)   # 必须在读字号/量屏幕之前，与 Linux 版一致
        self.fonts = _setup_theme(self.root)

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        # 窗口钳制用的"可用像素"。字号换算固定在 96dpi 后（_normalize_font_scale），
        # 1 个 Tk 像素 = 系统缩放倍数 个物理像素；winfo 报的屏幕是 Tk 像素，
        # 但窗口的物理宽度 = Tk像素 × 缩放倍数，直接拿 winfo 去钳会顶出屏幕。
        # 所以 Windows 上改用 物理分辨率 / 缩放倍数 来算，保证窗口永远落在屏幕里，
        # 并且整块界面（字、间距、控件）同比例缩放，和 Ubuntu 版一致。
        fit_width, fit_height = screen_width, screen_height
        if sys.platform.startswith("win"):
            try:
                import ctypes

                dpi = max(96, int(ctypes.windll.user32.GetDpiForSystem()))
                zoom = dpi / 96.0
                fit_width = max(640, int(ctypes.windll.user32.GetSystemMetrics(0) / zoom) - 80)
                fit_height = max(480, int(ctypes.windll.user32.GetSystemMetrics(1) / zoom) - 100)
            except Exception:  # noqa: BLE001 - 拿不到就用 winfo 的值兜底
                pass

        width = max(640, min(WINDOW_SIZE[0], fit_width))
        height = max(480, min(WINDOW_SIZE[1], fit_height))
        # 矮屏（机器人自带屏等）去掉小节标题，把高度让给日志
        self._compact = height < 800

        self.root.geometry(f"{width}x{height}")
        self.root.configure(bg=C["bg"])

        # 窗口图标优先用公司徽标，读不到就退回画一个圆点
        emblem = _load_image(ASSETS / "logo-emblem.png")
        if emblem is not None:
            try:
                self.root.iconphoto(True, emblem)
            except tk.TclError:
                pass
            self._icon = emblem
        else:
            self._icon = _make_icon(self.root)

        outer = ttk.Frame(self.root, padding=(PAD, 12, PAD, 12))
        outer.pack(fill="both", expand=True)

        # ---- 标题栏（logo 放右上角） ------------------------------------
        header = ttk.Frame(outer)
        header.pack(fill="x")
        header.columnconfigure(1, weight=1)

        self.dot = tk.Label(
            header, text="●", font=self.fonts.body_bold, fg=C["faint"], bg=C["bg"]
        )
        self.dot.grid(row=0, column=0, sticky="w", padx=(0, 8))

        ttk.Label(header, text="Prime Lite · 动作录制 / 回放", style="Title.TLabel").grid(
            row=0, column=1, sticky="w"
        )

        self._logo_image = _load_image(ASSETS / "logo.png")
        if self._logo_image is not None:
            ttk.Label(header, image=self._logo_image).grid(row=0, column=2, sticky="e")

        # 版本号紧跟 logo 右边
        ttk.Label(header, text=f"v{display_version()}", style="Muted.TLabel").grid(
            row=0, column=3, sticky="e", padx=(10, 0)
        )

        # ---- 机器人类型 + 重新连接 + 扫描 --------------------------------
        robot_row = ttk.Frame(outer)
        robot_row.pack(fill="x", pady=(12, 0))

        ttk.Label(robot_row, text="机器人类型").pack(side="left")

        segments = ttk.Frame(robot_row)
        segments.pack(side="left", padx=(12, 0))
        self.type_buttons: dict[str, ttk.Button] = {}
        for kind in config.ROBOT_TYPES:
            button = ttk.Button(
                segments,
                text=config.ROBOT_TYPE_LABELS[kind],
                command=lambda k=kind: self._choose_type(k),
            )
            button.pack(side="left", padx=(0, 6))
            self.type_buttons[kind] = button
        self._sync_type_buttons()

        # 右侧从外往里排：扫描电机在最右，重新连接在它左边
        self.btn_scan = ttk.Button(
            robot_row, text="扫描电机", style="Scan.TButton", command=self._scan_clicked
        )
        self.btn_scan.pack(side="right")

        self.btn_reconnect = ttk.Button(
            robot_row, text="重新连接", style="Ghost.TButton", command=self._reconnect
        )
        self.btn_reconnect.pack(side="right", padx=(0, 8))

        # ---- 状态行（放在机器人类型这一行下面） ---------------------------
        self.banner = ttk.Label(
            outer, text="正在连接机器人…", style="Muted.TLabel", anchor="w"
        )
        self.banner.pack(fill="x", pady=(8, 0))

        # ---- 录制 ------------------------------------------------------
        self._section(outer, "录制（拖动示教）")

        record_row = ttk.Frame(outer)
        record_row.pack(fill="x")
        record_row.columnconfigure(1, weight=1)

        ttk.Label(record_row, text="动作名").grid(row=0, column=0, sticky="w")
        self.entry_label = ttk.Entry(record_row, width=10)
        self.entry_label.grid(row=0, column=1, sticky="ew", padx=(8, 12))
        self.btn_record = ttk.Button(
            record_row, text="● 开始录制", style="Record.TButton", command=self._record
        )
        self.btn_record.grid(row=0, column=2)
        self.btn_save = ttk.Button(
            record_row, text="■ 结束并保存", style="Save.TButton", command=self._stop_clicked
        )
        self.btn_save.grid(row=0, column=3, padx=(10, 0))

        # ---- 回放 ------------------------------------------------------
        self._section(outer, "回放")

        # 动作下拉框 + 列表操作同一行
        picker = ttk.Frame(outer)
        picker.pack(fill="x")
        picker.columnconfigure(0, weight=1)

        self.combo = ttk.Combobox(picker, state="readonly", values=[])
        self.combo.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self.btn_refresh = ttk.Button(
            picker, text="刷新", style="Ghost.TButton", command=self._refresh_motions
        )
        self.btn_refresh.grid(row=0, column=1)
        self.btn_open = ttk.Button(
            picker, text="打开文件夹", style="Ghost.TButton", command=self._open_folder
        )
        self.btn_open.grid(row=0, column=2, padx=(6, 0))
        self.btn_delete = ttk.Button(
            picker, text="删除", style="Ghost.TButton", command=self._delete_motion
        )
        self.btn_delete.grid(row=0, column=3, padx=(6, 0))

        # ---- 参数 + 回放按钮（同一行） ----------------------------------
        params_row = ttk.Frame(outer)
        params_row.pack(fill="x", pady=(10, 0))
        params_row.columnconfigure(1, weight=1)  # 中间留弹性空隙，把按钮推到右边

        spin_width = 3 if self._compact else 4
        soft_label = "靠近" if self._compact else "出发前靠近"

        params_left = ttk.Frame(params_row)
        params_left.grid(row=0, column=0, sticky="w")

        ttk.Label(params_left, text="次数").pack(side="left")
        self.spin_repeat = ttk.Spinbox(params_left, from_=1, to=999, width=spin_width)
        self.spin_repeat.set("1")
        self.spin_repeat.pack(side="left", padx=(6, 0))

        ttk.Label(params_left, text="速度").pack(side="left", padx=(16, 0))
        self.spin_speed = ttk.Spinbox(params_left, from_=0.2, to=3.0, increment=0.1, width=spin_width)
        self.spin_speed.set("1.0")
        self.spin_speed.pack(side="left", padx=(6, 0))
        ttk.Label(params_left, text="×", style="Muted.TLabel").pack(side="left", padx=(4, 0))

        ttk.Label(params_left, text=soft_label).pack(side="left", padx=(16, 0))
        self.spin_soft = ttk.Spinbox(params_left, from_=0.5, to=5.0, increment=0.5, width=spin_width)
        self.spin_soft.set(f"{config.REPLAY_SOFT_START_S:.1f}")
        self.spin_soft.pack(side="left", padx=(6, 0))
        ttk.Label(params_left, text="秒", style="Muted.TLabel").pack(side="left", padx=(4, 0))

        # 回放/停止 和参数同一行，右对齐（与其它行的按钮共用同一条右边界）
        actions = ttk.Frame(params_row)
        actions.grid(row=0, column=2, sticky="e")
        self.btn_play = ttk.Button(
            actions, text="▶ 执行回放", style="Primary.TButton", command=self._replay
        )
        self.btn_play.pack(side="left")
        self.btn_stop = ttk.Button(
            actions, text="■ 停止", style="Danger.TButton", command=self._stop_clicked
        )
        self.btn_stop.pack(side="left", padx=(10, 0))

        # ---- 状态 + 进度 ----------------------------------------------
        self.status = ttk.Label(outer, text="等待连接…", style="Status.TLabel", anchor="w")
        self.status.pack(fill="x", pady=(12, 0))
        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=100, value=0)
        self.progress.pack(fill="x", pady=(6, 0))

        # ---- 日志 ------------------------------------------------------
        self._section(outer, "日志")

        log_wrap = tk.Frame(outer, bg=C["border"], bd=0, highlightthickness=0)
        log_wrap.pack(fill="both", expand=True, pady=(0, 0))

        self.log = tk.Text(
            log_wrap,
            width=30,
            height=1 if self._compact else 2,
            wrap="word",
            state="normal",
            font=self.fonts.log,
            bg=C["card"],
            fg=C["text"],
            insertbackground=C["text"],
            selectbackground=C["accent_soft"],
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            spacing1=1,
            spacing3=2,
        )
        scrollbar = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)

        # 日志只读但允许选中复制：拦掉所有编辑类按键，只放行复制/全选/翻页
        self.log.bind("<Key>", self._log_keypress)

        self.log.tag_configure("time", foreground=C["faint"])
        self.log.tag_configure("info", foreground=C["text"])
        self.log.tag_configure("dim", foreground=C["muted"])
        self.log.tag_configure("ok", foreground=C["ok"])
        self.log.tag_configure("warn", foreground=C["warn"])
        self.log.tag_configure("error", foreground=C["danger"])

        # 最小窗口尺寸按内容算，日志至少留得下两行，同时不超过屏幕
        self.root.update_idletasks()
        self.root.minsize(
            min(self.root.winfo_reqwidth(), screen_width - 20),
            min(self.root.winfo_reqheight(), screen_height - 60),
        )

    def _log_keypress(self, event):
        keysym = event.keysym
        if event.state & 0x4 and keysym.lower() in ("c", "a", "insert"):
            return None  # Ctrl+C / Ctrl+A / Ctrl+Insert
        if keysym in ("Up", "Down", "Left", "Right", "Home", "End", "Prior", "Next", "Tab", "Escape"):
            return None
        return "break"  # 其余按键一律不进日志

    def _section(self, parent: tk.Misc, text: str) -> None:
        """小节标题 + 一条细分隔线；矮屏上只保留分隔线以节省高度。"""
        row = ttk.Frame(parent)
        if self._compact:
            row.pack(fill="x", pady=(10, 4))
            ttk.Separator(row, orient="horizontal").pack(fill="x")
            return

        row.pack(fill="x", pady=(12, 4))
        ttk.Label(row, text=text, style="Sect.TLabel").pack(side="left")
        ttk.Separator(row, orient="horizontal").pack(side="left", fill="x", expand=True, padx=(10, 0))

    # ==================================================================
    # 线程安全的界面更新
    # ==================================================================
    def _post(self, item) -> None:
        self.ui.put(item)

    def _log_text(self, text: str, level: str = "info") -> None:
        """往日志里追加一行（带时间戳和颜色）。只能在主线程调用。"""
        self.log.insert("end", datetime.now().strftime("%H:%M:%S") + "　", ("time",))
        self.log.insert("end", text + "\n", (level,))
        self.log.see("end")

    def _log_safe(self, text: str, level: str = "info") -> None:
        """任意线程都能用的日志。"""
        self._post(("log", (text, level)))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.ui.get_nowait()
                if kind == "log":
                    if isinstance(payload, tuple):
                        self._log_text(payload[0], payload[1])
                    else:
                        self._log_text(str(payload))
                elif kind == "status":
                    self.status.configure(text=str(payload))
                elif kind == "progress":
                    done, total = payload
                    self._progress_set(done, total)
                elif kind == "mode":
                    self._apply_mode(payload)
                elif kind == "refresh":
                    self._refresh_motions()
                elif kind == "connected":
                    self._on_connected()
                elif kind == "scan":
                    self._show_scan(*payload)
                elif kind == "error":
                    title, message = payload
                    self._log_text(f"【{title}】{message}", "error")
                    messagebox.showerror(title, message)
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    # ---- 进度条 ------------------------------------------------------
    def _progress_idle(self) -> None:
        if self._progress_mode != "determinate":
            self.progress.stop()
            self._progress_mode = "determinate"
        self.progress.configure(mode="determinate", maximum=100, value=0)

    def _progress_pulse(self) -> None:
        if self._progress_mode != "indeterminate":
            self.progress.configure(mode="indeterminate", maximum=100)
            self.progress.start(16)
            self._progress_mode = "indeterminate"

    def _progress_set(self, done: float, total: float) -> None:
        if self._progress_mode != "determinate":
            self.progress.stop()
            self._progress_mode = "determinate"
        self.progress.configure(mode="determinate", maximum=max(1.0, float(total)), value=float(done))

    # ---- 状态机 ------------------------------------------------------
    def _apply_mode(self, mode: str) -> None:
        self.mode = mode
        dot_color = C["faint"]

        if mode == "connecting":
            dot_color = C["accent"]
            text = "正在连接机器人…"
        elif mode == "scanning":
            dot_color = C["accent"]
            text = "正在扫描电机…"
        elif mode == "recording":
            dot_color = C["warn"]
            text = f"录制中 · 已录 {self._frame_hint} 帧，用手拖动机械臂做动作"
        elif mode == "replaying":
            dot_color = C["accent"]
            text = "回放中"
        elif self.arm is not None:
            dot_color = C["ok"]
            text = "就绪 · " + self.arm.summary()
        else:
            text = "未连接"

        self.dot.configure(fg=dot_color)
        self.banner.configure(text=text)

        ready = self.arm is not None and mode == "idle"
        idle = mode == "idle"

        self.btn_reconnect.configure(state="normal" if idle else "disabled")
        self.btn_record.configure(state="normal" if ready else "disabled")
        self.btn_save.configure(state="normal" if mode == "recording" else "disabled")
        self.btn_play.configure(state="normal" if ready and self.motions else "disabled")
        self.btn_stop.configure(state="normal" if mode in ("recording", "replaying") else "disabled")

        for button in (self.btn_refresh, self.btn_open, self.btn_delete):
            button.configure(state="normal" if idle else "disabled")
        for button in self.type_buttons.values():
            button.configure(state="normal" if idle else "disabled")
        self.btn_scan.configure(state="normal" if idle else "disabled")
        self.combo.configure(state="readonly" if idle else "disabled")

        if mode == "recording":
            self._progress_pulse()
        elif mode != "replaying":
            self._progress_idle()

    def _on_connected(self) -> None:
        self._apply_mode("idle")

    # ---- 机器人类型 / 扫描电机 ----------------------------------------
    def _sync_type_buttons(self) -> None:
        """选中的机型用实心蓝，其余描边。"""
        current = self.robot_type.get()
        for kind, button in self.type_buttons.items():
            button.configure(style="SegmentOn.TButton" if kind == current else "SegmentOff.TButton")

    def _choose_type(self, kind: str) -> None:
        if kind == self.robot_type.get():
            return
        self.robot_type.set(kind)
        self._sync_type_buttons()
        label = config.ROBOT_TYPE_LABELS.get(kind, kind)
        self._log_text(f"机型切换为「{label}」，重新连接中 …")
        self._reconnect()

    def _scan_clicked(self) -> None:
        if self.mode != "idle" or (self.worker is not None and self.worker.is_alive()):
            return
        robot_type = self.robot_type.get()
        forced = self.forced_channels
        self._apply_mode("scanning")
        if not self._start(lambda: self._worker_scan(forced, robot_type)):
            self._apply_mode("idle")

    def _worker_scan(self, forced, robot_type: str) -> None:
        label = config.ROBOT_TYPE_LABELS.get(robot_type, robot_type)
        was_connected = self.arm is not None
        try:
            # Windows 的 USB-CAN 是**独占句柄**：还连着的时候再枚举，
            # 正在用的那条总线会被 candle 库当成"不可用"漏掉（表现为
            # 第二次扫描左臂"消失"、扫描失败）。所以扫描前必须先断开。
            if self.arm is not None:
                self._log_safe("扫描前先断开当前连接（USB-CAN 是独占句柄）…")
                try:
                    self.arm.close()
                except Exception:  # noqa: BLE001
                    pass
                self.arm = None
                self.last_setup = None
                self._log_safe("已断开", "ok")

            self._log_safe(f"扫描电机（机型：{label}）…")
            setup = detect.scan(
                forced=forced, expect=robot_type, interface=self.interface, on_progress=self._log_safe
            )
            self._log_safe(setup.describe(), "warn" if setup.missing else "ok")

            # 把所有**没有电机**的 CAN 口挨个报一遍状态：
            # 让"哪个口开着、哪个口没插线、哪个口插了但对面没上电"一目了然。
            try:
                empty = [
                    t for t in detect.list_targets(self.interface)
                    if t.label not in setup.channels
                ]
                if empty:
                    stats = detect.sniff_many(empty, seconds=0.5)
                    self._log_safe("其它 CAN 口状态：")
                    for t in empty:
                        counts, errors = stats.get(t.label, ({}, 0))
                        if counts:
                            self._log_safe(
                                f"  {t.label}：有数据帧（{sum(counts.values())} 帧）但无电机应答",
                                "warn",
                            )
                        elif errors:
                            self._log_safe(
                                f"  {t.label}：总线报错（{errors} 个错误帧）——"
                                "电缆插着，但对面没上电 / 缺 120Ω 终端电阻",
                                "warn",
                            )
                        else:
                            self._log_safe(f"  {t.label}：静音（这个口上没插电缆）", "dim")
            except Exception:  # noqa: BLE001 - 只是附加信息，失败不影响扫描结果
                pass

            if was_connected:
                self._log_safe("扫描完成。点「重新连接」即可恢复连接。", "info")
            self._post(("scan", (setup, robot_type)))
        except Exception as exc:
            traceback.print_exc()
            self._post(("error", ("扫描失败", str(exc))))
        finally:
            self._post(("mode", "idle"))

    def _show_scan(self, setup, robot_type: str) -> None:
        """弹窗列出每个电机的在线情况。"""
        label = config.ROBOT_TYPE_LABELS.get(robot_type, robot_type)
        expected = config.names_for(robot_type)
        online_names = set(setup.motor_names)

        top = tk.Toplevel(self.root)
        top.title("电机扫描结果")
        top.transient(self.root)
        top.configure(bg=C["bg"])
        top.resizable(True, True)

        ttk.Label(top, text="电机扫描结果", style="Title.TLabel").pack(anchor="w", padx=16, pady=(14, 2))
        ttk.Label(
            top,
            text=f"机型：{label}　　在线 {len(online_names)} / {len(expected)}",
            style="Muted.TLabel",
        ).pack(anchor="w", padx=16)

        frame = tk.Frame(top, bg=C["border"])
        frame.pack(fill="both", expand=True, padx=16, pady=10)

        text = tk.Text(
            frame,
            width=46,
            height=min(18, 4 + len(expected)),
            font=self.fonts.log,
            bg=C["card"],
            fg=C["text"],
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            wrap="none",
            state="normal",
        )
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)

        text.tag_configure("ok", foreground=C["ok"])
        text.tag_configure("bad", foreground=C["danger"])
        text.tag_configure("dim", foreground=C["muted"])

        for side, side_label in (("left", "左臂"), ("right", "右臂")):
            label = setup.mapping.get(side)
            if label:
                driver = (setup.interface or {}).get(label, "")
                suffix = f"  [{driver}]" if driver and driver != "socketcan" else ""
                text.insert("end", f"{side_label} → {label}{suffix}\n", ("dim",))
        text.insert("end", "\n")

        for spec in config.MOTORS:
            if spec.name not in expected:
                continue
            channel = next(
                (ch for ch, names in setup.channels.items() if spec.name in names), "—"
            )
            online = spec.name in online_names
            tag = "ok" if online else "bad"
            text.insert("end", f"{spec.name:24s} ID {spec.can_id:<3d} {spec.model:6s} {channel:5s}", (tag,))
            text.insert("end", " 在线\n" if online else " 未响应\n", (tag,))

        text.configure(state="disabled")

        footer = ttk.Frame(top)
        footer.pack(fill="x", padx=16, pady=(0, 14))
        if setup.missing:
            ttk.Label(
                footer,
                text="⚠ 有电机没响应，检查线缆与供电",
                style="Muted.TLabel",
                foreground=C["warn"],
            ).pack(side="left")
        ttk.Button(footer, text="关闭", style="Primary.TButton", command=top.destroy).pack(side="right")

        top.grab_set()
        top.focus_set()

    # ==================================================================
    # 连接
    # ==================================================================
    def _connect(self, forced_channels: dict[str, str] | None, robot_type: str = "auto") -> None:
        try:
            self._log_safe("检查 CAN 口 …")
            channels = list(dict.fromkeys(forced_channels.values())) if forced_channels else None

            # 没配置好就自动跑一次 canup.sh（和老程序启动时的行为一致）
            ready, messages = detect.ensure_can(channels, on_progress=self._log_safe, interface=self.interface)
            for message in messages:
                self._log_safe(f"  · {message}", "warn")
            if not ready:
                self._post(
                    ("error", ("CAN 未就绪", "\n".join(messages) or "请手动执行：bash canup.sh"))
                )
                self._post(("mode", "idle"))
                return
            self._log_safe("CAN 口就绪", "ok")

            self._log_safe(f"扫描电机（机型：{config.ROBOT_TYPE_LABELS.get(robot_type, robot_type)}）…")

            def explain(exc: Exception) -> Exception:
                """把"一个电机都没扫到"落到具体是哪一层坏了。"""
                targets = detect.list_targets(self.interface)
                if not targets:
                    return exc          # 连适配器都没有，原话更有用
                detail = "，".join(target.label for target in targets)
                self._log_safe(f"已识别到 {len(targets)} 条 CAN 总线：{detail}", "info")
                sniffed = detect.sniff_many(targets, seconds=0.5)
                silent, erroring = [], []
                for t in targets:
                    counts, errors = sniffed.get(t.label, ({}, 0))
                    if counts:
                        continue
                    (erroring if errors else silent).append(t.label)
                if erroring:
                    self._log_safe(
                        "这些总线在报错（电缆插着但对面没上电/没终端电阻）："
                        + "，".join(erroring),
                        "warn",
                    )
                if silent:
                    self._log_safe(
                        "这些总线完全静音（这条口上没插电缆）：" + "，".join(silent), "warn"
                    )
                return RuntimeError(
                    f"CAN 适配器工作正常（{len(targets)} 条都能打开），但没有电机应答。\n"
                    "请确认：机器人已上电、CAN 线接好（两端 120Ω 终端电阻）、"
                    "电机 ID 与 config.py 一致。\n"
                    "更细的信息跑一次：LiteRecordReplay.exe --diagnose"
                )

            try:
                setup = detect.scan(
                    forced=forced_channels,
                    expect=robot_type,
                    interface=self.interface,
                    on_progress=self._log_safe,
                )
            except detect.DetectError as first:
                if not sys.platform.startswith("linux"):
                    # Windows 没有 canup.sh 这一套，重扫一遍没意义
                    raise explain(first) from first

                # Linux 下 USB-CAN 常常要在适配器重新上电后重新配置一次
                self._log_safe("一个电机都没扫到，重新配置 CAN 口后再试一次 …", "warn")
                _ok, output = detect.bring_up_can(on_progress=self._log_safe)
                for line in output.splitlines():
                    if line.strip():
                        self._log_safe("  " + line.strip(), "dim")
                try:
                    setup = detect.scan(
                        forced=forced_channels,
                        expect=robot_type,
                        interface=self.interface,
                        on_progress=self._log_safe,
                    )
                except detect.DetectError as exc:
                    raise explain(exc) from exc

            self._log_safe(setup.describe(), "warn" if setup.missing else "info")
            if setup.missing:
                self._log_safe(
                    f"⚠ 按「{config.ROBOT_TYPE_LABELS.get(robot_type, robot_type)}」应该有但没响应："
                    f"{', '.join(setup.missing)}",
                    "warn",
                )

            calibration, source, missing = storage.load_calibration(online_names=setup.motor_names)
            self._log_safe(f"标定文件：{source}")
            if missing:
                self._log_safe(
                    f"这些关节没有标定偏移，按 0 处理：{', '.join(missing)}", "warn"
                )

            arm = Arm(
                setup.channels,
                calibration,
                interface=setup.interface,
                options=setup.open_options,
            )
            self.arm = arm
            self.last_setup = setup
            self._log_safe("已连接 · " + arm.summary(), "ok")
            self._post(("connected", None))
        except Exception as exc:
            traceback.print_exc()
            self._log_safe("连接失败", "error")
            self._post(("error", ("连接失败", str(exc))))
            self._post(("mode", "idle"))

    def _reconnect(self) -> None:
        if self.arm is not None:
            self._log_safe("断开当前连接 …")
            self.arm.close()
            self.arm = None
        self._apply_mode("connecting")
        robot_type = self.robot_type.get()
        if not self._start(lambda: self._connect(self.forced_channels, robot_type)):
            self._apply_mode("idle")

    # ==================================================================
    # 录制
    # ==================================================================
    def _record(self) -> None:
        if self.arm is None:
            return
        label = self.entry_label.get().strip()
        self._apply_mode("recording")
        if not self._start(lambda: self._worker_record(label)):
            self._apply_mode("idle")

    def _worker_record(self, label: str) -> None:
        arm = self.arm
        try:
            self._log_safe("上电（力矩上限 %.1f Nm，可以直接用手拖）…" % config.RECORD_TORQUE_LIMIT)
            arm.begin(config.RECORD_TORQUE_LIMIT)
            self._log_safe("开始录制，拖动机械臂做动作；做完点「结束并保存」", "ok")

            motion, error = motion_mod.record(
                arm, label=label, stop=self.stop, on_status=self._record_status
            )

            if motion.num_frames >= 2:
                path = storage.save(motion)
                self._log_safe(f"已保存 {path.name} · {motion.describe()}", "ok")
                self._post(("refresh", None))
            else:
                self._log_safe("录制太短（不足 2 帧），没有保存", "warn")

            if error is not None:
                self._log_safe(f"录制提前结束：{error}", "warn")
        except Exception as exc:
            traceback.print_exc()
            self._post(("error", ("录制失败", str(exc))))
        finally:
            try:
                arm.end()
                self._log_safe("已松手（电机失能）")
            except Exception as exc:
                self._log_safe(f"松手时出错：{exc}", "warn")
            self._post(("status", ""))
            self._post(("mode", "idle"))

    def _record_status(self, info: dict) -> None:
        self._frame_hint = info["frames"]
        self._post(
            (
                "status",
                f"已录 {info['frames']} 帧 · {info['seconds']:.1f} 秒 · "
                f"实际 {info['hz']:.1f} Hz · 峰值力矩 {info['torque']:.1f} Nm",
            )
        )

    # ==================================================================
    # 回放
    # ==================================================================
    def _selected_motion(self) -> Path | None:
        index = self.combo.current()
        if index < 0 or index >= len(self.motions):
            messagebox.showwarning("没有选择动作", "请先在下拉框里选一个动作")
            return None
        return self.motions[index]

    def _replay(self) -> None:
        if self.arm is None:
            return
        path = self._selected_motion()
        if path is None:
            return
        try:
            repeat = max(1, int(float(self.spin_repeat.get())))
            speed = max(0.1, float(self.spin_speed.get()))
            soft = max(0.0, float(self.spin_soft.get()))
        except ValueError:
            messagebox.showwarning("参数不对", "次数 / 速度 / 靠近时间必须是数字")
            return

        self._apply_mode("replaying")
        if not self._start(lambda: self._worker_replay(path, repeat, speed, soft)):
            self._apply_mode("idle")

    def _worker_replay(self, path: Path, repeat: int, speed: float, soft: float) -> None:
        arm = self.arm
        try:
            motion = storage.load(path)
            self._log_safe(f"回放 {motion.describe()}　{repeat} 次　速度 {speed:g}×")

            arm.begin(None)  # 用各型号自己的力矩上限
            self._log_safe("上电完成，开始回放", "ok")

            result = motion_mod.replay(
                arm,
                motion,
                repeat=repeat,
                speed=speed,
                soft_start=soft,
                stop=self.stop,
                on_status=self._replay_status,
            )

            for note in result.get("notes") or []:
                self._log_safe(f"  · {note}", "warn")
            self._log_safe(
                f"回放结束 · 完成 {result['completed']}/{repeat} 次 · "
                f"{result['frames']} 帧 · 实测 {result['actual_hz']:.1f} Hz · "
                f"峰值力矩 {result['peak_torque']:.1f} Nm",
                "ok",
            )
        except Exception as exc:
            traceback.print_exc()
            self._post(("error", ("回放中止", str(exc))))
        finally:
            try:
                arm.end()
                self._log_safe("已松手（电机失能）")
            except Exception as exc:
                self._log_safe(f"松手时出错：{exc}", "warn")
            self._post(("status", ""))
            self._post(("mode", "idle"))

    def _replay_status(self, info: dict) -> None:
        done = info["frame"]
        total = info["of_frames"]
        self._post(("progress", (done, total)))
        percent = 100.0 * done / total if total else 0.0
        self._post(
            (
                "status",
                f"第 {info['loop']}/{info['of_loops']} 次 · {done}/{total} 帧（{percent:.0f}%）"
                f" · 峰值力矩 {info['torque']:.1f} Nm",
            )
        )

    # ==================================================================
    # 动作列表
    # ==================================================================
    def _refresh_motions(self) -> None:
        self.motions = storage.listing()
        values = [storage.display_name(path, compact=True) for path in self.motions]
        previous = self.combo.current()

        self.combo.configure(values=values)
        if values:
            self.combo.current(min(previous if previous >= 0 else 0, len(values) - 1))
        else:
            self.combo.set("（还没有录制，点「开始录制」录一段）")

        self.btn_play.configure(
            state="normal" if self.arm is not None and self.motions and self.mode == "idle" else "disabled"
        )

        if not self.motions:
            self._log_text("还没有录制。点「开始录制」录一段；旧录制可用 python3 main.py --import-legacy 导入。", "dim")
            self._offer_import()

    def _offer_import(self) -> None:
        if self._offered_import:
            return
        self._offered_import = True
        directories = storage.default_legacy_dirs()
        if not directories:
            return
        listing = "\n".join(f"  · {path}" for path in directories)
        if messagebox.askyesno(
            "发现旧录制",
            f"在程序旁边发现了旧版本的录制文件：\n{listing}\n\n要导入到新的动作列表里吗？",
        ):
            self._do_import(directories[0])

    def _do_import(self, directory: Path) -> None:
        self._log_text(f"正在导入 {directory} …")
        self.root.update_idletasks()
        saved, problems = storage.import_legacy_dir(directory)
        self._log_text(f"导入完成 · 成功 {len(saved)} 个 · 跳过 {len(problems)} 个", "ok")
        for problem in problems:
            self._log_text(f"  · 跳过 {problem}", "warn")
        self._refresh_motions()

    def _delete_motion(self) -> None:
        path = self._selected_motion()
        if path is None:
            return
        if messagebox.askyesno("删除录制", f"确定删除这个动作吗？\n{path.name}"):
            storage.delete(path)
            self._log_text(f"已删除 {path.name}", "warn")
            self._refresh_motions()

    def _open_folder(self) -> None:
        """在文件管理器里打开录制目录。"""
        folder = storage.MOTIONS_DIR
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(str(folder))          # noqa: S606  资源管理器打开
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._log_text(f"录制目录：{folder}")
        except Exception as exc:
            self._log_text(f"打不开文件管理器（{exc}），路径：{folder}", "warn")

    # ==================================================================
    # 停止 / 退出
    # ==================================================================
    def _stop_clicked(self) -> None:
        if self.mode not in ("recording", "replaying", "connecting"):
            return
        self.stop.set()
        self._log_text("正在停止（会先阻尼再松手，约 0.3 秒）…", "warn")

    def _start(self, target) -> bool:
        if self.worker is not None and self.worker.is_alive():
            self._log_text("上一个动作还没结束，请稍等", "warn")
            return False
        self.stop.clear()
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()
        return True

    def _on_close(self) -> None:
        if self.mode in ("recording", "replaying"):
            if not messagebox.askokcancel("退出", "动作还在执行，退出前会先松手。确定退出吗？"):
                return
        self.root.config(cursor="watch")
        self.root.update_idletasks()

        self.stop.set()
        if self.worker is not None and self.worker.is_alive():
            self.worker.join(timeout=20)
        if self.arm is not None:
            self.arm.close()
        self.root.destroy()


# ======================================================================
# 命令行
# ======================================================================
def diagnose_report() -> str:
    """把 CAN 相关的环境一次说清楚，方便排障。返回报告正文。"""
    import platform
    import struct

    lines: list[str] = []

    def put(text: str = "") -> None:
        lines.append(text)

    put(f"Lite 动作录制 / 回放 {display_version()}")
    put(f"平台      : {platform.platform()}（{platform.machine()}，{struct.calcsize('P') * 8} bit）")
    put(f"Python    : {platform.python_version()}  {sys.executable}")
    put(f"运行方式  : {'打包 exe' if getattr(sys, 'frozen', False) else '源码'}")
    put(f"程序目录  : {ROOT_DIR}")
    put()
    put("== 依赖 ==")
    for module in ("numpy", "can", "robstride_dynamics", "candle_api", "candle", "usb", "gs_usb", "libusb_package"):
        try:
            imported = __import__(module)
            version = getattr(imported, "__version__", "")
            put(f"  [ok]   {module} {version}".rstrip())
        except Exception as exc:  # noqa: BLE001
            put(f"  [--]   {module}: {exc}")
    put(f"  [..]   libusb: {libusb.describe()}")
    put()
    put("== CAN 适配器 ==")

    targets: list = []
    try:
        targets = detect.list_targets()
    except Exception as exc:  # noqa: BLE001
        put(f"  枚举失败：{exc}")
    if not targets:
        put("  没有检测到（Windows 看下面的提示；Linux 先跑 bash app/canup.sh）")
    for target in targets:
        put(f"  {target.label:26s} 驱动 {target.driver:12s} channel={target.channel!r}")
    for item in detect.missing_components():
        put(f"  ! {item}")

    if targets:
        put()
        put("== 扫描电机（每条总线最多 1 秒）==")
        matched: set[str] = set()
        try:
            setup = detect.scan(timeout=0.05, on_progress=lambda text: put(f"  {text}"))
        except Exception as exc:  # noqa: BLE001
            put(f"  {exc}")
        else:
            put("  " + setup.describe().replace("\n", "\n  "))
            matched = set(setup.channels)

        # 只对"没有电机"的总线做静默监听，免得每台都多花 1.5 秒
        quiet_targets = [target for target in targets if target.label not in matched]
        if quiet_targets:
            put()
            put("== 监听这些没有应答的总线 1.5 秒（只听不说）==")
            all_counts = detect.sniff_many(quiet_targets)
            for target in quiet_targets:
                counts, errors = all_counts.get(target.label, ({}, 0))
                total = sum(counts.values())
                if errors and errors >= total:
                    # 电缆插着、但对面没有任何节点应答：适配器自己在刷错误帧
                    put(
                        f"  {target.label:26s} 总线在报错（{errors} 个错误帧，0 帧正常数据）"
                    )
                    put("      → 电缆插着，但对面没上电 / 缺 120Ω 终端电阻 / CAN_H-CAN_L 接反")
                    continue
                if not total and not errors:
                    put(f"  {target.label:26s} 一点动静都没有（这条口上没插电缆？）")
                    continue
                if errors:
                    put(f"  {target.label:26s} {total} 帧数据 + {errors} 个错误帧")
                else:
                    put(f"  {target.label:26s} {total} 帧数据")
                for arb, count in sorted(counts.items(), key=lambda kv: -kv[1])[:6]:
                    put(
                        f"      id=0x{arb:08x} ×{count:<4d}"
                        f" 通信类型={(arb >> 24) & 0x1F:2d} 电机ID≈{(arb >> 8) & 0xFF}"
                    )
    return "\n".join(lines) + "\n"


def run_can_scan_worker() -> int:
    """`--can-scan-worker`（隐藏参数）：被自己以子进程方式调用。

    窗口程序（PyInstaller --windowed）里 sys.stdin/stdout 是 None，
    但子进程的 stdin/stdout 是我们用管道接好的，这里手动把它们接回标准流，
    然后把活转给 can_scan.main_worker()。
    """
    import io

    try:
        if sys.stdin is None:
            sys.stdin = io.TextIOWrapper(os.fdopen(0, "rb"), encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = io.TextIOWrapper(os.fdopen(1, "wb"), encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = io.TextIOWrapper(os.fdopen(2, "wb"), encoding="utf-8")
    except OSError:
        pass

    import can_scan

    return can_scan.main_worker()


def run_diagnose() -> int:
    """`--diagnose`：打印报告并写到程序目录的 can-diagnose.txt。

    打包成 exe 后是窗口程序、没有控制台，所以报告同时落一份文件，
    出问题时把这个文件发出来就行。
    """
    try:
        report = diagnose_report()
    except Exception:  # noqa: BLE001
        report = "诊断失败：\n" + traceback.format_exc()
    try:
        print(report)
    except Exception:  # noqa: BLE001 - 窗口程序里 stdout 可能是空的
        pass
    target = Path(ROOT_DIR) / "can-diagnose.txt"
    try:
        target.write_text(report, encoding="utf-8")
    except OSError:
        pass
    else:
        try:
            print(f"报告已写入 {target}")
        except Exception:  # noqa: BLE001
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Prime Lite 双臂机器人 动作录制/回放")
    parser.add_argument("--channels", "-c", default="", help="手动指定左右臂 CAN 口，例如 can2,can1（默认自动识别）")
    parser.add_argument("--import-legacy", default="", metavar="DIR", help="把旧版 .npy 录制批量导入后退出")
    parser.add_argument("--list", action="store_true", help="列出现有录制后退出")
    parser.add_argument("--diagnose", action="store_true", help="打印 CAN 环境诊断后退出（结果同时写入 can-diagnose.txt）")
    parser.add_argument("--version", action="version", version=f"Prime Lite 动作录制/回放 v{display_version()}")
    parser.add_argument(
        "--interface",
        default="auto",
        help="CAN 驱动：Linux 默认 socketcan；Windows 可填 pcan / canalystii / slcan / gs_usb 等（默认自动探测）",
    )
    parser.add_argument(
        "--robot",
        "-r",
        default="auto",
        choices=config.ROBOT_TYPES,
        help="机器人类型：auto 自动识别（默认）/ gripper 有夹爪 / no_gripper 无夹爪",
    )
    parser.add_argument(
        "--can-scan-worker",
        action="store_true",
        help=argparse.SUPPRESS,   # 子进程入口：碰 candle 硬件的操作在子进程里做，卡死可杀
    )
    args = parser.parse_args()

    if args.can_scan_worker:
        return run_can_scan_worker()

    if args.diagnose:
        # 诊断也会去开设备，同样受单实例限制，免得和正在跑的 GUI 抢棒子
        if not acquire_single_instance():
            print("已经有一个 LiteRecordReplay 在运行，先关掉它再诊断（USB-CAN 是独占设备）")
            return 2
        return run_diagnose()

    if args.import_legacy:
        saved, problems = storage.import_legacy_dir(Path(args.import_legacy))
        print(f"导入 {len(saved)} 个，跳过 {len(problems)} 个")
        for problem in problems:
            print(f"  跳过 {problem}")
        return 0

    if args.list:
        motions = storage.listing()
        if not motions:
            print("还没有录制")
        for path in motions:
            print(f"{path.name:32s} {storage.display_name(path)}")
        return 0

    forced = detect.parse_channels(args.channels) if args.channels else None

    if not acquire_single_instance():
        # 两个实例同时抢 USB-CAN 会把 candle 原生库卡死（进程杀不掉、设备一直被占）
        try:
            _enable_dpi_awareness()
            _warn = tk.Tk()
            _warn.withdraw()
            messagebox.showwarning(
                "已经有实例在运行",
                "LiteRecordReplay 已经打开了一个窗口。\n\n"
                "USB-CAN 适配器是独占设备，两个实例同时开会把设备卡死，"
                "需要拔插适配器才能恢复。请先关掉原来那个窗口。",
            )
            _warn.destroy()
        except Exception:  # noqa: BLE001
            print("已经有一个实例在运行，请先关掉它。", file=sys.stderr)
        return 2

    _enable_dpi_awareness()   # 必须在 tk.Tk() 之前
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"无法打开窗口（{exc}）。请在有图形界面的终端里运行。", file=sys.stderr)
        return 1

    App(root, forced, args.robot, args.interface)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
