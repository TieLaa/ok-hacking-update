"""训练倍率纪录：跨局长期保存，破纪录时存一张图。

用户口径（2026-11-24）：
- 只记训练动画的倍率（`NNN% Bonus`）；
- 门槛 500%：`v > 500` 才算候选；
- **破上一次纪录**才记一条：`v > best`（两者合成一条判据，best 初始 = 500）；
- 数字始终保存；`自动存图` 开关只决定要不要同时存截图；
- 目录 `newrecord/`（项目根，和 `logs/` 平级），不参与按局清理；
- 读不准的数字不猜（宁可漏记，不把错数写进长期纪录）。

文件结构：

    newrecord/boost_records.json        {"version":1,"best":560,"records":[...]}
    newrecord/boost_560_1124-105842.jpg
"""

import json
import os
import time

from src.tasks.evidence import write_jpg
from src.tasks.training.constants import PROJECT_ROOT

FOLDER = os.path.join(PROJECT_ROOT, "newrecord")
RECORD_PATH = os.path.join(FOLDER, "boost_records.json")
#: 用户口径：门槛 500%，best 初始就是它
FLOOR = 500


def _empty():
    return {"version": 1, "best": FLOOR, "records": []}


def load(path=None):
    """读纪录文件；损坏时改名保留再重新开始（绝不静默覆盖坏文件）。"""
    path = path or RECORD_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("boost_records.json 不是对象")
        data.setdefault("records", [])
        data["best"] = max(int(data.get("best") or FLOOR), FLOOR)
        return data
    except FileNotFoundError:
        return _empty()
    except Exception:                                # noqa: BLE001
        try:
            os.replace(path, f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
        except OSError:
            pass
        return _empty()


def _write(data, path):
    """原子写：临时文件 + os.replace，进程被杀不会留半个 JSON。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def record(task, value, frame=None, captain="", day="", folder=None, save_image=True):
    """破纪录就记数字；截图由 `save_image` 控制（开关只管图，不管数字）。

    返回截图文件名（没存图返回空串）；没破（或 ≤500）返回 None。
    """
    if value is None or int(value) <= FLOOR:
        return None
    folder = folder or FOLDER
    path = os.path.join(folder, os.path.basename(RECORD_PATH))
    data = load(path)
    value = int(value)
    if value <= int(data.get("best") or FLOOR):
        return None
    stamp = time.strftime("%m%d-%H%M%S")
    name = f"boost_{value}_{stamp}.jpg" if save_image else ""
    try:
        os.makedirs(folder, exist_ok=True)
        if name:
            try:
                if frame is None or not write_jpg(os.path.join(folder, name), frame):
                    raise OSError("写截图失败")
            except Exception as e:                   # noqa: BLE001
                task.log_warning(f"倍率纪录截图失败（{type(e).__name__}: {e}）→ 只记数字")
                name = ""
        data["best"] = value
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        data["records"].append({
            "value": value,
            "at": data["updated_at"],
            "captain": captain or "",
            "day": day or "",
            "image": name,
        })
        _write(data, path)
    except Exception as e:                           # noqa: BLE001
        task.log_warning(f"倍率纪录保存失败（{type(e).__name__}: {e}）")
        return None
    return name
