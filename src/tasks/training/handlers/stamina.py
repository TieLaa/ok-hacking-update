"""专注恢复：大厅顶栏「+」→「专注恢复」页 → 喝饮料；**永远不花钻石**。

用户 2026-11-04 口径（三句话就是全部规则）：

1. 「在大厅点击我标注的红框位置，进入恢复专注页面」—— 红框 = 顶栏专注药丸 + 它右边的「+」；
2. 「专注不够的时候自己进去点击恢复」—— 主动补，不用等人点；
3. 「禁止使用钻石恢复专注」—— 恢复页第一行「钻石购买」按钮是钻石图标 + 价格数字，
   **不是「使用」**，所以「只点整词『使用』」这一条就是执行点（见 `labels.BUTTONS["use"]`）。

页面识别、按钮位置全在 `parsers.read_stamina_recover_options`（纯识别），
这里只做**决策**：先看差多少 → 挑最小的够用瓶 → 点「使用」。

配套的 `flow` 侧：`note_run_score` / `finish_run` / 多角色队列在 `flow.py`。
"""

from src.tasks.ui import Defs, labels

import re
import time


def pick_stamina_bottle(rows, deficit):
    """在恢复页的三张卡里挑一张 → 返回那一行（dict），没有可用的返回 None。

    规则（用户口径只说了「第二个 +30、第三个 +60、第一个钻石不许点」，
    这里补的是「挑哪一瓶」）：

    1. **钻石行永远不进候选**（`forbidden`），拥有 0 的也跳过；
    2. 差多少补多少：优先「够用且最小」的那瓶（差 25 就用 +30，不浪费 +60）；
    3. 都不够用 → 用最大的那瓶（补到多少算多少，下一拍还会进来继续补）；
    4. 不差（deficit <= 0）→ None（已经满了就别浪费饮料）。
    """
    if deficit is None or deficit <= 0:
        return None
    usable = [r for r in rows
              if not r.get("forbidden")
              and r.get("use") is not None
              and r.get("gain")
              and (r.get("owned") is None or r["owned"] > 0)]
    if not usable:
        return None
    enough = [r for r in usable if r["gain"] >= deficit]
    if enough:
        return min(enough, key=lambda r: r["gain"])
    return max(usable, key=lambda r: r["gain"])


class StaminaHandlers:

    # 恢复页最多连续占多少拍（列表页→详情层→确认→关页，正常 4~5 拍就够）。
    # 超过就认「这页走不掉」，记 giveup 后主动关页，别等到 STALL_LIMITS 的 60 拍。
    STAMINA_PAGE_MAX_TICKS = 8

    # 「训练所需专注还差25，是否进行回复呢？」—— 大厅点训练时专注不够就会弹。
    # 实测文案（logs/debug/1027-192726_nochange3/ocr.txt 整条 1.00 分）：
    #     训练所需专注还差25，是否进行回复呢？
    SHORT_TEXTS = ("专注还差", "是否进行回复")

    def handle_stamina_short(self, texts):
        """「训练所需专注还差 N」弹窗：点「回复」进恢复页（**不点取消**）。

        必须排在 `handle_popup` 之前：那个 handler 在没有「确定」时会点「取消」，
        于是「差 25 → 取消 → 再点训练 → 又差 25」无限循环（和 2026-10-26 休息确认
        那次是同一类事故）。

        **「自动恢复精力」关掉时反着来**（用户 2026-11-05 监控页新增
        「允许吃专注药」开关，关 = 一口都不喝）：**先点「取消」关掉这个弹窗**，
        然后停止任务（`_abort_reason`，与 `stop_for_no_captain` 同一条链路），
        弹窗提示用户。用户 2026-11-05 口径：专注不足跟「休息」没关系，
        不要置 `_force_rest` 改走休息 —— 停下来告诉用户就行。

        认不出「取消」也照样停（告警里说明）：总比留着弹窗在原地重试好。
        """
        if self.match_text(texts, self.SHORT_TEXTS) is None:
            return False
        if not self.config.get("自动恢复精力"):
            cancel = self.exact_text(texts, labels.BUTTONS["exit_hatch"])
            if cancel is not None:
                self.click_box(cancel, after_sleep=0.8)
                self.log_info("专注不足：禁止喝药（允许吃专注药=关）→ 点「取消」关闭弹窗")
            else:
                self.log_warning("专注不足弹窗：禁止喝药但认不出「取消」→ 不点了，直接停任务")
            self._abort_reason = ("专注不足且禁止喝药（允许吃专注药=关）→ 已停止自动训练；"
                                  "勾上开关或手动喝药后再继续")
            self.log_warning(self._abort_reason, notify=True)
            return True
        self._stamina_need = self.stamina_short_need(texts)
        for name in ("回复", "确定", "是"):
            target = self.exact_text(texts, (name,))
            if target is not None:
                self.click_box(target, after_sleep=1.0)
                self.log_info(f"专注不足：点「{name}」（还差 {self._stamina_need}）→ 恢复页")
                return True
        self.log_warning("专注不足弹窗：认不出「回复」按钮 → 交给下一拍重试")
        return False

    def stamina_short_need(self, texts):
        """从「还差N」里取 N（取不到返回 None，调用方按「补到下限」处理）。"""
        for t in texts:
            m = re.search(r"还差\s*(\d+)", t.name)
            if m:
                return int(m.group(1))
        return None

    def handle_stamina_recover(self, texts):
        """「专注恢复」整页：按需喝饮料；不需要/没得喝就关掉。

        返回 True = 这一页归我管（点过了或者关掉了），False = 不是这一页。

        两种子状态（2026-11-04 实跑区分出来，`logs/debug/1104-190912_loop60`）：
          ① 列表页：三张卡各带整词「使用」→ 点够用的那瓶；
          ② **道具详情层**（点了「使用」之后弹出）：必须再点「使用道具」才真的喝下去。
             旧版不认识这一层，于是「列表页 → 详情层 → 差 0 → 关页失败」来回 60 拍，
             饮料一瓶没喝掉，最后被 `STALL_LIMITS` 停任务。
        """
        if not (self.in_stamina_item_detail(texts) or self.in_stamina_recover_page(texts)):
            return False
        # 用户 2026-11-05 监控页「允许吃专注药」开关：关掉时这一页一律不喝，
        # 直接关掉走人（钻石行本来就是结构性禁止，这里连饮料也不碰）。
        if not self.config.get("自动恢复精力"):
            self._stamina_need = None
            self.log_info("专注恢复页：允许吃专注药=关 → 不喝，直接关闭这一页", notify=True)
            self._stamina_giveup_at = time.monotonic()
            self.close_stamina_recover(texts)
            return True
        self._stamina_ticks = getattr(self, "_stamina_ticks", 0) + 1
        if self._stamina_ticks > self.STAMINA_PAGE_MAX_TICKS:
            # 兜底：连点这么多拍还没离开恢复页（列表页 / 详情层都算）→ 认了「补不了」，
            # 主动关页退出，别等到 STALL_LIMITS 的 60 拍才停。
            self.log_warning(
                f"专注恢复页连续 {self._stamina_ticks} 拍没走掉 → 放弃并关闭这一页")
            self._stamina_giveup_at = time.monotonic()
            self.close_stamina_recover(texts)
            return True

        # ① **先看专注够不够**（用户 2026-11-04 口径：这个页面要先检测专注值，
        #    确认不够了再吃，够了就退出）。不够 → 才继续；够了 → 立刻关页。
        deficit = self.stamina_deficit(texts)
        if deficit is not None and deficit <= 0:
            self.log_info(f"专注恢复：专注已够（差 {deficit}）→ 直接关闭这一页，不再喝")
            self._stamina_wait_until = 0.0
            self._stamina_before = None
            self.close_stamina_recover(texts)
            return True

        # ② 刚喝过 → 等精力数字刷新，**这段时间一下都不许再点**
        #    （2026-11-04 事故：详情层确认后立刻连点，一口气喝掉两罐，专注却没涨）
        if time.monotonic() < getattr(self, "_stamina_wait_until", 0.0):
            return True

        # ③ 喝完等过一轮了：如果专注**一点没涨**，说明这瓶没生效（页面读数滞后/被挡），
        #    不许接着喝第二罐 —— 记 giveup 关页退出，下一轮再补。
        before = getattr(self, "_stamina_before", None)
        if (before is not None and before[0] is not None
                and deficit is not None):
            cur = self.stamina_read(texts)[0]
            if cur is not None and cur <= before[0]:
                self.log_warning(
                    f"专注恢复：喝了一瓶但专注没涨（{before[0]} → {cur}）→ 先关页，"
                    "不再连喝（下一轮再补）")
                self._stamina_giveup_at = time.monotonic()
                self._stamina_before = None
                self._stamina_wait_until = 0.0
                self.close_stamina_recover(texts)
                return True

        if self.in_stamina_item_detail(texts):
            return self._confirm_stamina_use(texts)
        if getattr(self, "_stamina_use_at", 0.0):
            if time.monotonic() - self._stamina_use_at < 1.2:
                return True                      # 刚点过「使用」，等数字刷新
        rows = self.read_stamina_recover_options(texts)
        pick = pick_stamina_bottle(rows, deficit)
        if pick is None:
            self.log_info(f"专注恢复：没有可喝的饮料（差 {deficit}）→ 关闭这一页")
            # 记一笔「补不了」：不然大厅的 ensure_stamina 会「点+ → 关页 → 再点+」死循环
            self._stamina_giveup_at = time.monotonic()
            self.close_stamina_recover(texts)
            return True
        self._stamina_use_at = time.monotonic()
        self._stamina_before = self.stamina_read(texts)
        self.click_box(pick["use"], after_sleep=0.9)
        self.log_info(f"专注恢复：喝「{pick['title']}」+{pick['gain']}"
                      f"（差 {deficit}，拥有 {pick['owned']}）"
                      + ("（跳过钻石购买行）" if any(r["forbidden"] for r in rows) else ""))
        return True

    def _confirm_stamina_use(self, texts):
        """道具详情层：**再确认一次专注值**，不够才点「使用道具」，够了直接关页。

        用户 2026-11-04：「这个页面要先检测专注值，确认不够了再吃，够了就退出」——
        详情层是真正扣道具的那一层，所以这一层必须自己再判一次，不能只信上一拍。
        """
        if time.monotonic() < getattr(self, "_stamina_wait_until", 0.0):
            return True                          # 刚喝过，等精力刷新
        deficit = self.stamina_deficit(texts)
        if deficit is not None and deficit <= 0:
            self.log_info(f"专注恢复：详情层复查专注已够（差 {deficit}）→ 关页，不喝这一瓶")
            self.close_stamina_recover(texts)
            return True
        button = self.match_text(texts, labels.STAMINA_ITEM_DETAIL)
        if button is None:
            return True                          # 判据刚成立但读不到框，下一拍再看
        self._stamina_use_at = time.monotonic()
        self._stamina_before = self.stamina_read(texts)
        # 确认喝下之后 **2.5 秒内一下都不许再点**（给精力数字刷新留时间）
        self._stamina_wait_until = time.monotonic() + 2.5
        self.click_box(button, after_sleep=0.9)
        self.log_info("专注恢复：道具详情 → 点「使用道具」确认喝下（之后等精力刷新）")
        return True

    def in_stamina_recover_page(self, texts):
        """「专注恢复」页判据：标题「专注恢复」或「当前专注」+「下次恢复」。"""
        if self.match_text(texts, labels.STAMINA_RECOVER_MARKS[0]) is not None:
            return True
        return (self.match_text(texts, labels.STAMINA_RECOVER_MARKS[1]) is not None
                and self.match_text(texts, "下次恢复") is not None)

    def stamina_deficit(self, texts):
        """这一趟要补多少专注 → int（读不到返回 None）。

        「补多少」不是「补满」——补满会把 +60 的大瓶浪费在只差 25 的时候：

        | 怎么进来的 | 目标 |
        |---|---|
        | 「训练所需专注还差 N」弹窗（`_stamina_need = N`） | 当前 + N |
        | 大厅主动补（`ensure_stamina`，专注 < `恢复精力下限`） | 补到 `恢复精力下限` |
        | 兜底（两种情况都没记） | 补满 |

        数值来源优先**顶栏**（`top_bar` 自带专注条像素兜底）；页面上那个大数字
        （「当前专注 107/150」）只当兜底，而且**上限必须在
        `labels.STAMINA_CAP_RANGE` 里才采信** —— 2026-11-04 事故：
        道具详情层的「当前专注 15/150」被 OCR 拆成 `15/15` + `0`，
        旧代码把 15 当上限 → 目标 15 → 差 0 → 该喝饮料的时候判「不用喝」，
        于是恢复页来回空转 60 拍。
        """
        cur, mx = self.stamina_read(texts)
        if cur is None or mx is None:
            return None
        need = getattr(self, "_stamina_need", None)
        if need:
            target = min(mx, cur + int(need))
        else:
            floor = int(self.config.get("恢复精力下限") or 0)
            target = min(mx, max(floor, 0))
        return max(0, target - cur)

    def stamina_read(self, texts):
        """当前专注读数 → `(cur, max)`，读不到 `(None, None)`。

        优先顶栏（`top_bar` 自带专注条像素兜底），再用页面里「当前专注 N/M」兜底，
        而且**上限必须落在 `labels.STAMINA_CAP_RANGE`（100~250）**才采信 ——
        2026-11-04 事故：详情层的「当前专注 15/150」被 OCR 拆成 `15/15` + `0`，
        把 15 当上限 → 目标 15 → 差 0 → 该喝的时候判「不用喝」。
        """
        bar = self.top_bar(texts)
        cur, mx = bar.get("stamina"), bar.get("stamina_max")
        if cur is not None and mx is not None:
            return cur, mx
        lo, hi = labels.STAMINA_CAP_RANGE
        for t in texts:
            m = labels.STAMINA_RE.search(t.name.replace(" ", ""))
            if not m:
                continue
            cy = (t.y + t.height / 2) / self.height
            if not 0.35 <= cy <= 0.70:
                continue
            if not lo <= int(m.group(2)) <= hi:
                self.log_debug(f"专注恢复：忽略「{t.name}」（上限不在 {lo}~{hi}）")
                continue
            return int(m.group(1)), int(m.group(2))
        return None, None

    def close_stamina_recover(self, texts=None):
        """关掉恢复页：**优先点 OCR 到的右上角「X」**，认不出来才退回固定坐标。

        实测（1549x871，`logs/debug/1104-190912_loop60`）：
        `Defs.STAMINA_RECOVER_CLOSE = (0.942, 0.157)` → (1459,136)，
        而 X 框中心是 (1448,105) —— y 偏 31px，点了 30 次都没关上，
        于是「列表页 ↔ 详情层」死循环。X 的 OCR 置信度实测 0.82~0.95，够稳。
        """
        self._stamina_need = None                 # 这一趟补完了，别带到下一次
        pool = texts if texts is not None else (getattr(self, "_tick_texts", None) or [])
        point = self.stamina_recover_close_point(pool)
        if point is None:
            x, y = Defs.STAMINA_RECOVER_CLOSE
            point = (int(self.width * x), int(self.height * y))
            self.log_warning(f"恢复页认不出右上角 X → 按固定坐标 {point} 关页")
        self.click(int(point[0]), int(point[1]), after_sleep=0.8)

    # ---------- 主动补（大厅 / 训练剧本页） ----------

    def ensure_stamina(self, need=None):
        """专注低于 `need` 就主动点「+」进恢复页。返回 True = 这一拍去补了。

        - `need` 省略时用配置 `恢复精力下限`（默认 30）；
        - 只在**大厅 / 训练剧本**这类「顶栏有 +」的页面生效：训练内的顶栏
          药丸没有「+」（`parsers.stamina_plus_point` 会返回 None → 这里直接放弃），
          免得在科目页瞎点；
        - 配置 `自动恢复精力` 关掉就什么都不做。
        """
        if not self.config.get("自动恢复精力"):
            return False
        # 刚刚试过但没饮料可喝 → 60 秒内不再折腾（否则「点+ → 关页」会无限循环）
        giveup = getattr(self, "_stamina_giveup_at", 0.0)
        if giveup and time.monotonic() - giveup < 60:
            return False
        bar = self.top_bar()
        cur, mx = bar.get("stamina"), bar.get("stamina_max")
        if cur is None or mx is None:
            return False
        if need is None:
            need = int(self.config.get("恢复精力下限") or 0)
        if cur >= need:
            return False
        self._stamina_need = None        # 主动补：目标 = 恢复精力下限（不是补满）
        if not self.open_stamina_recover():
            return False
        self.log_info(f"专注 {cur}/{mx} < {need} → 主动进专注恢复页")
        return True

    def open_stamina_recover(self):
        """点顶栏专注药丸右边的「+」。返回 True = 点到了一下。

        认不出「+」（训练内 HUD / OCR 丢字）时**不**乱点固定坐标，
        只在大厅这一类页面上退回 `Defs.STAMINA_PLUS_FALLBACK` 并告警 ——
        训练日的顶栏药丸在左半边，套这个坐标会点到「技能/赛程」那些地方。
        """
        texts = self._ocr_region()
        point = self.stamina_plus_point(texts)
        if point is None:
            if self.match_text(texts, labels.LOBBY_MARKS) is None:
                self.log_debug("顶栏没有专注「+」，且当前不在大厅 → 不主动恢复")
                return False
            self.log_warning("大厅顶栏认不出专注「+」→ 按固定坐标兜底"
                             f"{Defs.STAMINA_PLUS_FALLBACK}")
            point = (int(self.width * Defs.STAMINA_PLUS_FALLBACK[0]),
                     int(self.height * Defs.STAMINA_PLUS_FALLBACK[1]))
        self.click(int(point[0]), int(point[1]), after_sleep=1.0)
        self.log_info(f"点顶栏专注「+」@({int(point[0])},{int(point[1])}) → 进恢复页")
        return True
