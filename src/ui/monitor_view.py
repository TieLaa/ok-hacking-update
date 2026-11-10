"""监控页的纯渲染层：只认 layout 字典 + data 字典，不碰任何业务对象。

拆出来的原因：
1. 布局要能直接用界面改（编辑态）或手改 JSON，不用改 Python；
2. 渲染层可以脱离 ok 框架单独实例化，方便离线截图验证。

数据里的占位符统一写成 `{key}`，缺失值显示 `—`；模板可带 `fallback` 整句兜底。
编辑态下每个区块右上角有上移/下移/隐藏，双击文字可直接改（写回 JSON）。
"""

from __future__ import annotations

import os
import re
import time

from PySide6.QtCore import QEvent, Qt, Signal, Slot
from PySide6.QtGui import QIntValidator, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (CheckBox, ComboBox, FluentIcon, LineEdit, PrimaryPushButton, SwitchButton)

from ok.util.file import get_relative_path

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
MISSING = "—"
GROUP = "__group__"
ATTR_ICON_DIR = os.path.join("assets", "ui", "attrs")
# 和游戏底部属性面板一致：三行两列，顺序不能改
DEFAULT_ATTRS = ("投射", "弹跳", "运控", "体能", "防守", "意识")


class MonitorActions:
    """页面按钮 + 布局编辑的回调容器。MonitorTab 会把自己的方法挂进来。"""

    def refresh_connection(self):
        pass

    def open_logs(self):
        pass

    def set_captain(self, name, memory=""):
        pass

    def toggle_capture(self):
        pass

    def toggle_connection(self):
        """第一栏的【开始 / 暂停】：执行器总开关（等价起始页的【开始(F9)】）。"""
        pass

    def set_skill(self, key, value):
        pass

    def set_quick_match(self, enabled):
        """队长栏右侧「快速比赛」开关：只管快速比赛模块自己（跟自动训练无关）。"""
        pass

    def set_score_stop(self, enabled):
        """「刷到目标分」勾选框：开着就反复练到目标分（复用多角色队列的提前终止）。"""
        pass

    def set_score_target(self, target):
        """「目标分」输入框：达标判据 = 一局结束的训练评分 ≥ 目标。"""
        pass

    def set_stamina_allowed(self, allowed):
        """「允许吃专注药」勾选框：关掉后专注不够就休息，绝不喝饮料/钻石。"""
        pass

    def toggle_auto_training(self):
        """第二栏的【开始/继续/暂停】：与框架任务卡同源（start/pause/unpause）。"""
        pass


class _SplitHandle(QFrame):
    """hero 中间那条线：编辑态可以左右拖，改右栏宽度。"""

    moved = Signal(int)

    def __init__(self, target: QWidget, ratio_mode: bool = False):
        super().__init__()
        self.setObjectName("monitorSplitHandle")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("editing", "false")
        self.target = target
        self.min_width = 120 if ratio_mode else 160
        self.max_width = 900 if ratio_mode else 520
        self._dragging = False
        self._start_x = 0
        self._start_w = 0
        self.setFixedWidth(1)

    def mousePressEvent(self, event):
        if self.width() <= 1:
            return
        self._dragging = True
        self._start_x = event.globalPosition().x()
        self._start_w = self.target.width()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        dx = event.globalPosition().x() - self._start_x
        width = int(max(self.min_width, min(self.max_width, self._start_w - dx)))
        self.target.setFixedWidth(width)

    def mouseReleaseEvent(self, event):
        if not self._dragging:
            return
        self._dragging = False
        self.moved.emit(self.target.width())


class _HeroSplit(QWidget):
    """主状态卡的左右分栏：按比例分配，窗口缩放时保持比例。"""

    def __init__(self, left: QWidget, handle: QFrame, right: QWidget, ratio: float = 0.5):
        super().__init__()
        self._handle = handle
        self._right = right
        self._ratio = max(0.2, min(0.8, float(ratio)))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(left, 1)
        lay.addWidget(handle)
        lay.addWidget(right, 0)

    def ratio(self) -> float:
        return self._ratio

    def avail(self) -> int:
        return max(1, self.width() - self._handle.width())

    def _apply(self):
        if self.width() < 240:
            return
        width = int(self.avail() * self._ratio)
        if self._right.width() != width:
            self._right.setFixedWidth(width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply()


class MonitorView(QWidget):
    """按 layout 描述生成卡片树，并在 update(data) 时刷新文本。"""

    def __init__(self, layout: dict, actions: MonitorActions | None = None,
                 captains: dict | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.layout_spec = layout or {}
        self.actions = actions or MonitorActions()
        self.captains = captains or {}
        self._captain_check: CheckBox | None = None
        self._captain_combo: ComboBox | None = None
        self._btn_capture: QPushButton | None = None
        self._btn_conn: QPushButton | None = None
        self._conn_text: tuple[str, str] = ("开始", "暂停")
        # 正在接入游戏时的文案（接入要等窗口稳定 + 设备就绪，几秒到几十秒）。
        # 2026-11-10 归位：这个态原来挂在**第二栏**【开始】上，但「接入游戏」
        # 从 2026-11-01 起就是第一栏的职责 —— 谁被点谁给反馈，第二栏保持
        # 【开始】不动（漏网之鱼见 docs/UI_MONITOR.md「启动中…归第一栏」）。
        # 按钮当前处在哪一态：running / idle —— 只在翻转时 setIcon，
        # 不拿 `icon()` 和枚举比（PySide 每次返回新 QIcon，比出来永远不相等）。
        self._conn_state: str | None = None
        self._switch_quick: SwitchButton | None = None
        self._btn_train: QPushButton | None = None
        # 「刷到目标分」勾选框 + 目标分输入框（用户 2026-11-05）
        self._check_score_stop: CheckBox | None = None
        self._edit_target: LineEdit | None = None
        # 「允许吃专注药」勾选框（关 = 不喝饮料，宁停不耗）
        self._check_stamina: CheckBox | None = None
        # 防止 update_data 回填时把用户正在输的值/刚点的勾抢回去
        self._syncing_extras = False
        self._train_text: tuple[str, str] = ("开始", "暂停")
        # 按钮当前处在哪一态：running / paused / idle —— 只在翻转时 setIcon
        self._train_state: str | None = None
        self._score_value: QLabel | None = None
        self._skill_labels: dict = {}
        self._skill_gain: QLabel | None = None
        self._attr_labels: dict = {}
        self._last_captain = ""
        self._last_valid_index = 0
        self._syncing_captain = False
        self._callbacks: list = []
        self._bindings: list[tuple[QLabel, str, str | None]] = []
        self._timeline_box: QVBoxLayout | None = None
        self._timeline_spec: dict = {}
        self._stat_bars: dict[str, QProgressBar] = {}

        self.setObjectName("monitorRoot")
        self.vBox = QVBoxLayout(self)
        self.vBox.setContentsMargins(0, 0, 0, 0)
        self.vBox.setSpacing(12)
        self.vBox.setAlignment(Qt.AlignTop)

        self._build()

    # ---------- 构建 ----------

    def _build(self):
        for idx, section in enumerate(self.layout_spec.get("sections", [])):
            if section.get("_hidden"):
                continue
            kind = section.get("type")
            path = ("sections", idx)
            if kind == "connection":
                self._build_connection(section, path)
            elif kind == "hero":
                self._build_hero(section, path)
            elif kind == "cards":
                self._build_cards(section, path)
            elif kind == "captain":
                self._build_captain(section, path)
            elif kind == "timeline":
                self._build_timeline(section, path)

    def _card(self, height: int | None = None) -> QFrame:
        frame = QFrame()
        frame.setObjectName("monitorCard")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        if height:
            frame.setFixedHeight(int(height))
        return frame

    def _label(self, text: str = "", role: str = "") -> QLabel:
        lb = QLabel(text)
        if role:
            lb.setProperty("mRole", role)
        return lb

    def _bind(self, label: QLabel, template: str, fallback: str | None = None):
        self._bindings.append((label, template, fallback))
        return label

    def _keep(self, callback):
        """保住槽的强引用。

        PySide6 对外部对象的绑定方法 / lambda 只持弱引用，
        对象被回收后按钮点了没反应（实测：暂停/停止按钮点了不动）。
        """
        self._callbacks.append(callback)
        return callback

    # ---- 槽：全部走 MonitorView 自己的方法，Qt 才会可靠地保持连接 ----


    def _button(self, text, handler, height=28, object_name="monitorGhost",
                tooltip=None, min_width=None):
        btn = QPushButton(text)
        btn.setObjectName(object_name)
        btn.setFixedHeight(height)
        if min_width:
            btn.setMinimumWidth(min_width)
        if tooltip:
            btn.setToolTip(tooltip)
        # 不用 clicked 信号：这个环境下绑定方法连不上（点了没反应），
        # 统一走 eventFilter 的鼠标抬起事件。
        btn._click_handler = self._keep(lambda h=handler: h())
        btn.installEventFilter(self)
        return btn

    @Slot()
    def _slot_capture(self):
        self.actions.toggle_capture()

    @Slot()
    def _slot_run(self):
        self.actions.toggle_connection()

    @Slot()
    def _slot_refresh(self):
        self.actions.refresh_connection()

    @Slot()

    def _make_primary(self, text, handler, height=30, icon=None):
        btn = PrimaryPushButton(icon or FluentIcon.PLAY, text)
        btn.setFixedHeight(height)
        btn._click_handler = self._keep(lambda h=handler: h())
        btn.installEventFilter(self)
        return btn


    def _attr_icon(self, name: str, width: int = 18):
        """属性小图标：assets/ui/attrs/<属性>.png；没有就返回 None（只显示文字）.

        素材是游戏里的原始比例（65x52，不是正方形），按宽度等比缩放。
        """
        path = get_relative_path(os.path.join(ATTR_ICON_DIR, f"{name}.png"))
        if not os.path.exists(path):
            return None
        pix = QPixmap(path)
        if pix.isNull():
            return None
        height = max(1, round(width * pix.height() / max(1, pix.width())))
        label = QLabel()
        label.setPixmap(pix.scaled(width, height, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation))
        label.setFixedSize(width, height)
        return label

    @Slot(str, int)

    @Slot(bool)
    def _slot_quick(self, checked):
        self.actions.set_quick_match(bool(checked))

    @Slot()
    def _slot_train(self):
        self.actions.toggle_auto_training()

    # ---------- 目标分 / 专注药 槽（用户 2026-11-05） ----------

    @Slot(bool)
    def _on_score_stop(self, checked: bool):
        if self._syncing_extras:
            return
        if self._edit_target is not None:
            self._edit_target.setEnabled(checked)
        self.actions.set_score_stop(checked)

    @Slot()
    def _on_target_edited(self):
        if self._syncing_extras or self._edit_target is None:
            return
        text = self._edit_target.text().strip()
        self.actions.set_score_target(int(text) if text.isdigit() else 0)

    @Slot(bool)
    def _on_stamina_toggle(self, checked: bool):
        if self._syncing_extras:
            return
        self.actions.set_stamina_allowed(checked)

    def _build_connection(self, spec: dict, path):
        card = self._card(spec.get("height", 54))
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)

        halo = QFrame()
        halo.setObjectName("monitorHalo")
        halo.setFixedSize(16, 16)
        halo_lay = QGridLayout(halo)
        halo_lay.setContentsMargins(4, 4, 4, 4)
        dot = QFrame()
        dot.setObjectName("monitorDot")
        dot.setFixedSize(8, 8)
        dot.setProperty("state", "idle")
        self._dot = dot
        halo_lay.addWidget(dot, 0, 0)
        lay.addWidget(halo)

        name = self._label("", "connName")
        lay.addWidget(self._bind(name, spec.get("title", "{device_title}")))
        meta = self._label("", "connMeta")
        lay.addWidget(self._bind(meta, spec.get("meta", "{device_meta}")))

        lay.addStretch(1)

        if spec.get("show_capture", False):
            self._btn_capture = self._button(
                spec.get("capture_text", "开始截图"), self._slot_capture,
                tooltip="等同「截图方式」页的截图按钮")
            lay.addWidget(self._btn_capture)

        if spec.get("show_run", True):
            # 第一栏右边是执行器总开关：开始 = pause 与否 / 暂停，等价起始页【开始(F9)】。
            # （旧的「开始/暂停」按的是 executor.pause()，会把第二栏的自动训练
            #  一起按住，逻辑正好反了 —— 见 MonitorTab.toggle_connection。）
            self._conn_text = (spec.get("run_text", "启动"),
                               spec.get("stop_text", "停止"))
            self._btn_conn = self._make_primary(
                self._conn_text[0], self._slot_run,
                icon=FluentIcon.PLAY)
            self._btn_conn.setToolTip(
                "执行器总开关（等价起始页的【开始(F9)】）：开始 = 连接并启动执行器；暂停 = 暂停执行器")
            lay.addWidget(self._btn_conn)

        for text, slot in (("重新检测", self._slot_refresh),):
            lay.addWidget(self._button(text, slot))

        self.vBox.addWidget(card)

    def _build_hero(self, spec: dict, path):
        card = self._card(spec.get("height", 176))
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(20)

        left = QVBoxLayout()
        left.setSpacing(6)

        head_row = QHBoxLayout()
        head_row.setSpacing(10)
        head_row.addWidget(self._label(spec.get("kicker", "当前进度"), "kicker"))
        head_row.addWidget(self._bind(
            self._label("", "heroText"),
            spec.get("title", "第 {week} 周 {day}"), spec.get("fallback")))
        head_row.addStretch(1)
        left.addLayout(head_row)

        stats = QHBoxLayout()
        stats.setSpacing(28)
        for sidx, stat in enumerate(spec.get("stats", [])):
            base = path + ("stats", sidx)
            box = QVBoxLayout()
            box.setSpacing(3)
            box.addWidget(self._label(stat.get("label", ""), "statKey"))

            value_row = QHBoxLayout()
            value_row.setSpacing(2)
            value = self._label("", "statValue")
            value_row.addWidget(self._bind(value, stat.get("value", "")))
            if stat.get("suffix"):
                suffix = self._label("", "statSuffix")
                value_row.addWidget(self._bind(suffix, stat["suffix"]), 0, Qt.AlignBottom)
            box.addLayout(value_row)

            if stat.get("bar"):
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setValue(0)
                bar.setTextVisible(False)
                bar.setFixedHeight(6)
                bar.setFixedWidth(int(stat.get("bar_width", 150)))
                bar.setObjectName("monitorBar")
                self._stat_bars[stat.get("bar_key", stat.get("label", "bar"))] = bar
                box.addWidget(bar)
            box.addStretch(1)
            stats.addLayout(box)
        stats.addStretch(1)
        left.addLayout(stats)
        left.addStretch(1)

        left_box = QWidget()
        left_box.setLayout(left)
        left_box.setMinimumWidth(int(spec.get("left_min_width", 280)))

        right = QVBoxLayout()
        right.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(self._label(spec.get("score_label", "分数预测"), "kicker"))
        self._score_value = self._label("", "scoreValue")
        head.addWidget(self._score_value)
        head.addSpacing(16)
        for key, text in (("color", "彩"), ("gold", "金"), ("white", "白")):
            head.addWidget(self._label(text, "skillTag"))
            value = self._label("", "skillValue")
            head.addWidget(value)
            self._skill_labels[key] = value
            head.addSpacing(4)
        self._skill_gain = self._label("", "skillGain")
        head.addWidget(self._skill_gain)
        head.addStretch(1)
        right.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(4)
        for idx, name in enumerate(spec.get("attrs") or DEFAULT_ATTRS):
            cell = QHBoxLayout()
            cell.setSpacing(6)
            icon = self._attr_icon(name)
            if icon is not None:
                cell.addWidget(icon, 0, Qt.AlignVCenter)
            cell.addWidget(self._label(name, "attrName"))
            cell.addStretch(1)
            value = self._label("", "attrValue")
            cell.addWidget(value)
            self._attr_labels[name] = value
            holder = QWidget()
            holder.setLayout(cell)
            # 和游戏一致：列优先（左列 投射/弹跳/运控，右列 体能/防守/意识）
            grid.addWidget(holder, idx % 3, idx // 3)
        right.addLayout(grid)
        right.addStretch(1)

        right_box = QWidget()
        right_box.setLayout(right)
        right_box.setFixedWidth(int(spec.get("right_width", 232)))

        handle = _SplitHandle(right_box, ratio_mode=True)
        split = _HeroSplit(left_box, handle, right_box,
                           float(spec.get("right_ratio", 0.5) or 0.5))
        lay.addWidget(split, 1)

        self.vBox.addWidget(card)

    def _build_captain(self, spec: dict, path):
        """队长选择：勾选框 + 按流派分组的下拉。取消勾选 = 无队长。"""
        card = self._card(spec.get("height", 54))
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)

        self._captain_check = CheckBox(spec.get("label", "指定队长"))
        # 2026-11-01：**默认不勾选**。
        # 旧版靠 `_sync_captain` 回填状态，但回填发生在第一次 `update_data` 之后；
        # 在那之前勾选框是「勾上」的，而下拉框的 currentIndex 又是第 0 项
        # （「── 训练流 ──」分组标题）。用户一进页面就点「启动」，
        # 勾选框的勾选状态会被当成「有队长」→ `set_captain(第一个训练流角色)`
        # → 扭蛋流被写成训练流。
        # 现在默认不勾，等 `_sync_captain` 按配置里的实际值回填。
        self._captain_check.setChecked(False)
        lay.addWidget(self._captain_check)

        combo = ComboBox()
        combo.setMinimumWidth(int(spec.get("combo_width", 220)))
        for style, names in self.captains.items():
            combo.addItem(f"── {style} ──", userData=GROUP)
            for name in names:
                combo.addItem(name, userData=name)
        self._captain_combo = combo
        lay.addWidget(combo)

        self._captain_check.setToolTip(
            spec.get("hint", "取消勾选 = 不接开局，只跑已有局"))

        if spec.get("show_auto_training", True):
            # 第二栏的按钮只管**自动训练脚本自己**的开关（开始 / 暂停），
            # 不碰「接入游戏」这件事 —— 那是第一栏总开关的职责。
            # 一个实例、两种文案：跑着的时候显示「暂停」，没跑显示「开始」。
            self._train_text = (spec.get("start_text", "开始"),
                               spec.get("pause_text", "暂停"))
            self._btn_train = self._make_primary(
                self._train_text[0], self._slot_train,
                icon=FluentIcon.PLAY)
            self._btn_train.setToolTip(
                "自动训练脚本开关：开始 / 暂停（没接上游戏时按【开始】会自动接入，"
                "接入进度显示在第一栏【启动】按钮上）")

            lay.addWidget(self._btn_train)

        # ---------- 目标分停 / 专注药（用户 2026-11-05 口径） ----------
        # 「持续刷到评分多少以后停止」复用**多角色队列**的提前终止机制：
        # 勾上 = 把队列配置成「指定队长:目标分」一项；取消 = 关掉多角色队列。
        # 顺序（用户 2026-11-05）：三个新控件**紧跟开始按钮**，快速比赛放最后。
        if spec.get("show_score_stop", True):
            self._check_score_stop = CheckBox("刷到目标分")
            self._check_score_stop.setToolTip(
                "勾上后反复练同一队长，直到一局结束的「训练评分」≥ 目标分才停"
                "（复用多角色队列的提前终止；取消勾选 = 跑完一局就结束）")
            self._check_score_stop.toggled.connect(self._on_score_stop)
            lay.addWidget(self._check_score_stop)

            self._edit_target = LineEdit()
            self._edit_target.setPlaceholderText("目标分")
            self._edit_target.setFixedWidth(int(spec.get("target_width", 96)))
            self._edit_target.setClearButtonEnabled(True)
            self._edit_target.setValidator(QIntValidator(0, 99999))
            self._edit_target.setToolTip("训练评分达到这个数就停止（勾选「刷到目标分」后生效）")
            self._edit_target.editingFinished.connect(self._on_target_edited)
            lay.addWidget(self._edit_target)

        if spec.get("show_stamina_toggle", True):
            self._check_stamina = CheckBox("允许吃专注药")
            self._check_stamina.setToolTip(
                "勾上：专注低于「恢复精力下限」时自动喝饮料（+30/+60，绝不花钻石）；\n"
                "取消勾选：专注不足时点「取消」并停止自动训练，绝不喝药/钻石")
            self._check_stamina.toggled.connect(self._on_stamina_toggle)
            lay.addWidget(self._check_stamina)

        lay.addStretch(1)

        if spec.get("show_quick_match", True):
            lay.addWidget(self._label(spec.get("quick_label", "快速比赛"), "rowLeft"))
            self._switch_quick = SwitchButton()
            self._switch_quick.setOnText("开")
            self._switch_quick.setOffText("关")
            # 快速比赛是**独立模块**（常驻 TriggerTask），跟第二栏的自动训练无关：
            # 养成里的比赛由自动训练自己处理，这个开关管的是全国大赛 / 圣山战 /
            # 竞技场这些「养成之外」的局。开着的时候执行器停着会顺手唤醒一下，
            # 但「接入游戏」仍是第一栏【启动】的事。
            self._switch_quick.setToolTip(
                "常驻监听养成之外的比赛（全国大赛 / 圣山战 / 竞技场等）："
                "赛前跳过 / 进比赛点 ▶▶ / 收结算。\n"
                "开启时执行器还停着会顺手唤醒（接入游戏仍按第一栏【启动】）；"
                "关闭只停快速比赛，不动自动训练，也不关执行器。")
            self._switch_quick.checkedChanged.connect(self._slot_quick)
            lay.addWidget(self._switch_quick)

        combo.currentIndexChanged.connect(self._on_captain_index)
        self._captain_check.toggled.connect(self._on_captain_check)

        if not self.captains:
            combo.setEnabled(False)
            self._captain_check.setEnabled(False)

        self.vBox.addWidget(card)

    # ---------- 队长控件 ----------

    def _first_captain(self) -> str:
        for names in self.captains.values():
            if names:
                return names[0]
        return ""

    def _index_of(self, name: str) -> int:
        combo = self._captain_combo
        if combo is None or not name:
            return -1
        for i in range(len(combo.items)):
            if combo.itemData(i) == name:
                return i
        # 2026-10-31：名字写法可能和角色表不一样（游戏里读到「亚理纱贾斯敏」、
        # 表里写「亚理纱·贾斯敏」），再按归一化后的包含关系找一遍，
        # 否则下拉框会空着/跳回第一个，看着像「没匹配上」。
        try:
            from src.tasks.ui.labels import name_matches
        except Exception:  # noqa: BLE001
            return -1
        for i in range(len(combo.items)):
            data = combo.itemData(i)
            if isinstance(data, str) and data != GROUP and name_matches(name, data):
                return i
        return -1

    def _select_index(self, index: int):
        if self._captain_combo is None or index < 0:
            return
        self._syncing_captain = True
        try:
            self._captain_combo.setCurrentIndex(index)
        finally:
            self._syncing_captain = False

    def _combo_name(self) -> str:
        """下拉框当前指向的真实角色名（分组标题返回空串）。"""
        combo = self._captain_combo
        if combo is None:
            return ""
        data = combo.itemData(combo.currentIndex())
        return str(data) if isinstance(data, str) and data != GROUP else ""

    def _on_captain_check(self, checked: bool):
        """勾选框：勾上 = 有队长，取消 = 无队长。

        取消勾选时**必须把变量清空**（用户 2026-11-01 口径）：
        旧版这里已经调用了 `set_captain("")`，但页面刚进来时勾选框是
        **默认勾上**的 —— 于是「勾选状态」和「配置里的队长」对不上：
        界面显示有队长，配置里其实是空的；反过来，用户取消勾选时
        `set_captain("")` 又因为「配置本来就是空」被当成「没变化」而跳过，
        变量没被清、流派还停在旧的（训练流）。

        现在两件事都做了：
        - 取消勾选 → `set_captain("")`（MonitorTab 里会清 config **和** `_style`），
          但**记忆留着**（`memory=` 传下拉当前的人）：下拉菜单也要落盘、
          重启后回到这个人（2026-11-09 用户口径）；
        - 勾选 → 用下拉当前选中的角色（**不是**「第一个角色」），
          否则会默默把流派定成列表里第一个（训练流）的人。
        """
        if self._syncing_captain:
            return
        combo = self._captain_combo
        if not checked:
            if combo is not None:
                combo.setEnabled(False)
            # 名字/流派从「指定队长」里清掉，但下拉记忆保留
            self.actions.set_captain("", memory=self._last_captain or self._combo_name())
            return
        if combo is not None:
            combo.setEnabled(True)
        # 取下拉**当前**选中的角色；没有就退回上一次选过的，再没有就第一个
        name = self._combo_name() or self._last_captain or self._first_captain()
        if name:
            index = self._index_of(name)
            if index >= 0:
                self._last_valid_index = index
                self._select_index(index)
            self.actions.set_captain(name)

    def _on_captain_index(self, index: int):
        combo = self._captain_combo
        if self._syncing_captain or combo is None:
            return
        data = combo.itemData(index)
        if data == GROUP:
            # 分组标题不可选：立刻退回上一个真实角色
            self._select_index(self._last_valid_index)
            return
        if data:
            self._last_captain = str(data)
            self._last_valid_index = index
            self.actions.set_captain(str(data))

    def _sync_captain(self, name: str, memory: str = ""):
        """按配置回填勾选框和下拉。

        勾选状态**只由配置里的队长名决定**（`name` 为空 = 无队长 = 不勾选）。
        没勾选时下拉显示 `memory`（`队长下拉选择`，上次选过的人）——
        这是落盘的界面记忆，否则重启后一下拉就是「── 训练流 ──」分组标题
        （2026-11-09 用户口径：下拉菜单也要启动装载）。
        旧版勾选框默认是勾上的，回填要等第一次 `update_data` 才发生；
        在那之前用户点「启动」，勾选状态会被误当成「有队长」。
        """
        combo, check = self._captain_combo, self._captain_check
        if combo is None or check is None:
            return
        if combo.dropMenu is not None and combo.dropMenu.isVisible():
            return          # 用户正在下拉，别抢状态
        checked = bool(name)
        shown = name or memory
        self._syncing_captain = True
        try:
            if check.isChecked() != checked:
                check.setChecked(checked)
            index = self._index_of(shown)
            if index >= 0:
                self._last_captain = shown
                self._last_valid_index = index
                if combo.currentIndex() != index:
                    combo.setCurrentIndex(index)
            elif not checked:
                self._last_captain = ""
            combo.setEnabled(checked)
        finally:
            self._syncing_captain = False

    def _build_cards(self, spec: dict, path):
        row = QHBoxLayout()
        row.setSpacing(12)
        for jdx, item in enumerate(spec.get("items", [])):
            if item.get("_hidden"):
                continue
            base = path + ("items", jdx)
            card = self._card(spec.get("height", 126))
            lay = QVBoxLayout(card)
            lay.setContentsMargins(16, 14, 16, 10)
            lay.setSpacing(0)
            lay.addWidget(self._label(item.get("title", ""), "cardTitle"))
            lay.addSpacing(4)
            for kdx, row_spec in enumerate(item.get("rows", [])):
                row_frame = QFrame()
                row_frame.setObjectName("monitorRow")
                row_lay = QHBoxLayout(row_frame)
                row_lay.setContentsMargins(0, 5, 0, 5)
                row_lay.setSpacing(8)
                row_lay.addWidget(self._label(row_spec[0], "rowLeft"))
                row_lay.addStretch(1)
                right = self._label("", "rowRight")
                if len(row_spec) > 1:
                    row_lay.addWidget(self._bind(right, row_spec[1]))
                else:
                    row_lay.addWidget(right)
                lay.addWidget(row_frame)
            row.addWidget(card, 1)
        self.vBox.addLayout(row)

    def _build_timeline(self, spec: dict, path):
        card = self._card()
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(0)
        lay.addWidget(self._label(spec.get("title", "最近动作"), "cardTitle"))
        lay.addSpacing(6)
        box = QVBoxLayout()
        box.setSpacing(7)
        lay.addLayout(box)
        lay.addStretch(1)
        self._timeline_box = box
        self._timeline_spec = spec
        self.vBox.addWidget(card, 1)

    # ---------- 刷新 ----------

    def update_data(self, data: dict):
        for label, template, fallback in self._bindings:
            text, complete = _render(template, data)
            label.setText(text if complete or fallback is None else fallback)

        for key, bar in self._stat_bars.items():
            raw = data.get(key)
            try:
                bar.setValue(max(0, min(100, int(round(float(raw))))))
            except (TypeError, ValueError):
                bar.setValue(0)

        state = data.get("state", "idle")
        if hasattr(self, "_dot"):
            self._dot.setProperty("state", state)
            self._dot.style().unpolish(self._dot)
            self._dot.style().polish(self._dot)

        if self._timeline_box is not None:
            self._fill_timeline(data.get("timeline") or [])

        if self._captain_check is not None:
            self._sync_captain(data.get("captain") or "",
                               data.get("captain_memory") or "")

        self._sync_run_button(data)
        self._sync_switches(data)
        self._sync_score(data)

    def _sync_score(self, data: dict):
        if self._score_value is not None:
            score = data.get("score")
            self._score_value.setText(str(score) if score is not None else MISSING)
        attrs = data.get("attrs") or {}
        for name, label in self._attr_labels.items():
            value = attrs.get(name)
            label.setText(str(value) if value is not None else MISSING)
        for key, label in self._skill_labels.items():
            value = data.get(f"skill_{key}")
            label.setText(str(value) if value is not None else MISSING)
        if self._skill_gain is not None:
            gained = int(data.get("skill_gained") or 0)
            self._skill_gain.setText(f"本局 +{gained}" if gained else "")
            self._skill_gain.setToolTip(
                f"本局三选一拿到的技能数；上一次：{data.get('skill_last') or '—'}")

    def _sync_run_button(self, data: dict):
        """第一栏：执行器暂停 → 【开始】▶；非暂停 → 【暂停】‖（与框架 StartCard 一致）。

        判据是**会话在跑**（`connected`）—— 截帧通道活着 **且** 执行器没
        暂停（2026-11-02 修正，详见 MonitorTab._connected）。只看截帧通道
        会在启动时就为真（框架启动会自己建 WGC 会话），第一栏一进页面
        就显示【停止】，看着像「默认已经开始了」。

        图标必须跟着文案走：跑着 = ■（停止），没跑 = ▶（播放）。
        用户 2026-11-02 口径：「开始了以后，按钮应该是 ■停止，而不是 ▶停止」。

        **接入中（`connecting`）显示在这里**（2026-11-10 归位）：
        `start_controller.starting` 期间既不是「已连接」也不是「没点过」，
        按钮得给点反馈（几秒到几十秒）。这个态以前挂在**第二栏**【开始】上，
        于是「点第一栏【启动】、第二栏却变成【启动中…】」—— 看着像两颗按钮
        串位了。谁被点谁给反馈，第二栏保持【开始】不动。
        """
        if self._btn_conn is None:
            return
        # 与框架 StartCard.update_status 同款：只看执行器是否暂停，没有第三个态。
        if data.get("paused"):
            state, text, icon = "idle", self._conn_text[0], FluentIcon.PLAY
        else:
            state, text, icon = "running", self._conn_text[1], FluentIcon.PAUSE
        if self._btn_conn.text() != text:
            self._btn_conn.setText(text)
        if self._conn_state != state:
            self._btn_conn.setIcon(icon)
            self._conn_state = state

    def _sync_switches(self, data: dict):
        """第二栏按钮：自动训练**真在跑** → 【暂停】；否则 → 【开始】。

        2026-11-02 修正：旧判据是 `running or enabled`（enabled 含「在队列里
        等」），于是点完【开始】按钮立刻变【暂停】，但其实一帧都还没跑。
        现在只认 `training_active`（= enabled 且 running）。

        **接入中的【启动中…】不在这里**（2026-11-10 归位）：没接上游戏时点
        【开始】仍然会去主动接入（用户 2026-11-02 口径），但进度显示在
        **第一栏**【启动】按钮上（`_sync_run_button`）—— 接入是第一栏的职责，
        第二栏只管脚本自己，保持【开始】不动。

        **断开游戏时**（第一栏点【停止】）第二栏要回到未启动状态：
        训练任务已被 disable，`enabled` 立刻为假 → `training_active` 为假
        → 显示【开始】。不用等任务线程把 `running` 收尾（那有几秒滞后）。
        """
        if self._btn_train is not None and "training_active" in data:
            if data.get("training_paused"):
                # 框架 TaskCard 口径：enabled+paused → Start(Resume)。
                # 显示【开始】（▶），点击 = task.unpause() 恢复。
                state, text, icon = "paused", self._train_text[0], FluentIcon.PLAY
            elif data.get("training_active"):
                state, text, icon = "running", self._train_text[1], FluentIcon.PAUSE
            else:
                state, text, icon = "idle", self._train_text[0], FluentIcon.PLAY
            if self._btn_train.text() != text:
                self._btn_train.setText(text)
            if self._train_state != state:
                self._btn_train.setIcon(icon)
                self._train_state = state
        for key, switch in (("quick_enabled", self._switch_quick),):
            if switch is None or key not in data:
                continue
            value = bool(data.get(key))
            if switch.isChecked() != value:
                switch.blockSignals(True)
                switch.setChecked(value)
                switch.blockSignals(False)
        self._sync_extras(data)

    def _sync_extras(self, data: dict):
        """「刷到目标分 / 目标分 / 允许吃专注药」按配置回填。

        与快速比赛开关同一套防抖：程序回填时 blockSignals，
        只在**值真的变了**才动控件，免得把用户正在输入的数字抢回去。
        输入框有焦点时（用户正在打字）完全跳过这一拍。
        """
        if self._syncing_extras:
            return
        self._syncing_extras = True
        try:
            if self._check_score_stop is not None and "score_stop" in data:
                checked = bool(data.get("score_stop"))
                if self._check_score_stop.isChecked() != checked:
                    self._check_score_stop.setChecked(checked)
                if self._edit_target is not None:
                    self._edit_target.setEnabled(checked)
            if (self._edit_target is not None and "score_target" in data
                    and not self._edit_target.hasFocus()):
                text = str(data.get("score_target") or "")
                if self._edit_target.text() != text:
                    self._edit_target.setText(text)
            if self._check_stamina is not None and "stamina_allowed" in data:
                allowed = bool(data.get("stamina_allowed"))
                if self._check_stamina.isChecked() != allowed:
                    self._check_stamina.setChecked(allowed)
        finally:
            self._syncing_extras = False

    def _fill_timeline(self, events: list):
        box = self._timeline_box
        while box.count():
            item = box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        limit = int(self._timeline_spec.get("max_rows", 8))
        for ev in events[:limit]:
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(12)
            time_lb = self._label(str(ev.get("time", "")), "tlTime")
            time_lb.setFixedWidth(int(self._timeline_spec.get("time_width", 62)))
            lay.addWidget(time_lb)
            ev_lb = self._label(str(ev.get("event", "")), "tlEvent")
            ev_lb.setFixedWidth(int(self._timeline_spec.get("event_width", 80)))
            lay.addWidget(ev_lb)
            detail = self._label(str(ev.get("detail", "")), "tlDetail")
            lay.addWidget(detail, 1)
            result = self._label(str(ev.get("result", "")), "tlResult")
            lay.addWidget(result)
            box.addWidget(row)

    # ---------- 双击编辑 ----------
    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonRelease:
            handler = getattr(obj, "_click_handler", None)
            if handler is not None and event.button() == Qt.LeftButton:
                handler()
                return True
        return super().eventFilter(obj, event)


def _render(template: str, data: dict) -> tuple[str, bool]:
    """把 `{key}` 换成值；返回 (文本, 是否全部有值)。"""
    if not template:
        return "", True
    complete = True

    def sub(m):
        nonlocal complete
        value = data.get(m.group(1))
        if value in (None, ""):
            complete = False
            return MISSING
        return str(value)

    return PLACEHOLDER.sub(sub, template), complete

