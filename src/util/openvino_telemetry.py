"""关掉 OpenVINO 遥测，避免关闭窗口时进程退不掉。

为什么需要（2026-11-15 实跑，v0.1.20 → v0.2.0 升级失败）：
- openvino_telemetry 用 ThreadPoolExecutor 异步发 GA4 事件，HTTP 连接没有超时；
  国内网络连不上 Google Analytics 时，工作线程卡在 socket.connect。
- Python 退出时 `concurrent.futures` 的 atexit 钩子会无超时 join 这个线程，主线程
  被固定住；退出看门狗随后执行 `os._exit()`，但 ExitProcess 停在 DLL 清理阶段，
  进程只剩 1 个线程永远不退。
- 实测：关闭窗口后残留 12 小时（PID 43368），launcher 因为「应用仍在运行」无法
  更新到 v0.2.0；线程栈见安装版 `logs/shutdown_thread_dumps.txt`。
- consent 文件内容为 "0" 时 openvino_telemetry 判为 DECLINED，不再发送任何事件，
  线程池也就不会提交任务（见 openvino_telemetry/main.py 的 init）。
- 这只是关统计，不影响 OpenVINO 推理本身。

不改 .venv 里的 openvino_telemetry：重装/升级会被覆盖，改动留不住。
用法：由 `src/config.py` 在导入时调用一次（必须在 OCR 初始化之前）。
"""

import os

# openvino_telemetry 的 opt-in 标记：%LOCALAPPDATA%\Intel Corporation\openvino_telemetry
_CONSENT_RELATIVE = os.path.join('Intel Corporation', 'openvino_telemetry')


def disable_openvino_telemetry():
    """写入 opt-out 标记，让 openvino_telemetry 不再发送 GA4 事件。

    写失败只是回到旧行为（退出时可能卡住），不能让启动失败。
    """
    local_app_data = os.environ.get('LOCALAPPDATA')
    if not local_app_data:
        return
    path = os.path.join(local_app_data, _CONSENT_RELATIVE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='ascii') as file:
            file.write('0')
    except OSError:
        pass
