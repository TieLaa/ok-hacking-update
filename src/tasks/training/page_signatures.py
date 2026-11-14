"""页面签名（Page Signature）—— 阶段 2 的核心。

阶段 2 之前，每个 handler 自己在开头写一串「我不是这个页面」的排除条件：

    # handle_event_choice 里就有 20 个词
    if self.match_text(texts, "失败率") is not None: return False
    if self.match_text(texts, ("上新时间", "能力详情")) is not None: return False
    ...

这些条件其实是**别的页面的签名**。散在 30+ 个 handler 里，谁改了都会互相影响，
每撞一次事故就再补一条（2026-11-13「在公园」正文那一次就是）。

这一模块把「某个页面的特征词」集中声明一次：

    SIGNATURES = {
        "shop": (("上新时间",), ("能力详情",), ("剩余",)),
        ...
    }

用法（handler 里一行）：

    if self.page_is_other(texts, "event_choice"):
        return False

`page_is_other(page)` = 「这一帧命中了**除 page 以外**某个页面的签名」。
于是「排除邻居」变成「声明自己」，互斥关系只存在这一张表里。

**语义**：
- 一个页面 = 若干个**组**，组之间是**或**（任一组命中就算这个页面）；
- 一组内部是**且**（组里的词必须同时出现）；
- 词可以是：
  - 字符串 → 包含匹配（「上新时间」这种不会出现在正文里的词）；
  - `Exact("词")` → **整框**（「决定」「训练核心」这种正文里也会出现的词；
    2026-10-27 事故：事件页正文「成功后，决定通过广播站平台支持球队。」
    里有「决定」，包含匹配会把事件页判成科目页 → 事件选项没人认领）；
  - `Places()` → 整框地点名 ≥2 个（周末地图页）；
  - `MainAction("VS", ...)` → 右下角主按钮区域（训练日主界面 / 比赛开场页）。
"""

from src.tasks.ui import labels


class Exact:
    """标记一个签名词必须**整框**匹配（正文里出现不算）。

    2026-10-27 事故：事件页正文「成功后，决定通过广播站平台支持球队。」里有
    「决定」，包含匹配会把事件页判成训练科目页 → 事件选项没人认领 →
    兜底点屏幕中央 → 3 次没反应 → 卡死停止（docs/shots/2026-10-27-broadcast-event.png）。
    """

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class Places:
    """标记一个签名靠「整框地点名 ≥2 个」判定（周末地图页）。

    地图上有「公园/餐厅/游戏厅/步行街」等整框地点名；正文里提到地名不算
    （2026-10-26 卡死：周末首页台词「告诉她公园的草坪是假的」把首页判成地图，
    于是反复点「划船」→ 空转 50 秒）。
    """

    __slots__ = ("value",)

    def __init__(self, value="地图地点"):
        self.value = value


class MainAction:
    """标记一个签名靠「右下角主按钮区域」判定（训练日主界面 / 比赛开场页）。

    只看 `main_action` 区域（右下角），不看全屏 —— 全屏的台词里
    也会出现「比赛」「训练」。
    """

    __slots__ = ("words",)

    def __init__(self, *words):
        self.words = words


# 页面 ID → 特征词组。
# 组之间是「或」，组内是「且」。只放**页面级强特征**。
SIGNATURES = {
    # 商店内页：右上「上新时间」/ 左下「能力详情」/ 列表「剩余」任一出现
    "shop": (("上新时间",), ("能力详情",), ("剩余",)),
    # 训练科目页：右上独立的「决定」按钮（整框）
    "training_subject": ((Exact("决定"),),),
    # 训练日主界面 / 比赛开场页：右下角主按钮写着 VS / 比赛 / 训练
    "training_hub": ((MainAction("VS", "比赛", "训练"),),),
    # 训练计划（日历）页
    "training_plan": (("训练计划",),),
    # 比赛结算 / 关卡列表页：右侧栏目成对出现
    "match_result": (("关卡加成", "奖励预览"),),
    "match_detail": (("对手实力", "前往挑战"),),
    # 本周总结页（球队评分表格）：左列竖排「控卫／分卫／小前／大前／中锋」，
    # 同一列、2 字短文本，被 choice_candidates 当成竖排三选一，
    # 每拍点一次「控卫」连点 4~5 下（2026-10-27 00:02 实机）。
    # 页面在播动画（分数每拍都在变），看门狗的「3 次无变化」判不出来，
    # 只能靠页面特征排除 —— 三个词要**同时**出现才算。
    "weekly_summary": (("本周总结", "球队评分"), ("球员", "位置", "评分")),
    # 活动赛赛程页：「继续匹配」「暂时退出」并排
    "match_event": (("继续匹配", "暂时退出"),),
    # 周末地图：整框地点名 ≥2 个
    "weekend_map": ((Places(),),),
    # 炒股选股页（天赋「股神!」）
    "stock_pick": ((labels.STOCK_PICK_TITLE,),),
    # 注册球员流程
    "register_player": (("注册球员",),),
    # 组队 / 笔记 / 器材 / 编队确认
    "team": (("选择队友",),),
    "note_select": (("选择笔记",),),
    "equipment": (("选择器材",),),
    "confirm_team": (("确认编队",),),
    # 选队长页
    "captain": ((Exact("训练核心"),),),
    # 公告页
    "announcement": (("公告",),),
    # 扭蛋页的说明文字里也会出现「体能」「投射」等词
    # （例：「所有小游戏额外获得20点体能」）—— 不排除掉会被当成科目页，
    # 然后疯狂点卡片、点确定（2026-10-25 死循环）。
    "gacha_page": (("扭蛋", "本局训练中", "小游戏强化",
                   "器材熟练度", "熟练度"),),
    # 「专属页面」的聚合签名：这些页面上也有 2~3 张上色卡，
    # 但点下去会花钱或改阵容 —— 未登记卡兜底（handle_unmarked_cards）不能碰。
    # 2026-11-03 实跑暴露：扭蛋/商店/游戏厅/地图/餐厅/队伍/结算都会被兜底抢走。
    # 原来这 36 个词写在 `labels.QUALITY_CARD_EXCLUDE`，由那个 handler 自己判断；
    # 现在收口到这里，各页面只声明一次。
    "exclusive_page": (labels.QUALITY_CARD_EXCLUDE,),
}


def _match_word(task, texts, word):
    """签名里的一个词怎么算命中。"""
    if isinstance(word, Exact):
        return task.exact_text(texts, (word.value,)) is not None
    if isinstance(word, Places):
        return len([t for t in texts
                     if t.name.strip().rstrip("+") in labels.WEEKEND_PLACES
                     and len(t.name.strip()) <= 4]) >= 2
    if isinstance(word, MainAction):
        action = task.texts_in_region(texts, "main_action")
        return task.match_text(action, word.words) is not None
    return task.match_text(texts, word) is not None


def _match_group(task, texts, group):
    """一组词：全部命中才算（且）。"""
    return all(_match_word(task, texts, w) for w in group)


def matched_signatures(task, texts):
    """这一帧命中了哪些页面签名（返回页面 ID 列表）。"""
    hits = []
    for page, groups in SIGNATURES.items():
        if any(_match_group(task, texts, g) for g in groups):
            hits.append(page)
    return hits


def page_is_other(task, texts, page):
    """这一帧是不是**别的**页面（命中了除 `page` 以外的签名）。

    `page` 传 None 时 = 「命中了任何签名」。
    """
    for other in matched_signatures(task, texts):
        if other != page:
            return True
    return False


def page_has(task, texts, page):
    """这一帧命中了**指定**页面的签名吗（只看这一页）。"""
    return page in matched_signatures(task, texts)
