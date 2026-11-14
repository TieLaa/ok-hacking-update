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

from collections import namedtuple

from src.tasks.training import params
from src.tasks.training.constants import (
    PARK_PICNIC_STAMINA, SHOP_SEASON_WEEKS, WEEKEND_MAIN_PLACE,
    WEEKEND_PARK_GAP, WEEKEND_PLAN, WEEKEND_WORK_MONEY_GATE, WORK_STAMINA_COST)
from src.tasks.ui import labels

# 周末计划的一步：place=地点名，urgent=True=急招临时插队（不是今天的主计划）。
WeekendStep = namedtuple("WeekendStep", "place urgent")


class MapPlace:
    """周末地图的决策与状态。**不点击**，只给判断。"""

    # ---------- 地图页判据 ----------

    def weekend_map_page(self, texts):
        """这是不是周末地图？

        地图上没有「出行」按钮（2026-10-27 修正）：旧判据是「有地点名就是地图」，
        但周末首页的台词也含地点名（「告诉她公园的草坪是假的」），
        结果首页被判成地图、永远不点「出行」。

        2026-11-13 修正：地点名必须是**整框短标签**，且至少两个。
        事件正文也会出现「公园」（「篮球社集体在公园打街球时…」），
        包含匹配会把事件页当成地图 → `handle_weekend` 误置「今天出行已用」，
        还会抢在 `handle_event_choice` 后面点不到按钮空转。
        """
        if self.match_text(texts, "出行") is not None:
            return False
        places = [t for t in texts
                  if len(t.name.strip()) <= 4
                  and t.name.strip().rstrip("+") in labels.WEEKEND_PLACES]
        return len(places) >= 2

    # ---------- 周末缺口（训练流） ----------

    def weekend_gap(self, texts=None):
        """精力缺口 = 上限 − 当前；读不到返回 None。"""
        bar = self.top_bar(texts)
        cur, top = bar.get("stamina"), bar.get("stamina_max")
        if cur is None or top is None:
            return None
        return max(top - cur, 0)

    def training_weekend_places(self, texts=None, day=None, st=None):
        """训练流周末：按精力缺口选今天做什么（2026-11-11 用户口径）。

        缺口 = 精力上限 − 当前精力。**不再看固定星期几**：

            缺口 ≥ 周末划船缺口（默认 30） → 公园
                公园内再按「周末野餐阈值」（默认 70）分野餐/划船；
            缺口 < 周末划船缺口（30） → 餐厅打工

        打工后面跟「公园」兜底：这一站打不了工时，公园划船还能拿属性，
        不至于把一天空掉。读不到精力时返回 None，由调用方回退「餐厅 → 公园」。

        为什么缺口小也不去步行街/游戏厅（2026-11-11 用户口径）：
        那两处都是**花掉一天 + 花掉零用钱**，只换一张卡；打工是**花掉一天 +
        赚 240~420 零用钱**，而商店买牛奶/秘籍不占日期 —— 特训牛奶 50 币
        能让 3 天训练少耗 40% 精力（约 1 个训练日），对训练流严格更划算。
        """
        gap = self.weekend_gap(texts)
        if gap is None:
            return None
        row_gap = int(self.config.get("周末划船缺口"))
        if gap >= row_gap:
            return ["公园"]
        self.log_info(
            f"周末缺口 {gap} < {row_gap} → 餐厅打工"
            "（零用钱留给商店的牛奶/秘籍，周末不去步行街/游戏厅）")
        return ["餐厅", "公园"]

    # ---------- 今天去哪几个地点 ----------

    def today_places(self, day=None, st=None, texts=None):
        """今天该去的地点（有序，前一个走完才轮到下一个）。

        判据来自 `constants.WEEKEND_PLAN`（星期 + 流派 → 地点列表），
        **训练流 / 扭蛋流 / 小游戏流除外**：
        - 训练流：按精力缺口选动作（见 `training_weekend_places`）；
          读不到缺口时回退「餐厅 → 公园」，**不再回退步行街/游戏厅**；
        - 扭蛋流 / 小游戏流：候选固定是「主地点 + 餐厅」，与星期无关
          （2026-11-10 用户口径）。这样训练日的额外出行日
          （二星野上泷第 9/16/30 天）也走同一条门槛，不再靠「读不到星期 →
          按周六兜底」隐式拿行程。
        """
        if st is None:
            st = self.weekend_state()
        if day is None:
            day = (st.get("date") or (None, None))[1]
        # 读不到星期时按周六那一行兜底（商店流还按表走）。
        day = day or "六"
        # 2026-11-14 用户口径（游戏设置，唯一策略）：第 7 周周日不能再出行，
        # 只能进商店清空零花钱。所有流派都不规划地点。
        week_now, day_now = self.current_day()
        if week_now == SHOP_SEASON_WEEKS and day_now == "日":
            self.log_info("第7周星期日：不能再出行 → 只进商店")
            return []
        if self.style == "训练流":
            gap_places = self.training_weekend_places(texts, day, st)
            if gap_places is not None:
                return gap_places
            # 缺口读不到：也不回退旧的「步行街 → 游戏厅」（训练流不再消费），
            # 餐厅打不了工还有公园兜底。
            self.log_info("周末：训练流读不到精力缺口 → 回退「餐厅 → 公园」")
            return ["餐厅", "公园"]
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
        `constants.WEEKEND_WORK_MONEY_GATE` 的注释。2026-11-13 试过 200，
        但折扣不是每次发生，没折扣时只够买 2 颗，已回滚 360。

        **训练流**（2026-11-11 起）**不再经过这里**：缺口 < 30 固定去餐厅打工，
        候选里已经没有步行街/游戏厅。下面这条「只有一边打折就先去那边」
        只留给仍带这两个候选的旧行程（防御性兜底，以后加流派时不用重写）。
        1. **只有一个在打折** → 去打折的那个；
        2. 其他情况（都打折 / 都不打折）→ 保持原顺序。

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
            week, _ = self.current_day()
            # 2026-11-14 用户口径（游戏设置）：第 7 周周六是最后一次出行，
            # **不看零花钱**直接去主地点；没消费再退出回餐厅打工（下一站）。
            if day == "六" and week == SHOP_SEASON_WEEKS:
                self.log_info(
                    f"周末地点（第7周）：最后一次出行 → 直接去「{main}」"
                    "（没消费再退出回餐厅打工）")
                return [main] + [p for p in places if p != main]
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

    # ---------- 今天的完整步骤（主计划 + 急招 detour） ----------

    def weekend_plan(self, day=None, st=None, texts=None):
        """今天要走的步骤（有序），每一步带 `urgent` 标记。

        `today_places` / `prefer_venue` 只算**主计划**，不认识急招。
        急招只在「主计划首选不是餐厅」时作为 detour 插到最前；
        原主计划完整保留在后面。

        为什么不在 today_places/prefer_venue 里插餐厅：
        - `urgent` 要跟着具体步骤走，餐厅 handler 才知道这一趟是
          “原计划就是打工”还是“急招临时插队”；
        - 主计划逻辑保持纯函数，现有测试不用动。

        用户 2026-11-13 口径：
        - 原计划首选就是餐厅 → urgent=False，急招卡不可选时可改打桌游GM；
        - 急招插队 → urgent=True，急招卡不可选时退出，回原计划。
        """
        if st is None:
            st = self.weekend_state()
        if day is None:
            day = (st.get("date") or (None, None))[1]
        visited = st.get("visited") or set()
        tried = st.get("gacha_tried") or set()

        # 先按原逻辑过滤「今天已经去过/试过」的地点，再算主计划。
        places = self.today_places(day, st, texts)
        places = [p for p in places if p not in visited]
        if "步行街" in tried:
            places = [p for p in places if p != "步行街"]
        main = self.prefer_venue(texts, places, day)
        if not main:
            steps = []
        elif main[0] == "餐厅":
            # 主计划首选就是餐厅 → 不是 detour（商店流 / 钱不够 / 训练流缺口小）。
            steps = [WeekendStep(p, False) for p in main]
        elif (not self.venue_has_urgent(texts, "餐厅")
              or "餐厅" in visited
              or (self.style == "扭蛋流" and st.get("gacha_spent"))):
            steps = [WeekendStep(p, False) for p in main]
        else:
            self.log_info(f"周末：餐厅有「急招！」→ 插到最前（原首选 {main[0]}）")
            steps = ([WeekendStep("餐厅", True)]
                     + [WeekendStep(p, False) for p in main if p != "餐厅"])
        return self.vacation_eve_guard(steps, texts, st)

    # ---------- 海边前周末：把体力拉回目标（第3周） ----------

    def vacation_eve_target(self, st=None):
        """第3周周末的体力目标；不是这一周就返回 None。

        用户 2026-11-13 口径：第4周海边合宿第4天是**全队友参与**的训练
        （玩家等级提升给的增益），体力不够就得休息、错过这一天。
        所以第3周周日结束时必须 ≥ 目标值（`strategy/params.json`）。
        """
        if st is None:
            st = self.weekend_state()
        week, day = st.get("date") or (None, None)
        if week != 3 or day not in ("六", "日"):
            return None
        return int(params.get("vacation.stamina_target"))

    def vacation_eve_guard(self, steps, texts, st=None):
        """第3周周末：体力不够时把打工换成公园，保证周日结束 ≥ 目标。

        算法（划船 +30 / 野餐 +70 / 打工 −25）：

            周六：今天还能靠周日补一次
                < 目标−70 → 今天必须先补，整天只去公园
                < 目标−45 → 今天不打工（打工后周日一次野餐也补不回）
                ≥ 目标−45 → 原计划（打工后周日野餐兜底）
            周日：今天就是最后一次机会
                < 目标    → 整天只去公园
                < 目标+25 → 今天不打工（打完就掉到目标以下）
                ≥ 目标+25 → 原计划

        体力读不到时不赌：今天只去公园。计划本来为空（地点都去过）时不插手，
        避免「公园已去过 → 再点一次」打转。
        """
        if not steps:
            return steps
        if st is None:
            st = self.weekend_state()
        target = self.vacation_eve_target(st)
        if target is None:
            return steps
        day = st["date"][1]
        stamina = self.top_bar(texts).get("stamina")
        if stamina is None:
            self.log_info("海边前周末：读不到体力 → 今天只去公园")
            return self.vacation_eve_park(st)
        if day == "六":
            must_recover = target - PARK_PICNIC_STAMINA
            if stamina < must_recover:
                self.log_info(
                    f"海边前周末：周六体力 {stamina} < {must_recover}"
                    "（周日一次野餐也补不回）→ 今天先去公园")
                return self.vacation_eve_park(st)
            floor = target - (PARK_PICNIC_STAMINA - WORK_STAMINA_COST)
        else:
            if stamina < target:
                self.log_info(
                    f"海边前周末：周日体力 {stamina} < {target} → 今天先补体力")
                return self.vacation_eve_park(st)
            floor = target + WORK_STAMINA_COST
        if stamina < floor:
            kept = [s for s in steps if s.place != "餐厅"]
            if not kept:
                self.log_info(
                    f"海边前周末：体力 {stamina} < {floor}，打工后到不了"
                    f" {target} → 改去公园")
                return self.vacation_eve_park(st)
            self.log_info(
                f"海边前周末：体力 {stamina} < {floor}，今天不去餐厅打工"
                f"（还剩 {'、'.join(s.place for s in kept)}）")
            return kept
        return steps

    def vacation_eve_park(self, st):
        """今天只去公园；公园今天已经去过就返回空计划（当天结束）。"""
        if "公园" in (st.get("visited") or set()):
            return []
        return [WeekendStep("公园", False)]

    # ---------- 地点名的框 ----------

    def _venue_box(self, texts, place):
        """地点名对应的文本框（整词优先，其次 ≤4 字的短框）。"""
        box = self.exact_text(texts, (place,))
        if box is not None:
            return box
        return next((t for t in texts
                     if place in t.name and len(t.name.strip()) <= 4), None)

    def venue_has_urgent(self, texts, place="餐厅"):
        """地点名字左侧有没有橙色「急招！」标签。

        2026-11-12 实拍（1112-002429_handle_weekend.jpg）：标签在餐厅名字左侧、
        同一水平带内（Δcy ≈ 0.06 屏高）。几何跟 `venue_on_discount` 一致，
        只是关键字换成「急招」；标签必须在名字左边，别把台词里的「急招」算进来。
        """
        if not texts:
            return False
        box = self._venue_box(texts, place)
        if box is None:
            return False
        cx = box.x + box.width / 2
        cy = box.y + box.height / 2
        for t in texts or []:
            name = t.name.replace(" ", "")
            if "急招" not in name:
                continue
            tcx = t.x + t.width / 2
            tcy = t.y + t.height / 2
            if tcx < cx and abs(tcy - cy) <= self.height * 0.12:
                return True
        return False
