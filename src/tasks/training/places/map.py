"""出行地图（周末地图）：**决策模块** —— 今天什么流派进什么地点。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：

    **地图模块就是决策层**：它比较各地点「今天值不值得去」，然后点地点名。
    地点模块**不需要**互相知道 ——
    「今天本来该进步行街，但现在在餐厅」这个问题由地图模块回答
    （说明是钱不够，或没有合适的扭蛋）。

所以本模块是**唯一**知道「有哪些地点」的地方 —— 地点模块只知道自己。

三层分工：

    本模块（地图）    决策：今天去哪几个地点、先去哪个
        ↓ 问「你今天值不值得去」
    地点模块          判断：内页认不认、买什么/做什么
        ↓
    通用交互层        动作：选选项、按确定、按坐标退出
"""

from src.tasks.training.constants import (
    WEEKEND_MAIN_PLACE, WEEKEND_PARK_GAP, WEEKEND_PLAN, WEEKEND_WORK_MONEY_GATE)
from src.tasks.ui import labels


class MapPlace:
    """周末地图的决策与状态。**不点击**，只给判断。"""

    # ---------- 地图页判据 ----------

    def weekend_map_page(self, texts):
        """这是不是周末地图？

        地图上没有「出行」按钮（2026-10-27 修正）：旧判据是「有地点名就是地图」，
        但周末首页的台词也含地点名（「告诉她公园的草坪是假的」），
        结果首页被判成地图、永远不点「出行」。
        """
        if self.match_text(texts, "出行") is not None:
            return False
        return self.match_text(texts, labels.WEEKEND_PLACES) is not None

    # ---------- 今天去哪几个地点 ----------

    def today_places(self, day=None, st=None):
        """今天该去的地点（有序，前一个走完才轮到下一个）。

        判据来自 `constants.WEEKEND_PLAN`（星期 + 流派 → 地点列表），
        **扭蛋流 / 小游戏流除外**：它们的候选固定是「主地点 + 餐厅」，
        与星期无关（2026-11-10 用户口径）。这样训练日的额外出行日
        （二星野上泷第 9/16/30 天）也走同一条门槛，不再靠「读不到星期 →
        按周六兜底」隐式拿行程。
        """
        if st is None:
            st = self.weekend_state()
        if day is None:
            day = (st.get("date") or (None, None))[1]
        # 读不到星期时按周六那一行兜底（训练流 / 商店流还按表走）。
        day = day or "六"
        main = WEEKEND_MAIN_PLACE.get(self.style)
        if main is not None:
            # 买成扭蛋当天就结束，不用再去餐厅（小游戏流由 visited 过滤）。
            places = [main, "餐厅"]
            if self.style == "扭蛋流" and st.get("gacha_spent"):
                places = [main]
        else:
            places = list(WEEKEND_PLAN.get((day, self.style))
                          or WEEKEND_PLAN.get(("六", self.style))
                          or ["餐厅"])
        places = [p for p in places if p != "商店"]
        # 今天本来有没有「开关控制的地点」（步行街 / 游戏厅）。
        # 只有这种日子才可能被两个开关删空 —— 周日训练流只有公园，不算，
        # 那两个开关跟它没关系。
        had_venues = any(p in ("步行街", "游戏厅") for p in places)
        if not self.config.get("允许进游戏厅"):
            places = [p for p in places if p != "游戏厅"]
        if not self.config.get("允许进步行街"):
            places = [p for p in places if p != "步行街"]
        # 2026-11-10 用户口径：开关把当天地点**全关掉**时按**体力缺口**二选一 ——
        #   缺口（上限−当前）> WEEKEND_PARK_GAP → 公园
        #   缺口 ≤ WEEKEND_PARK_GAP → 餐厅打工
        # 阈值 60 取自「两次划船合计 +60」。周日不一定划船：精力 < 周末野餐
        # 阈值时是野餐（+70），那两天实际回 100 —— 60 是分界，不是回收量的保证。
        # 训练流精力优先，零花钱不参与这个判断；读不到体力就不赌，仍去公园。
        if had_venues and not any(p in ("步行街", "游戏厅") for p in places):
            if "公园" in places and self.park_gap_small():
                places = ["餐厅"]
        # 兜底：表里没这个组合时总得花掉一天，也去餐厅（赚钱比白逛一天好）。
        if not places:
            places = ["餐厅"]
        return places

    # ---------- 体力缺口（双关时公园 vs 餐厅） ----------

    def park_gap_small(self):
        """体力缺口（上限 − 当前）小到不用去公园回血吗？

        `True`  = 体力够用 → 双关时改去餐厅打工；
        `False` = 缺得多、或读不到体力 → 去公园（不拿 OCR 抖动赌「够用」）。
        阈值在 `constants.WEEKEND_PARK_GAP`：60 = 周六 + 周日两次划船的回血量。
        """
        bar = self.top_bar()
        cur, top = bar.get("stamina"), bar.get("stamina_max")
        if cur is None or top is None:
            self.log_info("周末：读不到体力（精力/上限）→ 双关时按「公园」兜底")
            return False
        gap = top - cur
        self.log_info(
            f"周末：体力缺口 {top}−{cur}={gap}"
            f"{'≤' if gap <= WEEKEND_PARK_GAP else '>'} {WEEKEND_PARK_GAP} → "
            f"{'餐厅打工' if gap <= WEEKEND_PARK_GAP else '公园回精力'}")
        return gap <= WEEKEND_PARK_GAP

    # ---------- 先去哪个 ----------

    def prefer_venue(self, texts, places, day=None):
        """出行地图：决定今天先去哪个地点。

        两条决策链，**互斥**：

        **扭蛋流 / 小游戏流**（2026-11-10 用户口径，**取代** 2026-11-08 的 0~7 表）：
        只看零用钱，和队友、标签、星期都无关 ——
          - 读不到 → 主地点（不拿打工赌今天的消费）；
          - < `WEEKEND_WORK_MONEY_GATE`（360）→ **只去餐厅打工**；
          - ≥ 360 → 主地点。
        理由（一次步行街期望 3.3 颗、4 颗金 = 360 覆盖 78% 的触发轮次）见
        `constants.WEEKEND_WORK_MONEY_GATE` 的注释。

        **训练流周六**（步行街 / 游戏厅 二选一，买扭蛋/玩小游戏当天就结束）：
        1. **只有一个在打折** → 去打折的那个；
        2. 其他情况（都打折 / 都不打折）→ 保持原顺序
           （步行街 → 游戏厅 → 公园）。

        队友气泡（`venue_has_mate`）**不参与任何一条**（2026-11-10 用户口径）：
        它看不到品质（带队友的可能是根本不买的蓝卡），也看不到刷新后的批次
        （买一颗刷一批，下一批才冒队友），对「去哪儿」没有预测力，只当添头。
        删掉它之后，训练流周六原来那条「两边都有队友再比打折」自然退化成
        「只有一边打折就去那边」。

        打折判据见 venue_on_discount（OCR 文本 + 名字左侧）。
        """
        # 读不到星期时按周六那一行兜底（`today_places` 也是这么兜的）。
        day = day or "六"
        main = WEEKEND_MAIN_PLACE.get(self.style)
        if main is not None:
            pair = [p for p in places if p in (main, "餐厅")]
            if len(pair) < 2:
                return places
            money = self.read_money(texts)
            if money is None:
                self.log_info(f"周末地点：零用钱读不到 → 去「{main}」")
            elif money < WEEKEND_WORK_MONEY_GATE:
                self.log_info(
                    f"周末地点：零用钱 {money} < {WEEKEND_WORK_MONEY_GATE}"
                    " → 只去「餐厅」打工")
                return ["餐厅"]
            else:
                self.log_info(
                    f"周末地点：零用钱 {money} ≥ {WEEKEND_WORK_MONEY_GATE}"
                    f" → 去「{main}」")
            return [main] + [p for p in places if p != main]

        # ---- 训练流周六：只有一边打折就先去那边，否则按原顺序 ----
        if day != "六" or self.style != "训练流":
            return places
        pair = [p for p in places if p in ("步行街", "游戏厅")]
        if len(pair) < 2:
            return places
        discounted = [p for p in pair
                      if self.venue_on_discount(texts, self._venue_box(texts, p))]
        if len(discounted) == 1:
            chosen = discounted[0]
            self.log_info(f"周末地点：只有「{chosen}」在打折 → 先进它")
            return [chosen] + [p for p in places if p != chosen]
        self.log_info(f"周末地点：没分出高下，按原顺序 {pair}")
        return places

    # ---------- 地点名的框 ----------

    def _venue_box(self, texts, place):
        """地点名对应的文本框（整词优先，其次 ≤4 字的短框）。"""
        box = self.exact_text(texts, (place,))
        if box is not None:
            return box
        return next((t for t in texts
                     if place in t.name and len(t.name.strip()) <= 4), None)
