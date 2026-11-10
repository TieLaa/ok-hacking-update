"""日志文件兜底：ok 自己的 FileHandler 缺席时，由本模块接管写 `logs/run.log`。

为什么需要（2026-11-10 排查「刷新后多了一下点击」时发现）：
- GUI（`main_debug.py`）启动时会经 `ok.util.logger.config_logger()` 打开
  `logs/ok-script.log`，**启动那几行确实写进去了**；
- 但从任务开始跑起，文件里再没有一条日志 —— 用独占方式打开该文件**成功**，
  说明运行期间没有任何进程持有它的句柄，即 ok 的 FileHandler 已被关闭；
- UI 面板照常显示日志（`CommunicateHandler` 走的是内存事件），所以界面上看不出异常；
- 后果：出问题只能靠 `logs/clicks.jsonl` + `logs/events/*.json` 反推，
  这一拍到底 OCR 到了什么（这次「多点了大厅训练」的疑点）根本查不到。

口径（同 `src/util/log_noise.py`）：
- 不改 `.venv` 里的 ok 包（重装/升级即失效）；
- ok 的 FileHandler **在** → 什么都不做，不产生第二份日志；
- 缺席 → 加一个 `RotatingFileHandler` 写 `logs/run.log`，并把当时的 handler
  清单写进去 —— 这就是「ok 的文件日志失效」的永久证据，下次再出问题先看这行。

调用点：`flow.run_one_round()` 开头（GUI / MCP / CLI 三条入口都会经过）。
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from src.tasks.training.constants import PROJECT_ROOT

# 兜底日志：和 ok 的 ok-script.log 分开，避免两边同时生效时互相覆盖。
RUN_LOG = os.path.join(PROJECT_ROOT, "logs", "run.log")

_FORMAT = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s")


def ensure_run_log():
    """检查 `ok` logger 有没有文件 handler；没有就补一个。返回是否补了。

    幂等：本模块加过（handler 上打了 `_ok_fallback` 标记）就不再重复加。
    """
    logger = logging.getLogger("ok")
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            return False                    # ok 自己的文件日志在 → 不插手
        if getattr(handler, "_ok_fallback", False):
            return False                    # 本模块已经加过
    try:
        os.makedirs(os.path.dirname(RUN_LOG), exist_ok=True)
        handler = RotatingFileHandler(RUN_LOG, maxBytes=8 * 1024 * 1024,
                                      backupCount=3, encoding="utf-8")
    except OSError:
        return False
    handler._ok_fallback = True
    handler.setFormatter(_FORMAT)
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    names = [type(h).__name__ for h in logger.handlers if h is not handler]
    logger.info(f"日志兜底：ok 的 FileHandler 缺席（现有 handlers={names or '空'}）"
                f"→ 本次任务日志改写 {RUN_LOG}")
    return True
