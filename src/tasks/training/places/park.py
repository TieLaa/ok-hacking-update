"""公园（散步 / 划船 / 野餐）：**地点模块**。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：
每个地点有自己的模块，只管自己的判断。

公园的三个选项（`labels.PARK_OPTIONS`）：

    野餐 = 精力++   划船 = 属性+   散步 = 零用钱+

实跑数值（用户 2026-11-10 确认，长期有效，代码里**不使用**这两个数，
只按界面标签选；记在这里是为了别再到处问「回多少」）：

    划船：精力 +30，每多一个队友额外 +10 属性
    野餐：精力 +70，每多一个队友额外 +20 精力

（服务员打工 −25 精力，登记在 `places/restaurant.py`。）

选哪个（用户 2026-10-30 / 2026-11-01 / 2026-11-08 / 2026-11-11）：

- **训练流（2026-11-11 起）**：按**精力缺口**选，队友不参与 ——
  缺口（上限−当前）≥ `周末野餐缺口`（默认 70）→ 野餐；
  缺口 < 70 → 划船（属性+）。划船比野餐少回的精力拿去训练，
  收益高于划船那点属性，所以缺口大时野餐优先，不被「划船队友多 2」改走。
  缺口读不到时才回退旧行为（周六划船 / 周日看精力）。
- **其它流派：队友优先**（2026-11-08 用户口径，和餐厅「桌游GM 多两个就选左边」同一口径）：
  **划船的队友比野餐多 `MATE_MARGIN`（2）个及以上 → 优先划船**，不看精力。
  多 1 个不算，仍然是默认规则说了算。只在**默认选到野餐**时才由队友改判 ——
  配置里明确要「散步」的照旧散步。
- **其它流派**：用配置项 `公园选项` 指定的优先项（默认野餐）。

队友数怎么数：公园三个圆形图标头顶的「深蓝胶囊 + chibi 头像」和餐厅两卡**是同一套**
视觉，所以判据只在 `parsers.option_mate_counts` 写一份（先数脸，整页一张脸都没有
才退回深蓝兜底）。真实帧 `docs/shots/park_boat_mates.png`：划船 2~3 人、野餐 0 人。

**数不到就当平局、按默认规则走**（有意降级）：肤色判据挑的是「亮暖肤色」，
`docs/shots/2026-10-26-park.png` 那种偏暗/滤色重的帧两张都数到 0 —— 那时宁可选错
（走周六划船 / 周日看精力），也不要拿深蓝兜底去瞎加人（那个坑餐厅已经踩过一次，
见 `places/restaurant.py: card_mate_counts`）。

2026-10-26 踩过的坑（写在 docstring 里，避免又被改坏）：

    实机是三个**圆形图标**按钮，名字和效果都在图标下方（「划船 属性+」），
    页顶有「在公园干点什么好呢?」。旧判据 `labels.PARK_TITLE` 依赖
    「在公园」三个字，但实际文案是整句 —— 整句 OCR 时「在公园」不是独立框，
    match_text 的子串匹配靠"整段拼接"兜底才能命中，命中后返回的是**最长的框**
    （整句话），click_box 会点到句子中间，什么都点不到 → 卡死。

    所以：标题只当**判据**，实际点击永远走选项文字框。
"""

from src.tasks.training.constants import (PARK_BOAT_STAMINA, SHOP_SEASON_WEEKS,
                                          WEEKEND_PICNIC_FALLBACK)
from src.tasks.ui import labels

# 地点名（地图上的按钮文字 / 状态里的键）
PLACE = "公园"

# 退出公园的兜底坐标（右上角返回）。
EXIT_COORDS = (0.945, 0.040)


class ParkPlace:
    """公园的判断与状态。**不点击**，只给判断。"""

    # 划船的队友比野餐多几个人才改选（用户 2026-11-08 口径：多两个；
    # 与 `RestaurantPlace.MATE_MARGIN` 同一口径，「差 1 个不算」）。
    MATE_MARGIN = 2

    # ---------- 页面判据 ----------

    def park_page(self, texts):
        """这是不是公园内页？

        地图页/周末首页上有「出行」按钮，公园内页没有 —— 必须排除，
        否则地图页的对白「小澪奈总是吵着要去野餐…」会命中 `PARK_TITLE`
        里的「野餐」→ 把地图判成公园内页 → mark_visited("公园")
        → 反复点返回循环 9 拍，任务被判卡死停止（2026-10-27 事故）。
        """
        if self.match_text(texts, "出行") is not None:
            return False
        return self.match_text(texts, labels.PARK_TITLE) is not None

    # ---------- 今天选哪个（纯判断） ----------

    def park_order(self, texts, style, day=None):
        """今天点哪个公园选项？返回选项名的元组（第一个是要点的）。

        `day` = 星期几（"六"/"日"/…）。读不到时按「非周日」处理（划船）。
        """
        pick = self.park_pick(texts, style, day)
        return (pick,) + tuple(x for x in labels.PARK_OPTIONS if x != pick)

    def park_pick(self, texts, style, day=None):
        """今天选哪个选项（选项名）。

        2026-11-11 用户口径：**训练流按缺口选**，队友改判不再抢野餐 ——
        划船比野餐少回的精力拿去训练，收益高于划船那点属性：

            缺口（上限−当前）≥ 周末野餐缺口（默认 70） → 野餐
            缺口 < 周末野餐缺口                          → 划船

        其它流派保持 2026-11-08 的「划船队友多 2 个 → 划船」。

        第3周周末例外（用户 2026-11-13）：第4周海边合宿的最后一天是
        全队友训练，前一天体力不够就得休息，所以公园按「离目标还差多少」
        选 —— 差 > 划船 +30 才野餐，否则划船（队友和配置都不抢）。

        2026-11-15 实跑修正：「划船比赛！」只是**添头** —— 实测它只把
        「推荐属性+20 / 随机属性+10」换成「推荐属性+40」，精力、零用钱、
        出行次数都不变，所以**不压训练流缺口**（缺口 ≥70 照样野餐：
        为 10 点属性丢 40 点精力不划算），只在「划船 vs 野餐」摇摆时偏向划船。
        """
        # 第3周周末：海边前要把体力拉回目标，野餐/划船只按缺口选，
        # 队友和配置都不抢（`vacation_eve_target` 不是这一周时返回 None）。
        target = self.vacation_eve_target()
        if target is not None:
            stamina = self.top_bar(texts).get("stamina")
            if stamina is not None:
                gap = target - stamina
                if gap > PARK_BOAT_STAMINA:
                    self.log_info(
                        f"海边前周末：体力 {stamina} 距目标 {target} 差 {gap}"
                        f"（> 划船 {PARK_BOAT_STAMINA}）→ 野餐")
                    return "野餐"
                self.log_info(
                    f"海边前周末：体力 {stamina} 距目标 {target} 差 {gap} → 划船")
                return "划船"

        # 2026-11-15 用户口径（收窄）：最后一周没有训练日了，精力不再兑现 → 划船拿属性。
        week = self.top_bar(texts).get("week")
        if week is not None and week >= SHOP_SEASON_WEEKS:
            self.log_info("公园：最后一周没有训练日，精力不再兑现 → 划船（属性+）")
            return "划船"

        if style == "训练流":
            gap = self.weekend_gap(texts)
            if gap is not None:
                threshold = int(self.config.get("周末野餐缺口"))
                if gap >= threshold:
                    self.log_info(f"公园：缺口 {gap} ≥ {threshold} → 野餐补精力")
                    return "野餐"
                self.log_info(f"公园：缺口 {gap} < {threshold} → 划船（属性+）")
                return "划船"
            # 缺口读不到：回退旧行为（周六划船 / 周日看精力）
            return self.park_default_pick(texts, style, day)

        pick = self.park_default_pick(texts, style, day)
        if pick != "野餐":
            return pick
        # 2026-11-15 实跑修正：「划船比赛！」是**添头**，不是改道信号。
        # 实测（第1周周六 vs 周日两帧）：比赛只把「推荐属性+20/+10」换成「推荐属性+40」，
        # 精力 +30、零用钱、出行次数与普通划船完全相同 —— 多赚 10 点属性，
        # 换不回野餐那 40 点精力（缺口 ≥70 时仍按缺口野餐，见上面训练流分支）。
        # 所以标签只放在「本来就在划船/野餐之间摇摆」的这一层。
        if self.boat_race_badge(texts):
            self.log_info("公园：「划船比赛！」添头 → 划船（比普通划船多 +10 属性）")
            return "划船"
        boat, picnic = self.park_option_boxes(texts)
        if boat is None or picnic is None:
            return pick
        counts, how = self.option_mate_counts([boat, picnic])
        diff = counts[0] - counts[1]
        if diff < self.MATE_MARGIN:
            self.log_info(f"公园：野餐（队友按{how}：划船 {counts[0]} / 野餐 {counts[1]}，"
                          f"只多 {diff} 人，不够 {self.MATE_MARGIN} 人）")
            return pick
        self.log_info(f"公园：默认野餐 → 划船（队友比野餐多 {diff} 人，按{how}："
                      f"划船 {counts[0]} / 野餐 {counts[1]}）")
        return "划船"

    def boat_race_badge(self, texts):
        """「划船比赛！」是不是贴在**划船选项正上方**？

        2026-11-15：地图上的公园图标也挂同一个标签，整页子串匹配会在页面
        误判时把地图当成公园页。所以判据连位置一起判（同 `shop_fresh_badge`）：
        - 标签中心在划船选项中心正上方（|Δcx| ≤ 0.15，Δcy ∈ [0.005, 0.15]）；
        - 标签和选项被 OCR 读成一整块（框名本身带「划船比赛」）也算。
        """
        boat, _picnic = self.park_option_boxes(texts)
        if boat is None:
            return False
        if "划船比赛" in (boat.name or ""):
            return True
        bx = (boat.x + boat.width / 2) / self.width
        by = (boat.y + boat.height / 2) / self.height
        for t in texts:
            name = (t.name or "").strip()
            if "划船比赛" not in name:
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if abs(cx - bx) <= 0.15 and 0.005 <= (by - cy) <= 0.15:
                return True
        return False

    def park_default_pick(self, texts, style, day=None):
        """缺口读不到时的旧回退：训练流「周六划船 / 周日看精力」，其它流派用配置项。"""
        if style != "训练流":
            return self.config.get("公园选项")

        st = self.day_state()
        day = day if day is not None else (st.date[1] if st.date else None)
        if st.park_choice is None:
            if day == "日":
                stamina = self.top_bar(texts).get("stamina")
                # 只在「缺口」读不到时兜底；周末野餐缺口现在表示缺口，
                # 不能拿它当精力阈值用，所以这里用旧的固定 60。
                threshold = WEEKEND_PICNIC_FALLBACK
                if stamina is not None and stamina < threshold:
                    st.park_choice = "野餐"
                    self.log_info(f"周日精力 {stamina} < {threshold} → 野餐补精力")
                else:
                    st.park_choice = "划船"
                    self.log_info(f"周日精力 {stamina} ≥ {threshold} → 划船（属性+）")
            else:
                st.park_choice = "划船"
                self.log_info("周六公园：划船（属性+，不再满精力吃野餐）")

        return st.park_choice

    def park_option_boxes(self, texts):
        """公园选项里「划船」「野餐」两个文字框 → (划船, 野餐)；找不到的那项是 None。

        只拿位置（数队友要框的坐标），**点击永远走 `choose_option` 按名字找**
        —— 名字框认错位置最多数错人，不会点错地方。
        """
        opts = self.texts_in_region(texts, "weekend_options")
        found = {}
        for name in labels.PARK_OPTIONS:
            box = self.exact_text(opts, (name,)) or self.match_text(opts, (name,))
            if box is not None:
                found[name] = box
        return found.get("划船"), found.get("野餐")
