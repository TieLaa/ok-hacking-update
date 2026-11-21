"""准备页三模式（自动选择 / 半自动 / 预配置）的决策层。

只做**判断**，不点击；点击仍由 `handlers/entry.py` 的三个 handler 执行。
这样模式规则只有一份，三个页面不会各写一遍。

阶段 1：预配置是占位，行为等同自动选择；真正的选人逻辑在阶段 2。
"""

import time

MODE_AUTO = "自动选择"
MODE_SEMI = "半自动"
MODE_PRESET = "预配置"
MODES = (MODE_AUTO, MODE_SEMI, MODE_PRESET)
KEY = "准备页模式"

# 旧叫法（用户早期口径）→ 新选项
_ALIASES = {"全自动": MODE_AUTO}

PAGE_LABELS = {
    "team": "选择队友",
    "note": "选择笔记",
    "equipment": "选择器材",
}

WAIT_HEARTBEAT_SECONDS = 30


def mode(task):
    """当前准备页模式；配置缺失/非法一律回落到自动选择。"""
    if getattr(task, "config", None) is None:
        return MODE_AUTO
    # 写成 `task.config.get("准备页模式")` 是为了过 check_handlers 的配置门禁；
    # 其它地方写回时用 `prep.KEY`，两处必须保持一致。
    value = (task.config.get("准备页模式") or MODE_AUTO).strip()
    value = _ALIASES.get(value, value)
    return value if value in MODES else MODE_AUTO


def page_label(page):
    return PAGE_LABELS.get(page, page)


def is_first_round(task):
    """本 run 的第一局。`run_rounds` 在每局开头写 `_round_no`（从 1 开始）。"""
    return int(getattr(task, "_round_no", 0) or 0) <= 1


def decide(task, page, empty):
    """返回 `auto` / `next` / `wait`。

    `empty`：每个槽位是否为空（True=空）；`None` = 没识别出状态。
    """
    any_empty = True if empty is None else any(empty)
    full = empty is not None and not any_empty

    # MCP / CLI：不等待；空→自动选择，满→下一步；状态未知按空处理
    if getattr(task, "mcp_driven", False):
        return "next" if full else "auto"

    current = mode(task)
    if current == MODE_SEMI:
        # 状态未知不盲点，继续等；这是半自动唯一安全的兜底
        return "next" if full else "wait"

    # 自动选择 / 预配置（阶段 1 占位）：
    # 第一局保持现状，始终点自动选择；后续局满了才直接下一步。
    if is_first_round(task):
        return "auto"
    return "next" if full else "auto"


def begin_wait(task, page, detail=""):
    """进入等待态（同一页重复调用不重复打日志）。"""
    state = getattr(task, "_prep_wait", None)
    if state is not None and state.get("page") == page:
        return state
    now = time.time()
    state = {"page": page, "since": now, "last_log": now}
    task._prep_wait = state
    info = getattr(task, "info", None)
    if isinstance(info, dict):
        info["prep_wait"] = page_label(page)
    task.log_info(f"准备页：有空位 → 等待用户（{page_label(page)}{detail}）",
                  notify=True)
    return state


def clear_wait(task):
    """退出等待态；返回之前等待的页面（没在等返回 None）。"""
    state = getattr(task, "_prep_wait", None)
    if state is None:
        return None
    task._prep_wait = None
    info = getattr(task, "info", None)
    if isinstance(info, dict):
        info.pop("prep_wait", None)
    return state.get("page")


def waiting_page(task):
    state = getattr(task, "_prep_wait", None)
    return state.get("page") if state else None


def wait_heartbeat(task, now=None):
    """等待超过 30 秒写一行心跳，避免看起来像死机。测试可传 now。"""
    state = getattr(task, "_prep_wait", None)
    if state is None:
        return False
    now = time.time() if now is None else now
    if now - state.get("last_log", state["since"]) < WAIT_HEARTBEAT_SECONDS:
        return False
    state["last_log"] = now
    minutes = int((now - state["since"]) // 60)
    task.log_info(f"准备页：仍在等待用户（{page_label(state['page'])}，已 {minutes} 分钟）")
    return True
