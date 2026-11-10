"""一天的周期：新一天的**两个入口** + 全局日期。

用户 2026-11-01 定下的架构（见 `docs/ARCHITECTURE.md`）：

    **一天只有一个标识：顶栏的「第N周 星期X」。**
    游戏里「打工 / 消费 / 划船 / 买扭蛋」只要发生交互就过一天，所以
    日期变了就是新的一天，没变就是同一天 —— 不需要另外维护「今天过没过」。

    新一天有**两个入口**，它们是同级的：
      ① 训练日主界面（右下「训练 / 休息」）
      ② 出行前跟队长说话的界面（「出行」按钮所在的那页）

    这两个入口放在**同一个模块**（本文件），全局日期也写在这里 ——
    其他模块不自己读顶栏，只向这里要日期。

为什么不能各模块自己读顶栏（2026-11-01 的真实 bug）：

    `top_bar()` 会被 20 多个地方调用，而同一帧里 OCR 可能读到不同的值。
    `weekend_state()` 原来按「日期键变了就重建整个状态」，
    于是顶栏抖一帧 → `visited` / `gacha_spent` 全被清空 →
    当天「已经买过扭蛋」的事实丢了，又要去餐厅打工一次。

所以本模块提供**唯一的**日期读取入口，并且**每帧只读一次**。
"""

import time

from src.tasks.ui import Defs, labels
from src.tasks.training.constants import (SHOP_ENTRY_TRIES,
                                          STYLE_BY_NAME)


class DayCycleMixin:
    """全局日期 + 一天的周期状态。

    状态（按「第N周 + 星期X」重置）都挂在这里，别的模块通过
    `day_state()` 拿，**不直接碰** `self._weekend`。
    """

    # ---------- 全局日期（每帧只读一次） ----------

    def current_day(self, texts=None):
        """当前是「第N周 星期X」，返回 (week, day)；读不到返回 (None, None)。

        每帧只读一次：同一帧里 OCR 结果固定，但不同模块各读一次会
        各自触发一次 OCR，可能读到不同的值（斜杠被吃、图标被读成假汉字…）。
        缓存键是**帧对象**，所以下一帧自动重读。

        读不到完整日期键（周或星期缺一）时返回 (None, None) ——
        调用方要当「顶栏 OCR 抖动」处理，**不能**当成「换了一天」。
        """
        frame = getattr(self, "_tick_frame", None)
        cache = getattr(self, "_day_cache", None)
        if isinstance(cache, dict) and cache.get("frame") is frame:
            return cache["value"]
        state = self.top_bar(texts)
        value = (state.get("week"), state.get("day"))
        self._day_cache = {"frame": frame, "value": value}
        return value

    def day_name(self, texts=None):
        """「星期五」这样的星期几；读不到返回 None。"""
        return self.current_day(texts)[1]

    def is_weekend(self, texts=None):
        """今天是周末（六/日）吗？读不到日期时返回 False（不猜）。"""
        return self.current_day(texts)[1] in ("六", "日")

    # ---------- 一天的状态（按日期键重置） ----------

    def day_state(self):
        """今天的共享状态（`visited` / `gacha_spent` / `outing_done` …）。

        **重置的唯一触发条件是日期变了** —— 因为游戏里交互就过天，
        所以「日期没变」=「还在同一天」= 状态不该变。

        2026-11-01 修：原来只要 key 变了就整个重建，顶栏 OCR 抖一帧
        （扭蛋页只读到专注、没读到「第1周 星期五」）就会把当天状态清空。
        现在读不到完整日期键时**沿用旧状态**。
        """
        week, day = self.current_day()
        prev = self._weekend.get("date")
        if week is None or day is None:
            # 顶栏 OCR 抖动，不是「换了一天」→ 沿用旧状态
            if prev is not None:
                self.log_debug(f"顶栏没读到周/星期（{week}/{day}）→ 沿用 {prev} 的当天状态")
            self._ensure_day_state_keys()
            return self._weekend
        key = (week, day)
        if prev != key:
            if prev is not None:
                self.log_info(f"日期键 {prev} → {key}（新的一天，今日行程重置）")
            self._weekend = self._new_day_state(key)
        return self._weekend

    @staticmethod
    def _new_day_state(key):
        """新的一天的状态。"""
        return {
            "date": key,
            "shop_checked": False,
            "bought": False,
            "park_choice": None,
            "visited": set(),
            # 今天在步行街**试过**抽扭蛋（买没买成都算试过）。
            # 地图上按它排除步行街，防止「买不到 → 又点进去」的无限循环。
            "gacha_tried": set(),
            # 今天在步行街抽到过扭蛋（扭蛋流决定要不要再去餐厅打工）
            "gacha_spent": False,
            # 训练日里出现「出行」时先外出（野上泷周五也能出行），一天只做一次
            "outing_done": False,
        }

    def _ensure_day_state_keys(self):
        """补齐 `_weekend` 里缺的键。

        2026-11-01：`_weekend` 初始值只有 4 个键（TrainingTask.__init__），
        其它键要等重建时才补上。顶栏读不到日期时不重建 → 那些键就永远缺，
        `task._weekend["visited"]` 会 KeyError。
        """
        defaults = self._new_day_state(self._weekend.get("date"))
        for k, v in defaults.items():
            if k not in self._weekend:
                self._weekend[k] = v
        return self._weekend

    # ---------- 一天的入口 ①：训练日主界面 ----------

    def training_day_action(self, texts):
        """训练日主界面：今天练还是休？返回 ("训练"/"休息", 理由) 或 (None, 理由)。

        判据都在这里（专注阈值、失败率上限、强制休息…），
        别的地方不重复判「今天该不该休息」。
        """
        state = self.top_bar(texts)
        cur = state.get("stamina")
        day = state.get("day")
        limit = self.rest_threshold(day)
        if self._force_rest:
            self._force_rest = False
            return "休息", "失败率过高，强制休息"
        if cur is None:
            return "训练", f"专注读不到 → 照常训练"
        if cur < limit:
            return "休息", f"专注 {cur} < {limit}（{self.style}·星期{day}）→ 休息"
        return "训练", f"专注 {cur}，进入训练科目"

    # ---------- 一天的入口 ②：出行前（跟队长说话那页） ----------

    def outing_offer(self, texts, block_if_used=True):
        """这一页要不要外出？返回 ("点按钮", 理由) / ("推进对话", 理由) / (None, 理由)。

        **两个入口同级**：训练日主界面和周末首页都能外出，判断放一起。

        `block_if_used`：今天的外出机会用过了还让不让点？
        - 训练日（`handle_training_day`）：用过了就不点（野上泷周五的**额外**外出，一天一次）；
        - 周末首页（`handle_go_out`）：**不**拦（周末出行是主流程，不是额外机会）。

        2026-10-31 修正：只认**按钮**，而且点到了才记「今天用过了」。
        旧版 `click_text("出行")` 会把对话框台词「今天可以出行哦！要不」当按钮点
        （官方日志 `left_click 今天可以出行哦！要不 (950, 182)`），按钮一下没点到，
        外出标记却已经置上，下一拍直接进训练（用户：「为什么没有点出行，
        反而默认进了训练」）。

        2026-11-03 再修：「点到了」还不够 —— 点下去也可能**没生效**（页面淡入期、
        按钮被挡）。所以 `mark_outing_used()` 只在 `handle_weekend` **看到地图**时
        调用；点空了下一拍这里仍然返回 "点按钮"，会自动重试
        （`handle_training_day` 用 `OUTING_CLICK_LIMIT` 限次）。
        """
        if self.match_text(texts, "出行") is None:
            return None, "这一页没有「出行」"
        if block_if_used and self.outing_used():
            return None, "今天的额外出行已经用过了"
        button = self.find_go_out_button(texts)
        if button is not None:
            return "点按钮", "有「出行」按钮 → 外出（一天一次）"
        line = self.match_text(texts, "出行")
        if line is not None and len(line.name.strip()) > 4:
            # 按钮还没出来，这一拍只推进对话（不算用掉当天机会）
            return "推进对话", "对话框提到「出行」但还没按钮 → 先推进对话"
        return None, "「出行」按钮认不出来"

    # ---------- 一天的入口 ③：看到「新品上市」才进店（2026-11-08 起不分周数） ----------

    def late_shop_step(self, texts):
        """看到「新品上市」提示就进店（**不再分周数**）。返回 True = 这一拍已经点过入口。

        用户 2026-11-08 口径（**取代** 2026-11-05 的「第 7 周以后才进」）：
        **只要有「新品上市」的标签就进商店** —— 标签挂在右上角「商店」按钮
        **正上方**（2026-11-05 截图：第 3 周 星期一 比赛日主界面）。进去以后
        第 7 周之前只买 0 元商品，第 7 周以后按 红包 → 彩 → 金 → 紫 买
        （购买口径在 `places/shop.py: pick_shop_item`）。

        它和上面「训练/休息」「出行」是**同级的一天入口**，判断就写在这里
        （本模块管「一天」，不要在每个 handler 里各判一次）。调用点在 `flow.run`
        主循环里、`_handle_any` 之前 —— 一处盖住训练日主界面、比赛日主界面、
        周末首页三种页面，谁也不会先把手里的这一天用掉。
        """
        week, day = self.current_day(texts)
        week_tag = f"第{week}周" if week is not None else "周数未知"
        if self.shop_page(texts):
            return False                       # 已经在店里 → handle_shop 买/关
        if (self.match_text(texts, "星期") is None
                and self.match_text(texts, "出行") is None):
            return False                       # 不是带日期的主界面（剧情/结算/开局页）
        box = self.find_shop_button(texts)
        if box is None:
            return False
        # 入口必须在右上角 shop_entry 那块里：`find_shop_button` 的整屏兜底会返回
        # 台词里的「商店」（「那家商店早上六点会卖打折商品」），那个不能点。
        x1, y1, x2, y2 = Defs.region("shop_entry")
        cx = (box.x + box.width / 2) / self.width
        cy = (box.y + box.height / 2) / self.height
        if not (x1 - 0.06 <= cx <= x2 and y1 <= cy <= y2):
            return False
        # 判据：商店按钮**正上方**有「新品上市」提示（`places/shop.py: shop_fresh_badge`）。
        # 没提示 = 没上新 = 今天不进店。
        key = (week, day)
        badge, why = self.shop_fresh_badge(texts, box)
        if badge is None:
            # 一天只打一行 INFO（DEBUG 没人翻；每拍打一条又会把日志刷满）
            if key != getattr(self, "_shop_nobadge_key", None):
                self._shop_nobadge_key = key
                self.log_info(f"{week_tag} 星期{day or '?'}：商店上方没有「新品上市」提示"
                              f"（{why}）→ 今天不进店")
            return False
        if key == getattr(self, "_shop_day", None):
            return False                       # 今天已经真进过店（handle_shop 记的账）
        if day is None:
            # 星期读不到：退化成「至少隔 60 秒再进一次」，别整天反复点
            if time.monotonic() - getattr(self, "_shop_at", 0.0) < 60:
                return False
            tries = 1
        else:
            if (key == getattr(self, "_shop_try_key", None)
                    and getattr(self, "_shop_try_count", 0) >= SHOP_ENTRY_TRIES):
                return False                   # 今天点了两次都没打开 → 别空点
            tries = (getattr(self, "_shop_try_count", 0) + 1
                     if key == getattr(self, "_shop_try_key", None) else 1)
        self._shop_try_key, self._shop_try_count = key, tries
        self._shop_at = time.monotonic()
        self.click_box(box, after_sleep=0.9)
        self.log_info(f"{week_tag}：看到「{badge.name.strip()}」→ 进店"
                      f"（按「{self.config.get('商店购买策略')}」买｜星期{day or '?'}"
                      f"｜第 {tries}/{SHOP_ENTRY_TRIES} 次点）")
        return True

    # ---------- 队长 / 流派：两条解析路径 ----------

    # 「认不出队长」时的弹窗标题
    CAPTAIN_ALERT_TITLE = "认不出队长"

    def resolve_style_by_config(self):
        """路径 A：配置里填了「指定队长」→ 查表定流派。

        返回 True = 定下来了；False = 认不出（调用方弹窗，**不许保底**）。

        2026-11-01 用户口径：
          - 配置里有队长 → 查表定流派，找得到 √，找不到**弹窗**；
          - 配置为空 → 走路径 B（回训练室/校门口点日历）。

        注意这里**不再**区分「是不是大厅开的局」——
        旧逻辑只在 `is_lobby()` 时套用配置队长，于是**接手的局**如果当前不是训练日
        （日历只在训练日弹），就永远读不到队长，`_style` 一直是空的
        → `style` 兜底「训练流」，周末计划全走错（实跑 2026-11-01 19:19：
        扭蛋流跑成了训练流）。
        """
        name = self.configured_captain()
        if not name:
            return False                    # 没配置 → 走路径 B
        style = self.style_for_name(name)
        if not style:
            self.log_warning(
                f"配置的队长「{name}」不在角色表（STYLE_BY_NAME）→ 认不出流派")
            return False
        self._style = style
        self._style_name = name
        self.log_info(f"流派：{style}（配置指定队长「{name}」，跳过日历）")
        return True

    def resolve_style_by_calendar(self):
        """路径 B：回训练室/校门口 → 点日历 → 抓队长名 → 查表定流派。

        返回 True = 定下来了；False = 认不出（调用方弹窗，**不许保底**）。

        2026-11-01 用户口径：
        「接手的时候首先就是要点多次返回，回到训练日的训练室或者休息日的校门口，
        以抓取日期。所以：如果是接手训练，返回带日期的界面，点日历，抓取名字，
        识别队长和流派，识别到了 √，识别不到（弹窗）。」

        为什么必须「返回到带日期的界面」：
        日历页**自己不带日期**（实测 111 帧里只有 3 帧带），
        所以要先回到训练室/校门口（顶栏有「第N周 星期X」）才有日期。
        """
        name = self.probe_captain_from_calendar()
        if not name:
            return False
        style = self.style_for_name(name)
        if not style:
            self.log_warning(
                f"日历上的队长「{name}」不在角色表（STYLE_BY_NAME）→ 认不出流派")
            return False
        self._style = style
        self._style_name = name
        self.log_info(f"流派：{style}（日历队长「{name}」）")
        return True

    def probe_captain_from_calendar(self, texts=None):
        """读日历页左上角的队长名字。读不到返回 None。

        `texts` 不给就用本拍的 OCR 结果（`_ocr_region()`）。

        这一步**不点返回**、也不做别的动作 —— 只负责把日历打开、名字读出来。
        打开日历/读名字的时机由调用方安排（见 `resolve_style_by_calendar`）。
        """
        # 日历页左上角「XX的训练计划」里的 XX。
        if texts is None:
            texts = self._ocr_region()
        if not self.is_training_plan(texts):
            return None
        return self.read_plan_captain(texts)

    def captain_alert(self):
        """认不出队长/流派 → **弹窗「未能识别到队长」，不许保底**。

        用户 2026-11-01 口径：「两种情况都不允许保底。」
        之前是静默兜底「训练流」，结果扭蛋流跑成训练流、周末计划全走错，
        而且日志上看不出来 —— 这比直接停下更糟。

        `alert()` 用 MessageBoxW，**模态阻塞** —— 用户不点掉，任务线程就停在这里。
        这正是想要的：这种错误不许自己往下走。
        """
        self.log_error("未能识别到队长", notify=True)
        self.alert(self.CAPTAIN_ALERT_TITLE, "未能识别到队长")
        return False

    # ---------- 路径 B 的多拍状态机 ----------

    # 「返回」最多点几次（兜底，防止点不动时无限循环）。
    # 实测：周末地图点一次右上角 ↩ 就回到校门口；地点内页要先退出详情弹窗、
    # 再退出地点，可能要 2~3 下。
    CAPTAIN_RETURN_MAX = 8

    def style_pending(self):
        """还在等识别队长吗？"""
        return bool(getattr(self, "_style_pending", False))

    def _resolve_style_step(self, texts):
        """路径 B 的一步。返回 True = 这一拍处理完了（别再进主循环的其它逻辑）。

        状态机（用户 2026-11-01 口径）：
            1. **接手训练以后主动识别返回，一直到回到带日历的页面**；
            2. 点日历 → 抓队长名字 → 查表定流派；
               认到了 √，认不到 → **弹窗「未能识别到队长」**。

        「带日历的页面」怎么认？
        不靠文字（校门口和训练室的文字完全不同，地点内页也带日期），
        靠**行为**：点左上角 → 下一帧是不是日历页。
        """
        state = getattr(self, "_style_probe_state", None) or "返回"

        # 整页公告（游戏刚启动）会盖住一切：先关掉，否则下面点「返回」永远没反应。
        if self.is_announcement(texts):
            return self.handle_announcement(texts)

        if state == "返回":
            # 先确认这一页是不是已经能点日历了（训练室/校门口）
            if self._looks_like_calendar_host(texts):
                self._style_probe_state = "点日历"
                self.log_info("已回到带日历的页面 → 点左上角日历")
                return True
            tries = getattr(self, "_style_return_tries", 0)
            if tries >= self.CAPTAIN_RETURN_MAX:
                self._style_pending = False
                return self.captain_alert()
            self._style_return_tries = tries + 1
            # 点右上角的返回箭头（系统级固定位置）
            self.click_back(after_sleep=0.6)
            self.log_info(f"队长识别：点返回（第 {self._style_return_tries} 次）")
            return True

        if state == "点日历":
            self.click_calendar(after_sleep=0.8)
            self._style_probe_state = "抓名字"
            return True

        if state == "抓名字":
            name = self.probe_captain_from_calendar(texts)
            if name:
                self._style_pending = False
                self._style_probe_state = None
                if self.resolve_style_by_calendar_name(name):
                    return True
                return self.captain_alert()
            # 日历页还没出来/名字读不到 → 再点一次日历（限流）
            tries = getattr(self, "_style_calendar_tries", 0) + 1
            self._style_calendar_tries = tries
            if tries >= 3:
                self._style_pending = False
                return self.captain_alert()
            self.click_calendar(after_sleep=0.8)
            return True

        return False

    def _looks_like_calendar_host(self, texts):
        """这一页是不是「左上角能点开日历」的那一页（训练室/校门口）？

        判据：这一页**有日期**（顶栏「第N周 星期X」），
        而且**不是**某个地点的内页/地图（那些也带日期，但左上角不是日历）。

        为什么不用「有休息」：`handle_style_probe` 原来要求「星期」+「休息」，
        那是训练室专属 —— **校门口没有「休息」**，于是接手的局在周末
        永远点不了日历（2026-11-01 实测：周末地图上 handle_style_probe 返回 False）。
        """
        state = self.top_bar(texts)
        if not state.get("week") or not state.get("day"):
            return False
        # 地点内页 / 地图：都有地点名（地图上是好几个）
        places = [t for t in texts
                  if t.name.strip() in labels.WEEKEND_PLACES]
        if places:
            return False
        # 日历页本身也不该点日历（再点就关了）—— 它有「的训练计划」标题
        if self.is_training_plan(texts):
            return False
        return True

    def resolve_style_by_calendar_name(self, name):
        """已经拿到队长名字 → 查表定流派。"""
        style = self.style_for_name(name)
        if not style:
            return False
        self._style = style
        self._style_name = name
        self.log_info(f"流派：{style}（日历队长「{name}」）")
        return True

    def outing_used(self):
        """今天的额外出行机会用过了吗？"""
        return bool(self.day_state().get("outing_done"))

    def mark_outing_used(self):
        """标记今天的额外出行机会已用。

        **置位点只有一个：真的看到地图的那一拍**（`weekend.handle_weekend`）。
        2026-11-03 之前是在 `handle_training_day` 点完「出行」就置位，于是
        点空了也算用过 —— 周五（野上泷额外周末）当天再也进不去地图：
        `handle_go_out` 对非周末的 `outing_done` 是直接 `exit_page()`。
        改成「进门才算」以后，点空了下一拍还能重试。
        """
        self.day_state()["outing_done"] = True

