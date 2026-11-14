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

2026-11-11 用户口径（**取代**「第 7 周前只买 0 元」和「彩金紫」两套旧规则）：
- 商店内顺序**对所有流派相同**（2026-11-13 定稿：现金 → 牛奶 → 精力 →
  器材指导教程/指导书 → 器材手册 → 教科书 → 属性）。流派只决定「进不进店、
  带多少钱」，不决定买什么；
- **训练流 / 商店流**：进店就按顺序买，不设周数限制；
- **扭蛋流 / 小游戏流**：前 6 周零花钱留给周末的步行街/游戏厅，只拿必拿；
- 现金类（老板的馈赠 / 大乐透）永远最先，纯赚档不看余额；
- 苦味/怪味、神之庇佑、同心护符：当前策略下不买；
- 精力商品仍过三类门槛（见 `shop_stamina_gate`）。
"""

from src.tasks.ui import labels
from src.tasks.training import attributes
from src.tasks.training.constants import (LATE_SHOP_FROM_WEEK,
                                          SHOP_BASE_CATEGORY, SHOP_ITEMS,
                                          SHOP_ITEM_PREFIX,
                                          SHOP_NEVER_BUY,
                                          SHOP_SEASON_WEEKS,
                                          SHOP_STAMINA_ITEMS,
                                          SHOP_STAMINA_UNKNOWN_ASSUME,
                                          SHOP_TRAINING_WEEKDAYS)

# 星期 → 数字（用来算剩余训练日）
WEEKDAY_INDEX = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "日": 7, "天": 7}

# 时限商品至少还要有这么多训练日才值得买（兑现不了就不买）
SHOP_MIN_DAYS_LEFT = {"milk": 3, "textbook": 2, "equipment_boost": 2}

# 地点名（地图上的按钮文字 / 状态里的键）
PLACE = "商店"

# 关店的兜底坐标（右上角 X）。实测帧里 OCR 的 X 框中心偏下约 8% 框高，
# 所以先走文字框、按坐标兜底时用 y=0.11。
EXIT_COORDS = (0.94, 0.11)


class ShopPlace:
    """商店的判断与状态。**不点击**，只给判断。"""

    # 2026-11-12 用户口径（合并 11-11 的类别优先 + 11-08 的品质判断）：
    # 顺序 = 现金 → 牛奶 → 精力 → 其余按 **彩/金/紫**（蓝/白不买）
    # → 同品质内按下面 SHOP_SLOT_RULES。
    # 牛奶/精力是功能性商品（用户口径里排在品质之前），不按颜色筛。
    SHOP_BUY_QUALITIES = ("彩", "金", "紫")
    SHOP_QUALITY_RANK = {"彩": 0, "金": 1, "紫": 2}
    SHOP_GROUP = {"milk": 0, "stamina": 1}   # 不在表里的进第 2 组（按品质）
    # 只挡**明确取色到**的蓝/白；"其他"=取色失败，不等于蓝卡，照买。
    SHOP_SKIP_QUALITIES = ("蓝", "白")

    # 2026-11-13 用户口径：**同品质内**的先后
    #   器材指导教程 / 器材指导书（同档，按数值分先后） → 器材手册 → 教科书 → 属性
    # 牛奶/精力由 SHOP_GROUP 排在品质之前，不参与这张表。
    # 名字认不出时退回类别档，OCR 错字只会掉档、不会让商品消失。
    SHOP_SLOT_RULES = (
        ("器材指导教程", 0),
        ("器材指导书", 0),
        ("器材手册", 1),
        ("教科书", 2),
    )
    SHOP_SLOT_BY_CATEGORY = {"textbook": 2, "equipment_boost": 1,
                             "attribute": 3}

    # ---------- 详情面板效果文本（2026-11-12） ----------
    # 商品卡只显示「名字 + 价格 + 品质底色」，**效果要选中卡片后看右侧详情**。
    # 靠 `SHOP_ITEMS` 推类别必须图鉴点亮，而每个账号点亮进度不同 → 不成立。
    # 所以改成「点卡选中（不花钱）→ OCR 右侧 → 按游戏给的效果文案判类别」，
    # 商品表降级成缓存/兜底。效果文案来自 `logs/captures/codex/codex_items.tsv`。
    #
    # 顺序有意义：先否后肯、先具体后宽泛。
    #   - 「随机属性-10」（苦味）必须排在「随机属性」（属性）之前；
    #   - 「训练等级」（教科书）必须排在「投射」之前；
    #   - 「训练消耗精力-40%」（牛奶）不能命中「训练精力消耗+10%」（器材教程）。
    SHOP_EFFECT_RULES = (
        ("零用钱", "cash"),
        ("必定成功", "never"),            # 神之庇佑
        ("队友训练", "never"),            # 同心护符
        ("随机属性-", "never"),           # 苦味饮料：拿属性换精力
        ("什么都没发生", "never"),        # 怪味饮料：可能白花钱
        ("训练消耗精力-", "milk"),        # 特训牛奶：训练消耗精力-40%
        ("训练等级", "textbook"),         # 投射训练等级+1
        ("器材熟练度", "equipment_boost"),  # 全部器材熟练度+10
        ("器材暴击训练收益", "equipment_boost"),
        ("精力+", "stamina"),
        ("全属性", "attribute"),
        ("推荐属性", "attribute"),
        ("随机一个属性", "attribute"),
        ("随机属性", "attribute"),
        ("随机获得", "attribute"),
        ("投射", "attribute"),
        ("弹跳", "attribute"),
        ("防守", "attribute"),
        ("运控", "attribute"),
        ("体能", "attribute"),
        ("意识", "attribute"),
    )

    def parse_shop_detail(self, text):
        """详情面板的效果文本 → 类别；认不出返回 None（调用方退回名字表）。"""
        clean = (text or "").replace(" ", "").replace("\n", "")
        if not clean:
            return None
        for word, category in self.SHOP_EFFECT_RULES:
            if word in clean:
                return category
        return None

    def parse_shop_stamina(self, text):
        """详情文本里的精力恢复量：`精力+60，随机属性+10` → 60；读不到 None。"""
        clean = (text or "").replace(" ", "")
        start = clean.find("精力+")
        if start < 0:
            return None
        digits = ""
        for ch in clean[start + 3:]:
            if ch.isdigit():
                digits += ch
            else:
                break
        return int(digits) if digits else None

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

    def shop_stamina_gate(self, item, stamina, stamina_max):
        """这件恢复精力商品过不过三类门槛？返回拒因字符串，None = 能买。

        2026-11-08 用户口径（商店版，数据来自登记表；扭蛋侧同一套口径在
        `places/street.py: recover_rejected`，只是数据来源是卡面效果文本）：
          ① 立刻恢复 N：当前 + N > 上限 → 溢出，拒买
          ② 全部恢复：上限 − 当前 ≤ 100 → 缺口太小，拒买
          ③ 接下来几日恢复：当前 / 上限 ≥ 1/2 → 精力还够，拒买
        登记表里恢复量是 None（还没采集到）→ 按 `SHOP_STAMINA_UNKNOWN_ASSUME`
        保守估算（2026-11-12：满精力照买过一瓶酸奶+，溢出 100 点）；
        读不到精力/上限 → 不拦（同「周数读不到不过滤」口径，OCR 抖一帧不罢工）。
        """
        name = item.get("name") or ""
        # 详情面板读到的恢复量优先（2026-11-12）：它是游戏当前版本的真值，
        # 不受「图鉴没点亮 / 名字是 OCR 错字」影响；读不到才查登记表。
        if item.get("detail_category") == "stamina" and item.get("stamina_amount"):
            kind, amount = "immediate", item["stamina_amount"]
        else:
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
            assumed = amount is None
            if assumed:
                amount = SHOP_STAMINA_UNKNOWN_ASSUME
            if stamina + amount > stamina_max:
                note = "，恢复量未登记按 %d 估" % amount if assumed else ""
                return (f"立刻 {amount} 会溢出"
                        f"（{stamina}+{amount}>{stamina_max}{note}）")
            return None
        if kind == "daily":
            if stamina / stamina_max >= 0.5:
                return f"精力 {stamina}/{stamina_max} ≥ 一半"
            return None
        return None

    # ---------- 商品表 / 流派优先级 ----------

    def shop_base_name(self, name):
        """商品基础名：去掉尾部 `+ / ++ / ＋` 和空白（2026-11-12 用户口径）。

        「老板的馈赠++」「大乐透+」这类后缀只影响售价/数值，类别不变；
        识别只认基础名，图鉴没点亮变体也能归类。
        """
        base = (name or "").strip()
        while base and base[-1] in "+＋":
            base = base[:-1].strip()
        return base

    def shop_category(self, name):
        """商品类别：精确表 → **基础名** → 名字前缀 → 默认属性。

        2026-11-12 用户口径：名字只认基础名 ——
          老板的馈赠 / + / ++  → 一个基础名全覆盖；
          六个属性 × 教科书（18 种）→ 6 个基础名；
          器材指导书 / 器材指导教程 / 器材手册（9 种）→ 3 个基础名。
        精确表 `SHOP_ITEMS` 仍保留（带售价/数值，做排序缓存），但**不再是前提**。

        归「属性」是保守兜底：它仍然受周数、余额、上限余量约束，
        不会像 `never` 那样被无条件丢掉，也不会像 `cash` 那样无条件优先。
        """
        name = (name or "").strip()
        hit = SHOP_ITEMS.get(name)
        if hit is not None:
            return hit[0]
        base = self.shop_base_name(name)
        cat = SHOP_BASE_CATEGORY.get(base)
        if cat is not None:
            return cat
        for prefix, cat in SHOP_ITEM_PREFIX:
            if prefix in name:
                return cat
        return "attribute"

    def item_category(self, item):
        """商品类别：**详情面板读到的优先**，读不到才退回名字表/前缀。

        2026-11-12：名字表要求图鉴点亮，各账号进度不同；详情面板是游戏自己
        给的效果文案，选中即可读，才是权威来源。
        """
        return item.get("detail_category") or self.shop_category(item["name"])

    def shop_slot(self, name, category):
        """同品质内的档位（2026-11-13 用户口径）。

        0 器材指导教程 / 器材指导书（同档，数值再分先后）
        1 器材手册
        2 教科书
        3 属性（再按 `attr_sub`：全属性 → 需求属性 → 专属饮料 → 奇妙秘籍 → 其他）
        名字认不出时退回类别档：教科书 2、其余器材 1、属性 3。
        所有流派共用同一条顺序，商店里不按流派筛选。
        """
        for word, slot in self.SHOP_SLOT_RULES:
            if word in (name or ""):
                return slot
        return self.SHOP_SLOT_BY_CATEGORY.get(category, 3)

    def shop_known(self, name):
        """这个名字认得出来吗？精确表 → 基础名 → 前缀，任一层命中就算。"""
        return (name in SHOP_ITEMS
                or self.shop_base_name(name) in SHOP_BASE_CATEGORY
                or any(p in (name or "") for p, _c in SHOP_ITEM_PREFIX))

    def shop_attr_target(self, name):
        """属性商品加哪一项：`ALL` / 属性名 / None（判据在 `attributes` 一份）。"""
        return attributes.item_attr_target(name)

    def shop_value(self, item):
        """同档内比大小用的数值。

        未登记商品按品质色给个估值（彩75/金50/紫25），但它带 `unknown=1`
        子键，排在所有已登记商品之后，不会用估值压过真实的点数。
        """
        hit = SHOP_ITEMS.get(item["name"])
        if hit is None:
            # 变体（+ / ++）没单独登记时，用基础款的数值当参考
            hit = SHOP_ITEMS.get(self.shop_base_name(item["name"]))
        if hit is None:
            return {"彩": 75, "金": 50, "紫": 25}.get(
                item.get("quality"), 0)
        cat, _price, val = hit
        if val is None:
            return 0
        if cat == "milk":
            return val[0] * 10 + val[1]
        if cat == "textbook":
            return val[1]
        if cat == "equipment_boost":
            return val[1] if isinstance(val, tuple) else 0
        if cat == "stamina" and isinstance(val, tuple):
            return val[0]
        return val if isinstance(val, (int, float)) else 0

    def save_money_for_weekend(self):
        """这个流派的零花钱要留给周末自己的场所吗？

        2026-11-11 用户口径：
        - 扭蛋流（步行街）/ 小游戏流（游戏厅）要留 360，前 6 周只拿必拿；
        - 训练流 / 商店流不设这道限制，进店就按顺序买；
        - 流派未知时按保守处理（当作要留钱）。
        """
        if not self.style_known:
            return True
        return self.style in ("扭蛋流", "小游戏流")

    def shop_targets(self):
        """本局要重点买的科目/属性集合；None = 不筛选（目前默认）。

        将来按队长定位算：
          队长名 → `roster.lookup` → 位置/推荐属性 → 科目集合
        返回集合后，非目标科目的教科书/属性商品会降一个子档（不剔除，
        避免刷不出目标时什么都不买）。
        """
        return None

    def _target_penalty(self, item):
        """非目标科目的教科书/属性商品 → 1；其余或未指定目标 → 0。"""
        targets = self.shop_targets()
        if not targets:
            return 0
        if self.shop_category(item["name"]) not in ("textbook", "attribute"):
            return 0
        return 0 if any(t in item["name"] for t in targets) else 1

    def training_days_left(self):
        """本局剩余训练日（周一~周五）；读不到日期返回 None（不拦）。"""
        week, day = self.current_day()
        idx = WEEKDAY_INDEX.get(day or "")
        if week is None or idx is None:
            return None
        this_week = max(0, SHOP_TRAINING_WEEKDAYS - idx + 1) if idx <= 5 else 0
        full_weeks = max(0, SHOP_SEASON_WEEKS - week)
        return this_week + full_weeks * SHOP_TRAINING_WEEKDAYS

    def pick_shop_item(self, items, money, policy, stamina=None, stamina_max=None,
                       required=None, attrs=None, caps=None,
                       soft_cap=1300, gap_limit=100):
        """选哪件商品？返回 (商品, 原因) 或 (None, 原因)。

        2026-11-11 重构（用户口径）：**一条管道** admit → tier → rank，
        不再有「彩金紫」和「精力垫底」的硬编码分支。

        顺序：
          1. 永不买（苦味/怪味/庇佑/护符）直接剔；
          2. 「不买」策略总否决；
          3. 现金类（老板的馈赠/大乐透）无条件优先；
          4. 扭蛋流/小游戏流第 7 周前只买 0 元；
          5. 精力类过三类门槛；
          6. 余额不够的不买；
          7. 排序分组：**牛奶 → 精力 → 其余**；牛奶/精力不按颜色筛（功能性商品），
             其余**只挡明确的蓝/白**，取色失败（其他）照买（0 元商品不受限）；
          8. 时限商品要剩余训练日够；
          9. 排序：分组 → 其余组的品质 彩→金→紫 → 同品质档位
             （器材指导教程/指导书 → 器材手册 → 教科书 → 属性）
             → 属性子档 → 数值 → 价格。

        2026-11-12 用户口径（对 11-11 的补充，不是回退）：
        现金、牛奶依然最先，接着各种精力商品，然后才按 彩→金→紫 挑；
        同品质内再按 2026-11-13 的档位表。11-11 的「牛奶/精力优先」保留。
        「默认不买蓝色」只作用于最后一组 —— 蓝色属性饮料不再被买走。

        2026-11-13 用户口径（最后一场比赛）：第 7 周周日只买直接加六维的
        属性商品，**不分品质**（蓝/白也买）；器材、教科书、牛奶、精力都不买。
        属性缺口、`soft_cap` 减半、余额、0 元商品规则照旧。
        """
        week, day = self.current_day()
        items = [i for i in items
                 if self.shop_category(i["name"]) != "never"
                 and not any(w in i["name"] for w in SHOP_NEVER_BUY)]
        if policy == "不买":
            return None, "策略是只逛不买"

        # 现金类：必拿。价格以**表里登记的售价**为准（OCR 把 0 元读成 200
        # 也照拿）；表里没有的才看 OCR 价格。
        cash = [i for i in items if self.shop_category(i["name"]) == "cash"]
        for i in cash:
            hit = SHOP_ITEMS.get(i["name"])
            if hit is not None:
                i["table_price"] = hit[1]
        free_cash = [i for i in cash
                     if i.get("table_price", i["price"]) == 0]
        paid_cash = [i for i in cash
                     if i.get("table_price", i["price"]) > 0
                     and money is not None
                     and i.get("table_price", i["price"]) <= money]
        if free_cash or paid_cash:
            pick = max(free_cash or paid_cash, key=lambda i: -i["y"])
            return pick, "必定优先拿（%s）" % pick["name"]

        # 2026-11-14 用户口径（最后一周策略，**两层**）：
        #   - **第 7 周整周**：不买器材、教科书、牛奶、精力（训练类兑现不了）；
        #   - **第 7 周周日**（最后一场比赛）：同上，且属性商品**蓝色也买**。
        # 所以周日 = 第 7 周规则 + 放开蓝色；不用去算「训练一天比赛一天」。
        # 属性缺口、soft_cap 减半、余额、0 元商品规则照旧。
        final_week = week is not None and week >= LATE_SHOP_FROM_WEEK
        final_match = week == SHOP_SEASON_WEEKS and day == "日"
        if final_week:
            items = [i for i in items
                     if i["price"] == 0
                     or self.item_category(i) not in ("equipment_boost",
                                                      "textbook", "milk",
                                                      "stamina")]
            if not items:
                return None, "最后一周：店里只剩器材/教科书/牛奶/精力，关店"
        if final_match:
            # 0 元（红包）白送，不算购买，照拿（和「0 元照拿」的既有口径一致）
            items = [i for i in items
                     if i["price"] == 0
                     or self.item_category(i) == "attribute"]
            if not items:
                return None, "最后一场比赛：店里没有属性商品，关店"

        # 扭蛋流 / 小游戏流：前 6 周零花钱要留周末，只拿必拿（现金类，
        # 上面已经返回）；训练流 / 商店流不设周数限制，进店就按顺序买。
        if (week is not None and week < LATE_SHOP_FROM_WEEK
                and self.save_money_for_weekend()):
            return None, "第 7 周前扭蛋/小游戏只买必拿（零花钱留周末）"

        # 精力门槛：按 `SHOP_STAMINA_ITEMS` 登记表判（不是按类别），
        # 免费的不拦；登记表外的商品 `shop_stamina_gate` 直接返回 None。
        kept, rejected = [], []
        for i in items:
            if i["price"] == 0:
                kept.append(i)
                continue
            why = self.shop_stamina_gate(i, stamina, stamina_max)
            if why:
                i["reject"] = why
                rejected.append("%s（%s）" % (i["name"], why))
            else:
                kept.append(i)
        if not kept:
            return None, "恢复精力商品不满足门槛，关店：" + "、".join(rejected)

        # 2026-11-13 用户口径：第 7 周（赛季最后一周）不再买牛奶/精力。
        # 0 元商品不受限：白送的不叫购买，和「0 元照拿」的既有口径一致。
        if week is not None and week >= LATE_SHOP_FROM_WEEK:
            kept = [i for i in kept
                    if i["price"] == 0
                    or self.item_category(i) not in ("milk", "stamina")]
            if not kept:
                return None, "第 7 周不买牛奶/精力，关店"

        # 余额
        affordable = [i for i in kept
                      if i["price"] == 0
                      or (money is not None and i["price"] <= money)]
        if not affordable:
            return None, ("没有买得起的商品了"
                          + ("；零用钱读不到，不盲买" if money is None else ""))

        # 时限商品：剩余训练日不够就不买
        days_left = self.training_days_left()
        if days_left is not None:
            affordable = [
                i for i in affordable
                if days_left >= SHOP_MIN_DAYS_LEFT.get(
                    self.item_category(i), 0)]

        # 2026-11-12 用户口径（对 11-11 的补充，不是回退）：
        #   现金 → 牛奶 → 精力 → 其余按 彩/金/紫 → 同品质档位（教程/手册/教科书/属性）。
        # 牛奶/精力是功能性商品（用户把它们的优先级排在品质之前），**不按颜色筛**；
        # 其余（教科书/器材/属性）默认只买 彩/金/紫 —— 蓝/白不买。
        #
        # 2026-11-12 用户口径（品质没认出来时）：**只挡明确的蓝/白**。
        #   - 牛奶/精力不按颜色筛（功能性商品，优先级本来就在品质之前）；
        #   - 其余只要不是明确读到「蓝/白」就参与购买 —— **不再要求名字已登记**。
        #     未登记的新商品、彩卡本来就可能取色失败（实跑「器材指导教程+」
        #     一帧判其他、一帧判金；选中的黄框会污染采样，见
        #     `parsers.card_quality`）。拿「有没有登记」当购买前提，等于要求
        #     把整个图鉴人工补一遍 —— 用户 2026-11-12 明确否掉。
        premium = []
        for i in affordable:
            cat = self.item_category(i)
            quality = i.get("quality")
            if i["price"] == 0 or cat in self.SHOP_GROUP:
                premium.append(i)
            elif final_match and quality != "白":
                premium.append(i)
            elif quality not in ("蓝", "白"):
                premium.append(i)
        if not premium:
            return None, "只剩蓝/白商品了（默认不买蓝色）"
        affordable = premium

        # 排序：分组（牛奶→精力→其余）→ 其余组的品质 彩→金→紫
        #       → 0 元 → 同品质档位（教程/指导书→手册→教科书→属性）
        #       → 属性子档 → 数值 → 价格 → 屏幕靠上
        ranked = []
        required = set(required or ())
        attrs = attrs or {}
        caps = caps or {}
        for i in affordable:
            cat = self.item_category(i)
            slot = self.shop_slot(i["name"], cat)
            group = self.SHOP_GROUP.get(cat, 2)
            quality = (0 if group != 2
                       else self.SHOP_QUALITY_RANK.get(i.get("quality"), 9))
            # 2026-11-13：属性类内部 全属性 > 需求属性 > 专属饮料 > 奇妙秘籍 > 其他；
            # 缺口不足 `gap_limit` 的不买，过 `soft_cap` 的数值减半。
            # 2026-11-14：**随机效果商品降到最后**（偶然饮料 = 随机一个属性+N，
            # 期望值只有 N/6，还可能加到不需要的属性上）。
            # 「专属饮料」（两个推荐属性+N）是**确定的**，保持原档。
            attr_sub = 0
            value = self.shop_value(i)
            if cat == "attribute":
                target = self.shop_attr_target(i["name"])
                if attributes.is_random_effect(i["name"]):
                    attr_sub = 5
                elif target == "ALL":
                    attr_sub = 0
                elif target in required:
                    attr_sub = 1
                elif target == "PREFERRED" or "专属" in i["name"]:
                    attr_sub = 2
                elif "奇妙" in i["name"]:
                    attr_sub = 3
                else:
                    attr_sub = 4
                cur, cap = attrs.get(target), caps.get(target)
                if (target and target not in ("ALL", "PREFERRED")
                        and cur is not None and cap is not None):
                    if cap - cur < gap_limit:
                        continue
                    if cur >= soft_cap:
                        value *= 0.5
            ranked.append((group, quality, 0 if i["price"] == 0 else 1, slot,
                           attr_sub,
                           0 if self.shop_known(i["name"]) else 1,
                           self._target_penalty(i),
                           -value, i["price"], i["y"], i))
        if not ranked:
            return None, "当前流派不买这些商品"
        ranked.sort(key=lambda r: r[:10])
        pick = ranked[0][10]
        return pick, "按顺序购买（%s/%s）" % (
            pick.get("quality"), self.item_category(pick))
