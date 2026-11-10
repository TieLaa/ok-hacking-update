"""常驻「快速比赛」：任何模式的比赛都能跳过 / 加速 / 收尾。

为什么要单独做一个 TriggerTask：

1. 所有比赛都走同一套界面：赛前准备（跳过比赛 / 前往比赛）→ 开场 ▶▶ →
   跳球 → 比赛内 HUD（自动/手动 + 快进）→ 结算「下一步」。这套流程跟养成模式无关，
   赛事、大厅 VS、剧情比赛都会遇到。
2. `TaskExecutor.next_task()` 里 **onetime 任务优先**，只有没有 onetime 任务在跑时
   才会轮到 TriggerTask。所以养成任务运行时它自动让位，不会抢点击。
3. 默认关闭（TriggerTask 的 `_enabled` 默认 False）：用户在其它模式手动打比赛时，
   不想被脚本接管；需要在任务页手动启用。

用法：
    在任务页启用「快速比赛」，之后任何比赛都会自动跳过 / 进比赛点 ▶▶ / 收结算。
"""

from ok import TriggerTask

import re

from src.tasks.BaseGirlsTask import BaseGirlsTask
from src.tasks.training.handlers.match import MatchHandlers
from src.tasks.training.parsers import TrainingParsersMixin
from src.tasks.training.scoring import TrainingScoringMixin
from src.tasks.training.systemui import SystemUIHandlers

# 常驻任务没有看门狗：判据误命中时会每 0.5 秒点同一处、永远不停
# （2026-10-27 实机：全中「对手实力」点了 30 秒）。所以这里自己数：
# 同一个 handler 在**同一画面指纹**上连续命中 N 拍，就存证 + 停手，
# 过一会儿再试一次（避免页面真卡住时永久放弃）。
REPEAT_LIMIT = 3
REPEAT_RETRY_TICKS = 20

# 比赛内 HUD / 开场 ▶▶ 本来就该连续点（画面对它来说是"同一页"），不参与限流
REPEAT_EXEMPT = ("handle_in_match", "handle_match_intro")

# 画面指纹里要抹掉的「会自己变、但不代表换了页面」的内容：数字。
# 比赛画面的倒计时（05:47→05:44）和比分（8:1→10:1）每拍都在变，
# 直接拿整帧文字做指纹的话同一页永远算「新画面」，限流一次都不会触发。
DIGITS_RE = re.compile(r"[0-9０-９]+")


class QuickMatchTask(MatchHandlers, TrainingParsersMixin, TrainingScoringMixin,
                     SystemUIHandlers, BaseGirlsTask, TriggerTask):

    # 顺序即优先级：先认比赛类界面，赛前准备放最后（它的「跳过/前往」是唯一的入口）
    HANDLERS = (
        MatchHandlers.handle_match_intro,
        MatchHandlers.handle_in_match,
        MatchHandlers.handle_match_result,
        MatchHandlers.handle_skill_select,
        MatchHandlers.handle_event_choice,
        MatchHandlers.handle_prepare,
        MatchHandlers.handle_tap_to_continue,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "快速比赛"
        self.description = "常驻监听比赛：赛前准备自动跳过/进比赛，开场点 ▶▶，结算点下一步（其它模式也能用）"
        self.trigger_interval = 0.5
        self.default_config.update({
            "比赛日处理": "自动（先跳过，跳不了就进比赛加速）",
            "比赛内自动选技能和事件": True,
            "技能标签优先": ["得分手"],
            "空闲等待(秒)": 3,
        })
        self.config_type.update({
            "比赛日处理": {
                "type": "drop_down",
                "options": ["自动（先跳过，跳不了就进比赛加速）",
                            "总是跳过", "总是进比赛（外部战斗脚本）"],
            },
        })
        self.config_description.update({
            "比赛日处理": "自动 = 先试跳过，跳不了就进比赛点 ▶▶；总是跳过 = 跳不了也不进",
            "比赛内自动选技能和事件": "比赛内出现「选择技能 / 事件二选一」时自动点；自动模式下游戏自己会处理，可关闭",
            "空闲等待(秒)": "你动鼠标或敲键盘时脚本立刻停手，等你停手这么多秒后才重新拉起游戏继续挂机（期间绝不抢前台）。0 = 不等（用户操作时也照抢）",
            "技能标签优先": "同品质时优先的技能标签",
        })
        self.init_match_state()
        self._steps = 0                 # dump_unknown 的命名要这个
        self._repeat_sig = None
        self._repeat_count = 0
        self._mute_sig = None           # 正在停手的画面（OCR 文本集合）
        self._mute_ticks = 0
        # 是否「在比赛中」：技能三选一 / 事件二选一这两类 handler 形态太通用
        # （大厅「强化」页三张升级卡也能凑成横向多选一），必须先进了比赛才允许动。
        self._in_match = False

    @staticmethod
    def _screen_sig(texts):
        """画面指纹：**把数字统一成 `#`** 后再比对。

        2026-11-03 实机（限时比赛活动）：比赛画面带 `05:47 → 05:44` 倒计时和
        `8:1 → 10:1` 比分，旧指纹 `frozenset(全部文本)` 每拍都不同，
        于是 `_note_hit` 的计数一直从 1 开始 —— `handle_event_choice` 在同一页
        连点 20 次，`REPEAT_LIMIT` 一次都没触发（10-27「对手实力」事故重演）。

        为什么替换成 `#` 而不是删掉：保留「这行是数字/时间」的结构，
        `05:47 → #:#`、`115/115 → #/#`、`2× → #×` 都还能区分形态；
        整条都是数字的条目不会因为被删空而互相吞并。
        """
        sig = set()
        for t in texts:
            s = DIGITS_RE.sub("#", t.name.strip())
            if s:
                sig.add(s)
        return frozenset(sig)

    def _is_muted(self, sig):
        """停手期判断：同一画面已经连点 N 拍没变化就先不动，过期后自动重试一次。"""
        if self._mute_sig is None or sig != self._mute_sig:
            return False
        self._mute_ticks += 1
        if self._mute_ticks < REPEAT_RETRY_TICKS:
            return True
        self._mute_sig = None
        self._repeat_sig, self._repeat_count = None, 0
        return False

    def _note_hit(self, texts, sig, handler_name):
        """命中并点击后记账：同一 handler 在同一画面上连点 REPEAT_LIMIT 拍就存证停手。"""
        if handler_name in REPEAT_EXEMPT:
            return
        if sig == self._repeat_sig:
            self._repeat_count += 1
        else:
            self._repeat_sig, self._repeat_count = sig, 1
        if self._repeat_count >= REPEAT_LIMIT:
            self.log_warning(
                f"{handler_name} 连续 {self._repeat_count} 拍同一画面仍在点，"
                f"存证后停手 {REPEAT_RETRY_TICKS} 拍")
            self.dump_unknown(texts, "repeat")
            self._mute_sig, self._mute_ticks = sig, 0

    # ---------- MCP 契约 ----------
    # 常驻任务**不能**回大厅：比赛打到一半把界面点回大厅等于毁掉这一局。
    # 所以 reset 只确认画面可用，verify 只断言当前界面是「认识的」场景之一。

    def reset_to_start(self):
        self.begin_attempt()
        frame = self.next_frame()
        return frame is not None

    def verify_start_state(self):
        texts = self._ocr_region()
        assert texts, "起始校验失败：这一帧没有识别到任何文字（窗口被遮挡？）"
        joined = "".join(t.name for t in texts)
        known = (self.is_lobby(texts)
                 or self._has_match_hud(texts)
                 or any(k in joined for k in ("训练", "比赛", "商店", "公园",
                                              "出行", "跳过", "下一步", "失败率")))
        assert known, ("起始校验失败：画面既不是大厅也不是比赛/训练界面"
                       f"（识别到：{'/'.join(t.name for t in texts[:10])}）")
        return True

    def run(self):
        self.begin_attempt()
        self.apply_user_idle_wait(self.config.get("空闲等待(秒)", 3))
        texts = self._ocr_region()
        if not texts or self.is_loading(texts):
            return False
        self._steps += 1

        if self.is_lobby_subpage(texts):        # 大厅子菜单：不操作
            return False

        sig = self._screen_sig(texts)
        if self._is_muted(sig):
            return False

        auto_pick = self.config.get("比赛内自动选技能和事件", True)
        in_match = self._in_match or self._has_match_hud(texts)
        for handler in self.HANDLERS:
            if (not auto_pick
                    and handler in (MatchHandlers.handle_skill_select,
                                    MatchHandlers.handle_event_choice)):
                continue
            # 三选一 / 二选一只有在比赛里才处理：
            # 2026-10-28 用户反馈「只开了快速比赛，为什么在大厅强化页被点升级卡」——
            # 强化页三张卡的黄色价格条正好中了 handle_event_choice 的「横向多选一」。
            if (handler in (MatchHandlers.handle_skill_select,
                            MatchHandlers.handle_event_choice)
                    and not in_match):
                continue
            if handler(self, texts):
                self._last_state = handler.__name__
                self._note_hit(texts, sig, handler.__name__)
                self.log_debug(f"命中 {handler.__name__}")
                if handler in (MatchHandlers.handle_match_intro,
                               MatchHandlers.handle_in_match):
                    self._in_match = True
                elif handler is MatchHandlers.handle_match_result:
                    self._in_match = False
                return True
        return False

    @staticmethod
    def _has_match_hud(texts):
        """画面上有没有比赛 HUD（比分/节次/加速），用来判断「现在在比赛里」。"""
        joined = "".join(t.name for t in texts)
        return any(k in joined for k in (
            "比分", "第1节", "第2节", "第3节", "第4节", "暂停", "跳过比赛", "VS"))
