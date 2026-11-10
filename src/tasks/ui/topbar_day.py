"""顶栏日期（「第N周 星期X」）的**区域重读**兜底。

为什么需要这个模块（2026-11-02 实测 1136 帧得出的结论）：

    整帧 OCR **从来没读错**星期几 —— 能读出来的 1023 帧，值 100% 正确。
    问题全是**丢字**：第三字「X」被并进前一个字的框里，框里只剩
    「星期」/「星期-」/「期日」/「期大」。丢字率 9.9%（1136 帧里 113 帧）。

丢字的规律很硬：**笔画越少越容易丢**（实测丢字率）：

    星期一 → 67.7%（只剩 70 帧「星期」+ 37 帧「星期-」）
    星期二 → 0.0%
    星期三 → 0.0%
    星期四 → 0.0%
    星期五 → 0.0%
    星期六 → 7.5%（读成「期大」）
    星期日 → 0.0%

原因看像素就明白：「一」只有**一条横线**，OCR 的检测框会把它并进「期」里，
于是整词变成「星期」两字。裁图放大后人工确认：117 张残框帧**全部**是
「星期一」，没有一张是「二」——「二」有两条横线，笔画够、不会丢。

所以这个模块的作用是：

1. **把读不出的帧救回来**：把那块裁出来放大重读，检测阶段会重新切分，
   单独那条横线就成了独立的框，识别成「一」。
   实测：117 张残框帧，区域重读救回 **116 张**（99.1%）。
2. **把「星期六」被读成「期大」纠正回来**：整词读错时框还在、位置还在，
   裁那块重读就能读对（5/5 救回）。

判据只认**整词**「星期X」（三个字，不多不少），避免把正文里的
「星期日」「比赛后期」误当成顶栏日期。

这里只放**纯函数**（frame → 结果），不依赖 ok-script 运行时，
可以用 `logs/` 里的历史帧离线校验（见 `tools/check_topbar_day.py`）。
"""

import re

from src.tasks.ui import zoom_reread

# 「星期X」的整词正则（严格：整段必须正好三个字）
DAY_WORD_RE = re.compile(r"^星期([一二三四五六日天])$")
# 「第N周」
WEEK_RE = re.compile(r"^第\s*(\d+)\s*周$")

# 星期框在顶栏的位置（归一化，相对游戏客户区）。
# 实测（logs/ 下 1136 帧，三种分辨率 1549x871 / 1920x1080 / 1349x759）：
#   框的左沿 x ≈ 0.1014~0.1065，右沿 x ≈ 0.1575~0.1762，上沿 y ≈ 0.006~0.028
#   （星期X 整词宽 66~144 px，正好是「星」「期」「X」三个字）
# 这里取比实测更宽一点的框，两侧各留余量：x 0.092~0.192，y 0~0.075。
DAY_REGION = (0.092, 0.000, 0.192, 0.075)
# 「第N周」框的位置（实测左沿 x ≈ 0.020~0.036，右沿 x ≈ 0.065~0.080）
WEEK_REGION = (0.012, 0.000, 0.095, 0.070)

# 放大倍数（实现在 `ui/zoom_reread.py`，这里只是给这两个热点用的默认值）。
# 实测 x3 在 1031 帧上与整帧 OCR 100% 一致（逐星期都是 100%），
# x4 反而在「星期一」上有 1/51 读不出，x5 有 6/51，x6 开始连「星期二」都掉。
ZOOM = zoom_reread.ZOOM

# 识别置信度下限。区域重读时只信高置信度的整词。
MIN_SCORE = zoom_reread.MIN_SCORE


def crop_region(frame, region):
    """按归一化区域裁一块（实现见 `zoom_reread.crop_px`）。"""
    return zoom_reread.crop_region(frame, region)


def day_from_texts(texts):
    """从一批 OCR 文本里找**整词**的「星期X」，返回 "X" 或 None。

    为什么只认整词：
    - 「星期」后面跟别的字（正文里「星期日要训练」之类）不能算；
    - 「比赛后期」这种含「期」的正文更不能算。
    `texts` 可以是字符串列表，也可以是 (文本, 置信度) 元组列表。
    """
    for item in texts or []:
        name = item[0] if isinstance(item, (tuple, list)) else item
        name = (name or "").replace(" ", "").strip()
        if not name:
            continue
        m = DAY_WORD_RE.match(name)
        if m:
            return "日" if m.group(1) == "天" else m.group(1)
    return None


def week_from_texts(texts):
    """从一批 OCR 文本里找**整词**的「第N周」，返回 int 或 None。"""
    for item in texts or []:
        name = item[0] if isinstance(item, (tuple, list)) else item
        name = (name or "").replace(" ", "").strip()
        if not name:
            continue
        m = WEEK_RE.match(name)
        if m:
            return int(m.group(1))
    return None


def read_day(ocr, frame):
    """裁出星期那块、放大重读，返回 "X" 或 None。

    `ocr` 传 ok-script 的任务实例（`self.ocr(box=..., frame=...)`）或 OCR 引擎
    （`engine.ocr(img)`）都行，兼容在 `zoom_reread.run_ocr`。
    读不出就返回 None —— **绝不猜**：猜错会让「当天状态」整个重置
    （`daycycle.day_state` 用 (周, 星期) 当日期键）。
    """
    texts = zoom_reread.reread_region(frame, ocr, DAY_REGION,
                                      scale=ZOOM, min_score=MIN_SCORE)
    return day_from_texts(texts)


def read_week(ocr, frame):
    """同样手法读「第N周」，返回 int 或 None。"""
    texts = zoom_reread.reread_region(frame, ocr, WEEK_REGION,
                                      scale=ZOOM, min_score=MIN_SCORE)
    return week_from_texts(texts)
