"""游戏厅（小游戏）：**地点模块**。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：
每个地点有自己的模块，只管自己的判断。

界面（实拍 2026-10-27 18:34，客户区 1307x735）::

    请选择一个小游戏
    [自动 弹跳 普通] 奖励：弹跳20~70    打折 64
    [自动 运控 普通] 奖励：运控20~70    打折 64
    [自动 投射 困难] 奖励：投射30~100   打折 80
    [能力详情]   [确定]

**「自动」两个字 = 已经打过的小游戏**（用户 2026-10-27 口径）：
带它才能自动挑战/跳过；没有它的要真人打（打篮球、拼图），
OCR 跟不上，进去就是白费一个周末日。所以只买带「自动」的卡片。

选卡规则（用户 2026-11-03 口径，取代 2026-10-29 的「有队友优先」）：
**买的起的情况下 品质优先：彩 > 金 > 紫 > 蓝**；同品质「有队友」优先，
都有队友时「队友多的」优先；还打平再按奖励上限高的优先。
价格从页面读（「打折」下面那个数字），读不到不猜。
"""

import re

from src.tasks.ui import labels

# 地点名（地图上的按钮文字 / 状态里的键）
PLACE = "游戏厅"

# 退出游戏厅的兜底坐标（右上角返回）。
EXIT_COORDS = (0.945, 0.040)


class ArcadePlace:
    """游戏厅的判断与状态。**不点击**，只给判断。"""

    # ---------- 页面判据 ----------

    def arcade_page(self, texts):
        """这是不是游戏厅内页？"""
        return self.match_text(texts, labels.ARCADE_TITLE) is not None

    def arcade_skip(self, texts):
        """找「跳过」按钮（已三星的小游戏）。

        先看选项区，再退回全屏 —— 「跳过」有时在选项区外。
        """
        skip = self.match_text(self.texts_in_region(texts, "weekend_options"),
                               labels.ARCADE_SKIP)
        if skip is None:
            skip = self.match_text(texts, labels.ARCADE_SKIP)
        return skip

    def arcade_paid_enabled(self):
        """配置项「游戏厅付费」开了吗？"""
        return bool(self.config.get("游戏厅付费"))

    # ---------- 选哪张卡（纯判断） ----------

    # 品质优先级（用户 2026-11-03：彩 > 金 > 紫；2026-11-06 用户口径：**没有蓝** ——
    # 蓝/白不是「最后兜底」，是根本不买。只剩蓝卡 → 退出游戏厅，钱留着。
    # 排序用 `scoring.QUALITY_RANK` 同一张表，别再各写一份；「其他」= 认不出的白卡，不买。
    ARCADE_QUALITIES = ("彩", "金", "紫")

    @staticmethod
    def _arcade_mates(card):
        """这张卡挂了几个人：新字段 `mates`（个数），老夹具只有 `mate`（有/没有）。"""
        if "mates" in card:
            return int(card.get("mates") or 0)
        return 1 if card.get("mate") else 0

    def arcade_cards(self, texts):
        """读出游戏厅的卡片列表。

        返回 [{"attr","hi","box","auto","price","quality","hue","sat","mate","mates"}]；
        `mates` 是队友个数，`mate` 是它的布尔值（老夹具只有后者）。
        """
        money = self.read_money(texts)
        icons = self.find_teammate_icons(0.0, 1.0)
        cards = []
        for t in texts:
            m = re.search(r"(投射|弹跳|防守|体能|运控|意识)\s*(\d+)\s*[~～]\s*(\d+)",
                          t.name)
            if m is None:
                continue
            cx = t.x + t.width / 2
            band = self.width * 0.12
            auto = any(o.name.strip() == "自动"
                       and abs((o.x + o.width / 2) - cx) < band
                       and o.y < t.y for o in texts)
            # 价格：**打折时会出现两个数字并且位置会偏**（用户 2026-10-27），
            # 所以不用固定坐标，扫「奖励下方 0.30h、同一列」的所有数字取最小值
            # （= 折后价）。OCR 还会把原价和现价粘成一个长串（「10080」），
            # 这种按 3 位切回来。
            prices = []
            for d in texts:
                v = d.name.strip()
                if not v.isdigit():
                    continue
                if abs((d.x + d.width / 2) - cx) > band:
                    continue
                if not (t.y < d.y < t.y + self.height * 0.30):
                    continue
                if len(v) <= 3:
                    prices.append(int(v))
                elif len(v) in (5, 6):
                    for k in range(0, len(v) - 2, 3):
                        prices.append(int(v[k:k + 3]))
            price = min(prices) if prices else None
            # 品质：取卡片「名字那一行 → 奖励那一行」之间的底色。
            # 排序口径见 `pick_arcade_card`（2026-11-03：彩>金>紫>蓝）；
            # 这里**不判彩色**（`rainbow=False`）：游戏厅卡面高光会把单色卡误判成彩虹，
            # 代价只是「彩色卡被当成金卡」，不会买错品质档。
            name_box = next((o for o in texts
                             if o.name.strip() == m.group(1)
                             and abs((o.x + o.width / 2) - cx) < band
                             and o.y < t.y), None)
            y1f = ((name_box.y / self.height) if name_box is not None
                   else t.y / self.height - 0.10)
            q, qh, qs = self.card_quality_at(cx / self.width, y1f,
                                             t.y / self.height)
            # 队友：卡片上方/左上挂的橙色小头像（和餐厅打工同一套判据）。
            # 2026-10-29 实跑：中间那张带队友的卡没被选中 —— 旧逻辑只看奖励上限。
            # 2026-11-03：改成**数个数**（「都有队友时队友多的优先」）。
            mates = self.mates_on_card(t, icons)
            cards.append({"attr": m.group(1), "hi": int(m.group(3)),
                          "box": t, "auto": auto, "price": price,
                          "quality": q, "hue": qh, "sat": qs,
                          "mate": bool(mates), "mates": int(mates or 0)})
            self.log_debug(f"游戏厅卡片：{m.group(1)} 奖励{m.group(2)}~{m.group(3)}"
                           f" 自动={auto} 队友={mates} 品质={q}(H={qh:.0f} S={qs:.0f})"
                           f" 价格={price}")
        return cards

    def pick_arcade_card(self, cards, money):
        """选哪张小游戏？返回 (卡片, 原因) 或 (None, 原因)。

        规则（用户 2026-11-03；2026-11-06 修订：**没有蓝**）：
        - 只买「自动」+ 彩/金/紫 的卡；蓝/白不是兜底，是不买；
        - **买的起的情况下**按 品质 → 有队友 → 队友多 → 奖励上限 排序；
        - 免费（0 元）优先拿；价格读不到不猜；钱读不到不买（盲买是死循环）。
        """
        ok_cards = [c for c in cards
                    if c["auto"] and c["quality"] in self.ARCADE_QUALITIES]
        if not ok_cards:
            return None, ("没有「自动」+ 彩/金/紫 的小游戏（"
                          + "、".join(f"{c['attr']}/{c['quality']}"
                                      f"{'/自动' if c['auto'] else ''}"
                                      for c in cards) + "）")

        # 品质永远第一档（2026-11-03）；同品质才比队友，队友一样多再比奖励上限。
        auto_cards = sorted(
            ok_cards,
            key=lambda c: (-self.QUALITY_RANK.get(c["quality"], 0),
                           -self._arcade_mates(c), -c["hi"]))
        for c in auto_cards:
            price = c["price"]
            if price == 0:
                # 免费小游戏（白神时久那类 0 元商品）：不花钱，钱读不到也能买
                return c, "免费（0 元）"
            if price is None:
                continue                    # 价格读不到：不猜
            if money is None:
                continue                    # 钱读不到就不买，盲买就是 2026-10-29 的死循环
            if money >= price:
                return c, f"零用钱 {money} ≥ {price}"
        if money is None:
            return None, "零用钱读不到，先不买（避免盲买）"
        if all(c["price"] is None for c in auto_cards):
            return None, "卡片价格都没读出来，先不买（不猜）"
        return None, f"带「自动」的都买不起（零用钱 {money}）"
