"""单拍状态机、存证、卡死检测。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""

import time

from src.tasks.training import params
from src.tasks.training.queue import CaptainQueue
from src.tasks.ui import labels
from src.util.emergency_stop import STOP
from src.util.log_fallback import ensure_run_log

# 点击流水 / 事件截图的落盘细节**全部在 src/tasks/evidence.py**（2026-11-10 归口：
# 原来这里和各 handler 各自拼路径，一共 7 个写入点）。本文件只决定「存什么」。

# 这些 handler 命中时存一帧（报告页要「事件 ↔ 截图」一一对应）。
# 训练科目/比赛内这种每几秒一次的就不存，避免每轮几十张图。
SNAP_HANDLERS = {
    "handle_event_choice", "handle_boost_training", "handle_restaurant",
    "handle_park", "handle_street", "handle_arcade", "handle_shop",
    "handle_stock_pick", "handle_stock_result", "handle_skill_select",
    "handle_weekend", "handle_go_out", "handle_training_plan",
    "handle_result", "handle_match_result", "handle_prepare",
    "handle_register_player", "handle_vacation", "handle_rest_confirm",
}

# 游戏厅小游戏播动画期间，用这些「真实界面标记」判断动画结束了没。
# 注意不能放「星期 / 比赛」这类顶栏文字：小游戏画面本身也带顶栏（第N周 星期六）。
ARCADE_WAIT_MARKS = ("出行", "公园", "步行街", "游戏厅", "失败率", "决定", "休息",
                     "训练", "商店", "获得奖励", labels.TAP_TO_CONTINUE, "跳过", "下一步",
                     "战术板", "赛程")

# 卡住接管时优先点的「推进型」按钮（按顺序试），点不到才回退点屏幕中央。
#
# 为什么不能只点中央（2026-11-08 实跑，logs/debug/1108-231656_noresp3s）：
# 炒股选股页是「选卡 → 确定」两段式，屏幕中央正好是中间那张卡 —— 三次接管全
# 点在卡上，界面纹丝不动，一分钟后被判死停任务。两段式页面只有点按钮才推进。
#
# **接管永远不点「确定」**（2026-11-09 收紧）：扭蛋选卡页（「请选择1个扭蛋」+
# 60/80/100 + 确定）和游戏厅选卡页（「请选择一个小游戏」+ 价格 + 确定）整页
# 没有「购买/钻石」字样，用关键词黑名单根本挡不住 —— 点下去就是替用户花零用钱，
# 还绕过了 handle_street / handle_arcade 的品质与精力判断。所以只允许点
# 「下一步 / 决定 / 过场提示」这三类不会产生消费的按钮；两段式页面推不动就
# 让 3 次接管后的停任务兜底（停任务比乱花钱安全，正解在 handler 判据/顺序）。
STUCK_ADVANCE_BUTTONS = ("next", "decide")

# 监控页（src/ui/MonitorTab.py）只认「域」这一级，不逐个 handler 维护文案。
# 新增 handler 只要模块不变就自动归类，不会因为漏配而显示成技术名。
UI_AREAS = {
    "popup": "弹窗",
    "training": "训练",
    "weekend": "周末",
    "match": "比赛",
    "entry": "入口",
    "fishing": "钓鱼",
}

# 监控页时间线保留的条数（UI 那边最多显示 8 条）。
UI_EVENT_LIMIT = 12

# 监控页「最近动作」详情列 = 本拍识别到的文字。
# 一帧 OCR 常有 20~40 条（专注、金币、按钮、顶栏都算），单行放不下，
# 取前几条去重后截断；保留顺序（OCR 的检测顺序），不排序 —— 排序会让
# 「第 N 周」「训练」这些关键界面词乱序，反而不像原界面。
UI_TEXT_LIMIT = 8
UI_TEXT_MAXLEN = 50


def summary_texts(texts, limit=UI_TEXT_LIMIT, max_len=UI_TEXT_MAXLEN):
    """把本拍 OCR 结果压成一行短文案，给监控页时间线用。

    只做展示，不参与业务判断；`texts` 为空或元素没有 name 都容错。
    """
    seen, items = set(), []
    for t in texts or []:
        name = (getattr(t, "name", "") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        items.append(name)
        if len(items) >= limit:
            break
    text = " / ".join(items)
    if len(text) > max_len:
        text = text[:max_len - 1] + "…"
    return text


def _stall_limits():
    """`handlers/__init__.py: STALL_LIMITS` 的懒加载（flow 不能顶层 import handlers，会成环）。"""
    from src.tasks.training.handlers import STALL_LIMITS
    return STALL_LIMITS


class TrainingFlowMixin:

    def input_blocked(self):
        """输入层每一下动作前会问一次：现在该不该动手（True = 别动）。

        挂到 `interaction.abort_check` 上，供等待用户空闲的循环和点击前的二次校验用。
        覆盖三种：紧急停止键、任务被停、用户点了【暂停】。
        """
        if STOP.event.is_set() or not self._enabled:
            return True
        return bool(getattr(self, "paused", False))

    def wait_while_paused(self):
        """暂停 = 原地挂起，不退出、不丢本局进度。

        框架的 `Task.pause()`（ok/task/task.py:1214）对一次性任务只置 `_paused=True`，
        `TaskExecutor` 不会打断正在执行的 `task.run()`，所以本局会照常点下去——
        用户点【暂停】后鼠标仍然被抢，根因就在这里（2026-11-08）。这里自己挂住：
        暂停期间一拍都不做，继续后从当前这一拍接着跑。
        """
        if not getattr(self, "paused", False) or not self._enabled or STOP.event.is_set():
            return
        self.log_info("已暂停 → 挂起（鼠标已放手；点【开始】从当前进度接着跑）", notify=True)
        while self.paused and self._enabled and not STOP.event.is_set():
            self.sleep(0.2)
        if self._enabled and not STOP.event.is_set():
            self.log_info("已继续 → 恢复挂机")

    def run(self):
        """一次 run = 把**多角色队列**里的角色依次练到目标分（用户 2026-11-04 口径）。

        队列有两个来源：CLI/MCP 注入的 `_launch_queue`（一次性，不落盘）
        或由配置派生（`指定队长` + `刷到目标分`/`目标分`，落盘、重启还在）。
        都没有 → 老行为，按界面「指定队长」跑完一局就结束（`run_one_round`
        返回 True 即任务完成）。有队列 → 每一局跑完回来记一次分、必要时换队长，
        直到**全部达标提前终止**，或撞上 `最大局数` 兜底。
        """
        try:
            return self.run_rounds()
        finally:
            # interaction 是框架全局单例：本任务退出后必须把 abort_check 摘掉，
            # 否则 TriggerTask（快速比赛）会被一个「已停止的训练任务」永远拦着。
            self.release_input_gate()

    def run_rounds(self):
        self.init_captain_queue()
        max_runs = max(1, int(self.config.get("最大局数")))
        # 队列在**上次运行**里就已经全部达标（进度存在 logs/training_queue.json）→
        # 直接收工，不要再白跑一局（2026-11-04：达标却还开新局跑）
        if self._queue is not None and self._queue.finished:
            self.log_info(self.queue_finished_message(self._queue), notify=True)
            return True
        runs = 0
        while True:
            runs += 1
            self._round_no = runs        # 局ID 里的「第N局」（只在队列模式出现）
            if not self.run_one_round():
                return False
            if not self.advance_queue_after_round():
                return True
            if runs >= max_runs:
                goal = "目标分" if len(self._queue.items) == 1 else "全部目标"
                self.log_error(f"连跑 {runs} 局仍未达成{goal}（最大局数 {max_runs}）→ 停止",
                               notify=True)
                return False

    def release_input_gate(self):
        """把 abort_check 从共享 interaction 上摘掉（只摘自己挂的那个）。

        `og.device_manager.interaction` 是框架全局单例，TriggerTask 也在用它；
        训练任务退出后留着 `input_blocked`，快速比赛的点击会被永远拦掉。
        """
        interaction = getattr(self, "_gate_interaction", None)
        self._gate_interaction = None
        if interaction is None:
            return
        checker = getattr(interaction, "abort_check", None)
        if getattr(checker, "__self__", None) is self:
            interaction.abort_check = None

    def run_one_round(self):
        """跑**一局**：从当前界面一直到「注册球员完成、回大厅」。

        返回 True = 这一局正常跑完（可以接着开下一局），False = 出事了/被停止。
        多角色时上层（`run`）负责换队长再进来一次。

        存证按局分目录（2026-11-10）：本方法只负责「开局写 meta、收工写 meta」，
        目录布局和命名全在 `evidence.EvidenceSink.begin_round/end_round`。
        """
        try:
            return self._run_one_round()
        finally:
            # captain 要到收工才知道：接管局开局时没指定队长（目录已按「接手」命名），
            # 队长由日历识别出来、只写在 `_style_name` 里（2026-11-10 19:08 实跑）。
            self.evidence.end_round(
                getattr(self, "_abort_reason", "") or "",
                self._steps,
                captain=getattr(self, "_style_name", "") or self.configured_captain(),
                style=getattr(self, "_style", "") or "")

    def start_round_evidence(self):
        """定下本局的存证目录（回大厅之后调用：那段时间不属于这一局）。"""
        captain = self.configured_captain()
        self.evidence.begin_round(
            captain=captain,
            handover=not captain,          # 没指定队长 = 快速接手别人开好的局
            round_no=getattr(self, "_round_no", 0),
            style=getattr(self, "_style", "") or "",
            entry="MCP" if self.mcp_driven else "GUI",
            keep_days=self.config.get("日志保留天数"),
            save=bool(self.config.get("保存调试存证")),
        )

    def _run_one_round(self):
        self._steps = 0
        # 日志文件自检（2026-11-10）：GUI 跑任务时 ok 的 FileHandler 会失踪，
        # 整场运行没有日志落盘。这里每次开局确认一次，没有就由我们补一个
        # 写 logs/run.log 的 handler（细节见 src/util/log_fallback.py）。
        ensure_run_log()
        # 暂停中不要开新局（队列模式会在两局之间回来）：等着，继续后接着开
        self.wait_while_paused()
        # MCP / GUI 双入口：定本次运行的产物根。MCP 的 runner 已经 reset+verify 过，
        # GUI/CLI 走这里自己恢复基线（失败只告警，不打断用户手动起的任务）。
        self.begin_attempt()
        if not self.mcp_driven:
            if not self.recover_to_lobby():
                self.log_warning("GUI 运行：自动回大厅失败，按当前界面继续"
                                 "（MCP 运行遇到同样情况会直接判失败）")
            else:
                self.log_info("GUI 运行：已回到大厅，开始跑流程")
        # 回大厅之后再开局目录：局ID 的时间戳 = 真正开始跑这一局的时刻
        self.start_round_evidence()
        self.log_info(f"本局存证：{self.evidence.round_dir}")
        STOP.ensure_started()
        self.log_info(f"紧急停止热键：{STOP.label or '（注册失败，F8/F12 轮询兜底已启用）'}")
        # 代码版本戳：确认跑的是哪份 parsers.py（圈识别调效果时排查过「改了不生效」）
        try:
            from src.tasks.training import parsers as _p
            self.log_info(f"parsers: {_p.__file__} v{getattr(_p, 'CODE_VERSION', '?')}")
        except Exception:                        # noqa: BLE001
            pass
        interaction = self.apply_user_idle_wait(self.config.get("空闲等待(秒)"))
        # 等待用户空闲可能很久：紧急停止、任务被停、用户点了【暂停】都要能立刻
        # 中断等待（否则暂停后它还在等 3 秒才收手）。暂停后的常态拦截见
        # `wait_while_paused`：框架的 task.pause() 对一次性任务只置标志，不会打断 run()。
        if interaction is not None and hasattr(interaction, "abort_check"):
            interaction.abort_check = self.input_blocked
            self._gate_interaction = interaction      # 退出时按这个引用摘掉（见 run）
        # 2026-11-01 修正（用户口径）：队长/流派**只有两条解析路径，都不许保底**：
        #   A. 配置里填了「指定队长」→ 查表定流派；
        #      查得到 √，查不到 → **弹窗**。
        #   B. 配置为空（快速接手）→ 点多次返回回到训练室/校门口（那里能看到日期），
        #      点日历 → 抓队长名字 → 查表定流派；
        #      认到了 √，认不到 → **弹窗**。
        #
        # 旧逻辑的毛病：认不出就静默兜底「训练流」，结果扭蛋流跑成训练流
        # （实跑 2026-11-01 19:19），而且日志上看不出来。
        #
        # 这里只做「路径 A」——路径 B 要点几次返回、跨几拍才能完成，
        # 放在主循环里做（见 `_resolve_style_step`）。
        self.reset_style()
        if not self.resolve_style_by_config():
            self._style_pending = True
            self.log_info("未指定队长 → 回训练室/校门口点日历，识别队长")
        else:
            # 配置里的队长已经把流派定下来了 → 明确关掉「待认队长」，
            # 否则上一局留下的 True 会让这一局开局先去点日历（多角色换人时踩过）
            self._style_pending = False
        # 运行期状态每次开局都要清（2026-10-29 实机：连开第二把时任务秒退，
        # 因为 _register_stage 还停在上次的 "back"，run() 的终点判据立刻命中）
        self.reset_run_state()
        self.log_info(f"流派：{self.style}"
                      + ("" if self.style_known else "（待日历识别队长）"))
        # 用户 2026-11-04 口径：「2000 步的限制不要了」—— 正常运行**不封顶**。
        # 只有 MCP 的 tick harness（`tools/mcp/training_tick.py`）会用
        # `单局最大操作数` 注入一个临时上限（跑 N 拍就停），所以这里只在
        # 配置里**真的有**这个键时才生效（它已经不在 default_config 里了）。
        max_steps = self.config.get("单局最大操作数")
        max_steps = int(max_steps) if max_steps else None
        interval = float(self.config.get("操作间隔(秒)"))
        # 卡住判据（用户 2026-11-04 的**两条**口径，都做成「立刻接管」）：
        #   ① 点击后 `点击无响应判定(秒)`（默认 3 秒）界面没变化；
        #   ② 同一天点击超过 `单日最大点击数`（默认 100）下。
        # 见 flow.stuck_takeover。
        self.reset_day_home()
        self._sig_changed_at = time.time()
        last_sig = None                # 只用于 unknown 存证限流 + 上面那 3 秒判据
        self._last_dump = 0.0

        while max_steps is None or self._steps < max_steps:
            # 业务侧主动喊停（例如指定队长不在池子里）：下一拍就退出，不再往下点。
            if getattr(self, "_abort_reason", None):
                self.log_error(f"主动停止：{self._abort_reason}", notify=True)
                return False
            # 本局已经收账（「训练评分」页读到达标分 / 注册球员流程走完）→ 回大厅开下一局
            if getattr(self, "_round_done", False):
                self._round_done = False
                self.log_info("本局结束（评分已收账）→ 交给队列判定/提前终止", notify=True)
                return True
            if STOP.consume():
                self.log_warning(f"按下紧急停止键（{STOP.last_press or STOP.label}）→ 立即中断，鼠标已释放",
                                 notify=True)
                return False
            if not self._enabled:
                self.log_info("任务已停止")
                return False
            # 暂停：框架不会打断 run()，这里自己挂住（不退出、不丢进度）
            self.wait_while_paused()
            self._steps += 1

            # 本拍只用这一帧：识别、存证、调试包都从它来。
            # 坑（2026-10-30 用户反馈「截图没截到正确时间点」）：snap_event 原来在
            # handler 返回后读 self.frame，那时采集线程已经给出**点击之后**的新帧，
            # 存下来的是「已经翻页」的画面，报告里自然对不上事件。
            frame = self.frame
            self._tick_frame = frame
            self._tick_clicks = []
            texts = self._ocr_region(frame=frame)
            # 本拍 OCR 结果留给 top_bar()：**裁剪顶栏再 OCR 会把专注条的「/」吃掉**
            # （`0/130` → `301130`），STAMINA_RE 要求有斜杠 → stamina 恒为 None，
            # 「精力不够就改选不耗精力那张」的规则全部失效（2026-10-31 餐厅实跑）。
            # 整帧这份是免费的，直接复用。
            self._tick_texts = texts
            if not texts:
                self.log_debug("本帧没有识别到文字，等待")
                self.sleep(0.3)
                continue

            if self.is_loading(texts):
                self.sleep(0.3)
                continue

            # 游戏厅买完小游戏：小游戏会**自己打完**，只等动画结束就行（用户 2026-10-29
            # 口径），不要点、也不要算卡死。等画面重新出现已知界面标记再交回 handler。
            if self.arcade_still_playing(texts):
                if time.time() - self._arcade_wait_since > 300:
                    self.log_warning("游戏厅小游戏 5 分钟还没结束 → 继续按普通流程处理")
                    self._arcade_wait_since = 0.0
                else:
                    last_sig = None                # 动画期间不算「界面没变」
                    self.sleep(0.5)
                    continue
            elif getattr(self, "_arcade_wait_since", 0.0):
                self._arcade_wait_since = 0.0
                last_sig = None

            # 上一拍被暂停/让路拉长（等用户停手、监控页暂停、抢前台失败）→ 这段空转
            # 里没有点击、界面当然不会变，不能算「点了没反应」。
            # 2026-11-10 19:10 实跑：让路 13.9 秒被判成 15.8 秒没变化，接管 3 次后停任务。
            now = time.time()
            if now - getattr(self, "_tick_at", 0.0) > 5.0:
                self._sig_changed_at = now
                self._last_click_at = 0.0
            self._tick_at = now

            # 界面指纹：同一页面每拍都一样；用于 ①「3 秒无响应」判据 ② unknown 存证限流
            sig = self._signature(texts)
            changed = sig != last_sig
            if changed:
                last_sig = sig
                self._sig_changed_at = time.time()

            # 同一天计数器：读到新日期就归零（用户口径：「日期更新以后计数器重置」）
            self.day_counter_step(texts)

            # ---- 卡住就**立刻接管**（用户 2026-11-04 两条口径）----
            # ① 点击后 3 秒界面没变化；② 同一天点击超过 100 下。
            # **必须优先于所有 handler** —— 否则「handler 一直命中、点了没反应」会空转到死
            # （剧情页「跳过」是灰的，连点 70+ 次，实训 2026-10-26）。
            if self.stuck_takeover(texts, frame):
                continue

            # ---- 点 3 次没变化 → 立刻进入 debug（用户 2026-10-26 口径）----
            # 这一条和第②条互补：②看「同一天点了多少下」，这条看「连点 3 下同一页没翻」。
            if self._sig_after_click is not None:
                if sig == self._sig_after_click:
                    self._no_change += 1
                else:
                    self._no_change = 0
                    self._sig_after_click = None
                    self._stuck_rounds = 0

            if self._no_change >= 3:
                self._stuck_rounds += 1
                self.dump_debug(texts, f"nochange{self._no_change}", frame)
                self.log_warning(
                    f"连续 {self._no_change} 次点击界面没变化（上次动作：{self._last_state}）"
                    f"→ 已存证（本局 debug/），点击屏幕中央重试")
                if self._stuck_rounds >= 3:
                    self.log_error("已经 3 轮「点不动」→ 停止任务，避免一直卡在同一界面")
                    return False
                self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=interval)
                self._no_change = 0
                self._sig_after_click = sig
                continue

            # ---- 队长/流派还没定 → 先专心认队长，不做别的 ----
            # 2026-11-01 用户口径：接手别人的局时，点多次返回回到训练室/校门口，
            # 点日历抓队长名字。识别期间**不跑别的动作** ——
            # 认错队长 = 整局按错流派跑（比停下来更糟）。
            if self.style_pending():
                if self._resolve_style_step(texts):
                    self.sleep(interval)
                    continue

            # ---- 一局的终点：注册球员六步走完、**回到大厅**才算跑完 ----
            # 2026-10-27 用户补充：打完训练赛 → 注册球员 → 编队 → 保存 → 回大厅，
            # 才是完整一局；旧版停在「注册球员」页就算完成，把最后 6 步留给玩家。
            #
            # 2026-11-10 修正（用户报「刷完提示后界面又点开训练页」）：旧判据是
            # 「不在战术板」，靠「战术板 / 首发 / 保存编队」三个**短词做子串匹配**，
            # 回大厅的过渡帧里「首发」很容易误中 → 这一拍不算收工，继续往下走 handler，
            # 被 handle_lobby 点了大厅「训练」（实跑 01:06:41 点了 (1203,788)）。
            # 现在**先认大厅**（`is_lobby`：社团GO / 赛事 / 转一转 / 冠军之路…），
            # 认到就收工；认不出大厅时再退回旧的「不在战术板」兜底。
            at_board = (self.match_text(texts, "战术板") is not None
                        or self.match_text(texts, "首发") is not None
                        or self.match_text(texts, "保存编队") is not None)
            if getattr(self, "_register_stage", None) == "back" and (
                    self.is_lobby(texts) or not at_board):
                self.log_info("注册球员流程完成（已回大厅）→ 本局结束", notify=True)
                return True

            # ---- 商店：看到「新品上市」标签就进店（2026-11-08 起不分周数）----
            # 放在所有 handler 之前 —— 一处盖住训练日 / 比赛日 / 周末首页三种入口，
            # 否则训练、前往比赛、出行会先把这一天用掉
            # （判据见 daycycle.late_shop_step + places/shop.py: shop_fresh_badge）。
            if self.late_shop_step(texts):
                self.sleep(interval)
                continue

            if self._handle_any(texts, frame):
                self.sleep(interval)
                continue

            # ---- 兜底：不认识的界面也**让流程继续走**，不要卡死 ----
            # 存证限流：同一段时间最多 5 秒存一张，避免动画期间刷出几十张图
            if changed and time.monotonic() - self._last_dump > 5:
                self.dump_unknown(texts, "unknown")
                self._last_dump = time.monotonic()
                self.log_warning("没见过这个界面 → 已存证，点一下继续")

            # 兜底点击也要参与「点 3 次没变化」检测，否则不认识的界面
            # 会一直静默点中央，既不 debug 也不停（2026-10-26 公园详情页踩过）。
            self._sig_after_click = sig
            self._last_state = "unknown"
            # 点画面中央：对话页能推进、空白处能关弹窗、大部分过场也吃这一下
            self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=interval)

        self.log_warning(f"达到最大操作数 {max_steps}，停止")
        return False

    def reset_run_state(self):
        """每次 run() 开始时清掉「上一局遗留」的运行期状态。

        ok-script 会复用同一个 Task 实例，不清就会：
        - `_register_stage == "back"` → 第二把秒退「任务完成」（2026-10-29 实机）；
        - 商品/商店标记、卡死计数、快进标记带到新的一局。
        """
        self._register_stage = None
        self._reg_ticks = 0
        self._abort_reason = None
        self._force_rest = False
        self._shop_day = None
        self._shop_try_key = None
        self._shop_try_count = 0
        self._shop_seen = False
        self._shop_unmark_seen = set()      # 未登记商品一局只存一次样本
        self._arcade_wait_since = 0.0
        self._no_change = 0
        self._sig_after_click = None
        self._stuck_rounds = 0
        # 卡住判据的计数器（同一天在休息室/校门口点了几下）：换局必须清，
        # 否则上一局攒到 90 多次的计数会带进新的一局，开局就误判「卡住」
        self.reset_day_home()
        # 本局收账标记（「训练评分」弹层读到分 → `_round_done`）必须每局清干净，
        # 否则第二局开局就自己判「本局结束」（2026-11-04 加）
        self._round_done = False
        self._round_score_seen = False
        self._score_click_at = 0.0
        # 「同一个 handler 连续命中」计数（STALL_LIMITS 用），换局必须清
        self._loop_handler = None
        self._loop_count = 0
        # 旁路页面分类的缓存：换局重新记录「页面变化」，不要把上一局最后一页带进来
        self._page_id = None
        self._page_conflict = ()
        # 选队长前的筛选流程（显示设定 → 筛选 → 重置 → 缩池 → 确定）
        self._captain_filter_done = False
        self._captain_filter_stage = None
        self._picked_subject = False
        self._picked_index = 0
        self._subject_confirmed_at = 0.0
        self._match_flags = {"manual_fixed": False, "fast_clicked": False,
                             "last_fast_at": 0.0}
        self._subject_dumps = 0
        self._skill_dumps = 0
        self._stock_dumps = 0
        # 选择笔记页的限流标记（2026-10-30：同一页重复点击）
        self._note_auto_done = False
        self._note_next_tries = 0
        # 转盘（钓鱼 / 做饭 等）：点一次后锁定 8 秒（后面是动画，界面文字不变）
        self._wheel_clicked_at = 0.0
        # 上一局的六维不能带进新的一局（换角色 / 换存档时监控页会显示别人的数值）。
        # 下一次进休息室会拍新快照（见 parsers.snapshot_lounge），很快就填回来。
        # `_attr_caps`（六维上限）同理：不同角色上限不同，沿用上一局会算错缺口。
        self._attr_caps = {}
        info = getattr(self, "info", None)
        if isinstance(info, dict):
            info.pop("attrs", None)
            # 「卡住干预」那一行的上一次结论也别留着（新一局从头数点击）
            info.pop("stuck", None)
            # 技能是**一局一个角色**的量（`handle_skill_select` 每选一次 +1），
            # 必须每局清零。2026-11-10 实跑：只在 enable 时清 → 8 局攒成
            # 「彩1 金37 白9」→ 监控页分数预测 9444，而注册球员页真值 7703
            # （真值 1 彩 + 4 金 + 2 白 = 325 分，一局只有 5 次三选一）。
            info.pop("skill_gained", None)
            info.pop("skill_last", None)

    # ---------- 多角色队列（2026-11-04 用户口径） ----------

    def captain_queue_spec(self) -> str:
        """从**配置**派生队列参数（唯一入口）：`指定队长` + `刷到目标分`/`目标分`。

        2026-11-09 用户口径：第二栏的勾选框/下拉/目标分都是**设置**，落盘、
        启动装载；队列串本身不落盘 —— 它是从这三个键算出来的派生值，
        每次 run 重新算，配置一改自然就换计划。

        返回 `"队长:目标分"`（有目标分）或 `"队长"`（勾了刷分但还没填分），
        没指定队长 / 没勾「刷到目标分」→ 空串（单队长模式）。
        """
        captain = (self.config.get("指定队长") or "").strip()
        if not captain or not self.config.get("刷到目标分"):
            return ""
        target = int(self.config.get("目标分") or 0)
        return f"{captain}:{target}" if target else captain

    def init_captain_queue(self):
        """定下这一次 run 要练的角色顺序。

        两个来源，优先级从高到低：
        1. **启动参数**（`self._launch_queue`，CLI / MCP 的 `next_captain.json`
           注入）—— 一次性，不写 configs、进程结束即失效；
        2. **配置派生**（`captain_queue_spec()`：指定队长 + 刷到目标分/目标分）
           —— 落盘的设置，重启后照旧生效。

        清空的职责在**界面侧**：`MonitorTab.set_captain()` 在用户改队长时清
        `_launch_queue`（「本次注入的安排作废」）；配置派生值不用清 ——
        用户改队长/目标分，下一次 run 重新派生即可。
        `多角色队列` 配置键已废弃（保留在 default_config 里只为不炸老存档）。

        **暂停→继续不丢进度**：`self._queue` 还挂在任务实例上、游标在内存里。
        这里**先看活着的队列**：还在且指纹没变 → 接着跑，不重建。
        """
        self._run_score = None
        spec = (getattr(self, "_launch_queue", "") or "").strip()
        if not spec:
            spec = self.captain_queue_spec()
        living = getattr(self, "_queue", None)
        if not spec:
            # 没队列参数（正常单队长模式，或界面改队长时刚被清掉）
            if living is not None:
                self.log_info("队列参数已空（界面改过队长）→ 单队长模式")
            self._queue = None
            return None
        if living is not None and not living.empty:
            # 暂停后继续（或 run() 正常返回又进来）：队列对象还活着 → 接着跑。
            # 指纹对不上 = 队列参数被换过 → 重建。
            if living.signature() == CaptainQueue(spec).signature():
                self._queue = living
                self.log_info(f"多角色队列继续（暂停后续跑）：{living.summary()}")
                return self._queue
            self.log_info("队列参数已变更 → 重建多角色队列")
        self._queue = None
        queue = CaptainQueue(spec)
        if queue.empty:
            self.log_warning("队列参数解析为空 → 按单队长跑")
            self._launch_queue = None
            return None
        self._queue = queue
        if len(queue.items) == 1:
            item = queue.items[0]
            target = item.get("target")
            self.log_info(f"刷分队列：{item['name']} → "
                          f"{target if target else '一直刷到手动停止'}")
        else:
            self.log_info(f"多角色队列（本次运行参数）：{queue.summary()}")
        self.log_info(f"当前队长：{queue.captain}｜位置 {queue.position}"
                      f"｜目标 {queue.target}｜已跑 {len(queue.runs_for())} 局")
        return queue

    def adopt_calendar_captain(self, name):
        """接手局认出队长 → 勾了「刷到目标分」时记下队长并补建队列。

        用户 2026-11-11 口径：不指定队长 = 接手一局没打完的训练；
        勾了「刷到目标分」就应该接到这个人身上继续练到分停。
        日历认出名字后（`handlers/training.handle_training_plan` 或
        `daycycle.resolve_style_by_calendar_name`）调这里：

        - 只在**接管模式**（配置里没有队长）生效 —— 用户自己指定过队长时
          日历根本不会识别（`style_known` 已定），这里也不抢；
        - 只在**勾了刷到目标分**时写配置 —— 没勾就跑完当前这局结束，
          没必要把「不指定队长」变成「指定队长」（下次还想接手）；
        - 写完立刻 `init_captain_queue()`：`captain_queue_spec()` 现在能派生
          `"队长:目标分"`，这一局结束就有队列可记分、未达标继续同一个人。
        """
        name = (name or "").strip()
        if not name:
            return False
        if (self.config.get("指定队长") or "").strip():
            return False
        if not self.config.get("刷到目标分"):
            return False
        self.config["指定队长"] = name
        self.config["队长下拉选择"] = name
        self.init_captain_queue()
        target = int(self.config.get("目标分") or 0)
        self.log_info(f"接手局：日历认出队长「{name}」→ 已写入指定队长，"
                      f"随后练到 {target} 分（未填则一直练）", notify=True)
        return True

    def note_run_score(self, texts):
        """在「恭喜获得 / 训练奖励」页读**这一局要判定的球员评分**（每局只记第一次）。

        队长 → 位置（`roster.position_by_name`）→ 那一列的四位数评分。
        读不到就返回 None 且**不改** `_run_score`：`target_met(None)` 恒为 False，
        这一局按「未达标」处理，同一个队长再练一局（宁可多练，不许假达标）。
        """
        queue = getattr(self, "_queue", None)
        if queue is None:
            return None
        if getattr(self, "_run_score", None) is not None:
            return self._run_score
        ratings = self.read_squad_ratings(texts)
        pos = queue.position
        score = ratings.get(pos) if pos else None
        if score is None:
            # 页面淡入的第一拍 OCR 必读不到（2026-11-10 实跑 4/4 都是下一拍才成功），
            # 这里报 warning 会每次都吓人一跳；真读不到由局末收账补一条。
            self.log_debug(f"读不到队长「{queue.captain}」（位置 {pos}）的评分"
                           f"——这一页读到 {ratings or '空'}，下一拍再读")
            return None
        self._run_score = score
        self.log_info(f"本局评分：{queue.captain}（{pos}）{score}"
                      f" / 目标 {queue.target}")
        return score

    def advance_queue_after_round(self):
        """一局结束：记分 → 达标就换下一个队长。返回 True = 还要接着跑下一局。

        用户 2026-11-04 口径：「跑完当前局再换」——所以判定点在一局终点
        （`注册球员 → 回大厅`），不在训练中途；中途达标的也不打断这一局。
        """
        queue = getattr(self, "_queue", None)
        if queue is None:
            return False                       # 单队长模式：跑完就结束
        # 用户 2026-11-11 口径：一局结束读一次「刷到目标分」——
        # 中途取消 = 这局跑完就停，不再开新局。只在结束判定读，不碰开局。
        if not self.config.get("刷到目标分"):
            self.log_info("「刷到目标分」已取消 → 跑完这局就停，不再开新局", notify=True)
            self._queue = None
            return False
        captain = queue.captain
        score = getattr(self, "_run_score", None)
        if score is None:
            self.log_warning(f"整局都没读到队长「{captain}」的评分 → 本局按未达标算")
        met = queue.record(score)
        self.log_info(f"第 {len(queue.runs)} 局收账：{captain} 评分 {score}"
                      f" / 目标 {queue.target} → {'达标' if met else '未达标，再练一局'}"
                      f"（{queue.summary()}）")
        if queue.finished:
            self.log_info(self.queue_finished_message(queue), notify=True)
            return False
        # 用户 2026-11-11 口径：界面派生的单队长队列，局末按**界面当前值**
        # 重算下一局刷谁 —— 中途换队长/改目标分，本局不变，下一局就换。
        # MCP/CLI 注入的队列（`_launch_queue`）走队列自己的推进，不读界面。
        if not getattr(self, "_launch_queue", None):
            current = (self.config.get("指定队长") or "").strip()
            target = int(self.config.get("目标分") or 0)
            if not current:
                self.log_info("「指定队长」已取消 → 跑完这局就停，不再开新局", notify=True)
                self._queue = None
                return False
            if current != queue.captain or target != (queue.target or 0):
                self.log_info(f"界面改了队长/目标分 → 下一局改刷「{current}」"
                              f"（目标 {target or '未填'}）", notify=True)
                self.init_captain_queue()
                queue = getattr(self, "_queue", None)
                if queue is None or queue.empty:
                    return False
                self.switch_captain(queue.captain)
                return True
        self.switch_captain(queue.captain)
        return True

    def queue_finished_message(self, queue):
        """队列跑完时的那句提示（用户看的通知）。

        为什么不再一律说「多角色队列」（2026-11-10 用户反馈：只选了一个队长，
        提示却说多角色队列）：第二栏勾「刷到目标分」时，`captain_queue_spec`
        派生出的是**只有一项**的队列（`队长:目标分`），机制相同但说法该按项数分岔。
        """
        if len(queue.items) == 1:
            item = queue.items[0]
            target = item.get("target")
            if target:
                return f"已刷到目标分 {target} → 提前终止（{queue.summary()}）"
            return f"本队列已跑完 → 提前终止（{queue.summary()}）"
        return f"多角色队列全部达标 → 提前终止（{queue.summary()}）"

    def switch_captain(self, name):
        """换队长（只在内存里换）：清流派 + 清上一局的运行期状态。

        **不写 config**（用户 2026-11-07 口径）：`指定队长` 是界面上的用户设定，
        任务运行时改它会把用户的选择顶掉、还随框架存盘。队列内部的「现在练谁」
        由 `self._queue.cursor` 记账；进度文件 `logs/training_queue.json`
        本来就跟着队列走，不需要污染配置。
        """
        self.reset_style()
        # **不是** True：`_style_pending` 的含义是「流派没定，要去点日历认队长」，
        # 而换队长的流派由 `run_one_round` 开头的 `resolve_style_by_config()` 直接查表定，
        # 置 True 会让新一局开局先去点日历（2026-11-04 写多角色队列时差点踩这个坑）。
        self._style_pending = False
        self._run_score = None
        self.reset_run_state()                 # 含 _register_stage / 卡死计数 / 六维快照
        self.log_info(f"换队长 → {name}（位置 {self._queue.position if self._queue else '?'}"
                      f"｜目标 {self._queue.target if self._queue else '?'}）→ 回大厅开新局",
                      notify=True)

    def snap_event(self, handler_name, texts, frame=None):
        """事件命中时存一帧 + 一份 OCR 摘要，供报告页「事件 ↔ 截图」配对。

        frame 必须是**本拍识别用的那一帧**（run() 里在 handler 之前取好）。
        不传才退回 self.frame —— 那时 handler 已经点过，可能已经是下一页，
        这就是 2026-10-30 用户反馈「截图没截到正确时间点」的原因。

        限流与落点都在 `evidence.EvidenceSink.snap_event`；这里只决定**哪些**
        handler 值得存一帧（业务口径，见 SNAP_HANDLERS）。
        """
        if handler_name not in SNAP_HANDLERS:
            return
        self.evidence.snap_event(
            handler_name, texts,
            self.frame if frame is None else frame,
            clicks=getattr(self, "_tick_clicks", None))

    @staticmethod
    def _signature(texts):
        """界面指纹：只看**文字内容**，不看位置。

        坑（2026-10-26）：之前把坐标也算进指纹，动画/滚动时文字位置抖动 20px 就变，
        结果每一拍都被当成"新界面"。

        现在它**只用于 unknown 存证限流**（同一页面 5 秒最多存一张）。
        卡住判据已经改成「同一天攒了多少」（`day_stuck_step`）：顶栏数字/星标每拍都在闪，
        指纹一直在变，靠它判卡死只会永远判不出来。
        """
        return hash("|".join(sorted(t.name for t in texts)))

    # ---------- 点击流水 ----------

    def click(self, x=-1, y=-1, *args, **kwargs):
        """转发 ok 的 click，同时把每次点击写进 logs/clicks.jsonl。

        为什么要再包一层：ok 自带 click 只在传了 name 时才打坐标日志，而业务代码
        大多不传 name，事后无法复盘「第几秒点了哪里、当时是什么界面」。
        另外卡住判据要**数点击**（`note_day_home_click`）：所有点击都走这一个漏斗，
        handler 里点几次就数几次，不怕漏。
        """
        at = time.time()
        px, py = self._click_point(x, y)
        record = {
            "at": round(at, 3),
            "iso": time.strftime("%H:%M:%S", time.localtime(at)) + f".{int(at % 1 * 1000):03d}",
            "step": self._steps,
            "state": self._last_state or "",
            "name": kwargs.get("name") or "",
            "x": px,
            "y": py,
            "key": kwargs.get("key", "left"),
            "ok": True,
        }
        try:
            return super().click(x, y, *args, **kwargs)
        except Exception as e:                       # noqa: BLE001
            record["ok"] = False
            record["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            if self._click_skipped(at):
                # 闸门拦下的点击（暂停/停止/抢不到前台）根本没发出去：
                # 既不算「最近一次点击」（否则看门狗会以为点过），也不计入当天点击数。
                # 2026-11-10 19:10 实跑：暂停中点了「安心按摩」，日志里有 left_click，
                # 实际一下都没发，看门狗照样接管 3 次后停任务。
                record["ok"] = False
                record["skipped"] = True
            else:
                # 点击**完成**时刻才算「最近一次点击」：用户抢鼠标时 interaction 会在
                # click 里面暂停几秒，用开始时刻计时会让统计把这段让路算成空档。
                self._last_click_at = time.time()
                # 卡住判据②：同一天点击数 +1（`stuck_takeover` 用它判「一天点了多少下」）
                self.note_click()
            self._journal_click(record)
            # 记进本拍点击列表，snap_event 会把它写进事件 json（报告「内容」列）
            tick_clicks = getattr(self, "_tick_clicks", None)
            if tick_clicks is not None:
                tick_clicks.append({"name": record["name"], "x": px, "y": py})

    def _click_skipped(self, at):
        """这次 click 是不是被闸门拦下了（暂停/停止/抢不到前台），没真正发出去。"""
        interaction = getattr(self, "_gate_interaction", None)
        return getattr(interaction, "last_skip_at", 0.0) >= at

    @staticmethod
    def _click_point(x, y):
        """把 Box / Box 列表 / 相对坐标换算成可读的像素点，只用于存证。"""
        if isinstance(x, (list, tuple)):
            x = x[0] if x else -1
        if hasattr(x, "width") and hasattr(x, "height"):
            return int(x.x + x.width / 2), int(x.y + x.height / 2)
        return x, y

    # ---------- 卡住就立刻接管（2026-11-04 用户口径：两条判据都要） ----------
    #
    # 用户原话：「两个方法都要，点击 3 秒无响应要立刻接管，同一天点击超过 100 下要
    #            立刻接管，2000 步的限制不要了。」
    #
    #   判据①「点击无响应判定(秒)」=3：界面指纹超过 3 秒没变、而且这段时间里**点过**
    #        → 点了没反应（不是干等动画）→ 立刻接管；
    #   判据②「单日最大点击数」=100：同一天累计点击超过 100 下还没换天 → 立刻接管。
    #        **日期一更新就归零**（`day_counter_step`），正常的一天碰不到。
    #
    # 「接管」= 存证 logs/debug/ + 清掉当天拦路标记 + 点屏幕中央继续推进，**不**停任务；
    # 同一天接管 3 次还推不动才停（防无限空点）。
    # 声明表 `handlers/__init__.py: DAY_BUDGET_EXEMPT`（比赛快进 / 注册球员 / 赛前准备）
    # 两条都不算 —— 那些页合法连跑几百拍、也不换天。

    def day_counter_step(self, texts=None):
        """每拍更新「同一天」计数器（用户口径：**日期更新以后计数器重置**）。

        计数器都挂在当前日期键 `_day_key` 上：
          - `_day_clicks`：这一天点了多少下（判据②的输入，在 `note_click` 里累加）；
          - `_day_fixes`：这一天接管过几次（到 3 就停）。

        只认**读到新日期**才归零；看不到日期的帧不动日期键（分不清有没有换天就不猜），
        但点击照常累计 —— 否则停在无日期页面上空转就没人管了（2026-11-04 用户口径）。
        """
        week, day = self.current_day(texts)
        key = (week, day) if (week is not None and day is not None) else None
        if key is not None and key != getattr(self, "_day_key", None):
            prev = getattr(self, "_day_key", None)
            if prev is not None:
                self.log_info(
                    f"日期更新 {prev} → {key}：卡住计数器归零"
                    f"（上一天点击 {getattr(self, '_day_clicks', 0)} 下 / "
                    f"接管 {getattr(self, '_day_fixes', 0)} 次）")
            self._day_key = key
            self._day_clicks = 0
            self._day_fixes = 0
        return key

    def _stuck_exempt(self):
        """这一拍所在页的 handler 是否声明了「合法长跑、不算卡住」。

        声明表在 `handlers/__init__.py: DAY_BUDGET_EXEMPT`（谁长跑谁声明），
        flow 这边不认识任何具体 handler 名字 —— 和 `STALL_LIMITS` 同一套做法。

        `ImportError` 兜底：`reload_tasks` **不重载 `handlers/` 子包**（见
        `docs/PROMPT_PLAYBOOK.md`），热更时旧模块里可能没有这张表 —— 那就按
        「没有豁免」处理并告警一次，别把任务直接炸掉。
        """
        try:
            from src.tasks.training.handlers import DAY_BUDGET_EXEMPT  # 局部导入，避开环
        except ImportError:
            if not getattr(self, "_day_exempt_warned", False):
                self._day_exempt_warned = True
                self.log_warning("handlers.DAY_BUDGET_EXEMPT 不存在（热更未重载 handlers/）"
                                 "→ 长跑页也会被判卡住，建议重启进程")
            return False
        return getattr(self, "_last_state", None) in DAY_BUDGET_EXEMPT

    def reset_day_home(self, key=None):
        """同一天计数器归零（新的一局 / 换了一天 / 接管之后）。"""
        self._day_key = key
        self._day_clicks = 0
        self._day_fixes = 0

    def note_click(self):
        """`click()` 每点一下都会调这里：同一天点击数 +1（判据②的输入）。"""
        self._day_clicks = getattr(self, "_day_clicks", 0) + 1

    def stuck_takeover(self, texts, frame=None):
        """卡住 → 立刻接管。返回 True = 这一拍已处理（主循环 continue）。

        两条判据任一命中就动手（用户 2026-11-04 口径）：
          ① 界面指纹超过 `点击无响应判定(秒)` 没变化，且这段时间里点过（点了没反应）；
          ② 同一天累计点击超过 `单日最大点击数`，日期还没更新。

        接管三件事：存证 → 清掉当天拦路标记 → 点屏幕中央继续；同一天 3 次才停。
        """
        if self._stuck_exempt():
            return False
        key = getattr(self, "_day_key", None)
        tag, why = None, ""
        noresp = float(self.config.get("点击无响应判定(秒)") or 3)
        click_limit = max(1, int(self.config.get("单日最大点击数") or 100))
        changed_at = getattr(self, "_sig_changed_at", 0.0)
        clicked_at = getattr(self, "_last_click_at", 0.0)
        idle = time.time() - changed_at
        clicks = getattr(self, "_day_clicks", 0)

        if changed_at and clicked_at >= changed_at and idle >= noresp:
            tag = f"noresp{int(noresp)}s"
            why = f"点了之后界面 {idle:.1f} 秒没变化（上次动作：{self._last_state}）"
        elif clicks > click_limit:
            tag = f"clicks{click_limit}"
            where = f"第{key[0]}周 星期{key[1]}" if key else "当前页看不到日期"
            why = f"{where}：这一天已经点了 {clicks} 下，日期还没更新"

        if tag is None:
            return False
        self._day_clicks = 0                       # 先归零，接管这一下不算进新一轮
        self._day_fixes = getattr(self, "_day_fixes", 0) + 1
        self._sig_changed_at = time.time()
        self.dump_debug(texts, tag, frame)
        self.log_warning(
            f"{why} → 立刻接管：已存证（本局 debug/），清掉当天拦路标记后点屏幕中央继续"
            f"（第 {self._day_fixes}/3 次接管）", notify=True)
        info = getattr(self, "info", None)
        if isinstance(info, dict):
            who = f"第{key[0]}周 星期{key[1]}" if key else "无日期页"
            info["stuck"] = f"{who} · 已接管 {self._day_fixes} 次（{tag}）"
        if self._day_fixes >= 3:
            self._abort_reason = f"{why}；同一天接管 3 次仍然推不动（已存证（本局 debug/））"
            self.log_error(self._abort_reason, notify=True)
            return True                            # 下一拍 run() 看到 _abort_reason 就退出
        self._clear_day_home_brakes()
        if not self._stuck_advance(texts):
            self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=0.3)
        return True

    def _stuck_advance(self, texts):
        """卡住接管时先点「推进型」按钮；点不到返回 False（回退点屏幕中央）。

        只点这三类，**永远不点「确定」**：
          1. `下一步` / `决定`（`labels.BUTTONS`，全等匹配）；
          2. `labels.TAP_TO_CONTINUE_TEXTS` 的过场提示（全等，不用包含匹配）。

        为什么不点「确定」：扭蛋/游戏厅的选卡页整页没有「购买」字样，页面上
        只有「确定」—— 一点就是花零用钱，而且绕过专属 handler 的品质/精力判断。
        两段式页面（选卡 → 确定）推不动，就让 3 次接管后的停任务兜底。

        2026-11-08 事故：接管原本无条件点屏幕中央，而炒股选股页中央就是中间
        那张卡 → 三次接管全白费、一分钟后停任务（`logs/debug/1108-231656_noresp3s`）。
        """
        for key in STUCK_ADVANCE_BUTTONS:
            if self.click_button(key, texts=texts, after_sleep=0.35, must=False):
                self.log_warning(
                    f"卡住接管：点「{labels.BUTTONS[key][0]}」推进（不再点中央）")
                return True
        for text in labels.TAP_TO_CONTINUE_TEXTS:
            target = self.exact_text(texts, (text,))
            if target is not None:
                self.click_box(target, after_sleep=0.35)
                self.log_warning(f"卡住接管：点「{text}」过场提示推进（不再点中央）")
                return True
        return False


    def _clear_day_home_brakes(self):
        """干预时的「纠错」：清掉会**把这一天堵死**的当天标记。

        这些标记都是「做过了就不再试」的语义，一旦它们在没成功的路径上被置位，
        这一天就彻底推不动了（商店点了两次没开、出行标记写脏、科目选过…）。
        清掉以后同一天的决策会从头再走一遍，**进程就继续往前了**。

        不动的东西：`_register_stage`（一局终点的账，清了下一次会重复注册）、
        `_shop_day`（今天真的进过店，清了会重复买东西）、`_style*`（流派是队长的属性）。
        """
        self._no_change = 0
        self._sig_after_click = None
        self._stuck_rounds = 0
        self._loop_handler = None
        self._loop_count = 0
        # 商店入口的「今天试过几次」（late_shop_step）
        self._shop_try_key = None
        self._shop_try_count = 0
        self._shop_at = 0.0
        # 训练日主界面的「等出行按钮」计数 / 头一拍不决策（handlers/__init__.py: LEAVE_RESETS）
        self._hub_ticks = 0
        self._outing_wait = 0
        self._outing_clicks = 0
        # 笔记 / 科目 / 转盘这些「已经做过」的瞬时标记
        self._note_auto_done = False
        self._note_next_tries = 0
        self._picked_subject = False
        self._picked_index = 0
        self._wheel_clicked_at = 0.0
        # 今天去过的地点：卡住的很可能就是「记成去过了、其实没进去」
        state = self.day_state()
        state["visited"] = set()
        state["current_step"] = None
        self.log_info("干预纠错：已清掉当天拦路标记（商店试次/出行/笔记/科目/已去地点）")

    def _journal_click(self, record):
        """点击流水落盘（单点归口：`evidence.EvidenceSink.click`，每行带 round）。"""
        self.evidence.click(record)

    def dump_debug(self, texts, tag, frame=None):
        """点不动的时候存一份「调试包」：截图 + OCR + 最近动作 + 线程。

        用户口径（2026-10-26）：连续点 3 次界面没变化就要开始 debug。
        产物在当前局目录的 `debug/<时间>_<tag>/`（落点见 `evidence.EvidenceSink`）。
        frame 同样要传**本拍识别那一帧**，否则存的是点击后的画面。
        """
        summary = (f"step={self._steps} last={self._last_state} "
                   f"no_change={self._no_change} rounds={self._stuck_rounds} "
                   f"day={getattr(self, '_day_key', None)}"
                   f" clicks={getattr(self, '_day_clicks', 0)}"
                   f" fixes={getattr(self, '_day_fixes', 0)}")
        folder = self.evidence.dump_debug(
            texts, tag,
            self.frame if frame is None else frame,
            summary=summary, actions=list(self._recent_actions))
        if folder:
            self.log_info(f"调试包已保存：{folder}")

    def _reset_handler_leave_states(self, hit_name):
        """命中 `hit_name` 后，把**其它** handler 声明的瞬时状态清回默认值。

        声明表在 `handlers/__init__.py: LEAVE_RESETS` —— 谁的状态谁声明，
        这里不认识任何具体 handler 名字，所以**加 handler 不用改本文件**。
        """
        from src.tasks.training.handlers import LEAVE_RESETS   # 局部导入，避开导入环

        for owner, defaults in LEAVE_RESETS.items():
            if owner == hit_name:
                continue
            for attr, default in defaults.items():
                setattr(self, attr, default() if callable(default) else default)

    def _note_page(self, texts):
        """旁路页面分类：记录「这一帧是什么页面」，并把 PageID 返回给调用方。

        2026-11-13 全局方案阶段 1：先把 PageID 和实际命中的 handler 对照，
        差异清零后再把 `_handle_any` 切成「先分类、再分派」。
        2026-11-14 阶段 2：已经切了 —— 调用方用返回值做**路由**，
        分类器只当加速器，路由失败照旧全量遍历。

        失败时只记 debug，绝不影响主流程。
        """
        from src.tasks.training.pages import classify_page      # 局部导入，避开导入环

        try:
            page, candidates = classify_page(self, texts)
        except Exception as e:                                  # noqa: BLE001
            self.log_debug(f"页面分类异常：{type(e).__name__}: {e}")
            return None
        prev = getattr(self, "_page_id", None)
        self._page_id = page
        if page != prev:
            self.log_info(f"页面分类：{prev or '?'} → {page or 'unknown'}"
                          f"（候选 {list(candidates)}）")
        elif len(candidates) > 1 and candidates != getattr(self, "_page_conflict", ()):
            self.log_warning(f"页面分类冲突：{list(candidates)} → 暂取 {page}")
        self._page_conflict = candidates
        return page

    def _handle_any(self, texts, frame=None):
        page = self._note_page(texts)
        # 大厅子菜单（强化/招募/卡池…）：玩家自己点进去的，任务一律不动，
        # 也不要走「不认识的界面 → 点屏幕中央」兜底（2026-10-28 用户反馈：
        # 强化页被当成三选一点升级卡、招募页被 story_arrow 点了一下）。
        if self.is_lobby_subpage(texts):
            return True
        # 阶段 2：先分类、再分派（2026-11-14）。
        # 分类器认出页面 → 先试这个页面对应的 handler；**跑不到就照旧全量遍历**。
        # 顺序遍历里沉淀了踩坑的教训（「共用『下一步/确定』的必须排在专属
        # handler 之后」），所以分类器只当**加速器**，绝不排除任何 handler ——
        # 最坏情况 = 现在的行为（多跑几个 handler 的判据而已）。
        if page is not None and self.config.get("页面分类路由"):
            for first in self._routed_handlers(page):
                if self._try_handler(first, texts, frame):
                    return True
        for handler in self.HANDLERS:
            if self._try_handler(handler, texts, frame):
                return True
        return False

    def _routed_handlers(self, page):
        """这个页面对应的 handler 列表（按顺序表里的先后；没有就空）。"""
        from src.tasks.training import page_handlers   # 局部导入，避开导入环

        names = page_handlers.handlers_for(page)
        if not names:
            return []
        by_name = {h.__name__: h for h in self.HANDLERS}
        return [by_name[n] for n in names if n in by_name]

    def _try_handler(self, handler, texts, frame):
        """跑一个 handler；命中了就把这一拍记成它的动作。"""
        try:
            if not handler(self, texts):
                return False
        except Exception as e:
            self.log_error(f"{handler.__name__} 异常: {type(e).__name__}: {e}")
            return True  # 吞掉这一拍，避免同一异常反复刷屏
        self._last_state = handler.__name__
        self._ui_event(handler, texts)
        self.snap_event(handler.__name__, texts, frame)
        self._recent_actions.append(f"step{self._steps} {handler.__name__}")
        # 记录「点击之后应该看到的那一帧」的指纹，下一拍对比是否变化
        self._sig_after_click = self._signature(texts)
        self.log_debug(f"命中 {handler.__name__}（第 {self._steps} 步）")
        # 各 handler 的瞬时状态由它自己声明（handlers/__init__.py: LEAVE_RESETS），
        # 命中别的界面时自动清零。这里**不再用 handler 名字字符串判断** ——
        # 那种写法每加一个 handler 都要回来改本函数（2026-11-01 重构）。
        self._reset_handler_leave_states(handler.__name__)
        # ---- 同一个 handler 连续命中太多次 = 这一页怎么点都推不动 ----
        # 声明表在 handlers/__init__.py: STALL_LIMITS（谁需要谁声明）。
        # 「点 3 次没变化」那套看门狗靠**界面指纹不变**，而顶栏数字/星标
        # 每拍都在闪时指纹每拍都变 → 永远不触发（2026-10-04 实跑：
        # 「选择憋气难度」页把「确定」点了 1019 次、烧掉 1200 步）。
        if handler.__name__ == getattr(self, "_loop_handler", None):
            self._loop_count = getattr(self, "_loop_count", 0) + 1
        else:
            self._loop_handler, self._loop_count = handler.__name__, 1
        limit = _stall_limits().get(handler.__name__)
        if limit and self._loop_count >= limit:
            self.dump_debug(texts, f"loop{self._loop_count}", frame)
            self._abort_reason = (
                f"{handler.__name__} 连续 {self._loop_count} 拍命中同一页"
                "却推不动（已存证（本局 debug/））")
            self.log_error(self._abort_reason, notify=True)
        return True

    @property
    def no_captain_mode(self):
        """无队长模式：这次 run 没有「指定队长」（队列参数也算）且开关打开。

        用户 2026-10-31 口径：取消勾选队长 = 不接开局，只跑已经手动开好的局。
        开关 `无队长不接开局` 默认 True；回归桩（tools/check_handlers.py）关掉它，
        这样测试仍然按旧的「自动选第一个」流程跑。
        """
        if not self.config.get("无队长不接开局"):
            return False
        return not self.configured_captain()

    def stop_for_no_captain(self, stage):
        """无队长模式下遇到开局页面：不动作，直接让 run() 停下。"""
        self._abort_reason = f"无队长模式：不接开局（遇到「{stage}」）"
        self.log_warning(
            f"无队长模式：只跑已有局，当前在「{stage}」→ 停止任务。"
            "勾选队长后才会自动开局（启程/选人/笔记/器材）。",
            notify=True)
        return True

    def _ui_event(self, handler, texts=None):
        """把本拍命中的 handler 写进 info，供「监控」页显示。

        只写三个键：action（当前动作）、style（流派）、timeline（最近动作）。
        不参与业务逻辑，写失败也不影响流程。

        时间线的 `detail` 是**本拍识别到的文字**（`summary_texts`），
        看得到「当时屏幕上有什么」，比只写 handler 名好复盘；
        步数/全名另存 `step` / `handler`（UI 暂不显示，排障时用）。
        """
        try:
            info = getattr(self, "info", None)
            if not isinstance(info, dict):
                return
            module = handler.__module__.rsplit(".", 1)[-1]
            area = UI_AREAS.get(module, "流程")
            name = handler.__name__.removeprefix("handle_")
            info["action"] = f"{area} · {name}"
            # 用 self.style：没识别队长时它自己会兜底成「训练流」，
            # 这才是脚本实际在跑的流派（旧版写「未定」，监控页就看不到真实值）
            info["style"] = self.style
            info["style_known"] = self.style_known
            events = info.get("timeline")
            if not isinstance(events, list):
                events = []
            events.insert(0, {
                "time": time.strftime("%H:%M:%S"),
                "event": area,
                "detail": summary_texts(texts),
                "result": "成功",
                "step": self._steps,
                "handler": name,
            })
            del events[UI_EVENT_LIMIT:]
            info["timeline"] = events
        except Exception as e:                       # noqa: BLE001
            self.log_debug(f"写入监控信息失败: {type(e).__name__}: {e}")

    def arcade_still_playing(self, texts):
        """游戏厅小游戏还在播动画吗？

        买完小游戏后画面只有顶栏、没有按钮（角色站在球场中间那种）。
        用户 2026-10-29 口径：小游戏会自己打完，**一直等到动画结束**就行。
        这里用「有没有重新出现已知界面标记」判断，不按时间猜。
        """
        if not getattr(self, "_arcade_wait_since", 0.0):
            return False
        names = "".join(t.name for t in texts)
        return not any(k in names for k in ARCADE_WAIT_MARKS)

    @property
    def style(self):
        """当前流派 —— 由队长决定（见 docs/RULES.md 第 0 节）。

        快速接手流程：进训练时这个变量是空的 → 点一次日历解析队长流派。
        全自动流程（另一条流程，尚未实装）：开局自己把流派写进 `_style`。
        还没定下来时按「训练流」跑（最保守）。
        """
        return getattr(self, "_style", None) or "训练流"

    @property
    def style_known(self):
        """流派定了没有？没定就要去开日历认队长。"""
        return bool(getattr(self, "_style", None))

    def reset_style(self):
        """清空流派（新的一局 / 接管一局）：等日历解析队长。"""
        self._style = None
        self._style_name = None
        # 状态机和计数器必须一起清：ok-script 复用同一个 Task 实例，
        # 上一局停在「抓名字」且 tries 已到 3，新局会直接进「抓名字」→
        # 一次读不到就放弃（2026-11-10 19:10 实跑：开新局 0.07 秒就报「未能识别到队长」）。
        self._style_probe_state = None
        self._style_return_tries = 0
        self._style_calendar_tries = 0

    def configured_captain(self):
        """这次 run 的「指定队长」：本次队列优先，否则界面配置。

        队列（`_launch_queue` 注入或配置派生）当前队到的队长**就是**这次的
        「指定队长」——流派、选人页都按它走；界面配置只在没队列时生效。
        只读，绝不写回 config（用户 2026-11-07 口径）。
        """
        queue = getattr(self, "_queue", None)
        if queue is not None and queue.captain:
            return queue.captain
        return (self.config.get("指定队长") or "").strip()

    def apply_configured_captain(self):
        """「指定队长」（队列参数或界面配置）→ 直接定流派，**不点日历**。

        2026-10-30 用户反馈「星期二为什么开日历了，没这个设置吧」：
        旧代码的「指定队长」只用于在选择队长页挑卡（handlers/entry.py），
        从没写进 `_style`；而 `_style` 的唯一赋值点是日历解析
        （handlers/training.py:handle_training_plan）。于是只要队长不是从日历读的，
        第一个训练日就必然触发 handle_style_probe 去点左上角日历，
        跟「配置里填了队长就开局有流派」的注释完全不一致。

        现在：填了队长就查表定流派；查不到（SR/R 或 OCR 名不匹配）才回落到日历。
        """
        name = self.configured_captain()
        if not name:
            return False
        style = self.style_for_name(name)
        if not style:
            self.log_warning(
                f"配置的队长「{name}」不在角色表（docs/roster）→ 仍按日历识别流派")
            return False
        self._style = style
        self._style_name = name
        self.log_info(f"流派：{style}（配置指定队长「{name}」，跳过日历）")
        return True

    def weekend_priority(self, day=None, st=None, texts=None):
        """今天该去的地点（有序）。

        **判断已搬到 `training/places/map.py`**（地图 = 决策模块）：
        训练流按缺口选动作、扭蛋流的「先抽扭蛋再打工」、两个开关过滤都在那里。
        这里保留旧名，让现有调用点不用改。
        """
        return self.today_places(day, st, texts)

    def rest_threshold(self, day=None):
        """今天「精力低于多少就休息」。

        用户 2026-11-04：**扭蛋流 / 小游戏流**在周四有硬阈值 **60**
        （`rest.thursday_threshold_by_style`，取代旧的「周二 65」）；**其它流派
        （训练流 / 商店流）周四没有特殊限制**，只遵守策略参数
        `rest.stamina_threshold`（`strategy/params.json`）。
        周二现在没有任何特殊阈值（旧的周二 65 / 周二 60 已删除）。
        有硬阈值时取 max(策略参数, 硬阈值)，参数调高时以参数为准。
        """
        base = int(params.get("rest.stamina_threshold"))
        if day == "四":
            hard = params.get("rest.thursday_threshold_by_style").get(self.style)
            if hard is not None:
                return max(base, hard)
        return base
