"""策略参数表的唯一读取入口。

分层：

    L0 机制常数   constants.py 里不会因打法改变的值
    L1 策略参数   src/tasks/training/strategy/params.json（本模块读）
    L2 用户配置   configs/TrainingTask.json（config_spec 声明）

业务代码只调 `get()` 或属性式访问（`params.scoring.manager`）；找不到就抛
`KeyError` / `AttributeError`，不要在调用处写默认值 —— 那等于把参数又写回代码。
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


class _Node:
    """属性式访问的中间节点：`params.rest.stamina_threshold`。

    为什么要有：点分字符串在调用处看不见键名，agent 改代码时容易再写一个
    同义常量；属性式访问能被 IDE/补全提示出来（2026-11-15）。
    """

    __slots__ = ("_data",)

    def __init__(self, data):
        self._data = data

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        try:
            value = self._data[key]
        except KeyError:
            raise AttributeError("strategy param not found: %s" % key)
        return _Node(value) if isinstance(value, dict) else value

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        value = self._data.get(key, default)
        return _Node(value) if isinstance(value, dict) else value


def __getattr__(name):
    """模块级属性访问：`params.scoring.manager`（键不存在时 AttributeError）。"""
    if name.startswith("_"):
        raise AttributeError(name)
    _load()
    try:
        value = _params[name]
    except KeyError:
        raise AttributeError("strategy param not found: %s" % name)
    return _Node(value) if isinstance(value, dict) else value
