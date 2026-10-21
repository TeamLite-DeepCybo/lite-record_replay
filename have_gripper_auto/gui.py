import tkinter as tk
from tkinter import messagebox
import subprocess
import threading
import os

current_path = os.path.abspath(__file__)
BASE_DIR = os.path.dirname(current_path)

record_process = None  # 用于保存录制进程
action_counter = 1     # 动作计数器，从1开始

def run_command(cmd, cwd=None):
    try:
        subprocess.run(cmd, shell=True, cwd=cwd)
    except Exception as e:
        print(f"Error: {e}")

def run_in_thread(cmd, cwd=None):
    thread = threading.Thread(target=run_command, args=(cmd, cwd))
    thread.start()

# 初始化：起 CAN 口 + 自动检测左右臂所在通道（仅作显示，
# 各脚本启动时也会各自重新检测，所以拔插 USB 后无需改任何配置）
def initialize():
    print("Running canup.sh...")
    run_command("bash canup.sh", cwd=BASE_DIR)
    print("Detecting arm bus channels...")
    result = subprocess.run(
        "python3 bus_autodetect.py",
        shell=True, cwd=BASE_DIR,
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)
    last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if last_line.startswith("{"):
        status_var.set(f"总线检测: {last_line}")
    else:
        status_var.set("总线检测失败，请检查 CAN 连接后重启程序")

# ======================
# 录制与回放逻辑
# ======================

def start_recording():
    global record_process, action_counter
    if record_process is None:
        action_name = f"record_{action_counter}"
        print(f"开始录制动作: {action_name}...")

        record_process = subprocess.Popen(
            f"python3 record_motion.py -n {action_name}",
            shell=True,
            cwd=BASE_DIR
        )
        btn_start.config(state=tk.DISABLED)
    else:
        print("录制已经在运行中！")

def stop_recording():
    global record_process, action_counter

    stop_flag = "/tmp/stop_record.flag"
    with open(stop_flag, "w") as f:
        f.write("stop")

    if record_process:
        record_process = None

    print(f"--- 停止录制成功！动作已保存为: 动作 {action_counter} ---")
    messagebox.showinfo("保存成功", f"动作已存储为：动作 {action_counter}")

    action_counter += 1
    entry_replay_id.delete(0, tk.END)
    entry_replay_id.insert(0, str(action_counter - 1))

    btn_start.config(state=tk.NORMAL)

def replay():
    target_id = entry_replay_id.get()
    if not target_id.isdigit():
        messagebox.showwarning("错误", "请输入有效的数字编号")
        return

    action_name = f"record_{target_id}"
    print(f"正在回放动作: {action_name}...")
    run_in_thread(f"python3 replay_motion.py -n {action_name}", cwd=BASE_DIR)

# ======================
# GUI 布局
# ======================

root = tk.Tk()
root.title("Robot Arm Control (auto-bus)")
root.geometry("380x340")

status_var = tk.StringVar(value="正在初始化 CAN 总线...")
tk.Label(root, textvariable=status_var, fg="#555").pack(pady=5)

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

row_id = tk.Frame(frame_replay)
row_id.pack()
tk.Label(row_id, text="回放动作编号:").pack(side=tk.LEFT)
entry_replay_id = tk.Entry(row_id, width=5)
entry_replay_id.insert(0, "1")
entry_replay_id.pack(side=tk.LEFT, padx=5)

btn_replay = tk.Button(frame_replay, text="执行回放", command=replay, bg="#cce5ff", height=1, width=15)
btn_replay.pack(pady=10)

# 启动初始化线程
threading.Thread(target=initialize, daemon=True).start()

root.mainloop()
