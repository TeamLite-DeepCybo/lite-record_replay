"""录制文件的存取，以及标定文件的读写。

录制文件是 npz，自带元数据（关节名、时间戳、标签、时长），所以：
- 换一台机器人、多/少一个夹爪，回放时能按关节名对齐；
- 想按真实速度重放，时间戳就在文件里；
- 文件名就是创建时间，不会覆盖旧的。

旧版本的 recorded_motion_record_N.npy 也能直接读进来（见 import_legacy）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np

from . import config
from .motion import Motion
from .util import pretty, stamp

ROOT = Path(__file__).resolve().parent.parent
MOTIONS_DIR = ROOT / "data" / "motions"

FORMAT = "bhl-motion-1"


# ======================================================================
# 录制文件
# ======================================================================
def save(motion: Motion, path: Path | str | None = None) -> Path:
    """保存一段录制（先写临时文件再改名，中途被杀不会留下坏文件）。"""
    if motion.num_frames < 2:
        raise ValueError("录制不足 2 帧，没有保存价值")

    target = Path(path) if path else MOTIONS_DIR / f"{stamp()}.npz"
    target.parent.mkdir(parents=True, exist_ok=True)

    meta = dict(motion.meta)
    meta.update(
        {
            "format": FORMAT,
            "joint_names": motion.joint_names,
            "frames": motion.num_frames,
            "joints": motion.num_joints,
            "duration": round(motion.duration, 4),
        }
    )
    # 导入的旧录制自带原始时间，不要被保存时间覆盖掉
    meta.setdefault("created", pretty(datetime.now()))

    temporary = target.with_suffix(".tmp.npz")
    np.savez_compressed(
        temporary,
        positions=np.asarray(motion.positions, dtype=np.float64),
        velocities=np.asarray(motion.velocities, dtype=np.float64),
        timestamps=np.asarray(motion.timestamps, dtype=np.float64),
        meta=json.dumps(meta, ensure_ascii=False),
    )
    os.replace(temporary, target)
    return target


def load(path: Path | str) -> Motion:
    """读一段录制；.npy 老文件会自动按旧格式解析。"""
    path = Path(path)
    if path.suffix.lower() == ".npy":
        return import_legacy(path)

    with np.load(path, allow_pickle=False) as data:
        meta = json.loads(str(data["meta"]))
        motion = Motion(
            positions=np.asarray(data["positions"], dtype=np.float64),
            velocities=np.asarray(data["velocities"], dtype=np.float64),
            timestamps=np.asarray(data["timestamps"], dtype=np.float64),
            joint_names=list(meta.get("joint_names") or []),
            meta=meta,
        )

    if not motion.joint_names:
        raise ValueError(f"{path.name} 里没有关节名信息，无法确定每一列是哪个关节")
    return motion


def listing() -> list[Path]:
    """按录制时间倒序列出（新录的在最上面）。"""
    if not MOTIONS_DIR.exists():
        return []

    def sort_key(path: Path):
        return (summary(path).get("created") or "", path.stat().st_mtime)

    return sorted(MOTIONS_DIR.glob("*.npz"), key=sort_key, reverse=True)


def summary(path: Path | str) -> dict:
    """只读元数据（不解压轨迹），用于界面列表。"""
    path = Path(path)
    try:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
    except Exception:
        meta = {}
    meta.setdefault("frames", 0)
    meta.setdefault("joints", 0)
    meta.setdefault("duration", 0.0)
    meta.setdefault("label", "")
    meta.setdefault("created", pretty(path.stat().st_mtime))
    return meta


def display_name(path: Path | str, compact: bool = False) -> str:
    """界面上显示的一行说明。

    compact=True 用于下拉框：省掉年份、缩短分隔符，
    并且当标签就是文件名（旧录制导入的）时不再重复显示。
    """
    path = Path(path)
    meta = summary(path)

    label = str(meta.get("label") or "").strip()
    if label == path.stem:
        label = ""

    created = str(meta.get("created") or "")
    if compact:
        created = created[5:]  # 去掉年份 2026-08-22 15:56:39 -> 08-22 15:56:39
        separator = " · "
    else:
        separator = "    "

    text = f"{created}{separator}{meta['duration']:.1f}s{separator}{meta['joints']} 关节"
    if label:
        text += f"{separator}{label}"
    return text


def delete(path: Path | str) -> None:
    Path(path).unlink(missing_ok=True)


# ======================================================================
# 旧格式（recorded_motion_record_N.npy）导入
# ======================================================================
def import_legacy(path: Path | str) -> Motion:
    """把老版本的 .npy 录制转换成 Motion。

    老文件只有 (帧数, 2, 关节数)，没有时间戳和关节名，所以：
    - 时间戳按原来的 50Hz 补出来；
    - 关节名优先读同名的 .joints.json（新一点的版本会存），
      否则按列数推断是"含夹爪 16 关节"还是"无夹爪 14 关节"。
    """
    path = Path(path)
    array = np.load(path, allow_pickle=False)
    if array.ndim != 3 or array.shape[1] != 2:
        raise ValueError(f"{path.name} 形状是 {array.shape}，不是预期的 (帧数, 2, 关节数)")

    positions = np.asarray(array[:, 0, :], dtype=np.float64)
    velocities = np.asarray(array[:, 1, :], dtype=np.float64)
    columns = positions.shape[1]

    sidecar = path.with_suffix(".joints.json")
    if sidecar.exists():
        names = json.loads(sidecar.read_text(encoding="utf-8"))
    elif columns == len(config.all_names()):
        names = config.all_names()
    elif columns == len(config.names_without_gripper()):
        names = config.names_without_gripper()
    else:
        raise ValueError(
            f"{path.name} 有 {columns} 列，和已知的 "
            f"{len(config.names_without_gripper())} 关节 / {len(config.all_names())} 关节配置都对不上，"
            f"无法确定关节顺序，已跳过"
        )

    if len(names) != columns:
        raise ValueError(f"{path.name} 有 {columns} 列，但关节名给了 {len(names)} 个，已跳过")

    frames = positions.shape[0]
    return Motion(
        positions=positions,
        velocities=velocities,
        timestamps=np.arange(frames, dtype=np.float64) / config.RECORD_HZ,
        joint_names=list(names),
        meta={
            "label": path.stem,
            "created": pretty(path.stat().st_mtime),  # 保留原始录制时间
            "rate_hz": config.RECORD_HZ,
            "imported_from": str(path.name),
        },
    )


def import_legacy_dir(directory: Path | str) -> tuple[list[Path], list[str]]:
    """把一个目录里的旧录制全部导入。返回 (成功的文件, 失败说明)。"""
    directory = Path(directory)
    saved: list[Path] = []
    problems: list[str] = []

    candidates = sorted(directory.glob("recorded_motion_*.npy"))
    if not candidates:
        candidates = sorted(directory.glob("*.npy"))

    for item in candidates:
        try:
            motion = import_legacy(item)
            saved.append(save(motion, MOTIONS_DIR / f"{item.stem}.npz"))
        except Exception as exc:
            problems.append(f"{item.name}: {exc}")

    return saved, problems


def default_legacy_dirs() -> list[Path]:
    """常见的旧录制目录（就在隔壁），用于首次启动时提示导入。"""
    parent = ROOT.parent
    names = ("have_gripper/arm", "no_gripper/arm", "no_gripper_repeat/arm", "have_gripper_auto")
    found = []
    for name in names:
        candidate = parent / name
        if candidate.is_dir() and list(candidate.glob("recorded_motion_*.npy")):
            found.append(candidate)
    return found


# ======================================================================
# 标定
# ======================================================================
def load_calibration(online_names=None):
    """读标定文件，返回 (给 RobstrideBus 用的标定字典, 文件名, 缺 offset 的关节)。

    关节方向以 config.py 为准，这里只取 home 偏移量，
    避免"方向在三个地方各写一遍"的老问题。
    """
    path = ROOT / config.CALIBRATION_FILE
    if not path.exists():
        for fallback in config.CALIBRATION_FALLBACKS:
            candidate = ROOT / fallback
            if candidate.exists():
                path = candidate
                break
    if not path.exists():
        raise FileNotFoundError(
            f"没有找到标定文件 {config.CALIBRATION_FILE}。\n"
            f"新机器请先运行：python3 calibrate.py"
        )

    raw = json.loads(path.read_text(encoding="utf-8"))
    calibration: dict[str, dict] = {}
    missing: list[str] = []

    for name, spec in config.MOTORS_BY_NAME.items():
        if online_names is not None and name not in online_names:
            continue
        entry = raw.get(name) or {}
        if "homing_offset" not in entry:
            missing.append(name)
        calibration[name] = {
            "id": spec.can_id,
            "direction": spec.direction,
            "homing_offset": float(entry.get("homing_offset", 0.0)),
        }

    return calibration, path.name, missing


def save_calibration(offsets: dict[str, float]) -> Path:
    """保存标定偏移量。方向不存这里（以 config.py 为准），避免两处不同步。"""
    data = {}
    for name, value in offsets.items():
        data[name] = {
            "id": config.MOTORS_BY_NAME[name].can_id,
            "direction": config.MOTORS_BY_NAME[name].direction,
            "homing_offset": float(value),
        }

    path = ROOT / config.CALIBRATION_FILE
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)
    return path
