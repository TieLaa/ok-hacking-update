"""纯打分函数，输入结构化数据，不碰 self 之外的副作用。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""

import re


class TrainingScoringMixin:

    # 科目预览打分（2026-10-29 定稿：彩框抬到 2.3）
    # 队友 1 / 装备 1 / 经理 2.1 / 金框 1.2 / 彩框 2.3
    #
    # 为什么 金框 1.2 < 经理 2.1 < 彩框 2.3：
    #   - 金框/彩框/经理是**同一个机制**：装备反复用 → 累积经验 → 满经验出现金边/彩边 → 暴击；
    #     经理反复一起练 → 累积经验 → 触发「经理的帮助」。
    #   - 彩框 > 经理（2026-10-29 用户口径「彩框要多加一档 2.3」）：彩框是真能吃到的暴击，
    #     用户明确要求「有彩框就该练」；金框只是「快满了」的预告，维持 1.2 不动。
    #   - 2026-10-27 实跑复核：暴击只比同周普通训练多 +23 左右（14 个样本）；
    #     2.3 是用户拍板的主观档位，不是拟合出来的数字。
    # 关键口径：**金框/彩框指训练科目按钮外面那一圈**，不是底部预览区的器材卡。
    # 按卡上的金框算会和「装备」重复计分（框本来就是装备/经理经验满了才点亮），
    # 所以装备只按张数算一次，框只看按钮外圈。
    W_MATE = 1.0
    W_EQUIP = 1.0
    W_MANAGER = 2.1
    W_GOLD = 1.2
    # 2026-10-29 用户口径：彩框再抬一档 = 2.3（压过经理 2.1）。
    # 实跑依据：16:45「意识」金框 1 得分 2.20，输给「防守」经理+2装备 4.10，
    # 用户明确要求「有彩框就该练」；彩框是真暴击，金框只是预告，所以只抬彩框。
    W_COLOR = 2.3

    @classmethod
    def empty_preview(cls):
        return {"names": [], "mates": 0, "manager": 0, "equip": 0, "full": 0,
                "gold": 0, "crit": False, "color": 0, "manager_crit": 0}

    @classmethod
    def subject_score(cls, r):
        """按权重算总分（全部是正分，直接排序）。"""
        if not r:
            return 0.0
        return (r["mates"] * cls.W_MATE
                + r["equip"] * cls.W_EQUIP
                + r["manager"] * cls.W_MANAGER
                + r["gold"] * cls.W_GOLD
                + r["color"] * cls.W_COLOR)

    @staticmethod
    def subject_rank(score, attr_value, y):
        """科目排序键（2026-10-27 用户口径）。

        分数 → 当前数值**低**的（成长空间大）→ 屏幕上靠前的（兜底）。
        **推荐属性（绿色大拇指）已取消**：用户实测训练完成后的总评分不受优势属性
        影响，练哪条都等价，所以它不再参与排序。
        读不到数值的属性用 10^6 垫底，不能靠「读不到」白捡便宜。
        """
        value = attr_value if attr_value is not None else 10 ** 6
        return (score, -value, -y)

    # ---------- 品质卡排序（2026-11-03） ----------
    # 用户口径：**彩 > 金 > 紫 > 蓝**；同品质比**队友头像数量**（多的优先、
    # 有队友优先于没队友）；都一样取**靠右**那张（做饭页右边就是更贵的食材，
    # 餐厅/技能这类页面也是「右边优先」的旧兜底口径，不引入第二种习惯）。
    # 判据（哪张卡是什么颜色）在 `parsers.quality_cards` —— 这里只做排序，
    # 不碰帧、不点击，可以脱离游戏跑（见 tests/test_training_rules.py）。
    QUALITY_RANK = {"彩": 4, "金": 3, "紫": 2, "蓝": 1, "其他": 0}

    @classmethod
    def quality_card_order(cls, cards):
        """按 品质 → 队友数 → 靠右 排序，返回 (选中项, 全表)。

        `cards` 是 `parsers.quality_cards` 的输出（每项含 box/quality/mates）。
        """
        ordered = sorted(cards, key=lambda c: (-cls.QUALITY_RANK.get(c["quality"], 0),
                                               -c["mates"], -c["box"].x))
        return ordered[0], ordered

    # 六维属性名：用来把「全属性+5」换算成真实的属性总量
    ATTR_NAMES = ("投射", "弹跳", "运控", "体能", "防守", "意识")

    # 精力**不参与属性分**（2026-11-03 口径修正）。
    # 之前 `STAMINA_WEIGHT = 0.2` 把精力折算成属性点加进同一个 ev：
    #   「属性+3，精力+30」= 3 + 6 = 9 分，会压过「属性+8」= 8 分 ——
    #   满精力时也一样，等于「回精力不如一点属性」这条用户口径没生效。
    # 扭蛋那边同源的毛病（恢复量 38 分压掉「有队友的卡」）已经证明：
    # **两个不同量纲的东西不能加进同一个分数**，只能在属性打平后按档位比。
    # 现在精力由 `stamina_gain` 单独算，作为 `_pick_choice` 的次级比较。

    @classmethod
    def stamina_gain(cls, items):
        """这组效果文本一共给多少点精力（原样点数，不折算、不封顶）。

        只认「精力+N / 体力+N」（「精力提升+30」也命中）；「消耗/清空精力」的
        选项返回 0 —— 那是花钱换属性，不是收益。
        能不能真用上（当前精力离上限还有多少）由调用方封顶，
        见 `_pick_choice` 里的 `sta`（调用方已经按余量算好）。
        """
        joined = "".join(t.name for t in items).replace(" ", "")
        if "消耗" in joined or "清空" in joined:
            return 0
        total = 0
        for m in re.finditer(r"(?:精力|体力)([^+\d]{0,6})\+(\d{1,3})", joined):
            if "上限" in m.group(1):
                # 「精力上限+10」是永久上限，不是这次能用的精力
                continue
            total += int(m.group(2))
        return total

    @classmethod
    def effect_value(cls, items):
        """一组「效果文本」→ (ev, flat)。**事件选项和加强训练共用这一份口径**。

        - `flat=True`：纯回精力（出现「精力」（或别名「体力」），但没有属性名、也没有「等级/属性」）
          —— 调用方一律不选它（用户口径：回精力不如一点属性）；
        - `ev`：**只算六维属性**的总量（`_effect_ev`：全属性×6、概率相乘），
          精力不进这个数（见 `stamina_gain`）；
          读不到数字但写着「等级/属性」时按 100 计（训练等级提升这类文字效果）。
        """
        joined = "".join(t.name for t in items)
        if not joined:
            return None, False
        attrs = any(a in joined for a in cls.ATTR_NAMES)
        # 只认「等级提升」这类；「属性+10」是普通属性收益，交给 _effect_ev
        level = "等级" in joined
        # 「体力」是游戏旧文案的别名，和「精力」同等看待（不是我们的术语）
        flat = (("精力" in joined or "体力" in joined) and not attrs and not level)
        ev = cls._effect_ev(items)
        if level:
            # 「等级/属性提升」按 100 计（比任何单条属性都值钱）；
            # 同一张卡还写着精力+30 时也取 100，不取 0。
            ev = 100.0 if ev is None else max(ev, 100.0)
        if ev is None and flat:
            ev = 0.0
        return ev, flat

    @classmethod
    def _effect_ev(cls, effect_texts):
        """从效果文本里算**真实属性总量**（用户 2026-10-27 口径）。

        关键：数字后面跟的说明决定倍率——「全属性+5」是六项各 +5 = **30 点**，
        「投射+3」才是 3 点。旧实现只看数字大小，会出现
        「全属性+5(30)」输给「精力+10(实际不值)」这种明显错误。

        规则：
        - 前缀含「全属性 / 所有属性 / 全体」→ ×6；
        - 前缀里有 n 个属性名（「投射+2，弹跳+3」）→ 分别按 1 倍累加；
        - **精力/体力不进这个数**（2026-11-03）：它和属性不是同一量纲，
          单独由 `stamina_gain` 算，只在属性打平后当次级比较；
        - 带概率的（「50%概率 +5」）按 收益×概率 计，并与确定收益取较大值。
        """
        joined = "".join(t.name for t in effect_texts).replace(" ", "")
        certain = 0.0
        best_prob = 0.0
        for m in re.finditer(r"(?:(\d{1,3})%概率)?[^+\d]{0,8}\+(\d{1,3})", joined):
            p = int(m.group(1)) if m.group(1) else 100
            v = int(m.group(2))
            # 说明文字在「+N」前面，就在这次匹配里（不能看 m.start() 之前，
            # 那个位置是空字符串，「全属性+5」会算成 5 而不是 30）
            prefix = m.group(0).split("+")[0]
            if ("全属性" in prefix or "所有属性" in prefix or "全体" in prefix
                    or "六维" in prefix):
                mult = len(cls.ATTR_NAMES)
            else:
                hits = sum(1 for a in cls.ATTR_NAMES if a in prefix)
                if hits:
                    mult = hits
                elif "精力" in prefix or "体力" in prefix:
                    continue          # 精力不算属性分（见 stamina_gain）
                else:
                    mult = 1
            ev = v * mult
            if p >= 100:
                certain += ev
            else:
                best_prob = max(best_prob, ev * p / 100.0)
        if certain == 0.0 and best_prob == 0.0:
            return None
        return max(certain, best_prob)
