"""步行街（扭蛋）：**地点模块**。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：

    每个地点有自己的模块，只管自己的判断：
    **内页判据**（这是不是我的页面）+ **买什么/做什么** + **自己的状态**。

这个模块提供三样东西，handler 只负责「照它说的做」：

    1. `street_page(texts)`  —— 这是不是步行街扭蛋页？
    2. `choose_gacha_card(...)` —— 三张卡里买哪张？（纯函数，不点击）
    3. `street_state()` —— 我自己的状态（试过没有 / 买成了没有）

**它不需要知道别的地点**。「现在在餐厅但今天本来该进步行街」
这种问题由**地图模块**回答（说明是钱不够或没有合适的扭蛋）。

品质优先级（用户 2026-10-26，2026-11-01 更新）：

    1. 品质优先级 **彩色 > 金色 > 紫色**（彩色是训练等级提升后解锁的最高品质）；
       蓝色、其他一律不买（宁可退出）；
    2. 同等情况下**先「增强款」，再有队友加成的，然后才是恢复精力的**
       （都相同才比价格）。2026-11-03 前恢复量是直接加进分数的，会压过
       「增强款 / 队友」这两个用户明确要求的档位，见下面的事故记录；
    3. 「消耗/清空精力」的卡：只有精力低于阈值（默认 30）才买，
       **品质也一样要在白名单里**（蓝/其他照样不买，见 2026-11-04 事故）；
    4. 恢复精力的卡**三类资格门槛**（2026-11-08 用户口径，`recover_rejected`）：
       ① 立刻恢复xx → 当前+xx 超上限拒买（溢出）；② 全部恢复 → 缺口 >100 才买；
       ③ 接下来几日恢复xx → 当前精力不到上限一半才买。读不到精力不拦。
       另：恢复精力的商品/扭蛋/小游戏在**同等条件**下优先级最低
       （2026-11-07 口径，用户原话「同等条件下，恢复精力的商品/扭蛋/小游戏
       优先级最低」）——比较键里恢复价值排最后就是这个意思；
    5. 没有合格卡就退出，不点确定。

历史事故（都写在这里，避免又被改坏）：

    - 2026-10-26：旧版只按 rank*10 打分，蓝卡也有 10 分，结果 72 精力买了
      「精力转换-意识（消耗所有精力）」，精力归 0，周日只能划船回 30。
      现在清空卡在精力够时必须排除。
    - 2026-11-01：彩色卡（`体能增加+++`，卡头是彩虹渐变）被 `card_quality_at`
      按中位色相判成「紫」（彩虹带的中位色相正好落在紫区），于是金卡 300 压过
      它 200 → 又买了金卡（用户：「中间的是彩色，你已经点错了一次了」）。
      现在 `card_band_quality` 先认彩虹（彩），再退回单色判据。
    - 2026-11-01 15:09：卡面文字先渲染、价格药丸后渲染，中间那一拍读不到卡，
      旧代码当成「页面是空的」→ 退出 → 白丢一次扭蛋。现在读卡有兜底定位、
      价格未出不算「稳定」、空结果等 4 拍。
    - 2026-11-03 10:11：第2周星期五（54/130、398 币）三张紫/蓝卡，
      右边「意识增加+」**卡头有队友标记**，却买了中间「精力恢复+」。
      日志原文 `精力恢复+(紫·增强·恢复110/70币/138)、意识增加+(紫+队友·增强/70币/100)`：
      识别全对，错在**恢复量直接进了 score**（138 > 100），
      队友只在「分数相同」时才当 tie-break，永远轮不到。
      现在恢复量从 score 里拿出来，排到「增强款 → 队友」之后。
    - 2026-11-04 23:56：精力 23（低于清空阈值 30）时买了**蓝卡**
      「精力转换-体能（消耗所有精力）」，精力直接清到 0（用户：「怎么买了一个
      蓝色的，不是不买吗」）。根因：品质白名单写成了 `elif`，只挂在非清空卡上，
      清空卡绕过了白名单。现在白名单对清空卡同样生效。
"""

from src.tasks.ui import labels
from src.tasks.training import attributes

    # 地点名（地图上的按钮文字 / 状态里的键）
PLACE = "步行街"


class StreetPlace:
    """步行街的判断与状态。**不点击**，只给判断。"""

    # 退出扭蛋页的兜底坐标（右上角 ↩）。
    # 2026-10-27 实测（logs/debug/1027-000428_nochange3）：第一次进店的
    # 「请选择1个扭蛋」页底部只有「能力详情 / 确定」，**没有「放弃」**，
    # 旧兜底坐标 (0.346w, 0.896h) 正好落在两个按钮中间的空白，
    # 连续 9 拍点不动 → 任务被判「连续 3 轮点不动」停止。
    # 右上角 ↩ (0.957w, 0.047h) 实测有效，退回周末地图页。
    #
    # 坐标放在**地点模块**里：通用层不认识这一页长什么样。
    EXIT_COORDS = (0.957, 0.047)

    # ---------- 页面判据 ----------

    def street_page(self, texts):
        """这是不是步行街扭蛋页？返回 True/False。

        2026-10-31 实跑（野上泷额外周末，第3周星期五）：**周末地图**上步行街
        那条奖励提示这次写的是「扭蛋增强」（平时是「零用钱+」），`STREET_TITLE`
        里的「扭蛋」用包含匹配把地图判成了扭蛋页 → 三张卡当然读不出来 →
        退出 → 又回到训练日主界面，当天的外出白跑（用户：「进了出行地图然后
        就退回来了，又点了训练」）。

        2026-11-14 修正：标题改成扭蛋页**专有**的「请选择1个扭蛋 / 超级扭蛋机」，
        不再认「扭蛋」这种泛词 —— 「扭蛋加成」技能状态说明页（11:37:52 实跑）
        会被误判成扭蛋页，在那页上找三张卡当然找不到，白丢一次扭蛋。
        状态说明/结算页的字（「扭蛋加成」「获得了…」「购买了扭蛋…」）一律排除。

        地图上有 ≥2 个地点名，扭蛋页只有一个，用它区分。
        """
        if self.match_text(texts, labels.STREET_TITLE) is None:
            return False
        for t in texts:
            name = t.name.strip()
            if any(w in name for w in labels.STREET_TITLE_EXCLUDE):
                return False
        places = [t for t in texts
                  if t.name.strip() in labels.WEEKEND_PLACES]
        return len(places) < 2

    def street_enabled(self):
        """配置开关「允许进步行街」开了吗？"""
        return bool(self.config.get("允许进步行街"))

    # ---------- 买哪张卡（纯判断，不点击） ----------

    def gacha_candidates(self, cards, stamina, stamina_max, money,
                         required=None, attrs=None, caps=None, gap_limit=100):
        """三张卡里哪些值得买、买哪张。

        返回 (best, detail, all_cards)：
        - `best` 是要买的那张（None = 没有可买的）
        - `detail` 是给人看的候选清单（日志用）
        - `all_cards` 是全部三张（不管买不买）

        参数里 `stamina` / `money` 可能是 None（读不到）——
        读不到零用钱或价格时**不买**（买不起点「确定」游戏不扣钱、页面也不关，
        旧代码会一直「选卡 → 确定 → 没反应」，2026-10-26 实测）。
        """
        clear_limit = int(self.config.get("扭蛋清空精力阈值"))
        allow_clear = stamina is not None and stamina < clear_limit
        required = set(required or ())
        attrs = attrs or {}
        caps = caps or {}

        # 品质优先级（配置项「扭蛋品质优先」，默认 彩色 > 金色 > 紫色）：
        # 列表 = 从高到低的品质名，分数按位置给（第一位 400、第二位 300……），
        # 间隔远大于同品质内的任何差别，保证「品质永远是第一权重」；
        # 不在列表里的品质（蓝/其他）直接不当候选。
        order = list(self.config.get("扭蛋品质优先") or ["彩", "金", "紫"])
        base_of = {q: (len(order) - i) * 100 for i, q in enumerate(order)}

        def score(c):
            """品质分：只由配置顺序决定（品质永远是第一权重）。

            名字里的「+」**不进分**（用户 2026-11-02）：「+」只代表**同名卡片**
            之间的档位差，没「+」的同名卡本来就是低一级（紫/蓝）—— 那已经由
            `quality` 体现，再算一次是重复计分。
            2026-11-02 01:07:59 实跑就是这么买错的：
              `随机加成(金·+队友/90币/200)` 输给 `体能增加++(金/90币/201)`，
              中间那张带队友的卡被这 +1 分压掉（用户当场指出）。
            """
            return base_of.get(c["quality"], 0)

        def recover_bonus(c):
            """恢复卡之间的比较值：按「这次真能补上的精力」折半。

            2026-11-08 用户口径：恢复精力的卡在**同等条件**下优先级最低
            （见下面 max 的比较键），这个加分**只在多张恢复卡之间**起作用
            ——属性卡永远排在恢复卡前面，轮不到它。
            历史口径（已被取代，留档防回改）：2026-10-27「恢复量必须参与比较」、
            2026-11-03「排在增强款→队友之后」，都是「恢复卡同分要赢」；
            2026-11-07 用户改口「同等条件下恢复精力优先级最低」。
            """
            if not c.get("recover") or stamina is None:
                return 0
            room = max(stamina_max - stamina, 0)
            return int(min(c["recover"], room) * 0.5)

        def recover_rejected(c):
            """恢复卡的三类资格门槛（用户 2026-11-08 口径），过不了 → 不买。

            读不到精力/上限时**不拦**（顶栏 OCR 抖一帧不能让扭蛋整拍罢工，
            同「第 7 周周数读不到不过滤」的既有口径）。
            混合卡（立刻60 + 每天20×4天）：立刻量只数「立刻恢复60精力」那段，
            持续部分不占这次的口子（`stamina_recover_immediate` 已摘掉持续段）。
            """
            if not self.is_stamina_recover_card(c.get("effect")):
                return None
            if stamina is None or stamina_max is None:
                return None                      # 读不到 → 不拦，按老规则买
            room = max(stamina_max - stamina, 0)
            imm = c.get("recover_immediate") or 0
            if c.get("full_recover"):
                # ② 全部恢复（拉满）：缺口 >100 才值得花一张卡的钱
                if room <= 100:
                    return f"缺口 {room} ≤ 100"
                return None
            if imm:
                # ① 立刻恢复xx：当前 + 立刻量 > 上限 = 溢出，回不上的白花钱
                if stamina + imm > stamina_max:
                    return f"立刻 {imm} 会溢出（{stamina}+{imm}>{stamina_max}）"
                return None
            # ③ 接下来几日恢复xx（没有立刻量）：精力还剩一半以上时不缺这几天的
            if stamina_max > 0 and stamina / stamina_max >= 0.5:
                return f"精力 {stamina}/{stamina_max} ≥ 一半"
            return None

        ok = []
        for c in cards:
            # 品质白名单对**所有**卡生效，清空卡也不例外（用户 2026-11-04：
            # 「怎么买了一个蓝色的，不是不买吗」）。
            # 2026-11-04 23:56 实跑就是这么买错的：这两条以前写成 `if clear / elif
            # quality not in base_of` —— 白名单只挂在**非清空卡**上，于是
            # 「精力转换-体能(蓝+队友·清空/50币)」绕过了白名单；精力 23 < 阈值 30
            # → 买下蓝卡把精力清到 0，当天剩下的训练全废
            # （日志：`体能增加(蓝/50币/0)、精力转换-体能(蓝+队友·清空/50币/0)、
            #   全属性增加(蓝/50币/0) → 选「精力转换-体能」`）。
            if c["quality"] not in base_of:
                continue
            # 清空卡额外一道门槛：只有精力低于阈值时才买（这时是拿精力换属性）
            if c["clear"] and not allow_clear:
                continue
            # 恢复卡三类资格门槛（2026-11-08 用户口径）：立刻溢出 / 全部恢复缺口
            # ≤100 / 持续型精力过半，都不买（拒绝理由进日志，方便复盘调阈值）。
            why = recover_rejected(c)
            if why:
                c["reject"] = why
                continue
            # 流派专属卡（2026-11-14 用户口径）：**先判定流派**——
            # 符合本流派的**优先买**，不符的**跳过**。
            card_style = attributes.card_style_target(c.get("name"), c.get("effect"))
            if card_style is not None and card_style != self.style:
                c["reject"] = f"{card_style}流专属"
                continue
            # 属性取向（2026-11-13）：该属性缺口不足阈值 → 不买（加了会溢出）。
            target = attributes.item_attr_target(c.get("name"))
            if target is None:
                # 卡名认不出六维属性名时，用**效果文本**兜底（2026-11-14）：
                # 「延迟满足」的效果是「5天后，全队成员获得全属性+13」，
                # 实际是全属性卡（78 点），卡名认不出 → 归「其他专项」就输了。
                target = attributes.effect_attr_target(c.get("effect"))
            random_effect = attributes.is_random_effect(c.get("name"))
            if target and target not in ("ALL", "PREFERRED", "CASH"):
                cap, cur = caps.get(target), attrs.get(target)
                if (cap is not None and cur is not None
                        and cap - cur < gap_limit):
                    c["reject"] = "缺口不足"
                    continue
            # 读不到价格就不买：买不起时点「确定」游戏不扣钱、页面也不关，
            # 旧代码会一直「选卡 → 确定 → 没反应」（2026-10-26 实测）。
            # 2026-11-01 15:09：卡面先渲染、价格后渲染的那一拍价格是 None，
            # 这时宁可退出等下一轮，也不能按「猜的价格」买。
            if c["price"] is None:
                continue
            # 买不起就跳过，看下一张（2026-10-26：零用钱 50、卡片 60，
            # 旧代码照样点确定 → 停留在扭蛋页反复点）。
            if money is not None and c["price"] > money:
                continue
            c["score"] = score(c)
            # 属性子档（参考商店 `shop.pick_shop_item` 的 attr_sub）：
            # 全属性 > 需求专项 > 推荐属性 > 其他专项 > **随机效果**。
            # 「推荐属性」不再落进「其他」，否则同品质同价时会被卡片顺序
            # 决定胜负（2026-11-13 实跑：金弹跳升级包赢了金推荐属性++）。
            # 2026-11-14：随机效果（偶然饮料 = 随机一个属性+N）降到最后一档，
            # 期望值只有 N/6，还可能加到不需要的属性上。
            c["attr_rank"] = (4 if random_effect
                              else 0 if target == "ALL"
                              else 1 if target in required
                              else 2 if target == "PREFERRED" else 3)
            # 用户 2026-11-14：**增加零用钱的卡最高优先**（商店流/扭蛋流/小游戏流
            # 都要零用钱买牛奶/秘籍）。它排在**品质之前** ——
            # 2026-11-14 实跑「器材暴击++(金) vs 额外零用钱+(紫)」选了金卡，
            # 零用钱才是这些流派的瓶颈。
            # **训练流例外**：训练流不消费零用钱，零用钱卡没有价值。
            money_first = self.style != "训练流"
            c["money_rank"] = 1 if (money_first and target == "CASH") else 0
            # 2026-11-14：**符合本流派的专属卡排最前**（流派优先，品质之前）。
            # 例：训练流里「器材暴击++」比同品质的属性卡更该买（收益率不同）。
            c["style_rank"] = 1 if (card_style is not None
                                    and card_style == self.style) else 0
            ok.append(c)

        detail = "、".join(
            f"{c['name']}({c['quality']}{'+队友' if c['mate'] else ''}"
            f"{'·增强' if c.get('enhanced') else ''}"
            f"{'·恢复' + str(c['recover']) if c.get('recover') else ''}"
            f"{'·清空' if c['clear'] else ''}"
            f"{'·' + c['reject'] if c.get('reject') else ''}"
            f"{'/' + str(c['price']) + '币' if c['price'] is not None else '/价格未出'}"
            f"/{score(c)})" for c in cards)
        if not ok:
            return None, detail, cards
        # 取舍顺序（用户 2026-11-07 口径：**同等条件下恢复精力的优先级最低**，
        # 取代 2026-10-27/11-03 的「恢复卡同分要赢」；都相同再比价格）：
        # **流派专属卡** → **零用钱卡** → 品质 → 属性档 → 增强款 → 队友 →
        # **非恢复优先** → 恢复价值 → 便宜的。
        # 非恢复卡恒为 1、恢复卡恒为 0，恢复卡只有池里全是恢复卡时才轮得到。
        # 2026-11-08 修：判据从 `recover > 0` 改成按效果文本
        # （`is_stamina_recover_card`）——「精力补充」这类**全部恢复卡 recover=0**，
        # 旧判据把它当成非恢复卡，反而排在所有恢复卡前面（同类 bug 的前提已消失）。
        #
        # 2026-11-14：流派专属卡（符合本流派）**在同品质里优先** ——
        # 训练流的「器材暴击++」只有训练流吃得到（收益率不同），
        # 同品质时比普通属性卡更该买；不符合流派的已在闸门那一步跳过。
        # 注意**不跨品质**：训练增强（紫，+3 点）不该压过运控增加+++（金，+100）。
        best = max(ok, key=lambda c: (
            c.get("money_rank", 0), c["score"], c.get("style_rank", 0),
            -c.get("attr_rank", 2),
            bool(c.get("enhanced")), c["mate"],
            not self.is_stamina_recover_card(c.get("effect")),
            recover_bonus(c), -c["price"]))
        return best, detail, cards

    # ---------- 地图侧：值不值得来 ----------

    def has_boost_label(self, texts, label_box):
        """步行街名字框的**左上方**有没有「扭蛋增强！」标签？

        2026-11-01 修：原来只 `match_text(texts, ("扭蛋增强","扭蛋加强"))` ——
        全屏出现这四个字就生效，不看它贴在哪。于是别处只要出现同样的字
        （比如队友台词、别的界面的说明文字），就会误判成「步行街有加成」
        → 不管钱和队友直进步行街。

        现在连**位置**一起判（和 `venue_on_discount` 同一套路）：
        标签必须贴着步行街名字的左上方（Δcx ≤ 0.20、Δcy ≤ 0.12、且在左边）。
        实测 10 张真实帧的 Δcx=0.105~0.107 / Δcy=0.048~0.049，阈值留了 2 倍余量。
        """
        if label_box is None:
            return False
        cx = label_box.x + label_box.width / 2
        cy = label_box.y + label_box.height / 2
        for t in texts or []:
            name = t.name.strip()
            if not any(k in name for k in labels.STREET_BOOST_TEXTS):
                continue
            tcx = t.x + t.width / 2
            tcy = t.y + t.height / 2
            if tcx >= cx:                      # 必须在名字左边
                continue
            if abs(tcx - cx) / self.width > self.STREET_BOOST_DX:
                continue
            if abs(tcy - cy) / self.height > self.STREET_BOOST_DY:
                continue
            return True
        return False

    # 「扭蛋增强！」标签与「步行街」名字框的相对位置阈值（比例）。
    # 实测 10 张真实地图帧（1549x871）：标签 cx=0.457 cy=0.809、
    # 步行街 cx=0.564 cy=0.858 → Δcx=0.105~0.107、Δcy=0.048~0.049。
    STREET_BOOST_DX = 0.20
    STREET_BOOST_DY = 0.12

    # ---------- 我自己的状态 ----------

    def street_state(self):
        """步行街自己的状态（试过没有 / 买成了没有）。

        三个状态分开（2026-11-01 15:09 事故的教训）：
        - `visited`     今天**进过**步行街（进过就算来过，防止反复进）
        - `gacha_tried` 今天**试过**抽扭蛋（买没买成都算）
        - `gacha_spent` 今天**买成了**

        地图上按 `gacha_tried` 排除步行街（试过了就不再试），
        按 `gacha_spent` 决定还要不要去餐厅打工。

        为什么不干脆只记一个「进过」：15:09 那次没买到，如果完全不登记，
        回到地图 todo 里还留着步行街 → 又点进去 → 又失败 → **无限循环**。
        """
        return self.day_state()

    def mark_tried(self):
        """今天试过抽扭蛋了（进过页面就算）。"""
        self.street_state().setdefault("gacha_tried", set()).add(PLACE)

    def mark_spent(self):
        """今天买成了（点「确定」成功才算）。"""
        self.street_state()["gacha_spent"] = True
