"""放大重读：把读不准的那一小块裁出来放大 N 倍，再跑一次 OCR。

**为什么有用**（两次实测）：OCR 出错多半不是「认错字」，是**检测框把边界划错了**：

    「星期一」 → 「星期」/「期日」   「一」只有一条横线，被并进「期」的框里（丢字率 67.7%）
    「B1016」  → 8116                评级 B 并进首位，B 的两个弧 + 1 的一竖 ≈ 8
    「3 488」  → 3488                等级数字粘在数值前面

放大之后检测阶段会**重新切框**，这些并字/漏字大多自己消失。实测效果：
整帧读不出的 117 帧「星期几」，x3 放大重读救回 **116 帧（99.1%）**；
2026-11-04 六维面板 `体能 8116`（真值 1016）也是同一个病根。

**为什么单独一个模块**：这是**一种技术机制**（裁 → 放大 → 重跑 → 换算），
不是某个业务判据的一部分。按 AGENTS「同一机制归口一处」，谁需要谁调，
不要在 parsers / handlers 里各写一套（`ui/topbar_day.py` 之前就是自己写了一份）。

**两条使用约定**：

1. 放大重读只用来**读值**，不用来找点击位置 —— 缩放后的坐标换算最容易错，
   要点击请继续用整帧 OCR 出来的框；
2. 只在**读不准时**才调（读不出来 / 超上限 / 丢字），不要每拍都跑：
   一次多花约 0.1~0.2 秒（裁得越小越便宜）。

用法：

    from src.tasks.ui import zoom_reread

    # 归一化区域（Defs.region 那种）
    texts = zoom_reread.reread_region(frame, self, (0.092, 0.0, 0.192, 0.075))

    # 像素区域（某个 OCR 框周围，六维每一格就是这么读的）
    texts = zoom_reread.reread_px(frame, self, (x1, y1, x2, y2))

    for name, score in texts: ...

`task` 传能跑 OCR 的东西：ok-script 的任务实例（`self.ocr(box=..., frame=...)`）、
OCR 引擎对象（`engine.ocr(img)`）、或直接一个 `fn(img)` 都兼容 ——
所以 `tools/check_topbar_day*.py` 那种离线脚本也能用同一套代码。
"""

import cv2

# 放大倍数：实测 x3 最好（星期几 1031 帧 100% 与整帧一致；x4 起开始丢，x6 连「星期二」都掉）。
ZOOM = 3.0
# 裁框外扩（占裁框尺寸的比例）：留一点余量，别把首/尾字符切掉。
PAD = 0.02
# 只信置信度够的结果：区域重读会放大噪声，低置信度的框往往是碎片。
MIN_SCORE = 0.30


def crop_px(frame, x1, y1, x2, y2, pad=0.0):
    """按**像素**裁一块（自动夹到画面内），返回 ndarray 或 None。"""
    if frame is None or not getattr(frame, "size", 0):
        return None
    h, w = frame.shape[:2]
    if pad:
        dx, dy = int((x2 - x1) * pad), int((y2 - y1) * pad)
        x1, x2 = x1 - dx, x2 + dx
        y1, y2 = y1 - dy, y2 + dy
    x1 = max(0, min(w - 1, int(x1)))
    y1 = max(0, min(h - 1, int(y1)))
    x2 = max(x1 + 1, min(w, int(x2)))
    y2 = max(y1 + 1, min(h, int(y2)))
    out = frame[y1:y2, x1:x2]
    return out if getattr(out, "size", 0) else None


def crop_region(frame, region, pad=0.0):
    """按**归一化**区域 `(x1, y1, x2, y2)` 裁一块。"""
    if frame is None or not getattr(frame, "size", 0):
        return None
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = region
    return crop_px(frame, w * x1, h * y1, w * x2, h * y2, pad=pad)


def enlarge(img, scale=ZOOM):
    """放大（CUBIC 插值）。scale<=1 原样返回。"""
    if img is None or not getattr(img, "size", 0) or scale <= 1:
        return img
    return cv2.resize(img, None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_CUBIC)


def run_ocr(ocr, img, min_score=MIN_SCORE):
    """对一张图跑 OCR，返回 `[(文本, 置信度), …]`。

    兼容三种调用形态（见模块 docstring）：任务实例 / OCR 引擎 / 普通函数。
    """
    if ocr is None or img is None or not getattr(img, "size", 0):
        return []
    attempts = []
    engine_call = getattr(ocr, "ocr", None)
    if callable(engine_call):
        attempts.append(lambda: engine_call(box=None, frame=img))
        attempts.append(lambda: engine_call(img))
    if callable(ocr):
        attempts.append(lambda: ocr(box=None, frame=img))
        attempts.append(lambda: ocr(img))
    result = None
    for attempt in attempts:
        try:
            result = attempt()
            break
        except TypeError:
            continue                            # 签名对不上，换下一种
        except Exception:                       # noqa: BLE001
            return []
    out = []
    entries = result or []
    # onnxocr 返回的是「页 → [框, (文本, 得分)]」两层；ok-script 的 self.ocr 返回
    # 扁平 Box 列表。这里两种都吃，免得离线脚本要自己包一层。
    if (len(entries) == 1 and isinstance(entries[0], (list, tuple))
            and entries[0] and isinstance(entries[0][0], (list, tuple))):
        entries = entries[0]
    for entry in entries:
        name = getattr(entry, "name", None)
        score = getattr(entry, "confidence", None)
        if name is None:
            name, score = _text_score(entry)
        if not name:
            continue
        try:
            score = float(score) if score is not None else 1.0
        except (TypeError, ValueError):
            score = 1.0
        if score >= min_score:
            out.append((str(name).strip(), score))
    return out


def _text_score(entry):
    """把一条 OCR 结果解成 `(文本, 得分)`：兼容 `(text, score)` 和 `[box, (text, score)]`。"""
    if not isinstance(entry, (list, tuple)):
        return None, None
    if len(entry) >= 2 and isinstance(entry[0], str):
        return entry[0], entry[1]
    if len(entry) >= 2:
        inner = entry[1]
        if isinstance(inner, (list, tuple)) and len(inner) >= 2 and isinstance(inner[0], str):
            return inner[0], inner[1]
        if isinstance(inner, str):
            return inner, None
    return None, None


def reread_px(frame, ocr, px_region, scale=ZOOM, pad=PAD, min_score=MIN_SCORE):
    """像素区域 → 放大重读 → `[(文本, 置信度), …]`。裁不出来返回 []。"""
    x1, y1, x2, y2 = px_region
    return run_ocr(ocr, enlarge(crop_px(frame, x1, y1, x2, y2, pad=pad), scale),
                   min_score=min_score)


def reread_region(frame, ocr, region, scale=ZOOM, pad=PAD, min_score=MIN_SCORE):
    """归一化区域 → 放大重读 → `[(文本, 置信度), …]`。"""
    return run_ocr(ocr, enlarge(crop_region(frame, region, pad=pad), scale),
                   min_score=min_score)
