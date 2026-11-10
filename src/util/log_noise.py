"""按「日志消息前缀」丢弃刷屏的 DEBUG 日志。

为什么需要（2026-11-10，用户报「一直刷 debug 太烦」）：
- `ok/task/TaskExecutor.py:get_task_by_class()` 每被调用一次就
  `logger.debug(f'get_task_by_class {cls} {self.onetime_tasks} {self.trigger_tasks}')`，
  把整个任务列表 repr 打出来。调度循环约 0.5 秒调一次，`main_debug.py`
  （`config['debug'] = True`）下控制台会被这一行完全刷满，读别的日志都费劲。
- 这一行对排查没有增量信息（对象地址无意义），所以只丢它，**不把整体级别
  抬到 INFO** —— 其他 debug 日志（如存证失败原因、抢前台尝试）仍然保留。
- 不改 `.venv` 里的 ok 包：重装/升级会被覆盖，改动留不住。

为什么按消息而不是按函数名过滤：ok 的 `Logger.debug()`（`ok/util/logger.py`）
是薄包装，日志记录里的 `funcName` 永远是 `debug`，拿不到真实调用方，
只能匹配消息前缀。消息由 `Logger` 自动加 `类名:` 前缀，所以带上了
`TaskExecutor:`。

用法：由 `src/config.py` 在导入时调用一次（见那里的注释），不要在各入口重复调用。

落点为什么在 `src/config.py` 而不是三个入口（2026-11-10 第二次接线）：
- `main.py` / `main_debug.py` / `main_gui_mcp.py` 以及 `python -m ok xxx
  --config src.config:config` **全部**会 import `src.config`，一处覆盖所有启动路径，
  也顺带管住 `ok run_task -d` 这类不走入口脚本的调试启动。
- 第一次接在三个入口时，改动还没提交就被另一会话的 `git reset --hard`
  一起冲掉了（跟踪文件回滚、本模块因未跟踪而幸存），表现为「模块在、接线没了」。
  接在单点并随提交落盘，避免同一种失效再来一次。
"""

import logging

# ok 的 Logger 把所有日志发到 "ok" logger，消息形如
# "TaskExecutor:get_task_by_class <class ...> [<...>] [<...>]"。
_QUIET_PREFIXES = ("TaskExecutor:get_task_by_class ",)


class _DropNoisyDebug(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno > logging.DEBUG:
            return True
        message = record.getMessage()
        return not message.startswith(_QUIET_PREFIXES)


def install_quiet_filters() -> None:
    logger = logging.getLogger("ok")
    if any(isinstance(f, _DropNoisyDebug) for f in logger.filters):
        return
    logger.addFilter(_DropNoisyDebug())
