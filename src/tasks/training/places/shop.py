"""商店（买器材/手册）：**地点模块**。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：
每个地点有自己的模块，只管自己的判断。

界面（实机 2026-10-26）：
- 左侧商品卡：名称 + 剩余数量 + 价格（100 / 60 / 45），**卡片底色 = 品质色**；
- 右侧是选中商品的详情和**黄色购买条**（价格就在条上）；
- 右上角 X 关闭；顶栏金币是专注条右边那个独立数字。

用户口径（2026-10-26）：
- 平时只买金卡和紫卡（蓝/白不买）；
- **不清空**（2026-11-05 用户口径：比赛日也不清空，不再有「赛前把钱花光」这条）。

所以这里一拍只买**一件**：买完不关店，下一拍重新 OCR 再决定；
按策略买不动了 / 钱花光了才关店。

2026-11-05 用户口径：进了店就按「商店购买策略」正常顺序买，**不清空**。

2026-11-08 用户口径（**取代**之前的四选项策略）：
- **第 7 周之前只买 0 元商品（红包）**，非 0 一律不买；
- 第 7 周以后按 **红包 → 彩 → 金 → 紫** 的顺序买；
- 精力类商品仍按之前的逻辑：**垫底** + 三类门槛（见 `shop_stamina_gate`），
  只买符合条件的；免费的照样优先拿。

2026-11-07 用户口径：**恢复精力的商品购买优先级最低**——彩金紫里先挑非恢复的
（品质 → 价格），池里只剩恢复的才买它；免费的（0 元）不受影响，照样优先拿。

2026-11-08 用户口径：**恢复精力商品过三类门槛**（登记过恢复量的才拦）：
① 立刻恢复 N：当前+N > 上限 → 溢出拒买；② 全部恢复：上限−当前 ≤100 → 拒；
③ 接下来几日恢复：当前/上限 ≥ 1/2 → 拒。登记表是
`constants.SHOP_STAMINA_ITEMS`（列表卡没有效果文本，只能按名字认；
用户实跑看到就报，会话里加一行；恢复量 None = 只垫底、不拦）。
"""

from src.tasks.ui import labels
from src.tasks.training.constants import (LATE_SHOP_FROM_WEEK, SHOP_MUST_TAKE,
                                          SHOP_NEVER_BUY, SHOP_STAMINA_ITEMS)

# 地点名（地图上的按钮文字 / 状态里的键）
PLACE = "商店"

# 关店的兜底坐标（右上角 X）。实测帧里 OCR 的 X 框中心偏下约 8% 框高，
# 所以先走文字框、按坐标兜底时用 y=0.11。
EXIT_COORDS = (0.94, 0.11)


class ShopPlace:
    """商店的判断与状态。**不点击**，只给判断。"""

    # ---------- 页面判据 ----------

    def shop_page(self, texts):
        """这是不是商店内页？

        周末首页也有「商店」两个字，但同时有「出行」—— 那是按钮不是商店页。
        还要有「上新时间/剩余/能力详情」之一才认（别把别的页面的「商店」当商店）。
        """
        if self.match_text(texts, "出行") is not None:
            return False
        if self.match_text(texts, "商店") is None:
            return False
        return self.match_text(texts, ("上新时间", "剩余", "能力详情")) is not None

    # ---------- 「新品上市」提示：第 7 周以后要不要进店 ----------

    def _has_fresh_mark(self, name):
        """这段文字是不是商店入口上方的「上新」标签？"""
        name = (name or "").strip()
        if not name or any(x in name for x in labels.SHOP_FRESH_EXCLUDE):
            return False
        return any(x in name for x in labels.SHOP_FRESH_MARKS)

    def shop_fresh_badge(self, texts, shop_box):
        """商店按钮**头顶**有没有「新品上市」提示？返回 (提示框, 原因)。

        实测（用户 2026-11-05 截图，第 3 周 星期一 比赛日主界面，1552×871）：
        橙色标签「新品上市」中心 ≈(0.920, 0.156)，「商店」按钮中心 ≈(0.920, 0.230)
        —— 同一列、按钮上方约 0.07 屏高。

        判据**连位置一起判**（沿用「扭蛋增强！」的教训：全屏出现同样字眼就生效，
        别处台词一提「上新」就会误进店）：
        - 提示中心必须落在按钮**正上方**：横向对齐（|Δcx| ≤ 0.15）、纵向在按钮上方
          一小段（Δcy ∈ [0.01, 0.15]，Δcy 为负 = 在下面）；
        - 提示和按钮被 OCR 读成一整块（框名里就带「新品上市」）也算 —— 那种情况
          中心会和按钮重合，位置判据判不了，按文字认；
        - 长度限制在 2~8 字，别把正文当标签。
        """
        if not texts:
            return None, "没读到文字"
        # 提示和按钮被 OCR 读成一整块（比如整个框叫「新品上市 商店」）
        if self._has_fresh_mark(getattr(shop_box, "name", "")):
            return shop_box, "按钮框本身就是「%s」" % shop_box.name.strip()
        bx = (shop_box.x + shop_box.width / 2) / self.width
        by = (shop_box.y + shop_box.height / 2) / self.height
        best, best_dy, best_dx = None, None, None
        for t in texts:
            name = t.name.strip()
            if not self._has_fresh_mark(name):
                continue
            if not (2 <= len(name) <= 8):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            dx = abs(cx - bx)
            dy = by - cy                    # > 0 = 在按钮上方
            if dx > 0.15:
                continue                    # 不在按钮这一列（左边/右边/正文）
            if dy < 0.01 or dy > 0.15:
                continue                    # 同一行（dy≈0）或离太远都算「没贴在头上」
            if best is None or (dy, dx) < (best_dy, best_dx):
                best, best_dy, best_dx = t, dy, dx
        if best is None:
            return None, "商店按钮上方没有「新品上市」提示"
        return best, "「%s」在商店按钮上方" % best.name.strip()

    # ---------- 买哪件（纯判断） ----------

    def shop_items(self, texts):
        """读出商品列表（`read_shop_items` 的封装）。"""
        return self.read_shop_items(texts)

    @staticmethod
    def _is_stamina_drink(item):
        """这件商品是不是恢复精力的（垫底 + 可能过门槛）。

        2026-11-07 用户口径：恢复精力的商品优先级最低（垫底顺序在外层）。
        2026-11-08 用户口径：登记过恢复量的还要过三类门槛（见 `stamina_gate`）。
        判据 = `constants.SHOP_STAMINA_ITEMS` 登记表（列表卡只有名字 + 价格，
        没有效果文本，只能按名字认；用户实跑采集、会话里加行）。
        """
        name = item.get("name") or ""
        return any(word in name for word in SHOP_STAMINA_ITEMS)

    def shop_stamina_gate(self, item, stamina, stamina_max):
        """这件恢复精力商品过不过三类门槛？返回拒因字符串，None = 能买。

        2026-11-08 用户口径（商店版，数据来自登记表；扭蛋侧同一套口径在
        `places/street.py: recover_rejected`，只是数据来源是卡面效果文本）：
          ① 立刻恢复 N：当前 + N > 上限 → 溢出，拒买
          ② 全部恢复：上限 − 当前 ≤ 100 → 缺口太小，拒买
          ③ 接下来几日恢复：当前 / 上限 ≥ 1/2 → 精力还够，拒买
        登记表里恢复量是 None（还没采集到）→ **不拦，只垫底**；读不到
        精力/上限 → 不拦（同「周数读不到不过滤」口径，OCR 抖一帧不罢工）。
        """
        name = item.get("name") or ""
        hit = None
        for word, effect in SHOP_STAMINA_ITEMS.items():
            if word in name and (hit is None or len(word) > len(hit[0])):
                hit = (word, effect)
        if hit is None:
            return None
        kind, amount = hit[1]
        if stamina is None or stamina_max is None or stamina_max <= 0:
            return None
        room = max(stamina_max - stamina, 0)
        if kind == "full":
            if room <= 100:
                return f"缺口 {room} ≤ 100"
            return None
        if kind == "immediate":
            if amount is None:
                return None                    # 恢复量未登记：只垫底，不拦
            if stamina + amount > stamina_max:
                return f"立刻 {amount} 会溢出（{stamina}+{amount}>{stamina_max}）"
            return None
        if kind == "daily":
            if stamina / stamina_max >= 0.5:
                return f"精力 {stamina}/{stamina_max} ≥ 一半"
            return None
        return None

    def pick_shop_item(self, items, money, policy, stamina=None, stamina_max=None):
        """选哪件商品？返回 (商品, 原因) 或 (None, 原因)。

        `policy` = 配置项「商店购买策略」：红包→彩→金→紫 / 不买。
        `stamina` / `stamina_max` = 当前精力 / 上限（顶栏读，可能 None）——
        给恢复精力商品的三类门槛用（2026-11-08 用户口径）。

        2026-11-08 用户口径（**取代**之前的「购买策略」四选项）：
          - **第 7 周之前只买 0 元商品（红包）**，非 0 一律不买；
          - 第 7 周以后按 **红包 → 彩 → 金 → 紫** 的顺序买；
          - 精力类商品仍按之前的逻辑：**垫底**（先买非恢复的），并先过三类门槛
            （溢出 / 缺口 ≤ 100 / 精力过半都拒买，见 `shop_stamina_gate`），
            **只买符合条件的**；免费的精力商品不受门槛影响。
          - 周数读不到时按第 7 周以后处理（OCR 抖一帧不能把采购整拍废掉）。

        以下两条口径**不受周数影响**，继续生效：
          - `constants.SHOP_MUST_TAKE` 里的商品必定优先拿（2026-11-08「老板的馈赠」、
            2026-11-11「大乐透」）——不看周数、不看品质；纯赚档连余额都不看，
            花钱档要求余额读得到且够价格。「不买」策略仍是否决开关；
          - 苦味/怪味饮料系（带负面或随机效果）一律从候选整体剔除（含免费）。

        2026-11-05 用户口径：任何日子都不清空，只按策略买合适的
        （所以这里没有「把钱花光」分支）。
        """
        week = self.current_day()[0]
        # 2026-11-08 用户口径：苦味/怪味饮料带负面或随机效果（苦味+ = 精力+120、
        # 随机属性-10；怪味 = 精力+30 或什么都没发生），**一律不买**——
        # 从候选整体剔除，连免费/0 元也不拿（负面效果不因免费就值得拿）。
        items = [i for i in items
                 if not any(w in i["name"] for w in SHOP_NEVER_BUY)]
        if policy == "不买":
            return None, "策略是只逛不买"

        # 2026-11-08 / 2026-11-11 用户口径：见到必拿（`constants.SHOP_MUST_TAKE`）
        # **插在免费分支、周数和品质判断之前**——不看周数、不看品质；余额按表里的
        # 「要不要看余额」分两档：
        #   - 纯赚档（老板的馈赠：0 元 +50 零用钱）→ 不看余额，价格读错也照拿；
        #   - 花钱档（大乐透：60 元换 20~200 零用钱）→ 同样优先，但余额读不到
        #     或不够价格时不盲买（点不动会撞看门狗，把这一天整店关掉）。
        # 「商店购买策略 = 不买」这句在上面已经提前返回，仍是否决开关。
        free_must, paid_must = [], []
        for i in items:
            hit = None
            for word, spec in SHOP_MUST_TAKE.items():
                if word in i["name"] and (hit is None or len(word) > len(hit[0])):
                    hit = (word, spec)
            if hit is None:
                continue
            need_money, why = hit[1]
            i["must_why"] = why
            if need_money and (money is None or i["price"] > money):
                continue
            (paid_must if need_money else free_must).append(i)
        if free_must or paid_must:
            # 纯赚的（不花钱）先拿，省下的钱还能再买别的东西；同档多张时取最上面那张
            pick = max(free_must or paid_must, key=lambda i: -i["y"])
            return pick, f"必定优先拿（{pick['must_why']}）"

        # 0 元商品（红包）：不花零用钱，余额不够、甚至钱读不到时都应该拿。
        free = [i for i in items if i["price"] == 0]
        if free:
            # 免费优先：0 元不花钱，品质只影响先拿哪张
            order = {"彩": 3, "金": 2, "紫": 1}
            pick = max(free, key=lambda i: (order.get(i["quality"], 0), -i["y"]))
            return pick, "0 元红包优先"

        # 2026-11-08 用户口径：第 7 周之前**只买 0 元商品**（上面没有 free 就关店）。
        if week is not None and week < LATE_SHOP_FROM_WEEK:
            return None, "第 7 周之前只买 0 元红包（没有免费商品）"

        # 第 7 周以后：红包 → 彩 → 金 → 紫
        pool = [i for i in items
                if i["quality"] in ("彩", "金", "紫")
                and i["price"] > 0 and money is not None
                and i["price"] <= money]
        if not pool:
            return None, ("没有买得起的商品了（只买彩/金/紫"
                          + ("；零用钱读不到，不盲买" if money is None else "")
                          + "）")

        # 2026-11-08 用户口径：恢复精力商品先过三类门槛 —— 拒买的从候选剔除
        #（免费的不拦：0 元不花钱，照样优先拿）。全被门槛拦掉才关店，
        # 日志里带上每家被拒的原因，方便复盘时核对登记表数值。
        kept, rejected = [], []
        for i in pool:
            why = self.shop_stamina_gate(i, stamina, stamina_max)
            if why:
                i["reject"] = why
                rejected.append(f"{i['name']}（{why}）")
            else:
                kept.append(i)
        if not kept:
            return None, ("恢复精力商品不满足门槛，关店："
                          + "、".join(rejected))

        # 2026-11-07 用户口径：恢复精力的垫底——先买非恢复的，
        # 只剩恢复的（其他彩金紫都买不动了）才轮到它（不是不买）。
        drink_only = all(self._is_stamina_drink(i) for i in kept)
        picks = kept if drink_only else [i for i in kept
                                         if not self._is_stamina_drink(i)]
        pick = max(picks, key=lambda i: ({"彩": 3, "金": 2, "紫": 1}[i["quality"]],
                                         i["price"]))
        return pick, ("彩金紫只剩恢复精力的商品，垫底购买" if drink_only
                      else "红包→彩→金→紫")
