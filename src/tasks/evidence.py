"""存证的**唯一落盘口**：目录布局、文件命名、限流、保留期都只在这里决定。

为什么要有这一层（2026-11-10 用户口径）：

    存证原来是 7 个调用点各自拼路径 —— `events` 一处、`unknown` 一处、
    `unmark` 一处、`debug` **四处**（flow / match / talent / parsers），
    再加 `clicks.jsonl`。结果是「按局分目录」这种全局性改动必须改 7 遍，
    漏一处就有一类帧继续写回老位置。归口之后调用方不知道任何路径，
    只说要存什么。

目录约定（**局** = 脚本视角的一局：从开始跑流程到收工回大厅）：

    MCP 运行：logs/mcp/<run_id>/            ← 框架注入的 artifact_directory，不能换
                └─ <局ID>/{events,unknown,unmark,debug}/ + meta.json

    GUI 运行：logs/<局ID>/{events,unknown,unmark,debug}/ + meta.json

    局ID = <mmdd-HHMMSS>[_接手][_第N局][_队长]
      - 时间戳是**开始管这一局**的时刻（不是点「开始」那一刻，见 begin_round）；
      - `接手` 只在没指定队长（快速接手别人开好的局）时出现 —— 那种局的开局
        时刻脚本无从得知，不能假装时间戳就是它；
      - `第N局` 只在多角色队列模式下出现；
      - 局目录里有 `pre/` 子目录的场合：那一局还没开始（MCP 的 reset/verify
        阶段就出错），存证只能挂在「本次运行」根下。

四个子目录的产物与老结构**逐字节同构**（文件名规则不变），所以老工具只需
把「扫一层目录」换成「扫递归」，判据和回归语料都不用动。

保留期：每局开头清一次 `日志保留天数` 之前建好的**整局目录**。只删
「名字像局ID + 里面确实有那四个子目录之一」的目录，`clicks.jsonl`、
`reports/`、`mcp/connections/`、`docs/shots/` 都不在扫描范围内。
用户 2026-11-10 口径：**不做 jpg 开关** —— 真需要调试时没有图更麻烦，
靠按局清理控制体积。
"""

import json
import os
import re
import shutil
import threading
import time

from src.tasks.training.constants import JPEG_PARAMS, PROJECT_ROOT

import cv2

#: 所有运行期产物的根（GUI 运行时局目录直接挂在这里）
LOG_ROOT = os.path.join(PROJECT_ROOT, "logs")
#: 点击流水：全局单文件（跨局），每行带 round 字段
CLICK_LOG = os.path.join(LOG_ROOT, "clicks.jsonl")
#: 局目录判定：`1110-091635` 开头
ROUND_RE = re.compile(r"^\d{4}-\d{6}_")
#: 局目录里必然存在的子目录（清理时的安全阀：没有就绝不删）
ROUND_SUBS = ("events", "unknown", "unmark", "debug")
#: 局还没开始时的存证落点
PRE_DIR = "pre"


def _stamp(at=None):
    return time.strftime("%m%d-%H%M%S", time.localtime(at or time.time()))


def _safe(name):
    """目录名只保留可移植字符；中文队长名照留（Windows + Python 没问题）。"""
    return "".join(c for c in str(name) if c not in '<>:"/\\|?*').strip()


def _texts_block(texts):
    return "\n".join(f"{t.name}\t({t.x},{t.y},{t.width},{t.height})" for t in texts or [])


def write_jpg(path, frame):
    """写 JPEG 到**任意路径**（含中文/空格）。

    为什么不用 `cv2.imwrite`：Windows 上 OpenCV 走 ANSI 路径，目录名带中文
    （队长名）时它**静默失败**（返回 False、不抛异常）—— 旧代码在
    `parsers.py` 里就为此把文件名限制成 ASCII。这里改用 `imencode` 拿字节、
    再用 Python 的 `open` 写（它走宽字符 API），中文目录名就能用。
    """
    ok, buf = cv2.imencode(".jpg", frame, JPEG_PARAMS)
    if not ok:
        return False
    with open(path, "wb") as f:
        f.write(buf.tobytes())
    return True


class EvidenceSink:
    """一次任务实例一个；`begin_round()` 一局换一次目录。

    调用方（各 handler / mixin）只调 `snap_event` / `dump_unknown` /
    `dump_unmark` / `dump_debug` / `dump_frame` / `click`，**不碰任何路径**。
    """

    def __init__(self):
        self.root = ""          # 本次运行的产物根
        self.round_id = ""      # 当前局ID
        self.round_dir = ""     # 当前局目录（绝对路径）
        self._last_snap = 0.0   # 事件存帧限流
        self._meta = {}
        self._log = None        # 存证失败时用任务自己的 log_debug 打一行

    def set_logger(self, fn):
        """由任务注入 `log_debug`；失败原因要留痕，不能静默吞掉。"""
        self._log = fn
        return self

    def _fail(self, exc, what):
        if self._log is not None:
            try:
                self._log(f"{what}失败: {type(exc).__name__}: {exc}")
            except Exception:                        # noqa: BLE001
                pass
        return None

    # ---------- 运行 / 局 ----------

    def begin_run(self, root=None):
        """定下本次运行的产物根：MCP = artifact_directory，GUI = logs/。"""
        self.root = root or LOG_ROOT
        return self.root

    def begin_round(self, *, captain="", handover=False, round_no=0,
                    style="", entry="GUI", keep_days=3):
        """开一局：清旧目录 → 建新目录 → 写 meta.json。

        返回局目录的绝对路径。时间戳取**调用时刻**，调用点必须在
        `recover_to_lobby()` **之后**（回大厅可能几十秒，那段时间不属于这一局）。
        """
        if not self.root:
            self.begin_run()
        self.prune(keep_days)

        parts = [_stamp()]
        if handover:
            parts.append("接手")
        if round_no > 1:
            parts.append(f"第{round_no}局")
        captain = _safe(captain or "")
        if captain:
            parts.append(captain)
        self.round_id = "_".join(parts)
        self.round_dir = os.path.join(self.root, self.round_id)
        for sub in ROUND_SUBS:
            os.makedirs(os.path.join(self.round_dir, sub), exist_ok=True)

        self._last_snap = 0.0
        self._meta = {
            "round": self.round_id,
            "entry": entry,
            "captain": captain,
            "style": style,
            "round_no": round_no,
            "handover": bool(handover),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "started_ts": round(time.time(), 3),
            "run_log": os.path.join("logs", "run.log"),
            "run_log_start": self._log_size(),
        }
        self._write_meta()
        return self.round_dir

    def end_round(self, reason="", steps=0, **extra):
        """收工：把结束时间 / 步数 / run.log 偏移写回 meta.json。

        额外的字段（流派等开局后才知道的）用 `**extra` 传进来合并。
        `run_log_start/end` 是 `logs/run.log` 的字节偏移，复盘时可以按
        `[start, end)` 把这一局的进程日志整段切出来（进程日志是连续写的，
        没法按目录分）。
        """
        if not self.round_dir:
            return
        self._meta.update(extra)
        self._meta.update({
            "ended_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ended_ts": round(time.time(), 3),
            "steps": int(steps or 0),
            "end_reason": reason,
            "run_log_end": self._log_size(),
        })
        self._write_meta()

    @property
    def dir(self):
        """当前存证目录：局目录；局还没开始（MCP reset 阶段）退回 <root>/pre。"""
        base = self.round_dir or os.path.join(self.root or LOG_ROOT, PRE_DIR)
        return base

    def _sub(self, name, leaf=""):
        folder = os.path.join(self.dir, name, leaf) if leaf else os.path.join(self.dir, name)
        os.makedirs(folder, exist_ok=True)
        return folder

    def _write_meta(self):
        try:
            with open(os.path.join(self.round_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump(self._meta, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    @staticmethod
    def _log_size():
        try:
            return os.path.getsize(os.path.join(LOG_ROOT, "run.log"))
        except OSError:
            return 0

    # ---------- 四类存证 ----------

    def snap_event(self, handler, texts, frame=None, clicks=None):
        """事件级截图 + JSON（报告页「事件 ↔ 截图」用）。同一秒最多一张。"""
        now = time.time()
        if now - self._last_snap < 1.0:
            return None
        if frame is None or not getattr(frame, "any", lambda: False)():
            return None
        self._last_snap = now
        try:
            base = os.path.join(self._sub("events"), f"{_stamp(now)}_{handler}")
            write_jpg(base + ".jpg", frame)
            with open(base + ".json", "w", encoding="utf-8") as f:
                json.dump({"at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                           "round": self.round_id,
                           "handler": handler,
                           "texts": [t.name.strip() for t in texts if t.name.strip()][:30],
                           "clicks": list(clicks or [])},
                          f, ensure_ascii=False)
            return base
        except Exception as e:                       # noqa: BLE001
            return self._fail(e, "存证")

    def dump_unknown(self, texts, tag, frame=None, step=0):
        """完全认不出来的界面：jpg + OCR 文本（文本才是排查时真正读的东西）。"""
        try:
            base = os.path.join(self._sub("unknown"), f"{_stamp()}_{tag}_s{step}")
            if frame is not None:
                write_jpg(base + ".jpg", frame)
            with open(base + ".txt", "w", encoding="utf-8") as f:
                f.write(_texts_block(texts))
            return base
        except Exception as e:                       # noqa: BLE001
            return self._fail(e, "存证")

    def dump_unmark(self, texts, tag, frame=None, step=0, cards=None, picked=None, note=""):
        """未标注样本：按品质色猜着选的页面，三件套 jpg/txt/json。"""
        try:
            base = os.path.join(self._sub("unmark"), f"{_stamp()}_{tag}_s{step}")
            if frame is not None:
                write_jpg(base + ".jpg", frame)
            with open(base + ".txt", "w", encoding="utf-8") as f:
                f.write(_texts_block(texts))
            with open(base + ".json", "w", encoding="utf-8") as f:
                json.dump({"round": self.round_id, "tag": tag, "note": note, "picked": picked,
                           "cards": [{"name": c.get("name"), "quality": c.get("quality"),
                                      "mates": c.get("mates"),
                                      "x": c["box"].x, "y": c["box"].y}
                                     for c in (cards or [])]},
                          f, ensure_ascii=False, indent=2)
            return base
        except Exception as e:                       # noqa: BLE001
            return self._fail(e, "存证")

    def dump_debug(self, texts, tag, frame=None, summary="", actions=None):
        """点不动 / 异常的调试包：frame.jpg + ocr.txt + actions.txt + threads.txt。"""
        try:
            folder = self._sub("debug", f"{_stamp()}_{tag}")
            if frame is not None:
                # 存 JPEG 不存 PNG：同一帧 PNG 约 2.4 MB、JPEG 约 0.4 MB，读图更快。
                # JPEG_PARAMS = q95 + 4:4:4，保证颜色可复核（判据回归的基线）。
                write_jpg(os.path.join(folder, "frame.jpg"), frame)
            with open(os.path.join(folder, "ocr.txt"), "w", encoding="utf-8") as f:
                f.write(_texts_block(texts))
            with open(os.path.join(folder, "actions.txt"), "w", encoding="utf-8") as f:
                f.write(f"round={self.round_id} {summary}\n")
                f.write("\n".join(actions or []))
            with open(os.path.join(folder, "threads.txt"), "w", encoding="utf-8") as f:
                for th in threading.enumerate():
                    f.write(f"{th.name}\t{th.is_alive()}\n")
            return folder
        except Exception as e:                       # noqa: BLE001
            return self._fail(e, "存证")

    def dump_frame(self, kind, texts, frame=None, idx=0, header=""):
        """限流校准帧（技能三选一 / 炒股 / 科目页）：`<kind><序号>` 由调用方管。"""
        try:
            stamp = _stamp()
            folder = self._sub("debug")
            idx = int(idx or 0)
            path = os.path.join(folder, f"{stamp}_{kind}{idx:02d}.jpg")
            if frame is not None and getattr(frame, "any", lambda: False)():
                write_jpg(path, frame)
            with open(path[:-4] + ".txt", "w", encoding="utf-8") as f:
                if header:
                    f.write(header if header.endswith("\n") else header + "\n")
                f.write(_texts_block(texts))
            return path
        except Exception as e:                       # noqa: BLE001
            return self._fail(e, "存证")

    # ---------- 点击流水 ----------

    def click(self, record):
        """每次点击一行，追加到全局 `logs/clicks.jsonl`（带 round）。"""
        try:
            os.makedirs(os.path.dirname(CLICK_LOG), exist_ok=True)
            line = dict(record)
            line["round"] = self.round_id
            with open(CLICK_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception:                            # noqa: BLE001
            pass

    # ---------- 保留期 ----------

    def prune(self, keep_days=3):
        """删掉 `keep_days` 天之前的**整局目录**；0 = 不清理。

        安全阀有三道：名字必须像局ID、里面必须有 `events|unknown|unmark|debug`
        之一、必须是目录。所以 `clicks.jsonl` / `reports/` / `mcp/connections/`
        / `connections` / `docs/shots` 都不可能被误删。
        """
        keep_days = int(keep_days or 0)
        if keep_days <= 0:
            return []
        cutoff = time.time() - keep_days * 86400
        removed = []
        for base in self._round_parents():
            try:
                names = os.listdir(base)
            except OSError:
                continue
            for name in names:
                if not ROUND_RE.match(name):
                    continue
                path = os.path.join(base, name)
                if not os.path.isdir(path):
                    continue
                if not any(os.path.isdir(os.path.join(path, s)) for s in ROUND_SUBS):
                    continue
                try:
                    if os.path.getmtime(path) >= cutoff:
                        continue
                    shutil.rmtree(path, ignore_errors=True)
                    removed.append(path)
                except OSError:
                    continue
        return removed

    def _round_parents(self):
        """局目录可能挂在两处：GUI 的 logs/ 和 MCP 的 logs/mcp/<run_id>/。"""
        parents = [self.root or LOG_ROOT]
        mcp = os.path.join(LOG_ROOT, "mcp")
        try:
            for name in os.listdir(mcp):
                sub = os.path.join(mcp, name)
                if os.path.isdir(sub) and name not in ("connections", "attempts"):
                    parents.append(sub)
        except OSError:
            pass
        return parents
