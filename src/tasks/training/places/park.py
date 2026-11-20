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
  缺口（上限−当前）≥ `训练流-周末野餐缺口`（默认 70）→ 野餐；
  缺口 < 70 → 划船（属性+）。划船比野餐少回的精力拿去训练，
  收益高于划船那点属性，所以缺口大时野餐优先，不被「划船队友多 2」改走。
  缺口读不到时才回退旧行为（周六划船 / 周日看精力）。
- **其它流派：队友优先**（2026-11-08 用户口径，和餐厅「桌游GM 多两个就选左边」同一口径）：
  **划船的队友比野餐多 `Style.MATE_MARGIN`（2）个及以上 → 优先划船**，不看精力。
  多 1 个不算，默认野餐（2026-11-18 起固定，不再有「公园选项」配置）。

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

from src.tasks.training.constants import WEEKEND_PICNIC_FALLBACK
from src.tasks.training.strategy import styles
from src.tasks.training.strategy.context import ParkContext
from src.tasks.ui import labels

# 地点名（地图上的按钮文字 / 状态里的键）
PLACE = "公园"

# 退出公园的兜底坐标（右上角返回）。
EXIT_COORDS = (0.945, 0.040)

# 其它流派的默认公园项（2026-11-18 起固定，不再走配置）：
# 现有规则只会把它改判成「划船」（划船比赛 / 队友多），「散步」没有任何规则会选。
DEFAULT_PARK_PICK = "野餐"


class ParkPlace:
    """公园的判断与状态。**不点击**，只给判断。"""

    # 划船的队友比野餐多几个才改选：见 `strategy/styles.py` 的 `Style.MATE_MARGIN`
    # （2026-11-08 口径「多两个」，与餐厅同一口径，差 1 个不算）。

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

        判断在 `strategy/styles.py` 的 `pick_park`；这里只负责读这一帧：
        顶栏（精力 / 周数）、配置、选项框和队友数。

        读不到关键数据时 `pick_park` 返回 None → 走 `park_default_pick`
        （它会写 `st.park_choice`，属于当天状态，不放进只读 ctx）。
        """
        bar = self.top_bar(texts)
        week, day_key = self.current_day()
        spec = styles.style_spec(style)
        mate_boats = mate_picnic = None
        mate_how = ""
        boat_race = False
        # 队友数和「划船比赛」只有非训练流会用到；训练流以前就不数，别多读。
        if spec.teammate_first:
            boat, picnic = self.park_option_boxes(texts)
            if boat is not None and picnic is not None:
                counts, mate_how = self.option_mate_counts([boat, picnic])
                mate_boats, mate_picnic = counts[0], counts[1]
            boat_race = self.boat_race_badge(texts)
        ctx = ParkContext(
            style=style,
            week=week,
            day=day_key,
            top_week=bar.get("week"),
            stamina=bar.get("stamina"),
            stamina_max=bar.get("stamina_max"),
            vacation_target=self.vacation_eve_target(),
            mate_boats=mate_boats,
            mate_picnic=mate_picnic,
            mate_how=mate_how,
            boat_race=boat_race,
            eat_threshold=int(self.config.get("训练流-周末野餐缺口")),
        )
        pick, why = spec.pick_park(ctx)
        if pick is None:
            return self.park_default_pick(texts, style, day)
        self.log_info(why)
        return pick

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
        """缺口读不到时的旧回退：训练流「周六划船 / 周日看精力」，其它流派默认野餐。"""
        if styles.style_spec(style).teammate_first:
            return DEFAULT_PARK_PICK

        st = self.day_state()
        day = day if day is not None else (st.date[1] if st.date else None)
        if st.park_choice is None:
            if day == "日":
                stamina = self.top_bar(texts).get("stamina")
                # 只在「缺口」读不到时兜底；训练流-周末野餐缺口现在表示缺口，
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
