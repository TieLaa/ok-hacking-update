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
    """商品/卡片名加哪一项：`ALL` = 全属性，属性名 = 专项，None = 认不出。

    用户 2026-11-13：属性类内部顺序 **全属性 > 需求专项 > 其他专项**。
    「专属 / 偶然」这类随机或不限定的，认不出具体属性 → 归「其他专项」。
    """
    name = name or ""
    if "全能" in name or "全属性" in name:
        return "ALL"
    for attr in SUBJECTS:
        if attr in name:
            return attr
    return None


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
