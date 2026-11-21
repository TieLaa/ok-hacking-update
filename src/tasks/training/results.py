"""每局训练结果的记录与汇总（2026-11-21）。

- 数据源：`handlers/match.handle_training_score` 读到的评分/评级；
- 单队长模式也要记（不再依赖 `CaptainQueue`）；
- 停止时写 `logs/reports/training_results_<run>.json/.txt`，GUI 额外弹汇总；
- MCP 只写文件，不弹模态（会卡住 runner）。
"""

import json
import os
import time

from src.tasks.training.constants import PROJECT_ROOT


def _captain(task):
    name = (getattr(task, "_style_name", "") or "").strip()
    if name:
        return name
    try:
        return (task.configured_captain() or "").strip() or "接手局"
    except Exception:                                # noqa: BLE001
        return "接手局"


def _target(task):
    queue = getattr(task, "_queue", None)
    if queue is not None:
        return queue.target
    try:
        return int(task.config.get("目标分") or 0) or None
    except (AttributeError, TypeError, ValueError):
        return None


def begin_run(task):
    """一次 run 开始：清空上一轮结果，定下本次文件名。"""
    task._run_results = []
    task._results_run_id = time.strftime("%Y%m%d-%H%M%S")
    task._results_done = False


def record(task, score, rating=None, persist=True):
    """记一局；同一局重复调用会更新（后到的非空字段覆盖）。

    只对 TrainingTask 生效：`_run_results` 由 `begin_run`/任务初始化建好；
    QuickMatchTask 这类共用 handler 的任务不记（没有这个列表）。

    `persist=True` 时**每局立刻落盘**：用户直接关窗口/进程退出，
    已完成的局也不会丢（2026-11-22 实测第二轮就是关窗口结束的）。
    """
    if score is None:
        return None
    rounds = getattr(task, "_run_results", None)
    if rounds is None:
        return None
    if getattr(task, "_results_run_id", None) is None:
        task._results_run_id = time.strftime("%Y%m%d-%H%M%S")
    round_no = int(getattr(task, "_round_no", 0) or 0) or (len(rounds) + 1)
    target = _target(task)
    met = score >= target if target else None
    item = {
        "round": round_no,
        "captain": _captain(task),
        "score": int(score),
        "rating": rating,
        "target": target,
        "met": met,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    for existing in rounds:
        if existing.get("round") == round_no:
            existing.update({k: v for k, v in item.items() if v is not None})
            if persist:
                write(task)
            return existing
    rounds.append(item)
    if persist:
        write(task)
    return item


def summary_lines(task):
    rounds = sorted(getattr(task, "_run_results", None) or [],
                    key=lambda item: item.get("round", 0))
    lines = []
    for item in rounds:
        rating = f"（{item['rating']}）" if item.get("rating") else ""
        target = item.get("target")
        if target:
            verdict = "达标" if item.get("met") else "未达标"
            goal = f" / 目标 {target} → {verdict}"
        else:
            goal = ""
        lines.append(f"第{item.get('round', '?')}局 {item.get('captain', '?')} "
                     f"评分 {item.get('score', '?')}{rating}{goal}")
    return lines


def summary_text(task):
    lines = summary_lines(task)
    if not lines:
        return "本次运行没有完成任何一局"
    return "本次运行训练结果：\n" + "\n".join(lines)


def write(task, folder=None):
    """写 JSON + 文本；返回 (json_path, txt_path)。"""
    folder = (folder or getattr(task, "_results_folder", None)
              or os.path.join(PROJECT_ROOT, "logs", "reports"))
    os.makedirs(folder, exist_ok=True)
    run_id = getattr(task, "_results_run_id", None) or time.strftime("%Y%m%d-%H%M%S")
    json_path = os.path.join(folder, f"training_results_{run_id}.json")
    txt_path = os.path.join(folder, f"training_results_{run_id}.txt")
    rounds = getattr(task, "_run_results", None) or []
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"run_id": run_id, "results": rounds}, f,
                      ensure_ascii=False, indent=2)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(summary_text(task) + "\n")
    except OSError as e:
        task.log_warning(f"训练结果写盘失败（{type(e).__name__}: {e}）")
        return None, None
    return json_path, txt_path


def finish_run(task, folder=None):
    """run 收尾：写文件 + GUI 弹汇总。只执行一次。"""
    if getattr(task, "_results_done", False):
        return None
    rounds = getattr(task, "_run_results", None) or []
    if not rounds:
        task._results_done = True
        return None
    task._results_done = True
    json_path, txt_path = write(task, folder)
    text = summary_text(task)
    for line in summary_lines(task):
        task.log_info(line)
    if json_path:
        task.log_info(f"训练结果已保存：{json_path}")
    if getattr(task, "mcp_driven", False):
        task.log_info(text)
    else:
        detail = f"\n\n明细：{json_path}" if json_path else ""
        task.alert("训练结果", text + detail)
    return json_path, txt_path
