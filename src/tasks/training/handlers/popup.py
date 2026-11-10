"""弹窗 / 抽奖 / 休息确认。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""


from src.tasks.ui import labels


class PopupHandlers:

    def handle_rest_confirm(self, texts):
        """「休息需要消耗一天，可恢复精力，是否要休息？」确认框：点「确定」。

        必须排在 handle_popup 之前。旧逻辑把「取消」当通用弹窗关闭词，
        结果点了休息→确认框→点取消→再点休息，永远休息不了（实训 2026-10-26）。
        """
        if self.match_text(texts, labels.REST_CONFIRM_TEXTS) is None:
            return False
        # 按钮是 [取消] [确认]；**不能**整词匹配「休息」——左上角标题就叫「休息」，
        # 点了等于点对话框标题，什么都不发生（2026-10-26 实训卡死）。
        target = self.exact_text(texts, labels.BUTTONS["confirm"])
        if target is None:
            bottom = [t for t in texts
                      if t.name.strip() == "休息"
                      and (t.y + t.height / 2) / self.height > 0.62]
            target = bottom[0] if bottom else None
        if target is not None:
            self.click_box(target, after_sleep=0.9)
            self.log_info(f"休息确认：点「{target.name.strip()}」")
            return True
        return False

    def handle_resume_training(self, texts):
        """大厅「训练确认」弹窗：**只继续训练，永不删档**。

        实机（2026-10-26 用户截图）：标题「训练确认」+ 主角名 + 目标
        「赢得与XXX的比赛」，按钮是 [删档]（青色，左）[继续训练]（黄色，右）。
        这个弹窗盖在大厅上，背景还能 OCR 到「赛事 / 社团GO / 训练中」，
        所以优先级要排在 handle_lobby 前面，否则会被当成大厅反复点「训练」。

        用户 2026-11-02 口径：**开训练消耗的专注在删档时不会返还**，所以
        删档是纯亏损操作，脚本里不允许存在这个动作。「新一局删档」开关
        已从 config_spec 删除；即使旧配置里残留 True 也不会再被读。
        """
        if self.match_text(texts, "训练确认") is None:
            return False
        # 按钮按整词找；match_text 的兜底会返回「最长的框」，在弹窗里可能
        # 落到别的按钮上 —— 这里只接受名字里真的含「继续训练」的框。
        target = self.exact_text(texts, ("继续训练",))
        if target is None:
            cands = [t for t in texts if "继续训练" in t.name]
            target = max(cands, key=lambda t: len(t.name)) if cands else None
        if target is not None:
            self.click_box(target, after_sleep=0.9)
            self.log_info("训练确认：继续上次训练")
            return True
        # 认不出文字时用实测相对坐标（黄色主按钮中心）。
        # 左边青色的是「删档」——不能套用「点最左 / 最右按钮」这类通用规则。
        self.click(int(self.width * 0.65), int(self.height * 0.75), after_sleep=0.9)
        self.log_warning("训练确认：没认出「继续训练」，按固定坐标点黄色按钮（不点删档）")
        return True

    def handle_popup(self, texts):
        """公告 / 领奖 / 月卡 / 提示：先关掉，否则什么都点不动。

        「点击屏幕继续」类过场交给 handle_tap_to_continue（点那行字）。
        """
        # 「点击屏幕继续」：**点那行字本身**（用户 2026-10-28：MVP 页点屏幕正中
        # 没反应）。同一画面第二拍还是这页，才退回点屏幕中央（训练日开场的
        # 「新目标」弹窗是整屏可点，点文字可能被下面的按钮挡住）。
        hint = self.match_text(texts, labels.TAP_TO_CONTINUE)
        if hint is not None:
            sig = frozenset(t.name.strip() for t in texts)
            if getattr(self, "_tap_sig", None) == sig:
                self._tap_count = getattr(self, "_tap_count", 1) + 1
            else:
                self._tap_sig, self._tap_count = sig, 1
            if self._tap_count <= 1:
                self.click_box(hint, after_sleep=0.4)
                self.log_info(f"弹窗：点击屏幕继续（点文字 "
                              f"@{hint.x + hint.width // 2},{hint.y + hint.height // 2}）")
            else:
                self._tap_count = 0
                self.click(int(self.width * 0.5), int(self.height * 0.4), after_sleep=0.4)
                self.log_info("弹窗：点击屏幕继续（文字没反应，改点中央）")
            return True
        # 训练后的过场动画（2026-10-27 用户截图）：「球队经理的帮助！」+ 经理立绘，
        # 点屏幕任意位置继续。以前没人认领 → 走 unknown 兜底 + 每 3 秒存证一张。
        # **必须排除加强训练页**：那页左上角也写着「球队经理的帮助」，
        # 而它有真正的二选一卡（handle_boost_training 处理），不能被这里点掉。
        if (self.match_text(texts, "球队经理的帮助") is not None
                and self.match_text(texts, "加强训练") is None):
            self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=0.5)
            self.log_info("过场：球队经理的帮助")
            return True
        target = self.match_text(texts, ("今日不再提示", "关闭"))
        if target:
            self.click_box(target, after_sleep=0.35)
            self.log_info(f"关闭弹窗「{target.name}」")
            return True
        # 「取消」只在**没有确定按钮**的纯提示框里点；有确定按钮的确认框
        # 交给具体 handler（休息/购买），否则会把用户想做的操作取消掉。
        if self.match_text(texts, "确定") is None:
            cancel = next((t for t in texts if t.name.strip() == "取消"), None)
            if cancel is not None:
                self.click_box(cancel, after_sleep=0.35)
                self.log_info("关闭弹窗「取消」")
                return True
        # 抽卡/活动横幅：只有「立即前往」和右上角一个 X，没有「关闭」文字。
        # 不处理的话兜底会点屏幕中央，正好点进抽卡页（实训 2026-10-26）。
        if self.match_text(texts, "立即前往") is not None:
            self.click(int(self.width * 0.89), int(self.height * 0.17), after_sleep=0.6)
            self.log_info("关闭活动横幅")
            return True
        return False

    def is_announcement(self, texts):
        """整页公告（开服/更新公告）：标题「公告」+ 左侧「置顶/最新/热门」列表。

        右上角 ✕ 没有文字、OCR 认不出，所以判据只能用列表标签 + 固定比例坐标。
        """
        return (self.exact_text(texts, labels.ANNOUNCEMENT_TITLE) is not None
                and self.match_text(texts, labels.ANNOUNCEMENT_TABS) is not None)

    def handle_announcement(self, texts):
        """关掉整页公告（2026-11-10 实机：游戏刚启动时这页盖住全部界面）。

        事故：新装/刚启动游戏时，任务进入「队长识别」→ 反复点右上「返回」（这页没有）
        → 连续 3 轮没变化 → 看门狗停任务。判据放在队长识别之前（见 daycycle）。
        """
        if not self.is_announcement(texts):
            return False
        x = int(self.width * labels.ANNOUNCEMENT_CLOSE[0])
        y = int(self.height * labels.ANNOUNCEMENT_CLOSE[1])
        self.click(x, y, after_sleep=0.8)
        self.log_info(f"公告页：点右上角关闭 @({x},{y})")
        return True

    def handle_draw(self, texts):
        """大厅「幸运大抽奖」转盘：点「转一转」抽一次，结果交给弹窗处理。"""
        if self.match_text(texts, ("幸运大抽奖", "普通技能", "强力技能")) is None:
            return False
        target = self.match_text(texts, "转一转")
        if target is not None:
            self.click_box(target, after_sleep=1.2)
            self.log_info("抽奖：转一转")
        else:
            self.click(int(self.width * 0.5), int(self.height * 0.8), after_sleep=1.0)
            self.log_info("抽奖：点结果继续")
        return True
