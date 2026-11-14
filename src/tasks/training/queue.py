"""多角色队列：**练谁 → 练到多少分 → 练到哪了**。

用户 2026-11-04 口径（原话拆成四条）：

1. 等所有任务跑完，选 **野上泷** 当队长训练，反复练到 **7500 分以上**；
2. 换 **亚理纱·贾斯敏**，反复练到 **7300 分以上**；
3. 换 **近江奏**，反复练到 **7300 分以上**；
4. 全部达标 → 提前终止。

于是「多角色」= **多局串行**，不是一局内换人：

- 一局 = 一次养成，终点是「注册球员 → 回大厅」（`END_OF_ROUND.md`）；
- 每局结束时在「恭喜获得 / 训练奖励」页读一次**这个球员的评分**
  （`parsers.read_squad_ratings` + `roster.position_by_name` 队长→位置）；
- 达标 → 游标 +1，换队列里的下一个队长开新局；
- 没达标 → **同一个队长**再开一局（这就是「反复训练」）。

**为什么不在同一局里换队长**：流派是队长的属性（`RULES.md §0`），周末计划、
休息阈值、扭蛋/商店策略全部按流派派生；一局中途换队长等于前三天按 A 的计划跑、
后四天按 B 的，会把两种流派都跑坏。

**为什么不把目标分写进配置的每一条**：配置项只有一个字符串 `队长队列`
（`野上泷:7500,亚理纱·贾斯敏:7300,近江奏:7300`），解析在这里做 ——
新增/删除角色不用改代码，也不用给每个角色加一个配置项。
"""

import json
import os
import re
import time

from src.tasks.training import roster
from src.tasks.training.constants import PROJECT_ROOT
from src.tasks.ui import labels

# 进度文件：**跨进程/重启续跑**才用（2026-11-07 起：队列默认一次性内存对象，
# 只有明确传了 `path` 才落盘）。放 logs/ 下（已被 .gitignore 忽略），不污染 configs/。
QUEUE_PROGRESS = os.path.join(PROJECT_ROOT, "logs", "training_queue.json")

# 「名字 + 目标分」的分隔符。角色名里只会出现「·」，不会撞上它们。
ITEM_SPLIT_RE = re.compile(r"[,，;；\n]+")
TARGET_SEPARATORS = (":", "：", "=", " ")


def parse_queue(spec):
    """`'野上泷:7500, 亚理纱·贾斯敏:7300'` → `[{'name','target'}, …]`。

    容错：分隔符中英文都收；「名字」后面没有目标分 → `target=None`
    （含义见 `target_met`：不设目标就永远不达标 → 该角色只跑 `最大局数` 局）。
    """
    items = []
    for raw in ITEM_SPLIT_RE.split(spec or ""):
        item = raw.strip()
        if not item:
            continue
        name, target = item, None
        for sep in TARGET_SEPARATORS:
            if sep in item:
                head, _, tail = item.partition(sep)
                name = head.strip()
                m = re.search(r"\d+", tail)
                target = int(m.group()) if m else None
                break
        if not name:
            continue
        items.append({"name": name, "target": target})
    return items


def position_of(name):
    """角色名 → 场上位置（`中锋/控卫/分卫/小前/大前`），查不到返回 None。

    名字比较一律过 `labels.name_matches`（间隔点被 OCR 吃掉、带 `SR-` 前缀都算同一个）。
    """
    for known, pos in roster.position_by_name().items():
        if labels.name_matches(known, name):
            return pos
    return None


class CaptainQueue:
    """一个多角色队列 + 它的进度（默认一次性，可选持久化）。

    2026-11-07 用户口径：队列是**外部注入的参数**（agent 指定），
    不是配置项 —— 默认 `resume=False`、`path=None`（纯内存，跑完即弃）；
    只有明确要跨重启续跑的场景才传 `path`。
    """

    def __init__(self, spec, path=None, resume=False):
        self.items = parse_queue(spec)
        # path=None → 纯内存队列（2026-11-07 用户口径：队列是一次性参数，
        # 不落盘）；明确传 path 才持久化（跨重启续跑的场景）。
        self.path = path
        self.cursor = 0
        self.runs = []               # 每局的 (队长, 目标, 评分, 是否达标, 时间)
        if resume:
            self.load()

    # ---------- 查询 ----------
    @property
    def empty(self):
        return not self.items

    @property
    def current(self):
        """当前队长条目；队列跑完返回 None。"""
        return self.items[self.cursor] if self.cursor < len(self.items) else None

    @property
    def captain(self):
        item = self.current
        return item["name"] if item else None

    @property
    def target(self):
        item = self.current
        return item["target"] if item else None

    @property
    def position(self):
        """当前队长的场上位置（读球队评分那一页要用）。"""
        name = self.captain
        return position_of(name) if name else None

    @property
    def finished(self):
        return self.cursor >= len(self.items)

    def target_met(self, score):
        """这一局算不算达标。评分读不到（None）一律算**没达标**，不许蒙混过关。"""
        target = self.target
        if target is None or score is None:
            return False
        return score >= target

    def summary(self):
        """一行给日志用：`野上泷 7500 (2/5) → 亚理纱…`。"""
        if self.empty:
            return "（队列为空）"
        parts = []
        for i, item in enumerate(self.items):
            mark = "✓" if i < self.cursor else ("▶" if i == self.cursor else "·")
            target = f"{item['target']}" if item["target"] else "无目标"
            parts.append(f"{mark}{item['name']} {target}")
        return " ｜ ".join(parts)

    # ---------- 记账 ----------
    def record(self, score):
        """记一局的结果。返回 True = 达标（游标已前进）。"""
        item = self.current
        if item is None:
            return False
        met = self.target_met(score)
        self.runs.append({"captain": item["name"], "target": item["target"],
                          "score": score, "met": met,
                          "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        if met:
            self.cursor += 1
        self.save()
        return met

    def runs_for(self, name=None):
        """该角色已经跑过的局（复盘用）。"""
        name = name or self.captain
        return [r for r in self.runs if r["captain"] == name]

    # ---------- 持久化 ----------
    def signature(self):
        """队列指纹：配置改了就**不续**旧进度（否则游标会指向别的角色）。"""
        return "|".join(f"{i['name']}:{i['target']}" for i in self.items)

    def save(self):
        if self.path is None:
            return                      # 纯内存队列（2026-11-07 起默认）：没有文件可写
        data = {"signature": self.signature(), "cursor": self.cursor,
                "runs": self.runs[-200:]}          # 只留最近 200 局，别无限长
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[queue] 进度写不进去（{type(e).__name__}: {e}）")

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict) or data.get("signature") != self.signature():
            return False
        cursor = data.get("cursor")
        if not isinstance(cursor, int) or not (0 <= cursor <= len(self.items)):
            return False
        self.cursor = cursor
        runs = data.get("runs")
        self.runs = [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []
        return True
