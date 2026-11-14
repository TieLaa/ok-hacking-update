"""兜底：**没登记的「品质卡」页面**（2~3 张横向并排、标题条按品质上色）。

用户 2026-11-03 口径：
遇到不认识的事件页（新的角色天赋、新的小游戏……）不要只点屏幕中央赌一把 ——

1. 按卡片**品质色**选：**彩 > 金 > 紫 > 蓝**；
2. 同品质比**队友头像数量**：队友多的优先，有队友的优先于没队友的；
3. 同一页**截图存证**到 `logs/unmark/`（jpg + OCR 原文 + 判定结果 json）
   —— unmark = 未标注样本，人工看一眼就能把它补成正式 handler。

放在 `HANDLERS` 表**最末**：前面任何专属 handler 认领的页面都轮不到它。
判据本身见 `parsers.quality_cards`（至少两张卡能判出品质色才算选项页，
所以普通按钮行/信息栏不会被它抢走）。
"""

import time

from src.tasks.training import page_signatures

# handler 是**函数**（注册表里按 `handler(self, texts)` 调），不是绑定方法，
# 所以限流/锁只能放在模块级，不能用 `self.UNMARK_INTERVAL` 这种类属性。
UNMARK_INTERVAL = 10.0     # 同一页最多 10 秒存一张
CONFIRM_LOCK = 2.5         # 点过「确定」后的补点窗口


class UnmarkedHandlers:
    """未登记页面的品质卡兜底（mixin，方法通过 self 访问任务状态）。"""

    def handle_unmarked_cards(self, texts):
        cards = self.quality_cards(texts)
        if len(cards) < 2:
            return False
        # 邻页排除（用户 2026-11-03 实跑暴露）：扭蛋/商店/游戏厅/地图/餐厅/队伍/
        # 结算这些页面同样是「2~3 张上色卡」，但点下去会花钱或改阵容。
        # 专属 handler 正常时轮不到这里；**只有它没认出来的那一拍**才靠这层兜底。
        # 阶段 2：那 36 个词收口到 `page_signatures` 的 `exclusive_page` 签名，
        # 这里不再自己维护一份。
        if page_signatures.page_has(self, texts, "exclusive_page"):
            return False
        if page_signatures.page_has(self, texts, "weekend_map"):
            return False
        # 「下一步」不收进签名表（2026-11-14 实测）：锦标赛对阵图只有
        # 「下一步」而没有「关卡加成/奖励预览」，收进去会把 stage_list
        # 挤掉、让 handle_result 抢走（docs/shots/2026-10-26-bracket.png）。
        # 「下一步」是「这页有推进按钮」，不是某个页面的身份。
        if self.match_text(texts, "下一步") is not None:
            return False

        # 已经点过「确定」但页面还没切走：只补点确定，别重新选
        if (time.monotonic() - getattr(self, "_unmark_confirmed_at", 0.0)
                < CONFIRM_LOCK):
            self.click_button("confirm", after_sleep=0.8, must=False)
            return True

        pick, ordered = self.quality_card_order(cards)
        detail = "｜".join(f"{c['name']}({c['quality']}/队友{c['mates']})"
                          for c in ordered)
        now = time.monotonic()
        if now - getattr(self, "_unmark_dumped_at", 0.0) > UNMARK_INTERVAL:
            self.dump_unmark(texts, "cards", cards, picked=pick["name"],
                             note="未登记的品质卡页面：按 彩>金>紫>蓝/队友多 猜选")
            self._unmark_dumped_at = now
        self.log_warning(
            f"未登记的选项页：{detail} → 按品质选「{pick['name']}」"
            f"（{pick['quality']}，已存证到本局 unmark/）")

        self.click_box(pick["box"], after_sleep=0.5)
        # 有的页面（做饭那种食材三选一）选完还要点「确定」。认不到就不点，
        # 下一拍看门狗会接手（3 次没变化 → 存证 + 点中央 / 停止）。
        if self.click_button("confirm", after_sleep=0.8, must=False):
            self._unmark_confirmed_at = time.monotonic()
        return True
