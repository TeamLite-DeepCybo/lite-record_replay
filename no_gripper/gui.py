import sys, os

# Put the arm/ directory on sys.path so _entry_point_init can be found
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ARM_DIR = os.path.join(_BASE_DIR, "arm")
if _ARM_DIR not in sys.path:
    sys.path.insert(0, _ARM_DIR)

import _entry_point_init  # noqa: F401 -- macOS shim: must be FIRST import

import tkinter as tk
from tkinter import messagebox
import subprocess
import threading

current_path = os.path.abspath(__file__)
BASE_DIR = os.path.dirname(current_path)
ARM_DIR = os.path.join(BASE_DIR, "arm")

# Use the interpreter that is running this GUI, not whatever "python3" happens
# to be first on PATH — otherwise the child processes may land in a different
# environment that doesn't have robstride_dynamics installed.
PYTHON = sys.executable

record_process = None  # 用于保存录制进程


def next_action_number():
    """下一个可用的动作编号 = 已有文件里最大的编号 + 1。

    不能用内存里的计数器：GUI 每次启动都从 1 开始，重开一次就会把上次录的
    record_1 覆盖掉。改成看目录里实际有哪些文件。
    """
    import re

    pattern = re.compile(r"^recorded_motion_record_(\d+)\.npy$")
    used = []
    try:
        for name in os.listdir(ARM_DIR):
            m = pattern.match(name)
            if m:
                used.append(int(m.group(1)))
    except OSError:
        pass
    return max(used, default=0) + 1


action_counter = next_action_number()  # 动作计数器

def run_command(cmd, cwd=None):
    try:
        return subprocess.run(cmd, shell=True, cwd=cwd)
    except Exception as e:
        print(f"Error: {e}")
        return None

def run_in_thread(cmd, cwd=None):
    thread = threading.Thread(target=run_command, args=(cmd, cwd), daemon=True)
    thread.start()

# 初始化
def initialize():
    # canup.sh 用的是 Linux 的 iproute2（ip link），macOS 上不存在，
    # 而且里面的 sudo 会卡住等待密码输入，所以 macOS 上跳过。
    if sys.platform != "darwin":
        print("Running canup.sh...")
        run_command("bash canup.sh", cwd=BASE_DIR)

    print("Discovering actuators...")
    # 一次扫完：macOS 上 discover_actuators.py 会先按电机 ID 自动识别左右臂
    # 再扫描，不依赖 can0/can1 或适配器序列号。Linux 上仍回落到 can0/can1。
    run_command(f'"{PYTHON}" discover_actuators.py', cwd=BASE_DIR)

# ======================
# 录制与回放逻辑
# ======================

def start_recording():
    global record_process, action_counter
    if record_process is None:
        # 使用当前计数器作为文件名的一部分
        action_name = f"record_{action_counter}"
        print(f"开始录制动作: {action_name}...")

        # Popen 启动异步进程
        record_process = subprocess.Popen(
            f'"{PYTHON}" record_motion.py -n {action_name}',
            shell=True,
            cwd=ARM_DIR
        )
        # 禁用开始按钮，防止重复点击
        btn_start.config(state=tk.DISABLED)
    else:
        print("录制已经在运行中！")

def stop_recording():
    global record_process, action_counter

    # Double-click on "结束录制" used to write the flag and then run the whole
    # success path again, reporting a recording that was never made.
    if record_process is None:
        print("当前没有正在进行的录制。")
        return

    stop_flag = "/tmp/stop_record.flag"
    with open(stop_flag, "w") as f:
        f.write("stop")

    # 等录制进程真正退出——.npy 是在它退出前才写盘的，
    # 不等的话立刻点回放会找不到文件。
    proc = record_process
    record_process = None
    if proc is not None:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if proc is not None and proc.returncode not in (0, None):
        messagebox.showerror("保存失败", f"录制进程异常退出 (code {proc.returncode})，请查看终端输出。")
        btn_start.config(state=tk.NORMAL)
        return

    print(f"--- 停止录制成功！动作已保存为: 动作 {recorded_id} ---")
    messagebox.showinfo("保存成功", f"动作已存储为：动作 {recorded_id}")

    # 动作编号递增，并更新回放输入框的默认值
    recorded_id = action_counter
    action_counter = next_action_number()
    entry_replay_id.delete(0, tk.END)
    entry_replay_id.insert(0, str(recorded_id))

    # 恢复开始按钮
    btn_start.config(state=tk.NORMAL)

def replay():
    # 从输入框获取想要回放的动作编号
    target_id = entry_replay_id.get()
    if not target_id.isdigit():
        messagebox.showwarning("错误", "请输入有效的数字编号")
        return

    action_name = f"record_{target_id}"
    print(f"正在回放动作: {action_name}...")
    run_in_thread(f'"{PYTHON}" replay_motion.py -n {action_name}', cwd=ARM_DIR)

# ======================
# GUI 布局
# ======================

root = tk.Tk()
root.title("Robot Arm Control (v2)")
root.geometry("350x300")

# 录制控制区
frame_record = tk.LabelFrame(root, text="录制控制", padx=10, pady=10)
frame_record.pack(pady=10, fill="x", padx=20)

btn_start = tk.Button(frame_record, text="开始录制", command=start_recording, bg="#d4edda", height=1, width=15)
btn_start.pack(pady=5)

btn_stop = tk.Button(frame_record, text="结束录制", command=stop_recording, bg="#f8d7da", height=1, width=15)
btn_stop.pack(pady=5)

# 回放控制区
frame_replay = tk.LabelFrame(root, text="回放控制", padx=10, pady=10)
frame_replay.pack(pady=10, fill="x", padx=20)

# 编号选择
row_id = tk.Frame(frame_replay)
row_id.pack()
tk.Label(row_id, text="回放动作编号:").pack(side=tk.LEFT)
entry_replay_id = tk.Entry(row_id, width=5)
entry_replay_id.insert(0, "1")  # 默认显示1
entry_replay_id.pack(side=tk.LEFT, padx=5)

btn_replay = tk.Button(frame_replay, text="执行回放", command=replay, bg="#cce5ff", height=1, width=15)
btn_replay.pack(pady=10)

# 启动初始化线程
threading.Thread(target=initialize, daemon=True).start()

root.mainloop()
