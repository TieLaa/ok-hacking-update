"""准备页槽位检测：只读图像，不点击。

标定帧 1548x871（`tests/fixtures/prep/`）。三个页面：
    队友 4 槽（队长另占底部中间，不在这里）
    笔记 2 槽
    器材 5 槽

空/满只看槽位里有没有 `+` 形状 —— **颜色不作为判据**：
黄色是「当前待选」高亮，空/满都可能黄；白/青/黄都算空。
器材页额外看拐角：`+` 是直角十字（~90°），卡片上的四角星尖端 ~30°，不算空位。
"""

import cv2
import numpy as np

# 标定帧尺寸（`tests/fixtures/prep/*.jpg`）
_CAL_W = 1548.0
_CAL_H = 871.0

# (x, y, w, h) 帧坐标（1548x871），检测时按实际帧缩放
_SLOTS = {
    "team": (
        (219, 72, 34, 30),
        (529, 50, 32, 34),
        (253, 357, 43, 40),
        (751, 209, 34, 36),
    ),
    "note": (
        (204, 333, 68, 68),
        (769, 334, 67, 67),
    ),
    "equipment": (
        (171, 527, 55, 55),
        (333, 526, 55, 54),
        (496, 526, 55, 54),
        (659, 526, 55, 54),
        (822, 526, 55, 54),
    ),
}

# 检测框四周的余量（标定帧像素）：躲 OCR/点击带来的几像素漂移
_PAD = 10

# 各页 `+` 的实际颜色和大小（宽度占比，随分辨率缩放）。
# 白色只用于团队页，避免把笔记/器材的白色卡面误当十字；
# 大小范围按标定帧实测：团队 34px、笔记 68px、器材 55px（1548 宽）。
_COLORS = {
    "team": {"colors": ("white", "yellow"), "size": (0.015, 0.035)},
    "note": {"colors": ("yellow", "cyan"), "size": (0.035, 0.055)},
    "equipment": {"colors": ("yellow", "cyan"), "size": (0.028, 0.045)},
}

# 器材页的 `+` 是直角十字，卡片上的四角星尖端只有 ~30°。
# 只对器材页启用：队友/笔记的空位 `+` 是圆角，拐角会落到 30~60°，不能一起卡。
_SHAPE = {"equipment": {"min_corner": 60.0}}


def slots(page):
    """页面的槽位框（帧坐标，基准 1548x871）。未知页面直接报错。"""
    if page not in _SLOTS:
        raise KeyError(f"未知准备页: {page}")
    return _SLOTS[page]


def _scaled(box, width, height):
    """标定帧坐标 → 当前帧坐标。"""
    sx, sy = width / _CAL_W, height / _CAL_H
    x, y, w, h = box
    return (int((x - _PAD) * sx), int((y - _PAD) * sy),
            int((w + _PAD * 2) * sx), int((h + _PAD * 2) * sy))


def _min_corner_angle(component):
    """轮廓拐角的最小角度（度）。直角十字 ~90°，四角星尖端 ~30°。"""
    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0
    contour = max(contours, key=cv2.contourArea)
    approx = cv2.approxPolyDP(
        contour, 0.03 * cv2.arcLength(contour, True), True).reshape(-1, 2)
    if len(approx) < 4:
        return 0.0
    angles = []
    for i in range(len(approx)):
        p0, p1, p2 = approx[i - 1], approx[i], approx[(i + 1) % len(approx)]
        v1, v2 = p0 - p1, p2 - p1
        denom = np.linalg.norm(v1) * np.linalg.norm(v2)
        if denom == 0:
            continue
        angles.append(np.degrees(np.arccos(
            np.clip(np.dot(v1, v2) / denom, -1.0, 1.0))))
    return float(min(angles)) if angles else 0.0


def _is_plus(mask, min_side, max_side, min_corner=0.0):
    """mask 里有没有一个「亮色十字」连通块。

    `min_corner` 给器材页用：真 `+` 的拐角接近直角；卡面上的星星尖端很尖，
    只看尺寸/填充率会把它当成空位（2026-11-27 实拍）。
    """
    n, labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (min_side <= w <= max_side and min_side <= h <= max_side):
            continue
        # `+` 是接近正方形；细长条（文字/装饰）不要
        if not (0.7 <= w / h <= 1.5):
            continue
        fill = area / float(w * h)
        # `+` 只占框的一部分（实测 0.34~0.41）；整块实心/太稀疏都不算
        if not (0.15 <= fill <= 0.45):
            continue
        cx, cy = x + w // 2, y + h // 2
        row = int(mask[cy, x:x + w].sum())
        col = int(mask[y:y + h, cx].sum())
        if row < w * 0.45 or col < h * 0.45:
            continue
        if min_corner:
            component = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
            if _min_corner_angle(component) < min_corner:
                continue
        return True
    return False


def has_plus(frame, box, colors=("white", "yellow", "cyan"),
             size=(0.012, 0.07), min_corner=0.0):
    """框里有没有 `+`。只看形状；`colors`/`size` 限定该页 `+` 的实际范围。"""
    if frame is None or not getattr(frame, "size", 0):
        return False
    height, width = frame.shape[:2]
    x, y, w, h = _scaled(box, width, height)
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(width, x + w), min(height, y + h)
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return False

    b, g, r = cv2.split(roi)
    masks = {
        "white": (b >= 225) & (g >= 225) & (r >= 225),
        "yellow": (r > 190) & (g > 160) & (b < 170),
        "cyan": (g > 170) & (b > 170) & (r < 170),
    }
    min_side = max(12, int(width * size[0]))
    max_side = max(min_side + 1, int(width * size[1]))
    for name in colors:
        if _is_plus(masks[name].astype(np.uint8), min_side, max_side,
                    min_corner):
            return True
    return False


def empty_slots(frame, page):
    """每个槽位是否为空（True = 检测到 `+`）。顺序与 `slots(page)` 一致。"""
    spec = _COLORS.get(page, {"colors": ("white", "yellow", "cyan"),
                              "size": (0.012, 0.07)})
    shape = _SHAPE.get(page, {})
    return [has_plus(frame, box, spec["colors"], spec["size"],
                     shape.get("min_corner", 0.0))
            for box in slots(page)]
