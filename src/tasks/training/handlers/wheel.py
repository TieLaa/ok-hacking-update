"""转盘类事件：底部黄色「转一转」（钓鱼啦！/ 开始做饭！/ 经理的小帮助……共用）。

**为什么独立成通用模块**（2026-11-05）：转盘最初出现在钓鱼/做饭两个天赋事件
的第二页，曾并入 `handlers/talent.py`；但「球队经理的小帮助」（非天赋的通用
事件）也触发同样的转盘，「转盘属于天赋」的归口不成立。它本来就是零领域知识
的通用 UI 机制 —— 按钮位置/文案在所有转盘页通用，谁触发都一样处理。

用户 2026-11-03 口径：

1. 转盘转到哪一半是随机的，**内容不影响决策** —— 只需要点一下；
2. 「转一转」按钮的位置和文案在所有转盘事件里是**通用**的，所以合并成一个 handler。

实测（1549x871，两页 OCR 都是 0.9997 的「转一转」，框也几乎一样）：

    钓鱼啦！   标题 @(612,10)  按钮 @(715,781,111,47)  → 中心 (0.500w, 0.923h)
    开始做饭！ 标题 @(576,12)  按钮 @(716,781,110,47)  → 中心 (0.500w, 0.923h)

判据（按顺序）：

1. 底部那一条（cy ≥ 0.85、cx 0.30~0.70）里有「转一转 / 转一圈」→ 点它的文本框；
2. 认不出按钮、但标题是已知转盘页（`labels.WHEEL_TITLES`）→ 点实测固定坐标
   (0.500w, 0.923h)；
3. 都没有 → 不是这一页。

**为什么第 1 条要卡位置**：大厅「玩法目录」里也有一个「转一转」入口
（`entry.handle_lobby` 用它），只认文字会在大厅点它。

节奏（沿用 2026-10-30 钓鱼那次实测）：**只点一次**，转盘动画要几秒；
点完锁 8 秒，动画期间只等不点；8 秒后还停在这一页（没点中 / 动画卡住）
才允许补点。旧版每秒补点一次，一次钓鱼被点了 4 下。

OCR 备注：实机帧和回归缓存里的按钮都是「转一转」（0.9997，
码位 0x8f6c 0x4e00 0x8f6c），**不是掉字**。仍然保留「包含匹配 + 固定坐标」
两级降级，是因为本项目有 `下一步`→`下一` 这种淡入掉字的先例。
"""

import time

from src.tasks.ui import labels


class WheelHandlers:
    """转盘页（mixin，方法通过 self 访问任务状态）。"""

    # 点完后的静默期（秒）：动画期间界面文字不变，不能靠「没反应」判失败。
    WHEEL_LOCK_SECONDS = 8.0

    def wheel_button(self, texts):
        """底部居中那一条里的「转一转 / 转一圈」文本框；大厅入口不算。

        大厅「玩法目录」的「转一转」在卡片区（cy≈0.5），不在底部按钮带里。
        """
        min_cy, x_lo, x_hi = labels.WHEEL_BUTTON_BAND
        for t in texts:
            name = t.name.strip()
            if not any(w in name for w in labels.WHEEL_BUTTONS):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if cy >= min_cy and x_lo <= cx <= x_hi:
                return t
        return None

    def handle_spin_wheel(self, texts):
        """「转一转」：点一次 → 锁 8 秒（动画）→ 8 秒后还在这一页才补点。"""
        btn = self.wheel_button(texts)
        if btn is None and self.match_text(texts, labels.WHEEL_TITLES) is None:
            return False
        if time.monotonic() - getattr(self, "_wheel_clicked_at", 0.0) < self.WHEEL_LOCK_SECONDS:
            self.log_debug("转盘：动画播放中，等它转完（不重复点）")
            self.sleep(0.5)
            return True
        self._wheel_clicked_at = time.monotonic()
        if btn is not None:
            self.click_box(btn, after_sleep=0.6)
        else:
            x, y = labels.WHEEL_BUTTON_POINT
            self.click(int(self.width * x), int(self.height * y), after_sleep=0.6)
            self.log_warning("转盘：没认出「转一转」，按实测固定坐标点底部按钮")
        self.log_info("转盘：点「转一转」（只点一次，后面是动画）")
        return True
