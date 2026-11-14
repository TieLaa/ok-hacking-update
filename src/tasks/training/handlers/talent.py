"""角色天赋触发的特殊事件（talent events），按天赋归口到一个 mixin。

这些事件**只有带了对应天赋的角色才会触发**，和周末地图（weekend.py）/
比赛（match.py）那种人人都有的流程不同，所以单独成模块。
天赋 → 角色对照表见模块级 `TALENT_OWNERS`（用户 2026-11-05 口径，只记了一部分）。

已实现的天赋事件（方法名不变，注册表 handlers/__init__.py 按方法引用）：

    handle_stock_pick / handle_stock_result   模拟炒股，天赋「股神!」
                                               （闪堂电 / 亚理纱贾丝敏）
    handle_fishing_ground                      钓鱼场三选一，天赋「钓鱼」
                                               （真崎辉世 / 佐久间雨乃）
    handle_theme_select / handle_manuscript_submit
                                               写小说题材三选一 + WARD文档交稿
                                               （望月透；2026-11-02 实机在他身上触发）
    handle_ingredient_select                   做饭食材三选一（千圣贝尔丝）
    handle_live_stream                         直播主题三选一，天赋「人气主播」
                                               （和泉香织；2026-11-07 实机触发 5 次）
    handle_izumi_question                      和泉的问题二选一（继续倾听/婉拒）
                                               （2026-11-07 实机触发 1 次，固定选第一个）
    handle_construction                        工地施工页「进行施工」，天赋「建设达人」
                                               （百目鬼剑；2026-11-13 实机触发 6 次）

钓鱼/做饭事件的**第二页转盘**（「转一转」）不在本模块：它是通用 UI 机制，
非天赋事件（如「球队经理的小帮助」）也触发同样的转盘，2026-11-05 起
归 `handlers/wheel.py: handle_spin_wheel`。

**还没触发过**的天赋（潜水/跑团/异世界碎片/演唱会/汉堡券/占卜/
龙宫寺集团）已在 `TALENT_OWNERS` 里占位：没有实机截图，写不了判据；触发后
按「实现一个新天赋事件」的步骤补——

    1. 先读 logs/debug/<时间>_<tag>/ocr.txt 确认页面文字和布局；
    2. `ui/labels.py` 登记标题判据（不要内联在 handler 里）；
    3. TalentHandlers 加 `handle_xxx`（docstring 写清口径来历和踩过的坑）；
    4. handlers/__init__.py 注册，注意和「跳过/确定」类通用 handler 的先后；
    5. docs/shots/ 存回归样例，跑 `python tools/check_handlers.py --cache`。
"""

import time

from src.tasks.ui import labels

# 做饭页同一页重复存证的间隔（秒）：点卡 → 点确定要 2~3 拍，别把一页刷成十几张图。
UNMARK_INTERVAL = 10.0

# 天赋 → 角色对照表（用户 2026-11-05 口径，只记了一部分）。
#   - 已实现的四个天赋排在前面；
#   - 「None」= 还没触发过，没有实机截图、写不了判据，只是**占位预留**；
#     触发后按模块 docstring 里「实现一个新天赋事件」的五步补，再把 None 换成实现。
#   - 角色名照用户原文（有的天赋对应两个角色）。
TALENT_OWNERS = {
    # ---- 已实现 ----
    "股神!":     ("闪堂电", "亚理纱贾丝敏"),        # 炒股：handle_stock_pick / handle_stock_result
    "钓鱼":      ("真崎辉世", "佐久间雨乃"),        # handle_fishing_ground
    "写小说":    ("望月透",),                       # handle_theme_select / handle_manuscript_submit
    "做饭":      ("千圣贝尔丝",),                   # handle_ingredient_select
    # ---- 未实现：其中「人气主播」已实现（handle_live_stream），其余占位 ----
    "潜水":      None,
    "跑团":      ("羽濑川凑", "近江奏"),
    "人气主播":  ("和泉香织",),        # = 直播三选一「请选择1个直播主题」，handle_live_stream
    "异世界碎片": ("羽田朱志香",),
    "演唱会":    ("远野桐",),
    "汉堡券":    ("澪奈贾妮丝",),
    "占卜":      ("卧云真昼",),        # 占卜时间「确定」，handle_divination（用户 2026-11-13 口径）
    "龙宫寺集团": ("龙宫寺心叶",),
    # ---- 2026-11-13 夜间实测补齐 ----
    "建设达人":  ("百目鬼剑",),        # 工地「进行施工」，handle_construction
}


class TalentHandlers:
    """角色天赋特殊事件的界面（mixin，方法通过 self 访问任务状态）。"""

    # ==================== 模拟炒股（天赋「股神!」：闪堂电 / 亚理纱贾丝敏） ====================

    STOCK_TITLE = labels.STOCK_PICK_TITLE
    STOCK_TYPES = ("稳健型", "平衡型", "风险型")

    def handle_stock_pick(self, texts):
        """天赋「股神!」的模拟炒股：在**买得起**的卡里选**队友最多**的那张，再点确定。

        用户 2026-10-29 真机截图：
        - 三张卡「稳健型/平衡型/风险型」，价格 24/120/200，买不起的价格显示红色；
        - 黄色角标 + 头顶台词挂在游戏推荐的那张（两次分别在第一张、第二张），
          所以「角标」不是可靠判据，可靠的是**价格 ≤ 当前零用钱**。
        - 当时口径：选第一个（最左）能买的那张。

        用户 2026-11-02 改口径（实拍 1102-234130_stock03：左 1 个队友、右 2 个）：
        **默认选队友多的那张**；队友一样多才按老规则（买得起→价格高的）。
        队友数 = 卡片头顶那排 chibi 头像的个数（`stock_teammate_counts` 数嘴），
        **不是气泡数** —— 气泡一张卡最多一句，2 个队友也只冒一句，数不出人数。
        """
        if self.match_text(texts, self.STOCK_TITLE) is None:
            return False
        self.dump_stock_frame(texts)
        # 这一页的金币在右上角（真机截图 cx≈0.80, cy≈0.11），不在训练日顶栏的
        # 0.50~0.62 区域，read_money 读不到 → 用页内的读法兜底。
        money = self.read_money(texts)
        if money is None:
            money = self.stock_money(texts)
        pairs = []
        seen = []
        for t in texts:
            # 三张卡的类型名**可能重复**（实拍 1102-234130：风险型 / 风险型 / 稳健型），
            # 旧写法「按名字各取一个」会漏掉中间那张 —— 按 x 收集、同列去重。
            # 名字用**包含**匹配（OCR 偶尔多带一个字符），只认这三种 3 字词。
            if not any(k in t.name for k in self.STOCK_TYPES):
                continue
            cx = t.x + t.width / 2
            if any(abs(cx - (b.x + b.width / 2)) < self.width * 0.05 for b in seen):
                continue
            seen.append(t)
        for card_box in seen:
            pairs.append((card_box, self.stock_card_price(texts, card_box)))
        if not pairs:
            self.log_warning("炒股：认不出三张卡片，点第一个类型附近兜底")
            self.click(int(self.width * 0.25), int(self.height * 0.55), after_sleep=0.6)
            return True
        pairs.sort(key=lambda pair: pair[0].x)
        mates = self.stock_teammate_counts([b for b, _p in pairs])
        if not any(mates):
            # 一个头像都没认出来（换了 UI / 分辨率怪）→ 退回气泡判据，别把这条规则废掉。
            mates = [self.stock_card_marks(texts, b) for b, _p in pairs]
        cards = [(b, p, m) for (b, p), m in zip(pairs, mates)]
        detail = "、".join(
            f"{b.name}={p}{'·队友' + str(m) if m else ''}" for b, p, m in cards)
        affordable = [c for c in cards if money is None or c[1] is None or c[1] <= money]
        if not affordable:
            self.log_warning(f"炒股：都没有买得起的（零用钱 {money}｜{detail}）→ 放弃",
                             notify=True)
            self.click_button("give_up", after_sleep=0.6, must=False)
            return True
        # 用户口径：买得起的里**队友多**优先，同档价格高的优先。
        pick, price, marks = max(
            affordable, key=lambda x: (x[2], x[1] if x[1] is not None else -1))
        badge = f"·队友{marks}" if marks else ""
        cx = pick.x + pick.width / 2
        cy = pick.y + self.height * 0.18
        self.click(int(cx), int(cy), after_sleep=0.6)
        self.log_info(f"炒股：零用钱 {money}｜{detail} → 选「{pick.name}」({price}){badge}")
        if self.click_button("confirm", after_sleep=0.8, must=False):
            self.log_info("炒股：确定")
        return True

    def dump_stock_frame(self, texts):
        """炒股页限流存帧（每局最多 6 张）：用来量「头顶队友头像有几个」。

        存的是**本拍识别用的那一帧**（`_tick_frame`），和同目录的 `.txt` 是同一屏 ——
        读 `self.frame` 会拿到点击之后的新帧，图上的头像和 OCR 框对不上。
        """
        if getattr(self, "_stock_dumps", 0) >= 6:
            return
        frame = getattr(self, "_tick_frame", None)
        if frame is None:
            frame = self.frame
        if frame is None or not frame.any():
            return
        idx = getattr(self, "_stock_dumps", 0)
        if self.evidence.dump_frame("stock", texts, frame, idx=idx):
            self._stock_dumps = idx + 1

    def stock_card_marks(self, texts, label_box):
        """卡片头顶的台词气泡（「相信奇迹!」「一定能赚!」「我梭哈了」）= 有队友推荐。

        气泡是**短文本 + 感叹号**，按 x 关联到最近的卡片（±0.12 屏宽）。
        OCR 偶尔把「!」吃掉，那就当这张没有标记。

        2026-11-02 起它只是**兜底**：气泡一张卡最多一句，2 个队友也只冒一句，
        数不出人数。真正的「队友几个」由 `stock_teammate_counts`（数头像的嘴）给，
        只有在头像一个都没认出来时才退回这里。
        """
        if label_box is None:
            return 0
        cx_card = (label_box.x + label_box.width / 2) / self.width
        n = 0
        for t in texts:
            s = t.name.strip()
            if not s.endswith(("！", "!")) or len(s) > 12:
                continue
            cx = (t.x + t.width / 2) / self.width
            if abs(cx - cx_card) <= 0.12:
                n += 1
        return n

    def stock_money(self, texts):
        """炒股页右上角的金币数字（裸数字，没有斜杠）。"""
        for t in texts:
            v = t.name.strip()
            if not v.isdigit() or len(v) > 5:
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if 0.70 <= cx <= 0.90 and 0.05 <= cy <= 0.18:
                return int(v)
        return None

    def stock_card_price(self, texts, label_box):
        """卡片价格：类型名下方、同一列的那个数字（买不起时是红色的，OCR 一样能读）。"""
        best = None
        for t in texts:
            v = t.name.strip()
            if not v.isdigit() or len(v) > 4:
                continue
            cx = t.x + t.width / 2
            if abs(cx - (label_box.x + label_box.width / 2)) > self.width * 0.16:
                continue
            if not (label_box.y < t.y < label_box.y + self.height * 0.35):
                continue
            if best is None or t.y > best[0]:
                best = (t.y, int(v))
        return best[1] if best else None

    def handle_stock_result(self, texts):
        """炒股的两个结算页（2026-10-29 用户截图）。

        1. **股票走势**（有涨跌幅）：这一页只能点「卖出」，卖出后回到选股页再买；
        2. **炒股队收益排名**（全部炒股日结束后的总结）：点「下一步」继续。
        """
        if self.match_text(texts, ("股票走势", "涨跌幅")) is not None:
            if self.click_text(("卖出",), after_sleep=0.7, must=False):
                self.log_info("炒股：走势页卖出 → 回去买下一只")
                return True
            # 2026-10-30 实机：面板上的「卖出」是白字黄底，OCR 经常读不到 →
            # 落到 handle_dialog_continue 去点右上角「跳过」（快进图标，不推进），
            # 连续 3 轮点不动后任务直接停。这里按位置兜底点底部黄色大按钮。
            self.click(int(self.width * 0.5), int(self.height * 0.925), after_sleep=0.7)
            self.log_info("炒股：没认出「卖出」→ 点底部黄色按钮兜底")
            return True
        if self.match_text(texts, "收益排名") is not None:
            if self.click_button("next", after_sleep=0.7, must=False):
                self.log_info("炒股：收益排名 → 下一步")
                return True
        return False

    # ==================== 钓鱼（天赋「钓鱼」：真崎辉世 / 佐久间雨乃） ====================

    GROUND_NAMES = ("家门口的小溪", "普通的钓鱼场", "专业的钓鱼场")
    # 三张卡的价格左→右递增，实测两局都是 0 / 10 / 20（2026-10-30、2026-11-12）。
    # 只在 OCR 读不到价格时兜底；读到了就用真值。
    GROUND_PRICES = (0, 10, 20)

    def handle_fishing_ground(self, texts):
        """钓鱼场三选一：挑**买得起的最贵**那张，再点「确定」。

        2026-10-30 用户实机截图（第1周 星期四，79/130，零用钱 120）：
        「请选择1个钓鱼场」——家门口的小溪（0）/ 普通的钓鱼场（10）/
        专业的钓鱼场（20）。用户口径：**尽量选高价格的**。

        价格那三个数字（0/10/20）OCR **读不到**：实测整页只认出三张卡的标题、
        说明和「确定」（logs/debug/1030-152606_nochange3/ocr.txt）。所以旧版
        按位置取最右那张——三张卡的价格左→右递增，最右 = 最贵。

        2026-11-12 卡死（logs/1112-001437_真崎辉世/debug/1112-001819_recover_failed）：
        第 1 周零用钱只有 10，旧逻辑永远选最右「专业的钓鱼场」（20 币）→
        点「确定」游戏不理（钱不够、价格显示红色）→ 同一页无限重试，
        最后靠 noresp3s 看门狗兜。现在先读顶栏零用钱，**买不起就降一档**：
        价格读到了用真值，读不到按 GROUND_PRICES 兜底，从右往左挑第一张
        买得起的。零用钱也读不到时保持旧行为（最右）。

        旧版没有这个 handler：界面落到「没见过 → 点一下继续」，连点 3 次
        没变化后任务卡死（logs/debug/1030-152608_nochange3）。
        """
        cards = [t for t in texts if t.name.strip() in self.GROUND_NAMES]
        if self.exact_text(texts, ("请选择1个钓鱼场",)) is None and len(cards) < 2:
            return False

        money = self.read_money(texts)

        def center_x(t):
            return t.x + t.width / 2

        if cards:
            cards.sort(key=lambda t: t.x)
            # 价格与卡片按中心横坐标配对（列间距 0.134w，阈值 0.08w）
            readings = {}
            for t in texts:
                s = t.name.strip()
                if not s.isdigit():
                    continue
                cy = (t.y + t.height / 2) / self.height
                if not 0.55 <= cy <= 0.76:
                    continue
                idx = min(range(len(cards)),
                          key=lambda i: abs(center_x(cards[i]) - center_x(t)))
                if abs(center_x(cards[idx]) - center_x(t)) <= self.width * 0.08:
                    readings[idx] = int(s)

            def price_of(i):
                if i in readings:
                    return readings[i]
                if len(cards) == len(self.GROUND_PRICES):
                    return self.GROUND_PRICES[i]
                return None

            pick_i = len(cards) - 1
            if money is not None:
                for i in range(len(cards) - 1, -1, -1):
                    price = price_of(i)
                    if price is not None and price <= money:
                        pick_i = i
                        break
            target = cards[pick_i]
            label = target.name.strip()
            x = int(center_x(target))
            # 点卡片中部而不是标题：标题贴着卡片上沿，点在边缘可能选不中
            y = int(target.y + target.height / 2 + self.height * 0.12)
            price = readings.get(pick_i)
        else:
            label = "最右卡（卡名没认出）"
            price = None
            x, y = int(self.width * 0.81), int(self.height * 0.45)

        self.click(x, y, after_sleep=0.45)
        self.log_info(f"钓鱼场：零用钱 {money}｜选「{label}」"
                      + (f"（{price} 币）" if price is not None else "")
                      + "（买不起最贵就降档）")
        if not self.click_button("confirm", after_sleep=0.9, must=False):
            self.click(int(self.width * 0.5), int(self.height * 0.885),
                       after_sleep=0.9)
        self.log_info("钓鱼场：确定")
        return True

    # 「钓鱼啦！」那一页的转盘：见 handlers/wheel.py: handle_spin_wheel（通用机制）。

    # ==================== 写小说题材（天赋「写小说」：望月透） ====================

    def handle_theme_select(self, texts):
        """「请选择一个题材」：有红字标签就选它；颜色都一样就选中间那张，再点「确定」。

        2026-11-02 用户实机截图（第 2 周 星期一，体力 100/130，零用钱 110，
        灵感 11，角色 望月透 / 伊澄综合学园，状态「采风之后脑」）：
        三张竖卡横排，每张卡上沿一个题材标签（#末世 / #豪门 / #权谋）。

        选哪张（用户 2026-11-02 三次修正后的**最终口径**）：
        1. **优先红色**：哪张卡的标签是红字（粉/洋红都算），就选哪张
           —— 天赋触发的那张就是这个颜色（`#权谋` 那次是粉字）；
        2. **都一样就选中间**：要么三张全白（`#悬疑/#治愈/#爽文` 那次），
           要么三张全红（用户第二次实报），都算「一样」→ 按 x 排序取中位那张。

        注意判据**不是**「哪张跟别人不一样」：用户明确纠正过这一点。
        所以**不能按位置选，也不能按黄框选**（三张卡顺序随机；黄框只是光标/
        默认选中，第一次截图里黄框在中间 `#豪门`，要选的却是最右 `#权谋`）。

        旧版没有这个 handler：OCR 认得全部文字，但没有 handler 认领 →
        落 `unknown` 兜底，在原地刷存证帧（logs/debug/1102-220231_nochange3）。
        页面带「跳过」，所以**必须排在** `handle_dialog_continue` /
        `handle_story_arrow` 之前（注册表里已注明），否则被快进图标吃掉。
        """
        if self.exact_text(texts, labels.THEME_TITLE) is None:
            return False
        tags = [t for t in texts if labels.THEME_TAG_RE.match(t.name.strip())]
        if len(tags) < 2:
            return False

        # 已经点过「确定」但页面还没切走（切页有淡出动画）：补点，别重新判一遍颜色
        if time.monotonic() - getattr(self, "_theme_confirmed_at", 0.0) < 2.5:
            self.click_button("confirm", after_sleep=0.7, must=False)
            return True

        tags.sort(key=lambda t: t.x)
        scored = [(self.theme_tag_red_ratio(t), t) for t in tags]
        detail = "｜".join(f"{t.name.strip()} 红{r:.2f}" for r, t in scored)
        reds = [pair for pair in scored if pair[0] >= labels.THEME_TAG_RED_MIN]

        if len(reds) == 1:
            target = reds[0]
            self.log_info(f"题材：有唯一红字标签（{detail}）→ 选它")
        else:
            # 没有红字（三张全白），或者红的不止一张（三张全红/两张红）：
            # 用户口径「都一样就选中间」——按 x 排序取中位那张。
            target = scored[len(scored) // 2]
            why = "三张都红" if len(reds) == len(scored) else (
                "没有红字" if not reds else f"红字有 {len(reds)} 张、分不出主次")
            self.log_warning(f"题材标签{why}（{detail}）→ 按口径选中间那张")

        label = target[1].name.strip()
        cx = int(target[1].x + target[1].width / 2)
        # 点卡片**正文区**而不是标签本身：标签贴着卡片上沿，点在边缘可能选不中
        cy = int(target[1].y + target[1].height / 2 + self.height * 0.08)
        self.click(cx, cy, after_sleep=0.55)
        self.log_info(f"题材：选「{label}」")
        self.info_set("题材", label)
        self.sleep(0.3)          # 等选中动画走完，再点确定（否则确定会点空）
        self.click_button("confirm", after_sleep=0.9, must=False)
        self._theme_confirmed_at = time.monotonic()
        return True

    def handle_manuscript_submit(self, texts):
        """「WARD文档」汇报页：点右下角「交稿」。

        2026-11-02 用户截图（第 3 周）：标题「WARD文档」，左栏「收集到的idea」
        列着这几次用过的题材标签（#权谋 / #治愈 / #读心术），右侧正文是这次
        生成的小说，右下角黄色「交稿」，右上角「跳过」快进图标。

        判据 = `WARD文档` + `收集到的idea`。**不能只认标题**：页面底部（y≈0.93）
        还印着一个装饰用的「WARD文档」，只靠标题判的话会出现只有标题的中间态。
        页面带「跳过」，所以必须排在 `handle_dialog_continue` / `handle_story_arrow`
        之前（同「题材」页那条教训）。
        """
        for mark in labels.MANUSCRIPT_MARKS:
            if self.match_text(texts, mark) is None:
                return False
        # 已经点过「交稿」但结算还没出来：补点一次，别重复判页面
        if time.monotonic() - getattr(self, "_submit_confirmed_at", 0.0) < 2.5:
            self.click_button("submit", after_sleep=0.9, must=False)
            return True
        if self.click_button("submit", after_sleep=0.9, must=False):
            self.log_info("WARD文档：交稿")
        else:
            # 按钮文字没认出来：多半是**正文没滚到底**，「交稿」要滚到末尾才出现
            # （2026-11-06 实机：正文有滚动条时页面上根本没有这个按钮，
            # 旧逻辑盲点 (0.787w, 0.812h) 点的是正文空白，连点十几次纹丝不动）。
            # 在正文区向下拖一屏把按钮拖出来；滚到底后拖动不再改变文字集合。
            x = int(self.width * 0.58)
            prev = frozenset(t.name.strip() for t in texts)
            self.swipe(x, int(self.height * 0.72), x, int(self.height * 0.30),
                       duration=0.30, after_sleep=0.8)
            cur = self._ocr_region()
            self.click_button("submit", after_sleep=0.9, must=False)
            if not self.match_text(cur, "交稿") and not self.match_text(self._ocr_region(), "交稿"):
                # 还是没认出来：最后兜底按实测相对坐标点右下角黄色按钮
                self.click(int(self.width * 0.787), int(self.height * 0.812),
                           after_sleep=0.9)
                self.log_warning("WARD文档：滚动后仍没认出「交稿」，按固定坐标点右下角按钮")
            else:
                self.log_info("WARD文档：正文滚到底后点「交稿」")
        self.info_set("状态", "交稿")
        self._submit_confirmed_at = time.monotonic()
        return True

    # ==================== 做饭（天赋「做饭」：千圣贝尔丝） ====================

    def handle_ingredient_select(self, texts):
        """「请选择要使用的食材」：按品质（彩>金>紫>蓝）→ 队友多 选一张，再点「确定」。

        2026-11-03 实机（第 1 周 星期四，精力 97/130，零用钱 150）：
        三张竖卡横排，标题条按品质上色 ——
            隔夜饭（蓝，0 零用钱）/ 菜市场的普通食材（紫，10）/ 珍稀食材（金，20）

        用户口径（2026-11-03）：**默认 金 > 紫 > 蓝**（彩色按更通用的
        彩 > 金 > 紫 > 蓝 处理）；同品质再比队友头像数量。这一页三张卡都没有
        队友头像，所以实际就是按品质选最贵的那张 —— 金「珍稀食材」。

        为什么不能按名字/价格选：
        - 名字 OCR 会**截断最后一个字**（实机帧读到「隔夜…」「菜市场的普通食…」）；
        - 价格是**品质的副产物**（蓝 0 / 紫 10 / 金 20），但别的天赋事件不一定这样；
        - 判据只看颜色，和扭蛋/游戏厅共用 `card_quality_at` 的色相阈值。

        旧版没有这个 handler：OCR 认得全部文字，但没有 handler 认领 →
        每拍落 `unknown` 兜底点屏幕中央（紫色卡默认被选中，白送 10 零用钱的那张）。
        """
        if self.match_text(texts, labels.COOKING_TITLE) is None:
            return False
        cards = self.quality_cards(texts)
        if len(cards) < 2:
            # 标题认出来了但卡片还没淡入：等下一拍，别抢着点
            return False

        # 已经点过「确定」但页面还没切走（切页有淡出动画）：补点，别重新选一遍
        if time.monotonic() - getattr(self, "_cook_confirmed_at", 0.0) < 2.5:
            self.click_button("confirm", after_sleep=0.7, must=False)
            return True

        # 存证（未标注样本，供以后核对品质色判据）：同一页最多 10 秒存一次
        pick, ordered = self.quality_card_order(cards)
        now = time.monotonic()
        if now - getattr(self, "_cook_dumped_at", 0.0) > UNMARK_INTERVAL:
            self.dump_unmark(texts, "cook", cards, picked=pick["name"])
            self._cook_dumped_at = now

        detail = "｜".join(f"{c['name']}({c['quality']}/队友{c['mates']})"
                          for c in ordered)
        self.log_info(f"做饭：{detail} → 选「{pick['name']}」（{pick['quality']}）")

        box = pick["box"]
        # 点卡片**正文区**：标题条贴着卡片上沿，点边缘可能选不中
        cx = int(box.x + box.width / 2)
        cy = int(box.y + box.height / 2 + self.height * 0.06)
        self.click(cx, cy, after_sleep=0.5)
        self.info_set("做饭", pick["name"])
        self.sleep(0.3)          # 等选中动画走完，再点确定（否则确定会点空）
        if self.click_button("confirm", after_sleep=0.9, must=False):
            self._cook_confirmed_at = time.monotonic()
        else:
            self.log_warning("做饭：没认出「确定」按钮，下一拍重新判断这一页")
        return True

    # 「开始做饭！」那一页的转盘：通用机制，见 handlers/wheel.py: handle_spin_wheel
    # （2026-11-05 搬出：经理的小帮助等非天赋事件也触发同样的转盘）。

    # ==================== 直播三选一（天赋「人气主播」：和泉香织） ====================

    def handle_live_stream(self, texts):
        """「请选择1个直播主题」：纯卡面规则选一张，再点「确定」。

        用户 2026-11-07 口径：**不读顶栏粉丝数、不读目标数**（要做数字判断
        还得点开人数看，麻烦），只看三张卡自己的字面 —— 七次实录全中：

        1. **金卡/彩卡 → 直接选**（效果未知是碰高收益的机会，好过确定的
           粉丝增益：第3次胜风险卡、第5次胜花钱蓝卡；**第7次（晚 23:40）
           页面 金卡+右紫卡，旧版按旧②选了紫卡，用户反馈「没选金色 选了
           右边的紫色，逻辑要修正」→ 金卡提到所有紫卡规则之前**）；
        2. 否则**不扣精力且粉丝为正的紫卡**（粉丝增益大小不比：
           第1/4次「动画杂谈回」+3万/投射+30 都选它，哪怕别的紫卡 +15万）；
        3. 否则**粉丝增益最大的紫卡**（精力代价不计：第2次 -30精力/+15万
           胜过 -10精力/+10万，原话「只是减精力而已，但粉丝加的多」）；
        4. 否则**剩余卡里粉丝增益为正的最大者**（第6次：闲聊回
           精力+30/粉丝-1万｜联机回 紫卡「可能翻倍/可能-5万」｜抽奖回
           零用钱-100/粉丝+10万 → 选抽奖回，原话「左边加精力没用，中间
           没收益，只能选右边了」——没有紫卡/金卡可选时，确定的正粉丝
           就是唯一收益，花钱蓝卡也选；精力加成不缺就没用，风险卡期望≈0。
           旧口径「花钱蓝卡无例外垫底」据此修正：它垫的是风险卡/掉粉卡，
           不是金卡）；
        5. 全负/风险只剩时选第一张（六次实录没单独出现过，纯兜底）。

        卡面效果行只有四种判读材料（七次实录，见 labels.LIVE_* 注释）：
        「粉丝±N万」「精力±N」「可能…」「效果未知」。品质色沿用
        `quality_cards` 的 彩/金/紫/蓝 阈值（标题条上色，同一套）。

        为什么不做「粉丝 vs 目标」动态判断（2026-11-07 讨论）：目标是
        10万→30万→50万 递进的（第4次 42万 差 8万，+15万够；第5次 45万
        差 5万，蓝卡 +10万 够），动态规则两次都会改选蓝/紫大卡 —— 与
        用户口径直接冲突。静态规则会「晚一周达标」，用户明确接受。

        页面没有「跳过」，但会被品质卡兜底 `handle_unmarked_cards` 认领
        （三张上色卡全中形态判据）→ 本 handler 必须排在它前面，且标题
        已加进 labels.QUALITY_CARD_EXCLUDE（双保险）。
        """
        if self.exact_text(texts, labels.LIVE_TITLE) is None:
            return False
        cards = self.quality_cards(texts)
        if len(cards) < 2:
            # 标题认出来了但卡片还没淡入：等下一拍，别抢着点
            return False

        # 已经点过「确定」但页面还没切走（切页有淡出动画）：补点，别重新选
        if time.monotonic() - getattr(self, "_live_confirmed_at", 0.0) < 2.5:
            self.click_button("confirm", after_sleep=0.7, must=False)
            return True

        infos = [self._live_card_info(texts, c) for c in cards]
        pick, why = self._live_pick(infos)
        detail = "｜".join(f"{c['name']}({c['quality']}/{c['desc']})"
                          for c in infos)
        self.log_info(f"直播：{detail} → 选「{pick['name']}」（{why}）")
        self.info_set("直播", pick["name"])

        box = pick["box"]
        # 点卡片**正文区**：标题条贴着卡片上沿，点边缘可能选不中
        cx = int(box.x + box.width / 2)
        cy = int(box.y + box.height / 2 + self.height * 0.06)
        self.click(cx, cy, after_sleep=0.5)
        self.sleep(0.3)          # 等选中动画走完，再点确定（否则确定会点空）
        if self.click_button("confirm", after_sleep=0.9, must=False):
            self._live_confirmed_at = time.monotonic()
        else:
            self.log_warning("直播：没认出「确定」按钮，下一拍重新判断这一页")
        return True

    def _live_card_info(self, texts, card):
        """读一张直播卡的字面效果，归一成 {fans, stamina, risk, unknown, desc}。

        fans：粉丝净变化（万，负=扣粉丝，None=读不到/「翻倍」这类没法静态算）；
        stamina：精力变化（None=不涉及）；risk/unknown：「可能…」/「效果未知」。
        行首的「と」是卡头图标被 OCR 硬读出来的噪声 → 一律 `re.search`。
        效果行取「标题框下方、同列」的框：列宽按卡心 ±0.09 屏宽（卡间距
        ≈0.195 屏宽，一半 ≈0.098，不会串到邻卡）；0.35 屏高内覆盖两行效果
        （实测 515~605 @1080p），又不会够到底部「确定」（y≈765）。
        """
        box = card["box"]
        lines = [t.name for t in texts
                 if abs((t.x + t.width / 2) - (box.x + box.width / 2)) < self.width * 0.09
                 and 0 < (t.y - box.y) <= self.height * 0.35]
        info = {"name": card["name"], "box": box, "quality": card["quality"],
                "fans": None, "stamina": None, "risk": False, "unknown": False,
                "desc": card["quality"]}
        for raw in lines:
            s = raw.strip()
            if labels.LIVE_UNKNOWN_MARK in s:
                info["unknown"] = True
                info["desc"] += "/效果未知"
                continue
            if labels.LIVE_RISK_MARK in s:
                if not info["risk"]:
                    info["risk"] = True
                    info["desc"] += "/风险"
            m = labels.LIVE_FANS_RE.search(s)
            if m:
                info["fans"] = int(m.group(1))
                info["desc"] += f"/粉丝{m.group(1)}万"
                continue
            m = labels.LIVE_STAMINA_RE.search(s)
            if m:
                info["stamina"] = int(m.group(1))
                info["desc"] += f"/精力{m.group(1)}"
        return info

    def _live_pick(self, infos):
        """五条静态规则（口径见 handle_live_stream docstring），返回 (卡, 理由)。"""
        def fans(c):
            return c["fans"] if c["fans"] is not None else -999
        # ① 金卡/彩卡直接选：效果未知是碰高收益的机会，好过一切**确定的**
        #    粉丝增益（第3次胜风险卡、第5次胜花钱蓝卡；第7次页面 金卡+右紫卡，
        #    旧版把金卡排在紫卡之后 → 选了紫卡，用户打回「没选金色」）
        golds = [c for c in infos if c["quality"] in ("金", "彩")]
        if golds:
            return golds[0], "①金卡效果未知优先"
        # ② 不扣精力且粉丝为正的紫卡（可能多张：全选的时候第一张）
        safe = [c for c in infos if c["quality"] in ("紫", "彩")
                and not c["risk"] and not c["unknown"]
                and c["fans"] is not None and c["fans"] > 0
                and (c["stamina"] is None or c["stamina"] >= 0)]
        if safe:
            return max(safe, key=fans), "②不扣精力粉丝为正"
        # ③ 紫卡里粉丝增益最大的（精力代价不计；粉丝为正才值得选——
        # 「翻倍」读不到数 → fans=-999，固定掉粉的紫卡也不进）
        purples = [c for c in infos if c["quality"] in ("紫", "彩")
                   and not c["risk"] and not c["unknown"]
                   and (c["fans"] is not None and c["fans"] > 0)]
        if purples:
            return max(purples, key=fans), "③紫卡粉丝增益最大"
        # ④ 没有紫卡/金卡可选时，剩余卡里粉丝增益为正的最大者（第6次实录：
        #    闲聊回精力+30/粉丝-1万｜联机回紫卡风险｜抽奖回零用钱-100/粉丝
        #    +10万 → 用户选抽奖回，原话「加精力没用，中间没收益，只能选右边」）。
        #    花钱蓝卡也选——确定的正粉丝是此时唯一收益；精力不缺就没用，
        #    风险卡期望≈0（风险/效果未知卡不进这层，见②③同款排除）。
        gainers = [c for c in infos
                   if not c["risk"] and not c["unknown"]
                   and c["fans"] is not None and c["fans"] > 0]
        if gainers:
            return max(gainers, key=fans), "④剩余卡粉丝增益最大"
        # ⑤ 全负/风险兜底：只好选一张（七次实录没单独出现过，纯兜底）
        return infos[0], "⑤只剩负收益卡，选第一张"

    # ==================== 直播三选一结束 ====================

    # ==================== 和泉的问题（对话二选一） ====================

    def handle_izumi_question(self, texts):
        """「和泉的问题」对话二选一：**固定选第一个**（继续倾听），点完就生效。

        2026-11-07 实机（第 1 周 星期二，logs/debug/1107-182236_nochange3）：
        粉底横幅「继续倾听和泉的问题」（带和泉头像）+ 白底横条
        「婉拒，建议和泉去找其他人」。用户口径：**固定选第一个**，
        不看第二个选项的内容。

        为什么必须有专属 handler（2026-11-07 卡死事故）：
        - 这一页右上角**有**「跳过」二字，但它在这个状态下是**灰的、点不到**
          （用户口径：背景变灰了）→ 通用 handle_dialog_continue 会一直点它，
          3 次没变化后看门狗直接停任务；
        - 通用 handle_event_choice 也认领不了：两个选项是**整句**，
          「婉拒，…」带逗号被 choice_candidates 的句读规则排除；
          options_not_paragraph 又因两条选项左边缘太齐（居中同宽底板）
          被判成「段落」→ 两个兜底全漏。实录 ocr.txt 只有三个框：
          跳过 / 继续倾听和泉的问题 / 婉拒，建议和泉去找其他人。

        触发角色未确认（只见过和泉香织在场的一例），先按角色专属事件
        归口本模块；日后出现别的角色的同款页面再考虑改成通用。
        """
        first = self.exact_text(texts, (labels.IZUMI_QUESTION_FIRST,))
        if first is None:
            return False
        if self.match_text(texts, labels.IZUMI_QUESTION_SECOND) is None:
            return False
        # 已经点过但页面还没切走（切页有淡出动画）：补点第一个，别重复判页面
        if time.monotonic() - getattr(self, "_izumi_confirmed_at", 0.0) < 2.5:
            self.click_box(first, after_sleep=0.6)
            return True
        detail = "、".join(t.name.strip() for t in texts if t.name.strip())
        self.log_info(f"和泉的问题：二选一（{detail}）→ 固定选第一个「{first.name.strip()}」")
        self.info_set("和泉的问题", first.name.strip())
        self.click_box(first, after_sleep=0.8)
        self._izumi_confirmed_at = time.monotonic()
        return True

    # ==================== 和泉的问题结束 ====================

    # ==================== 工地施工（天赋「建设达人」：百目鬼剑） ====================

    def handle_construction(self, texts):
        """天赋「建设达人」的工地页：点「进行施工」推进。

        用户 2026-11-13 口径：工地是百目鬼剑的天赋【建设达人】事件。
        实拍两次（第 2/3 周星期四，1548x871）：整页只有「工地 /
        消耗获得奖励 / 越多，越有可能获得高额奖励 / -21 / 进行施工」，
        没有第二个选项——点「进行施工」= 扣精力换奖励。

        事故来历（2026-11-13 夜间实跑）：没人认领时 `handle_training_hub`
        把整页当成训练日主界面（右下角还露着「训练」），反复点主按钮
        → 空转 100 下 → 卡住接管 3 次 → 任务自停（同日 12:56、13:21 两次）。
        判据要「工地」+「进行施工」同时出现，别把普通训练日页抢走。

        用户 2026-11-13 口径：**无条件点，不设精力下限**（不做休息阈值判断，
        也别加配置项）。
        """
        if self.match_text(texts, labels.CONSTRUCTION_TITLE) is None:
            return False
        button = self.match_text(texts, labels.CONSTRUCTION_START)
        if button is None:
            return False
        self.click_box(button, after_sleep=0.8)
        self.log_info("工地（建设达人）：点「进行施工」")
        return True

    # ==================== 工地施工结束 ====================

    # ==================== 占卜（天赋「占卜」：卧云真昼） ====================

    def handle_divination(self, texts):
        """天赋「占卜」的结果页：点「确定」拿奖励。

        用户 2026-11-13 口径：这是**卧云真昼**的天赋事件【占卜】
        （`TALENT_OWNERS` 原来写成先斗月绮，已按实跑更正）。
        实拍（2026-11-13 16:45，第 1 周星期四，1548x871）：
            「占卜时间 / 中吉 / 训练基础值+2 / 确定」——整页只有「确定」一个按钮。
        旧版没人认领：`handle_popup` 有「确定」时故意不点（留给具体 handler），
        unknown 兜底点屏幕中央 (774,435) 点不中 (737,731) 的「确定」，
        接管 3 次后任务自停。
        """
        if self.match_text(texts, "占卜时间") is None:
            return False
        confirm = self.exact_text(texts, labels.BUTTONS["confirm"])
        if confirm is None:
            return False
        self.click_box(confirm, after_sleep=0.5)
        self.log_info("占卜：点「确定」")
        return True

    # ==================== 占卜结束 ====================
