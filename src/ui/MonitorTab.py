"""监控页：把训练任务的实时状态放到一屏里。

职责边界：
- 本文件只做「取数据 + 定时刷新 + 热重载」，界面结构全在 monitor_view.py；
- 布局来自 configs/monitor_layout.json，样式来自 assets/ui/monitor.*.qss，
  两者都在运行时监听 mtime，改完存盘即生效，不用重启；
- 不启动/不干扰任务逻辑，只在用户点按钮时调用框架已有的 executor 接口。
"""

from __future__ import annotations

import json
import os
import time

from PySide6.QtCore import QTimer
from qfluentwidgets import FluentIcon, isDarkTheme

from ok import Logger, og
from ok.core.events import communicate
from ok.ui.qt.widget.CustomTab import CustomTab
from ok.util.file import get_relative_path

from src.ui.monitor_view import MISSING, MonitorActions, MonitorView

logger = Logger.get_logger(__name__)

LAYOUT_PATH = "configs/monitor_layout.json"
QSS_DIR = os.path.join("assets", "ui")
# 启动后自动切到「监控」页（想回到「截图方式」页就改成 False）
START_ON_MONITOR = True
START_RETRY_MS = 100
START_RETRY_MAX = 100  # 最多等 10 秒，主窗口还没显示就不抢了
# 技能品质权重（docs/score-model/README.md：彩 75 / 金 50 / 白 25）
SKILL_WEIGHTS = {"color": 75, "gold": 50, "white": 25}
SKILL_KEYS = {"color": "技能-彩色", "gold": "技能-金色", "white": "技能-白色"}
# 六维必须**齐 6 项**才算「分数预测」（和 training/parsers.ATTR_KEYS 一致）。
# 面板被说明气泡挡住 / 这一帧漏读一项时，拿 4~5 项求和会给出偏低的分，
# 用户看到的就是「分数自己乱跳」——宁可不显示，也不显示错的。
ATTR_KEYS = 6

DEFAULT_LAYOUT = {
    "version": 1,
    "sections": [
        {"type": "connection", "height": 54,
         "title": "{device_title}", "meta": "{device_meta}",
         "show_run": True, "run_text": "开始", "stop_text": "暂停"},
        {"type": "captain", "height": 54,
         "label": "指定队长",
         "hint": "取消勾选 = 不接开局，只跑已有局",
         "show_quick_match": True, "show_auto_training": True,
         "start_text": "开始", "pause_text": "暂停"},
        {"type": "hero", "height": 204,
         "kicker": "当前进度",
         "title": "第 {week} 周 {day}",
         "fallback": "等待开始",
         "right_ratio": 0.5,
         "stats": [
             {"label": "精力", "value": "{stamina}", "suffix": "/{stamina_max}",
              "bar": True, "bar_key": "stamina_pct", "bar_width": 150},
             {"label": "零用钱", "value": "{coin}"},
             {"label": "流派", "value": "{style}"},
             {"label": "已运行", "value": "{uptime}"},
         ]},
        {"type": "cards", "height": 126, "items": [
            {"id": "today", "title": "今日计划", "rows": [
                ["比赛倒计时", "{match_countdown}"],
                ["周末去处", "{weekend_plan}"],
                ["野餐阈值", "精力 < {picnic_threshold}"],
            ]},
            {"id": "shop", "title": "商店", "rows": [
                ["商店购买策略", "{buy_strategy}"],
                ["今日已逛", "{shop_today}"],
            ]},
            {"id": "risk", "title": "风险", "rows": [
                ["当前失败率", "{fail_rate}"],
                ["失败率上限", "{fail_limit}"],
                ["卡住干预", "{stuck}"],
            ]},
        ]},
        {"type": "timeline", "title": "最近动作", "max_rows": 8,
         "time_width": 62, "event_width": 80},
    ],
}


class _Actions(MonitorActions):
    def __init__(self, tab: "MonitorTab"):
        self.tab = tab

    def refresh_connection(self):
        self.tab.refresh()

    def open_logs(self):
        self.tab.open_logs()

    def set_captain(self, name, memory=""):
        self.tab.set_captain(name, memory)

    def toggle_capture(self):
        self.tab.toggle_capture()

    def toggle_connection(self):
        self.tab.toggle_connection()

    def set_skill(self, key, value):
        self.tab.set_skill(key, value)

    def set_quick_match(self, enabled):
        self.tab.set_quick_match(enabled)

    def set_score_stop(self, enabled):
        self.tab.set_score_stop(enabled)

    def set_score_target(self, target):
        self.tab.set_score_target(target)

    def set_stamina_allowed(self, allowed):
        self.tab.set_stamina_allowed(allowed)

    def toggle_auto_training(self):
        self.tab.toggle_auto_training()


class MonitorTab(CustomTab):
    """左侧导航的「监控」页。"""

    @property
    def name(self):
        return "监控"

    @property
    def add_after_default_tabs(self):
        # False = 插在「截图方式」之后、任务/触发之前；启动时还会自动切过来（见 _select_on_startup）。
        return False

    def __init__(self):
        super().__init__()
        self.icon = FluentIcon.SPEED_HIGH
        self.setObjectName("MonitorTabView")
        self._layout_data = self._load_layout()
        self._layout_mtime = self._mtime(self._layout_path())
        self._qss_mtime = 0.0
        self._view: MonitorView | None = None
        # 监控页「最后一次读到的值」缓存（只用于展示，见 `_sticky`）：
        # 某一拍 OCR 没抓到 → 保持上一拍，不闪成「—」。
        self._last_seen: dict = {}

        self._build_view()
        self._apply_style()
        self._bind_theme()

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._started_selected = False
        if START_ON_MONITOR:
            QTimer.singleShot(0, self._select_on_startup)

    # ---------- 启动默认页 ----------

    def _select_on_startup(self, tries: int = 0):
        """启动后把左侧导航切到「监控」页。

        构造期间主窗口还没赋给 og.main_window、也还没显示，所以轮询等到
        showEvent 之后（win.shown）再切，避免和导航栏的初始化抢。
        """
        if self._started_selected:
            return
        win = getattr(og, "main_window", None)
        if win is None or not getattr(win, "shown", False):
            if tries < START_RETRY_MAX:
                QTimer.singleShot(START_RETRY_MS, lambda: self._select_on_startup(tries + 1))
            return
        self._started_selected = True
        try:
            win.switchTo(self)
            logger.info("启动默认页：监控")
        except Exception as e:  # noqa: BLE001
            logger.error(f"切换到监控页失败: {e}")

    # ---------- 布局 / 样式文件 ----------

    @staticmethod
    def _layout_path() -> str:
        return get_relative_path(LAYOUT_PATH)

    @staticmethod
    def _qss_path(dark: bool) -> str:
        name = "monitor.dark.qss" if dark else "monitor.light.qss"
        return get_relative_path(os.path.join(QSS_DIR, name))

    @staticmethod
    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _load_layout(self) -> dict:
        path = self._layout_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            self._write_default_layout(path)
            return DEFAULT_LAYOUT
        except Exception as e:  # noqa: BLE001
            logger.error(f"monitor_layout.json 解析失败，用内置默认布局: {e}")
            return DEFAULT_LAYOUT

    def _write_default_layout(self, path: str):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_LAYOUT, f, ensure_ascii=False, indent=2)
            logger.info(f"已生成默认布局文件 {path}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"写默认布局失败 {path}: {e}")

    def _build_view(self):
        if self._view is not None:
            self.vBoxLayout.removeWidget(self._view)
            self._view.deleteLater()
        task = self._task()
        config = dict(getattr(task, "config", {}) or {}) if task else {}
        # 当前队长要传进去：它的流派就算被 `CAPTAIN_DROPDOWN_STYLES` 隐藏了，
        # 下拉里也得留着这个人，否则回填不到 / 一勾就被换成训练流。
        # 下拉记忆（`队长下拉选择`）同理：没勾选时显示的是它。
        captain = (config.get("指定队长") or "").strip()
        memory = (config.get("队长下拉选择") or "").strip()
        self._view = MonitorView(self._layout_data, _Actions(self),
                                  captains=self._captains(captain or memory))
        self.add_widget(self._view)
        self._apply_style()
        self._update_data()

    def _apply_style(self):
        path = self._qss_path(isDarkTheme())
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.setStyleSheet(f.read())
            self._qss_mtime = self._mtime(path)
        except OSError:
            logger.warning(f"样式文件不存在，使用框架默认样式: {path}")

    def _bind_theme(self):
        from qfluentwidgets import qconfig
        qconfig.themeChanged.connect(lambda *_: self._apply_style())

    # ---------- 刷新 ----------

    def _tick(self):
        if self._layout_path_changed():
            self._layout_data = self._load_layout()
            self._layout_mtime = self._mtime(self._layout_path())
            self._build_view()
            self._apply_style()
            logger.info("检测到布局文件变更，已重建监控页")
        elif self._mtime(self._qss_path(isDarkTheme())) != self._qss_mtime:
            self._apply_style()
            logger.info("检测到样式文件变更，已重新加载")
        self._update_data()

    def _layout_path_changed(self) -> bool:
        return self._mtime(self._layout_path()) != self._layout_mtime

    def _update_data(self):
        if self._view is None:
            return
        self._view.update_data(self._collect())

    # ---------- 数据 ----------

    def _task(self):
        try:
            from src.tasks.TrainingTask import TrainingTask
            return self.executor.get_task_by_class(TrainingTask) if self.executor else None
        except Exception:  # noqa: BLE001
            return None

    def _sticky(self, key, value):
        """取「最后一次读到的值」：这一拍为 None 就返回上一拍的值。

        为什么需要（2026-11-09 用户反馈）：监控页第三栏左半边（精力 / 零用钱 /
        六维 / 分数）每隔几拍就闪一下「—」。`info` 是任务每拍覆盖写的 UI 通道，
        OCR 漏读一拍、或 `top_bar()` 被局部 texts 调用时就会短暂变 None；
        直接渲染就是「好值 → — → 好值」。

        只用于**展示**：业务判据读的是 `top_bar()` 的返回值，None 语义不变。
        新一局 `info` 会被 `info_clear()` 清空，`_collect` 里同时清掉这个缓存，
        免得新角色开局先显示上一个人的数。
        """
        cache = getattr(self, "_last_seen", None)
        if cache is None:
            cache = self._last_seen = {}
        if value is not None:
            cache[key] = value
            return value
        return cache.get(key)

    def _collect(self) -> dict:
        task = self._task()
        info = dict(getattr(task, "info", {}) or {}) if task else {}
        # 新一局/重载：框架 `enable()` 会 info_clear() 把 info 清空 →
        # 上一局的展示缓存一并丢掉，否则新角色会先显示上一个人的数值。
        if not info:
            self._last_seen = {}
        config = dict(getattr(task, "config", {}) or {}) if task else {}
        attrs = self._sticky("attrs", dict(info.get("attrs") or {}) or None) or {}
        week = self._sticky("week", info.get("week"))
        day = self._sticky("day", info.get("day"))
        stamina = self._sticky("stamina", info.get("stamina"))
        stamina_max = self._sticky("stamina_max", info.get("stamina_max"))
        coin = self._sticky("coin", info.get("coin"))
        # 配置里是开局已有的技能数；info["skill_gained"] 是本局三选一拿到的，相加才是当前总数
        gained = info.get("skill_gained") if isinstance(info.get("skill_gained"), dict) else {}
        skills = {k: int(config.get(key, 0) or 0) + int(gained.get(k, 0) or 0)
                  for k, key in SKILL_KEYS.items()}
        # 评分 = 六维总和 + 75×彩 + 50×金 + 25×白（每角色开局恒 1 彩 + 1 白，见 docs/score-model）
        # **六维不齐就不算**（2026-11-04）：面板被挡/OCR 漏一项时，拿 4 项求和会给出
        # 一个偏低的分（用户看到「突然 9k / 1w4」的另一半原因），不如显示「—」。
        total_attr = sum(v for v in attrs.values() if isinstance(v, (int, float)))
        score = None
        if len(attrs) >= ATTR_KEYS and total_attr:
            score = int(round(total_attr)) + sum(
                SKILL_WEIGHTS[k] * v for k, v in skills.items())

        pct = 0
        if isinstance(stamina, (int, float)) and isinstance(stamina_max, (int, float)) and stamina_max:
            pct = round(stamina / stamina_max * 100)

        data = {
            "state": self._state(task),
            # 留 None 而不是「—」，这样 hero 标题能命中 fallback（等待开始）。
            # 这里的 None 已经是 `_sticky` 过滤后的：读不到时沿用上一拍的值。
            "week": week,
            "day": day,
            "stamina": stamina if stamina is not None else MISSING,
            "stamina_max": stamina_max if stamina_max is not None else MISSING,
            "stamina_pct": pct,
            "coin": coin if coin is not None else MISSING,
            "style": info.get("style") or self._style_from_config(config),
            "action": info.get("action") or "空闲",
            "uptime": _fmt_uptime(task),
            "task_hint": info.get("task_hint") or "自动训练",
            "match_countdown": info.get("match_countdown") or MISSING,
            "weekend_plan": info.get("weekend_plan") or MISSING,
            "picnic_threshold": config.get("周末野餐阈值", MISSING),
            "buy_strategy": config.get("商店购买策略", MISSING),
            "shop_today": info.get("shop_today") or "否",
            "fail_rate": info.get("fail_rate") or info.get("当前失败率") or MISSING,
            "captain": (config.get("指定队长") or "").strip(),
            # 下拉框记忆：勾选框没勾时也要把「上次选过谁」回填到下拉，
            # 否则重启后一勾就又退回列表第一个（2026-11-09 用户口径：
            # 队长下拉菜单也要落盘、启动装载）。
            "captain_memory": (config.get("队长下拉选择") or "").strip(),
            # 第一栏按钮的**唯一**判据（2026-11-09 改成对齐框架 StartCard）：
            # 执行器是否暂停。A（起始页 F9）就只看这一条。
            "paused": self._executor_paused(),
            # 第二栏按钮的状态：自动训练任务在跑 / 在队列里等
            "training_running": self._running_of(task),
            # 第二栏按钮文案要区分「真在跑」和「只是排上队了」：
            # 只有 running 才算在跑；enabled 但没 running = 还没开始。
            "training_active": self._active_of(task),
            # 任务是否处于框架的 task.pause() 挂起态（enabled=True + paused=True）：
            # 暂停态下第二栏按钮显示【开始】（点击 = unpause 恢复），
            # 对齐框架 TaskCard.update_buttons 的「enabled+paused → Start(Resume)」。
            "training_paused": (bool(getattr(task, "enabled", False))
                                and bool(getattr(task, "paused", False))) if task else False,
            "attrs": attrs,
            "score": score,
            "skill_color": skills["color"],
            "skill_gold": skills["gold"],
            "skill_white": skills["white"],
            "skill_gained": (gained.get("color", 0) + gained.get("gold", 0)
                             + gained.get("white", 0)),
            "skill_last": info.get("skill_last") or "",
            "quick_enabled": self._enabled_of(self._task_by_name("QuickMatchTask")),
            # 第二栏的「刷到目标分 / 目标分」是**配置项**（2026-11-09 用户口径：
            # 落盘、启动装载），不再借 `task._score_stop_on` 这个内存开关。
            # 队列参数由 `TrainingFlowMixin.captain_queue_spec()` 从这三个键派生。
            "score_stop": bool(config.get("刷到目标分")),
            "score_target": str(config.get("目标分") or ""),
            "stamina_allowed": bool(config.get("自动恢复精力")),
            "fail_limit": config.get("失败率上限(%)", MISSING),
            "stuck": info.get("stuck") or (
                f"3 秒无响应 / 日 {config.get('单日最大点击数', MISSING)} 次 · 未触发"),
            "timeline": info.get("timeline") or [],
            "device_title": "篮球少女：飞跃巅峰",
            "device_meta": self._device_meta(),
        }
        return data

    @staticmethod
    def _style_from_config(config: dict) -> str:
        """配置里的指定队长 → 流派；查不到就按训练流（和 flow.style 的兜底一致）。

        2026-11-03 改成走 `style_for_name`：错字别名（羽漱川湊 → 羽濑川湊）
        和「带稀有度前缀 / 丢间隔点」的归一化都只在那一处，别在这里再实现一遍。
        """
        name = (config.get("指定队长") or "").strip()
        if name:
            try:
                from src.tasks.training.parsers import TrainingParsersMixin
                style = TrainingParsersMixin.style_for_name(name)
                if style:
                    return style
            except Exception:  # noqa: BLE001
                pass
        return "训练流"

    def _state(self, task) -> str:
        if task is None:
            return "idle"
        if getattr(task, "running", False):
            return "running"
        if getattr(task, "enabled", False):
            return "paused" if getattr(task, "paused", False) else "queued"
        return "idle"

    @staticmethod
    def _executor_paused() -> bool:
        """执行器是否暂停 —— 第一栏按钮的唯一判据（对齐框架 `StartCard.update_status`）。

        A（起始页【开始(F9)】）就只看 `og.executor.paused` 这一个布尔：
        暂停 → 显示【开始】，非暂停 → 显示【暂停】。不再掺截图通道状态。
        """
        executor = getattr(og, "executor", None)
        return True if executor is None else bool(getattr(executor, "paused", True))


    @staticmethod
    def _active_of(task) -> bool:
        """任务是不是「已启动」（对齐框架 TaskCard.update_buttons 的判据）。

        框架口径：`task.enabled` 为真就显示 Stop/Pause 按钮（无论 paused 还是
        running）；只有未 enabled 才回到 Start。我们第二栏的【开始/暂停】
        现在用框架的 task.pause()/unpause()，「暂停态」也是 enabled=True ——
        所以这里的语义从「真在跑」放宽为「已启动（在跑或已暂停）」：
        - enabled=True（running 或 paused）→ 第二栏显示【暂停】、■ 可点；
        - enabled=False（未启动/已停止）→ 显示【开始】、■ 置灰。

        断开游戏后 `disable()` 同步生效 → enabled 立刻为假 → 回到【开始】，
        不用等任务线程收尾（用户 2026-11-02 口径保留）。
        """
        if task is None:
            return False
        return bool(getattr(task, "enabled", False))

    def _device_meta(self) -> str:
        try:
            device = og.device_manager.get_preferred_device()
        except Exception:  # noqa: BLE001
            device = None
        if not device:
            return "未连接 · 请在「截图方式」页选择窗口"
        exe = device.get("nick") or device.get("selected_exe") or device.get("device") or "windows"
        status = "已连接" if device.get("connected") else "未连接"
        return f"{exe} · {status}"

    # ---------- 操作 ----------

    def set_captain(self, name, memory: str = ""):
        """勾选框/下拉改队长：写回 task.config（Config 会自动存盘）。

        2026-11-09 用户口径：第二栏的**勾选框 + 下拉菜单都是设置**，落盘、
        启动装载，所以这里写两个键：

        - `指定队长`：勾着时的队长；取消勾选 = 空串 = 无队长模式（业务只读它）；
        - `队长下拉选择`：下拉框记忆。勾选框没勾时也记，重启后下拉回到这个人，
          不会一勾就退回列表第一个。业务不读这个键。

        **换队长时「刷到目标分」的勾选和目标分保留**（用户 2026-11-08 口径）：
        它们现在是配置键（`刷到目标分` / `目标分`），换队长只改 `指定队长`，
        队列由 `captain_queue_spec()` 下次 run 重新派生 —— 不需要再手工重建参数。

        **CLI / MCP 注入的队列参数要清**（用户 2026-11-07 口径）：
        「每次指定新队长，旧的安排就清掉，不管单个指定还是传参进来的队列」
        —— 界面改队长 = 作废这次注入的 `_launch_queue`（配置派生值不受影响）。

        运行期变量 `_style` 也清，流派回到「按队长重新认」。
        取消勾选（name 为空）时同样关掉 `刷到目标分`（没队长没法刷分）。
        """
        task = self._task()
        if task is None:
            logger.warning("监控页：没有找到训练任务，队长改动未保存")
            return
        value = (name or "").strip()
        remember = (memory or value).strip()
        if (task.config.get("指定队长") or "") != value:
            task.config["指定队长"] = value
            if value:
                logger.info(f"监控页：指定队长 → {value}")
            else:
                logger.info("监控页：取消指定队长 → 清空变量，流派改回按游戏内队长识别")
        if remember and (task.config.get("队长下拉选择") or "") != remember:
            task.config["队长下拉选择"] = remember
        if getattr(task, "_launch_queue", None):
            task._launch_queue = None
            logger.info("监控页：界面指定队长 → 本次注入的多角色队列参数已作废")
        if not value:
            # 取消勾选队长 = 无队长模式，顺带关掉「刷到目标分」（落盘的设置也改掉）
            if task.config.get("刷到目标分"):
                task.config["刷到目标分"] = False
                logger.info("监控页：取消指定队长 → 「刷到目标分」一并关闭")
                # 勾选框会自己变灰/取消，不解释一句用户会以为「界面把我的设置吃了」
                self._notify("已取消指定队长：没有队长就没法刷分，「刷到目标分」一并关闭")
            reset = getattr(task, "reset_style", None)
            if callable(reset):
                try:
                    reset()
                except Exception as e:  # noqa: BLE001
                    logger.error(f"重置流派失败: {e}")
        self._update_data()

    @staticmethod
    def _captains(captain: str = "") -> dict:
        """流派 → [角色名]，数据来自业务常量 STYLE_BY_NAME。

        显示哪些分组由 `CAPTAIN_DROPDOWN_STYLES` 决定（用户 2026-11-08：
        先只保留训练流 / 扭蛋流，商店流 / 小游戏流先隐藏）。

        `captain` = 配置里当前的队长名：它那一组**不管隐藏与否都要在**——
        下拉里没有这个人，`_sync_captain` 就回填不到，勾选框一勾
        就会退回「第一个训练流角色」，把正在跑的队长悄悄换掉。
        """
        try:
            from src.tasks.training.constants import (
                CAPTAIN_DROPDOWN_STYLES, STYLE_BY_NAME)
        except Exception as e:  # noqa: BLE001
            logger.error(f"读取角色表失败: {e}")
            return {}
        groups: dict[str, list[str]] = {}
        for name, style in STYLE_BY_NAME.items():
            groups.setdefault(style, []).append(name)
        order = ["训练流", "商店流", "扭蛋流", "小游戏流"]
        keys = [key for key in order if key in CAPTAIN_DROPDOWN_STYLES]
        if not keys:                      # 常量写空了也别让下拉整个空掉
            keys = list(order)
        if captain:
            try:
                from src.tasks.training.parsers import TrainingParsersMixin
                style = TrainingParsersMixin.style_for_name(captain)
            except Exception:             # noqa: BLE001
                style = ""
            if style and style in groups and style not in keys:
                keys = [key for key in order if key in set(keys) | {style}]
        return {key: sorted(groups[key]) for key in keys if key in groups}

    # ---------- 快捷操作 ----------

    def toggle_capture(self):
        """等同「截图方式」页的截图按钮。"""
        try:
            og.main_window.start_tab.capture()
        except Exception as e:  # noqa: BLE001
            logger.error(f"开始截图失败: {e}")
        self._update_data()

    def toggle_connection(self):
        """第一栏【开始 / 暂停】—— 与框架 `StartCard.clicked` 完全一致。

        在跑 → 暂停；没跑 → 连接并启动执行器。没有其它判断、不切页。
        """
        if not og.executor.paused:
            og.executor.pause()
        else:
            og.app.start_controller.start()


    def set_skill(self, key, value):
        """技能品质数量：写回任务配置并立刻重算预计分数。"""
        task = self._task()
        name = SKILL_KEYS.get(key)
        if task is None or name is None:
            return
        task.config[name] = max(0, int(value))
        self._update_data()

    # ---------- 目标分停 / 专注药（用户 2026-11-05；2026-11-09 改落盘设置） ----------
    #
    # 「刷到目标分」「目标分」都是**配置键**（落盘、启动装载，2026-11-09 用户
    # 口径）。任务下一次 `run()` 的 `init_captain_queue()` 用
    # `captain_queue_spec()` 从这两个键 + `指定队长` 派生队列；界面不再往
    # `task._queue_spec` 之类内存字段里挂参数（那个通道只留给 CLI/MCP 注入）。
    # 达标判定和提前终止由 `handlers/match.handle_training_score`（训练评分页）
    # 和 `flow.advance_queue_after_round`（注册球员页）做。
    # - 「允许吃专注药」= `自动恢复精力`。钻石购买在 `handlers/stamina.py`
    #   是结构性禁止（钻石行没有「使用」按钮 → `forbidden`）。

    def set_score_stop(self, enabled: bool):
        """勾选「刷到目标分」：写配置键 `刷到目标分`（Config 自动存盘）。

        **先勾后填也允许**（2026-11-07 口径，修复「先有鸡还是先有蛋」死锁）：
        勾上只要求**已指定队长**；目标分可以之后填 —— 输入框一填数字
        （editingFinished）就写 `目标分`（set_score_target）。

        - 勾上：队长必填（没有就拒绝 —— 队列空了会静默降级成单队长模式）；
          目标分暂时为空 → 派生 `"队长"`（无目标），该角色按未达标一直刷，
          填入分数后下一次 run 自动按目标判定。
        - 取消勾选：`刷到目标分=False`，恢复「跑完一局就结束」的老行为。

        重启后勾选状态原样回来（用户在 2026-11-09 明确要求落盘）。
        换队长不动这两个键 —— 队列是派生值，下次 run 自然按新队长重算。
        """
        task = self._task()
        if task is None:
            logger.warning("监控页：没有找到训练任务，「刷到目标分」未保存")
            return
        if not enabled:
            if task.config.get("刷到目标分"):
                task.config["刷到目标分"] = False
                logger.info("监控页：刷到目标分 → 关（跑完一局就结束；设置已保存）")
            self._update_data()
            return
        captain = (task.config.get("指定队长") or "").strip()
        target = self._read_target(task)
        if not captain:
            logger.warning("监控页：勾「刷到目标分」失败 —— 请先在左侧指定队长")
            self._notify("勾选「刷到目标分」失败：请先在左侧指定队长", error=True)
            self.notify_stop_check()
            return
        if target and int(task.config.get("目标分") or 0) != target:
            task.config["目标分"] = target
        task.config["刷到目标分"] = True
        if target:
            logger.info(f"监控页：刷到目标分 → 开（{captain} 刷到 {target} 分后停止；设置已保存）")
        else:
            logger.warning(f"监控页：刷到目标分 → 开（{captain}，目标分未填 —— "
                           f"填入「目标分」输入框后自动生效）")
            self._notify(f"「刷到目标分」已开（{captain}）：请在「目标分」里填分数，"
                         f"否则会一直刷到手动停止")
        self._update_data()

    def set_score_target(self, target: int):
        """目标分输入框：写配置键 `目标分`（落盘，重启后回填）。"""
        task = self._task()
        if task is None:
            return
        target = max(0, int(target or 0))
        if int(task.config.get("目标分") or 0) == target:
            return
        task.config["目标分"] = target
        logger.info(f"监控页：目标分 → {target}（设置已保存，下一次 run 生效）")
        self._update_data()

    def _read_target(self, task) -> int:
        """读输入框里用户刚填的目标分（勾选时还没触发 editingFinished）。"""
        view = getattr(self, "_view", None)
        edit = getattr(view, "_edit_target", None)
        if edit is None:
            return 0
        text = (edit.text() or "").strip()
        return int(text) if text.isdigit() else 0

    @staticmethod
    def _notify(message: str, error: bool = False):
        """在界面上弹一条提示（InfoBar + 托盘），失败绝不影响主流程。

        **别用 `logger.warning(..., notify=True)`**：`ok.Logger.info/warning` 只收
        一个 message，多传 `notify` 就是 `TypeError` —— 而它发生在 Qt 槽里，
        异常被事件循环吞掉，表现成「提示从来没出现过」（2026-11-08 实测，
        本文件里 6 处这种调用全都没生效过）。

        合规通道是框架自己的 `ok.core.notifications`：
        `alert_info / alert_error` → `communicate.notification.emit(...)` →
        `MainWindow.show_notification`（qfw InfoBar，必要时加托盘/外部通知）。
        走 signal 发送，跨线程安全（任务线程里调用也不会摸 Qt 控件）。
        """
        try:
            from ok.core.notifications import alert_error, alert_info
            (alert_error if error else alert_info)(message)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"界面提示发送失败（只写日志）: {e}")

    def notify_stop_check(self):
        """校验失败把勾选框弹回去（view 层按 config 回填，这里强制刷一次）。"""
        view = getattr(self, "_view", None)
        if view is not None:
            task = self._task()
            view._syncing_extras = False
            view._sync_extras({"score_stop": bool(task.config.get("刷到目标分"))
                               if task else False})

    def set_stamina_allowed(self, allowed: bool):
        """「允许吃专注药」：映射到 `自动恢复精力`。

        关掉后 `ensure_stamina` 不再进恢复页；「训练所需专注还差 N」弹窗
        仍会点「回复」进页 —— 但没有可用饮料时（用户不勾 = 通常没饮料）
        `handle_stamina_recover` 会「没有可喝的 → 关页」，不会喝、更不会
        碰钻石行（钻石行永远 `forbidden`，写死在 stamina.py）。
        """
        task = self._task()
        if task is None:
            logger.warning("监控页：没有找到训练任务，「允许吃专注药」未保存")
            return
        value = bool(allowed)
        if bool(task.config.get("自动恢复精力")) != value:
            task.config["自动恢复精力"] = value
            logger.info("监控页：允许吃专注药 → " + ("开（专注不足自动喝饮料，不花钻石）"
                                              if value else "关（专注不足也不喝，宁停不耗）"))
        self._update_data()

    def set_quick_match(self, enabled):
        """队长栏右侧的「快速比赛」开关 —— **只管快速比赛这个模块**。

        规则（用户 2026-11-02 口径，两个模块对称）：

        1. **执行器**（`og.executor`）是唯一的运行基础，**只有第一栏
           【启动/停止】能动它**：启动 = 接入 + `executor.start()`，
           停止 = `executor.pause()` + 释放截图会话（**不复位本开关**）。
        2. **开**：enable `QuickMatchTask`，执行器还停着就**顺手**
           `executor.start()`（`_wake_executor`，拉游戏 / 连设备仍归【启动】）。
        3. **关**：只 `task.disable()` —— **绝不去 pause 执行器**，
           关执行器不归它管（第二栏【暂停】也一样）。
        4. **开关状态落盘**：`QuickMatchTask` 是框架的 `TriggerTask`，
           `enable()/disable()` 会把 `_enabled` 写进 `configs/QuickMatchTask.json`，
           所以重启后开关原样回来（2026-11-09 用户口径：启动就要装载）。
           第一栏【停止】**不再把它复位成「关」**：执行器停着它本来就不跑，
           下次【启动】按落盘的状态自动恢复。

        分工上两个模块无关：自动训练自己处理养成里的比赛；快速比赛管
        **养成之外**的比赛（全国大赛 / 圣山战 / 竞技场），养成跑着时它自动让位
        （`next_task()` 里 onetime 优先）。

        旧版只 `task.enable()`：`TriggerTask` 只在执行器没被暂停时才会被调度，
        于是开关拨到「开」什么都不动，非得有人先按第二栏【开始】——
        用户看到的就是「快速比赛跟第二栏的开始绑在一起」，其实是没人唤醒执行器。
        """
        task = self._task_by_name("QuickMatchTask")
        if task is None:
            logger.warning("监控页：没找到「快速比赛」任务")
            return
        try:
            if enabled:
                task.enable()
            else:
                task.disable()
        except Exception as e:  # noqa: BLE001
            logger.error(f"快速比赛 → {'开' if enabled else '关'}失败: {e}")
            self._update_data()
            return
        if enabled:
            # 开：执行器还停着就顺手唤醒（不是必须，接入仍归【启动】）
            self._wake_executor()
            logger.info("监控页：快速比赛 → 开")
        else:
            # 关：只关自己。执行器归第一栏【停止】管，这里不碰
            logger.info("监控页：快速比赛 → 关（不动执行器）")
        self._update_data()

    def _wake_executor(self):
        """顺手把执行器从「暂停」里放出来 —— **只唤醒，不接入**。

        `executor.start()` 只是把 `paused` 置 False 并唤醒调度循环；刷新设备、
        拉起游戏 exe 这些「接入」归第一栏【启动】（`start_controller`）。
        所以这里是「顺手而不是必须」：游戏没开着时唤醒也拿不到帧，
        用户还是得按【启动】。返回是否真的动过执行器。
        """
        try:
            if self.executor is not None and getattr(self.executor, "paused", True):
                self.executor.start()
                logger.info("监控页：顺手唤醒执行器（接入游戏仍是第一栏【启动】的事）")
                return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"唤醒执行器失败: {e}")
        return False

    def toggle_auto_training(self):
        """第二栏【开始 / 继续 / 暂停】—— 与框架 `TaskCard` 同源（2026-11-09 用户口径）。

        - `enabled and paused`（挂起态）→ `task.unpause()`（框架那颗按钮此时显示 Resume）
        - 在跑（enabled 且 running）→ `task.pause()`
        - 其余（未启用 / 未连接 / 刚停）→ `start_controller.start(task)`：
          框架同一句，自己刷设备、必要时拉起游戏，连上后 enable 这个任务。

        没有 `_connected()` 判断，也不在这里抢前台 —— B（任务卡的【开始】）本来就不判断这些。
        """
        task = self._task()
        if task is None:
            logger.warning("监控页：没找到训练任务")
            self._notify("没找到训练任务：可能还在加载，稍后再点一次", error=True)
            return
        enabled = bool(getattr(task, "enabled", False))
        paused = enabled and bool(getattr(task, "paused", False))
        running = enabled and not paused and bool(getattr(task, "running", False))
        try:
            if running:
                # 在跑 → 暂停（框架 pause_clicked：task.pause()）
                try:
                    task.pause()
                except Exception as e:              # noqa: BLE001
                    # 框架的 pause(task) 在 current_task != 自己时会抛（刚好在换任务）。
                    # 暂停意图仍然要落地，否则按钮点了没反应、脚本继续抢鼠标。
                    logger.warning(f"框架 pause 失败，直接置暂停标志: {e}")
                    task._paused = True
                logger.info("监控页：自动训练 → 暂停（task.pause，可无损恢复）")
            elif paused:
                # 已暂停 → 恢复（框架 start_clicked 第一分支；框架按钮显示 Resume）
                task.unpause()
                logger.info("监控页：自动训练 → 继续（task.unpause）")
            else:
                # 未启用 / 未连接 / 刚停：与框架 TaskCard.start_clicked 同款 ——
                # `start_controller.start(task)` 自己会刷设备、必要时拉起游戏，
                # 连上之后 enable 这个任务。这里不需要 `_connected()` 判断。
                logger.info("监控页：自动训练 → 开始（start_controller.start(task)）")
                og.app.start_controller.start(task)
        except Exception as e:  # noqa: BLE001
            logger.error(f"自动训练开关失败: {e}")
        self._update_data()

    def _task_by_name(self, name):
        try:
            for task in getattr(self.executor, "trigger_tasks", []) or []:
                if task.__class__.__name__ == name:
                    return task
            for task in getattr(self.executor, "onetime_tasks", []) or []:
                if task.__class__.__name__ == name:
                    return task
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _enabled_of(task) -> bool:
        return bool(getattr(task, "enabled", False)) if task is not None else False

    @staticmethod
    def _running_of(task) -> bool:
        """任务是不是在跑（正在执行）或在队列里等。

        第二栏的【开始/暂停】按这个切文案：
        在跑 → 「暂停」；没跑 → 「开始」。
        """
        if task is None:
            return False
        return bool(getattr(task, "running", False) or getattr(task, "enabled", False))

    def refresh(self):
        """重新扫一遍窗口，再刷一次页面数据。"""
        try:
            refresh = getattr(og.device_manager, "refresh", None)
            if callable(refresh):
                refresh()
        except Exception as e:  # noqa: BLE001
            logger.error(f"刷新设备失败: {e}")
        self._update_data()

    def open_logs(self):
        try:
            from ok.util.explorer import open_explorer_folder
            open_explorer_folder(get_relative_path("logs"))
        except Exception as e:  # noqa: BLE001
            logger.error(f"打开日志目录失败: {e}")


def _fmt_uptime(task) -> str:
    start = getattr(task, "start_time", 0) or 0
    if not start:
        return MISSING
    seconds = max(0, int(time.time() - start))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
