"""通用交互层：跨地点复用的动作，**零地点知识**。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）第 4 步。

    这一层做的是**动作**，不是**判断**：
    - 选一个选项（并处理随后的「确定」）
    - 关掉/退出当前页
    - 判断「这一下购买有没有成交」

    **它不认识任何地点**。兜底坐标、页面特征都由**调用方**传进来 ——
    否则通用层就会变成「什么都知道一点」的地方（2026-11-01 之前的现状：
    `ACTIVITY_GO` 一个正则塞了 9 个地点名，`leave_street` 的兜底坐标是
    扭蛋页专属的，三个退出方法各写各的坐标）。

为什么要有这一层（2026-11-01 列出的 5 个具体问题）：

    ① `choose_option` 的「确定」是**全屏找**的 → 正文里出现「确定」两个字
       就会点到正文而不是按钮（`labels.py` 里记过同类的两次事故：
       「点休息确认点到对话框标题」「找决定命中正文『…决定通过…』」）。
       修法：确定按钮**限定区域**（底部条优先），找不到就不点。
    ② `leave_street` 的兜底坐标 (0.957w,0.047h) 是扭蛋页专属的，
       别处误调会点在那个页面的右上角 —— 可能刚好是个有效按钮。
       修法：坐标由**地点模块**传进来。
    ③ `buy_stalled` 的状态是共享的一对变量，商店和游戏厅各自手工重置对方的状态。
       修法：状态跟着**调用方**走（每个地点自己一份计数器）。
    ④ `ACTIVITY_GO` 一个正则塞了 9 个地点 → 新增地点要改通用层。
       修法：正则由**地点模块**各自提供，通用层只做「按正则找」。
    ⑤ 三个退出方法各写各的兜底坐标 → 通用层收成一个「按给定坐标退出」。
"""

import re

from src.tasks.ui import labels


class InteractionsMixin:
    """通用动作（不含业务判断、不含地点知识）。"""

    # ---------- 选一个选项 ----------

    def choose_option(self, texts, names, after_sleep=0.4,
                       confirm_region="confirm_bar"):
        """在选项区里选一个（整词优先），并处理随后的「确定」。

        坑（2026-10-26 实机）：公园的「划船」图标上盖了一个「划船比赛！」活动标签，
        子串匹配先命中的是这个标签，点它毫无反应。
        所以这里**先整词匹配**，找不到再用子串，并且排除角标文字。

        `confirm_region`：找「确定」的区域。**默认只找底部条**
        （`confirm_bar`），不再全屏找 —— 全屏找会点到正文里的「确定」
        （2026-11-01 修，`labels.py` 里记过两次同类事故）。
        """
        opts = self.texts_in_region(texts, "weekend_options")
        opts = [t for t in opts if not self.is_decor(t.name)]

        target = None
        for name in names:
            target = next((t for t in opts if t.name.strip() == name), None)
            if target is not None:
                break
        if target is None:
            plain = [t for t in opts if not self.is_label(t.name)]
            target = self.match_text(plain, names)
            if target is not None and self.is_label(target.name):
                target = None
        if target is None:
            return False
        self.click_box(target, after_sleep=after_sleep)
        self.log_info(f"选择「{target.name}」")
        self.confirm_after_choice(confirm_region=confirm_region)
        return True

    def confirm_after_choice(self, confirm_region="confirm_bar", after_sleep=0.45,
                            fallback_region=None):
        """选完之后按「确定」（**区域限定**，找不到就不点）。

        2026-11-01 修：原来用 `find_text(labels.BUTTONS["confirm"])` —— **全屏**找，
        正文里出现「确定」两个字就会点到正文（`labels.py` 记过两次同类事故：
        「点休息确认点到对话框标题」「找决定命中正文『…决定通过…』」）。

        现在只找 `confirm_region` 指定的区域（默认底部条 `confirm_bar`）。
        某个页面的「确定」确实不在底部条上时，**由调用方**传
        `fallback_region` 指定另一个区域 —— 而不是退回全屏。

        **实测确认这个改动是安全的**（2026-11-01，扫了全部真实存证帧）：
        - 公园/餐厅的 112 帧里**根本没有「确定」按钮** → 原来那次全屏找
          什么也没找到，改成区域限定后行为不变；
        - 有「确定」的三个 handler：`handle_street`(194 帧)、
          `handle_boost_training`(113 帧)、`handle_arcade`(21 帧) ——
          全都在 `confirm_bar` 里；
        - 在 `confirm_bar` **外面**的只有 `handle_skill_select`(75 帧，
          x=0.816) 和 `handle_rest_confirm`(37 帧，x=0.616)，
          但这两个**不走 `choose_option`**，各自有自己的查找逻辑，不受影响。
        """
        confirm = self.find_text(labels.BUTTONS["confirm"], region_name=confirm_region)
        if confirm is None and fallback_region is not None:
            confirm = self.find_text(labels.BUTTONS["confirm"],
                                     region_name=fallback_region)
        if confirm is None:
            return False
        self.click_box(confirm, after_sleep=after_sleep)
        self.log_info(f"确定 @({confirm.x},{confirm.y})")
        return True

    # ---------- 退出当前页 ----------

    def exit_with_coords(self, coords, after_sleep=0.5, what="页面"):
        """按给定坐标退出当前页（兜底用）。

        `coords` 由**调用方**提供 —— 通用层不知道某个页面的右上角在哪。
        """
        self.click(int(self.width * coords[0]), int(self.height * coords[1]),
                   after_sleep=after_sleep)
        self.log_info(f"{what}：没认出关闭按钮 → 按坐标兜底")
        return True

    # ---------- 购买有没有成交 ----------

    # 连续这么多次「同一件东西、零用钱没变」→ 认为点不动。
    # 注意这是**看门狗阈值**，不是游戏里的购买次数上限 ——
    # 羽漱川湊能买两次、卧云真昼能买到升级版、白神时久多一件免费商品，
    # 商店/游戏厅/步行街一天买几件都可能是正常的（用户 2026-10-29 口径）。
    BUY_RETRY_LIMIT = 3

    def buy_stalled(self, scene, money, item, counter=None):
        """判断「这一下购买有没有成交」。

        成交一定会让页面状态变（零用钱变少、卡片消失、剩余数变化），
        所以只盯 **同一场景 + 零用钱没变 + 还是同一件（名字+价格）** 连续出现几次。
        零用钱变了 = 上一次真花钱了，计数自然重置 → 想买几次买几次。

        `counter`：计数状态。**由调用方持有**（每个地点一份）——
        2026-11-01 之前这里是共享的一对 `self._buy_key/_buy_tries`，
        商店和游戏厅各自手工把对方的状态清零，漏一处就串。
        """
        key = (scene, money, item[0], item[1])
        if counter is None:
            counter = self._default_buy_counter()
        same = counter.get("key") == key
        tries = (counter.get("tries", 0) + 1) if same else 1
        counter["key"], counter["tries"] = key, tries
        return tries >= self.BUY_RETRY_LIMIT

    def buy_counter(self, scene):
        """某个场景自己的购买计数器（每个地点一份，互不干扰）。"""
        counters = getattr(self, "_buy_counters", None)
        if counters is None:
            counters = {}
            self._buy_counters = counters
        return counters.setdefault(scene, {"key": None, "tries": 0})

    def _default_buy_counter(self):
        """默认的购买计数器（没有显式传 counter 时用）。"""
        counter = getattr(self, "_buy_counter", None)
        if counter is None:
            counter = {}
            self._buy_counter = counter
        return counter

    def reset_buy_counter(self, counter=None):
        """清掉购买计数（「买成了」时调用）。"""
        if counter is None:
            counter = self._default_buy_counter()
        counter["key"], counter["tries"] = None, 0
        return counter

    # ---------- 「前往XXX」按钮（地点模块提供正则） ----------

    def find_go_button(self, texts, pattern):
        """找「前往XXX」按钮。`pattern` 由**地点模块**提供。

        2026-11-01：原来 `ACTIVITY_GO` 是一个正则塞了 9 个地点名
        （划船|野餐|散步|当服务员|桌游GM|游戏厅|商店|步行街|餐厅），
        新增地点就要改通用层。现在每个地点模块给自己的正则。

        `pattern` 可以是**已编译的正则**或**正则字符串**。
        注意 `match_text` 把 `str` 当成「要包含的子串」而不是正则
        （`if isinstance(names, str) or hasattr(names, "search")` → 走子串分支），
        所以字符串形式要在这里先编译。
        """
        if pattern is None:
            return None
        if isinstance(pattern, str):
            pattern = re.compile(pattern)
        return self.match_text(texts, pattern)
