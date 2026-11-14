"""比赛全流程：赛前准备、开场 ▶▶、比赛内快进、赛程结算、技能三选一、事件选项、倍速。

这些 handler 同时被 `TrainingTask`（养成）和 `QuickMatchTask`（常驻快速比赛）复用，
所以不要在这里假设「当前一定在养成流程里」。
"""

import time

import os

import cv2
import numpy as np

from src.tasks.training import page_signatures
from src.tasks.ui import Defs
from src.tasks.ui import labels

class MatchHandlers:

    # --- 左下角 ▶▶ 快进按钮的像素判据（见 Defs.MATCH_FF_ROI）---
    # 标定：历史 4895 帧（docs/shots + logs/unknown）只有 18 帧过，
    # 全是「准备跳球 / 对位 / 比赛内二选一」这些比赛画面，无一误判（2026-10-31）。
    # 药丸=深藏青实心块（灰度<80），双三角=白；信箱黑边/整块黑会被
    # 「贴边」「面积上限」「横长条」三条挡掉。
    FF_DARK = 80             # 药丸底色上限（灰度）
    FF_BRIGHT = 150          # 双三角白色下限（灰度）
    FF_MIN_DARK = 0.20       # 药丸面积 / ROI 面积
    FF_MAX_DARK = 0.75       # 整块黑（信箱黑边）会到 0.9 以上
    FF_ASPECT = (2.0, 6.0)   # 药丸是横长条
    FF_BRIGHT_RANGE = (0.03, 0.45)   # 双三角在药丸内的占比（实拍 0.089~0.136）

    def match_ff_visible(self, frame=None):
        """左下角 ▶▶ 快进按钮在不在（像素判定，不依赖 OCR）。

        为什么必须看像素：比赛开场（跳球前）整屏只有球场/「准备跳球」，
        OCR 一个可点控件都读不出来（实拍 logs/unknown/1026-155558：只有
        「坚行」「一心一意」两条噪声），而按钮本身是无文字图标
        （偶尔读成 "I"，位置也不稳）。所以不能用文字判「在比赛里」。

        2026-10-31 事故：`_match_intro_at` 只在「前往比赛」分支置位，
        但实战里点完「跳过比赛」游戏会直接进场 → 时间戳没置上 →
        整段开场一次没点，等跳球后 HUD 出来才开始点快进（用户现场复现）。
        """
        frame = frame if frame is not None else getattr(self, "_tick_frame", None)
        if frame is None:
            frame = getattr(self, "frame", None)
        if frame is None or not hasattr(frame, "shape"):
            return False
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = Defs.MATCH_FF_ROI
        roi = frame[int(height * y1):int(height * y2), int(width * x1):int(width * x2)]
        if roi.size == 0:
            return False
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        rh, rw = gray.shape[:2]
        dark = (gray < self.FF_DARK).astype(np.uint8)
        if float(dark.mean()) < self.FF_MIN_DARK:
            return False
        _, _, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
        lo, hi = self.FF_ASPECT
        best = None
        for i in range(1, len(stats)):
            bx, by, bw, bh, area = (int(v) for v in stats[i])
            if not (self.FF_MIN_DARK * dark.size <= area <= self.FF_MAX_DARK * dark.size):
                continue
            if not (lo <= bw / max(bh, 1) <= hi):
                continue
            # 贴边 = 整块黑边/背景，不是按钮
            if bx <= 1 or by <= 1 or bx + bw >= rw - 1 or by + bh >= rh - 1:
                continue
            if best is None or area > best[4]:
                best = (bx, by, bw, bh, area)
        if best is None:
            return False
        bx, by, bw, bh = best[:4]
        pill = gray[by:by + bh, bx:bx + bw]
        bright = float((pill > self.FF_BRIGHT).mean())
        bright_lo, bright_hi = self.FF_BRIGHT_RANGE
        return bright_lo <= bright <= bright_hi

    def handle_match_intro(self, texts):
        """NBA 转场：**跳过比赛 / 赛后结算之后**一闪而过的 NBA logo，没有可点的控件。

        2026-10-26 复盘：这个页面从不出现在比赛开场，只在「跳过比赛」成功和结算
        返回之后出现，约 1 秒自己走完。点左下角（比赛内快进的位置）不会有任何反应，
        所以这里只等，不点。
        2026-10-31：左下角要是有 ▶▶（VS/开场页 OCR 有时会读成 VS），
        让给 handle_in_match 去点，别在这里干等 0.4 秒。
        """
        if self.match_ff_visible():
            return False
        names = {t.name.strip() for t in texts}
        if not (names & {"NBA", "VS"}):
            return False
        # 纯转场页：文字很少，避免在比赛结算/数据页误判
        if len([t for t in texts if len(t.name.strip()) >= 2]) > 6:
            return False
        self._no_stall_until = time.monotonic() + 3
        self.sleep(0.4)
        return True

    def handle_in_match(self, texts):
        """比赛内：先把手动切成自动，再连点「快进」。

        开场阶段（跳球前）左下角**只有一个 ▶▶ 快进按钮**，没有「自动/手动」，
        旧代码因为找不到这两个词直接 return False，整段开场一次都不点
        （用户 2026-10-26 现场截图确认）。现在用两条判据认这个窗口：
        ① `_match_intro_at`（还留着：给「前往比赛 / 总是进比赛」分支用）；
        ② `match_ff_visible()` 直接看左下角有没有 ▶▶ —— 比赛进场路径太多
        （跳过比赛后游戏自己进场、用户手动点进场、外部战斗脚本接管），
        只靠时间戳会漏（2026-10-31 用户现场复现：整段开场没点）。
        """
        hud = self.texts_in_region(texts, "match_hud")
        intro = bool(self._match_intro_at) and time.monotonic() - self._match_intro_at < 90
        ff = self.match_ff_visible()
        if not hud and not intro and not ff:
            return False
        # 赛前准备页还没点掉：交给 handle_prepare 继续点「跳过/前往比赛」
        if self.match_text(texts, ("跳过比赛", "前往比赛")) is not None:
            return False

        auto = self.match_text(hud, "自动")
        manual = self.match_text(hud, "手动")
        if auto is not None or manual is not None:
            self._match_intro_at = 0.0          # 跳球完成，开场窗口关闭
        if manual is not None:
            self.click_box(manual, after_sleep=0.35)
            self.log_info("比赛：手动 → 自动")
            return True
        if auto is None and not intro and not ff:
            return False

        self.click_match_fast_forward(after_sleep=0.6)
        if auto is None:
            self.log_debug("比赛开场：点左下角 ▶▶ 快进")
        return True

    def handle_match_result(self, texts):
        """比赛结算 / 赛程页 / 目标达成：一路点「下一步」或屏幕。

        判定必须**整词**：「失败率 0%」里也有「失败」，科目页会被误判成结算页。

        一局的终点（用户 2026-10-26）：最终结算页（A 级评价大字那张）点「下一步」
        会进入**注册球员编队** —— 那是这一局的终点，任务停在这里。
        判据：只有评价等级大字（A/S/B 单独成框）+ 没有顶栏周数，才点「下一步」；
        点完看到「选择器材/确认编队」等编队页文字后，run() 直接 return 结束。

        限时比赛活动（2026-11-03 用户截图，活动赛赛程页）：页面上是
        「第1轮进行中 / 已晋级A组 / 胜利·失败 / 暂时退出 / 继续匹配」，
        **没有「下一步」**。但它有单独的「胜利」「失败」大字，会被下面的
        RESULT_TITLE_RE 认成结算页，然后掉进「点屏幕中央」的兜底 —— 点不动。
        所以「继续匹配」必须在这条兜底之前认领，且优先级最高。
        """
        # 活动赛：只有一个可靠的推进按钮。放在最前，别让任何其它判据抢先。
        resume = self.exact_text(texts, labels.BUTTONS["continue_match"])
        if resume is not None:
            self.click_box(resume, after_sleep=1.2)
            self.log_info("活动赛：继续匹配")
            self._match_intro_at = 0.0          # 上一局的开场窗口作废
            return True
        # 排除「关卡列表页」（关卡加成 + 奖励预览）。
        # 2026-11-14 改名：签名表里这一条原来叫 `match_result`，
        # 和 `pages.py` 的 `match_result`（比赛结算页）同名不同义 ——
        # 同名两套答案，实测有 331 帧判定不一致。现在叫 `stage_list`。
        if page_signatures.page_is_other(self, texts, "stage_list"):
            return False
        # 锦标赛对阵图：只有「赛程已完成 / 距离下场比赛 / 下一场」和学校名，
        # 没有「结算/赛程」这种整词标题，旧判据认不出来会被事件 handler 当成竖排选项。
        if self.match_text(texts, ("赛程已完成", "距离下场比赛", "挑战中")) is not None:
            nxt = self.match_text(texts, "下一步")
            if nxt is None:
                nxt = self.match_text(texts, "下一场")
            if nxt is not None:
                self.click_box(nxt, after_sleep=0.6)
                self.log_info("赛程：下一步")
            return True
        nxt_match = self.match_text(texts, "下一场")
        if nxt_match is not None:
            self.click_box(nxt_match, after_sleep=0.6)
            self.log_info("赛程：下一场")
            return True
        if self.match_text(texts, (labels.RESULT_TITLE_RE,)) is None:
            return False
        nxt = self.match_text(texts, "下一步")
        if nxt is not None:
            self.click_box(nxt, after_sleep=0.5)
            self.log_info("结算：下一步")
            return True
        if self.match_text(texts, labels.TAP_TO_CONTINUE) is not None:
            self.click(int(self.width_of_screen(0.5)), int(self.height_of_screen(0.9)), after_sleep=0.5)
            self.log_info("结算：点击继续")
            return True
        self.click(int(self.width_of_screen(0.5)), int(self.height_of_screen(0.5)), after_sleep=0.45)
        return True


    def init_match_state(self):
        """比赛相关的共享状态：养成任务和「快速比赛」模块都要初始化（两个任务共用）。"""
        self._skip_tries = 0
        self._match_intro_at = 0.0
        # 倍速：week 变了就重来；stalls 是看门狗（点击没让状态变化几次就锁本周）
        self._speed_week = None
        self._speed_clicks = 0
        self._speed_stalls = 0
        self._speed_sig = None
        self._speed_locked = False

    def _card_choices(self, texts):
        """底排横向大卡：同一行 2~3 块短标题 + 每块自己下方一行效果文字。

        返回 [(标题框, [效果框...]), ...]；不只是「按摩」，温泉/强化训练等
        三选一大卡都是这个形态。
        """
        h = self.height
        w = self.width
        rows = {}
        for t in texts:
            s = t.name.strip()
            cy = (t.y + t.height / 2) / h
            if not (0.62 <= cy <= 0.88):
                continue
            if not (2 <= len(s) <= 8) or any(ch.isdigit() for ch in s):
                continue
            if self.is_label(s) or self.is_decor(s):
                continue
            key = round(cy, 1)
            rows.setdefault(key, []).append(t)
        best = []
        for key, group in rows.items():
            if not (2 <= len(group) <= 3):
                continue
            items = []
            for t in group:
                cx = (t.x + t.width / 2) / w
                effs = [o for o in texts
                        if o is not t
                        and abs((o.x + o.width / 2) / w - cx) < 0.12
                        and (o.y + o.height / 2) / h > key
                        and (o.y + o.height / 2) / h < key + 0.16
                        and any(k in o.name for k in ("属性", "精力", "概率", "%"))
                        and o.name.strip() != t.name.strip()]
                if effs:
                    items.append((t, effs))
            if len(items) >= 2 and len(items) > len(best):
                best = items
        return best

    def handle_prepare(self, texts):
        """赛前准备页：跳过比赛 / 前往比赛。

        规则（用户实测）：只有**打赢过**的比赛才能跳过；没打赢的必须进比赛，
        而且只能在比赛里点加速。所以这里做两级降级：
        先点「跳过比赛」，下一拍如果还在这一页，就改点「前往比赛」。
        """
        skip = self.match_text(texts, "跳过比赛")
        go = self.match_text(texts, "前往比赛")
        if skip is None and go is None:
            return False

        if self.ensure_speed(texts):
            return True

        mode = self.config.get("比赛日处理") or ""
        if mode.startswith("总是进比赛") and go is not None:
            self._match_intro_at = time.monotonic()
            self.click_box(go, after_sleep=0.6)
            self.log_info("进比赛（等外部战斗脚本接管）")
            return True

        if skip is not None and self._skip_tries == 0:
            self._skip_tries = 1
            self.click_box(skip, after_sleep=0.5)
            self.log_info("尝试跳过比赛")
            return True

        if go is not None:
            self._skip_tries = 0
            # 从这一刻进入比赛开场：只有左下角 ▶▶，由 handle_in_match 点
            self._match_intro_at = time.monotonic()
            self.click_box(go, after_sleep=0.6)
            self.log_info("跳过不可用，进比赛（开场点 ▶▶ 加速）")
            return True

        self.log_warning("赛前准备页：跳过和前往都没找到")
        return False

    def handle_training_score(self, texts):
        """一局结束的「训练评分」页：读**个人评分** → 记录 → 达标就收工。

        为什么必须加（2026-11-04 实跑，`logs/mcp/a0e2b1ea…/run.log` 20:53:21）：
        这版游戏一局结算页写的是

            训练评分  7728  SS  点击屏幕继续

        而旧代码只认「恭喜获得 / 训练奖励」标题（`SQUAD_AWARD_MARKS`），
        这一页读不到 → **评分一次都没记**（队列里永远停在上一次的 7096）→
        一局结束后没人收账 → 大厅又开新局，表现就是「明明 7700 分了还在跑」。

        用户的判定口径（`docs/RULES.md §12`）就是「这个球员的评分 ≥ 目标」，
        所以这里读到分就立刻记进 `_run_score`，达标直接把本局标记为结束
        （`_round_done`），让 `advance_queue_after_round` 去收账并提前终止。
        """
        score, rating = self.read_training_score(texts)
        if score is None:
            return False
        if time.time() - getattr(self, "_score_click_at", 0.0) < 1.0:
            return True                          # 刚点过，等翻页
        if not getattr(self, "_round_score_seen", False):
            self._round_score_seen = True
            queue = getattr(self, "_queue", None)
            target = queue.target if queue is not None else None
            self.log_info(f"本局训练评分：{score}（评级 {rating or '?'}）/ 目标 {target}"
                          "（仅参考，不入账；判分以注册球员页为准）",
                          notify=True)
            # 2026-11-06 口径修正：**这一页的分数不再写进 `_run_score`**。
            # 用户口径的判分点是「恭喜获得/训练奖励」页上注册球员的球员评分
            # （含累积加成，本局实测同局两个数：弹层 8026 vs 注册球员 8380）。
            # 旧代码先到先得，弹层把注册球员页的分数短路掉，导致第 5 局
            # 8380 已达标却按 7420 继续白跑了 9 局。这里只留日志和参考。
        self._score_click_at = time.time()
        # 推进这一页：优先点「点击屏幕继续」的文字框，认不出点屏幕中央（这页点哪都走）
        target_box = self.match_text(texts, labels.TAP_TO_CONTINUE_TEXTS)
        if target_box is not None:
            self.click_box(target_box, after_sleep=0.6)
        else:
            self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=0.6)
        return True

    def handle_register_player(self, texts):
        """一局打完的**注册球员**收尾流程（用户 2026-10-27 提供 6 屏截图）。

        这是「训练赛 → 注册球员 → 编队 → 保存 → 回大厅」的固定六步，走完才算一局结束：

        1. 恭喜获得 / 注册球员页：点「下一步」（左下还有「记录笔记」，不要点）
        2. 「训练结束」弹窗：点「编队」（另一个「前往主界面」会跳过编队，不点）
        3. 战术板页：点「自动编队」（一行阵容自动排好）
        4. 同一页出现「评分上升」后：点「保存调整」（保存前是灰的，保存后变黄）
        5. 「更新最高队伍等级」弹窗：点「关闭」
        6. 战术板页：点左上角「«」（战术板标题左边的双箭头）回大厅

        **必须靠 `_register_stage` 串起来**：战术板页玩家自己也能进（阵容菜单），
        没有进入过第 1 步就绝不能在这里点「自动编队」，否则会改掉玩家的阵容。
        同理「关闭」不能交给 handle_popup —— 它会在任何页面上点第一个「关闭」。
        """
        stage = getattr(self, "_register_stage", None)
        # 活动积分结算页（PT 活动：分数进池换奖品）是正常展示，不是「卡住」：
        # 这页停留十几拍，旧判据会误报 register_stuck（2026-11-12 17:28 实跑 21 拍）。
        # 只清计数、不认领页面 —— 这页仍交给 popup / 兜底去点，别在这里抢。
        if self.match_text(texts, "获得活动积分") is not None:
            self._reg_tick_stage, self._reg_ticks = None, 0
            return False
        # 拍数上限：连续 20 拍停在同一 stage 上（页面没推进）就存证 + 放弃，
        # 绝不允许无限重试（2026-10-28 18:17 空烧 890 拍）。
        # 2026-10-29 修正：stage 为 None = **还没进注册流程**（普通训练/剧情页），
        # 这时也累加会让 21 拍就报一次 ERROR + 存一张废图（一轮 25 张）。
        if stage is None:
            self._reg_tick_stage, self._reg_ticks = None, 0
        else:
            ticks = getattr(self, "_reg_ticks", 0) + 1
            if getattr(self, "_reg_tick_stage", None) == stage:
                self._reg_ticks = ticks
            else:
                self._reg_tick_stage, self._reg_ticks, ticks = stage, 1, 1
            if ticks > 20:
                self.dump_unknown(texts, "register_stuck")
                self.log_error(f"注册球员流程连续 {ticks} 拍没推进（stage={stage}）"
                               "→ 存证到本局 debug/ 后放弃")
                self._reg_ticks = 0
                return False

        # 2) 训练结束弹窗：**必须排在第 1 步前面**。
        # 这个弹窗正文是「成为注册球员后，可以编队加入队伍。」，里面有「注册球员」，
        # 而它背后还露着上一页的「下一步」按钮 —— 旧顺序会让第 1 步抢到，
        # 去点被弹窗盖住的「下一步」（2026-10-28 18:17 空烧 890 拍）。
        if self.exact_text(texts, ("训练结束",)) is not None:
            target = self.exact_text(texts, ("编队",))
            if target is not None:
                self.click_box(target, after_sleep=0.8)
                self._register_stage = "team"
                self._reg_step1_tries = 0
                self.log_info("注册球员 2/6：编队")
                return True
            # OCR 读不出「编队」时：这页固定左「前往主界面」右「编队」，
            # 拿左边按钮关于弹窗中线镜像过去
            left = self.exact_text(texts, ("前往主界面",))
            body = next((t for t in texts if "成为注册球员" in t.name), None)
            if left is not None and body is not None:
                mid = body.x + body.width / 2
                x = int(2 * mid - (left.x + left.width / 2))
                y = int(left.y + left.height / 2)
                self.click(x, y, after_sleep=0.8)
                self._register_stage = "team"
                self._reg_step1_tries = 0
                self.log_info(f"注册球员 2/6：编队（镜像左侧按钮 @{x},{y}）")
                return True
            btns = self._yellow_buttons()
            if len(btns) >= 2:
                bx, by, bw, bh = max(btns, key=lambda b: b[0])
                self.click(int(bx + bw / 2), int(by + bh / 2), after_sleep=0.8)
                self._register_stage = "team"
                self._reg_step1_tries = 0
                self.log_info("注册球员 2/6：编队（黄色按钮兜底）")
                return True

        # 1) 注册球员页（标题「注册球员」+ 黄色「下一步」）
        # **必须整词**：弹窗正文「成为注册球员后…」里的「注册球员」不算，
        # 否则会点在弹窗背后的「下一步」（2026-10-28 18:17 空烧 890 拍）。
        if self.exact_text(texts, ("注册球员", "球员登录", "签约", "入部申请")) is not None:
            # 多角色队列的达标判据就写在这一页之前（用户 2026-11-04）：
            # 「恭喜获得 / 训练奖励」页上按位置列了五个球员的四位数评分，
            # 这里读一次记进 `_run_score`，一局真正结束时由 `flow.finish_run` 判定。
            # 放在点「下一步」之前：点了就翻页，评分再也读不到了。
            self.note_run_score(texts)
            target = self.exact_text(texts, labels.BUTTONS["next"])
            if target is None:
                # OCR 偶尔把「下一步」读成「下一」（丢字），按前缀兜底
                target = next((t for t in texts
                               if t.name.strip().startswith("下一")
                               and len(t.name.strip()) <= 4), None)
            # 右下角那颗「下一步」是上一页（比赛结算）的残留：注册球员页的在 0.66w。
            # 2026-11-10 / 11-12 三局实测第一次都点 (1333,780) 不动，第二次重新 OCR
            # 读到 (1019,759) 才推进（s858/s840 存证）。先排除，让结算页的 handler 去点。
            if target is not None:
                cx = (target.x + target.width / 2) / self.width
                if cx > 0.80:
                    self.log_debug(f"注册球员：右下角「下一步」({int(cx * 100)}%w) "
                                   "是上一页残留，本拍不点")
                    target = None
            if target is not None:
                tries = getattr(self, "_reg_step1_tries", 0) + 1
                now = time.monotonic()
                if now - getattr(self, "_register_click_at", 0.0) < 2.0:
                    return True
                # 点不动就换位置再试：某些分辨率下「下一步」的点击热区比 OCR 文字框高/低
                # （2026-10-28 1549x871 实跑连点 60 秒都没推进）
                offs = (0.0, -0.015, 0.015, -0.03, 0.03)
                dy = offs[min(tries - 1, len(offs) - 1)] * self.height
                self._register_click_at = now
                self._reg_step1_tries = tries
                if tries > 6:
                    self.dump_unknown(texts, "register_step1_stuck")
                    self.log_error("注册球员第 1 步点了 6 次都没推进 → 存证到本局 debug/ 后放弃")
                    self._reg_step1_tries = 0
                    return False
                if tries == 2:
                    # 第一次没推进就存一帧，方便事后核对按钮真实位置
                    self.dump_unknown(texts, "register_step1_fail")
                if stage is None:
                    self.sleep(0.45)
                x = int(target.x + target.width / 2)
                y = int(target.y + target.height / 2 + dy)
                self.click(x, y, after_sleep=1.0)
                self._register_stage = "next"
                self.log_info(f"注册球员 1/6：下一步 @({x},{y})"
                              f"{'' if tries == 1 else f' 第{tries}次'}")
                return True

        if stage is None:
            return False

        # 2b) 编队详情页（右侧是「注册球员」名单，底部 重置/自动选择/保存编队）
        #     这一步在用户截图里没有出现，但实跑（2026-10-27 16:18）确实会进：
        #     「编队」→ 详情页 → 自动选择 → 保存编队 → 回到战术板。
        in_team_edit = (self.exact_text(texts, ("保存编队",)) is not None
                        and self.exact_text(texts, ("自动选择",)) is not None)
        if in_team_edit:
            if stage in ("team", "next"):
                target = self.exact_text(texts, ("自动选择",))
                self.click_box(target, after_sleep=0.8)
                self._register_stage = "team_auto"
                self.log_info("注册球员 2b：编队详情 → 自动选择")
                return True
            if stage == "team_auto":
                target = self.exact_text(texts, ("保存编队",))
                self.click_box(target, after_sleep=1.0)
                self._register_stage = "team_saved"
                self.log_info("注册球员 2b：保存编队")
                return True

        # 3) 战术板：自动编队
        if (self.match_text(texts, "战术板") is not None
                and self.exact_text(texts, ("自动编队",)) is not None
                and stage in ("next", "team", "team_saved")):
            target = self.exact_text(texts, ("自动编队",))
            self.click_box(target, after_sleep=0.9)
            self._register_stage = "auto"
            self.log_info("注册球员 3/6：自动编队")
            return True

        # 4) 同一页出现「评分上升」后：保存调整
        if (self.match_text(texts, "战术板") is not None
                and self.exact_text(texts, ("保存调整",)) is not None
                and stage == "auto"):
            target = self.exact_text(texts, ("保存调整",))
            self.click_box(target, after_sleep=0.8)
            self._register_stage = "save"
            self.log_info("注册球员 4/6：保存调整")
            return True

        # 5) 更新最高队伍等级弹窗：关闭
        if (self.match_text(texts, "更新最高队伍等级") is not None
                and self.exact_text(texts, labels.BUTTONS["close"]) is not None):
            target = self.exact_text(texts, labels.BUTTONS["close"])
            self.click_box(target, after_sleep=0.6)
            self._register_stage = "closed"
            self.log_info("注册球员 5/6：关闭升级弹窗")
            return True

        # 6) 返回大厅：左上角 « **没有文字**，用标题框当锚点向左推
        #    2026-10-27 16:18 事故：按截图比例写成 (0.0505w, 0.1075h)，
        #    但截图带系统标题栏（比游戏画面高约 29px），点在按钮下沿外面，
        #    连点 19 拍没反应、走到最大步数才停。改成「标题框左边 3% 宽、同一行高」。
        if stage in ("save", "closed", "back"):
            title = self.exact_text(texts, ("战术板", "首发"))
            if title is not None or in_team_edit:
                if title is not None:
                    x = int(title.x - self.width * 0.030)
                    y = int(title.y + title.height / 2)
                else:
                    x, y = int(self.width * 0.0505), int(self.height * 0.068)
                self.click(x, y, after_sleep=0.9)
                self._register_stage = "back"
                self.log_info(f"注册球员 6/6：返回大厅 @({x},{y})")
                return True

        return False

    def handle_tap_to_continue(self, texts):
        """「点击屏幕继续」类过场：MVP 结算、胜负动画 —— 点屏幕中央推进。

        2026-10-28 用户实测：比赛打赢后的 MVP 页只有一行「点击屏幕继续」，
        没有任何按钮，训练流程和快速比赛的 handler 都不认领，于是卡在那儿。

        排在专属 handler 之后（顺序见 handlers/__init__.py）：
        带「下一步 / 确定 / 跳过 / 放弃 / 继续」按钮的页面不抢，交给对应 handler。
        """
        hint = self.match_text(texts, labels.TAP_TO_CONTINUE)
        if hint is None:
            return False
        if self.exact_text(texts, ("下一步", "确定", "跳过", "继续", "放弃")) is not None:
            return False
        # **先点这行字自身**（用户 2026-10-28：MVP 页点屏幕正中没反应）。
        # 但训练日开场的「新目标」弹窗是整屏可点、点文字可能被底下的按钮挡住，
        # 所以同一画面第二拍还没变化时退回点屏幕中央。
        sig = frozenset(t.name.strip() for t in texts)
        if getattr(self, "_tap_sig", None) == sig:
            self._tap_count = getattr(self, "_tap_count", 1) + 1
        else:
            self._tap_sig, self._tap_count = sig, 1
        if self._tap_count <= 1:
            self.click_box(hint, after_sleep=0.5)
            self.log_info("点击屏幕继续（点文字 "
                          f"@{hint.x + hint.width // 2},{hint.y + hint.height // 2}）")
        else:
            self._tap_count = 0
            self.click(int(self.width * 0.5), int(self.height * 0.4), after_sleep=0.5)
            self.log_info("点击屏幕继续（文字没反应，改点屏幕中央）")
        return True

    def handle_result(self, texts):
        """比赛/训练结算页：确认、继续、领取、下一步。

        一局的终点（用户 2026-10-26）：最终结算点「下一步」会进入**注册球员编队**，
        任务到这里就算跑完，不要再往下点。

        2026-10-26 补充：地点活动（划船/野餐）成功页也是「成功！+ 属性 + 下一步」，
        顶栏还在、没有评价大字，handle_match_result 的 RESULT_TITLE_RE 认不出来，
        这里必须兜住「下一步」，否则会在结果页点中央空转到卡死。
        """
        # 必须是整词按钮：match_text 的子串匹配会把台词里的「继续」当成按钮
        # （2026-10-26 实测：点了「…请继续保持这份斗志…」这句台词）。
        target = self.exact_text(texts, ("确认", "继续", "领取", "下一步"))
        if not target:
            return False
        # 结算页的「确认」可能连点两次
        self.click_box(target, after_sleep=0.45)
        self.log_info(f"结算页「{target.name}」")
        return True

    def handle_skill_select(self, texts):
        """技能三选一：**按品质算分**（评分公式见 docs/score-model/README.md）。

        评分里技能价值：彩色 75 / 金色 50 / 白色 25（蓝、绿图标都算白）。
        排序是**显式档位**，不是加权和（2026-11-03）：
        品质 → 左上角道具图标 → 技能标签是否与配置优先项匹配。
        旧版 `gem*100 + quality*10 + has_pref` 里道具权重是品质的 3 倍多，
        判据一旦误判（左上角金橙像素占比 > 0.03 就算），白卡能压过彩卡 ——
        和扭蛋「恢复量 38 分压掉有队友的卡」是同一类错误：不同量纲不能加总分。
        """
        if self.match_text(texts, "请选择1个技能") is None:
            return False
        # 2026-10-31 用户核对「监控页 7240 vs 注册球员页 7376」：本局白技能记成 6 个
        # （注册球员页真值 2 个），金 4 个是对的。存证帧
        # logs/events/1031-162657 / _162658_handle_skill_select.jpg 对比可见：
        # 点完卡后游戏把选中的那张飞出、剩下两张左移变暗，但「请选择1个技能」还在 →
        # handler 又跑一拍，空槽位读成「道具0(0.000)」、变暗的卡全判白，
        # 于是白送一个「技能+1（白）」。一局 5 次三选一 = 多 5 个白（+125 分）。
        # 判据：名字行三张都在才算真·三选一；退场帧直接放行，不选也不计数。
        slots = self.skill_card_slots(texts)
        if len(slots) < 3:
            self.log_info(f"技能卡位只剩 {sorted(i + 1 for i in slots)} 有名字"
                          "（选中后的退场帧），本拍不选也不计数")
            return True
        if os.environ.get("OK_DUMP_CHOICES"):
            self.dump_unknown(texts, "choice_skill")

        pref = list(self.config.get("技能标签优先"))
        frame = self.frame
        icons = self.find_teammate_icons(0.0, 1.0) if frame is not None else []

        def cx_of(i):
            return Defs.SKILL_CARDS[i][0]

        def cy_of(i):
            return Defs.SKILL_CARDS[i][1]

        best_idx, best_key, best_quality = 0, (-1, -1, -1), 1
        names = self.skill_card_names(texts)     # 只用于日志/对账，不参与排序
        detail = []
        for i in range(3):
            card_texts = self._card_texts(texts, i)
            has_pref = 1 if self.match_text(card_texts, tuple(pref)) is not None else 0
            quality = self._card_quality(frame, i)      # 3 彩 / 2 金 / 1 白
            # 2026-10-30 用户口径：左上角带装备/道具图标的优先。
            # 但它只是「左上角金橙像素占比 > 0.03」的猜测，2026-10-29 就误判过一次
            # （压过两张真金边卡），所以**只能排在品质后面**，不进分数。
            gem = 1 if self.skill_card_item_gem(cx_of(i), cy_of(i)) else 0
            key = (quality, gem, has_pref)
            detail.append(f"{i + 1}:{labels.QUALITY_LABEL[quality]}"
                          f"「{names.get(i, '?')}」/标签{has_pref}"
                          f"/道具{gem}({getattr(self, '_gem_ratio', 0):.3f})")
            if key > best_key:
                best_idx, best_key, best_quality = i, key, quality
        self.log_info("技能评分 " + " ".join(detail)
                      + f" → 选第 {best_idx + 1} 张「{names.get(best_idx, '?')}」")
        self.dump_skill_frame(texts)

        cx, cy = Defs.SKILL_CARDS[best_idx]
        self.click(int(self.width_of_screen(cx)), int(self.height_of_screen(cy)), after_sleep=0.4)
        confirm = self.find_text(labels.BUTTONS["confirm"])
        if confirm is not None:
            self.click_box(confirm, after_sleep=0.5)
            self.log_info("技能：确定")

        # 本局新拿到的技能：按品质记进 info，监控页「分数预测」会实时累加。
        # info 在每次 enable 时清空（= 本局统计），配置里的「技能-彩色/金色/白色」
        # 是开局已有数量，两者相加就是当前总数。
        key = {3: "color", 2: "gold", 1: "white"}.get(best_quality, "white")
        gained = self.info.get("skill_gained")
        if not isinstance(gained, dict):
            gained = {"color": 0, "gold": 0, "white": 0}
        gained[key] = int(gained.get(key, 0)) + 1
        self.info["skill_gained"] = gained
        self.info["skill_last"] = labels.QUALITY_LABEL.get(best_quality, "白")
        self.log_info(
            f"技能+1（{self.info['skill_last']}"
            f"「{names.get(best_idx, '?')}」）本局累计："
            f"彩{gained['color']} 金{gained['gold']} 白{gained['white']}")
        return True

    def skill_card_item_gem(self, cx, cy):
        """卡片**左上角**是不是「装备/道具」图标（橙色卡包）。

        2026-10-30 用户口径：三选一优先拿左上角带装备图标的那张。
        实测帧 logs/events/1030-151056_handle_skill_select.jpg（1549x871）：
        - 第 1/2 张左上角是**书/纸**图标（浅黄白、低饱和）→ 金橙像素占比 0.0000；
        - 第 3 张左上角是**橙色卡包 + 绿色小人徽章** → 金橙像素占比 0.1608。
        另两帧（150859 / 151057）也是 0.0000 / 0.0000 / 0.1060。

        旧版判据找的是紫/粉宝石（hue 125~165 / <=8 / >=170），而这套装备图标
        是橙色 —— 占比只有 0.0065，永远判 0，于是每次都退回按品质选第一张。
        现在改成「金橙色占比 > 0.05」，紫/粉宝石作为旧版图标兼容保留。
        """
        frame = self.frame
        if frame is None:
            self._gem_ratio = 0.0
            return False
        h, w = frame.shape[:2]
        x1 = int(w * (cx - 0.14))
        x2 = int(w * (cx - 0.06))
        y1 = int(h * (cy - 0.34))
        y2 = int(h * (cy - 0.24))
        roi = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        if roi.size == 0:
            self._gem_ratio = 0.0
            return False
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        gold = (sat > 120) & (val > 140) & (hue >= 10) & (hue <= 40)
        gem = (sat > 110) & (val > 110) & (((hue >= 125) & (hue <= 165))
                                           | (hue <= 8) | (hue >= 170))
        gold_ratio, gem_ratio = float(gold.mean()), float(gem.mean())
        self._gem_ratio = max(gold_ratio, gem_ratio)
        # 2026-10-30 实测装备图标的金橙占比：0.074 / 0.106 / 0.131 / 0.161 / 0.532，
        # 书/纸图标三帧都是 0.0000。阈值从 0.05 降到 0.03，留 2.5 倍余量，
        # 免得带彩色翅膀徽章那种金色面积小的图标（0.074）哪天掉到线下漏判。
        return gold_ratio > 0.03 or gem_ratio > 0.02


    def dump_skill_frame(self, texts):
        """三选一限流存帧（每局最多 6 张），用于校准「有队友/有道具」判据。"""
        if getattr(self, "_skill_dumps", 0) >= 6:
            return
        frame = self.frame
        if frame is None or not frame.any():
            return
        idx = getattr(self, "_skill_dumps", 0)
        if self.evidence.dump_frame("skill", texts, frame, idx=idx):
            self._skill_dumps = idx + 1

    def has_long_dialog(self, texts):
        """台词条区域里有没有一整行长句（有就说明这是剧情页，不是选项页）。

        用于 `handle_event_choice`：没有「跳过」时，靠「没有长台词」来区分
        「事件选项」和「剧情对话」（2026-10-26 把台词当选项点过）。
        """
        x1, y1, x2, y2 = Defs.region("dialog_text")
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                continue
            s = t.name.strip()
            if len(s) < 12:
                continue
            if any("\u4e00" <= ch <= "\u9fff" for ch in s):
                return True
        return False

    def stamina_room(self, texts):
        """当前精力离上限还剩多少；顶栏读不到返回 None。

        「这次真能用上多少精力」必须按余量封顶：满精力时回 30 点一分钱不值
        （2026-11-03，和扭蛋 `recover_bonus` 同一口径）。
        """
        bar = self.top_bar(texts) or {}
        cur, mx = bar.get("stamina"), bar.get("stamina_max")
        if cur is None or not mx:
            return None
        return max(mx - cur, 0)

    def usable_stamina(self, effs, room):
        """这组效果**这次真能用上**多少精力（`room` = 离上限还剩多少）。

        属性分永远在前一档，所以读不到精力余量时不猜、直接用原样点数 ——
        宁可把回精力当同级参考，也不能因为读不到顶栏就让属性卡判输。
        """
        gain = self.stamina_gain(effs)
        if not gain:
            return 0
        return gain if room is None else min(gain, room)

    @staticmethod
    def _pick_choice(infos, kind):
        """按用户规则挑选项，返回 (选中项, 理由)。

        用户口径（2026-10-26，2026-10-27 确认多选同样适用）：
        1. **纯回精力**的选项不参与竞争（只补 10 点精力，不如一点属性）；
           全部都是回精力时才退回按数值取最大；
        2. 其余按六维属性的「收益×概率」期望取最高；
        3. 属性打平时才比**能用上的精力**（`sta`，调用方已按余量封顶），
           最后取靠右（二选一时）。
        竖向排列是剧情三选一，仍然固定选第一个。

        2026-11-03：精力**不再折成属性分**（旧口径 ×0.2 混进 ev）。
        「属性+3，精力+30」= ev 3 + 精力 30，不会再变成 9 分去压「属性+8」；
        满精力时 `sta` 会被调用方封成 0，也不会为了回不上的精力加分。
        """
        if kind == "v":
            return min(infos, key=lambda i: i["y"]), "竖向排列 → 第一个"

        def key(i):
            return (i["ev"] if i["ev"] is not None else -1,
                    i.get("sta") or 0, i["x"])

        pool = [i for i in infos if not i["flat"]]
        if not pool:
            # 全都是回精力：没有别的选择，取数值最高的那个（不能因此不选）
            pick = max(infos, key=key)
            return pick, "全是回精力 → 取数值最高的"
        pick = max(pool, key=key)
        why = ("二选一 → 排除纯回精力，取期望最高" if len(infos) == 2
               else "横向多选 → 排除纯回精力，取期望最高")
        if pick["ev"] is None:
            # 2026-10-30 实机：`以逸待劳(2·回精力)、紧张准备(?)` —— 收益读不出来的
            # 那个也**必须**选它：用户口径是「永远不选回精力的」，
            # 之前「未知→选第一个」正好又选回了回精力那张。
            why = "另一个是纯回精力 → 选收益未知的那个"
        return pick, why

    def handle_event_choice(self, texts):
        """突发事件二/三选一：必须点其中一个选项（右上「跳过」是灰的）。

        用户规则（2026-10-26）：
        1. **左右二选一不要选纯回精力的那个**（只回 10 精力，另一个期望值更高）；
           有「X%概率 +N」就按 收益×概率 算期望；
        2. **竖向三选一默认选第一个**（最上面那个）。

        判据用**黄色按钮边框**而不是底部短文字：训练动画页底部也有
        「投射/体能/防守」等 2 字属性名，旧判据会把它们当选项乱点。
        """
        # 这些是地点页的短标签。2026-11-13 实跑：二选一事件的台词
        # 「篮球社集体在公园打街球时…」含「在公园」被判成地点页 → 事件页没人认领，
        # handle_weekend 去点「划船」，点不动 → 接管 3 次后停任务
        # （logs/mcp/6250c6a6…/1113-131116_第2局_百目鬼剑/debug/1113-132147_noresp3s）。
        # 地名标签都很短，长台词不算。
        place_words = ("在公园", "在餐厅", "野餐", "散步", "划船",
                       "当服务员", "桌游", "扭蛋")
        if any(len(t.name.strip()) <= 8 and any(w in t.name for w in place_words)
               for t in texts):
            return False
        # 周末地图：地点名是导航，不是选项（2026-10-26 把「游戏厅/公园」当选项点了）。
        if len([t for t in texts if t.name.strip() in labels.WEEKEND_PLACES]) >= 2:
            return False
        # ---- 阶段 2：互斥声明收口到 `page_signatures` ----
        # 原来这里有 20 个排除词（失败率 / 决定 / 上新时间 / 能力详情 /
        # 继续匹配 / 暂时退出 / 关卡加成 / 奖励预览 / 对手实力 / 前往挑战 /
        # 本周总结 / 球队评分 / 球员 / 位置 / 评分 / 下一步 / main_action…），
        # 全是在说「我不是这些页面」。现在改成一次签名查询：
        # 「这一帧命中了别的页面签名吗」。
        # 签名表在 `src/tasks/training/page_signatures.py`，各页面只声明一次。
        if page_signatures.page_is_other(self, texts, "event_choice"):
            return False
        # 训练科目页也有黄色卡片（器材卡）和底部属性面板，但它们不是选项。
        # 科目页必有「失败率」，并且「决定」是**独立的按钮框**（实训 2026-10-26）。
        # 2026-10-27 事故（第3周「球星卡训练事件·广播站支持」）：正文里有
        # 「成功后，决定通过广播站平台支持球队。」——旧判据用包含匹配找「决定」，
        # 把事件页当成科目页直接 return False，结果 3 次点屏幕中央没反应、卡死停止。
        # 「决定」必须是**独立按钮框**（正文里的不算），所以单独判。
        if self.exact_text(texts, labels.BUTTONS["decide"]) is not None:
            return False
        # 右下角主按钮（VS / 比赛 / 训练）是训练日主界面、比赛开场页的特征。
        if self.match_text(self.texts_in_region(texts, "main_action"),
                           ("VS", "比赛", "训练")) is not None:
            return False
        # 2026-10-27 事故（第2周周日划船结算页）：中间「工资／获得／获得」
        # 三行奖励条目被 choice_candidates 当成竖排三选一，连点 9 次都不动，
        # 任务被判卡死停止。结算页底部一定有「下一步」，事件选项页没有。
        # 「下一步」是**推进按钮**，很多页面都有（结算/商店/公告…），
        # 放签名表会把事件页也排除掉，所以单独判。
        if self.match_text(texts, "下一步") is not None:
            return False
        # ---- 竖向 2/3 选 1（白底彩边，跳过是灰的）----
        # **不要求白底**：事件页背景有时偏暖/偏暗，_has_white_band 会漏判；
        # 漏判后 dialog_continue 会去点灰的「跳过」，表现是一直点跳过、
        # 不选第一个（用户 2026-10-26 反馈）。形态判据见 choice_candidates。
        has_skip = (self.match_text(texts, "训练计划") is None
                    and self.match_text(
                        self.texts_in_region(texts, "top_right"), "跳过") is not None)

        # ---- 底排横向 3 选 1 大卡片（按摩/温泉/强化训练这类）----
        # 2026-10-28 实跑卡死：按摩事件「放弃按摩 属性+10 / 安心按摩 70%概率属性+30 /
        # 神秘按摩 30%概率属性+60」，三个选项是白底大卡（不是黄色按钮），
        # 既不成居中一列、也不被 _yellow_buttons 认出 → 没人认领 → 卡死停任务。
        cards = self._card_choices(texts)
        if len(cards) >= 2:
            room = self.stamina_room(texts)
            infos = []
            for box, effs in cards:
                # 统一口径：属性=ev（不含精力）、纯回精力=flat（不选）、
                # 精力只做属性打平后的次级比较（2026-11-03）
                ev, flat = self.effect_value(effs)
                infos.append({"x": box.x, "y": box.y, "box": (box.x, box.y, box.width, box.height),
                              "name": box.name.strip(), "name_box": box,
                              "ev": ev, "flat": flat,
                              "sta": self.usable_stamina(effs, room)})
            pick, why = self._pick_choice(infos, "h")
            detail = "、".join(
                f"{i['name']}({('%.0f' % i['ev']) if i['ev'] is not None else '?'}"
                f"{'·精力+' + str(i['sta']) if i.get('sta') else ''}"
                f"{'·回精力' if i['flat'] else ''})" for i in infos)
            self.log_info(f"事件选项（横向卡片）：{detail} → 选「{pick['name']}」（{why}）")
            self.click_box(pick["name_box"], after_sleep=0.45)
            return True

        # 2026-10-29 新事件（docs/shots/2026-10-29-event-sim-software.png）：
        # 「只是模拟软件而已，让她们试试 / 不许她们乱来」是各自居中的整句，
        # choice_candidates 的「短名词、无句读」判据会把它们全筛掉，
        # 于是 dialog_continue 一直点灰的「跳过」，卡在原地不动。
        # 这里先认「非段落式整句选项」，再走原来的短名词判据。
        lenient = self.options_not_paragraph(texts)
        if len(lenient) >= 2:
            pick = min(lenient, key=lambda t: t.y)
            self.log_info("事件选项（整句）："
                          + "、".join(t.name.strip() for t in lenient)
                          + f" → 选第一个「{pick.name.strip()}」")
            self.click_box(pick, after_sleep=0.45)
            return True

        group = self.choice_candidates(texts)
        # 事件只可能二/三选一：组里出现 4 项以上，说明把表格列或信息栏捞进来了
        # （2026-10-27 00:02 周结算页 5 个位置名「控卫…中锋」就是这样被当成选项的）
        if group and len(group) > 3:
            return False
        # 竖向选项是**居中**的一列卡片：实测 `2026-10-26-story-choice.png` 三项
        # cx 全是 0.50。靠这条排除「贴边的一列短文本」——这类几乎都是表格列或字幕：
        #   周结算左列「控卫/分卫/小前/大前/中锋」cx≈0.26，
        #   周末前对话「我要去和小式一起去看电影」cx≈0.68。
        # 比按文字内容/符号做排除可靠：台词里什么符号都可能出现。
        if group:
            centers = [(t.x + t.width / 2) / self.width for t in group]
            if (max(centers) - min(centers) > 0.06
                    or not 0.38 <= sum(centers) / len(centers) <= 0.62):
                return False
        # 只有 1 项也不是选项：剧情页右上角同样有「跳过」，has_skip 会把它放进来
        # （2026-10-27 10:07 周末前对话「我要去和小式一起去看电影」被点 5 次空点）
        # 2026-10-29 器材事件：`精力恢复 / 资金援助` 两行选项、**没有「跳过」**。
        # 旧条件要求 has_skip，导致这里放弃 → 兜底点中央正好压中下面那个选项。
        # 判据不能按「字数」：用户另一次上下选项里上面那行有 10 个字以上。
        # 真正的区别是**选项压在白色圆角底板上**，剧情台词直接浮在画面上：
        #   - 短标签（≤8 字，器材事件这种）也算；
        #   - 或者整组都检测到白色底板（不限字数）。
        short_pair = (len(group) >= 2
                      and all(len(t.name.strip()) <= 8 for t in group)
                      and not self.has_long_dialog(texts))
        plate_pair = (len(group) >= 2
                      and all(self._has_white_band(t) for t in group))
        if group and len(group) >= 2 and (has_skip or len(group) >= 3
                                          or short_pair or plate_pair):
            pick = min(group, key=lambda t: t.y)
            if os.environ.get("OK_DUMP_CHOICES"):
                self.dump_unknown(texts, "choice_vertical")
            self.log_info("事件选项（竖向）：" + "、".join(t.name.strip() for t in group)
                          + f" → 选第一个「{pick.name.strip()}」")
            self.click_box(pick, after_sleep=0.45)
            return True

        buttons = self._yellow_buttons()
        if len(buttons) < 2:
            return False

        # 只接受「排成一行」或「排成一列」且尺寸相近的按钮组。
        # 训练剧本页也有黄色「启程」大按钮 + 标题里的黄色碎片，但两者是斜对角，
        # 既不成行也不成列，必须排除（实训 2026-10-26）。
        def center(b):
            return b[0] + b[2] / 2, b[1] + b[3] / 2

        def similar(a, b):
            return (max(a[2], b[2]) / max(min(a[2], b[2]), 1) < 2.6
                    and max(a[3], b[3]) / max(min(a[3], b[3]), 1) < 2.2)

        best_group, best_kind = [], None
        by_y = sorted(buttons, key=lambda b: b[1])
        for i, b in enumerate(by_y):
            group = [b]
            for c in by_y[i + 1:]:
                if abs(center(b)[1] - center(c)[1]) < self.height * 0.05 and similar(b, c):
                    group.append(c)
            if len(group) > len(best_group):
                best_group, best_kind = group, "h"
        by_x = sorted(buttons, key=lambda b: b[0])
        for i, b in enumerate(by_x):
            group = [b]
            for c in by_x[i + 1:]:
                if abs(center(b)[0] - center(c)[0]) < self.width * 0.12 and similar(b, c):
                    group.append(c)
            if len(group) > len(best_group):
                best_group, best_kind = group, "v"
        if len(best_group) < 2:
            return False
        buttons = best_group
        # 2026-10-29 实机：上下两个选项被当成横向，机器人选了下面那个。
        # 用几何形状兜底：一组按钮的纵向跨度 > 横向跨度，就是竖排 → 选最上面。
        centers = [(b[0] + b[2] / 2, b[1] + b[3] / 2) for b in buttons]
        dx = max(c[0] for c in centers) - min(c[0] for c in centers)
        dy = max(c[1] for c in centers) - min(c[1] for c in centers)
        if dy > dx:
            best_kind = "v"

        infos = []
        room = self.stamina_room(texts)
        for (bx, by, bw, bh) in buttons:
            inside = []
            for t in texts:
                cx = (t.x + t.width / 2) / self.width
                cy = (t.y + t.height / 2) / self.height
                if (bx / self.width <= cx <= (bx + bw) / self.width
                        and by / self.height <= cy <= (by + bh) / self.height):
                    inside.append(t)
            # 名字放宽到 12 字、不要求纯中文（「85%概率」类卡片也要能认出）；
            # 纯数字不算名字。
            names = [t for t in inside
                     if 2 <= len(t.name.strip()) <= 12
                     and not t.name.strip().isdigit()]
            effects = [t for t in inside if any(ch.isdigit() for ch in t.name)]
            name_box = max(names, key=lambda t: t.height) if names else None
            name = name_box.name.strip() if name_box else "?"
            ev = self._effect_ev(effects)
            attrs = any(any(a in t.name for a in labels.TRAINING_SUBJECTS)
                        for t in effects)
            # 「纯回精力」= 只补精力、不加任何六维属性。带属性的（「精力+10，投射+5」）不算，
            # 否则会把好选项也一起排除掉。
            flat_stamina = (any("精力" in t.name for t in effects) and not attrs)
            infos.append({"x": bx, "y": by, "box": (bx, by, bw, bh),
                          "name": name, "name_box": name_box,
                          "ev": ev, "flat": flat_stamina,
                          "sta": self.usable_stamina(effects, room)})

        # 框内一个字都没有的，是两卡之间的黄色装饰/空白，点它没用
        # （用户 2026-10-26 反馈：二选一时在中间空白点了两次）。
        infos = [i for i in infos if i["name_box"] is not None]
        if len(infos) < 2:
            return False

        pick, why = self._pick_choice(infos, best_kind)
        detail = "、".join(
            f"{i['name']}({('%.0f' % i['ev']) if i['ev'] is not None else '?'}"
            f"{'·精力+' + str(i['sta']) if i.get('sta') else ''}"
            f"{'·回精力' if i['flat'] else ''})" for i in infos)
        self.log_info(f"事件选项：{detail} → 选「{pick['name']}」（{why}）")
        if os.environ.get("OK_DUMP_CHOICES"):
            self.dump_unknown(texts, "choice_yellow")
        # 点文字框中心，不点黄色外框中心：外框可能横跨两卡，
        # 中心落在中间空白上（用户 2026-10-26 反馈）。
        self.click_box(pick["name_box"], after_sleep=0.45)
        return True
