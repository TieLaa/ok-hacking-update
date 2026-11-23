"""流派策略：最终形状（接口先行，**尚未接入**）。

一段话说明白这个文件是干什么的：

    游戏里每个队长的「流派」不同，导致「周末去哪、商店要不要少买、公园选什么」
    的答案不同。这个文件就是这些答案的清单 ——
    上半部分是给人看的（每天每个流派干什么），
    下半部分是给程序看的（那些答案写成一类叫 Style 的东西）。

**怎么读这个文件**：

    - `Style` 的一个子类 = 一个流派。类属性就是答案（值），方法就是判断。
    - 值：现在就能查，`style_spec("扭蛋流").weekend_main` → "步行街"。
    - 方法：5 个事件判断已接（周末去哪、先去哪个、公园、餐厅、练或休）。
      步行街挑卡**不是流派差异**：它是同一套排序，只是拿当前流派名比
      「专属卡是不是本流派的」（`places/street.py`）；唯一的流派值是
      `cash_first`，已经读表。所以没有 `pick_street_card` 方法。
    - 「待迁 / 不改 / 待定」见业务地图里的标记说明。

三部分：按天业务地图（注释）→ 值（类属性，可用）→ 判断（方法，未接）。

清单最后核对：2026-11-17（对齐 `d350d5e` / `3cf2e0c` 之后的代码）。
"""

# ============================================================================
# 一、术语（游戏里的说法，先看这个）
# ============================================================================
#
#   主地点    周末花钱去的地方（步行街=抽卡 / 游戏厅=小游戏）。
#             训练流 / 商店流没有：它们周末只打工或回精力，不花钱。
#             注意和「探卡池」区别：探卡池是所有流派出行日都先去步行街看一眼；
#             主地点是进去正常消费。
#   行程      今天按顺序去哪几个地方，前一个走完才轮到下一个
#   收工      这件事做完今天就不再出门（买到扭蛋就不再去餐厅打工）
#   缺口      精力上限 − 当前精力；差多少才补得回来
#   留钱      少买东西，把零用钱留到周末或下一次出行
#   探卡池    出行日第一步先去步行街，看有没有两张彩色目标卡
#   科目页    点「训练」后选练哪个属性（投射/弹跳/防守/运控/体能/意识）的那一页
#
# ============================================================================
# 二、一天里的顺序（所有流派共用）
# ============================================================================
#
#   开局 / 换队长   定流派：配置了队长 → 查表；没配置 → 点日历认队长（flow.py）
#   每天开始        读日期 / 精力 / 零用钱，每帧只读一次（daycycle.top_bar）
#   训练            一律先点「训练」进科目页；失败率超限才退出来休息
#                   （daycycle.training_day_action）
#   进店            有「新品上市」标签就进；第 7 周周日强制进
#                   （daycycle.late_shop_step）
#   出行            有「出行」按钮就去（daycycle.outing_offer）
#   出行中          地图决定去哪 → 地点内决定做什么（places/map.py → places/*.py）
#
# ============================================================================
# 三、训练日（周一~周五；有没有出行看按钮，不看日期）
# ============================================================================
#
#   事项          训练流        商店流        扭蛋流            小游戏流
#   练 / 休        先进科目页     先进科目页     先进科目页         先进科目页
#   周四低精力     ——            ——            精力 < 60 直接休   精力 < 60 直接休
#   进店           有标签就进     有标签就进     有标签就进         有标签就进
#   店内买什么     同顺序         同顺序         同顺序             同顺序
#   店内留钱       不留           不留           前 6 周只拿必拿    前 6 周只拿必拿
#   有出行按钮     见第四节       见第四节       见第四节           见第四节
#
#   已迁  前 6 周只拿必拿   → places/shop.py: save_money_for_weekend（读 saves_money）
#   已迁  周四低精力阈值   → styles.py: Style.should_rest（thursday_rest + 参数）
#
# ============================================================================
# 四、周末（周六、周日）
# ============================================================================
#
#   事项          训练流                  商店流                扭蛋流              小游戏流
#   今天去哪      见下方「训练流细则」     餐厅                  步行街 → 餐厅        游戏厅 → 餐厅
#   钱不够时      不看钱                  不看钱                <360 只去餐厅打工    同扭蛋流
#   主地点成交后  ——                      ——                    买到即收工          靠「今天去过」过滤
#   公园选什么    缺口 ≥70 野餐           配置默认（野餐）      同商店流             同商店流
#                 缺口 <70 划船           队友多 2 → 划船       队友多 2 → 划船      队友多 2 → 划船
#   场所开关关了  不涉及                  不涉及                步行街关 → 餐厅      游戏厅关 → 餐厅
#
#   训练流细则（缺口 = 精力上限 − 当前精力）：
#     缺口 < 30              → 餐厅 → 公园（打不了工还有公园兜底）
#     缺口 ≥ 30 · 周六       → 餐厅 → 公园（单卡队友 ≥3 人才真打工，分散就退出回公园）
#     缺口 ≥ 30 · 周日       → 公园（精力花光，下周训练日会少一天）
#     第 7 周周六 · 钱 > 120 → 步行街 → 餐厅（刷最后一次彩卡，没买成才打工）
#     第 7 周周六 · 钱 ≤ 120 → 餐厅
#     缺口读不到             → 餐厅 → 公园
#
#   注意：训练流那一行的「今天去哪」不是读表，是算法（按缺口 + 星期 + 周数算）。
#   值表里给训练流的 weekend_plan = ("餐厅",) 只是兜底值，正常不会用到。
#
#   已迁  今天去哪 / 行程 / 收工  → places/map.py: today_places（读值表）
#   已迁  公园队友优先            → places/park.py: park_default_pick（读 teammate_first）
#   已迁  步行街先看钱            → places/street.py（读 cash_first）
#   已迁  训练流按缺口补训        → styles.py: TrainingStyle.weekend_places
#   已迁  训练流公园按缺口        → styles.py: TrainingStyle.pick_park_normal
#   不改  训练流周六餐厅「看一眼」 → styles.py: TrainingStyle.accept_work_card
#                                 （单卡 <3 人 → 退出餐厅，交回地图）
#   不改  钱不够只去餐厅          → places/map.py: prefer_venue
#
# ============================================================================
# 五、叠加在任何天上的规则（都要读当场数据，接入 ctx 前留在原函数）
# ============================================================================
#
#   规则          谁            内容                                          位置
#   探卡池        所有流派      出行日第一步先去步行街：
#                               · 两张彩色目标卡（笔记继承 / 球员位置）照买；
#                               · 其它彩色卡也用闲钱买（按下面的「留钱」规则）；
#                               · 买到一张目标卡后，这趟转成正常买卡模式。
#                                                                           map.gacha_probe
#                                                                           street.gacha_candidates
#   急招          所有流派      餐厅有急招且主计划不是餐厅 → 先插一趟           map.event_detour
#                               （第 7 周周六不插）
#   步行街打折    只有扭蛋流    折后够、原价不够 → 先绕去步行街                 map.map_context
#   第 3 周周末   所有流派      体力低于海边目标 → 把打工换成公园               map.vacation_eve_guard
#   最后一周      所有流派      整周不买训练类；最后一天只买属性（蓝白也买）    shop.pick_shop_item
#   留钱          所有流派      最后一天之前买完要剩 120（留给步行街两张卡）；
#                              第 7 周周六起不再留（商店和探卡池都放开）      shop.pick_shop_item
#                                                                           street.gacha_candidates
#   第 7 周周日   所有流派      不能出行，只进商店清零花钱                      map.today_places + daycycle
#   比赛日        所有流派      按配置跳过或加速                               handlers/match.py
#   专属卡归属    见词表        训练流/小游戏流/扭蛋流各自吃哪些卡              attributes.card_style_target
#

from src.tasks.training import params
from src.tasks.training.constants import PARK_BOAT_STAMINA, SHOP_SEASON_WEEKS


class Style:
    """一个流派：默认值 + 默认行为，子类只写差异。

    类属性 = 值（现在就能查）；方法 = 判断（接入 ctx 前一律抛错）。
    字段注释写的是「这个字段回答哪个业务问题」，不是类型说明。
    """

    # ---------------- 值（已可用） ----------------
    name = ""                     # 我是谁（和 docs/roster 里的 style_tag 一致）
    weekend_main = None           # 周末花钱去哪个地点？None = 周末不专门消费
    weekend_plan = ("餐厅",)      # 没有主地点时，今天按顺序去哪？
    ends_day_after_venue = False  # 主地点买到/玩完，今天还去餐厅打工吗？
    saves_money = False           # 前 6 周商店要不要少买、把钱留给周末？
    cash_first = False            # 步行街：先看钱够不够，还是先看卡好不好？
    teammate_first = False        # 公园：队友多 2 个就改划船吗？
    discount_bonus = False        # 主地点贴「打折！」且钱差一点时，先绕过去？
    thursday_rest = False         # 周四精力低要不要直接休息？（阈值在 params）

    # 划船队友比野餐多几个才改判（与 `RestaurantPlace.MATE_MARGIN` 同一口径，
    # 「差 1 个不算」）。
    MATE_MARGIN = 2

    # ---------------- 判断（尚未接入，调用即报错） ----------------
    #
    # 每个方法的实现都已在别处，接入时只做「搬 + 改签名」：
    #   docstring 末尾写上现有位置，方便照着改。

    def weekend_places(self, ctx):
        """今天去哪（有序）。返回 (地点列表 | None, 理由, 是否最终)。

        `None` = 读不到关键数据，调用方走旧回退。
        `最终` = True 时不经过后面的场所开关 / 兜底（训练流原来就是直接返回）。

        基类 = 值表：主地点 + 餐厅；买到/玩完当天就收工。
        训练流覆盖成「按精力缺口」（见 `TrainingStyle.weekend_places`）。
        """
        if self.weekend_main is not None:
            places = [self.weekend_main, "餐厅"]
            if self.ends_day_after_venue and ctx.gacha_spent:
                places = [self.weekend_main]
            return places, "", False
        return list(self.weekend_plan), "", False

    def prefer_venue(self, ctx):
        """今天先去哪个（有序）。返回 (地点列表, 理由)。

        有主地点的流派看零用钱门槛：
          读不到钱 → 主地点；钱 < 周末打工门槛 → 只去餐厅；否则去主地点。
        第 7 周周六是最后一次出行，不看钱直接去主地点。
        没有主地点的流派（训练流 / 商店流）原样返回候选。
        """
        main = self.weekend_main
        if main is None:
            return list(ctx.places), ""
        pair = [p for p in ctx.places if p in (main, "餐厅")]
        if len(pair) < 2:
            return list(ctx.places), ""
        rest = [main] + [p for p in ctx.places if p != main]
        if ctx.day == "六" and ctx.week == SHOP_SEASON_WEEKS:
            return rest, (
                f"周末地点（第7周）：最后一次出行 → 直接去「{main}」"
                "（没消费再退出回餐厅打工）")
        if ctx.money is None:
            return rest, f"周末地点：零用钱读不到 → 去「{main}」"
        gate = int(params.weekend.work_money_gate)
        if ctx.money < gate:
            return ["餐厅"], (
                f"周末地点：零用钱 {ctx.money} < {gate} → 只去「餐厅」打工")
        return rest, f"周末地点：零用钱 {ctx.money} ≥ {gate} → 去「{main}」"

    def pick_park(self, ctx):
        """公园选散步 / 划船 / 野餐。返回 (选项, 理由)；`None` = 交给旧回退。

        三层，顺序不能换：
          ① 第 3 周周末（海边前）：距目标 > 划船 +30 才野餐，否则划船。
             日期用 `ctx.week/day`（`current_day()`，读不到任一就是 None）。
          ② 最后一周：没有训练日，精力不再兑现 → 划船。
             周数用 `ctx.top_week`（`top_bar()`，只读到周也算）。
          ③ 平时：各流派自己的规则（训练流按缺口，其它流派队友优先）。

        训练流读不到缺口时返回 `(None, 理由)`，调用方 `park.py` 走原来的
        `park_default_pick`（它会写 `st.park_choice`，是状态，不进只读 ctx）。
        """
        if ctx.vacation_target is not None and ctx.stamina is not None:
            gap = ctx.vacation_target - ctx.stamina
            if gap > PARK_BOAT_STAMINA:
                return "野餐", (
                    f"海边前周末：体力 {ctx.stamina} 距目标 {ctx.vacation_target}"
                    f" 差 {gap}（> 划船 {PARK_BOAT_STAMINA}）→ 野餐")
            return "划船", (
                f"海边前周末：体力 {ctx.stamina} 距目标 {ctx.vacation_target}"
                f" 差 {gap} → 划船")

        if ctx.top_week is not None and ctx.top_week >= SHOP_SEASON_WEEKS:
            return "划船", "公园：最后一周没有训练日，精力不再兑现 → 划船（属性+）"

        return self.pick_park_normal(ctx)

    def pick_park_normal(self, ctx):
        """③ 平时怎么选：划船比赛 / 划船队友多 → 划船；否则野餐补精力。

        2026-11-18 起「公园选项」配置已删除，默认固定野餐；
        只有这两条会改判（其它流派的队友优先口径不变）。
        """
        if ctx.boat_race:
            return "划船", "公园：「划船比赛！」添头 → 划船（比普通划船多 +10 属性）"
        if ctx.mate_boats is None or ctx.mate_picnic is None:
            return "野餐", "公园：没数到队友 → 默认野餐"
        diff = ctx.mate_boats - ctx.mate_picnic
        if diff < self.MATE_MARGIN:
            return "野餐", (
                f"公园：野餐（队友按{ctx.mate_how}：划船 {ctx.mate_boats} / "
                f"野餐 {ctx.mate_picnic}，只多 {diff} 人，"
                f"不够 {self.MATE_MARGIN} 人）")
        return "划船", (
            f"公园：默认野餐 → 划船（队友比野餐多 {diff} 人，"
            f"按{ctx.mate_how}：划船 {ctx.mate_boats} / 野餐 {ctx.mate_picnic}）")

    def accept_work_card(self, ctx):
        """这张工接不接？返回 (接不接, 理由)。默认 = 都接（按原排序挑）。

        训练流·周六「看一眼」趟要求单卡队友 ≥3 人，两张卡分散就退出餐厅
        （覆盖见 `TrainingStyle.accept_work_card`）。
        """
        return True, ""

    def should_rest(self, ctx):
        """周四低精力要不要直接休息。返回 (休不休息, 理由)。

        基类 = 不休息：训练日一律先进科目页，失败率在科目页判。
        扭蛋流 / 小游戏流周四有硬阈值：低于它直接休息，保证周五能进出行地图。
        """
        if not self.thursday_rest or ctx.day != "四" or ctx.stamina is None:
            return False, ""
        if ctx.stamina < ctx.threshold:
            return True, (f"周四 {ctx.style} 精力 {ctx.stamina} < {ctx.threshold}"
                          "（低精力休息，保周五出行地图）")
        return False, ""


class TrainingStyle(Style):
    """训练流（游戏里练属性为主那类队长）。

    工作日
        · 训练日先进科目页；失败率超限才退出来休息
        · 周四没有特殊规则
        · 商店有「新品上市」就进；买什么和别的流派同一条顺序
    周末（按精力缺口 = 上限 − 当前）
        · 缺口 < 30              → 餐厅 → 公园（打不了工还有公园兜底）
        · 缺口 ≥ 30 · 周六       → 餐厅 → 公园（单卡队友 ≥3 人才真打工，分散就回公园）
        · 缺口 ≥ 30 · 周日       → 公园（精力花光，下周训练日会少一天）
        · 第 7 周周六 · 钱 > 120 → 步行街 → 餐厅（刷最后一次彩卡）
        · 第 7 周周六 · 钱 ≤ 120 → 餐厅
    公园
        · 缺口 ≥ 70 野餐，< 70 划船；队友不参与（缺口优先）
    钱
        · 不需要留：不进步行街也不进游戏厅，卡对训练流以外没用
    """

    name = "训练流"

    def weekend_places(self, ctx):
        """训练流周末：按精力缺口选今天做什么。

        缺口 = 上限 − 当前：
          < 训练流-周末划船缺口           → 餐厅 → 公园（打不了工还有公园兜底）
          ≥ 缺口 · 周六            → 餐厅 → 公园（单卡队友 ≥3 人才真打工，分散回公园）
          ≥ 缺口 · 周日            → 公园（精力花光，下周训练日会少一天）
          第 7 周周六 · 钱 > 保留线 → 步行街 → 餐厅（刷最后一次彩卡）
          第 7 周周六 · 钱 ≤ 保留线 → 餐厅
          缺口读不到                → None（调用方回退「餐厅 → 公园」）
        """
        if ctx.week == SHOP_SEASON_WEEKS and (ctx.day or "六") == "六":
            keep = int(params.shop.keep_money_before_last_day)
            if "步行街" in ctx.visited:
                if ctx.gacha_spent:
                    return [], "周末（第7周周六）：步行街买到卡了 → 今天不再打工", True
                return ["餐厅"], "周末（第7周周六）：步行街没买成 → 去餐厅打工", True
            if ctx.money is not None and ctx.money > keep:
                return ["步行街", "餐厅"], (
                    f"周末（第7周周六）：零用钱 {ctx.money} > {keep}"
                    " → 先去步行街刷彩卡（没买成再打工）"), True
            return ["餐厅"], (
                f"周末（第7周周六）：零用钱不够（{ctx.money}）→ 去餐厅打工，"
                "钱留给周日商店"), True
        if ctx.stamina is None or ctx.stamina_max is None:
            return None, "周末：训练流读不到精力缺口 → 回退「餐厅 → 公园」", True
        gap = ctx.stamina_max - ctx.stamina
        if gap >= ctx.row_gap:
            if (ctx.day or "六") == "六":
                return ["餐厅", "公园"], (
                    f"周末（周六）缺口 {gap} ≥ {ctx.row_gap} → 先餐厅看一眼"
                    "（单卡 ≥3 才打工，分散回公园；周日还能补精力）"), True
            return ["公园"], "", True
        return ["餐厅", "公园"], (
            f"周末缺口 {gap} < {ctx.row_gap} → 餐厅打工"
            "（零用钱留给商店的牛奶/秘籍，周末不去步行街/游戏厅）"), True

    def pick_park_normal(self, ctx):
        """训练流按精力缺口选：≥ 训练流-周末野餐缺口 → 野餐，否则划船。

        缺口读不到时返回 `(None, 理由)`，调用方走旧回退（周六划船 / 周日看精力）。
        """
        if ctx.stamina is None or ctx.stamina_max is None:
            return None, "公园：缺口读不到 → 走旧回退"
        gap = ctx.stamina_max - ctx.stamina
        if gap >= ctx.eat_threshold:
            return "野餐", f"公园：缺口 {gap} ≥ {ctx.eat_threshold} → 野餐补精力"
        return "划船", f"公园：缺口 {gap} < {ctx.eat_threshold} → 划船（属性+）"

    def accept_work_card(self, ctx):
        """训练流·周六「看一眼」趟：只打单卡队友 ≥3 人的高收益工。

        条件（全部成立才退出餐厅）：
          - 主计划趟（急招插队趟不看，双倍工资本身就不亏）；
          - 第 7 周之前（最后一周没有下一次出行，打了也白打）；
          - 缺口 ≥ 训练流-周末划船缺口（缺口小根本不会走「先看餐厅」这条路）；
          - 两张卡的队友都 <3 人（分散 2+2 = 普通工资）。
        """
        if not ctx.allow_fallback:
            return True, ""
        if ctx.week is None or ctx.week >= SHOP_SEASON_WEEKS:
            return True, ""
        if ctx.gap < ctx.row_gap:
            return True, ""
        if ctx.mate_counts and max(ctx.mate_counts) < 3:
            return False, (
                f"餐厅：两张卡分散（{list(ctx.mate_counts)}），最高 <3 人 → "
                "普通工资不划算，退出餐厅")
        return True, ""


class ShopStyle(Style):
    """商店流（钱主要花在商店那类队长）。

    周末固定去餐厅打工，商店是唯一消费口 —— 所以步行街也先看钱（cash_first），
    公园按「队友多就划船」拿属性（teammate_first），不刻意省精力。
    """

    name = "商店流"
    cash_first = True
    teammate_first = True


class GachaStyle(Style):
    """扭蛋流（周末去步行街抽卡那类队长）。

    工作日
        · 周四精力 < 60 直接休息（保证周五还能进出行地图）
        · 商店前 6 周只拿免费红包，钱留给周末
    周末
        · 去步行街；钱 < 360 就先只去餐厅打工攒钱，攒够再去
        · 抽到卡当天收工，不再去餐厅 —— 钱已经花掉了
    公园
        · 队友比野餐多 2 个 → 划船；否则配置默认（野餐）
    """

    name = "扭蛋流"
    weekend_main = "步行街"
    weekend_plan = ("步行街", "餐厅")
    ends_day_after_venue = True
    saves_money = True
    cash_first = True
    teammate_first = True
    discount_bonus = True
    thursday_rest = True


class MiniGameStyle(Style):
    """小游戏流（周末去游戏厅玩小游戏那类队长）。

    和扭蛋流几乎一样：周末去游戏厅、前 6 周留钱、钱不够先打工、周四低精力休息。
    唯一不同：玩完小游戏游戏会自己结束，「今天去过游戏厅」本身就是标记，
    不需要额外的「收工」开关（ends_day_after_venue = False）。
    """

    name = "小游戏流"
    weekend_main = "游戏厅"
    weekend_plan = ("游戏厅", "餐厅")
    saves_money = True
    cash_first = True
    teammate_first = True
    thursday_rest = True


STYLES = {s.name: s() for s in
          (TrainingStyle, ShopStyle, GachaStyle, MiniGameStyle)}

# 流派下拉的选项（2026-11-23 起没有「跟随队长」：流派是存储值，
# 选队长时带出一次，之后两个下拉各改各的）。
UNSET_STYLE = "未选择"
STYLE_OPTIONS = tuple(STYLES)
# 任务配置卡的下拉：多一个「未选择」（监控页用不可选的占位项，不放进这里）。
CONFIG_STYLE_OPTIONS = (UNSET_STYLE, *STYLE_OPTIONS)

# 流派没定 / 认不出名字时的兜底：`cash_first` / `teammate_first` 按
# **非训练流**处理（与收口前的 `style != "训练流"` 等价）；`saves_money` 更保守 ——
# 认不出流派时宁可少花钱（同 `shop.save_money_for_weekend`）。
class UnknownStyle(Style):
    """兜底流派：名字为空，行为按「非训练流」走。"""

    name = ""
    cash_first = True
    teammate_first = True
    saves_money = True


UNKNOWN = UnknownStyle()


def style_spec(name):
    """流派名 → 该流派的实例。认不出返回 UNKNOWN（不抛 KeyError）。"""
    return STYLES.get(name or "") or UNKNOWN


# ============================================================================
# 六、ctx（每个事件的只读输入）
# ============================================================================
#
# 方法只能拿到 `ctx`，不能拿整个任务对象 —— 否则等于把 if 搬进类里。
# ctx 定义在 `strategy/context.py`，每个事件一个，只装当场数据：
#
#   ParkContext     公园：精力、周数、队友数、配置
#   WorkContext     餐厅：第几周、缺口、两张卡的队友数
#   WeekendContext  今天去哪：钱、精力、去过哪、买到没
#   VenueContext    先去哪个：钱、候选地点
#   RestContext     练或休：星期、精力、阈值
#
# 新增一个事件时，先加它自己的 ctx，不要把所有字段塞进一个大的。
#
# ============================================================================
# 七、迁移进度
# ============================================================================
#
#   已完成（2026-11-17）：
#     · 值：商店留钱、周末行程、公园队友、步行街钱优先、周四休息、
#       步行街打折 —— places/* 与 daycycle 里不再有 style == 的业务分支。
#     · 方法：weekend_places / prefer_venue / pick_park / accept_work_card /
#       should_rest 已接。
#
#   剩下：无。步行街挑卡是同一套排序（用当前流派名比专属卡），
#   不是变体；唯一的流派值 cash_first 已经读表（`places/street.py`）。
