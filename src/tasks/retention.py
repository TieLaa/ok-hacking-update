"""日志保留策略的**唯一实现**：哪些算过期、怎么删，全在这里。

为什么单独一层（2026-11-10 用户口径：「你把这些清理挂在哪里了？归集到一起行不行」）：

    之前有两套：
      - 运行时 `EvidenceSink.prune()` 自己删局目录、裁 clicks、删按天日志、删连接记录；
      - 运维 `tools/prune_logs.py` 另写一套 `collect*`（平铺老目录、mcp run、总量兜底）。
    两套各自演化必然漂移 —— 改一条保留规则要记得改两处。

现在的分工：

    本模块：算「哪些算过期」+ 执行删除（`plan` → `apply`）
    `EvidenceSink.prune()`：每局开头调 `plan(kinds=RUNTIME)`，只要运行时该管的四类
    `tools/prune_logs.py`：`plan(kinds=ALL)` + `--apply`，多管平铺老目录/run 目录/总量

预算（`kinds`）按用途分组，谁该管什么一目了然：

    round       整局存证目录（logs/<局ID>/ 与 logs/mcp/<run_id>/<局ID>/）
    clicks      logs/clicks.jsonl 裁行（文件永不删）
    daily       ok-script.YYYY-MM-DD.log 整天文件
    connection  logs/mcp/connections/<pid>-<port>.json（进程已死的）
    legacy      2026-11-10 之前的平铺目录 logs/{events,unknown,unmark,debug}/
    mcp_run     logs/mcp/<run_id>/ 里第三次以后的 run.log / status.json
    cap         总量上限兜底（最老的截图先删）

安全阀写在各函数里，共同原则：
    1. 局目录必须「名字像局ID + 里面有 events|unknown|unmark|debug 之一」；
    2. 活跃文件不删：`ok-script.log`（无日期）、正在跑的进程的连接记录；
    3. 文本保命：平铺老目录只删图片，OCR 文本（txt/json）保留；
    4. 永不碰 `docs/shots/`（判据回归语料）和局目录的 `meta.json`。
"""

import glob
import json
import os
import re
import shutil
import time

from src.tasks.training.constants import PROJECT_ROOT

LOG_ROOT = os.path.join(PROJECT_ROOT, "logs")
CLICK_LOG = "clicks.jsonl"

#: 局目录名：`1110-091635[_接手][_第N局][_队长]`
ROUND_RE = re.compile(r"^\d{4}-\d{6}_")
#: 局目录里必然存在的子目录（安全阀：没有就不算局目录）
ROUND_SUBS = ("events", "unknown", "unmark", "debug")
#: 框架按天日志的文件名日期：ok-script.2026-11-03.log
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

#: 老平铺目录：(目录名, 保留天数, 是否保留非图片文件)
LEGACY = (("unknown", 3, True), ("unmark", 3, True),
          ("events", 2, False), ("debug", 3, True))

RUNTIME = ("round", "clicks", "daily", "connection")
ALL = RUNTIME + ("legacy", "mcp_run", "cap")


# ---------- 通用 ----------

def _act(kind, path, size, reason, mode, **extra):
    item = {"kind": kind, "path": path, "size": int(size),
            "reason": reason, "mode": mode}
    item.update(extra)
    return item


def _size_of(path):
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def total_size(log_dir=None):
    """整个 logs/ 的体积（打印/门禁用）。"""
    return _size_of(log_dir or LOG_ROOT)


def _ages(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return time.time()


# ---------- round：整局存证目录 ----------

def round_parents(log_dir):
    """局目录挂在两处：`logs/`（GUI）和 `logs/mcp/<run_id>/`（MCP）。"""
    parents = [log_dir]
    mcp = os.path.join(log_dir, "mcp")
    try:
        for name in os.listdir(mcp):
            sub = os.path.join(mcp, name)
            if os.path.isdir(sub) and name not in ("connections", "observations"):
                parents.append(sub)
    except OSError:
        pass
    return parents


def is_round_dir(path):
    return (os.path.isdir(path) and ROUND_RE.match(os.path.basename(path))
            and any(os.path.isdir(os.path.join(path, sub)) for sub in ROUND_SUBS))


def round_actions(keep_days, log_dir):
    cutoff = time.time() - keep_days * 86400
    out = []
    for base in round_parents(log_dir):
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            if not ROUND_RE.match(name):
                continue
            path = os.path.join(base, name)
            if not is_round_dir(path) or _ages(path) >= cutoff:
                continue
            out.append(_act("round", path, _size_of(path),
                            f"局目录: 超过 {keep_days} 天", "rmtree"))
    return out


# ---------- clicks：裁行，文件永不删 ----------

def trim_clicks(keep_days, path, cutoff=None):
    """裁掉 `at` 超期的行（写 tmp 再 os.replace）。返回 (删掉行数, 保留行数)。

    `keep_days <= 0` = 不清理（直接返回，别把整份流水删光）。
    """
    if cutoff is None:
        if int(keep_days or 0) <= 0:
            return (0, 0)
        cutoff = time.time() - keep_days * 86400
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return (0, 0)
    if not lines:
        return (0, 0)

    def fresh(line):
        try:
            at = json.loads(line).get("at")
        except ValueError:
            return True                      # 解析不了的行保留，别顺手毁证据
        return not isinstance(at, (int, float)) or at >= cutoff

    try:
        oldest = json.loads(lines[0]).get("at")
    except ValueError:
        oldest = None
    if isinstance(oldest, (int, float)) and oldest >= cutoff:
        return (0, len(lines))               # 第一行都没超期 → 不重写

    kept = [line for line in lines if fresh(line)]
    if len(kept) == len(lines):
        return (0, len(lines))
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(kept)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return (0, len(lines))
    return (len(lines) - len(kept), len(kept))


def clicks_action(keep_days, log_dir):
    path = os.path.join(log_dir, CLICK_LOG)
    if not os.path.isfile(path):
        return []
    cutoff = time.time() - keep_days * 86400
    expiring = 0
    size = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    at = json.loads(line).get("at")
                except ValueError:
                    continue
                if isinstance(at, (int, float)) and at < cutoff:
                    expiring += 1
                    size += len(line.encode("utf-8"))
    except OSError:
        return []
    if not expiring:
        return []
    return [_act("clicks", path, size,
                 f"点击流水: 超过 {keep_days} 天的行", "trim_lines",
                 keep_days=keep_days, cutoff=cutoff, lines=expiring)]


# ---------- daily：框架按天日志 ----------

def daily_log_actions(keep_days, log_dir):
    cutoff = time.time() - keep_days * 86400
    out = []
    try:
        names = os.listdir(log_dir)
    except OSError:
        return out
    for name in names:
        if not (name.startswith("ok-script") and name.endswith(".log")):
            continue
        match = DATE_RE.search(name)
        if not match:
            continue                          # ok-script.log = 活跃文件，永不删
        try:
            stamp = time.mktime(time.strptime(match.group(1), "%Y-%m-%d"))
        except ValueError:
            continue
        if stamp >= cutoff:
            continue
        path = os.path.join(log_dir, name)
        if not os.path.isfile(path):
            continue
        out.append(_act("daily", path, _size_of(path),
                        f"按天日志: 超过 {keep_days} 天", "remove"))
    return out


# ---------- connection：MCP 连接记录 ----------

def pid_alive(pid, start_time=None):
    """进程还在不在（最好还是同一个实例）；判断不了就当活着，不删。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import psutil
    except ImportError:
        return True
    try:
        proc = psutil.Process(pid)
        if start_time and abs(proc.create_time() - float(start_time)) > 5:
            return False                      # pid 被复用了，旧记录作废
        return True
    except Exception:                         # noqa: BLE001
        return False


def connection_actions(keep_days, log_dir):
    folder = os.path.join(log_dir, "mcp", "connections")
    cutoff = time.time() - keep_days * 86400
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(folder, name)
        if _ages(path) >= cutoff:
            continue
        size = _size_of(path)
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            if pid_alive(meta.get("pid"), meta.get("process_start_time")):
                continue
        except (OSError, ValueError):
            pass                              # 读不出来 + 已超期：连接记录不是证据
        out.append(_act("connection", path, size,
                        f"连接记录: 超过 {keep_days} 天且进程已不在", "remove"))
    return out


# ---------- legacy：2026-11-10 之前的平铺目录 ----------

def legacy_actions(legacy_days, log_dir):
    now = time.time()
    out = []
    for name, days, keep_txt in LEGACY:
        keep_days = int(legacy_days.get(name, days) or 0)
        if keep_days <= 0:
            continue
        base = os.path.join(log_dir, name)
        if not os.path.isdir(base):
            continue
        cutoff = now - keep_days * 86400
        patterns = ("*.jpg", "*.jpeg", "*.png") if keep_txt else ("*",)
        for pattern in patterns:
            for path in glob.glob(os.path.join(base, pattern)):
                if not os.path.isfile(path):
                    continue
                if keep_txt and not path.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                if _ages(path) >= cutoff:
                    continue
                out.append(_act("legacy", path, _size_of(path),
                                f"{name}: 超过 {keep_days} 天", "remove"))
    return out


# ---------- mcp_run：旧 run 的 run.log / status.json ----------

def mcp_run_actions(keep_runs, log_dir):
    runs = sorted(glob.glob(os.path.join(log_dir, "mcp", "*", "status.json")),
                  key=_ages, reverse=True)
    out = []
    for status in runs[max(int(keep_runs or 0), 1):]:
        run_dir = os.path.dirname(status)
        for name in ("run.log", "status.json"):
            path = os.path.join(run_dir, name)
            if os.path.isfile(path):
                out.append(_act("mcp_run", path, _size_of(path),
                                f"mcp: 第 {keep_runs + 1} 次以后的旧 run", "remove"))
    return out


# ---------- cap：总量上限兜底 ----------

def cap_actions(planned, max_bytes, log_dir):
    if not max_bytes:
        return []
    have = _size_of(log_dir) - sum(a["size"] for a in planned)
    if have <= max_bytes:
        return []
    picked = {a["path"] for a in planned}
    picked_dirs = tuple(p + os.sep for p in picked if os.path.isdir(p))
    images = []
    for dirpath, _dirs, files in os.walk(log_dir):
        for name in files:
            if not name.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            path = os.path.join(dirpath, name)
            if path in picked or (picked_dirs and path.startswith(picked_dirs)):
                continue
            images.append((_ages(path), path, _size_of(path)))
    images.sort()
    out = []
    freed = 0
    for _mtime, path, size in images:
        if have - freed <= max_bytes:
            break
        out.append(_act("cap", path, size, "总量上限：最老的截图先删", "remove"))
        freed += size
    return out


# ---------- 入口 ----------

def plan(keep_days=3, log_dir=None, *, kinds=ALL, mcp_keep_runs=10,
         legacy_days=None, max_bytes=None):
    """算出「哪些该删」。只读，不改文件。"""
    keep_days = int(keep_days or 0)
    log_dir = log_dir or LOG_ROOT
    if keep_days <= 0:
        return []
    kinds = tuple(kinds)
    actions = []
    if "round" in kinds:
        actions += round_actions(keep_days, log_dir)
    if "clicks" in kinds:
        actions += clicks_action(keep_days, log_dir)
    if "daily" in kinds:
        actions += daily_log_actions(keep_days, log_dir)
    if "connection" in kinds:
        actions += connection_actions(keep_days, log_dir)
    if "legacy" in kinds:
        actions += legacy_actions(legacy_days or {}, log_dir)
    if "mcp_run" in kinds:
        actions += mcp_run_actions(mcp_keep_runs, log_dir)
    if "cap" in kinds:
        actions += cap_actions(actions, max_bytes, log_dir)
    return actions


def apply(actions):
    """执行 `plan()` 的结果。返回 {"removed": 项数, "freed": 字节, "lines": 裁掉行数}。"""
    removed = 0
    freed = 0
    lines = 0
    for item in actions:
        mode = item["mode"]
        path = item["path"]
        try:
            if mode == "rmtree":
                shutil.rmtree(path)
            elif mode == "trim_lines":
                cut, _kept = trim_clicks(item["keep_days"], path,
                                         item.get("cutoff"))
                lines += cut
            else:
                os.remove(path)
        except OSError:
            continue
        removed += 1
        freed += item["size"]
    return {"removed": removed, "freed": freed, "lines": lines}
