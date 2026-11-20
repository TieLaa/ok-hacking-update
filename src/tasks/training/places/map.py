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
    PARK_PICNIC_STAMINA, SHOP_SEASON_WEEKS,
    WEEKEND_PARK_GAP, WORK_STAMINA_COST)
from src.tasks.training.strategy import styles
from src.tasks.training.strategy.context import VenueContext, WeekendContext
from src.tasks.training.places import street
from src.tasks.ui import labels

# 周末计划的一步：place=地点名，urgent=True=急招临时插队（不是今天的主计划），
# probe=True=探卡池（所有流派出行日的第一步：只去步行街找笔记继承/球员位置，
# 买到一张后当天转正常购买逻辑，见 `handle_street`）。
WeekendStep = namedtuple(
    "WeekendStep", "place urgent probe", defaults=(False,))

# 一次地图决策的只读上下文。钱/精力等易变值**不存进 DayState**（2026-11-15
# 用户口径：实例只放一天最多变一次的值），每次决策从同一份 texts 读一次装这里。
MapContext = namedtuple(
    "MapContext",
    "style money urgent street_discount visited gacha_spent "
    "final_outing discount_worth places")

# 一条改道规则：place/urgent = 改道到哪、是不是临时插队；when 读 MapContext。
Detour = namedtuple("Detour", "place urgent when")

# 地图事件改道规则：**只写会改道的**，没写 = 不改道。
#   place/urgent = 改道到哪、是不是临时插队
#   when         = 条件（只读 MapContext，不碰 OCR/状态）
# 训练流、商店流、扭蛋增强、划船比赛、地狱游戏、每周补助都不在这张表里。
MAP_DETOURS = (
    # 急招：主计划不是餐厅时，先去餐厅赚双倍。
    # 第 7 周周六是最后一次出行 → 不插队（2026-11-14 口径）。
    Detour("餐厅", True, lambda c: c.urgent and not c.final_outing),
    # 步行街打折：钱不够 360、但折后够 → 先去消费（错过就是实打实的损失）。
    Detour("步行街", False, lambda c: c.discount_worth),
)


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

    # ---------- 今天去哪几个地点 ----------

    def today_places(self, day=None, st=None, texts=None):
        """今天该去的地点（有序，前一个走完才轮到下一个）。

        判据来自 `strategy/styles.py` 的 `weekend_places`：
        - 扭蛋流 / 小游戏流：主地点 + 餐厅（与星期无关，2026-11-10 口径）；
          买到 / 玩完当天收工；
        - 商店流：餐厅；
        - 训练流：按精力缺口 + 星期 + 第 7 周（`TrainingStyle.weekend_places`）；
          缺口读不到时回退「餐厅 → 公园」。

        这里只做三件**所有流派相同**的事：第 7 周周日不出行、两个场所开关过滤、
        开关把地点删空时按体力缺口二选一。
        """
        if st is None:
            st = self.day_state()
        if day is None:
            day = (st.date or (None, None))[1]
        # 读不到星期时按周六兜底（训练流的「周六先看餐厅」和 prefer_venue 的分档要用）。
        day = day or "六"
        # 2026-11-14 用户口径（游戏设置，唯一策略）：第 7 周周日不能再出行，
        # 只能进商店清空零花钱。所有流派都不规划地点。
        week_now, day_now = self.current_day()
        if week_now == SHOP_SEASON_WEEKS and day_now == "日":
            self.log_info("第7周星期日：不能再出行 → 只进商店")
            return []
        bar = self.top_bar(texts)
        ctx = WeekendContext(
            style=self.style,
            week=week_now,
            day=day,
            money=self.read_money(texts) if texts is not None else None,
            stamina=bar.get("stamina"),
            stamina_max=bar.get("stamina_max"),
            visited=frozenset(st.visited),
            gacha_spent=st.gacha_spent,
            row_gap=int(self.config.get("训练流-周末划船缺口")),
        )
        places, why, final = styles.style_spec(self.style).weekend_places(ctx)
        if places is None:
            self.log_info(why)
            return ["餐厅", "公园"]
        if why:
            self.log_info(why)
        if final:
            return places
        places = [p for p in places if p != "商店"]
        # 今天本来有没有「开关控制的地点」（步行街 / 游戏厅）。
        # 只有这种日子才可能被两个开关删空 —— 周日训练流只有公园，不算，
        # 那两个开关跟它没关系。
        had_venues = any(p in ("步行街", "游戏厅") for p in places)
        if not self.config.get("小游戏流-允许进游戏厅"):
            places = [p for p in places if p != "游戏厅"]
        if not self.config.get("允许进步行街"):
            places = [p for p in places if p != "步行街"]
        # 2026-11-10 用户口径：开关把当天地点**全关掉**时按**体力缺口**二选一 ——
        #   缺口（上限−当前）> WEEKEND_PARK_GAP → 公园
        #   缺口 ≤ WEEKEND_PARK_GAP → 餐厅打工
        # 阈值 60 取自「两次划船合计 +60」。周日不一定划船：精力 < 周末野餐
        # 缺口时是野餐（+70），那两天实际回 100 —— 60 是分界，不是回收量的保证。
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
        先看零用钱，队友和星期都无关 ——
          - 读不到 → 主地点（不拿打工赌今天的消费）；
          - < `weekend.work_money_gate`（360）→ **只去餐厅打工**；
          - ≥ 360 → 主地点。
        事件改道（步行街打折 / 餐厅急招）**不在这里**，统一走
        `event_detour`（2026-11-15 收口）：这里只算「钱够不够」的基准。
        理由（一次步行街期望 3.3 颗、4 颗金 = 360 覆盖 78% 的触发轮次）见
        `strategy/params.json` 的 `weekend._doc`。2026-11-13 试过 200，
        但折扣不是每次发生，没折扣时只够买 2 颗，已回滚 360。
        180 只在**地图确认今天打折**时由事件层生效，不是无条件降门槛。

        **训练流 / 商店流**（主地点为空）**不经过这里**：原样返回候选，
        由 `today_places` 给的两个地点决定顺序。

        队友气泡（`venue_has_mate`）**不参与任何一条**（2026-11-10 用户口径）：
        它看不到品质（带队友的可能是根本不买的蓝卡），也看不到刷新后的批次
        （买一颗刷一批，下一批才冒队友），对「去哪儿」没有预测力，只当添头。

        打折判据见 venue_on_discount（OCR 文本 + 名字左侧）。
        """
        # 读不到星期时按周六兜底（第 7 周周六分档要用）。
        day = day or "六"
        ctx = VenueContext(
            style=self.style,
            day=day,
            week=self.current_day()[0],
            money=self.read_money(texts) if texts is not None else None,
            places=tuple(places),
        )
        ordered, why = styles.style_spec(self.style).prefer_venue(ctx)
        if why:
            self.log_info(why)
        return ordered

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

        2026-11-15：急招/打折从本方法里抽出来，统一走 `event_detour`；
        这里只算「基准计划」，顺序固定为 基准 → 事件 → 海边守卫。
        """
        if st is None:
            st = self.day_state()
        if day is None:
            day = (st.date or (None, None))[1]
        visited = st.visited
        tried = st.gacha_tried

        # 先按原逻辑过滤「今天已经去过/试过」的地点，再算主计划。
        places = self.today_places(day, st, texts)
        places = [p for p in places if p not in visited]
        if "步行街" in tried:
            places = [p for p in places if p != "步行街"]
        main = self.prefer_venue(texts, places, day)
        steps = [WeekendStep(p, False) for p in main]
        steps = self.event_detour(st, texts, steps, places)
        steps = self.vacation_eve_guard(steps, texts, st)
        return self.gacha_probe(steps, st)

    def gacha_probe(self, steps, st=None):
        """所有流派的出行日：第一步先去步行街找「笔记继承 / 球员位置」。

        用户 2026-11-15 口径：
        - 不管什么流派、什么零用钱，只要是出行日就先看一眼；
        - 没有这两张就退出，回地图走原计划（`handle_street` 进门即
          `mark_tried`，今天只探一次）；
        - 这趟只买这两张的**彩色**版本（`street.gacha_candidates(allow_others=False)`）；
          买到一张后当天转正常购买逻辑（`handle_street`，2026-11-17）。

        例外只有三种（都是游戏/开关的硬限制）：
        - **第7周周日不能出行**：`today_places` 已返回空计划，这里不插；
        - `允许进步行街` 关了；
        - 今天已经探过一次（`gacha_tried` / `visited`）。

        第 3 周海边前周末也照探（用户 2026-11-15 补充口径：这是最高优先级，
        不看体力）——买到就花掉一天，没买到退出继续原计划。

        2026-11-17 用户口径：两张目标卡都到手后**不再探**（`top_gacha_got`）。

        计划里已经有步行街（扭蛋流钱够 / 打折改道）时不插：
        正常模式照旧买最好的卡，这两张已经靠排序键排第一。
        """
        if not steps:
            return steps
        if not self.config.get("允许进步行街"):
            return steps
        if st is None:
            st = self.day_state()
        if "步行街" in st.gacha_tried or "步行街" in st.visited:
            return steps
        if any(s.place == "步行街" for s in steps):
            return steps
        if set(street.TOP_GACHA_NAMES) <= self.top_gacha_got():
            return steps
        self.log_info("周末：先去步行街找「笔记继承/球员位置」")
        return [WeekendStep("步行街", False, True)] + steps

    # ---------- 事件层：地图标签 → 要不要改道 ----------

    def map_context(self, st, texts, places):
        """一次决策的只读上下文；钱/精力只读一次，不写回 `DayState`。

        为什么不把 money 存进实例（2026-11-15 用户口径）：它每拍都可能变，
        存进去就要跟着 OCR 校准、还要把每个扣减点接进来，架构更重。
        """
        money = self.read_money(texts)
        work_gate = int(params.weekend.work_money_gate)
        discount_gate = int(params.weekend.discount_money_gate)
        street_discount = bool(
            self.venue_on_discount(texts, self._venue_box(texts, "步行街")))
        week, day = st.date or (None, None)
        return MapContext(
            style=st.style,
            money=money,
            urgent=self.venue_has_urgent(texts, "餐厅"),
            street_discount=street_discount,
            visited=st.visited,
            gacha_spent=st.gacha_spent,
            # 第 7 周周六是最后一次出行：事件不许插队
            final_outing=(week == SHOP_SEASON_WEEKS and day == "六"),
            # 扭蛋流 + 步行街自己的「打折」+ 钱在折后门槛到原门槛之间
            discount_worth=(styles.style_spec(st.style).discount_bonus
                            and street_discount
                            and money is not None
                            and discount_gate <= money < work_gate),
            places=tuple(places),
        )

    @staticmethod
    def _detour_hits(rule, ctx, steps):
        """这条规则今天成立吗？守卫都在这里，规则本身只写条件。

        - `urgent=True`（急招）是**插队**：目标可以在今天的候选之外
          （训练流缺公园也能先去餐厅），只要求没去过；
        - `urgent=False`（打折）是**替首选**：目标必须在今天的候选里，
          否则「改去步行街」会让当天计划凭空多出一个没规划的地点。
        """
        if not steps or steps[0].place == rule.place:
            return False
        if rule.place in ctx.visited:
            return False
        if not rule.urgent and rule.place not in ctx.places:
            return False
        # 扭蛋流今天已经买到扭蛋 → 不再去餐厅打工
        if (rule.place == "餐厅" and ctx.gacha_spent
                and styles.style_spec(ctx.style).ends_day_after_venue):
            return False
        return bool(rule.when(ctx))

    def event_detour(self, st, texts, steps, places):
        """地图事件改道：只处理 `MAP_DETOURS` 里写了的规则。

        没写的标签（扭蛋增强 / 划船比赛 / 地狱游戏 / 每周补助）**默认不改道**，
        所以不需要在表里写一堆 False。规则按顺序试，第一条命中就返回。
        """
        if not steps:
            return steps
        ctx = self.map_context(st, texts, places)
        for rule in MAP_DETOURS:
            if not self._detour_hits(rule, ctx, steps):
                continue
            why = "急招" if rule.urgent else "打折"
            self.log_info(
                f"周末：{why} → 改去「{rule.place}」（原首选 {steps[0].place}）")
            return ([WeekendStep(rule.place, rule.urgent)]
                    + [s for s in steps if s.place != rule.place])
        return steps

    # ---------- 海边前周末：把体力拉回目标（第3周） ----------

    def vacation_eve_target(self, st=None):
        """第3周周末的体力目标；不是这一周就返回 None。

        用户 2026-11-13 口径：第4周海边合宿第4天是**全队友参与**的训练
        （玩家等级提升给的增益），体力不够就得休息、错过这一天。
        所以第3周周日结束时必须 ≥ 目标值（`strategy/params.json`）。
        """
        if st is None:
            st = self.day_state()
        week, day = st.date or (None, None)
        if week != 3 or day not in ("六", "日"):
            return None
        return int(params.vacation.stamina_target)

    def vacation_eve_guard(self, steps, texts, st=None):
        """第3周周末：体力不够时把打工换成公园，保证周日结束 ≥ 目标。

        算法（划船 +30 / 野餐 +70 / 打工 −25）：

            周六：今天还能靠周日补一次
                < 目标−70 → 今天必须先补（原计划有步行街则保留，公园补）
                < 目标−45 → 今天不打工（打工后周日一次野餐也补不回）
                ≥ 目标−45 → 原计划（打工后周日野餐兜底）
            周日：今天就是最后一次机会
                < 目标    → 补体力（原计划有步行街则保留，公园补）
                < 目标+25 → 今天不打工（打完就掉到目标以下）
                ≥ 目标+25 → 原计划

        体力读不到时不赌：今天只去公园。计划本来为空（地点都去过）时不插手，
        避免「公园已去过 → 再点一次」打转。
        """
        if not steps:
            return steps
        if st is None:
            st = self.day_state()
        target = self.vacation_eve_target(st)
        if target is None:
            return steps
        day = st.date[1]
        stamina = self.top_bar(texts).get("stamina")
        if stamina is None:
            self.log_info("海边前周末：读不到体力 → 今天只去公园")
            return self.vacation_eve_park(st)
        if day == "六":
            must_recover = target - PARK_PICNIC_STAMINA
            if stamina < must_recover:
                kept = self._keep_planned_street(steps, st)
                if kept is not None:
                    self.log_info(
                        f"海边前周末：周六体力 {stamina} < {must_recover}"
                        "（周日一次野餐也补不回）→ 保留步行街，公园补体力")
                    return kept
                self.log_info(
                    f"海边前周末：周六体力 {stamina} < {must_recover}"
                    "（周日一次野餐也补不回）→ 今天先去公园")
                return self.vacation_eve_park(st)
            floor = target - (PARK_PICNIC_STAMINA - WORK_STAMINA_COST)
        else:
            if stamina < target:
                kept = self._keep_planned_street(steps, st)
                if kept is not None:
                    self.log_info(
                        f"海边前周末：周日体力 {stamina} < {target}"
                        " → 保留步行街（正常购买），公园补体力")
                    return kept
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

    def _keep_planned_street(self, steps, st):
        """体力不够、原计划本来就要去步行街：保留它，公园补体力。

        步行街不耗体力；正常模式本来就会优先买彩卡
        （`street.gacha_candidates` 的排序键），所以不归探卡池管
        （2026-11-17 用户口径）。补体力日不去餐厅（打工 −25 会毁掉目标）。
        公园已经去过（补不回来）返回 None，交给原逻辑结束当天。
        """
        if not any(s.place == "步行街" for s in steps):
            return None
        park = self.vacation_eve_park(st)
        if not park:
            return None
        kept = [s for s in steps if s.place not in ("餐厅", "公园")]
        return kept + park

    def vacation_eve_park(self, st):
        """今天只去公园；公园今天已经去过就返回空计划（当天结束）。"""
        if "公园" in st.visited:
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
        return self._venue_label_left(texts, place, ("急招",))

    def _venue_label_left(self, texts, place, keywords):
        """地点名字左侧、同一水平带内有没有某个标签。"""
        if not texts:
            return False
        box = self._venue_box(texts, place)
        if box is None:
            return False
        cx = box.x + box.width / 2
        cy = box.y + box.height / 2
        for t in texts or []:
            name = t.name.replace(" ", "")
            if not any(k in name for k in keywords):
                continue
            tcx = t.x + t.width / 2
            tcy = t.y + t.height / 2
            if tcx < cx and abs(tcy - cy) <= self.height * 0.12:
                return True
        return False
