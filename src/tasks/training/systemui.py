"""系统级 UI：**固定位置、固定逻辑**的控件。

用户 2026-11-01 定下的分层（见 `docs/ARCHITECTURE.md`）：

    **系统级** —— 位置固定、行为固定，跟业务无关：
        左上角返回、右上角 倍速/历史记录/菜单/返回、
        左下角 快进/倍速/自动·手动。
        这些在**任何页面**都在同一处、作用一样 → 归这里。

    **事件级** —— 具体的界面事件（训练日、周末地图、某个地点…），
        归各自的业务模块。

为什么单独一层（2026-11-01 的实际理由）：

    这些控件的**坐标是版式常量**，但它们的**逻辑散在各处**：
    - 倍速图标：`Defs.SPEED_ICON_CLICK` 在 Defs，策略在 `match.ensure_speed`，
      却被 `training.handle_training_day` 调用（跨域调用）；
    - 比赛内快进：`Defs.MATCH_FAST_FORWARD`，策略在 `match.handle_in_match`；
    - 右上角返回：`BaseGirlsTask.exit_page` 用 (0.945, 0.040)，
      `weekend.leave_street` 用 (0.957, 0.047) —— **两个不同的点**，
      各写各的。

    归到一起之后：坐标只有一份、判读只有一份、点击策略只有一份。

**这一层不做业务判断**（今天练不练、去哪个地点），只管「按这个固定控件」。
"""

from src.tasks.ui import Defs, labels
from src.tasks.ui import topbar


class SystemUIHandlers:
    """系统级 UI 的判读与点击。"""

    def top_icon_point(self, name):
        """某个顶栏图标的点击坐标（相对坐标）。"""
        return Defs.TOP_ICON_POINTS[name]

    # ---------- 顶栏右上角四个图标 ----------

    # 图标从左到右：倍速 / 历史记录 / 菜单 / 返回（Defs.TOP_ICON_POINTS）。
    # 「菜单」**禁止点击**（误触会毁局，见 Defs 里的注释）。
    TOP_ICON = ("speed", "history", "menu", "back")

    # 左上角的「训练计划日历」按钮 —— **系统级固定位置**。
    # 用户 2026-11-01：日历的位置是系统级的，位置可以复用 ——
    # 训练室（训练日）和校门口（休息日）左上角都是它，点开就是同一个日历页。
    #
    # 实测：训练日主界面点 (0.035w, 0.06h) 会打开「XX的训练计划」日历。
    # 坑（BaseGirlsTask.exit_page 的注释）：这个位置在训练日是**日历**、不是返回。
    CALENDAR_POINT = (0.035, 0.06)

    def click_calendar(self, after_sleep=0.8):
        """点左上角打开训练计划日历（系统级固定位置）。"""
        x, y = self.CALENDAR_POINT
        self.click(int(self.width * x), int(self.height * y), after_sleep=after_sleep)
        return True

    def click_top_icon(self, name, after_sleep=0.5):
        """点顶栏右上角的某个图标（菜单除外）。"""
        if name == "menu":
            # 「菜单」点了是「保存后中断 / 放弃 / …」，误触毁局 —— 不提供点击。
            self.log_warning("顶栏「菜单」禁止点击（误触毁局）")
            return False
        x, y = self.top_icon_point(name)
        self.click(int(self.width * x), int(self.height * y), after_sleep=after_sleep)
        return True

    # ---------- 倍速（系统级；判读在 ui/topbar.py，策略在这里） ----------

    # 连续点 N 次状态都没变 → 锁到下一周，避免每拍刷屏。
    # 这是**看门狗阈值**，不是游戏上限。
    SPEED_STALL_LIMIT = 2

    def speed_level(self, texts):
        """当前倍速档位。返回 (文字标签, 是否到顶, 是否 OFF)。

        文字标签只用来记日志 —— 策略不依赖它（OCR 小字会漏）。

        像素判读用的是**本拍识别用的那一帧**（`flow.run` 存在 `_tick_frame`），
        不是 `self.frame`：`self.frame` 是采集线程的最新画面，读到它时页面可能已经
        翻页/淡入到一半，和 `texts` 描述的不是同一屏。
        """
        frame = getattr(self, "_tick_frame", None)
        if frame is None:
            frame = self.frame
        at_max = topbar.speed_at_max(frame)
        label = topbar.speed_label(texts, self.width, self.height)
        off = topbar.off_text(texts, self.width, self.height)
        return label, at_max, off

    def ensure_speed(self, texts):
        """把倍速点到**本存档当前解锁的最高档**（不假设是 2x 还是 3x，更不写死 3）。

        依据（2026-11-01 用户口径 + 逐档实测）：
        - 点击是**循环且一次只前进一档**：1x → 2x → 3x → OFF → 1x；
        - 高倍率随游戏进度逐步解锁，所以「最高档」这个数字**不是常量**；
        - 图标里实心三角的个数就是档位，还有空心三角就说明没到顶。

        所以本函数**一拍只点一次**（本帧的 `self.frame` 到下一拍才更新，
        盲连点会点过头，从 3x 连点两下直接回到 1x），下一拍重新判
        「还有没有空心三角」再决定要不要继续。

        只在「训练日主界面 / 赛前准备」这两个确定安全的时机调用，
        避免在结算页、商店页乱点顶栏。

        **判不出来就不点**（`speed_at_max` 返回 None）：这条横带里没画倍速图标
        （别页面 / 页面还在淡入）时，「有空心 = 没到顶」是假的 ——
        2026-11-02 23:41:34 就是这样把 3x 点成了 OFF（炒股页回来后第一拍）。
        """
        if self.match_text(texts, labels.WEEK_RE) is None:
            return False
        week_m = labels.WEEK_RE.search("".join(t.name for t in texts).replace(" ", ""))
        week = int(week_m.group(1)) if week_m else None

        label, at_max, off = self.speed_level(texts)
        if at_max is None:                       # 帧读不出来 / 这页没有倍速图标：不猜、不乱点
            self.log_debug("倍速：这一拍判不出图标状态（可能页面还在淡入）→ 不点")
            return False
        if at_max and not off:                   # 已经没有空心三角 = 到顶了
            return False

        if self._speed_week != week:
            self._speed_week = week
            self._speed_clicks = 0
            self._speed_stalls = 0
            self._speed_locked = False
            self._speed_sig = None
        if self._speed_locked:
            return False

        # 上一拍点过了：看这一拍状态有没有变化，没变就记一次"没进展"
        sig = (label, at_max, off)
        if self._speed_sig is not None and sig == self._speed_sig:
            self._speed_stalls += 1
            if self._speed_stalls >= self.SPEED_STALL_LIMIT:
                self._speed_locked = True
                self.log_info("倍速点了 2 次状态都没变，本周不再尝试")
                return False

        self.click_top_icon("speed", after_sleep=0.5)
        self._speed_clicks += 1
        self._speed_sig = sig
        self.log_info(f"倍速 +1 档（当前 {label or '未读出'}，图标仍有空心三角）")
        return True

    # ---------- 比赛内 HUD（左下角） ----------

    def click_match_fast_forward(self, after_sleep=0.6):
        """点比赛内左下角的 ▶▶ 快进（无文字图标，只能按位置点）。"""
        x, y = Defs.MATCH_FAST_FORWARD
        self.click(int(self.width_of_screen(x)), int(self.height_of_screen(y)),
                   after_sleep=after_sleep)
        return True

    # ---------- 返回（系统级；各页面坐标不同，调用方传候选） ----------

    # 返回箭头实测过的两个位置（相对坐标）：
    #   训练页/日历页 (0.945, 0.040)
    #   周末地点内页 (0.957, 0.047)
    # 两者不是同一个点，恢复时轮流点。
    BACK_POINTS = ((0.945, 0.040), (0.957, 0.047))

    def click_back(self, after_sleep=0.45):
        """点右上角的返回箭头（系统级坐标）。"""
        x, y = Defs.TOP_ICON_POINTS["back"]
        self.click(int(self.width * x), int(self.height * y), after_sleep=after_sleep)
        return True
