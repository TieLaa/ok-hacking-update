"""全国大赛（大厅 → 赛事 → 玩法目录 → 全国大赛 → 前往挑战）。

2026-11-22 第三版：连续打多局（试跑上限 `MAX_ROUNDS`）。
每局结束回到关卡列表就开下一局；任务本身不判断「今天还能打几次」，
入场券用完时「前往挑战」点不动，连续 3 次没进比赛就停。

比赛页全部复用现成 `MatchHandlers`：
    - 赛前准备：先点一次「跳过比赛」，下一拍还在这一页就改点「前往比赛」
      （用户口径：只有打赢过的关能跳过，每过几关有一关不能跳过）；
    - 进比赛后：`handle_in_match` 自动切自动 + 点 ▶▶ 快进；
    - 结算：`handle_match_result` 点「下一步」。

关卡列表上的「通关奖励 / 点击任意区域关闭」弹层不用单独处理：
直接点「前往挑战」的位置，第一下关弹层，第二下进赛前准备（用户 2026-11-22 口径）。

页面判据统一在 `training/pages.py`：
    lobby → national_menu（玩法目录）→ national_stage_list（关卡列表）
"""

import time

from src.tasks.BaseGirlsTask import BaseGirlsTask
from src.tasks.training.handlers.entry import EntryHandlers
from src.tasks.training.handlers.match import MatchHandlers
from src.tasks.training.pages import classify_page
from src.tasks.training.parsers import TrainingParsersMixin
from src.tasks.training.scoring import TrainingScoringMixin
from src.tasks.training.systemui import SystemUIHandlers

MAX_ROUNDS = 200        # 上限保护；正常情况下券用完会先停（前往挑战点不动）
MAX_STEPS = 400         # 单局上限（真打的关要快进上百拍）


class NationalCupTask(MatchHandlers, EntryHandlers, TrainingParsersMixin,
                      TrainingScoringMixin, SystemUIHandlers, BaseGirlsTask):

    # 顺序与 QuickMatchTask 一致：比赛内 / 结算在前，赛前准备在后
    HANDLERS = (
        MatchHandlers.handle_match_intro,
        MatchHandlers.handle_in_match,
        MatchHandlers.handle_match_star_result,
        MatchHandlers.handle_match_retry,
        MatchHandlers.handle_match_start,
        MatchHandlers.handle_match_result,
        MatchHandlers.handle_skill_select,
        MatchHandlers.handle_event_choice,
        MatchHandlers.handle_prepare,
        MatchHandlers.handle_tap_to_continue,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "全国大赛"
        self.description = "连续挑战全国大赛：能跳过就跳过，跳不了进比赛快进，挑战券用完自动停"
        self.init_match_state()
        self._pages = []
        self._started = False
        self._left_stage_list = False
        self._challenge_clicks = 0
        self._in_match = False

    # ---------- MCP 契约 ----------

    def reset_to_start(self):
        """MCP 基线：先回大厅，从可复现的位置开始。"""
        self.begin_attempt()
        return self.recover_to_lobby()

    def verify_start_state(self):
        texts = self._ocr_region()
        assert texts, "起始校验失败：这一帧没有识别到任何文字（窗口被遮挡？）"
        page, _ = classify_page(self, texts)
        assert page in ("lobby", "national_menu", "national_stage_list"), (
            f"起始校验失败：要先回大厅（当前识别为 {page}）")
        return True

    def tick(self, texts):
        """走一步：认页面 → 点入口 / 交给比赛 handler。返回 (页面, 是否动作)。"""
        page, _ = classify_page(self, texts)
        self._pages.append(page)
        if page == "lobby":
            target = self.exact_text(texts, ("赛事",))
            if target is not None:
                self.click_box(target, after_sleep=1.2)
                self.log_info("大厅 → 赛事")
                return page, True
        elif page == "national_menu":
            target = self.exact_text(texts, ("全国大赛",))
            if target is not None:
                self.click_box(target, after_sleep=1.2)
                self.log_info("玩法目录 → 全国大赛")
                return page, True
        elif page == "national_stage_list":
            if self._started and self._left_stage_list:
                return page, False          # 一局打完回到这里：_play_round 负责收尾
            if self._challenge_clicks >= 5:
                return page, False
            # 弹层（通关奖励 / 点击任意区域关闭）会吃掉这一下，不影响：
            # 同一位置再点一次就进赛前准备（用户 2026-11-22 口径）。
            target = self.exact_text(texts, ("前往挑战",))
            if target is not None:
                self.click_box(target, after_sleep=1.0)
                self._started = True
                self._challenge_clicks += 1
                # 每局重新给「跳过比赛」一次机会：handle_prepare 靠 _skip_tries
                # 记「本页已经点过跳过」，不重置的话第二局起会直接进比赛
                # （2026-11-22 实跑：能跳过的关被当成了不能跳过）。
                self._skip_tries = 0
                self.log_info(f"全国大赛：前往挑战（第 {self._challenge_clicks} 次）")
                return page, True
            return page, False
        for handler in self.HANDLERS:
            if handler(self, texts):
                if handler in (MatchHandlers.handle_match_intro,
                               MatchHandlers.handle_in_match):
                    self._in_match = True
                elif handler is MatchHandlers.handle_match_result:
                    self._in_match = False
                self.log_debug(f"命中 {handler.__name__}")
                return page, True
        self.log_info(f"当前页面：{page}（没有可走的入口）")
        return page, False

    def play_round(self, index):
        """打一局：从当前页面走到开打，再回到关卡列表。"""
        self._started = False
        self._left_stage_list = False
        self._challenge_clicks = 0
        self._in_match = False
        last = None
        for _ in range(MAX_STEPS):
            texts = self._ocr_region()
            if not texts or self.is_loading(texts):
                self.sleep(0.6)
                continue
            clicks_before = self._challenge_clicks
            if self.match_text(texts, "全国大赛入场券数量不足") is not None:
                self.log_info("全国大赛：挑战券已用完（入场券数量不足），停止")
                return {"round": index + 1, "ok": False, "reason": "挑战券已用完"}
            last, acted = self.tick(texts)
            # 只有**开打之后**真正进过赛前准备/比赛/结算，才算「这一局真的开始了」。
            # 不能用「任何非关卡列表页面」：通关奖励弹层（点击屏幕继续）也会
            # 让页面变 None，回到关卡列表就被误判成整局结束
            # （2026-11-22 实跑：第 2 局一局没打就报 ok）。
            if self._started and (self._in_match or last in ("prepare", "match_result")):
                self._left_stage_list = True
            if self._started and self._left_stage_list and last == "national_stage_list":
                self.log_info(f"第 {index + 1} 局结束，已回到关卡列表")
                return {"round": index + 1, "ok": True}
            # 注意 clicks_before：点第 N 次的那一拍不判失败，要等下一拍确认有没有进比赛。
            # 通关奖励是两层弹层（「点击任意区域关闭」→「点击屏幕继续」），
            # 前两下都在关弹层，第三下才点到「前往挑战」（2026-11-22 实跑）。
            if (last == "national_stage_list" and clicks_before >= 5
                    and not self._left_stage_list):
                self.log_warning(f"第 {index + 1} 局：前往挑战点了 5 次都没进比赛（没券？）")
                return {"round": index + 1, "ok": False,
                        "reason": "前往挑战点了 5 次都没进比赛"}
            if not acted:
                self.sleep(0.8)
        self.log_warning(f"第 {index + 1} 局：{MAX_STEPS} 拍内没打完")
        return {"round": index + 1, "ok": False, "reason": f"{MAX_STEPS} 拍内没打完",
                "page": last}

    def run(self):
        self.begin_attempt()
        self._pages = []
        started_at = time.monotonic()
        rounds = []
        for index in range(MAX_ROUNDS):
            result = self.play_round(index)
            rounds.append(result)
            if not result["ok"]:
                break
        result = {"rounds": rounds,
                  "done": sum(1 for r in rounds if r["ok"]),
                  "elapsed_s": round(time.monotonic() - started_at, 1),
                  "pages": self._pages[-60:]}
        self.log_info(f"全国大赛试跑结果：{result}")
        return result
