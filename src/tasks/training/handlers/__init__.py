"""handler 注册表：**顺序就是优先级**，唯一来源。

规则：
1. 强判据（区域/关键词独特）放前面；
2. 共用「下一步/确定」这类通用词的，必须排在后面的专属 handler 之后；
3. 新 handler 先写进对应子模块，再在这里决定位置；
4. `tools/check_handlers.py` 会逐张图比对 `docs/shots/expected.json`，
   顺序改错会直接报红。
"""

from .popup import PopupHandlers
from .stamina import StaminaHandlers
from .training import TrainingHandlers
from .weekend import WeekendHandlers
from .match import MatchHandlers
from .entry import EntryHandlers
from .talent import TalentHandlers
from .wheel import WheelHandlers
from .unmarked import UnmarkedHandlers

HANDLERS = (
    # 一局结束的「训练评分」弹层（`训练评分 7728 SS 点击屏幕继续`）：
    # 多角色队列的**达标判据**就靠它读个人评分（2026-11-04 实跑漏读导致「7700 分了还在跑」）。
    # 必须排在 handle_popup / handle_tap_to_continue 之前 —— 它们会直接点「点击屏幕继续」翻页，
    # 翻过去评分就再也读不到了。
    MatchHandlers.handle_training_score,
    # 注册球员收尾流程必须排在 handle_popup 之前：它有「关闭」按钮，
    # 排在后面会被 popup 抢走（用户 2026-10-27 提供的 6 屏流程）。
    MatchHandlers.handle_register_player,
    PopupHandlers.handle_resume_training,
    # 专注不足弹窗（「训练所需专注还差25，是否进行回复呢？」）必须在 handle_popup
    # 之前：popup 在没有「确定」时会点「取消」，于是「差 25 → 取消 → 再点训练」死循环。
    StaminaHandlers.handle_stamina_short,
    # 「专注恢复」整页（大厅顶栏「+」进来）：三张卡只点「使用」，钻石行永不点。
    # 排在前面是因为这一页盖在大厅上，背景还能 OCR 到「训练/赛事」，
    # 交给 handle_lobby 会去点背后的按钮。
    StaminaHandlers.handle_stamina_recover,
    PopupHandlers.handle_rest_confirm,
    PopupHandlers.handle_popup,
    PopupHandlers.handle_draw,
    # 训练计划（日历）整页：必须排在所有地点 handler 之前 ——
    # 这一页带「野餐/划船/转动扭蛋/训练」，旧版被 handle_street / handle_park 抢走
    # （2026-10-27 00:54 当成扭蛋页退出并误记「步行街已去」；15:17 那局被点了「野餐」）。
    TrainingHandlers.handle_training_plan,
    MatchHandlers.handle_match_intro,
    MatchHandlers.handle_in_match,
    TrainingHandlers.handle_training_result,
    # 笔记/球星卡「学习」页（开局装备的笔记触发，不可跳过动画 + 右下「学习」）。
    # 必须排在 handle_street / handle_event_choice 之前：这页带「评分」，
    # 旧版被 handle_street 当成扭蛋详情页点了右上角「返回」（2026-11-02 01:22）。
    TrainingHandlers.handle_note_study,
    MatchHandlers.handle_match_result,
    MatchHandlers.handle_skill_select,
    # 角色天赋事件（handlers/talent.py，按天赋归口）：
    # 佐久间雨乃的天赋「钓鱼」：钓鱼场是三选一、带「确定」，
    # 必须排在通用「确定/下一步」类 handler 之前（旧版落到 unknown 兜底，连点 3 次卡死）。
    TalentHandlers.handle_fishing_ground,
    # 转盘（钓鱼啦！/ 开始做饭！/ 经理的小帮助……通用 UI 机制，handlers/wheel.py）：
    # 用户 2026-11-03 口径：转盘内容随机、不影响决策；「转一转」按钮的位置和文案
    # 在所有转盘页都是通用的（两页实测按钮框都是 715,781,111,47）。
    # 判据卡「底部居中」那一条，所以大厅「玩法目录」里那个「转一转」入口不会误伤。
    WheelHandlers.handle_spin_wheel,
    TrainingHandlers.handle_boost_training,
    # SSR 角色天赋触发的「请选择一个题材」三选一（2026-11-02）：
    # 页面右上角有「跳过」，必须排在 handle_dialog_continue / handle_story_arrow 之前，
    # 否则又变成「点快进图标，什么都不推进」（同炒股页那次事故）。
    TalentHandlers.handle_theme_select,
    # 同一事件的收尾页「WARD文档」里的「交稿」：也带「跳过」快进图标，
    # 同样必须排在 handle_dialog_continue / handle_story_arrow 之前。
    TalentHandlers.handle_manuscript_submit,
    # 角色天赋特殊事件「做饭」的食材三选一（2026-11-03 实机）：标题条按品质上色，
    # 按 彩>金>紫>蓝 → 队友多 选一张再点「确定」。必须排在 handle_event_choice
    # / 通用「确定」类 handler 之前（旧版没人认领，一直落 unknown 点屏幕中央）。
    TalentHandlers.handle_ingredient_select,
    # 角色天赋「人气主播」（和泉香织）的直播三选一（2026-11-07 实机×6，周四）：
    # 标题「请选择1个直播主题」+ 三张上色卡，纯卡面规则选卡（不读粉丝数/目标数，
    # 用户口径）。必须排在品质卡兜底 handle_unmarked_cards 之前——兜底的形态判据
    # 会认领这三张卡；标题也加进了 labels.QUALITY_CARD_EXCLUDE（双保险）。
    TalentHandlers.handle_live_stream,
    # 角色特殊事件「和泉的问题」对话二选一（2026-11-07 实机 1 次）：
    # 右上角「跳过」在这个状态是**灰的、点不到**，必须排在 handle_dialog_continue
    # 之前——旧版没人认领，连点 3 次灰「跳过」后看门狗停任务
    # （logs/debug/1107-182236_nochange3）。固定选第一个「继续倾听和泉的问题」。
    TalentHandlers.handle_izumi_question,
    # 「选择憋气难度」页（2026-11-04 实跑）：标题 + 底部「确定」，没有别的判据。
    # 必须排在通用「确定/下一步」类 handler 之前，否则会被 popup 的「关闭/取消」抢走。
    TrainingHandlers.handle_breath_difficulty,
    TrainingHandlers.handle_subject_select,
    # 炒股页（天赋「股神!」，handlers/talent.py）：必须排在 handle_event_choice **之前**。
    # 2026-11-08 实跑事故（logs/debug/1108-231656_noresp3s）：选股页的「稳健型/
    # 风险型/平衡型」+ 股票名 + 卡片说明全在居中的一列，handle_event_choice 的
    # 「竖向 3 选 1」判据先命中 → 每拍只点中间那张卡、从不点「确定」，
    # 界面纹丝不动，接管 3 次后任务停。桩（选卡 + 确定）只有 handle_stock_pick 会做。
    # 同时必须在 handle_dialog_continue 之前：股票走势面板右上角有「跳过」（快进图标），
    # 被它先命中就反复点跳过、什么都推进不了（2026-10-30 事故）。
    TalentHandlers.handle_stock_pick,
    TalentHandlers.handle_stock_result,
    MatchHandlers.handle_event_choice,
    MatchHandlers.handle_prepare,
    EntryHandlers.handle_dialog_continue,
    EntryHandlers.handle_lobby,
    EntryHandlers.handle_training_entry,
    EntryHandlers.handle_captain,
    EntryHandlers.handle_team,
    EntryHandlers.handle_note_select,
    EntryHandlers.handle_equipment,
    EntryHandlers.handle_confirm_team,
    # 2026-11-01：识别队长/流派改成**主动**做（daycycle._resolve_style_step）：
    # 接手别人的局时点返回回到「带日历的页面」→ 点日历 → 抓队长名字 →
    # 查表定流派；认不出就弹窗「未能识别到队长」，**不许保底**。
    # 原来的 handle_style_probe 只在训练日触发（要求「星期」+「休息」），
    # 接手的局在周末永远点不了日历 → 扭蛋流跑成训练流（2026-11-01 19:19 事故）。
    TrainingHandlers.handle_training_day,
    TrainingHandlers.handle_training_hub,
    WeekendHandlers.handle_activity_go,
    WeekendHandlers.handle_restaurant,
    WeekendHandlers.handle_park,
    WeekendHandlers.handle_street,
    WeekendHandlers.handle_arcade,
    WeekendHandlers.handle_shop,
    WeekendHandlers.handle_go_out,
    WeekendHandlers.handle_weekend,
    TrainingHandlers.handle_vacation,
    MatchHandlers.handle_tap_to_continue,
    MatchHandlers.handle_result,
    EntryHandlers.handle_club_members,
    EntryHandlers.handle_story_arrow,
    # ---- 兜底：**没登记的品质卡页面**（彩>金>紫>蓝 → 队友多）+ logs/unmark 存证 ----
    # 必须在整张表的最后：任何专属 handler 认领过的页面都轮不到它。
    # 用户 2026-11-03 口径：不认识的事件页也不能只点屏幕中央，按品质猜一张、
    # 把这一帧存进 logs/unmark/，人工看过就能补成正式 handler。
    UnmarkedHandlers.handle_unmarked_cards,
)

# handler 的**瞬时状态**声明：谁的状态谁声明。
# 命中别的 handler 时，这些字段自动清零（flow._reset_handler_leave_states）。
#
# 为什么要有这张表：以前的写法是在 flow._handle_any 里
#     if name != "handle_prepare": self._skip_tries = 0
# 用 handler 名字**字符串**表达「handler 之间有依赖」，于是每加一个 handler
# 都要回来改一次 flow（2026-11-01 重构前的三处：handle_prepare / handle_in_match /
# handle_subject_select）。现在依赖只存在这一张表里，flow 不认识任何 handler 名字；
# tools/check_handlers.py 会禁止 src/ 其它地方再出现 handler 名字字符串。
#
# 值可以是常量，也可以是工厂函数（可变默认值必须用 lambda，否则所有实例共享同一个 dict）。
LEAVE_RESETS = {
    # 跳过比赛只试一次：换个界面就重来
    "handle_prepare": {"_skip_tries": 0},
    # 比赛内的「手动修正/快进」标记：离开比赛界面就清空
    "handle_in_match": {
        "_match_flags": lambda: {"manual_fixed": False, "fast_clicked": False,
                                 "last_fast_at": 0.0},
    },
    # 训练科目页的选择缓存：离开科目页就清空
    "handle_subject_select": {"_picked_subject": False, "_picked_index": 0},
    # 训练日主界面的「第几拍」和「等出行按钮」计数：离开这一页就清零。
    # `_hub_ticks` 是「头一拍不决策」的依据（见 HUB_SETTLE_TICKS），
    # 不清就会把第二天的页面当成「已经看过好几拍」→ 又回到淡入那一拍就决策。
    # `_lounge_snapshot_done`：进休息室的六维/精力/零用钱快照，**每次进这一页只拍一次**
    # （2026-11-03 用户口径），所以离开时必须清掉，第二天才会重新拍。
    "handle_training_day": {"_hub_ticks": 0, "_outing_wait": 0, "_outing_clicks": 0,
                            "_lounge_snapshot_done": False},
    # 笔记学习页的等待/点击计数：离开这页就清零（下次遇到重新等、重新点）
    "handle_note_study": {"_note_study_wait": 0, "_note_study_clicks": 0},
    # 题材三选一的「已点确定」时间戳：离开这页就清掉，下次重新判颜色
    "handle_theme_select": {"_theme_confirmed_at": 0.0},
    # 「WARD文档」交稿的点击锁：离开这页就清掉
    "handle_manuscript_submit": {"_submit_confirmed_at": 0.0},
    # 「做饭」食材三选一：已点确定的时间戳 + 存证限流（离开这页就清）
    "handle_ingredient_select": {"_cook_confirmed_at": 0.0, "_cook_dumped_at": 0.0},
    # 「直播」三选一：已点确定的时间戳（离开这页就清，下次重新选卡）
    "handle_live_stream": {"_live_confirmed_at": 0.0},
    # 「和泉的问题」二选一：已点选第一个的时间戳（离开这页就清）
    "handle_izumi_question": {"_izumi_confirmed_at": 0.0},
    # 转盘的点击锁：离开这一页就清（下次遇到重新计时）
    "handle_spin_wheel": {"_wheel_clicked_at": 0.0},
    # 未登记品质卡页面的存证限流 / 确定锁：换界面就清
    "handle_unmarked_cards": {"_unmark_dumped_at": 0.0, "_unmark_confirmed_at": 0.0},
    # 加强训练页的「第几拍 / 已点确定的时刻」：离开这页就清零，
    # 否则下一张加强训练卡会被当成「已经看过好几拍」→ 又回到淡入那一拍就点（空点击）
    "handle_boost_training": {"_boost_ticks": 0, "_boost_confirmed_at": 0.0,
                              "_boost_gain_missing": 0, "_boost_gain_missing_at": 0.0},
    # 训练结果浮层的点击锁：离开就清，免得下次这页第一拍被误判成「刚点过」
    "handle_training_result": {"_result_click_at": 0.0},
    # 「专注恢复」页喝饮料的点击锁：离开这页就清（下次进来重新计），
    # `_stamina_ticks` 是「这页连续占了多少拍」的兜底计数（见 STAMINA_PAGE_MAX_TICKS）
    "handle_stamina_recover": {"_stamina_use_at": 0.0, "_stamina_ticks": 0},
    # 憋气难度页的「卡片选过没」：离开这页就清，下次重新选
    "handle_breath_difficulty": {"_breath_picked": False},
}


# 单个 handler **连续命中多少次**就该判定「这一页推不动了」（2026-11-04）。
#
# 为什么需要它：`flow` 里那套「点 3 次界面没变化」的看门狗靠**界面指纹不变**来判，
# 而这一页的顶栏数字/星标每拍都在闪 → 指纹每拍都变 → 看门狗永远不触发。
# 实测：「选择憋气难度」页把「确定」点了 **1019 次**（第 77~1200 步、27 分钟），
# 直到撞上「单局最大操作数」才停。
#
# 所以给「点了就该翻页」的 handler 单独声明一个上限（合法会长跑在同一个 handler 上的
# **不要写进来**：比赛快进、赛前准备、科目页选择都会连续命中几十上百拍）。
STALL_LIMITS = {
    "handle_breath_difficulty": 12,
    "handle_stamina_recover": 60,     # 喝饮料：每次 +30/+60，最多几十拍
}


# 会「合法连跑很多拍、而且不换天」的 handler：`flow.day_stuck_step` 的**闸 A**
# （`单日最大操作数`：同一天在有日期的页面上过了 N 拍 → 判空转）在这些页面上不计数。
#
# 为什么需要这张表（2026-11-04）：闸 A 数的是「这一天攒了多少拍」，但下面这些页面
# 本来就能连跑几十上百拍、而且**不会换天**：
#   - `handle_register_player`：实测连跑 **542 拍**（注册球员 → 编队 → 保存 → 回大厅）；
#   - `handle_in_match` / `handle_prepare`：比赛快进、赛前准备（就是上面 STALL_LIMITS
#     注释里说的「会连续命中几十上百拍」的那几个）。
# 把它们算进「一天的空转预算」→ 正常流程会被判成卡住（542 拍足够触发 3 次干预 → 停任务）。
#
# 判据仍然是「同一个 handler 连续命中」那一套（`_loop_handler`），这张表只声明
# **谁不算进闸 A**。要加就加「不换天也能合法长跑」的页，别往里塞普通的点一下翻页页。
DAY_BUDGET_EXEMPT = frozenset({
    "handle_register_player",
    "handle_in_match",
    "handle_prepare",
})
