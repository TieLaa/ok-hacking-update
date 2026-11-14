"""页面分类（Page Registry）——**旁路阶段**：只认页面，不点击、不改控制流。

为什么要有这一层（2026-11-13 全局方案）：
    现在 30+ 个 handler 各自在内部判断「这是不是我的页面」，判据散落、
    互不知道对方的边界；冲突只能靠 `HANDLERS` 顺序和一条条排除条件消化。
    结果是每撞一次事故就补一条排除（2026-11-13「在公园」正文让事件页
    被跳过、又被当成周末地图，就是典型）。

    这里先把「页面 → 锚点」集中到一张表，**旁路运行**：
    每拍算一个 PageID 写日志，和实际命中的 handler 对照。
    等差异清零，再把 `_handle_any` 切到「先分类、再分派」。

判据约定（和 docs/ENGINEERING.md §3.1 一致）：
- 锚点只用 **UI 词/形态**（整框按钮、黄色边框、整框地点名），
  正文长句不参与判页。
- 一个页面至少有一个独有锚点；没有独有锚点时先不登记。
- 多个页面同时命中时按 `PRIORITY` 取第一个，但把 candidates 一起返回——
  旁路日志会把它打出来，这就是「判据重叠」的可观测入口。

只登记了首批高频/出过事的页面；其余页面返回 None，仍走现有 handler。
"""

from src.tasks.ui import Defs, labels

# 页面 ID → 优先级（数字越小越先；和 HANDLERS 的顺序保持同一口径）
#
# 排法原则（2026-11-14 回放修正）：**独有整句标题**的页面排在前面，
# 靠通用关键词认的页面排在后面。
# 反例：技能三选一页的卡片说明里有「得分」（技能效果描述），
# 比赛结算页也按「得分」认 —— 谁在前谁赢。
# 独有标题（整句、只出现在那一页）：
#   请选择1个技能 / 请选择一种加强训练 / 赛前准备 / XX的训练计划 / 请选择1个扭蛋
PRIORITY = {
    "game_title": 10,
    "construction": 20,
    "register_player": 25,
    "result": 30,
    # --- 以下四个是独有整句标题 ---
    "boost_training": 35,
    "prepare": 40,
    "skill_select": 42,
    "result_page": 45,
    "training_plan": 48,
    # --- 以下靠通用关键词，排在标题页之后 ---
    "match_result": 50,
    "boost_status": 52,
    "training_subject": 55,
    "event_choice": 60,
    "weekend_map": 65,
    "go_out": 66,
    "restaurant": 70,
    "street": 80,
    "park": 90,
    "shop": 100,
    "story_arrow": 120,
}


def _short_place_names(texts):
    """地图上的整框地点名（允许「公园+」这种带加号的短标签）。"""
    return [t for t in texts
            if len(t.name.strip()) <= 4
            and t.name.strip().rstrip("+") in labels.WEEKEND_PLACES]


# ---------- 每个页面的锚点 ----------

def _is_game_title(task, texts):
    if task.exact_text(texts, (labels.GAME_TITLE_ENTER,)) is None:
        return False
    return task.match_text(texts, labels.GAME_TITLE_MARKS) is not None


def _is_construction(task, texts):
    return (task.match_text(texts, labels.CONSTRUCTION_TITLE) is not None
            and task.exact_text(texts, (labels.CONSTRUCTION_START,)) is not None)


def _is_register_player(task, texts):
    """注册球员页：标题「注册球员」+ 流程词（下一步 / 记录笔记 / 编队 / 保存调整）。

    注意：结算页（恭喜获得 / 训练奖励）**也算**这一页 ——
    `handlers/match.py: handle_register_player` 在这一页上读五个位置的评分
    （达标判据），所以它优先于 `handle_result`。分类器要和 handler 一致，
    否则阶段 2 切换分派时会改变行为。
    """
    if task.match_text(texts, "注册球员") is None:
        return False
    return task.match_text(texts, ("下一步", "记录笔记", "编队", "保存调整")) is not None


def _is_training_subject(task, texts):
    """训练科目页：「失败率」+ 右边有个 2~3 字的按钮。

    2026-11-14 回放修正：原来要求 `confirm` 区（右上）整框是「决定」，
    但科目页的「决定」在**最右边**（cx≈0.95），而且 **cy 随选中科目移动**
    （0.34~0.83 都有 —— 右侧是纵向滚动的科目列表，「决定」跟着选中项走），
    所以固定区域框不住它。
    而且 OCR 常把它读成「快定」（实跑帧里 113 帧如此，占 16%）。

    加「快定」到词表是治标 —— OCR 错读会一直变。改成**形态判据**：
    失败率 + 最右边（cx>0.85）有一个 2~3 字的**汉字**框。
    左边（cx≈0.13）那些是队友名（望月/真崎/希雅），也是 2~3 字汉字，
    靠 cx 区分；「Lv.1」「MAX」是等级，靠「只含汉字」排除。
    """
    if task.match_text(texts, "失败率") is None:
        return False
    for t in texts:
        s = t.name.strip()
        if not (2 <= len(s) <= 3):
            continue
        if not all("\u4e00" <= c <= "\u9fff" for c in s):
            continue
        if (t.x + t.width / 2) / task.width <= 0.85:
            continue
        return True
    return False


def _is_match_result(task, texts):
    """比赛结算页：比分大字 + 统计表头 + 奖励弹层。

    2026-11-14 回放：4116 帧里 286 帧属于这一页，原先全部掉进 unknown。
    统计词要**≥2 个**才算 —— 技能三选一页的卡片说明里也出现「得分」
    （技能效果描述），单看一个词会把那一页抢走。

    这一页有三个变体，锚点各不相同：
      - 完整结算：「胜利 26 20」+ 得分/篮板/助攻 统计表
      - 目标达成：「目标达成」+「获得奖励」
      - 胜利结算：「战胜XX学校」+「获得奖励」

    「失败」不能用：训练科目页的「失败率 27%」含「失败」
    （docs/shots/2026-10-26-subject-crit.png 就是这样被抢走的）。
    """
    if task.match_text(texts, "失败率") is not None:
        return False
    if task.match_text(texts, ("胜利", "失败", "结算", "目标达成")) is not None:
        return True
    if task.match_text(texts, "获得奖励") is not None:
        return True
    stats = [w for w in ("得分", "篮板", "助攻", "盖帽", "抢断")
             if task.match_text(texts, w) is not None]
    return len(stats) >= 2


def _is_boost_training(task, texts):
    """加强训练二选一：标题「请选择一种加强训练」。

    这是这一页独有的标题（`handlers/training.py: handle_boost_training`
    的判据），其它页面不会有这句整句。
    """
    return task.match_text(texts, "请选择一种加强训练") is not None


def _is_prepare(task, texts):
    """赛前准备页：标题「赛前准备」+（跳过比赛 / 前往比赛）。

    两个词同时出现才算 —— 单看「跳过比赛」会撞活动赛赛程页。
    """
    if task.match_text(texts, "赛前准备") is None:
        return False
    return task.match_text(texts, ("跳过比赛", "前往比赛")) is not None


def _is_skill_select(task, texts):
    """技能三选一：标题「请选择1个技能」。

    这一页原来完全没登记（4116 帧里 45 帧，100% 被 event_choice 抢走）。
    「请选择1个技能」是这一页独有的标题。
    """
    return task.match_text(texts, "请选择1个技能") is not None


def _is_result_page(task, texts):
    """活动/打工结算页：「成功！」+「下一步」。

    和 `match_result`（比赛结算）分开：这一页是**地点活动**的结算
    （划船/野餐/打工），没有比分统计，只有「成功！」和奖励。

    2026-11-14 回放修正：原来只认「成功！/成功!」两个词形，
    但 OCR 把「成功」读成过「成动」「茂功」「俄动」（实测 111/16/1/1 次），
    19 帧因此掉进 unknown。改成形态判据：整框 2~3 字，
    含「功」或「动」字（成功 / 感动 / 激动的客人 / 成功）。
    """
    if task.match_text(texts, "下一步") is None:
        return False
    for t in texts:
        s = t.name.strip()
        if not (2 <= len(s) <= 3):
            continue
        if "功" in s or "动" in s:
            return True
    return False


def _is_training_plan(task, texts):
    """训练计划（日历）页：左上角标题「XX的训练计划」。

    必须整框（`plan_title` 区域）：训练日台词「今天的训练计划，我看看」
    也含这四个字，包含匹配会把台词当标题。
    """
    for t in task.texts_in_region(texts, "plan_title"):
        if labels.PLAN_TITLE_RE.match(t.name.strip()):
            return True
    return False


def _is_go_out(task, texts):
    """周末首页：有「出行」大按钮（地图上没有）。

    和 `weekend_map`（出行地图，整框地点名 ≥2）分开 ——
    2026-11-14 拆分：两者是**两个页面**，之前合并导致 42% 的地图帧
    被当成首页（首页有「出行」，地图没有）。
    """
    if len(_short_place_names(texts)) >= 2:
        return False
    return task.exact_text(texts, ("出行",)) is not None


def _choice_buttons(task, texts):
    """黄色选项按钮：成行、尺寸相近、框内有非数字名字。

    和 `handlers/match.py: handle_event_choice` 的黄框分支同源（阶段 2 合并）。
    只看黄框数量不够：扭蛋详情页的「100」价格也是黄框，训练剧本页的
    「东部大赛」标题卡 + 「启程」按钮也各是一个黄框（2026-11-13 回归集误判）。
    """
    buttons = task._yellow_buttons()
    if len(buttons) < 2:
        return []

    def center(b):
        return b[0] + b[2] / 2, b[1] + b[3] / 2

    def similar(a, b):
        return (max(a[2], b[2]) / max(min(a[2], b[2]), 1) < 2.6
                and max(a[3], b[3]) / max(min(a[3], b[3]), 1) < 2.2)

    row = []
    for b in sorted(buttons, key=lambda b: b[1]):
        group = [b]
        for c in buttons:
            if c is b:
                continue
            if (abs(center(b)[1] - center(c)[1]) < task.height * 0.05
                    and similar(b, c)):
                group.append(c)
        if len(group) > len(row):
            row = group
    if len(row) < 2:
        return []

    names = []
    for bx, by, bw, bh in row:
        inside = [t for t in texts
                  if bx <= (t.x + t.width / 2) <= bx + bw
                  and by <= (t.y + t.height / 2) <= by + bh]
        cand = [t for t in inside
                if 2 <= len(t.name.strip()) <= 12
                and not t.name.strip().isdigit()
                and t.name.strip() not in ("确定", "取消", "关闭", "下一步")]
        if cand:
            names.append(max(cand, key=lambda t: t.height))
    return names if len(names) >= 2 else []


def _has_yellow_choices(task, texts):
    return len(_choice_buttons(task, texts)) >= 2


def _has_centered_choices(task, texts):
    """居中竖排的选项组（`choice_candidates` 一列，中心 0.38~0.62）。

    训练动画页的「训练加成/表情加成」是横向三张，分组后每组只有 1 个；
    周结算页的「控卫/分卫…」是贴边一列，中心 0.26 —— 都被这条排除。

    还要排除「地点内页的属性面板」：公园/餐厅页底部那六行属性
    （投射/弹跳/防守…）也是居中一列的短词，但不是选项。
    """
    group = task.choice_candidates(texts)
    if len(group) < 2 or len(group) > 3:
        return False
    centers = [(t.x + t.width / 2) / task.width for t in group]
    if max(centers) - min(centers) > 0.06:
        return False
    if not 0.38 <= sum(centers) / len(centers) <= 0.62:
        return False
    # 属性面板：六维名 + 数值（分开两个框，数值那列更靠右）。
    if any(any(a in t.name for a in labels.TRAINING_SUBJECTS) for t in group):
        return False
    return True


def _is_boost_status(task, texts):
    """加成状态页：底部列着「表情加成 / 倍率加成 / 属性加成 / 额外队友」等增益标签。

    2026-11-14 回放：4116 帧里有 56 帧属于这一页，之前全掉进 unknown，
    被 `handle_event_choice` 兜底认领。它和事件选项页长得像（都在底部属性
    面板上方），但**没有可点选项**。

    只认**标签**词，不能用泛词「加成」—— 正文里到处是「加成」
    （「器材加成-70熟练度」「训练加成状态」「暴击训练的加成」都是效果描述，
    见 `logs/.../1112-002441_handle_event_choice.jpg`）。
    """
    for t in texts:
        s = t.name.strip()
        if len(s) <= 6 and (s.endswith("加成") or s in ("额外队友", "Bonus", "Bonus:")):
            return True
    return False


def _is_event_choice(task, texts):
    """事件选项：**成行的黄色选项按钮**（≥2 个），且不是邻居页面。

    2026-11-14 实测：竖排分支（`choice_candidates` 居中一列）会和
    公园/餐厅的属性面板、选股页的卡片列重叠 —— 同一形态在多个页面出现。
    阶段 1 只保留**最可靠**的黄框形态，竖排事件页等阶段 2 再用
    「页面级标题」单独登记。

    排除项都是**页面级强特征**，不是正文词：
    科目页有「失败率」、结算页有「下一步」、首页/内页有「出行」、
    地图有 ≥2 个整框地点名、赛程页有「继续匹配 / 暂时退出」、
    选股页有「请选择你要投资的股票」。
    """
    if not (_has_yellow_choices(task, texts)
            or _has_centered_choices(task, texts)
            or len(task.options_not_paragraph(texts)) >= 2):
        return False
    if task.match_text(texts, "失败率") is not None:
        return False
    if task.match_text(texts, "下一步") is not None:
        return False
    if task.match_text(texts, "出行") is not None:
        return False
    if task.match_text(texts, ("继续匹配", "暂时退出")) is not None:
        return False
    if task.match_text(texts, labels.STOCK_PICK_TITLE) is not None:
        return False
    if len(_short_place_names(texts)) >= 2:
        return False
    # 地点内页的底部按钮（野餐/划船、桌游GM/当服务员）也是居中一列短词，
    # 形态和竖排事件选项一样 —— 用**页面标题**区分（2026-11-14 实测）。
    if _is_park(task, texts) or _is_restaurant(task, texts):
        return False
    return True


def _is_weekend_map(task, texts):
    if task.match_text(texts, "出行") is not None:
        return False
    return len(_short_place_names(texts)) >= 2


def _is_training_plan(task, texts):
    """训练计划（日历）页：左上角标题「XX的训练计划」。

    2026-11-14 回放修正：原来用 `labels.PLAN_TITLE_RE` 匹配**整框**，
    但那一页的标题被 OCR 拆成两个框 —— 角色名「真崎」一个框、
    「的训练计划」另一个框（实测 4 张存证帧全是这样），所以整框正则
    永远不中，35 帧全掉进 restaurant（活动格里也写着「当服务员」）。

    改成：plan_title 区域里有「的训练计划」这个框就算。
    区域限定仍要保留 —— 训练日台词「今天的训练计划，我看看」也含这四个字，
    但它不在左上角标题区（`parsers.read_plan_captain` 用同一招）。
    """
    for t in task.texts_in_region(texts, "plan_title"):
        if "的训练计划" in t.name:
            return True
    return False


def _is_restaurant(task, texts):
    """餐厅内页：标题是「在餐厅 / 桌游GM / 当服务员」之一。

    但**详情页**（点开某个打工项）也算餐厅流程 —— 那页有「前往当服务员」，
    同样属于餐厅 handler 的工作范围。

    2026-11-14 回放修正：日历页（训练计划）的活动格里也写着「当服务员」
    「桌游GM」「野餐」—— 32 帧因此被误判成餐厅页。
    排除：训练计划页有自己独有的左上角标题。
    """
    if task.exact_text(texts, labels.RESTAURANT_TITLE) is not None:
        if _is_training_plan(task, texts):
            return False
        return True
    return task.match_text(texts, ("前往当服务员", "前往桌游GM")) is not None


def _is_street(task, texts):
    """步行街扭蛋页：标题是「请选择1个扭蛋」或「超级扭蛋机」。

    2026-11-14 修正：原来标题含「扭蛋」就行，「扭蛋加成」技能状态说明页
    会被误判成扭蛋页（实跑 11:37:52）。现在只认扭蛋页专有标题。
    """
    if task.match_text(texts, labels.STREET_TITLE) is None:
        return False
    for t in texts:
        name = t.name.strip()
        if any(w in name for w in labels.STREET_TITLE_EXCLUDE):
            return False
    return len(_short_place_names(texts)) < 2


def _is_park(task, texts):
    """公园内页：标题含「在公园 / 干点什么好呢 / 野餐」。

    必须**整框**：事件正文里的「在公园打街球」（2026-11-13 事故）
    会命中包含匹配；整框 + 短标签才不会。
    """
    if task.match_text(texts, "出行") is not None:
        return False
    if len(_short_place_names(texts)) >= 2:
        return False
    for name in labels.PARK_TITLE:
        if task.exact_text(texts, (name,)) is not None:
            return True
    return False


def _is_shop(task, texts):
    """商店内页：有「商店」且有「上新时间 / 剩余 / 能力详情」之一。"""
    if task.match_text(texts, "出行") is not None:
        return False
    if task.match_text(texts, "商店") is None:
        return False
    return task.match_text(texts, ("上新时间", "剩余", "能力详情")) is not None


def _is_result(task, texts):
    """结算页：标题含「恭喜获得 / 训练奖励 / 训练评分」等。"""
    if task.match_text(texts, labels.SQUAD_AWARD_MARKS) is not None:
        return True
    return task.match_text(texts, labels.TRAINING_SCORE_MARKS) is not None


def _is_story_arrow(task, texts):
    """剧情/事件推进页：底部台词条有 12 字以上长中文，且右下角有白色 ▼。

    和 `handlers/entry.py: handle_story_arrow` 同源：光靠像素的 ▼ 太宽
    （几乎每页右下角都有白像素，实测会把 11 张回归图误判成剧情页），
    必须再叠一条「底部台词条里有长中文」。
    """
    if task.match_text(texts, ("失败率", "决定", "休息", "赢得比赛", "VS")) is not None:
        return False
    if len(_short_place_names(texts)) >= 2:
        return False
    # 有其它页面的强特征就不是剧情页
    if task.exact_text(texts, labels.RESTAURANT_TITLE) is not None:
        return False
    if task.match_text(texts, labels.STREET_TITLE) is not None:
        return False
    if task.match_text(texts, "商店") is not None:
        return False
    if task.match_text(texts, "训练剧本") is not None:
        return False
    if not task.has_story_arrow():
        return False
    x1, y1, x2, y2 = Defs.region("dialog_text")
    for t in texts:
        cx = (t.x + t.width / 2) / task.width
        cy = (t.y + t.height / 2) / task.height
        if not (x1 <= cx <= x2 and y1 <= cy <= y2):
            continue
        s = t.name.strip()
        if len(s) < 12:
            continue
        if any(ch.isdigit() for ch in s) or "/" in s:
            continue
        if any("\u4e00" <= ch <= "\u9fff" for ch in s):
            return True
    return False


_ANCHORS = {
    "game_title": _is_game_title,
    "construction": _is_construction,
    "register_player": _is_register_player,
    "training_subject": _is_training_subject,
    "event_choice": _is_event_choice,
    "weekend_map": _is_weekend_map,
    "go_out": _is_go_out,
    "restaurant": _is_restaurant,
    "street": _is_street,
    "park": _is_park,
    "shop": _is_shop,
    "result": _is_result,
    "story_arrow": _is_story_arrow,
    # --- 2026-11-14 补登记（依据 4116 张实跑存证帧回放）---
    "match_result": _is_match_result,
    "boost_status": _is_boost_status,
    "boost_training": _is_boost_training,
    "prepare": _is_prepare,
    "skill_select": _is_skill_select,
    "result_page": _is_result_page,
    "training_plan": _is_training_plan,
}


def classify_page(task, texts):
    """返回 `(page_id, candidates)`。

    `page_id` 是优先级最高的命中页；没有命中时是 None。
    `candidates` 是**所有**命中的页面（按优先级），用来发现判据重叠。
    """
    hits = [name for name, check in _ANCHORS.items() if check(task, texts)]
    if not hits:
        return None, ()
    hits.sort(key=lambda name: PRIORITY[name])
    return hits[0], tuple(hits)
