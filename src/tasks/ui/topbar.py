"""顶栏右上角图标条的判读：倍速档位（0=OFF / 1x / 2x / 3x / …）。

四个图标的含义与坐标见 `docs/UI_MAP.md`「顶栏右上角图标条（倍速 / 历史记录 / 菜单 / 返回）」：

    ① 倍速     ▶/Nx     点击在 1x → 2x → 3x → OFF → 1x 之间**一档一档**循环
    ② 历史记录  纸页      事件流水（只读、安全）
    ③ 菜单     ☰        保存后中断 / 放弃 / …（**禁止点击**）
    ④ 返回     箭头      返回上一级

倍速图标的画法（2026-11-01 逐档实测 + 10-26 旧帧对照，见 `logs/topbar/`）：

- **图标里画几个三角 = 这个存档当前解锁到几档**（只解锁到 2x 时画两个三角，解锁 3x 后画三个）。
- **实心（蓝）的三角个数 = 当前档位**；没实心的那些是**白色空心三角**。
- 所以「**还有空心三角 → 没到顶；一个空心都没有 → 已经到该存档的最高档**」。
  这条判据**不需要知道上限是 2 还是 3 还是 4**，游戏以后再加 4x/5x 也不用改代码。
- OFF 态整个图标变成灰字「OFF / 加速」，没有三角 —— 像素上和「全实心」极像（实测 solid 0.52 / hollow 0.01，
  与 3x 的 0.51 / 0.00 无法区分），所以 **OFF 必须靠 OCR 文字判**，见 `off_text()`。
- **实心占比 ≈ 0 表示「这条横带里没有倍速图标」**（换页、淡入、别页面的金币徽章…），
  这时不能按「没有空心三角 = 到顶」「有空心 = 没到顶」去判 —— 一律返回 None，让调用方不点。

除了倍速，顶栏还有一个「读数」问题：**精力条左端的 ⚡ 图标会被 OCR 并进数字**
（`3 54/130` / `3130/130`），斜杠还会被吃成 `1`（`3921130` = `3 92/130`）。
清洗也在本文件（`strip_icon_digit` / `strip_icon_reading`，纯文本判据、不看坐标），
调用点是 `BaseGirlsTask._ocr_region`（全项目唯一的 OCR 出口）。

这里只放**纯函数**（帧 / OCR 结果 → 状态），不依赖 ok-script 运行时，方便用 `logs/topbar/` 里的整帧离线校验。

关于阈值：本文件里的数字都是**UI 版式阈值**（三角在屏幕上画在哪、白色空心有多亮），
不是游戏进度量（上限、解锁、次数）。游戏以后加档位只会改变「三角个数」，不会改变这些版式阈值。
"""

import re

# 三个三角所在的一行（含可能出现第 4/5 个三角的余量），相对坐标
SPEED_TRI_BAND = (0.742, 0.012, 0.786, 0.048)
# 「还有空心三角」的判据（UI 版式阈值）：实测 1x 0.25 / 2x 0.12 / 3x 0.00，取 0.05 分界
SPEED_HOLLOW_MIN = 0.05
# 「这一条横带里真的画着倍速图标」的判据（UI 版式阈值）：实心三角占比。
# 逐档实测 1x 0.26 / 2x 0.42 / 3x 0.51 / OFF 0.52；而**不是倍速图标的画面**（炒股页的
# 金币徽章、别的页面、页面淡入那一拍）实心占比是 0.00，空心却是 1.00 ——
# 恰好落进「有空心 = 没到顶」这条判据里（2026-11-02 23:41:34 就是被这个骗的：
# 3x 状态被判成「还有空心」→ 点一下把 3x 打成了 OFF）。
# 取 0.10：离最低的真档位 0.26 留一倍余量，也把 0.00~0.05 的「没画图标」全挡掉。
SPEED_SOLID_MIN = 0.10

# OFF 态的文字（实测见过 OFF / 0FF；「加速」是图标下方第二行）
OFF_TEXTS = ("OFF", "0FF", "加速")
OFF_REGION = (0.70, 0.00, 1.00, 0.10)      # 只有右上角这块里的 OFF 才算倍速图标的 OFF 态


def tri_band_stats(frame):
    """返回 (实心蓝占比, 空心占比)。frame 为 BGR，判不了返回 None。"""
    if frame is None:
        return None
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = SPEED_TRI_BAND
    roi = frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
    if roi.size == 0:
        return None
    b = roi[:, :, 0].astype(int)
    g = roi[:, :, 1].astype(int)
    r = roi[:, :, 2].astype(int)
    solid = (b - r > 12) & (b > 140) & (b < 243)      # 实心三角
    hollow = (b < 246) & (g < 244) & ((b - r) <= 12)  # 白色空心三角（含浅灰描边）
    return float(solid.mean()), float(hollow.mean())


def speed_at_max(frame):
    """已经到本存档解锁的最高档？（还有空心三角 = 没到顶）

    三种结果：
    - True / False：判得出来；
    - None：**这一条横带里没有倍速图标**（页面不是训练日主界面、还在淡入、
      或者右上角被别的面板盖住了）—— 见 `SPEED_SOLID_MIN`，调用方必须不点。

    「没有空心三角」也不能当「到顶」：没有图标时实心/空心是 0.00/1.00，
    只看空心会判成「没到顶」（2026-11-02 把 3x 点成 OFF 的那次）。
    ↳ 所以先用实心占比确认图标在，再谈空心。
    """
    stats = tri_band_stats(frame)
    if stats is None:
        return None
    solid, hollow = stats
    if solid < SPEED_SOLID_MIN:
        return None
    return hollow < SPEED_HOLLOW_MIN


def off_text(texts, width, height):
    """当前帧的右上角有没有「OFF / 加速」字样（倍速图标的 OFF 态）。

    texts 是 ok-script 的 OCR 结果（.name / .x / .y / .width / .height，像素）。
    必须限定右上角：正文、商店筛选条里也会出现「OFF」，全局找会误判。
    """
    if not texts or not width or not height:
        return False
    x1, y1, x2, y2 = OFF_REGION
    for t in texts:
        name = (getattr(t, "name", "") or "").strip()
        if not name or not any(k in name for k in OFF_TEXTS):
            continue
        cx = (t.x + t.width / 2) / width
        cy = (t.y + t.height / 2) / height
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            return True
    return False


def speed_label(texts, width, height):
    """右上角读到的档位文字（`1x` / `2x` / `3x` / `OFF` 归一化后），没有就 None。

    只用来**记录和日志**：策略不依赖它（OCR 小字会漏，实测 2x 偶尔读不出）。
    """
    if not texts or not width or not height:
        return None
    x1, y1, x2, y2 = OFF_REGION
    best = None
    for t in texts:
        name = (getattr(t, "name", "") or "").strip()
        if not name:
            continue
        cx = (t.x + t.width / 2) / width
        cy = (t.y + t.height / 2) / height
        if not (x1 <= cx <= x2 and y1 <= cy <= y2):
            continue
        if any(k in name for k in ("OFF", "0FF")):
            return "OFF"
        low = name.lower().replace(" ", "")
        if low and low[-1] == "x" and low[:-1].isdigit():
            best = low[:-1] + "x"
    return best


# ---------- 精力条左端 ⚡ 图标的识别噪声 ----------

# A：首位多出来的数字 + 真实数字 + /上限
_ICON_PREFIX_RE = re.compile(r"^(\d)(\d{1,3})/(\d{1,3})$")

# B：斜杠被吃成 '1' —— 前缀 + '1' + 3 位上限
_SLASH_LOST_RE = re.compile(r"^(\d{1,4})1(\d{3})$")

# 同一帧里「读对了」的 3 位上限（用来给 B 支定上限，防止把别的数字改坏）
_CLEAN_CAP_RE = re.compile(r"^\d{1,4}/(\d{3})$")

# 精力上限的量级（实测 121 / 130 / 145 / 164）。超过这个量级的 3 位数
# （零用钱、六维之类的尾数）一律不碰。
MIN_CAP, MAX_CAP = 100, 250


def _known_cap(texts):
    """同一帧里唯一那个「读对了」的 3 位上限；没有或不止一个就返回 None。"""
    caps = set()
    for t in texts:
        name = (getattr(t, "name", "") or "").replace(" ", "").strip()
        m = _CLEAN_CAP_RE.match(name)
        if m and MIN_CAP <= int(m.group(1)) <= MAX_CAP:
            caps.add(int(m.group(1)))
    return caps.pop() if len(caps) == 1 else None


def strip_icon_digit(name, known_cap=None):
    """`3 54/130` → `54/130`、`3921130` → `92/130`；不像噪声就原样返回。"""
    s = (name or "").replace(" ", "").strip()

    m = _ICON_PREFIX_RE.match(s)
    if m:
        cap = int(m.group(3))
        if not MIN_CAP <= cap <= MAX_CAP:
            return name                      # 上限量级不对 → 不是精力条，别碰
        if int(m.group(1) + m.group(2)) <= cap:
            return name
        return f"{m.group(2)}/{m.group(3)}"

    m = _SLASH_LOST_RE.match(s)
    if not m:
        return name
    value, cap_text = m.group(1), m.group(2)
    cap = int(cap_text)
    if cap_text.startswith("0") or not MIN_CAP <= cap <= MAX_CAP:
        return name
    if known_cap is not None and cap != known_cap:
        return name
    if int(value) > cap:
        # 前缀 > 上限 ⇒ 首位是图标，丢掉那一位
        value = value[1:]
        if not value or int(value) > cap:
            return name
    elif len(value) < 2:
        # 没图标又只剩一位数（`11228` 这种 5 位数）→ 分不清是「零用钱」还是
        # 「1/228」，宁可不洗。实测 52/130、45/130 这类都是两位值。
        return name
    return f"{value}/{cap_text}"


def strip_icon_reading(texts):
    """就地把一批 OCR 结果里被图标污染的文本改干净，返回同一个列表。

    只改 `name`，`x/y/width/height` 原样保留 —— 下游按坐标做的判读
    （`texts_in_region`、点击点）不受影响。
    """
    texts = texts or []
    known_cap = _known_cap(texts)
    for t in texts:
        name = getattr(t, "name", None)
        if not name:
            continue
        fixed = strip_icon_digit(name, known_cap=known_cap)
        if fixed != name:
            t.name = fixed
    return texts
