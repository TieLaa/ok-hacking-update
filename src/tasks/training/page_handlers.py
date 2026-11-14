"""handler ↔ 页面 的映射表。

阶段 2 第 4 步（切执行路径）用：分类器认出页面后，先跑这个页面对应的
handler；**跑不到才退回全量顺序遍历**。

为什么这么设计（安全第一）：
    44 个 handler 的顺序就是优先级（`handlers/__init__.py` 的注释写得很清楚：
    「共用『下一步/确定』这类通用词的，必须排在后面的专属 handler 之后」）。
    顺序里沉淀了十几年踩坑的教训，不能因为分类器「更聪明」就丢掉。

    所以分类器只当**加速器**：认得出页面 → 先试这个页面的 handler；
    认不出 / 认错 → 照旧顺序遍历。**任何情况下都不会漏掉 handler**。
    最坏情况 = 现在的行为（多跑几个 handler 的判据）。

映射表的边界：
    - 只登记「一个页面 ↔ 一个 handler」的干净对应；
    - 一个 handler 管多个页面（比如 handle_activity_go 同时管餐厅和公园详情），
      或者一个页面对应多个 handler（比如注册球员流程有 5 步），
      都**不登记** —— 那些页面交给顺序遍历。
"""

# 页面 ID → handler 名
PAGE_HANDLERS = {
    "announcement": "handle_announcement",
    "game_title": "handle_game_title",
    "training_score": "handle_training_score",
    "register_player": "handle_register_player",
    "stamina_short": "handle_stamina_short",
    "stamina_recover": "handle_stamina_recover",
    "rest_confirm": "handle_rest_confirm",
    "training_plan": "handle_training_plan",
    "match_intro": "handle_match_intro",
    "note_study": "handle_note_study",
    "skill_select": "handle_skill_select",
    "spin_wheel": "handle_spin_wheel",
    "boost_training": "handle_boost_training",
    "theme_select": "handle_theme_select",
    "manuscript_submit": "handle_manuscript_submit",
    "ingredient_select": "handle_ingredient_select",
    "izumi_question": "handle_izumi_question",
    "stock_pick": "handle_stock_pick",
    "construction": "handle_construction",
    "event_choice": "handle_event_choice",
    "prepare": "handle_prepare",
    "lobby": "handle_lobby",
    "training_entry": "handle_training_entry",
    "captain": "handle_captain",
    "team": "handle_team",
    "equipment": "handle_equipment",
    "confirm_team": "handle_confirm_team",
    "street": "handle_street",
    "arcade": "handle_arcade",
    "shop": "handle_shop",
    "go_out": "handle_go_out",
    "weekend_map": "handle_weekend",
    "popup": "handle_popup",
    "story_arrow": "handle_story_arrow",
    "vacation": "handle_vacation",
    "breath_difficulty": "handle_breath_difficulty",
    "stamina_short": "handle_stamina_short",
}

# 一个页面对应**多个** handler 时，按顺序表里的先后列出。
# 比赛结算页有两个 handler 管：`handle_match_result`（位置 15，比赛结算专用）
# 和 `handle_result`（位置 54，通用结算页）。顺序表里前者在前，
# 路由也必须先试前者 —— 否则会跳过更专属的那个。
PAGE_HANDLER_ORDER = {
    "match_result": ("handle_match_result", "handle_result"),
    "result_page": ("handle_result",),
}


def handlers_for(page):
    """这个页面对应的 handler 名列表（按顺序表里的先后）。

    一个页面要多个 handler 时（比赛结算页有专用的和通用的两个），
    路由要按顺序全试一遍。
    """
    if page in PAGE_HANDLER_ORDER:
        return PAGE_HANDLER_ORDER[page]
    name = PAGE_HANDLERS.get(page)
    return (name,) if name else ()
