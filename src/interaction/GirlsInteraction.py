"""Unity 客户端输入后端（真实输入版）。

2026-10-25 实测结论（《篮球少女：飞跃巅峰》Windows 客户端）：

1. `PostMessage(WM_LBUTTONDOWN/UP)` **完全无效**：调用返回成功，游戏毫无反应。
   试过的组合：裸消息 / 加 WM_ACTIVATE / 同步移动光标 / 游戏前台，全部无效。
2. 真实 `mouse_event(LEFTDOWN/LEFTUP)` **有效**：能点开菜单。
3. 因此坐标不能靠猜像素——一律用 OCR/模板拿到的框换算出屏幕坐标再点。

实现要点：
- 点击前必须 `SetForegroundWindow`（游戏不在前台时真实点击会落到别的窗口）。
- 点击前把光标移到目标并留一点时间，点完还原光标，避免打扰用户。
- `mouse_down/mouse_up` 成对，异常也要抬起，否则会卡住鼠标键。
- **用户键鼠操作优先**（2026-10-29 定）：这个游戏只有前台才吃真实点击，所以脚本
  必须抢前台——那就把"抢的时机"交给用户：你一动键鼠，脚本立刻停手；你停手满
  `user_idle_wait` 秒（默认 3），才重新 Alt 拉起游戏继续挂机。等待没有上限，
  绝不因为"等太久"就去抢正在操作的用户。
"""

import ctypes
import time

import win32api
import win32con
import win32gui

from ok.device.interaction_methods.base import BaseInteraction
from ok.device.interaction_methods.keys import vk_key_dict
from ok.util.logger import Logger

from src.util.win_focus import force_foreground, restore_if_minimized

logger = Logger.get_logger(__name__)

CURSOR_SETTLE = 0.04     # 绝对定位后等引擎采样（一步到位，不用滑行）
FOREGROUND_WAIT = 0.15

# ---------- 用户优先（前台挂机） ----------
# 2026-10-29 需求（第二版）：**用户任意键鼠操作优先**。之前的写法是"暂停 5 秒
# 到点就继续"，到点后第一个动作就是 Alt 抢前台，用户刚停手就被踢下焦点，等于没让。
# 现在改成：只看"用户最后一次输入距今多久"，静默满 user_idle_wait 秒才算空闲。
# 判据用 GetLastInputInfo：系统"最后一次输入事件"的时间戳（键盘、鼠标移动、
# 滚轮、点击都算）。脚本自己的 mouse_event / keybd_event 也会更新它，所以每次
# 脚本动作结束后把这个 tick 记成基准；tick 变了 = 中间有别人的输入。
USER_IDLE_WAIT = 3.0         # 秒：用户最后一次输入之后静默这么久才继续（0 = 不等，一直抢）
IDLE_POLL = 0.1              # 秒：等待期间的采样间隔
IDLE_LOG_EVERY = 30.0        # 秒：等这么久还没空闲就提醒一次（只提醒，**不强制恢复**）
STEAL_MARK_SETTLE = 0.02     # 秒：脚本动作结束后等系统把这次注入记进输入队列

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

# 虚拟桌面度量（多屏时坐标要按整个虚拟桌面归一化）
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


class _LastInputInfo(ctypes.Structure):
    """GetLastInputInfo 的入参结构（cbSize + 最后一次输入的 GetTickCount 毫秒）。"""
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class GirlsInteraction(BaseInteraction):
    """真实鼠标 + 真实键盘，必要时要先把游戏窗口置前。"""

    def __init__(self, capture, hwnd_window, foreground=True):
        super().__init__(capture)
        self.hwnd_window = hwnd_window
        self.foreground = foreground
        self._saved_cursor = None
        self._down_pos = None
        self._pressed_keys = {}
        self.user_idle_wait = USER_IDLE_WAIT  # 任务层可通过配置项覆盖；0 = 关闭等待
        self._script_input_tick = None   # 脚本最后一次动作对应的输入事件 tick
        self._action_started_at = None   # 本次动作开始时刻（GetTickCount），记账区间起点
        self.steal_count = 0             # 累计让路次数（复盘用）
        # 可选：一个无参可调用对象，返回 True 表示"立刻结束等待"。
        # 任务层用它挂紧急停止（等待可能很久，热键不能等到用户停手才生效）。
        self.abort_check = None
        self._stop_requested = False     # 等待期间收到停止请求 → 下一次点击直接跳过
        # 最近一次「闸门拦下动作」的时刻（wall clock）：暂停/停止/抢不到前台时
        # 动作根本没发出去，任务层的看门狗不能把它当成「点过一下」。
        self.last_skip_at = 0.0

    # ---------- 窗口 ----------

    @property
    def hwnd(self):
        return self.hwnd_window.top_hwnd or self.hwnd_window.hwnd

    def ensure_foreground(self, tries=4):
        """把游戏窗口真正置前。**抢不到就返回 False，调用方不能硬点。**

        这一层只管**策略**：什么时候允许抢（等用户停手、检查暂停/停止）、
        抢之前要不要先恢复最小化；真正的抢法（Alt 注入 → AttachThreadInput →
        置顶再撤回）在 `src.util.win_focus.force_foreground` —— tools 里的独立
        脚本用的是同一份实现，别在这里再写一遍。
        """
        hwnd = self.hwnd
        if not hwnd:
            return False
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        if not self.foreground:
            return False

        restore_if_minimized(hwnd)

        # 等待归口在 `_ready_to_act`（唯一入口）：这里不再等第二次。
        # 直接调用本方法的外部代码（目前没有）必须自己先等用户空闲。
        # 等待期间用户点了【暂停】/按了停止键：不要再抢前台
        if self._blocked():
            logger.info("收到停止/暂停请求 → 放弃抢前台")
            return False

        if force_foreground(hwnd, tries=tries, wait=FOREGROUND_WAIT,
                            log=logger, on_alt_injected=self.mark_script_input):
            return True

        # 不要用「最小化再恢复」硬抢：前台是管理员进程时恢复会失败，窗口反复
        # visible=False，WGC 抓不到帧、点击也全部落空（实训 2026-10-26）。
        fg_name = win32gui.GetWindowText(win32gui.GetForegroundWindow())
        logger.warning(f"游戏未能置前（目标 {hwnd}，当前前台「{fg_name}」），本次操作放弃")
        return False


    # ---------- 用户优先（键鼠操作让路） ----------

    def _blocked(self):
        """外部（任务层）说「现在不要动手」：紧急停止 / 已停止 / 已暂停。

        和 `abort_check` 是同一个语义。`_stop_requested` 是「等待中被打断」留下的
        标记，消费一次就清掉，否则下一次点击会白跳。
        """
        if self._stop_requested:
            self._stop_requested = False
            return True
        if self.abort_check is None:
            return False
        try:
            return bool(self.abort_check())
        except Exception as e:                      # noqa: BLE001
            logger.debug(f"abort_check 失败（按不打断处理）: {e}")
            return False


    @staticmethod
    def _last_input_tick():
        """系统最后一次输入事件的 GetTickCount 毫秒；读不到返回 None。"""
        try:
            info = _LastInputInfo()
            info.cbSize = ctypes.sizeof(_LastInputInfo)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return int(info.dwTime)
        except Exception as e:                      # noqa: BLE001
            logger.debug(f"GetLastInputInfo 失败: {e}")
        return None

    @staticmethod
    def _now_tick():
        """开机毫秒，和 GetLastInputInfo 的 dwTime 同一个时钟（取低 32 位）。"""
        return ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF

    def _idle_seconds(self, tick):
        """距今多少秒没有输入事件；tick 为空返回 None。"""
        if tick is None:
            return None
        delta = (self._now_tick() - tick) & 0xFFFFFFFF
        if delta > 0x7FFFFFFF:          # 32 位回绕或时钟异常，按"刚刚动过"处理
            return 0.0
        return delta / 1000.0

    def mark_script_input(self, settle=STEAL_MARK_SETTLE, since=None):
        """动作结束：只把「本次动作区间内」的最后一次输入认成脚本自己的。

        为什么不能直接认「当前最后一次输入」（2026-11-13 用户反馈）：用户在你
        点完的 20ms settle 窗口里动一下鼠标，那一拍就会被记成脚本的，下一拍
        `wait_for_user_idle` 读到同一个 tick → 判定「没人动」→ 直接抢鼠标。
        持续操作时鼠标事件约 16ms 一个，这个窗口几乎必然命中，让路规则等于失效。

        `since` 是动作开始时刻（GetTickCount）；不传用 `_action_started_at`
        （由 `_ready_to_act` 统一记，所有动作共用这一个来源）。动作结束**之后**
        才出现的输入属于用户，不认领 —— 保留旧基准，下一次动作前自然会等满
        user_idle_wait 秒。

        settle 仍要等：脚本注入是异步入队的，不等可能读不到自己的事件。
        """
        if self.user_idle_wait <= 0:
            return
        if settle:
            time.sleep(settle)
        tick = self._last_input_tick()
        if tick is None:
            return
        since = since if since is not None else self._action_started_at
        if since is not None:
            end = self._now_tick()
            if ((tick - since) & 0xFFFFFFFF) > ((end - since) & 0xFFFFFFFF):
                return          # 输入发生在动作结束之后 = 用户的，不认领
        self._script_input_tick = tick

    def wait_for_user_idle(self):
        """动作前调用：用户还在操作就一直等，直到他停手满 user_idle_wait 秒。

        判据是「最后一次输入事件距今多久」，不是「暂停了几秒」。差别很关键：
        旧写法暂停到点就继续，而继续的第一个动作就是 Alt 抢前台，用户刚停手就被
        踢下焦点，等于没让。现在只有用户真的静默满 user_idle_wait 秒才动手。

        **没有等待上限**：用户的操作永远优先。如果日志里出现"仍在操作"的提醒，
        说明有别的程序在持续注入键鼠事件，不是用户本人在用 —— 去查那个程序，
        不要在这里加"等太久就抢"的兜底。

        返回这次是否等过（复盘用）。0 = 不等（一直抢）。
        """
        if self.user_idle_wait <= 0:
            return False
        tick = self._last_input_tick()
        if tick is None:
            return False
        if self._script_input_tick is None:
            self._script_input_tick = tick
            return False
        # tick 跟基准一致 = 上次脚本记账之后没人动过键鼠
        if tick == self._script_input_tick:
            return False

        start = time.monotonic()
        self.steal_count += 1
        logger.info(f"检测到键鼠操作 → 暂停，等你停手 {self.user_idle_wait:.1f} 秒后继续")
        last_log = start
        while True:
            time.sleep(IDLE_POLL)
            if self.abort_check is not None and self.abort_check():
                # 用户按了停止键：不要再抢前台（那一瞬间最不该做的事）,也不必真点这一下
                logger.info("收到停止请求 → 结束等待，取消本次操作")
                self._stop_requested = True
                return True
            now_tick = self._last_input_tick()
            if now_tick is None:
                now_tick = tick
            idle = self._idle_seconds(now_tick) or 0.0
            if idle >= self.user_idle_wait:
                break
            waited = time.monotonic() - start
            if waited - (last_log - start) >= IDLE_LOG_EVERY:
                last_log = time.monotonic()
                logger.info(f"用户仍在操作 → 继续等待（已等 {waited:.0f} 秒，最近一次输入 {idle:.1f} 秒前）")

        self._script_input_tick = now_tick
        logger.info(f"用户已停手 {self.user_idle_wait:.1f} 秒 → 恢复挂机"
                    f"（本次让路 {time.monotonic() - start:.1f} 秒）")
        return True

    # ---------- 光标 ----------

    def _to_screen(self, x, y):
        """客户区坐标 -> 屏幕像素。用框架自带换算（会算上捕获区域偏移）。"""
        try:
            return self.capture.get_abs_cords(int(x), int(y))
        except Exception:
            return win32gui.ClientToScreen(self.hwnd, (int(x), int(y)))

    @staticmethod
    def _virtual_desktop():
        m = win32api.GetSystemMetrics
        return (m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN),
                m(SM_CXVIRTUALSCREEN), m(SM_CYVIRTUALSCREEN))

    def _move_to(self, x, y, settle=None):
        """把光标一步移动到客户区 (x, y)。

        settle：移动后停留秒数，None = 用 CURSOR_SETTLE。拖动时内部传 0，
        由 swipe 自己按总时长分配间隔（见 swipe 的单位说明）。

        三种做法实测对比（2026-10-25，《篮球少女》）：
        1. `SetCursorPos`：游戏**完全感知不到**——它读 raw input，这个 API 不产生 raw 事件；
        2. 相对位移 `mouse_event(MOVE, dx, dy)` 分步滑行：有效，但要几十上百步，
           慢、且全程霸占用户鼠标；
        3. `mouse_event(MOVE|ABSOLUTE|VIRTUALDESK, ...)`：**既有 raw 事件又能一步到位**，
           坐标按虚拟桌面归一化到 0~65535，跟窗口大小/分辨率无关。

        所以统一走第 3 种。注意：绝不能再用 SetCursorPos 收尾，那会让系统光标和
        游戏内部位置错位，之后每一次点击都会偏 —— 这就是"点在没交互的地方"的根因。
        """
        sx, sy = self._to_screen(x, y)
        vx, vy, vw, vh = self._virtual_desktop()
        nx = int(round((sx - vx) * 65535 / max(vw - 1, 1)))
        ny = int(round((sy - vy) * 65535 / max(vh - 1, 1)))
        win32api.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                             nx, ny, 0, 0)
        time.sleep(CURSOR_SETTLE if settle is None else max(settle, 0))
        return sx, sy

    def _move_cursor(self, x, y):
        return self._move_to(x, y)

    # ---------- 鼠标 ----------

    _EVENTS = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }

    def _restore_cursor(self, saved, target):
        """点完把光标还给用户，但**只在它仍停在我们放的位置时**。

        `SetCursorPos` 不产生 raw 输入（游戏感知不到），所以不会把游戏内的光标搞错。
        但如果用户在我们按住的那 100ms 里动了鼠标，光标已经不在 `target` 上，这时候
        再复原就是硬拽——2026-11-08 用户反馈"用着鼠标被抢走"的一部分就是这里。
        """
        if saved is None or target is None:
            return
        try:
            if tuple(win32api.GetCursorPos()) == (int(target[0]), int(target[1])):
                win32gui.SetCursorPos(int(saved[0]), int(saved[1]))
        except Exception:                           # noqa: BLE001
            pass

    def _ready_to_act(self, *, log_skip=False, raise_if_no_foreground=False,
                      need_foreground=True):
        """动作前的统一闸门：True = 可以动手。点击/按下/拖动/滚动/按键全走这里。

        顺序是有历史原因的（2026-10-25 / 10-29 两次实事故）：
        1. `_blocked()`：暂停/停止后一个动作都不许发；
        2. `wait_for_user_idle()`：用户还在用键鼠就等（用户操作优先）；
        3. 再查一次：等的那几秒里用户可能按了暂停；
        4. `ensure_foreground()`：抢不到前台就**绝不动手** —— 真实鼠标事件会落到
           别的窗口上，实测点到了 ok-script GUI 上。

        `_action_started_at` 在这里统一记：`mark_script_input` 用它划出「本次动作
        区间」，区间之外的输入一律当用户输入（2026-11-13）。所有动作共用这一个
        来源，别在调用点各自记。

        `need_foreground=False`：只发键盘/输入文字，不需要抢前台（`input_text`）。

        `raise_if_no_foreground`：点击类是硬失败（抛错让人看见）；按下/移动/滚动类
        静默放弃（下一拍会重试）。
        """
        if self._blocked():
            self.last_skip_at = time.time()
            return False
        self.wait_for_user_idle()
        if self._blocked():
            if log_skip:
                logger.info("停止/暂停请求 → 本次点击跳过")
            self.last_skip_at = time.time()
            return False
        self._action_started_at = self._now_tick()
        if need_foreground and not self.ensure_foreground():
            self.last_skip_at = time.time()
            if self._blocked():          # 抢前台途中被停止/暂停叫停，不是故障
                return False
            if raise_if_no_foreground:
                raise RuntimeError("游戏窗口未能置前，放弃本次点击")
            return False
        return True

    def click(self, x=-1, y=-1, move_back=False, name=None, down_time=0.03, move=True, key="left"):
        if x < 0 or y < 0:
            x = round(self.capture.width * 0.5)
            y = round(self.capture.height * 0.5)

        if not self._ready_to_act(log_skip=True, raise_if_no_foreground=True):
            return
        saved = win32api.GetCursorPos()
        target = self._move_cursor(x, y) if move else None
        # 按住时间不能太短：实测「启程」这类按钮 70ms 的点击会被丢弃，
        # 人手点击一般在 80~150ms，这里统一抬到 100ms 起步。
        down_time = max(float(down_time), 0.10)
        down, up = self._EVENTS.get(key, self._EVENTS["left"])
        try:
            win32api.mouse_event(down, 0, 0, 0, 0)
            time.sleep(down_time)
        finally:
            win32api.mouse_event(up, 0, 0, 0, 0)
            self._restore_cursor(saved, target)
            self.mark_script_input()

    def mouse_down(self, x=-1, y=-1, name=None, key="left"):
        if x < 0 or y < 0:
            x = round(self.capture.width * 0.5)
            y = round(self.capture.height * 0.5)
        if not self._ready_to_act():
            return
        self._move_cursor(x, y)
        self._down_key = key
        win32api.mouse_event(self._EVENTS.get(key, self._EVENTS["left"])[0], 0, 0, 0, 0)
        self.mark_script_input()

    def mouse_up(self, name=None, key="left"):
        # 抬起永远不拦：停止/异常时必须能把按住的键松开。
        # 但**没按下过就不要发空 UP** —— 那会把用户正在拖的东西松掉。
        if getattr(self, "_down_key", None) is None:
            return
        key = self._down_key
        self._down_key = None
        win32api.mouse_event(self._EVENTS.get(key, self._EVENTS["left"])[1], 0, 0, 0, 0)
        self.mark_script_input()

    def scroll(self, x, y, scroll_amount):
        if not self._ready_to_act():
            return
        self._move_cursor(x, y)
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(scroll_amount) * 120, 0)
        self.mark_script_input()

    def move(self, x, y):
        # 2026-11-01：删掉没用过的 `down_btn` 参数（vulture 报的唯一一处真死参数，
        # 全仓无调用点传它）。其余 move_back / settle_time 是 ok 框架
        # `interaction_methods/base.py` 的签名契约，必须留着 —— 见 .vulture_whitelist.py
        if not self._ready_to_act():
            return
        self._move_cursor(int(x), int(y))
        self.mark_script_input()

    def swipe(self, x1, y1, x2, y2, duration=0.3, settle_time=0):
        """按住拖动（真实鼠标），用于列表/视角。

        duration 的单位是**毫秒**，不是秒：ok 框架的 `task.swipe(duration=0.4)`
        会先算 `ms = int(duration * 1000)` 再传进来（task.py:280）。2026-10-30
        实测：把 400 当秒用，12 步每步 33 秒 → 翻一页 6 分 42 秒。这里按毫秒
        解析，并兼容直接传秒的老调用（<5 视为秒）。

        节奏对齐实测正常的 `tools/collect_ssr.py:Screen.swipe`：按住后停 0.10s
        让游戏判定成拖动，然后匀速走 24 步（每步不额外 settle），松手前停 0.05s。

        拖拽中途每 6 步看一眼真人输入：用户抢过鼠标就提前松手（finally 里松），
        否则整段拖拽期间用户完全没法拿回指针。
        """
        total = float(duration)
        if total > 5:                       # 框架传的是毫秒
            total /= 1000.0
        total = max(total, 0.05)

        if not self._ready_to_act():
            return
        self._move_to(x1, y1)
        time.sleep(0.05)
        win32api.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        steps = 24
        step_delay = max(total / steps, 0.006)
        try:
            time.sleep(0.10)
            for i in range(1, steps + 1):
                nx = int(x1 + (x2 - x1) * i / steps)
                ny = int(y1 + (y2 - y1) * i / steps)
                self._move_to(nx, ny, settle=0)
                time.sleep(step_delay)
            time.sleep(0.05)
        finally:
            win32api.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            self.mark_script_input()

    # ---------- 键盘 ----------

    def _vk(self, key):
        name = str(key).upper()
        if name in vk_key_dict:
            return vk_key_dict[name]
        return win32api.VkKeyScan(str(key)) & 0xFF

    def send_key_down(self, key):
        if not self._ready_to_act():
            return
        vk = self._vk(key)
        win32api.keybd_event(vk, 0, 0, 0)
        self._pressed_keys[str(key).lower()] = self._pressed_keys.get(str(key).lower(), 0) + 1
        self.mark_script_input()

    def send_key_up(self, key):
        # 抬起永远不拦：停止/异常时必须能把按住的键松开
        name = str(key).lower()
        if self._pressed_keys.get(name, 0) <= 0:
            return
        self._pressed_keys[name] -= 1
        vk = self._vk(key)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        self.mark_script_input()

    def send_key(self, key, down_time=0.02):
        self.send_key_down(key)
        time.sleep(down_time)
        self.send_key_up(key)

    def input_text(self, text, **kwargs):
        if not self._ready_to_act(need_foreground=False):
            return
        for ch in str(text):
            vk = win32api.VkKeyScan(ch) & 0xFF
            win32api.keybd_event(vk, 0, 0, 0)
            time.sleep(0.01)
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        self.mark_script_input()
