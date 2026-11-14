"""属性取向：一整局「练什么」的统一判断（纯函数，无状态）。

用户 2026-11-13 确认的最终口径：

  1. **位置需求属性**：按队长位置补短板（`POSITION_TARGETS`）；
  2. **缺口 < 阈值**：离上限不足 `缺口排除阈值`（默认 100）→ 排除；
  3. **过减半门槛**：当前值 ≥ `属性减半门槛`（默认 1300）→ score × 0.5；
  4. **需求属性加成**：+0.5 加进 score（在 `scoring.subject_score` 里），
     跟着 0.5 折扣一起打折。

分工：
- 阈值从配置读（`属性减半门槛` / `缺口排除阈值`），本模块只接收参数；
- 本模块不读帧、不碰 self —— 单测可以脱离游戏跑。
"""

SUBJECTS = ("投射", "弹跳", "防守", "体能", "运控", "意识")

# 位置 → 需求属性（用户 2026-11-13 确认；来源：多篇攻略交叉，见 docs/RULES.md）
POSITION_TARGETS = {
    "控卫": ("运控",),
    "分卫": ("运控", "防守"),
    "小前": ("弹跳", "运控"),
    "大前": ("弹跳", "防守"),
    "中锋": ("弹跳", "防守"),
}

# 需求属性在 score 里的权重（用户口径：加在 score 里面，再一起打折）
REQUIRED_BONUS = 0.5

# 当前值读不到时的垫底值（与旧 subject_rank 一致：不能靠「读不到」白捡便宜）
UNKNOWN_VALUE = 10 ** 6

# 默认阈值（配置键缺失时用它；正常路径由调用方从 config 传入）
DEFAULT_SOFT_CAP = 1300
DEFAULT_GAP_LIMIT = 100


def required_attrs(position):
    """位置 → 需求属性集合；查不到返回空集。"""
    return set(POSITION_TARGETS.get(position or "", ()))


def item_attr_target(name):
    """商品/卡片名加哪一项：

    - `ALL` = 全属性；
    - `PREFERRED` = 「两个推荐属性」（不指定具体属性）；
    - `CASH` = 纯零用钱卡（不涨六维）；
    - 属性名 = 专项；
    - None = 认不出。

    用户 2026-11-13：属性类内部顺序 **全属性 > 需求专项 > 推荐属性/专属 > 其他**。
    用户 2026-11-14：**增加零用钱的卡最高优先**（商店流/扭蛋流/小游戏流
    都要零用钱买牛奶/秘籍），所以单独给 `CASH` 一档，排在所有属性之前。

    商店（`places/shop.py`）和扭蛋（`places/street.py`）共用这一份分类，
    不再各写一套「认不认得出推荐属性」。
    """
    name = name or ""
    if "全能" in name or "全属性" in name:
        return "ALL"
    if "推荐属性" in name:
        return "PREFERRED"
    if is_money_card(name):
        return "CASH"
    for attr in SUBJECTS:
        if attr in name:
            return attr
    return None


def effect_attr_target(effect):
    """**效果文本** → 属性类别（卡名认不出时的兜底）。

    2026-11-14：卡名里没有六维属性名，不代表这张卡不是属性卡 ——
    「延迟满足」的效果是「5天后，全队成员获得全属性+13」，实际是全属性卡
    （六项各 +13 = 78 点），但卡名认不出来 → 被归进「其他专项」，
    同品质里输给需求专项卡。

    这里只认**全属性**这一种（用户 2026-11-14：「那排 0 号可以」）：
    效果文本含「全属性 / 所有属性 / 全体」→ 按 `ALL` 处理。
    其它情况返回 None（不猜）。
    """
    text = (effect or "").replace(" ", "")
    if not text:
        return None
    if any(k in text for k in ("全属性", "所有属性", "全体")):
        return "ALL"
    return None


# 增加零用钱的卡名（2026-11-14 实跑）：
#   额外零用钱+  → 「接下来每个周六开始前，获得80零用钱。」
#   额外零用钱++ → 「获得110零用钱。」
#   超前消费     → 「立刻获取300零用钱。所有打工的基础零用钱-60。」
# 「超可消费」是同一族（OCR 变体）。
MONEY_CARD_NAMES = ("额外零用钱", "超前消费", "超可消费")


def is_money_card(name):
    """这张卡/商品是不是「增加零用钱」的（不涨六维）。

    **只认卡名**：效果文本「获得N零用钱」也会出现在「训练增强++」这类卡的
    附带说明里（2026-11-13 实跑：`额外零用钱+` 那张卡的说明写着
    「获得110零用钱」，但同一张卡名是别的）——按效果判会误伤。
    """
    name = name or ""
    return any(k in name for k in MONEY_CARD_NAMES)


# ---------- 随机效果商品/卡（2026-11-14 用户口径） ----------
#
# 「偶然饮料」的效果是**随机一个属性+N** —— 加哪个属性不确定，
# 期望值只有 N/6，比确定属性的 N 差得多，而且可能加到不需要的属性上。
# 「专属饮料」（两个推荐属性+N）是**确定的**（队长的两个推荐属性），
# 不算随机，保持原有档位。
#
# 商店（`places/shop.py`）和扭蛋（`places/street.py`）共用这一份判据。
RANDOM_EFFECT_NAMES = ("偶然", "随机")


def is_random_effect(name):
    """这个商品/卡的效果是不是随机的（期望值打折）？

    只认**名字**里的「偶然 / 随机」——效果文本会混在别的说明里，
    按效果判容易误伤。
    """
    name = name or ""
    return any(k in name for k in RANDOM_EFFECT_NAMES)


# ---------- 流派专属卡（2026-11-14 用户口径） ----------
#
# 有些卡的效果**只在特定流派下才兑现**，买了用不上就是白花：
#   小游戏强化+ → 「本局训练中，所有小游戏都可以额外获得30点体能」
#                  只有小游戏流去游戏厅才用得到；扭蛋流/商店流买了纯浪费。
#   器材暴击++  → 「接下来的训练中，器材的暴击加成提高20%」
#                  器材暴击只有训练流吃得到（收益率不同），别的流派买了没收益。
#
# 判据优先级：**先看卡名**（卡名往往直接写明归属），
# 认不出才看效果文本里的场景词。
# 顺序 = 归属的确定度，从高到低。
STYLE_CARD_NAMES = {
    # 卡名直接写明是哪个流派的装备。
    # 注意「升级包」**不能**算训练流：「体能升级包++」是属性卡（属性+训练等级），
    # 所有流派都吃得到（2026-11-14 回归实测）。
    "小游戏": "小游戏流",
    "游戏强化": "小游戏流",
    "暴击": "训练流",          # 器材暴击 → 只有训练流吃得到
    "训练增强": "训练流",
    "熟练度": "训练流",
    "教科书": "训练流",
}
# 效果文本里的场景词（卡名认不出时用）。
# 注意「训练中」不能单独当训练流标志 —— 「接下来的训练中，器材的…」
# 说的是器材（训练流），但「本局训练中，所有小游戏…」说的是小游戏。
STYLE_ONLY_EFFECTS = {
    "小游戏流": ("所有小游戏", "小游戏都可以"),
    "扭蛋流": ("所有扭蛋",),
}


def card_style_target(name, effect):
    """这张卡属于哪个流派？返回流派名，认不出返回 None。

    顺序（确定度从高到低）：
      1. **卡名**里的专属词（小游戏强化+ / 器材暴击++ / 训练增强++ …）；
      2. **效果文本**里的场景词（所有小游戏 / 所有扭蛋）。

    2026-11-14 用户口径：优先判定流派，**符合的优先购买，不符合则跳过**。
    """
    name = name or ""
    for word, style in STYLE_CARD_NAMES.items():
        if word in name:
            return style
    text = (effect or "").replace(" ", "")
    if not text:
        return None
    for style, words in STYLE_ONLY_EFFECTS.items():
        if any(w in text for w in words):
            return style
    return None


def is_other_style_card(name, effect, style):
    """这张卡是不是「别的流派专属」？是 → 本流派跳过（不买）。

    2026-11-14 用户口径：扭蛋流要排除小游戏卡（反之同理）。
    """
    only = card_style_target(name, effect)
    if only is None:
        return False
    return only != (style or "")


def subject_priority(score, value=None, cap=None, y=0,
                     soft_cap=DEFAULT_SOFT_CAP, gap_limit=DEFAULT_GAP_LIMIT):
    """科目排序键（越大越优先）；返回 None = 排除。

      - 缺口（上限−当前值）< `gap_limit` → 排除：接近上限，继续练会溢出；
      - 当前值 ≥ `soft_cap` → score × 0.5：超过门槛收益减半；
      - 其余按 score → 当前值低 → 屏幕靠上。

    上限或当前值读不到时，对应的判断跳过（读不到就不拦）。
    """
    if cap is not None and value is not None and cap - value < gap_limit:
        return None
    if value is not None and value >= soft_cap:
        score *= 0.5
    v = value if value is not None else UNKNOWN_VALUE
    return (score, -v, -y)
