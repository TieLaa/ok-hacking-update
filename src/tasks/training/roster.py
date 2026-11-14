"""角色表：从 `docs/roster/*.json` 读位置 / 推荐属性 / 流派。

2026-11-12 用户口径：推荐属性不手填配置，直接读采集好的角色列表。

运行时唯一入口：第一次 `lookup()` 才读文件，之后整局缓存
（json 改了要重开任务才生效）。读不到文件/条目一律返回空，不抛异常 ——
调用方按「查不到」回退现有行为。
"""

import json
import os

from src.tasks.training.constants import PROJECT_ROOT

ROSTER_DIR = os.path.join(PROJECT_ROOT, "docs", "roster")
# 文件名 → 稀有度（json 里 `rarity` 字段优先；老数据没有时用文件名兜底）
ROSTER_FILES = {"ssr_roster.json": "SSR", "sr_roster.json": "SR",
                "r_roster.json": "R"}

_CACHE = None


def _load():
    """读三个 json，按角色名建索引；读不到的条目跳过。"""
    table = {}
    for filename, file_rarity in ROSTER_FILES.items():
        path = os.path.join(ROSTER_DIR, filename)
        try:
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
        except (OSError, ValueError):
            continue
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            table[name] = {
                "position": row.get("position"),
                "preferred": tuple(row.get("preferred") or ()),
                # json 里 `style_tag` 才是训练流/商店流/扭蛋流/小游戏流；
                # `style` 是力量/技巧/速度那一维，代码目前不用。
                "style": row.get("style_tag"),
                # 选队长页的「显示设定 → 筛选」要按稀有度勾选
                "rarity": row.get("rarity") or file_rarity,
            }
    return table


def lookup(name):
    """按角色名查 `{position, preferred, style, rarity}`；查不到返回 `{}`。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = _load()
    return _CACHE.get((name or "").strip(), {})
