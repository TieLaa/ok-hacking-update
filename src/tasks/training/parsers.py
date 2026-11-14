"""只读解析（OCR 文本 + 帧取色），不产生点击。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""

import re

import cv2
import numpy as np

from ok.feature.Box import Box

from src.tasks.ui import Defs
from src.tasks.ui import labels
from src.tasks.ui import zoom_reread
from src.tasks.training import roster
from src.tasks.training.constants import STYLE_ALIASES

# 科目按钮外圈「框」的判定门限（比例 = 环带上命中像素 / 环带总像素）。
# 2026-10-27 第六局实跑 + 用户当场复核：
#   - 普通科目页里被点开的那一行，环带天然就有 0.075~0.114 的橙黄（选中高亮 +
#     科目图标本身），旧门限 0.10 全被当成金框（21 次选科目报 13 次，用户指出
#     「刚才没有金框」）；
#   - 真彩虹框同一套环形采样是 0.333，西瓜图标那一行是 0.100。
# 所以两者都取 0.20 作下限，金框再加「明显高于其它行中位数」这一条。
GOLD_RING_MIN = 0.20
GOLD_RING_FACTOR = 2.0
# 金框像素的**颜色**判据（2026-11-02，用户报「最后一个意识有金框没识别到」）。
#
# 事故复盘（最后一局 23:59:57，日志原文）：
#     科目金框：意识 占比 0.274，其它行中位数 0.229
#     探测「意识」：… 金框 0 …
# 绝对门限 0.20 其实**已经过了**（0.274 > 0.20），是被「必须 > 2×其它行中位数」
# 这条相对判据挡掉的：0.229 × 2 = 0.458 > 0.274。
#
# 为什么会冒出 0.229 这么高的基线？度假页（第 4 周海边合宿那套界面）里
# **科目按钮本身就是橙黄色的 MAX 按钮**，采样区整块命中橙色，
# 把「其它行中位数」抬到了 0.229 —— 这时候「2 倍基线」这条间接判据就必然误杀。
#
# 正解是**按颜色分开**，而不是靠基线比大小。离线统计（441 张存证帧 / 2422 个科目行，
# scripts 见 logs/_eval_final.py）：
#     橙黄按钮体 RGB≈(255,171,93)  H=13~21  S≈125~175
#     真金框       RGB≈(255,225,95)  H=18~31  S≈135~235
# G 通道分得最开：按钮体 G≈171，真金框 G≈221~227。
# 所以金色像素额外要求 **G >= 200**（把橙黄的 171 挡在门外），
# 色相下限收到 18（真金框像素的 98% 都在 H>=18）。
#
# 效果（同一批 2422 行）：
#     现行 hue>=8          → ratio>0.20 的行 295 行，基线中位数最大 0.218
#     改后 hue>=18 & G>=200 → ratio>0.20 的行 4 行（**全是已知真金框**），
#                            基线中位数最大 0.071，噪声上界 0.114
# 绝对门限仍是 0.20（真金框在新口径下是 0.219~0.284）。
GOLD_PIX_HUE_MIN = 18
GOLD_PIX_G_MIN = 200
# 「整页大多数科目行都过绝对门限」→ 判定为界面整体偏色（不是某一科目的金框）。
# 取 max(2, 行数//2)：6 个科目时需要 ≥3 行同时过门限才算「整页都这样」。
# 单行/双行同时有金框是正常情况（每个科目各自升级），不能因此误杀。
GOLD_PAGE_WIDE_MIN = 2
# 彩框（2026-10-31 两次重标定，见 has_crit_border 的 docstring）：
#   ① 采样半窗 0.060h → 0.050h：旧半窗下噪声行（选中态/图标）能到 0.15~0.23，
#      与门限 0.20 重叠，日志里凭空多出一个彩框；收到 0.050h 后同一批存盘帧是
#      彩框 0.27~0.39 / 噪声 0.00~0.11。再窄到 0.045h 会把真边框切掉（真彩框掉到 0.09~0.16）。
#   ② 下限 0.22 → 0.13（用户 2026-10-31 20:31 周四当场指出「有个彩边没认到」）：
#      那次「投射」占比只有 0.157（满经验 1，真彩边），被 0.22 挡掉。
#      同一时刻其它行都是 0.000 → 新半窗下噪声其实塌到 0 附近，0.22 定高了。
#      现在：真彩框下界 0.157、噪声上界 0.109(仅 1 例，其余 0.00~0.003)，取 0.13。
CRIT_RING_MIN = 0.13
CRIT_RING_FACTOR = 2.0
CRIT_RING_HALF_H = 0.050
# 技能三选一：卡片名字行在 cy≈0.43（实测帧 logs/events/1031-162657_handle_skill_select.jpg，
# 1549x871 → 0.431），三张卡名字的 cx≈0.19 / 0.50 / 0.81（= Defs.SKILL_CARDS 的 x）。
# 名字行下面是 0.49 的「角色/行为卡/通用」标签行，底部 0.88/0.93 是「得分手/控卫/能力详情」。
SKILL_NAME_CY = 0.43
SKILL_NAME_CY_TOL = 0.035
# 扭蛋卡头横带的「彩色」判据（用户 2026-11-01：训练等级提升后解锁了**彩色扭蛋**，
# 优先级 彩色 > 金色 > 紫色）。
# 彩色卡那一带是**整条彩虹渐变**，不是单一色相：实拍帧 logs/now.jpg（1549x871）里
# 中间那张彩色卡的色相摊在 75~165，30° 一档有 3 档各占 ≥14%（最大档 0.51）；
# 同页两张金卡 0.96~1.00 全落在同一档、紫/蓝卡同理。
# 所以：**≥3 档各占 ≥10%，且最大档 < 0.70** 才算彩色 —— 单色卡永远过不了第二条。
RAINBOW_BAND_MIN_BINS = 3
RAINBOW_BAND_BIN_FRAC = 0.10
RAINBOW_BAND_MAX_TOP = 0.70
# 代码版本戳（run() 会打到日志里）：排查「改完不生效」时一眼看出跑的是哪份代码
CODE_VERSION = "2026-11-02b"


# ---------- 六维面板读数（2026-11-04）----------
# 面板每格是「名字 + 等级 + 当前值/上限」，OCR 实测四种写法：
#     投射B488/1680      3488/1680（等级粘在前面）      体能B 464/+16      意识C 261/1689
#
# 旧判据「取这一行第一个 ≥100 的数字」读出过**天文数字**：2026-11-04 实跑
# `体能 8116`（真值 1016）、`意识 3114`（真值 1114）→ 监控页六维和「分数预测」
# 从 9k 跳到 1w4。因为 `info["attrs"]` 是合并语义，错值会一直挂到下次进休息室。
#
# **只有一条护栏：这一格的值必须 ≤ 这一格的上限。** 上限来源二选一：
#   - 这帧自己写了（`/1680`）；
#   - 上次从同一格读到过（`_attr_caps`，面板上限一局内不变）。
# 上面两个事故值都超上限（8116 > 1680、3114 > 1689），一条规则全覆盖 ——
# 不做「砍前导位猜回 488」、也不拿「上一次的值 ±窗口」再判一次：
# 误读就这一格不动（保留上次的值），下一帧读干净了自然补上。
ATTR_PAIR_RE = re.compile(r"(\d{1,4})\s*/\s*(\d{1,4})")
ATTR_NUM_RE = re.compile(r"(\d{2,4})")
ATTR_MIN_VALUE = 100
ATTR_CAP_MIN, ATTR_CAP_MAX = 500, 9999
ATTR_KEYS = 6                  # 六维齐全才算得出可信的「分数预测」


def attr_value_from(text, cap=None):
    """一格面板文字 → 当前值；读不出来或**超上限**返回 None。

    `cap` = 这一格的上限；本帧文字里带 `/上限` 时以它为准。
    """
    s = (text or "").replace(" ", "").replace("　", "")
    m = ATTR_PAIR_RE.search(s)
    if m:
        value, cell_cap = int(m.group(1)), int(m.group(2))
        if ATTR_CAP_MIN <= cell_cap <= ATTR_CAP_MAX:
            cap = cell_cap
    else:
        m = ATTR_NUM_RE.search(s)
        if m is None:
            return None
        value = int(m.group(1))
    if value < ATTR_MIN_VALUE:
        return None
    if cap and value > cap:
        return None                      # 超上限 = 误读（8116/1680、3114/1689）
    return value


def ring_is_border(ratio, baseline=None, floor=GOLD_RING_MIN,
                   factor=GOLD_RING_FACTOR):
    """环带命中比例 → 是不是「真外圈」。

    绝对下限挡掉选中高亮/科目图标自带的那点颜色；给了 baseline（其它行中位数）
    时还要求明显高于基线，度假页那种整屏偏黄就不会误判。
    """
    if ratio <= floor:
        return False
    if baseline is None:
        return True
    return ratio > baseline * factor


class TrainingParsersMixin:

    # ---------- 扭蛋页几何（2026-11-01） ----------
    # 扭蛋卡「标题条」的行位置（比例）。品质色取这一条横带。
    # 实拍（logs/events/1101-122241_handle_street.jpg，1549x871）OCR：
    #   卡名「投射增加++/体能增加+/全属性增加」cy=0.407，
    #   品质横带铺在 cy 0.25~0.43（饱和像素占比 >0.5 的行）。
    # 所以标题条取 0.36~0.43 落在横带上、又不吃卡名那一行。
    GACHA_TITLE_BAND = (0.36, 0.43)
    # 卡名行所在的纵向范围（比例）：卡名文字本身。
    GACHA_NAME_ROW = (0.28, 0.44)
    # 卡片说明文字（卡名下方那段）在 0.44~0.58。
    GACHA_EFFECT_ROW = (0.44, 0.58)
    # 扭蛋卡名列中心：实拍 0.253 / 0.503 / 0.752（1549 宽）→ 约 0.25/0.50/0.75。
    # 价格药丸是**晚于**卡面渲染的：2026-11-01 15:09 实跑里卡面文字已经读到了，
    # 价格数字还没画出来，所以「读不到卡」≠「这一页没卡」。
    GACHA_CARD_CXS = (0.25, 0.50, 0.75)

    # 商店商品格：实测 3 列 × 2 行（2026-10-27 商店帧 1920x1080）。
    # 列中心 0.223 / 0.357 / 0.490（间距 0.134），行中心 0.368 / 0.634（间距 0.266）。
    # 件数沿革：原本 4 件（3+1）→ 全局强化「商品+1」→ 现在 5 件（3+2，第二行居中）
    #   → 新角色入队可能再 +1 = 6 件（3+3）。
    # 6 件时第 6 格与第 3 列同列（名称 y≈0.544、价格 y≈0.746），区域天然覆盖，不用改判据。
    # 会不会出现 7 件还不知道（也可能不止一位角色有这能力）。真到那一步，屏幕上放不下
    # 第 3 行（价格会到 y≈1.01），大概率变成可滚动列表 —— 到时上面的告警会响，
    # 再决定是滚一屏还是改网格，别凭猜先写。
    # 注意：名称与价格只靠「水平距离 < 0.10w」配对，列间距实测 0.134，
    # 余量只有 0.034。如果哪天变成 4 列（间距≈0.10）就会配错，必须同时改阈值。
    SHOP_COLS = 3
    SHOP_ROWS = 2
    SHOP_MAX_ITEMS = SHOP_COLS * SHOP_ROWS
    # 商品名在**价格正上方固定距离**处（2026-11-14 实拍 1548x871 商店帧）：
    #   第一行：名 cy=0.277 / 价 cy=0.479 → 差 0.202
    #   第二行：名 cy=0.543 / 价 cy=0.746 → 差 0.203
    # 两行的绝对 cy 差很多，靠**相对价格的距离**才通用。
    # 同时排除卡内图标上的字（cy=0.344，只比卡名低 0.067）——
    # 2026-11-14 实跑「大乐透」的图标字被读成「大乐边」（置信度 0.78），
    # 旧判据（取 y 最大）正好选中那个模糊框 → 名字对不上 →
    # 详情标题校验失败 → 这张 60 币的现金类白丢。
    SHOP_NAME_ABOVE_PRICE = (0.17, 0.24)
    # 右侧详情面板的**第一行**是商品名（实测 cy=0.276），
    # 效果文案在 cy≈0.58，中间是商品图。标题只在这一行里。
    SHOP_DETAIL_TITLE_ROW = (0.22, 0.34)

    def read_money(self, texts):
        """顶栏金币：专注条右边的独立数字（不含斜杠）。"""
        best = None
        for t in texts:
            s = t.name.strip()
            if not s.isdigit() or len(s) > 5:
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if 0.50 <= cx <= 0.62 and cy < 0.09:
                best = int(s)
        return best

    # ---------- 顶栏专注条像素兜底（2026-11-01） ----------
    # OCR 把「/」读成「1」是固定毛病，不是偶发：57/130 → `571130`、30/130 → `301130`、
    # 33/115 → `333/115`、17/115 → `971115`（RULES.md 记过 301130 那次）。
    # 顶栏专注药丸 = 左边**黄色填充段** + 右边**灰色空段**，总宽是固定版式比例：
    # 实测 554/1920 = 447/1549 = 377/1307 = **0.2885w**（与上限 115/121/130 无关）。
    # 所以量填充比例就能反推专注，和 OCR 正读值比只差 ≤1 个单位
    # （58→58、40→39、50→49、75→74、61→60、94→93）。斜杠读错时，末 3 位当上限、
    # 比例当当前值即可还原，不必再指望 OCR 认斜杠。
    STAMINA_BAR_BOX = (0.10, 0.005, 0.75, 0.075)   # x1, y1, x2, y2（画面比例）
    STAMINA_BAR_WIDTH_F = 0.2885                   # 药丸总宽 / 画面宽（固定版式）
    STAMINA_BAR_LEFT_F = 0.2010                    # 药丸左沿 / 画面宽（固定版式）
    STAMINA_BAR_LEFT_TOL = 0.010                   # 左沿偏离这么多 = 不是专注条，别信
    STAMINA_BAR_TOL = 2                            # 像素换算 vs OCR 的容忍差（专注单位）

    def stamina_bar(self):
        """量顶栏专注条，返回 (填充比例, 左 px, 右 px)；量不出来返回 None。

        同一帧只算一次：一拍里 top_bar() 会被好几个 handler 调用。
        """
        frame = self.frame
        if frame is None:
            return None
        cache = getattr(self, "_stamina_bar_cache", None)
        if isinstance(cache, dict) and cache.get("frame") is frame:
            return cache.get("value")
        value = self._stamina_bar_measure(frame)
        self._stamina_bar_cache = {"frame": frame, "value": value}
        return value

    def _stamina_bar_measure(self, frame):
        """只量黄色填充段，总宽和左沿都用**版式常量**，不去找灰色空段。

        为什么不信灰段（2026-11-01 实测）：`2026-10-31-gacha-discount.png` 那帧
        带深色遮罩，灰段的 V 掉出灰底区间 → 找不到右沿 → 整帧被判「量歪」。
        而药丸左沿（0.2010w）和总宽（0.2885w）在 1920/1549/1307 三种画面上
        完全一致（386+554 / 312+447 / 263+377），直接当常量用更稳。
        """
        h, w = frame.shape[:2]
        x1f, y1f, x2f, y2f = self.STAMINA_BAR_BOX
        y1, y2 = int(h * y1f), min(h, int(h * y2f))
        xa, xb = int(w * x1f), int(w * x2f)
        roi = frame[y1:y2, xa:xb]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        yellow = (hue >= 18) & (hue <= 38) & (sat >= 150) & (val >= 200)
        cols = yellow.sum(axis=0)
        runs, start = [], None
        for i, c in enumerate(cols):
            if c >= 3:
                if start is None:
                    start = i
            elif start is not None:
                runs.append((start, i - 1))
                start = None
        if start is not None:
            runs.append((start, len(cols) - 1))
        runs = [r for r in runs if r[1] - r[0] >= 20]     # 够长的那段才是填充
        if not runs:
            return None
        left, fill_end = runs[0][0] + xa, runs[0][1] + xa
        # 版式校验：左沿必须是 0.2010w（小游戏页那种「7/10」的条位置完全不同）
        if abs(left - w * self.STAMINA_BAR_LEFT_F) > w * self.STAMINA_BAR_LEFT_TOL:
            return None
        total = w * self.STAMINA_BAR_WIDTH_F
        ratio = (fill_end - left + 1) / total
        if not (0.0 <= ratio <= 1.02):
            return None
        return min(ratio, 1.0), left, int(left + total)

    def top_bar(self, texts=None):
        """顶栏解析，外加**专注条像素兜底**（详见 STAMINA_BAR_* 的注释）。

        两条信息互相校验：
          - 斜杠被读成数字的粘连串（`571130` = 57/130）：能拆出**精确**的当前值候选；
          - 专注条填充比例：只知道大概（±1），但能仲裁候选里哪一个是对的。
        只在 OCR 解不出专注、或与像素差 > `STAMINA_BAR_TOL` 时才改判。

        另外把**零用钱**补进 `state` / `info`：取法直接用 `read_money()`（周末商店、
        游戏厅本来就在用它，别再造第二份判据）。监控页要显示这个数，业务侧不要
        反过来依赖 `info` —— 它们自己调 `read_money`。
        """
        state = super().top_bar(texts)
        pool = texts if texts is not None else getattr(self, "_tick_texts", None)
        coin = self.read_money(pool or [])
        if coin is not None and coin != state.get("coin"):
            state["coin"] = coin
            info = getattr(self, "info", None)
            if isinstance(info, dict):
                info["coin"] = coin
        ratio = self.stamina_bar()
        if ratio is None:
            return state
        frac, px_left, px_right = ratio
        cache = self._stamina_bar_cache if isinstance(self._stamina_bar_cache, dict) else {}
        if cache.get("resolved") and not state.get("stamina"):
            # 同一帧已经还原过：直接复用，别两次调用得出 56 / 57 两个值
            est, mx = cache["resolved"]
            state["stamina"], state["stamina_max"] = est, mx
            info = getattr(self, "info", None)
            if isinstance(info, dict):
                info.update({"stamina": est, "stamina_max": mx,
                             "stamina_pct": round(est / float(mx) * 100)})
            return state
        pool = texts if texts is not None else getattr(self, "_tick_texts", None)
        mx = state.get("stamina_max")
        cands = []
        if mx is None:
            mx, cands = self.stamina_candidates(pool, px_left, px_right)
        if mx is None:
            mx = getattr(self, "_stamina_max_seen", None)
        if not mx:
            return state                  # 上限都没读过：不猜（上层按「读不到」处理）
        # +0.5：填充段右沿永远是「半列黄 + 半列灰」，实测 8 帧正读值对照
        # （58/115、40/115、50/115、75/121、61/121、87/117、94/130、57/130）
        # 只按比例算都低 0.5~1 个单位，加上这个半格偏差后逐帧相等。
        est = int(round(frac * mx + 0.5))
        cur = state.get("stamina")
        if cur is not None and abs(cur - est) <= self.STAMINA_BAR_TOL:
            return state                  # OCR 和像素对得上，保持原样
        # 候选里挑离像素值最近的那个（同距取小的，斜杠「1」拆掉后一般就是它）
        pick = min(cands, key=lambda c: (abs(c - est), c)) if cands else None
        if pick is not None and abs(pick - est) <= 5:
            est = pick
        if cur is None:
            self.log_info(f"专注：OCR 没读出斜杠（粘成 {cands or '无'}），"
                          f"按专注条填充 {frac:.3f}×{mx} 还原为 {est}")
        else:
            # 像素和 OCR 差几个点是常态（2026-11-10 19:23 实跑顶栏一直 114 vs 117），
            # 每拍都报 warning 会把 GUI 告警区刷满（那一段刷了 30 多条）→ 值变了才记。
            key = (cur, est)
            if getattr(self, "_stamina_bar_logged", None) != key:
                self._stamina_bar_logged = key
                self.log_info(f"专注：OCR 读 {cur}，专注条填充 {frac:.3f}×{mx} → {est}，"
                              "按像素值算")
        state["stamina"], state["stamina_max"] = est, mx
        self._stamina_max_seen = mx
        if isinstance(self._stamina_bar_cache, dict):
            self._stamina_bar_cache["resolved"] = (est, mx)   # 同一帧结论固定，别算了又变
        info = getattr(self, "info", None)
        if isinstance(info, dict):
            info.update({"stamina": est, "stamina_max": mx,
                         "stamina_pct": round(est / float(mx) * 100)})
        return state

    def stamina_candidates(self, pool, px_left, px_right):
        """「斜杠/周数被读成数字」的粘连串 → (上限, [当前值候选])，没有返回 (None, None)。

        例：`571130` → (130, [5, 7, 11, 51, 57, 71, …] 里 ≤130 的那批)；
        `3251130`（第3周 + 25/130 粘成一串）同理。
        候选只负责「哪几位是粘进来的前缀」的穷举，具体哪个对由专注条填充比例仲裁
        （`top_bar` 取离像素值最近的那个，容差 5）。
        只在药丸的横向范围内找：顶栏金币也是纯数字，但它不在药丸里。
        """
        if not pool:
            return None, None
        for t in self.texts_in_region(pool, "top_bar"):
            s = t.name.replace(" ", "")
            if not s.isdigit() or not (5 <= len(s) <= 8):
                continue
            if not (px_left - 8 <= t.x <= px_right + 8):
                continue
            mx = int(s[-3:])
            if not (100 <= mx <= 200):
                continue
            head = s[:-3]
            cands = set()
            # head 里所有连续子串都当候选：粘进来的「第N周」和「斜杠读成的 1」
            # 都在 head 的前/中段，切掉它们剩下的就是当前专注。
            for i in range(len(head)):
                for j in range(i + 1, len(head) + 1):
                    v = int(head[i:j])
                    if v <= mx:
                        cands.add(v)
            if not cands:
                continue
            return mx, sorted(cands)
        return None, None

    # ---------- 专注恢复页 / 球队评分（2026-11-04） ----------

    def stamina_plus_point(self, texts):
        """大厅 / 训练剧本页顶栏专注药丸右侧「+」的点击点，返回 (x, y) 像素或 None。

        为什么单独一个方法（用户 2026-11-04：「在大厅点击我标注的红框位置，
        进入恢复专注页面」）：那个「+」不是文字按钮，OCR 有时把它并进药丸
        （`108/150+`）、有时单独给一个 `+` 框、有时干脆不读。三条路都要兜住，
        全都失败就返回 None，由调用方决定要不要按固定坐标点（并告警留痕）。

        **训练内**的顶栏药丸（`40/115`，cx≈0.25）**没有**「+」，所以只在
        画面右半区的药丸上找，绝不会误点训练日 HUD。
        """
        pill = None
        for t in texts:
            s = t.name.strip().replace(" ", "")
            if not labels.STAMINA_RE.search(s):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if cx < 0.45 or cy > 0.10:
                continue                      # 训练内 HUD 的药丸在左半边，跳过
            pill = (t, s, cx, cy)
            break
        if pill is None:
            return None
        t, s, cx, cy = pill
        if s.rstrip().endswith("+"):
            # 「108/150+」：'+' 就在框的最右端，往回收半个字宽
            return int(t.x + t.width - self.width * 0.006), int(t.y + t.height / 2)
        # 分开读的「+」：同一行、在药丸右侧
        for cand in texts:
            if cand.name.strip() != "+":
                continue
            ccy = (cand.y + cand.height / 2) / self.height
            ccx = (cand.x + cand.width / 2) / self.width
            if abs(ccy - cy) <= 0.04 and cx < ccx <= cx + labels.STAMINA_PILL_TOL:
                return (int(cand.x + cand.width / 2),
                        int(cand.y + cand.height / 2))
        # 只读到药丸、没读到 '+':按版式贴到药丸右沿外一点点（'+' 是固定宽度的小按钮）
        self.log_warning(f"专注药丸读到「{s}」但没有「+」框 → 按药丸右沿推算点击点")
        return (int(t.x + t.width + self.width * 0.010),
                int(t.y + t.height / 2))

    def in_stamina_item_detail(self, texts):
        """是不是「专注恢复」页里的**道具详情层**（已点开某一瓶饮料）？

        实测帧 `logs/debug/1104-190912_loop60`（1549x871）：
        `道具详情` + `专注能量饮料拥有：22` + `使用后可获得30专注。` + `拥有：22`
        + `1/22`（数量）+ **`使用道具`**（确认按钮）。

        这一层必须点「使用道具」才真的喝下去；点卡上的「使用」只是打开它。
        """
        return self.match_text(texts, labels.STAMINA_ITEM_DETAIL) is not None

    def stamina_recover_close_point(self, texts):
        """恢复页右上角 X 的点击点：优先用 OCR 到的「X」框，返回 (x, y) 或 None。

        为什么不能只用固定坐标：`Defs.STAMINA_RECOVER_CLOSE = (0.942, 0.157)` 在
        1549x871 上落到 (1459,136)，而实测 X 框中心是 (1448,105) —— y 偏了 31px，
        点空的 30 次全都没关上这一页（2026-11-04 死循环事故的最后一环）。
        """
        for t in texts:
            name = (t.name or "").strip().upper()
            if name not in ("X", "×"):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if cx >= 0.80 and cy <= 0.20:                 # 只认右上角那个 X
                return int(t.x + t.width / 2), int(t.y + t.height / 2)
        return None

    def read_stamina_recover_options(self, texts):
        """「专注恢复」页的三张卡 → `[{title, gain, owned, use, forbidden}]`。

        纯识别，不选：选哪张由 `handlers/stamina.py` 决定。
        每张卡横向落在「标题/说明」列（x 左侧）→「按钮」列（x 右侧），
        所以按 **y 带** 把同一行的文字归组，再取带内最右那个按钮框。

        `forbidden=True` 的判据是**两条独立事实**，任一成立就算：
          1. 标题里出现「钻石」（钻石购买）；
          2. 按钮框的文字**不是**整词「使用」（钻石行是图标 + 价格数字）。
        用户口径：**禁止使用钻石恢复专注**，所以这一行连"候选"都不进，
        连日志里都要显式写出来（方便复盘时确认脚本确实看见了、也确实没点）。
        """
        band = 0.055                                  # 卡片内文字的竖向容差（画面比例）
        rows = []
        for title in texts:
            name = title.name.strip()
            if not name:
                continue
            if (labels.STAMINA_RECOVER_TITLE not in name
                    and not any(w in name for w in labels.STAMINA_RECOVER_FORBIDDEN)):
                continue
            cy = (title.y + title.height / 2) / self.height
            same = [t for t in texts
                    if abs((t.y + t.height / 2) / self.height - cy) <= band]
            gain, owned, use, price = None, None, None, None
            for t in same:
                s = t.name.strip()
                m = labels.STAMINA_GAIN_RE.search(s)
                if m and gain is None:
                    gain = int(m.group(1))
                    continue
                m = labels.STAMINA_OWNED_RE.search(s)
                if m and owned is None:
                    owned = int(m.group(1))
                    continue
                if s == "使用" and t.x > title.x + title.width * 0.5:
                    use = t
                    continue
                if s.isdigit() and 1 <= len(s) <= 3 and t.x > title.x + title.width * 0.5:
                    price = int(s)                    # 钻石价格（只有付费行才有）
            forbidden = any(w in name for w in labels.STAMINA_RECOVER_FORBIDDEN)
            if price is not None and use is None:
                forbidden = True                      # 价格数字 = 付费按钮，不是「使用」
            rows.append({"title": name, "gain": gain, "owned": owned,
                         "use": use, "price": price, "forbidden": forbidden,
                         "box": title})
        rows.sort(key=lambda r: (r["gain"] is None, r["gain"] or 0))
        return rows

    def read_training_score(self, texts):
        """一局结束的「训练评分」弹层 → `(评分, 评级)`，不是这一页返回 `(None, None)`。

        实测（2026-11-04 20:53:21，`logs/mcp/a0e2b1ea…/run.log`）：

            训练评分  7728  SS  点击屏幕继续

        `7728` 就是**这个球员这一局的训练评分**（个人分），不是「本周总结」页那种
        全队评分（那个会从 7708 滚到 10485，不能当判据）。
        """
        if self.match_text(texts, labels.TRAINING_SCORE_MARKS) is None:
            return None, None
        score, rating = None, None
        for t in texts:
            s = (t.name or "").strip().replace(",", "").replace(" ", "")
            if score is None and s.isdigit() \
                    and labels.SQUAD_SCORE_MIN <= int(s) <= labels.SQUAD_SCORE_MAX:
                score = int(s)
                continue
            if rating is None and labels.TRAINING_RATING_RE.match((t.name or "").strip()):
                rating = t.name.strip()
        return score, rating

    def read_squad_ratings(self, texts):
        """「恭喜获得 / 训练奖励」页 → `{位置: 评分}`（读不到就是空 dict）。

        用户 2026-11-04 口径：多角色队列的达标判据就是**这一页的球员评分**。
        这一页只列五个位置（控卫/分卫/小前/大前/中锋）+ 评级 + 四位数评分，
        **没有名字**，所以按「位置词与评分数字的横向最近」配对，
        再由调用方用 `roster.position_by_name()` 把队长换成位置。

        实测帧 `docs/shots/2026-10-27-register-1.png`（1307x735）：
        `4822 (0.219) / 3028 (0.336) / 3025 (0.450) / 2915 (0.565) / 2917 (0.679)`，
        位置词 cy≈0.60、评分 cy≈0.64 → 都在 `SQUAD_SECTION_BAND` 里。
        评级字母（A+/B/S+）**不参与**配对，只留着打日志。
        """
        y1, y2 = labels.SQUAD_SECTION_BAND

        def cy_of(t):
            return (t.y + t.height / 2) / self.height

        def cx_of(t):
            return (t.x + t.width / 2) / self.width

        band = [t for t in texts if y1 <= cy_of(t) <= y2]
        scores, positions, ratings = [], [], []
        for t in band:
            s = t.name.strip()
            if s in labels.SQUAD_POSITIONS:
                # 位置词先判（精确匹配），避免「中锋5937」这种黏连框被当成评分
                positions.append((cx_of(t), s))
                continue
            m = labels.SQUAD_SCORE_RE.search(s)
            if m and labels.SQUAD_SCORE_MIN <= int(m.group(1)) <= labels.SQUAD_SCORE_MAX:
                # 2026-11-14 实跑：OCR 会把评分和相邻文字黏在一起
                # （`7929箱`、`中锋5937`），旧写法要求整框 `isdigit()`，
                # 这两列直接丢了 → 只读到 3/5 个位置，队长评分读到 None、
                # 达标判断永远「未达标」（logs/1114-230254_野上泷 第 1 局）。
                scores.append((cx_of(t), int(m.group(1))))
            elif labels.SQUAD_RATING_RE.match(s):
                ratings.append((cx_of(t), s))
        if not scores or not positions:
            return {}
        # 每一列的实际版式（实测 register-1.png）：**评级字母在评分正上方**、
        # **位置名词在评级右边约 0.075w**、评分在下面。所以先按「位置→最近的评分」
        # 配对会错一整列（位置整体右移 0.075，正好够跳到下一列去）。
        # 正确做法：以评分为锚，取「右边 0～0.12w 内最近的那个位置词」。
        out = {}
        for sx, value in scores:
            right = [(px - sx, name) for px, name in positions
                     if -0.02 <= px - sx <= 0.12]
            if right:
                out.setdefault(min(right)[1], value)
                continue
            px, name = min(positions, key=lambda p: abs(p[0] - sx))
            if abs(px - sx) <= 0.12:            # 兜底：位置词落在评分左边
                out.setdefault(name, value)
        if out and ratings:
            self.log_debug(f"球队评分：{out}（评级 {[r[1] for r in ratings]}）")
        return out

    def read_shop_items(self, texts):
        """解析商店左侧商品：名称 + 价格 + 卡片中心点击坐标。

        区域是**按实测网格**留的，不是按商品件数写死的：
        第二行价格实测 y≈0.746，旧上限 0.75 只剩 4px 余量，
        布局抖一下（换分辨率、强化加格子）第二行就整行消失 → 静默不买。

        2026-11-05：价格数字过 `labels.norm_digits` —— 商店第二行的价格「10」
        被 OCR 读成「①0」，`'①0'.isdigit()` 是 True 但 `int()` 抛 ValueError，
        handle_shop 每拍异常（被 flow 吞掉）→ 商店页永远推不动，任务原地停住
        （用户：「怎么停住了」）。
        """
        prices = []
        for t in texts:
            s = t.name.strip()
            # 免费商品（白神时久：每天多一件 0 元商品）：卡上是「0」或「免费」。
            # 旧判据要求 1~9999，正好把 0 元商品整件丢掉（2026-10-29 用户指出）。
            # 2026-11-05：价格要过 norm_digits —— '①0'.isdigit() 是 True 但
            # int() 抛 ValueError（商店第二行价格 10 被 OCR 读成 ①0），
            # 旧代码直接在这里炸，商店一拍都推进不了。
            if s in ("免费", "免费！", "免费!"):
                price = 0
            else:
                cleaned = labels.norm_digits(s)
                if cleaned is None or not (0 <= int(cleaned) <= 9999):
                    continue
                price = int(cleaned)
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            # 上限 0.82：覆盖第二行价格（0.746）并留 0.07 余量；
            # 底部「上新时间」在 0.93 附近，不会被吃进来。
            if 0.15 <= cx <= 0.58 and 0.38 <= cy <= 0.82:
                prices.append((price, t))

        names = []
        for t in texts:
            s = t.name.strip()
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (0.15 <= cx <= 0.58 and 0.18 <= cy <= 0.62):
                continue
            if "剩余" in s or "时间" in s or "商店" in s:
                continue
            if not (2 <= len(s) <= 12):
                continue
            if not any("\u4e00" <= ch <= "\u9fff" for ch in s):
                continue
            names.append(t)

        items = []
        for price, pt in prices:
            px = pt.x + pt.width / 2
            near = [n for n in names
                    if n.y + n.height / 2 < pt.y + pt.height / 2
                    and abs((n.x + n.width / 2) - px) < self.width * 0.10]
            if not near:
                continue
            # 商品名在**价格正上方固定距离**处（SHOP_NAME_ABOVE_PRICE）：
            # 第一行名 cy=0.277/价 0.479，第二行名 cy=0.543/价 0.746，
            # 相对距离都≈0.20；卡内图标上的字只低 0.067，靠距离排除。
            # 2026-11-14 实跑「大乐透」的图标字被读成「大乐边」（置信度 0.78），
            # 旧判据（取 y 最大）正好选中那个模糊框 → 名字对不上 →
            # 详情标题校验失败 → 这张 60 币的现金类白丢。
            pc_y = (pt.y + pt.height / 2) / self.height
            title_row = [n for n in near
                         if self.SHOP_NAME_ABOVE_PRICE[0] <=
                         pc_y - (n.y + n.height / 2) / self.height <=
                         self.SHOP_NAME_ABOVE_PRICE[1]]
            if title_row:
                name = min(title_row, key=lambda n: n.y)
            else:
                # 卡名行没读到（淡入/被裁）→ 退回同列最靠上的框
                name = min(near, key=lambda n: n.y)
            quality, qh, qs = self.card_quality(name, pt)
            items.append({
                "name": name.name.strip(),
                "price": price,
                "quality": quality,
                "hue": qh,
                "sat": qs,
                # 点名称和价格之间的卡片图，避免点到价格/数量
                "x": int(name.x + name.width / 2),
                "y": int((name.y + name.height / 2 + pt.y) / 2),
            })
        if len(items) > self.SHOP_MAX_ITEMS:
            # 超容量说明布局变了（强化又加格子？）——不做截断，截断可能把最贵的丢掉，
            # 但必须喊一声，否则就是静默少买/买错。
            self.log_warning(
                f"商店解析出 {len(items)} 件商品，超过 {self.SHOP_COLS}x{self.SHOP_ROWS} "
                f"网格容量 {self.SHOP_MAX_ITEMS} 件，请补一张商店截图核区域")
        return items

    def card_quality(self, name_box, price_box):
        """读商品卡片的品质色（金 / 紫 / 蓝 / 其他）。

        实机（docs/shots/2026-10-26-shop.png）：卡片底色就是品质色 ——
        紫卡 HSL 偏紫（OpenCV H≈124~132、S≈40、V≈251），
        金卡是暖黄（H≈20~40、S 更高），蓝卡 H≈100~115。
        采样卡片的**左右两条内边**（避开中间图标和文字），取中位色相。
        颜色只用来筛「彩/金/紫」，认不出来就按「其他」处理（不买），
        宁可少买也不乱花钱。
        """
        frame = self.frame
        if frame is None:
            return "其他", -1, -1
        h, w = frame.shape[:2]
        x1 = int(name_box.x - w * 0.015)
        x2 = int(name_box.x + name_box.width + w * 0.015)
        y1 = int(name_box.y - h * 0.02)
        y2 = int(price_box.y + price_box.height)
        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)
        if x2 - x1 < 20 or y2 - y1 < 40:
            return "其他", -1, -1

        band_y1 = y1 + int((y2 - y1) * 0.45)
        band_y2 = y2 - int((y2 - y1) * 0.15)
        # 2026-11-12：取样点往里收 —— 选中的卡片有一圈亮黄高亮框，旧版从
        # x1+4 起取，高亮色会被算进中位数：同一件「器材指导教程+」在一帧
        # 判「其他」、下一帧判「金」（logs/run.log 00:51:41 vs 00:52:28）。
        inset = max(6, int(w * 0.006))
        strips = [frame[band_y1:band_y2, x1 + inset:x1 + inset + 12],
                  frame[band_y1:band_y2, x2 - inset - 12:x2 - inset]]
        pixels = [s.reshape(-1, 3) for s in strips if s.size]
        if not pixels:
            return "其他", -1, -1
        pixels = np.concatenate(pixels)
        hsv = cv2.cvtColor(pixels.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
        colored = (hsv[:, 1] >= 20) & (hsv[:, 2] >= 170)
        if colored.sum() < 40:
            return "其他", -1, -1
        hue = float(np.median(hsv[colored, 0]))
        sat = float(np.median(hsv[colored, 1]))
        if 10 <= hue <= 45 and sat >= 55:
            return "金", hue, sat
        if 115 <= hue <= 155:
            return "紫", hue, sat
        if 90 <= hue < 115:
            return "蓝", hue, sat
        return "其他", hue, sat

    @staticmethod
    def is_stamina_clear(text):
        """这段文字是不是「清空精力」效果（而不是恢复精力）。

        只认 labels.GACHA_CLEAR_PHRASES 里写死的整句，不做「包含清空/包含精力」
        这种模糊匹配；并且先排除恢复类字样 —— 「恢复全部精力」不能被选成清空扭蛋。
        """
        s = (text or "").replace(" ", "").replace("　", "")
        if any(bad in s for bad in labels.GACHA_CLEAR_EXCLUDE):
            return False
        return any(p in s for p in labels.GACHA_CLEAR_PHRASES)

    @staticmethod
    def stamina_recover(text):
        """从扭蛋效果文本里算这张卡**总共能补多少精力**。

        2026-10-27 用户截图（第6周周六 45/130，三选一）：
            精力恢复++  立刻恢复90精力，之后每天恢复30点，持续4天，队友属性大幅提升
            金卡 / 100 币 / 带队友
        拆成 90 + 30×4 = 210 点。当时它和「运控增加++」同为 141 分，
        max() 取第一个把恢复卡丢掉了 —— 所以恢复量必须进评分。

        只认「恢复N精力」「恢复N点」两种写法，并且先排除清空/消耗型
        （那是相反效果，见 `is_stamina_clear`）。
        """
        s = (text or "").replace(" ", "").replace("　", "")
        # 「每天恢复N点，持续D天」这种写法不带「精力」二字，也要认。
        if "精力" not in s and "每天恢复" not in s:
            return 0
        # 注意不能复用 GACHA_CLEAR_EXCLUDE：那份列表含「恢复」，是给「清空卡」
        # 判断用的反向词表；恢复卡本身就带「恢复」。
        if "清空" in s or "消耗" in s:
            return 0
        total = 0
        # 持续型：每天恢复 N 点，持续 D 天
        m = re.search(r"每天恢复(\d+)点.*?持续(\d+)天", s)
        if m:
            total += int(m.group(1)) * int(m.group(2))
            s = s.replace(m.group(0), "")
        # 一次性：立刻恢复 N 精力 / 恢复 N 点 —— 可能有多句同时出现：
        # 「增强 | 立刻额外恢复30精力」叠在「立刻恢复60精力」上（2026-11-03 实拍，
        # 第2周星期五那张「精力恢复+」）。旧版只取第一处 → 110（应为 170），
        # 恢复价值被低估了三成。
        total += sum(int(m2.group(1))
                     for m2 in re.finditer(r"恢复(\d+)(?:精力|点)", s))
        return total

    @staticmethod
    def stamina_recover_immediate(text):
        """效果文本里**这次立刻**能补上的精力（用户 2026-11-08 溢出门用）。

        与 `stamina_recover`（总量，持续部分折算进来）不同：溢出判断只看
        「当前 + 立刻量 > 上限」，持续恢复是后面几天慢慢吃的，不占这次的口子。
        2026-11-08 实拍（第4周星期六扭蛋页，OCR 拆行）：
            立刻恢复60精力，之后每 / 天恢复20点，持续4天
        「每」和「天恢复20点」被拆成两框 —— 所以先把持续段整体摘掉再数「恢复N」，
        否则每天那 20 会被当成立刻量重复计入。
        """
        s = (text or "").replace(" ", "").replace("　", "")
        if "精力" not in s and "每天恢复" not in s:
            return 0
        if "清空" in s or "消耗" in s:
            return 0
        m = re.search(r"每天恢复(\d+)点.*?持续(\d+)天", s)
        if m:
            s = s.replace(m.group(0), "")
        return sum(int(m2.group(1))
                   for m2 in re.finditer(r"恢复(\d+)(?:精力|点)", s))

    @staticmethod
    def is_stamina_full_recover(text):
        """是不是「全部恢复（把精力拉满）」型效果（用户 2026-11-08 口径）。

        排除清空型（「清空全部精力 / 消耗所有精力」方向相反，见 `is_stamina_clear`）
        和带数字的恢复（「恢复60精力」是立刻型，不是拉满）——
        「全部/所有」后面跟「精力」才算，别把「获得全部属性」那种拽进来。
        """
        s = (text or "").replace(" ", "").replace("　", "")
        if "清空" in s or "消耗" in s:
            return False
        if "全部" not in s and "所有" not in s:
            return False
        return any(p in s for p in labels.GACHA_FULL_RECOVER_PHRASES)

    @staticmethod
    def is_stamina_recover_card(text):
        """这张卡/商品是不是恢复精力的（三类合一，见各门槛在 street.gacha_candidates）。

        三类（用户 2026-11-08 口径）：
        ① 立刻恢复xx → 当前 + 立刻量 > 上限就拒买（溢出，回不上的白花钱）
        ② 全部恢复 → 上限 - 当前 > 100 才买（缺口太小不值一张卡）
        ③ 接下来几日恢复xx → 当前 / 上限 < 1/2 才买（精力还多时不缺这几天的）
        """
        s = (text or "").replace(" ", "").replace("　", "")
        if "清空" in s or "消耗" in s:
            return False
        return (TrainingParsersMixin.stamina_recover(s) > 0
                or TrainingParsersMixin.is_stamina_full_recover(s))

    # 「打折」卡的价格药丸里有**两个**数字：左边划掉的原价、右边实付价。
    # 同一张卡的两个数字上下差几像素、左右差约 0.03w；相邻两张卡的药丸相隔约 0.25w。
    PRICE_ROW_TOL = 0.02
    PRICE_COL_TOL = 0.075

    def gacha_price_pills(self, texts):
        """把价格数字按「药丸」聚成一张卡一份，返回 [(price, cx, cy, top)]。

        2026-10-31 23:41 实跑（用户：「没有识别到中间的卡片有队友，又选择了
        左边的卡片」）：左侧「运控增加」是张打折蓝卡，药丸里 60/50 两个数字
        都被 OCR 读到 → 同一张卡被认成两张（日志 `运控增加(…/60币/…)、
        运控增加(…/50币/…)`，中间那张金卡也一样重复）。卡片一重复，
        同分时 max() 就退化成「按 OCR 顺序取第一张（最左边）」。
        所以先按行/列聚合，一张卡只留**最右边**那个数字（实付价）。
        """
        pills = []
        for t in texts:
            s = t.name.strip()
            if not s.isdigit() or not (1 <= int(s) <= 999):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (0.15 <= cx <= 0.85 and 0.60 <= cy <= 0.82):
                continue
            for p in pills:
                if (abs(p[2] - cy) <= self.PRICE_ROW_TOL
                        and abs(p[1] - cx) <= self.PRICE_COL_TOL):
                    if cx > p[1]:                   # 右边那个才是实付价
                        p[0], p[1], p[2], p[3] = int(s), cx, cy, t.y
                    break
            else:
                pills.append([int(s), cx, cy, t.y])
        return [tuple(p) for p in pills]

    def gacha_card_slots(self, texts):
        """扭蛋三张卡 = [(列中心 cx, 价格|None, 卡名底 y)]。

        两条路：
          1. 有价格药丸 → 用药丸定位（准），价格就是药丸上的数字；
          2. 没有 → 用卡名行反推列中心，价格 None（价格还没渲染出来）。

        返回的价格为 None 时，上层**不能**当成「这一页没卡」——
        那是 2026-11-01 15:09 白丢一次扭蛋的直接原因。
        """
        pills = self.gacha_price_pills(texts)
        if pills:
            return [(p[1], p[0], p[3]) for p in pills]
        # 兜底：按卡名行聚类出列
        name_boxes = []
        for t in texts:
            cy = (t.y + t.height / 2) / self.height
            s = t.name.strip()
            if not (self.GACHA_NAME_ROW[0] <= cy <= self.GACHA_NAME_ROW[1]):
                continue
            if not (2 <= len(s) <= 12):
                continue
            if not any("\u4e00" <= ch <= "\u9fff" for ch in s):
                continue
            name_boxes.append(((t.x + t.width / 2) / self.width, t.y + t.height))
        if not name_boxes:
            return []
        name_boxes.sort()
        cols = []
        for cx, bottom in name_boxes:
            if cols and abs(cx - cols[-1][-1][0]) < 0.13:
                cols[-1].append((cx, bottom))
            else:
                cols.append([(cx, bottom)])
        return [(sum(c for c, _ in col) / len(col), None, max(b for _, b in col))
                for col in cols]

    def read_gacha_cards(self, texts):
        """解析扭蛋三选一：卡名 / 品质色 / 是否有队友加成 / 分值 / 点击坐标。

        坐标靠「价格药丸」定位（每张卡底部一个 60/80 的数字）；
        价格**还没渲染出来**时退化成「按卡名行反推列中心」（`gacha_card_slots`），
        此时 `price` 为 None —— 买不买由上层决定，绝不能因为读不到价格就当没卡。
        「打折」卡药丸里的原价+实付价先用 `gacha_price_pills` 聚成一份，
        否则一张卡会读出两条记录（见那里的注释）。
        """
        frame = self.frame
        cards = []
        for cx, price, price_top in self.gacha_card_slots(texts):
            names = [n for n in texts
                     if self.GACHA_NAME_ROW[0] <= (n.y + n.height / 2) / self.height <= self.GACHA_NAME_ROW[1]
                     and abs((n.x + n.width / 2) / self.width - cx) < 0.13
                     and 2 <= len(n.name.strip()) <= 12
                     and any("\u4e00" <= ch <= "\u9fff" for ch in n.name.strip())]
            if not names:
                continue
            title = min(names, key=lambda n: abs(
                (n.y + n.height / 2) / self.height - 0.41))

            quality, hue, sat = self.card_band_quality(cx)
            # 彩色 = 2026-11-01 训练等级提升后解锁的最高品质，优先级最高
            # （顺序口径在 `scoring.QUALITY_RANK` / `places/street.py`，这里只判颜色）。
            mate = self.has_mate_badge(cx)
            # 「增强款」斜标签贴在卡片右上角（2026-10-30 截图：金/蓝卡都有）。
            # 卡内说明里也有「增强 额外获得体能+14」，所以必须限定在卡片顶部
            # y 0.24~0.35 那一带，不能整页找「增强」。
            enhanced = any(
                "增强" in n.name
                and 0.24 <= (n.y + n.height / 2) / self.height <= 0.35
                and abs((n.x + n.width / 2) / self.width - cx) < 0.14
                for n in texts)
            # 效果文本：卡名下沿到价格药丸之间的同列文字。
            # 2026-10-26 漏读「消耗所有精力」导致 72 精力买了清空扭蛋。
            # 价格还没渲染时（price_top = 卡名底），只取**说明文字那一段**
            # （GACHA_EFFECT_ROW），否则会把「确定」「能力详情」也当效果。
            if price is None:
                effect = " ".join(
                    n.name.strip() for n in texts
                    if title.y + title.height <= n.y
                    and self.GACHA_EFFECT_ROW[0] <= (n.y + n.height / 2) / self.height <= self.GACHA_EFFECT_ROW[1]
                    and abs((n.x + n.width / 2) / self.width - cx) < 0.13)
            else:
                effect = " ".join(
                    n.name.strip() for n in texts
                    if title.y + title.height <= n.y < price_top
                    and abs((n.x + n.width / 2) / self.width - cx) < 0.13)
            # 「+」不进分（用户 2026-11-02）：只代表同名卡片之间的档位差，
            # 没「+」的同名卡本身就是低一级品质。**这里不再算任何分数** ——
            # 选谁由 `places/street.gacha_candidates` 按显式档位决定
            # （品质 → 增强款 → 队友 → 恢复 → 价格），避免两套排序各说各话。
            cards.append({
                "name": title.name.strip(),
                "price": int(price) if price is not None else None,
                "quality": quality,
                "mate": mate,
                "enhanced": enhanced,
                "hue": hue,
                "sat": sat,
                "effect": effect,
                "clear": self.is_stamina_clear(effect),
                "recover": self.stamina_recover(effect),
                "recover_immediate": self.stamina_recover_immediate(effect),
                "full_recover": self.is_stamina_full_recover(effect),
                "x": int(title.x + title.width / 2),
                "y": int(title.y + title.height / 2),
            })
        return cards

    def card_band_quality(self, cx):
        """取扭蛋卡片标题条（y≈0.36~0.43）的底色，返回 彩/金/紫/蓝/其他 与 H/S。"""
        return self.card_quality_at(cx, self.GACHA_TITLE_BAND[0],
                                    self.GACHA_TITLE_BAND[1], rainbow=True)

    def card_quality_at(self, cx, y1f, y2f, rainbow=False):
        """按比例框取卡片底色判断品质，返回 彩/金/紫/蓝/其他 与中位 H/S。

        `cx` / `y1f` / `y2f` 都是比例（0~1）：扭蛋页用标题条 0.36~0.43，
        游戏厅卡片用自己的卡面区间（2026-10-27），两种情况共用同一套色相阈值。
        `rainbow=True` 时才判「彩色」：只有扭蛋页的标题条是**整条彩虹渐变**
        （用户 2026-11-01 训练等级提升后解锁彩蛋），游戏厅卡片底色仍是单色，
        传 False 就不做这一步，免得把带高光的卡误判成彩色。
        """
        frame = self.frame
        if frame is None:
            return "其他", -1, -1
        h, w = frame.shape[:2]
        x1 = int(w * (cx - 0.06))
        x2 = int(w * (cx + 0.06))
        y1, y2 = int(h * y1f), int(h * y2f)
        roi = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        if roi.size == 0:
            return "其他", -1, -1
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        m = (sat > 60) & (val > 120)
        if m.sum() < 50:
            return "其他", -1, -1
        hmed = float(np.median(hue[m]))
        smed = float(np.median(sat[m]))
        if rainbow and self.band_is_rainbow(hue[m]):
            return "彩", hmed, smed
        if 10 <= hmed <= 45 and smed >= 90:
            return "金", hmed, smed
        if 115 <= hmed <= 160:
            return "紫", hmed, smed
        if 90 <= hmed < 115:
            return "蓝", hmed, smed
        return "其他", hmed, smed

    @staticmethod
    def band_is_rainbow(hues):
        """色相是否摊成彩虹（= 彩色品质）。判据见 RAINBOW_BAND_* 的注释。

        实拍（logs/now.jpg，1549x871）：彩色卡 3 档 ≥0.10、最大档 0.51；
        金卡/紫卡/蓝卡最大档 0.88~1.00。两条判据同时用：档数挡「深色卡带点杂色」，
        最大档占比挡「整片同色但边缘蹭到背景」。
        """
        if hues is None or len(hues) < 50:
            return False
        counts, _ = np.histogram(hues, bins=list(range(0, 181, 30)))
        frac = counts / float(counts.sum())
        return (int((frac >= RAINBOW_BAND_BIN_FRAC).sum()) >= RAINBOW_BAND_MIN_BINS
                and float(frac.max()) < RAINBOW_BAND_MAX_TOP)

    # ---------- 通用「品质卡」判定（未登记事件页的兜底，2026-11-03） ----------
    # 卡片**标题条**按品质上色，色相阈值与扭蛋/游戏厅共用（card_quality_at）。
    # 2026-11-03 做饭页实测（logs/live/cook_now.jpg，1549x871，取标题条）：
    #   隔夜饭（蓝）       H/S/V = 103/121/197
    #   菜市场的普通食材（紫）= 135/115/188
    #   珍稀食材（金）      = 21/251/193
    # 用户口径（2026-11-03）：优先级 彩 > 金 > 紫 > 蓝；同品质再比队友数量
    # （队友多的优先、有队友的优先于没队友的）。**排序**在
    # `scoring.quality_card_order`，这里只负责「这张卡是什么颜色、几个人」。
    # 标题条本身没颜色时，再往上看卡片头部这一段（做饭页卡头 ≈ 标题上方 0.014~0.115 屏高）。
    QUALITY_HEAD_BAND = (0.115, 0.014)
    # 候选卡片标题的行范围（比例）：做饭页 0.41、技能卡 0.43、餐厅卡 0.53 都在内。
    QUALITY_CARD_ROWS = (0.28, 0.62)
    # 同一行的纵向容差：标题中心 y 相差不到 0.06 屏高就算一行。
    QUALITY_ROW_TOL = 0.06

    def card_quality_color(self, box):
        """这个标题框所在卡片的品质色（彩/金/紫/蓝/其他）。

        先取**标题条**（box 自己那一条横带）：做饭页三张卡的标题条就是品质色；
        标题条是白底/透明时，再往上看**卡片头部**。
        **只看颜色、不看名字** —— 名字 OCR 会被截断（实机帧读到「隔夜…」
        「菜市场的普通食…」），按名字匹配必然漏。
        """
        h = self.height
        cx = (box.x + box.width / 2) / self.width
        y1f, y2f = box.y / h, (box.y + box.height) / h
        quality = self.card_quality_at(cx, y1f, y2f, rainbow=True)[0]
        if quality != "其他":
            return quality
        up, down = self.QUALITY_HEAD_BAND
        return self.card_quality_at(cx, max(0.0, y1f - up),
                                    max(0.0, y1f - down), rainbow=True)[0]

    def quality_cards(self, texts):
        """横向并排的 2~3 张「标题条按品质着色」的卡，按 x 排好返回。

        每项：`{"box", "name", "quality", "rank", "mates"}`；不足两张返回 []。
        判据（缺一不可）：
          1. 同一行、2~3 张、横向铺开 ≥ 0.25 屏宽；
          2. 标题 2~10 个字、不含数字、不是角标/装饰词；
          3. **至少两张**能判出品质色 —— 只有一张有色的是普通按钮/标题，不是选项页。

        队友数只数**卡片上沿之上**的小头像：食材/扭蛋的卡头本身就是橙金色，
        照 `mates_on_card` 原样数会把卡头图标当成队友（做饭页金卡实测被数成 2 个）。
        深蓝头像条的判据仍由 `mates_on_card` 负责，它只看标题上方那一条带。
        """
        h, w = self.height, self.width
        rows = {}
        for t in texts:
            s = t.name.strip()
            if not (2 <= len(s) <= 10) or any(ch.isdigit() for ch in s):
                continue
            if self.is_label(s) or self.is_decor(s):
                continue
            cy = (t.y + t.height / 2) / h
            if not (self.QUALITY_CARD_ROWS[0] <= cy <= self.QUALITY_CARD_ROWS[1]):
                continue
            rows.setdefault(round(cy / self.QUALITY_ROW_TOL), []).append(t)
        icons = self.find_teammate_icons(0.0, 1.0)
        best = []
        for group in rows.values():
            if not (2 <= len(group) <= 3):
                continue
            group.sort(key=lambda t: t.x)
            cards = []
            for t in group:
                quality = self.card_quality_color(t)
                head_top = t.y - h * self.QUALITY_HEAD_BAND[0]
                mates = self.mates_on_card(
                    t, [p for p in icons if p[1] < head_top])
                cards.append({"box": t, "name": t.name.strip(),
                              "quality": quality, "mates": mates})
            if sum(1 for c in cards if c["quality"] != "其他") < 2:
                continue
            span = (cards[-1]["box"].x + cards[-1]["box"].width
                    - cards[0]["box"].x)
            if span < w * 0.25:
                continue
            if len(cards) > len(best):
                best = cards
        return best

    def has_mate_badge(self, cx):
        """卡头左上角有没有队友标记（= 这次带队友加成）。

        2026-11-07 重写（用户：「不可能遮住的，识别区域有问题」——他说对了）：
        旧判据 `b > r+20` 在**徽章完全可见**的帧上也返回 False。定因（离线
        复现脚本 `tools/check_mate_badge.py`，1408 帧历史扭蛋页回归）：
          1. ROI 上沿 0.22h 把**卡片上方的灰蓝背景带**框了进来 —— 背景色
             BGR≈(101,111,128)，`b-r≈30` 就命中旧判据（实测旧掩码里 `b-r`
             的 p50=42）；徽章胶囊（y 213~266）和背景带（y 191~207）在
             掩码里连成一整块 214×58，长宽比 3.69 超 2.8 上限被拒 → 漏检；
          2. 徽章自己分离出来的小块 49×25，面积占比 0.019 < 0.03 下限 → 也拒。
        也就是说旧判据在当前版本画面上**从来没对过**（10-31「三张金卡都记成
        有队友」、11-03「有队友的卡没识别到」两次事故同源：前者是 orange
        分支误报，后者是 deep 分支漏检，两头都坏）。

        新判据（全部离线验证过）：
          - ROI 上沿收到 **0.245h**：避开背景带（徽章顶 213/871≈0.244h），
            保留整枚徽章；
          - 色判据 `b-r>=45 且 S>=95 且 r<150`：徽章胶囊底色 b-r≈63~65、
            S≈117；背景带 b-r≈11~30、S≈61~68 —— 用**饱和度**把两者分开，
            旧判据只看 b-r 分不开；金卡卡头 b-r 为负不会命中（10-31 的
            误报坑不会复发，orange 兜底分支一并删除）；
          - 连通块加**实心率 fill>=0.45**：横幅文字描边的碎片块 fill≈0.2
            全被拦掉（191616/191808 横幅帧验证）。
          - 真徽章几何（1408 帧回归实测）：136~140×53~54 @ y=213、
            长宽比 2.5~2.6、fill≈0.5、占 ROI 0.16~0.18。

        遗留盲区：「再买一次！」橙色横幅（x 210~382, y 203~280）**物理盖住**
        左卡徽章位置（x 215~356, y 212~266），那几拍任何颜色判据都读不到 ——
        由 `settle_gacha_cards` 的「mate 按卡名当天缓存」兜底（见 weekend.py）。
        """
        frame = self.frame
        if frame is None:
            return False
        h, w = frame.shape[:2]
        x1 = int(w * (cx - 0.12))
        x2 = int(w * (cx + 0.02))
        y1, y2 = int(h * 0.245), int(h * 0.36)
        roi = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        if roi.size == 0:
            return False
        b = roi[:, :, 0].astype(int)
        r = roi[:, :, 2].astype(int)
        s = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)[:, :, 1].astype(int)
        deep = (b - r >= 45) & (s >= 95) & (r < 150)

        roi_area = float(deep.size)
        m = deep.astype(np.uint8)
        if not m.any():
            return False
        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(m, 8)
        for i in range(1, n):
            ww, hh, area = int(stats[i, 2]), int(stats[i, 3]), int(stats[i, 4])
            if area < 100:
                continue
            frac = area / roi_area
            ar = ww / max(hh, 1)
            fill = area / float(ww * hh)
            # 实心率是横幅帧的最后一道闸：描边碎片 fill≈0.2，真徽章≈0.5
            if 0.04 <= frac <= 0.30 and 0.5 <= ar <= 3.0 and fill >= 0.45:
                return True
        return False

    GO_OUT_WORD = "出行"

    def find_go_out_button(self, texts):
        """找「出行」**按钮**的文本框，找不到返回 None。

        2026-10-31 实跑（第3周 星期四 训练日，官方日志原文）：
            18:50:02 体力 130，进入训练科目
            18:50:03 left_click 今天可以出行哦！要不 (950, 182)
            18:50:04 点击「今天可以出行哦！要不」→ 训练日有「出行」入口 → 先外出
            18:50:05 体力 130，进入训练科目
        旧版 `click_text("出行")` 是**包含匹配**，命中的是对话框里的台词，
        右侧按钮一下都没点到；而「今天已经出行过」的标记已经置上了，
        下一拍就掉进 else 去点「训练」（用户：「没有点出行，反而默认进了训练」）。

        所以这里只认**整词**「出行」：台词是长句，永远匹配不上。
        OCR 把短按钮拆成「出」「行」两个单字框时（商店 商/店 同款坑）再拼一次。

        2026-11-02 再加一档「短词」：按钮上带箭头/角标时 OCR 会读成
        「出行▶」这种 2~6 字的框，纯整词匹配不到。台词最短也有 10 个字
        （「今天可以出行哦！要不」），用长度就能和按钮分开 ——
        绝不能退回子串匹配（那正是 2026-10-31 空点击台词的根因）。
        """
        cands = [t for t in texts if t.name.strip() == self.GO_OUT_WORD]
        if not cands:
            cands = [t for t in texts
                     if self.GO_OUT_WORD in t.name.strip()
                     and len(t.name.strip()) <= 6]
        if not cands:
            cands = self._split_go_out(texts)
        if not cands:
            return None
        x1, y1, x2, y2 = Defs.region("go_out")

        def rank(t):
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            in_box = 1 if (x1 <= cx <= x2 and y1 <= cy <= y2) else 0
            # 按钮区里的优先，其次越靠右下越像按钮
            return (in_box, cy, cx)

        return max(cands, key=rank)

    @staticmethod
    def _split_go_out(texts):
        """「出」「行」被 OCR 拆成两个单字框时，合成一个「出行」框。"""
        outs = [t for t in texts if t.name.strip() == "出"]
        ins = [t for t in texts if t.name.strip() == "行"]
        for a in outs:
            acx, acy = a.x + a.width / 2, a.y + a.height / 2
            for b in ins:
                bcx, bcy = b.x + b.width / 2, b.y + b.height / 2
                if abs(acy - bcy) > a.height * 0.8:
                    continue
                if abs(acx - bcx) > a.width * 2.5:
                    continue
                x = min(a.x, b.x)
                y = min(a.y, b.y)
                return [Box(x=x, y=y,
                            width=max(a.x + a.width, b.x + b.width) - x,
                            height=max(a.y + a.height, b.y + b.height) - y,
                            name="出行")]
        return []

    def weekend_state(self):
        """当天的状态（去过哪些地点、商店看没看、公园选什么）。

        **实现已搬到 `training/daycycle.py`**（用户 2026-11-01 的架构：
        全局日期 + 两个新一天入口放同一个模块）。这里只保留旧名，
        让现有调用点不用改 —— 新代码请直接用 `day_state()`。

        `visited` 是关键：游戏厅进去发现没有可跳过的小游戏会退出，
        如果不记录「今天已经去过」，下一拍又会点进游戏厅，来回循环（2026-10-26）。
        """
        return self.day_state()

    def read_fail_rate(self, texts):
        """读取当前选中科目的失败率（0~100），读不到返回 None。"""
        joined = "".join(t.name for t in self.texts_in_region(texts, "fail_rate")).replace(" ", "")
        if m := labels.FAIL_RATE_RE.search(joined):
            return int(m.group(1))
        return None

    def subjects_from_texts(self, texts):
        """从 OCR 结果取「科目名 -> 框」，并把被 OCR 拆成两个字的科目合起来。

        2026-10-27 第 4 周度假页实跑：「防守」被读成「防」「守」两个单字框，
        科目直接漏掉（用户：「两次防守有队友都没有被识别到就直接跳过了」）。
        这里把同一行相邻的两个单字拼回科目名，再用并集框当点击目标。
        """
        pool = self.texts_in_region(texts, "subject_list")
        found = {}
        for t in pool:
            name = t.name.strip()
            if name in labels.TRAINING_SUBJECTS:
                found.setdefault(name, t)
        singles = [t for t in pool if len(t.name.strip()) == 1]
        for i, a in enumerate(singles):
            sa = a.name.strip()
            acy = a.y + a.height / 2
            for b in singles[i + 1:]:
                name = sa + b.name.strip()
                if name not in labels.TRAINING_SUBJECTS or name in found:
                    continue
                if abs((b.y + b.height / 2) - acy) > a.height * 0.8:
                    continue
                gap = b.x - (a.x + a.width)
                if gap > a.width * 1.5:
                    continue
                x = min(a.x, b.x)
                y = min(a.y, b.y)
                w = max(a.x + a.width, b.x + b.width) - x
                h = max(a.y + a.height, b.y + b.height) - y
                found[name] = Box(name=name, x=x, y=y, width=w, height=h)
                self.log_debug(f"科目名被拆开，已合并：{sa}+{b.name.strip()} → {name}")
                break
        # 2026-10-29 第 4 周海边合宿 星期三实跑：科目行是「防守 + MAX」（满级标签），
        # OCR 把「守」吞进了「MAX」，整帧只剩一个「防」字 → 两个字拼不回来，
        # 「有队友的防守」连探测都没探测就被跳过（用户：「有队友的防守训练被跳过了」）。
        # 六个科目的首字/尾字在全表里都唯一，所以只剩一个字时也能确定是哪一行；
        # 只在「整名没读出来、且这个字只属于唯一一个科目」时才补，避免乱认。
        for t in singles:
            ch = t.name.strip()
            matches = [n for n in labels.TRAINING_SUBJECTS
                       if n not in found and (n.startswith(ch) or n.endswith(ch))]
            if len(matches) != 1:
                continue
            name = matches[0]
            found[name] = Box(name=name, x=t.x, y=t.y, width=t.width, height=t.height)
            self.log_debug(f"科目名只剩一个字，按唯一首/尾字补全：{ch} → {name}")
        return found

    def current_subject(self, texts):
        """当前已选中的科目：右边有「决定」的那一行。"""
        decide = self.match_text(texts, labels.BUTTONS["decide"])
        if decide is None:
            return None
        dy = decide.y + decide.height / 2
        cands = []
        for t in self.texts_in_region(texts, "subject_list"):
            if t.name.strip() not in labels.TRAINING_SUBJECTS:
                continue
            cy = t.y + t.height / 2
            if abs(cy - dy) < 45 * self.height / 1080.0:
                cands.append(t)
        return max(cands, key=lambda t: t.height).name.strip() if cands else None

    def find_teammate_icons(self, x_from=0.78, x_to=1.0):
        """在指定横向范围内找「有队友」的橙色小人头像，返回中心点列表。

        实测（2026-10-25）：小人是 42x42 的纯橙圆，主色 (255,145,0)；
        右上角「决定」按钮是亮黄 (255,237,75)，**G 通道高 90**，所以卡 g<190 就能排除。
        再用连通域尺寸过滤（42px 上下），后期按钮图标换色也不影响。

        默认只扫右侧科目区（0.78~1.0）；餐厅两个打工选项在屏幕中间，
        所以要能传整屏范围（2026-10-29）。
        """
        frame = self.frame
        if frame is None:
            return []
        h, w = frame.shape[:2]
        x0, x1 = int(w * x_from), int(w * x_to)
        roi = frame[:, x0:x1]
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        mask = ((r > 225) & (g > 100) & (g < 190) & (b < 70)).astype(np.uint8)
        if not mask.any():
            return []
        n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
        size = 42 * w / 1920.0                   # 按分辨率缩放
        icons = []
        for i in range(1, n):
            _x, _y, ww, hh, area = stats[i]
            if not (size * 0.6 <= ww <= size * 1.6 and size * 0.6 <= hh <= size * 1.6):
                continue
            if area < size * size * 0.3:
                continue
            icons.append((cent[i][0] + x0, cent[i][1]))
        return icons

    def mates_on_card(self, box, icons):
        """这张卡片**左上角**挂了几个队友小头像（用户 2026-10-29 口径）。

        餐厅打工的两个选项是「卡片」，队友头像排在卡片左上角，名字/效果写在
        卡片下方，所以只数文字框**上方偏左**那一片：
        纵向从文字框上沿往上 2.5 个文字高，横向从文字中心往左 1.4 个文字宽。
        数不到就返回 0 —— 调用方拿 0 当平局处理（退化成「选右边」）。
        """
        if not icons or box is None:
            return 0
        cx = box.x + box.width / 2
        x1, x2 = cx - box.width * 1.4, cx + box.width * 0.1
        y1, y2 = box.y - box.height * 2.5, box.y + box.height * 0.3
        n = sum(1 for ix, iy in icons if x1 <= ix <= x2 and y1 <= iy <= y2)
        if n:
            return n
        # 2026-10-30 用户实机（餐厅，报告 #12 / #84）：队友标记换成了
        # **深蓝底 + 头像的圆**，不是橙色小人 —— 橙色判据必然返回 0，
        # 于是两张卡都记成「0 人」，最后按「右边优先」选错。
        # 这里按位置兜底：文字框上方 2.4~4.6 个字高、横向 ±0.55 个字宽那一条带，
        # 深蓝像素占比 > 0.08 记 1 个队友。
        # 实测（1030-163846 / 1030-164421 两帧）：有标记 0.2226，没有 0.0123。
        frame = self.frame
        if frame is None:
            return 0
        fh, fw = frame.shape[:2]
        bx1 = int(max(0, cx - box.width * 0.55))
        bx2 = int(min(fw, cx + box.width * 0.55))
        by1 = int(max(0, box.y - box.height * 4.6))
        by2 = int(max(0, box.y - box.height * 2.4))
        roi = frame[by1:by2, bx1:bx2]
        if roi.size == 0:
            return 0
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        deep = (b > r) & (r < 120) & (g < 120) & (b < 160)
        return 1 if float(deep.mean()) > 0.08 else 0

    # ---------- 餐厅打工卡：头顶胶囊里有几个队友（2026-11-03） ----------

    # 现版本餐厅两卡的队友条是「深蓝胶囊 + chibi 头像」，胶囊宽度随人数变，
    # 但 `mates_on_card` 的深蓝兜底只能给「有/没有」（0 或 1）：
    # 实测帧 docs/shots/restaurant_cards.png（1549x871）左卡 2 人、右卡 1 人，
    # 两张都读成 1 → 平局 → 选右边，用户要的「桌游GM 多两个就选左边」永远不生效。
    # 所以直接**数脸**：同一套 chibi 的脸是同一块很亮的暖肤色，头发/背景/卡面都不是这个色。
    # 亮暖肤色：r≥232、g≥205、b≥190、r-b≥20（前三条排除深蓝胶囊，最后一条排除纯白/灰白）。
    MATE_SKIN = (232, 205, 190, 20)   # r, g, b, r-b 下限
    MATE_FACE_AREA = (0.00020, 0.00160)   # 占整帧面积的比例（1549x871 实测 793~1008）
    MATE_FACE_AR = (0.55, 1.50)           # 脸块的宽高比（实测 0.73 / 0.97 / 1.16）

    def mate_face_count(self, box, up=6.0, span=1.1):
        """标题框**上方**那排 chibi 头像有几个（按肤色连通块数）。读不到帧返回 None。

        只在标题上方 `up` 个字高、横向 `span` 个字宽的范围里数
        （餐厅实测：头像在标题上方 3.3~4.2 个字高、左右各 0.4 个字宽，
        6.0 个字高足够宽松，也不会碰到相邻那张卡）。
        面积/宽高比过滤掉环境里的碎块；两个头之间隔着头发，不会粘成一块。
        """
        frame = self.frame
        if frame is None or box is None:
            return None
        h, w = frame.shape[:2]
        hs = max(1, box.height)
        cx = box.x + box.width / 2
        x1 = int(max(0, cx - span * box.width))
        x2 = int(min(w, cx + span * box.width))
        y1 = int(max(0, box.y - up * hs))
        y2 = int(max(0, box.y - 0.5 * hs))
        if x2 <= x1 or y2 <= y1:
            return None
        roi = frame[y1:y2, x1:x2]
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        r_lo, g_lo, b_lo, rb_lo = self.MATE_SKIN
        skin = ((r >= r_lo) & (g >= g_lo) & (b >= b_lo)
                & ((r - b) >= rb_lo)).astype(np.uint8)
        if not skin.any():
            return 0
        skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(skin, 8)
        area_lo, area_hi = (w * h * f for f in self.MATE_FACE_AREA)
        ar_lo, ar_hi = self.MATE_FACE_AR
        count = 0
        for i in range(1, n):
            ww, hh, area = int(stats[i, 2]), int(stats[i, 3]), int(stats[i, 4])
            if not (area_lo <= area <= area_hi):
                continue
            if not (ar_lo <= ww / max(hh, 1) <= ar_hi):
                continue
            count += 1
        return count

    def option_mate_counts(self, boxes):
        """一排选项/卡片各挂几个队友 → (与 `boxes` 同序的人数, 用的哪套判据)。

        餐厅两卡和公园三图标用的是**同一套**视觉（深蓝胶囊 + chibi 头像挂在名字上方），
        所以判据也只有一处：**先数脸，只要有一个框数到脸，就只认脸**；
        整页一张脸都没数到（= 旧版深蓝圆标记的界面）才退回 `mates_on_card` 兜底。

        为什么要这道闸门（2026-11-03 餐厅实跑）：兜底分不清「队友标记」和
        「卡片自己的图标」，会给没有队友的那张白送 1 个 → 2 人 vs 0 人读成 2 人 vs 1 人，
        「多两个才改选」的规则永远差 1 个、一次都不生效。
        """
        faces = [self.mate_face_count(b) for b in boxes]
        if any(faces):
            return [f or 0 for f in faces], "数脸"
        icons = self.find_teammate_icons(0.0, 1.0)
        return [self.mates_on_card(b, icons) for b in boxes], "深蓝兜底"

    # ---------- 炒股选股页：卡片头顶的「队友头像」（2026-11-02） ----------

    # 队友头像是同一套 chibi 表情，**张开的嘴是同一块颜色**：
    #   r - g > 58 且 r > 175 且 b < 215（脸/皮肤只有 r-g ≈ 40~55，分得开）。
    # 为什么不用别的判据（2026-11-02 都试过，见 logs/debug/*stock*.jpg 四帧）：
    #   · **气泡文字**（「就选它了！」）一张卡最多一个，2 个队友也只冒一句 → 数不出人数；
    #   · 头发/皮肤颜色每个角色都不一样（橙发/棕发/金发都见过）；
    #   · 白描边在浅蓝卡面、白色气泡上分不开（纯白连通域只剩卡边和气泡）。
    # 位置：卡片类型名上方约 1.5~3 个字高那一条横带。四帧实测：3 个嘴都在 y=294、
    # 大小 14~16 x 7~8 像素（1549x871），所以按屏宽比例卡尺寸/面积即可换分辨率。
    STOCK_MOUTH_RG = 58
    STOCK_MOUTH_R_MIN = 175
    STOCK_MOUTH_B_MAX = 215

    def stock_teammate_counts(self, cards):
        """每张卡头顶挂了几个队友头像；返回和 `cards` 同序的个数列表。

        `cards` 是「稳健型/平衡型/风险型」这些类型名的文本框（按 x 排好序）。
        每张嘴归给横向最近的那张卡（卡的列心 = 类型名中心），离所有卡都远的丢掉。
        数不到就是 0 —— 调用方拿 0 当「没队友」，并保留气泡/价格兜底。
        """
        if not cards:
            return []
        counts = [0] * len(cards)
        frame = getattr(self, "_tick_frame", None)
        if frame is None:
            frame = self.frame
        if frame is None or not frame.any():
            return counts
        h, w = frame.shape[:2]
        y_label = min(b.y for b in cards)
        h_label = max(b.height for b in cards)
        y1 = int(max(0, y_label - h_label * 3.4))     # 头像顶（嘴再往下约 1.2 个字高）
        y2 = int(max(0, y_label - h_label * 0.7))
        if y2 <= y1:
            return counts
        b = frame[:, :, 0].astype(int)
        g = frame[:, :, 1].astype(int)
        r = frame[:, :, 2].astype(int)
        mask = (((r - g) > self.STOCK_MOUTH_RG)
                & (r > self.STOCK_MOUTH_R_MIN)
                & (b < self.STOCK_MOUTH_B_MAX)).astype(np.uint8)
        mask[:y1, :] = 0
        mask[y2:, :] = 0
        n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
        centers = [c.x + c.width / 2 for c in cards]
        for i in range(1, n):
            x, y, ww, hh, area = stats[i]
            # 嘴是**横着的窄条**；头发块是 38x40、脸是 30x25，宽高比过不了这关。
            if not (0.004 * w <= ww <= 0.025 * w and 0.001 * w <= hh <= 0.015 * w):
                continue
            if ww < hh * 1.2:
                continue
            if not (0.000012 * w * w <= area <= 0.0004 * w * w):
                continue
            cx = cent[i][0]
            k = min(range(len(centers)), key=lambda j: abs(cx - centers[j]))
            if abs(cx - centers[k]) > 0.18 * w:      # 离所有卡都远：不是卡上的头像
                continue
            counts[k] += 1
        self.log_debug(f"炒股：卡片头顶队友头像 {counts}（横带 y={y1}~{y2}）")
        return counts

    def is_training_plan(self, texts):
        """是不是训练计划（日历）页。

        判据要能挡掉训练日台词「今天的训练计划，我看看」——那句话同样含
        「的训练计划」。真正的那一页有**整周课表**：至少两个「星期X」列 + 三节课，
        台词页只有当天那一个「星期四」，课表数也不够。
        """
        if self.match_text(texts, "的训练计划") is None:
            return False
        if self.read_plan_captain(texts):
            return True
        day_cols = sum(1 for t in texts
                       if t.name.strip() in ("星期二", "星期三", "星期四",
                                             "星期五", "星期六", "星期日"))
        lessons = sum(1 for t in texts if "训练" in t.name)
        return day_cols >= 2 and lessons >= 3

    def read_plan_captain(self, texts):
        """训练计划（日历）页左上角「XX的训练计划」里的 XX。

        实测（logs/unknown/1029-093636_register_stuck_s636.txt，1349x759）OCR 会把
        名字和标题拆成两个框：
            望月     (184,74,158,91)
            的训练计划 (339,96,294,62)
        所以两种写法都要认：整段「真崎的训练计划」，或者「的训练计划」左边那个框。

        2026-10-29 实跑踩坑：名字被漏识别时，标题左边只剩**「星期」列头**
        （同一行区域，离标题 60px），于是「队长」被认成「星期」。
        现在候选名必须像人名（`_looks_like_captain`），并且**优先取能在角色表里
        查到的那个**，一个都不像就返回 None（让上一拍重试，而不是认一个假名字）。
        训练日台词「今天的训练计划，我看看」不在左上角区域，不会被当标题。
        """
        area = self.texts_in_region(texts, "plan_title")
        cands = []
        for t in area:
            s = t.name.replace(" ", "").strip()
            m = labels.PLAN_TITLE_RE.match(s)
            if m:
                cands.append(m.group(1))
        title = next((t for t in area
                      if t.name.replace(" ", "").strip() == "的训练计划"), None)
        if title is not None:
            cy = title.y + title.height / 2
            tcx = title.x + title.width / 2
            for t in area:
                if t is title:
                    continue
                # 名字就在标题左边、同一行，而且是**大字号**。
                # 2026-10-29 真帧（logs/live_run/plan_*.txt）：
                #     闪堂      (187,74,163,89)
                #     的训练计划 (339,91,295,71)   ← 名字右边缘 350 压过标题左边 339
                #     星期      (182,170,68,35)    ← 列头在正下方 96px
                # 所以：纵向 0.8 个标题高内、横向允许 5% 屏宽重叠、名字高度 ≥ 0.6 标题高。
                if abs((t.y + t.height / 2) - cy) >= title.height * 0.8:
                    continue
                if (t.x + t.width / 2) >= tcx:
                    continue
                gap = title.x - (t.x + t.width)
                if gap < -self.width * 0.05 or gap > self.width * 0.10:
                    continue
                if t.height < title.height * 0.6:
                    continue
                cands.append(t.name.replace(" ", "").strip())
        cands = [c for c in cands if self._looks_like_captain(c)]
        for c in cands:                       # 能查到流派的优先（最可靠）
            if self.style_for_name(c):
                return c
        return cands[0] if cands else None

    @staticmethod
    def _looks_like_captain(name):
        """这段字像不像队长名（1~6 个汉字，可以带「·」）。

        必须挡掉日历上的非人名：列头「星期」、周次「第1周」、状态「大胜」、
        按钮「训练」「比赛」… 否则名字一旦漏识别，就会拿这些当队长。
        """
        s = (name or "").replace(" ", "").strip()
        # 队长名至少两个字：日历上的单字全是噪声（「第」5周、「星」期…），
        # 2026-10-29 实跑就此把「第」当成了队长。
        if not (2 <= len(s) <= 6):
            return False
        if s.startswith("星期") or s in ("今天", "今日", "训练", "计划", "比赛",
                                        "周", "月", "日", "胜", "负"):
            return False
        if any(ch.isdigit() for ch in s):
            return False
        return all("\u4e00" <= ch <= "\u9fff" or ch in "·・" for ch in s)

    @staticmethod
    def style_for_name(name):
        """角色名（可能只有姓氏）→ 流派；查不到返回 None。

        查表用 `roster.style_by_name()`（`docs/roster/*.json` 的 `style_tag`）。
        日历标题只显示姓氏，所以按前缀匹配；多个前缀都命中（「羽」→ 羽田 / 羽濑川）
        时取**更长的那个**，长度一样才算歧义（返回 None，宁可不动）。

        2026-10-31：两侧都先过 `labels.norm_name` —— 游戏里「亚理纱·贾斯敏」可能被 OCR
        读成「亚理纱贾斯敏」，底部名字行还可能带「SR-」前缀，带间隔点直接比会漏。

        2026-11-03：错字别名（STYLE_ALIASES）**也参与前缀匹配** —— 日历/队长页
        可能写成「羽漱川」，只映射全名的话姓氏前缀就查不到了（回归测试抓到的）。
        """
        if not name:
            return None
        s = labels.norm_name(name).replace(" ", "").strip()
        # 错字先归到规范名（2026-11-03：羽漱川湊 → 羽濑川湊）。
        # 别名只做查询兼容，不进角色表 —— 那张表同时是监控页下拉的数据源。
        for wrong, right in STYLE_ALIASES.items():
            if s == labels.norm_name(wrong):
                s = labels.norm_name(right)
                break
        style_map = roster.style_by_name()
        variants = list(style_map.items())
        for wrong, right in STYLE_ALIASES.items():
            style = style_map.get(right)
            if style is not None:
                variants.append((wrong, style))
        best, best_len, tie = None, 0, False
        for full, style in variants:
            f = labels.norm_name(full)
            if s == f:
                return style
            if not (f.startswith(s) or s.startswith(f)):
                continue
            if len(f) > best_len:
                best, best_len, tie = style, len(f), False
            elif len(f) == best_len and style != best:
                tie = True
        return None if tie else best

    def has_teammate_icon(self, box, icons):
        """这个科目的右边有没有小人头像。

        两代 UI 都支持（2026-10-26 实测）：
        - 旧版：头像在按钮**右上角**，中心比文字中心高约 55px；
        - 新版：头像在按钮**右侧居中**，和文字中心几乎同高。
        不能用 ±80 的大范围：相邻两行只差 100px，会同时命中两个科目
        （旧代码就是这么把 5 个科目全当成「有队友」的）。
        """
        if not icons or box is None:
            return False
        cx = box.x + box.width / 2
        cy = box.y + box.height / 2
        old_ui_y = cy - 55 * self.height / 1080.0
        tol_old = 20 * self.height / 1080.0
        tol_new = 35 * self.height / 1080.0
        return any(
            ix > cx and (abs(iy - old_ui_y) < tol_old or abs(iy - cy) < tol_new)
            for ix, iy in icons)

    def probe_subject(self, box):
        """点开一个科目，返回预览信息：队友数 / 装备数 / 满经验数 / 彩虹边框。"""
        name = (box.name or "").strip()
        self.click_box(box, after_sleep=0.55)
        texts = self._ocr_region()
        self.log_debug(f"场上角色名：{self.scene_names(texts)}")
        self.dump_subject_frame(name, texts)
        return self.analyze_preview(texts, subject=name)

    def dump_subject_frame(self, subject, texts):
        """把科目预览帧存一份（限流），用于事后校准金框/彩框。

        为什么要它：2026-10-29 用户两次说「黄框/彩框没识别到」，但事后没有帧，
        只能靠猜。文件名带科目名，复盘时直接量环带占比。

        2026-11-12：上限 12 → 36。12 张只够跑到第 2 周，第 4 周的「彩框+金框」
        漏识别事故（用户报「连续两天金框都没识别到」）根本没有帧可查。
        """
        if getattr(self, "_subject_dumps", 0) >= 36:
            return
        frame = self.frame
        if frame is None or not frame.any():
            return
        idx = getattr(self, "_subject_dumps", 0)
        # 文件名只用 ASCII（evidence 那边统一生成）；科目名写进 txt 头
        if self.evidence.dump_frame("subject", texts, frame, idx=idx,
                                    header=f"# subject={subject}"):
            self._subject_dumps = idx + 1

    def analyze_preview(self, texts, subject=None):
        """读一帧科目预览，返回打分要用的全部字段。

        - 队友 / 经理：场景里飘在角色头顶的名字（经理的名字牌就是「球队经理」，
          会被当成队友名，必须单独扣出来）；
        - 器材数 / 满经验：底部器材卡的经验条与 MAX 字样（已实测）；
        - 金框 / 彩框：**训练科目按钮外面那一圈**（用户 2026-10-27 口径）。
          框本来就是装备/经理经验满了才点亮的，如果再去数器材卡上的金框，
          等于和「装备」重复计分，所以器材卡只按张数算一次。

        `subject` = 这次要评分的科目名。框是**常驻**的（用户 2026-10-27：
        「金框彩框很显眼，不需要选中也会亮起」），所以按科目名找它自己那一行，
        不要用「决定按钮所在行」去猜（旧做法在 56 次探测里错了 10 次）。
        """
        scene = self.read_scene(texts)
        equip, full = self.read_equipment(texts)
        crit = self.has_crit_border(texts, subject=subject)
        # 彩框和金框**互斥**：彩虹边框里本来就有一段橙黄，若不互斥，
        # 同一个按钮会同时算「金框 1.0 + 彩框 1.2」，等于一物两记。
        # 而且两者同源（装备/经理经验满了才亮），本来就不该叠加
        # （用户 2026-10-27 截图：科目按钮彩框 ↔ 中间装备卡彩框）。
        gold = 0 if crit else (1 if self.has_gold_border(texts, subject=subject) else 0)
        return {
            "names": scene["names"],
            "mates": scene["mates"],
            "manager": scene["manager"],
            "equip": equip,
            "full": full,
            "gold": gold,
            "crit": crit,
            "color": 1 if crit else 0,
            "manager_crit": 1 if (scene["manager"] and (full > 0 or crit)) else 0,
        }

    def read_scene(self, texts):
        """场上角色：队友数（扣掉队长和经理）+ 经理在不在。"""
        names = self.scene_names(texts)
        manager = 1 if any(any(m in n for m in labels.MANAGER_TEXTS) for n in names) else 0
        mates = [n for n in names if not any(m in n for m in labels.MANAGER_TEXTS)]
        return {
            "names": names,
            "manager": manager,
            # 队长总在场，所以队友数 = 场上名字 - 队长（- 经理已经被扣掉）
            "mates": max(0, len(mates) - 1),
        }

    def read_attribute_values(self, texts):
        """读底部六维面板的当前数值：`{属性名: 当前值}`，读不到的属性不出现。

        版式（2026-10-26 实测，三行两列）：每格「名字 + 等级 + 当前值/上限」，
        OCR 会拆成「999」+「/1450」，也会把等级/评级粘在数值前（`C+354/±90`、`3488/1680`）。
        每格的值必须 **≤ 该格上限**（`attr_value_from`），超了就当误读。

        **读不准的那一格会放大重读一次**（`zoom_reread`，2026-11-04 用户口径：
        「故障多发的就用这个方法重判」）：裁这一格 → x3 → 重跑 OCR → 再解析。
        实测那一帧就是这么从 `8116` 救回 `1016` 的（并字/切框错了，放大后重新切框）。
        """
        panel = self.texts_in_region(texts, "attr_panel")
        values = self._attr_values_from(panel)
        missing = [n for n in labels.TRAINING_SUBJECTS if n not in values]
        if missing:
            values.update(self._attr_values_reread(panel, missing))
        return values

    def _attr_values_from(self, panel, names=None):
        """从一批**已经在面板区域内**的 OCR 结果里解析六维（不重读）。"""
        caps = getattr(self, "_attr_caps", None)
        if not isinstance(caps, dict):
            caps = {}
            self._attr_caps = caps
        values = {}
        for name in (names or labels.TRAINING_SUBJECTS):
            # 必须整词匹配：match_text 的「拼接兜底」会返回面板里最长的框
            # （例如「C+354/±90」），拿来当名字会把整行读错。
            head = self.exact_text(panel, (name,))
            if head is None:
                continue
            cy = head.y + head.height / 2
            # 只取**这一格**里的文字：右边界是「名字右沿 + 一格的宽度」。
            # 不加上界的话，左列那一格会把右列的数值吞进来（2026-11-04 实测：
            # 运控那行把右边的上限 `1689` 当成自己的当前值）。
            right_limit = head.x + head.width + self.width * 0.28
            row = [t for t in panel
                   if abs((t.y + t.height / 2) - cy) <= self.height * 0.035
                   and head.x + head.width * 0.5 < t.x <= right_limit]
            cap = caps.get(name)
            for t in sorted(row, key=lambda b: b.x):
                m = ATTR_PAIR_RE.search(t.name.replace(" ", ""))
                if m and ATTR_CAP_MIN <= int(m.group(2)) <= ATTR_CAP_MAX:
                    cap = caps[name] = int(m.group(2))     # 上限一局内不变，记下来
                value = attr_value_from(t.name, cap)
                if value is not None:
                    values[name] = value
                    break
            else:
                if row:
                    self.log_debug(f"六维「{name}」这一帧读不出可信值 →"
                                   f"（{[t.name for t in row][:4]}，上限 {cap}）")
        return values

    def _attr_values_reread(self, panel, missing):
        """读不准的那几格：裁出来放大 x3 重读（`ui/zoom_reread.py`）。

        只裁**这一格**（名字右侧那一小条），不是整个面板：一次重读约 0.1 秒，
        整块 0.59w×0.22h 放大后要慢 3~4 倍。
        """
        frame = self.frame
        if frame is None or not missing:
            return {}
        h, w = frame.shape[:2]
        caps = getattr(self, "_attr_caps", {}) or {}
        out = {}
        for name in missing:
            head = self.exact_text(panel, (name,))
            if head is None:
                continue                       # 连名字都没认出来，没法定这一格的位置
            cy = head.y + head.height / 2
            x1 = head.x + head.width * 0.6
            x2 = min(w, head.x + head.width + w * 0.34)
            got = zoom_reread.reread_px(
                frame, self, (x1, cy - h * 0.045, x2, cy + h * 0.045))
            cap = caps.get(name)
            for text, _score in got:
                m = ATTR_PAIR_RE.search(text.replace(" ", ""))
                if m and ATTR_CAP_MIN <= int(m.group(2)) <= ATTR_CAP_MAX:
                    cap = caps[name] = int(m.group(2))
                value = attr_value_from(text, cap)
                if value is not None:
                    out[name] = value
                    self.log_info(f"六维「{name}」放大重读：{value}（原读不准，读到「{text}」）")
                    break
        return out

    def refresh_attr_info(self, texts):
        """读底部六维面板并**合并**进 `info["attrs"]`（监控页「六维 / 分数预测」用）。

        **只在「进休息室」时调一次**（`handlers/training.py:handle_training_day` →
        `snapshot_lounge`），不挂在科目页、也不是每拍读（2026-11-03 用户口径）。
        原因：
        - 六维面板只在休息室（训练日主界面）这几页是**当前角色的完整面板**；
          校门口 / 地图根本没有；
        - 周末加的是零碎属性（属性+10、精力+…），回头再累加没有意义，也不该拿来算。

        成本：只是对**本拍已有的 OCR 文本**做过滤（`read_attribute_values`），
        不额外截图、不额外推理。

        **合并而不是整块替换**：面板被说明气泡挡住（鼠标停在某一格上会弹
        「提高看破对手意图…」，实测会盖掉体能那一行）、或这一帧 OCR 漏一项时，
        那一项保留上一次读到（上一次进休息室）的值，不会掉成「—」。
        """
        info = getattr(self, "info", None)
        merged = dict(info.get("attrs") or {}) if isinstance(info, dict) else {}
        values = self.read_attribute_values(texts)
        if not isinstance(info, dict) or not values:
            return merged
        changed = {k: v for k, v in values.items() if merged.get(k) != v}
        if changed:
            merged.update(changed)
            info["attrs"] = merged
            self.log_debug(f"六维当前值：{merged}")
        return merged

    def snapshot_lounge(self, texts):
        """进休息室（训练日主界面）时的一次性快照：**六维 + 精力 + 零用钱**。

        用户 2026-11-03 口径：
        - 只在**进入休息室**时更新这三项，其它页面不刷；
        - 校门口 / 地图没有六维面板；
        - 周末加的是零碎属性（属性+10、精力+…），回头累加既对不上面板也没意义。

        实现：六维走 `refresh_attr_info`（合并）；精力和零用钱由 `top_bar` 解析，
        它本身就会把 `stamina / stamina_max / coin` 写进 `info`，这里只负责**触发**
        一次并留痕。返回六维字典（科目排行会用到）。
        """
        attrs = self.refresh_attr_info(texts)
        bar = self.top_bar(texts)
        self.log_debug(
            f"休息室快照：六维 {attrs}｜精力 {bar.get('stamina')}/{bar.get('stamina_max')}"
            f"｜零用钱 {bar.get('coin')}")
        return attrs

    def read_equipment(self, texts):
        """数预览界面底部的器材卡，并判断哪些已经满经验。

        两种形态：
        - 未满：卡片底部是黑色胶囊经验条，青色部分是进度；
        - 已满：经验条变成「MAX」字样（实测 2026-10-26），且卡片带彩虹边框。
        所以 装备数 = 经验条个数 + MAX 个数；满经验数 = MAX 个数 + 全满的经验条。
        金框**不在这里算**：框是科目按钮外圈的事（见 has_gold_border），
        按器材卡的金框再算一次会和「装备」重复计分（用户 2026-10-27）。
        """
        frame = self.frame
        equip = full = 0
        if frame is not None:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = Defs.region("equip_bars")
            roi = frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
            if roi.size:
                b = roi[:, :, 0].astype(int)
                g = roi[:, :, 1].astype(int)
                r = roi[:, :, 2].astype(int)
                dark = ((b < 90) & (g < 90) & (r < 90)).astype(np.uint8)
                n, lab, stats, _cent = cv2.connectedComponentsWithStats(dark, 8)
                min_w, max_w = int(w * 0.04), int(w * 0.10)
                for i in range(1, n):
                    x, y, ww, hh, _area = stats[i]
                    if not (min_w <= ww <= max_w and 8 <= hh <= 30):
                        continue
                    equip += 1
                    cb = b[y:y + hh, x:x + ww]
                    cg = g[y:y + hh, x:x + ww]
                    cr = r[y:y + hh, x:x + ww]
                    cyan = (cb > 150) & (cg > 110) & (cr < 160)
                    if float(cyan.mean()) > 0.75:
                        full += 1
        # MAX 字样（满经验卡没有经验条）
        bar_texts = self.texts_in_region(texts, "equip_bars")
        joined = "".join(t.name for t in bar_texts).upper().replace(" ", "")
        max_count = joined.count(labels.EQUIP_MAX_TEXT)
        if max_count:
            equip += max_count
            full += max_count
        return equip, full

    def has_gold_border(self, texts=None, subject=None, quiet=False):
        """科目按钮外圈有没有金色边框。

        用户 2026-10-27 口径：金框/彩框都指科目按钮外面那一圈，不是器材卡；
        而且**框是常驻的，不需要选中也会亮**，所以按科目名找它自己那一行。

        **必须用「相对亮度」判，不能只看绝对值**：海边度假那套界面里所有科目
        按钮本身就是橙黄色（等级强制 MAX），固定阈值会把 6 行全判成有金框。
        做法：把 6 个科目按钮的环带都量一遍，看目标行是否明显亮于其余行（中位数）。

        门限 0.20（2026-10-27 第六局实跑 + 用户当场复核）：普通科目页里被点开的
        那一行本来就比其它行更橙（选中高亮 + 科目图标本身），实测 0.075~0.114，
        旧门限 0.10 把整批高亮都当成了金框（21 次选科目报了 13 次，用户指出
        「刚才没有金框」）。真彩虹框同一套环形采样是 0.333，0.20 能把
        「选中高亮」和「真外圈」分开；倍数条件同步抬到 2.0。

        2026-11-02 修订（用户：「刚才最后一次的训练，最后一个意识有金框，没识别到」）：
        上面那条「2 倍基线」的相对判据**在度假页会误杀真金框** ——
        那一局「意识」占比 0.274 已过绝对门限，却因为基线被橙黄按钮抬到 0.229
        （需要 ≥0.458）被判成无框。改成**按颜色分开**：
        金色像素额外要求 G>=200（橙黄按钮体 G≈171，真金框 G≈221+）、
        色相下限 18。详见 GOLD_PIX_HUE_MIN / GOLD_PIX_G_MIN 的注释。
        """
        frame = self.frame
        if frame is None:
            return False
        texts = texts if texts is not None else self._ocr_region()
        subjects = [t for t in texts
                    if t.name.strip() in labels.TRAINING_SUBJECTS
                    and (t.x + t.width / 2) / max(self.width, 1) > 0.7]
        if len(subjects) < 2:
            return False
        h, w = frame.shape[:2]

        target = None
        if subject:
            target = next((t for t in subjects if t.name.strip() == subject), None)
            # 指定了科目但这一帧没找到它那一行 → 直接判「无框」。
            # 2026-10-27 17:04 事故：探测「防守」时 OCR 没认出防守那行，
            # 兜底退回「决定所在行」= 投射，把投射的金框 0.482 记到防守头上，
            # 结果两个科目同分 4.30，把真正的金框科目挤掉了。
            if target is None:
                return False
        if target is None:
            # 没有指定科目（离线工具）才退回「决定按钮所在行」
            decide = self.match_text(texts, labels.BUTTONS["decide"])
            sel_cy = ((decide.y + decide.height / 2) if decide is not None
                      else self.height / 2)
            target = min(subjects,
                         key=lambda t: abs((t.y + t.height / 2) - sel_cy))

        x1, x2 = int(w * 0.752), int(w * 0.968)
        t_ring = max(6, int(h * 0.012))

        def ring_gold_ratio(cy):
            y1 = max(0, int(cy - h * 0.055))
            y2 = min(h, int(cy + h * 0.055))
            roi = frame[y1:y2, x1:x2]
            if roi.size == 0:
                return 0.0
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            hue, sat, val = cv2.split(hsv)
            blue, green, _red = cv2.split(roi)
            # 颜色判据见 GOLD_PIX_HUE_MIN / GOLD_PIX_G_MIN 的注释：
            # 橙黄按钮体 G≈171，真金框 G≈221+，用 G 通道把两者分开。
            gold = ((sat > 110) & (val > 150)
                    & (hue >= GOLD_PIX_HUE_MIN) & (hue <= 42)
                    & (green >= GOLD_PIX_G_MIN))
            ring = np.zeros(roi.shape[:2], dtype=bool)
            ring[:t_ring, :] = True
            ring[-t_ring:, :] = True
            ring[:, :t_ring] = True
            ring[:, -t_ring:] = True
            return float((gold & ring).sum()) / max(int(ring.sum()), 1)

        rows = [target] + [s for s in subjects if s is not target]
        ratios = [ring_gold_ratio(s.y + s.height / 2) for s in rows]
        ratio = ratios[0]
        rest = sorted(ratios[1:])
        baseline = rest[len(rest) // 2] if rest else 0.0
        # 过绝对门限的行数（用来识别「整页都这个色」的情况）。
        # 2026-11-12：**彩框行不算金框证据** —— 彩虹边缘本身含一段橙黄，
        # 一屏「1 个彩框 + 2 个金框」正好凑够 6//2 = 3 行，会被 page_wide
        # 判成「整页偏色」，结果所有金框全灭（用户报：第 4 周星期三、星期四
        # 连续两天同时有彩框金框，金框一个都没识别到）。
        crit_idx = {
            i for i, s in enumerate(rows)
            if self.has_crit_border(texts, subject=s.name.strip(), quiet=True)
        }
        over = [v for i, v in enumerate(ratios)
                if v > GOLD_RING_MIN and i not in crit_idx]
        # 2026-11-02 改判据：主判据改成**绝对门限 + 颜色**（见 GOLD_PIX_* 注释）。
        # 「2 倍基线」这条相对判据降级成**兜底**：只有当整页大多数科目行都同时
        # 过了绝对门限时，才认为这是「界面本身就这样」（例如度假页橙黄按钮），
        # 而不是真金框。
        # 为什么不能继续拿基线当主判据：① 度假页把基线抬到 0.229 → 误杀真金框
        # （本次事故）；② 金框是**逐科目独立**的，物理上可能两行同时有金框，
        # 那时中位数也跟着升高，又会误杀。两条都指向「别拿基线比大小」。
        page_wide = len(over) >= max(GOLD_PAGE_WIDE_MIN, len(subjects) // 2)
        # 2026-10-29：金框在度假页也漏识别，占比与基线一直打出来（校准用）
        # 2026-11-01：`quiet=True` 原来是个**失效开关** —— 训练日选科目的候选扫描
        # （training.py:has_gold_border(..., quiet=True)）传了 True，函数体却从没读过它，
        # 于是 6 个科目×每拍的校准日志照刷。现在真的静音。
        if not quiet:
            self.log_debug(f"科目金框：{target.name.strip()} 占比 {ratio:.3f}，"
                           f"其它行中位数 {baseline:.3f}，过门限 {len(over)}/{len(subjects)}")
        if ratio <= GOLD_RING_MIN:
            return False
        if page_wide:
            # 整页都过门限：不是「某一科目的金框」，是界面整体偏色
            return False
        return True

    def has_crit_border(self, texts=None, subject=None, quiet=False):
        """科目按钮外圈有没有彩虹边框（= 有满经验器材，训练会暴击）。

        框常驻、不需要选中（用户 2026-10-27），所以按科目名定位那一行；
        没有指定科目时退回「决定按钮所在行」。
        只采样按钮**边缘一圈**：按钮内部的科目图标（西瓜/鞋）有绿色/红色，
        采样整块会把普通科目误判成彩虹（实训 2026-10-26）。

        2026-10-31 重标定（用户：「刚才那轮只有一个彩框，怎么出现了两个」）：
        实拍帧 `logs/debug/1031-195131_subject04.jpg` 里只有「投射」带彩虹边，
        日志却同时给「弹跳」记了彩虹 1（占比 0.214，只比旧门限 0.20 高 0.014）。
        在那三张存盘帧上重算（`logs/_crit_calib.py`）：
            半窗 ±0.060h（旧）：彩框 0.51~0.56，噪声行 0.15~0.23  ← 与门限重叠
            半窗 ±0.050h（新）：彩框 0.27~0.39，噪声行 0.00~0.11  ← 分得开
            半窗 ±0.045h：真彩框掉到 0.09~0.16（把真边框切掉了，会漏）
        所以半窗收到 0.050h，并补一条**相对判据**（同金框）：目标行还要明显高于
        其它行里最低的那个 —— 只有整页都带杂色时才挡得住，正常页面上由门限决定。

        2026-10-31 20:31 又调了一次下限（用户：「刚才星期四有个彩边，没认到吗」）：
        第 5 周星期四「投射」满经验 1、实机有彩边，占比却只有 **0.157**，被 0.22 挡掉。
        同一时刻其它行全是 0.000 —— 说明噪声在 0.050h 半窗下其实塌到 0 附近，
        0.22 是拿早期「强彩框」样本（0.27~0.39）定的，偏高。现在下限 **0.13**：
        真彩框下界 0.157，噪声上界 0.109（唯一一例，其余 0.000~0.003）。
        **注意彩边会闪**（同一科目不同帧 0.157 / 0.318 / 0.343 都出现过），
        单帧占比只是下界，别再据此往上抬门限。
        """
        frame = self.frame
        if frame is None:
            return False
        texts = texts if texts is not None else self._ocr_region()

        subjects = [t for t in texts
                    if t.name.strip() in labels.TRAINING_SUBJECTS
                    and (t.x + t.width / 2) / max(self.width, 1) > 0.7]
        target = None
        if subject:
            target = next((t for t in subjects if t.name.strip() == subject), None)
            if target is None:
                # 指名了科目却找不到，不能拿别的行顶替（2026-10-27 事故：把投射的
                # 金框记到防守头上，两个科目同分，真金框科目被挤掉）
                return False
            cy = target.y + target.height / 2
        else:
            decide = self.match_text(texts, labels.BUTTONS["decide"])
            if decide is None:
                return False
            sel_cy = decide.y + decide.height / 2
            if subjects:
                target = min(subjects,
                             key=lambda t: abs((t.y + t.height / 2) - sel_cy))
                cy = target.y + target.height / 2
            else:
                cy = sel_cy

        ratio = self._crit_ring_ratio(frame, cy)
        others = sorted(self._crit_ring_ratio(frame, t.y + t.height / 2)
                        for t in subjects if t is not target)
        b_min = others[0] if others else 0.0
        b_med = others[len(others) // 2] if others else 0.0
        # 2026-10-29：彩框在度假页也漏识别，占比一直打出来（校准用）
        # 2026-11-01：同 has_gold_border —— quiet 以前没人读，候选扫描每拍照刷一行。
        if not quiet:
            self.log_debug(f"科目彩框：{target.name.strip() if target else '当前行'}"
                           f" 占比 {ratio:.3f}，其它行最低 {b_min:.3f}"
                           f"／中位 {b_med:.3f}（门限 {CRIT_RING_MIN}）")
        return ring_is_border(ratio, b_min,
                              floor=CRIT_RING_MIN, factor=CRIT_RING_FACTOR)

    def _crit_ring_ratio(self, frame, cy):
        """量以 cy 这一行为中心的彩虹环带占比。同一帧内缓存：
        一次选科目要量 6 行（目标 + 其它行做基线），别重复算 HSV。"""
        cache = getattr(self, "_crit_cache", None)
        if cache is None or cache.get("frame") is not frame:
            cache = {"frame": frame, "ratios": {}}
            self._crit_cache = cache
        key = int(cy)
        if key not in cache["ratios"]:
            cache["ratios"][key] = self._crit_ring_ratio_raw(frame, cy)
        return cache["ratios"][key]

    @staticmethod
    def _crit_ring_ratio_raw(frame, cy):
        h, w = frame.shape[:2]
        x1, x2 = int(w * 0.74), int(w * 0.98)
        y1 = max(0, int(cy - h * CRIT_RING_HALF_H))
        y2 = min(h, int(cy + h * CRIT_RING_HALF_H))
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return 0.0
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        magenta = (sat > 90) & (val > 120) & (hue >= 125) & (hue <= 175)
        green = (sat > 90) & (val > 120) & (hue >= 35) & (hue <= 90)
        ring = np.zeros(roi.shape[:2], dtype=bool)
        t = max(4, int(h * 0.008))
        # 2026-10-30 修：原先取整圈 + x 只到 0.93w。
        # 实测（logs/debug/1030-154728_subject07.jpg，第 1 行「投射」明显有彩框）
        # 整圈 0.66~0.93w = 0.197，差 0.003 没过门限 → 判「无彩框」，
        # 于是把第一位带彩框的科目录成了 0 分，选了别的。
        # 原因：左右两条环带一条落在按钮外背景、一条落在按钮内部渐变，纯噪声。
        # 改成只采**上下两条边**、x 覆盖到按钮真实右边缘（0.74~0.98w）。
        ring[:t, :] = True
        ring[-t:, :] = True
        hit = (magenta | green) & ring
        return float(hit.sum()) / max(int(ring.sum()), 1)

    def scene_names(self, texts):
        """过滤出训练场景里飘在角色头顶的名字。"""
        x1, y1, x2, y2 = Defs.region("scene_names")
        skip = {"失败率", "决定", "训练", "休息", "比赛", "商店", "出行", "跳过"}
        found = []
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                continue
            s = t.name.strip()
            if s in skip:
                continue
            if 2 <= len(s) <= 4 and all("\u4e00" <= ch <= "\u9fff" for ch in s):
                found.append(s)
        return found

    def read_captain_name(self):
        """底部中间那行队长名字（区域见 Defs.CAPTAIN_NAME_AREA）。"""
        x1, y1, x2, y2 = Defs.CAPTAIN_NAME_AREA
        best = None
        for t in self._ocr_region():
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                continue
            if len(t.name.strip()) < 2:
                continue
            if best is None or t.width > best.width:
                best = t
        return best.name.strip() if best is not None else None

    def _card_texts(self, texts, idx):
        x1 = 0.05 + idx * 0.31
        x2 = x1 + 0.28
        return [t for t in texts if x1 <= (t.x + t.width / 2) / self.width <= x2]

    def skill_card_slots(self, texts):
        """三张技能卡里**有名字行**的槽位集合（0/1/2）。

        为什么需要它（2026-10-31 用户核对「监控页 7240 vs 注册球员页 7376」）：
        点完卡之后游戏会把选中的那张飞出、剩下两张左移，但顶上「请选择1个技能」
        还在，handler 会再认领一拍：空槽位读成「道具0(0.000)」、变暗的两张卡
        `_card_quality` 一律兜底成白，于是白记一个「技能+1（白）」。
        一局 5 次三选一就虚增 5 个白（+125 分），而金从来不会虚增
        （退场帧一律判白）—— 和用户观察到的「金对、白错」完全一致。

        判据只看**名字行**（cy≈0.43，cx 离哪个卡心最近就算哪个槽位），
        不看底部信息栏（得分手/控卫/能力详情在 cy≈0.88~0.93，天然被 cy 过滤掉）。
        """
        slots = set()
        for t in texts:
            cy = (t.y + t.height / 2) / self.height
            if abs(cy - SKILL_NAME_CY) > SKILL_NAME_CY_TOL:
                continue
            cx = (t.x + t.width / 2) / self.width
            if not 0.1 <= cx <= 0.9:
                continue
            nearest = min(range(len(Defs.SKILL_CARDS)),
                          key=lambda i: abs(cx - Defs.SKILL_CARDS[i][0]))
            slots.add(nearest)
        return slots

    def skill_card_names(self, texts):
        """三张技能卡**名字行**上的技能名 → `{槽位: 名字}`（认不出就不给）。

        判据和 `skill_card_slots` 同一套（cy≈0.43，cx 离哪个卡心最近），
        只是把「这一槽有没有名字」变成「这个名字是什么」。用途：日志里记下
        选的是哪张，事后对账不用再靠点击坐标反推（2026-11-10 核分数差异时
        就是这么反推的，一局 45 张卡）。

        同一张卡在名字行常被读成多个框（`logs/events/1110-112907`：
        `['速度强势', '★★', '速', '速度强势']`，`速` 是图标碎片、`★★` 是等级），
        所以只收「≥2 字、不含 ★/数字/百分号」的名字，同一槽取最长的那个。
        """
        out = {}
        for t in texts:
            cy = (t.y + t.height / 2) / self.height
            if abs(cy - SKILL_NAME_CY) > SKILL_NAME_CY_TOL:
                continue
            cx = (t.x + t.width / 2) / self.width
            if not 0.1 <= cx <= 0.9:
                continue
            name = t.name.strip()
            if len(name) < 2 or re.search(r"[★☆0-9%＋+/*]", name):
                continue
            idx = min(range(len(Defs.SKILL_CARDS)),
                      key=lambda i: abs(cx - Defs.SKILL_CARDS[i][0]))
            if len(name) > len(out.get(idx, "")):
                out[idx] = name
        return out

    def _card_quality(self, frame, idx):
        """判断技能卡品质，返回 3=彩色 / 2=金色 / 1=白色。

        评分公式里技能价值差一倍（75 / 50 / 25），所以必须先分清金色和白色。
        判据用卡片上部色带的**暖色像素的平均 G 通道**：
        橙色 (255,145,0) 的 G≈145 → 彩色；金色 (255,215,0) 的 G≈215 → 金色；
        白/蓝卡没有暖色带 → 白色。
        实测日志里会打印 G 均值，方便以后校准。
        """
        if frame is None:
            return 1
        h, w = frame.shape[:2]
        x1 = int(w * (0.07 + idx * 0.32))
        x2 = int(w * (0.07 + idx * 0.32 + 0.24))
        y1, y2 = int(h * 0.20), int(h * 0.34)
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return 1
        b, g, r = roi[:, :, 0].astype(int), roi[:, :, 1].astype(int), roi[:, :, 2].astype(int)
        warm = (r > 185) & (b < 120) & (g > 90)
        ratio = float(warm.mean())
        if ratio < 0.12:
            return 1
        mean_g = float(g[warm].mean())
        self.log_debug(f"技能卡{idx + 1} 暖色 {ratio:.2f} G均值 {mean_g:.0f}")
        return 3 if mean_g < 185 else 2

    def choice_candidates(self, texts):
        """屏幕中部的竖向选项候选（事件 / 对话三选一），按 y 从下到上排序。

        只做形态判断：同一列、竖排、短文本；**不要求白底**。
        用途：跳过按钮是灰的时，先认出选项，别让 dialog_continue 去点灰按钮。
        训练动画页的「训练加成/表情加成」是横向三个，分组后每组只有 1 个，
        不会误判成竖向选项。
        """
        ui = {"下一场", "下一步", "跳过", "确定", "取消", "关闭",
              "继续", "领取", "开始训练"}
        cands = []
        for t in texts:
            s = t.name.strip()
            if s in ui or s in labels.WEEKEND_PLACES or self.is_decor(s):
                continue
            if re.search(r"[0-9%]", s):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (0.22 <= cx <= 0.78 and 0.20 <= cy <= 0.72):
                continue
            if not (2 <= len(s) <= 20):
                continue
            # 选项名是短名词：带句读的台词、学校名都不是选项。
            # 2026-10-26 周末首页把「眼镜的度数又上涨了，教练 / 您可以陪我去配一副
            # 新的吗？/ 北空之丘女子高校」当成三选一，每拍点第一个导致卡死。
            # 选项名是短名词：带句读的台词、学校名都不是选项。
            if re.search(r"[，。？！、；：]", s):
                continue
            if re.search(r"(高校|公学|中学|大学|学校)$", s):
                continue
            # 选项是普通字号；「备战 / 东部大赛」这种大标题/大按钮不算。
            if t.height > self.height * 0.08:
                continue
            cands.append(t)

        groups = []
        for t in sorted(cands, key=lambda x: x.y):
            cx = (t.x + t.width / 2) / self.width
            placed = False
            for g in groups:
                gx = (g[-1].x + g[-1].width / 2) / self.width
                if abs(gx - cx) < 0.12 and 0 < (t.y - g[-1].y) / self.height < 0.25:
                    g.append(t)
                    placed = True
                    break
            if not placed:
                groups.append([t])
        if not groups:
            return []
        group = max(groups, key=len)
        return group if len(group) >= 2 else []


    def options_not_paragraph(self, texts):
        """2026-10-29 新事件：选项是**各自居中的整句**，不是左对齐的段落。

        真帧 docs/shots/2026-10-29-event-sim-software.png（1549x871）：
            只是模拟软件而已，让她们试试 (371,299,714,97)
            不许她们乱来                 (647,440,253,44)
        两条左边缘差 276px、宽度差 461px；而对话/关卡列表/本周总结都是
        **左对齐等宽**的段落（左边缘几乎相同）。所以这里只认「左边缘不齐」的
        2 行以上，避免把对话当选项（2026-10-26 卡死事故）。

        2026-11-02 实跑（第3周 星期六 校门口）：队长的台词是**居中气泡**
        （「和名雪同学约好了在游戏厅见 / 面，快迟到了一」），再加一条
        「今天餐厅缺人，快去看看吧！」和学校名 —— 日志原文
        `事件选项（整句）：面，快迟到了一、北空之丘女子高校、今天餐厅缺人，
        快去看看吧！ → 选第一个「面，快迟到了一」`，
        紧接着 `left_click 面，快迟到了一 (1014, 245)`：在对话框里空点一下。
        这几个框能通过「左边缘不齐 / 宽度不齐 / 同列」的全部判据
        （气泡续行的左边缘和上一行根本不在一起），内容上也没法排除。
        所以先判页面：**有「出行」按钮的校门口/训练日首页不是事件页**。
        """
        # 事件页是全屏浮层，从不出现「出行」入口（那是主界面的按钮）。
        if self.match_text(texts, "出行") is not None:
            return []
        ui = {"下一步", "下一场", "跳过", "确定", "取消", "关闭",
              "继续", "领取", "开始训练"}
        rows = []
        for t in texts:
            s = t.name.strip()
            if s in ui or s in labels.WEEKEND_PLACES or self.is_decor(s):
                continue
            if re.search(r"[0-9%]", s) or not (2 <= len(s) <= 30):
                continue
            # 学校名「北空之丘女子高校」是主界面的标题，永远不是选项
            # （choice_candidates 早就有这条，这里补上）。
            if re.search(r"(高校|公学|中学|大学|学校)$", s):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (0.30 <= cx <= 0.70 and 0.20 <= cy <= 0.72):
                continue
            if t.height > self.height * 0.13:
                continue
            rows.append(t)
        if len(rows) < 2:
            return []
        rows.sort(key=lambda t: t.y)
        lefts = [t.x for t in rows]
        widths = [t.width for t in rows]
        if max(lefts) - min(lefts) <= self.width * 0.05:
            return []                      # 左对齐 = 段落，不是选项
        if max(widths) - min(widths) <= self.width * 0.10:
            return []                      # 等宽换行 = 段落
        centers = [(t.x + t.width / 2) / self.width for t in rows]
        if max(centers) - min(centers) > 0.10:
            return []                      # 不在同一列
        return rows

    def _yellow_buttons(self):
        """找屏幕下半部分的黄色按钮框（事件选项 / 确认按钮）。

        事件按钮是「白底 + 黄色圆角边框」，所以用黄色 mask 找外框，
        再做闭运算把文字/图标造成的断口补上。返回 (x, y, w, h) 列表。
        """
        frame = self.frame
        if frame is None:
            return []
        h, w = frame.shape[:2]
        y0 = int(h * 0.60)
        roi = frame[y0:, :]
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        mask = ((r > 200) & (g > 150) & (b < 120)).astype(np.uint8)
        kx = max(3, w // 240)
        ky = max(3, h // 240)
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky)))
        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
        boxes = []
        for i in range(1, n):
            x, y, ww, hh, _area = stats[i]
            if ww < w * 0.12 or hh < h * 0.045:
                continue
            if ww > w * 0.95 or hh > h * 0.35:
                continue
            boxes.append((int(x), int(y + y0), int(ww), int(hh)))
        return boxes

    def _has_white_band(self, t):
        """文字所在的那条横带是不是白底（事件选项按钮）。

        竖向 3 选 1 的真按钮是「白底彩边」；训练动画的「训练加成/表情加成」
        是深色背景上的文字，白底占比很低。用这一条把两者分开。
        """
        frame = self.frame
        if frame is None:
            return False
        h, w = frame.shape[:2]
        cy = int(t.y + t.height / 2)
        y1, y2 = max(0, cy - 10), min(h, cy + 10)
        x1, x2 = int(w * 0.22), int(w * 0.78)
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return False
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        bright = (b > 195) & (g > 190) & (r > 180)
        return float(bright.mean()) > 0.50

    def theme_tag_red_ratio(self, box):
        """题材卡片「标签」文字里**红色**像素占高饱和像素的比例（0~1）。

        用户口径（2026-11-02，第二次修正）：「优先红色，都一样就选中间」——
        判据只问「这张卡的标签是不是红字」，**不**问「哪张跟别人不一样」。
        三种实测样本（1549x871）：
          - 白字 + 蓝底（普通卡）：饱和像素几乎全是蓝底（色相 100），红占比 0.00；
          - **粉/红字**（天赋卡 `#权谋`，或三张全红的 `#悬疑/#治愈/#爽文`）：
            红像素落在色相 0 与 170 两侧，占比 0.27 上下；
          - 白字 + 白底（`#权谋` 那次的底色）：饱和像素只剩字，占比接近 1.0。
        分母用「高饱和 + 亮」的像素而不是整框：OCR 框大小随字数变，绝对数不可比。
        """
        frame = self.frame
        if frame is None:
            return 0.0
        h, w = frame.shape[:2]
        x1, y1 = max(0, int(box.x)), max(0, int(box.y))
        x2 = min(w, int(box.x + box.width))
        y2 = min(h, int(box.y + box.height))
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return 0.0
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        colorful = (sat > 80) & (val > 140)
        total = int(colorful.sum())
        # 框里几乎没有彩色像素（页面刚淡入 / 白底白字）→ 判「不是红字」，
        # 不用 0/0 造一个假 0.0 去跟别人比。
        if total < 30:
            return 0.0
        # 红色：色相在 0 附近，或折回 180 那一侧（洋红/粉）。橙黄（色相 20~35）不算红。
        red = colorful & ((hue <= labels.THEME_RED_HUE_MAX) | (hue >= labels.THEME_RED_HUE_MIN_HIGH))
        return float(red.sum()) / total
