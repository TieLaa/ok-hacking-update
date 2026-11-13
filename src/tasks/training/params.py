"""策略参数表的唯一读取入口。

分层：

    L0 机制常数   constants.py 里不会因打法改变的值
    L1 策略参数   src/tasks/training/strategy/params.json（本模块读）
    L2 用户配置   configs/TrainingTask.json（config_spec 声明）

业务代码只调 `get()`；找不到就抛 `KeyError`，不要在调用处写默认值
——那等于把参数又写回代码。
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRATEGY_DIR = os.path.join(_HERE, "strategy")

_MISSING = object()

_params = None


def _load():
    global _params
    if _params is None:
        with open(os.path.join(_STRATEGY_DIR, "params.json"),
                  encoding="utf-8") as f:
            _params = json.load(f)


def reload():
    """测试/调试用：丢掉缓存，下次读取重新加载。"""
    global _params
    _params = None


def get(path, default=_MISSING):
    """按点分路径读策略参数，例如 `get("vacation.stamina_target")`。

    找不到时：给了 `default` 就返回它，否则抛 `KeyError`。
    """
    _load()
    node = _params
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            if default is not _MISSING:
                return default
            raise KeyError("strategy param not found: %s" % path)
        node = node[part]
    return node
