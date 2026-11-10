"""win32：把窗口真正置前 —— 项目里**唯一**一份实现。

为什么单独一层
    这段「Alt 注入 → AttachThreadInput → 置顶再撤回」的加码逻辑有两处要用：
      - `src/interaction/GirlsInteraction.py`：每次点击/按键前（**带**"等用户空闲"策略）
      - `tools/abs_mouse.py`：独立脚本（没有 ok 框架，也**不**等用户）
    放在这里两边都能 import，而且本模块只依赖 pywin32，不依赖 ok-script。

为什么不带策略
    "什么时候允许抢前台"是调用方的策略：GirlsInteraction 要先等用户停手、检查
    暂停/停止，工具脚本不需要。这一层只管"怎么抢"，不管"该不该抢"。

背景（2026-10-25 实测）
    Windows 默认禁止后台进程抢前台：`SetForegroundWindow` 会**静默失败**（返回成功但
    窗口没上来），于是鼠标/键盘事件全打到别的窗口。实测：ok-script GUI 自己弹到前台时，
    点「开始」变成点 GUI，游戏界面毫发无伤 —— 表现就是"点在没反应的地方"。

    也不要改用「最小化再恢复」抢前台：前台是管理员进程时恢复会失败，窗口会反复
    visible=False，WGC 抓不到帧、点击全部落空（实训 2026-10-26）。
"""

import logging
import time

import win32api
import win32con
import win32gui
import win32process

logger = logging.getLogger(__name__)

FOREGROUND_WAIT = 0.15   # 置顶/挂线程之后给系统一点时间真正切换前台


def force_foreground(hwnd, tries=4, wait=FOREGROUND_WAIT, log=None, on_alt_injected=None):
    """把 `hwnd` 真正置前；抢不到返回 False —— **调用方不能硬点**。

    三重保险，逐个加码：
      1. 模拟按一下 Alt —— 让系统认为"用户有输入意图"；
      2. `AttachThreadInput` 把当前线程挂到前台窗口的输入队列，绕过前台锁；
      3. `SetWindowPos(HWND_TOPMOST)` 再撤回，强行上浮。

    Args:
        hwnd: 目标窗口。
        tries: 每轮三种手段都试一遍，最多几轮。
        wait: 每轮末尾等多久再看一次前台（秒）。
        log: 日志对象，默认本模块 logger（调用方通常传 ok 的 Logger）。
        on_alt_injected: 注入过 Alt 时回调一次。GirlsInteraction 用它给「让路」
            记账（`mark_script_input`）—— 注入的 Alt 也是系统输入事件，不记账的话
            下一次动作会把它当成「用户还在操作」，白等 `user_idle_wait` 秒。

    Returns:
        bool：True = 已经是前台；False = 放弃（调用方应报错或等用户点一下游戏）。
    """
    log = log or logger

    if not hwnd:
        return False
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    injected = False
    try:
        for attempt in range(tries):
            fg = win32gui.GetForegroundWindow()

            # 1) Alt 技巧（走到这里说明游戏已经不在前台）
            log.debug(f"抢前台 attempt {attempt}：注入 Alt + SetForegroundWindow")
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            injected = True
            time.sleep(0.02)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception as e:
                log.debug(f"SetForegroundWindow attempt {attempt}: {e}")
            finally:
                # Alt 必须抬起，否则后续所有按键都会变成 Alt 组合键
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)

            if win32gui.GetForegroundWindow() == hwnd:
                return True

            # 2) AttachThreadInput 兜底
            try:
                cur_tid = win32api.GetCurrentThreadId()
                fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
                if fg_tid and fg_tid != cur_tid:
                    win32process.AttachThreadInput(cur_tid, fg_tid, True)
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                    finally:
                        win32process.AttachThreadInput(cur_tid, fg_tid, False)
            except Exception as e:
                log.debug(f"AttachThreadInput attempt {attempt}: {e}")

            if win32gui.GetForegroundWindow() == hwnd:
                return True

            # 3) 置顶再撤回
            try:
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
                win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
            except Exception as e:
                log.debug(f"SetWindowPos attempt {attempt}: {e}")

            time.sleep(wait)
            if win32gui.GetForegroundWindow() == hwnd:
                return True

        return False
    finally:
        if injected and on_alt_injected is not None:
            on_alt_injected()


def restore_if_minimized(hwnd, settle=0.2):
    """最小化就先恢复，再抢前台。返回是否做过恢复。"""
    if hwnd and win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(settle)
        return True
    return False
