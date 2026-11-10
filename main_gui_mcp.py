"""GUI 版 ok-script + 本机 MCP 桥（相当于「main_debug.py + --mcp」）。

为什么需要这个文件（2026-11-02）：
- `docs/MCP_DEV.md` 里写着 `python main_debug.py --mcp`，但 `main_debug.py` 直接
  `ok.OK(config).start()`，**不解析命令行参数** —— `--mcp` 是 `ok gui` 子命令的参数
  （`ok/cli.py:113`），对 main_debug.py 无效。MCP 开关的真源头是
  `config['mcp']['enabled']` 或全局设置 `ENABLE_MCP_SERVER`
  （`ok/__init__.py: start_mcp_server`）。
- 于是「GUI 挂着跑」和「MCP 热重载 / 驱一段」过去只能二选一，本文件把两者合起来：
  GUI 照常用，同时 `logs/mcp/connections/<pid>-<port>.json` 有记录，
  `python tools/mcp/client.py --reload` 就能改完源码立刻生效。

必须**管理员**运行（游戏进程是管理员权限，非管理员发不进消息）。

    powershell -Command "Start-Process -FilePath '.venv\\Scripts\\python.exe' `
        -ArgumentList 'main_gui_mcp.py' -WorkingDirectory '<repo>' -Verb RunAs"

注意别放在 tools/ 下跑：`python tools/xxx.py` 会把 sys.path[0] 设成 tools/，
  里面的 `click.py`／`click_text.py` 会**顶掉** PyPI 的 click，
  MCP 依赖链（uvicorn → click.Choice）直接报 `module 'click' has no attribute 'Choice'`。
"""

import ok

from src.config import config

if __name__ == '__main__':
    config['debug'] = True
    config['mcp'] = {**(config.get('mcp') or {}), 'enabled': True}
    ok = ok.OK(config)
    ok.start()
