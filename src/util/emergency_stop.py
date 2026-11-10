"""全局紧急停止热键。

为什么要自己再做一个：GirlsInteraction 每次点击都会把**物理光标**移到目标位置，
脚本跑起来以后人手基本抢不到鼠标，必须有一个不依赖界面焦点的中断键。

ok-script 自带 `Start/Stop`（默认 F9），但它有两个坑：

1. `RegisterHotKey` 是全局独占的。同时开着 ok-ef 这类默认同样用 F9 的程序时，
   只有一个能注册成功，另一个静默失败（日志里是 `Failed to register hotkey`）；
2. 它靠 GUI 线程的 `PeekMessageW` 轮询，GUI 忙/卡住时按了没反应。

这里自己开一条线程：`RegisterHotKey` + `GetMessageW` 阻塞等待，
命中后只置一个 `threading.Event`，任务主循环自己检查并退出。
热键线程里**不碰**任何 Qt 对象，避免跨线程调用。

按键是**按候选顺序抢**的（F12 经常被覆盖层/浏览器 DevTools 占掉，
实测本机 F12 = 1409 已被占用），抢到哪个日志里会写清楚。
"""

import ctypes
import ctypes.wintypes as wintypes
import threading
import time

from ok import Logger

logger = Logger.get_logger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
WM_HOTKEY = 0x0312
_HOTKEY_ID = 0xB601          # 0x0000~0xBFFF 之间，避开 ok-script 自带的 999

# **只用三键组合**（2026-10-27 二次收紧）：
#   1) 单键 F8/F12 会被别的东西按下（一次是猫踩键盘）；
#   2) 只按 Ctrl+Alt+F12 仍然会误停 —— 因为点击时 GirlsInteraction 会
#      `keybd_event(VK_MENU)` 注入一次 Alt，如果这时用户恰好按了 F12
#      （Steam 截图默认就是 F12），RegisterHotKey 立刻满足 Ctrl+Alt+F12。
#      19:27:36 那次误停就是「注入的 Alt + 用户 F12」撞出来的。
# 所以注册键要求再多按住 Shift：注入的 Alt 单靠自己凑不齐，必须真人三键同按。
DEFAULT_KEYS = (
    (MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x7B, "Ctrl+Alt+Shift+F12"),
)

# 轮询兜底要求 Ctrl+Alt 连续按住 3 拍（150ms），注入的 Alt（约 20ms）凑不满，
# 用户单按 F12 也不会触发。

# 轮询兜底：RegisterHotKey 是全局独占的，F8 被别的程序抢走时注册会静默失败。
# GetAsyncKeyState 不占用按键、也不需要注册，别的程序占了照样能读到物理按下。
# 2026-10-26 实机：F8 被占用后只抢到 Ctrl+Alt+F12，用户按 F8 完全没反应。
# 这里同样**要求 Ctrl+Alt 同时按下**，单按 F8/F12 一律忽略。
POLL_KEYS = ((0x7B, "F12"), (0x77, "F8"))
POLL_MODS = ((0x11, "Ctrl"), (0x12, "Alt"))

# 轮询必须连续 N 拍为按下才算真按：0.05s 单拍噪声（手柄/其他软件转发按键、
# GetAsyncKeyState 的瞬时抖动）会在用户没按键时误触发。2026-10-27 09:47 实机
# 误报一次，任务白停。真按住一般 ≥200ms，3 拍（~150ms）不会漏。
POLL_CONFIRM_TICKS = 3


class EmergencyStop:
    """一个可复用的“紧急停止”信号。"""

    def __init__(self, keys=DEFAULT_KEYS):
        self.keys = tuple(keys)
        self.event = threading.Event()
        self.ready = threading.Event()   # 注册尝试结束（成功或失败）
        self.label = None           # 真正抢到的按键名
        self.last_press = None      # 最后一次实际触发的按键（轮询可能和注册键不同）
        self._thread = None
        self._poll_thread = None
        self._lock = threading.Lock()
        self.registered = None      # None=还没试 / True / False

    # ---------- 对外 ----------

    def ensure_started(self, wait=1.0):
        """启动热键线程（并等一下注册结果）；注册失败只记警告，不影响任务本身。"""
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._listen, name="EmergencyStopHotkey", daemon=True)
                self._thread.start()
            if self._poll_thread is None:
                self._poll_thread = threading.Thread(
                    target=self._poll, name="EmergencyStopPoll", daemon=True)
                self._poll_thread.start()
        # 等注册尝试结束再返回，避免 run() 立刻读 label 读到 None（旧版会误报“未注册成功”）
        self.ready.wait(wait)
        return self.registered

    def consume(self):
        """任务循环里调用：这次按过键就返回 True（并清掉标记）。"""
        if self.event.is_set():
            self.event.clear()
            return True
        return False

    def request(self):
        """模拟一次按键（测试 / 外部兜底用）。"""
        self.event.set()

    # ---------- 热键线程 ----------

    def _listen(self):
        user32 = ctypes.windll.user32
        for mods, vk, label in self.keys:
            try:
                if user32.RegisterHotKey(None, _HOTKEY_ID, mods, vk):
                    self.label = label
                    self.registered = True
                    logger.info(f"紧急停止热键已就绪：{label}")
                    break
            except Exception as e:                  # noqa: BLE001
                logger.debug(f"热键 {label} 注册异常: {type(e).__name__}: {e}")
        self.ready.set()
        if not self.registered:
            logger.warning("紧急停止热键全部被占用；F8/F12 轮询兜底仍然可用")

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    logger.warning(f"按下 {self.label} → 请求停止")
                    self.last_press = self.label
                    self.event.set()
        finally:
            try:
                user32.UnregisterHotKey(None, _HOTKEY_ID)
            except Exception:                       # noqa: BLE001
                pass

    def _poll(self):
        """GetAsyncKeyState 轮询兜底：不注册、不独占，别的程序占了也能收到。

        只认 Ctrl+Alt+F8 / Ctrl+Alt+F12：单键会被别的程序或宠物误按（实测两次整局
        被误停，其中一次是猫踩键盘），所以必须同时按住两个修饰键。
        """
        user32 = ctypes.windll.user32
        counts = {vk: 0 for vk, _ in POLL_KEYS}
        while True:
            time.sleep(0.05)
            try:
                mods_down = all(user32.GetAsyncKeyState(vk) & 0x8000
                                for vk, _ in POLL_MODS)
                for vk, label in POLL_KEYS:
                    if mods_down and user32.GetAsyncKeyState(vk) & 0x8000:
                        counts[vk] += 1
                        if counts[vk] == POLL_CONFIRM_TICKS:
                            combo = "+".join(m for _vk, m in POLL_MODS) + "+" + label
                            logger.warning(f"按住 {combo}（轮询兜底 {counts[vk]} 拍）→ 请求停止")
                            if not self.label:
                                self.label = combo
                            self.last_press = combo
                            self.event.set()
                        elif counts[vk] < POLL_CONFIRM_TICKS:
                            logger.debug(f"{label} 单拍按下（{counts[vk]} 拍），等确认")
                    else:
                        counts[vk] = 0
            except Exception as e:                  # noqa: BLE001
                logger.debug(f"轮询热键异常: {type(e).__name__}: {e}")
                time.sleep(1)


# 全局单例：任务和工具脚本共用，谁先启动谁注册
STOP = EmergencyStop()
