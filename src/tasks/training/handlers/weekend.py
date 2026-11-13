"""商店、公园、步行街、游戏厅、外出。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。

炒股（天赋「股神!」）、钓鱼、写小说题材、做饭这些**角色天赋事件**
2026-11-05 起归 `handlers/talent.py`，不在这里。
"""

import re
import time

from src.tasks.ui import labels
from src.tasks.training import attributes, roster
from src.tasks.training.places import activities


class WeekendHandlers:

    # 扭蛋页「连续几拍读不到卡就放弃」的阈值。
    # 2026-11-01 15:09 实跑：卡面文字先画出来、价格药丸后画出来，中间那一拍
    # 旧判据（2 拍）正好卡在「卡面在、价格没有」这个中间态上 → 退出，白丢一次扭蛋。
    # 现在放宽到 4 拍（≈3.2 秒），末尾还有「再等一拍 1.2 秒」的兜底。
    GACHA_EMPTY_LIMIT = 4

    # 地点详情弹窗的「前往XXX」：公园点「划船」只是打开详情，还要再点这个。
    # 2026-11-01：原来是一个正则塞了 9 个地点名（住在通用层里 → 新增地点就要改
    # 通用层，而且「前往游戏厅」这种从没见过的组合也会被匹配）。
    # 现在每个地点模块给自己的正则（`places/activities.py`），这里只管找。
    ACTIVITY_GO = activities.PARK_GO
    ACTIVITY_GO_ALL = (activities.PARK_GO, activities.RESTAURANT_GO,
                       activities.ARCADE_GO, activities.SHOP_GO,
                       activities.STREET_GO)

    def handle_activity_go(self, texts):
        """地点详情弹窗：右下「前往划船 / 前往野餐」才是真正进入。

        坑（2026-10-26）：公园点「划船」只弹出详情（推荐属性 / 随机属性+10 /
        前往划船），必须再点「前往划船」。旧流程没有这个二级确认，
        于是停在详情页、每拍点屏幕中央都没反应，最后被卡死检测停掉。

        坑（2026-10-31 12:13 实跑，餐厅）：精力只剩 23/130，右卡「当服务员」
        的详情弹窗写着「精力不足 ⚡25」，「前往当服务员」是**灰的**。旧代码把它
        当普通按钮连点 4 分钟（每拍 OCR 有细抖动，`_no_change` 计数被复位，
        连「3 轮点不动就停任务」都没触发）。现在两级判据：弹窗自己说不足、
        或同一个「前往」点过一次画面没变 → **不点**，退出弹窗并把这张卡记成
        今天不可选；下一拍回到卡片页会自动改选另一张（桌游GM 不耗精力）。
        """
        target = self.find_go_button(texts, self.ACTIVITY_GO_ALL)
        if target is None:
            self._activity_go_key = None
            return False
        reason = self.activity_go_blocked(target, texts)
        if reason:
            name = self.block_activity(target.name)
            self.log_warning(
                f"「{target.name.strip()}」点不动（{reason}）→ 退出详情，改选「{name}」以外的卡",
                notify=True)
            self.close_activity_detail()
            return True
        self.click_box(target, after_sleep=0.9)
        self.log_info(f"地点确认：{target.name.strip()}")
        return True

    # 详情弹窗里「这个按钮现在点不动」的文字证据（2026-10-31 餐厅实测「精力不足」）。
    # 「体力不足」是同一状态的别名（旧文案/玩家说法），留着不冲突。
    BLOCKED_HINTS = ("精力不足", "体力不足", "条件不足", "无法选择", "无法前往",
                     "不可选择", "尚未满足")

    def activity_go_blocked(self, target, texts):
        """这个「前往XXX」现在点不点得动：点得动返回 None，否则返回原因。"""
        for t in texts:
            s = t.name.replace(" ", "")
            if not any(k in s for k in self.BLOCKED_HINTS):
                continue
            dy = abs((t.y + t.height / 2) - (target.y + target.height / 2))
            if dy < self.height * 0.25:
                return s
        # 上一拍点过**同一个**按钮、画面文字却一模一样 → 这个按钮对本页无效。
        # 比 flow 的 `_no_change` 靠谱：后者按整页指纹算，OCR 抖一下就被复位。
        key = (target.name.strip(), self._signature(texts))
        if getattr(self, "_activity_go_key", None) == key:
            return "上一下点了没反应"
        self._activity_go_key = key
        return None

    def block_activity(self, go_text):
        """「前往当服务员」→ 把选项名「当服务员」记成今天不可选。"""
        option = re.sub(r"^前往", "", go_text.strip())
        self.blocked_activities().add(option)
        return option

    def blocked_activities(self):
        """今天已经证实点不动的打工选项。

        挂在 `_weekend` 上：跟着「第N周 + 星期X」一起重置（`day_state()`），
        不落盘、不跨天。
        """
        return self._weekend.setdefault("blocked", set())

    # 地点详情弹窗右上角的 ×：2026-10-31 实测（餐厅 / 1549x871）在 (0.87w, 0.19h)。
    # 图上就是一个叉，OCR 读不到，所以先试文字、再按坐标兜底。
    ACTIVITY_CLOSE = (0.870, 0.195)

    def close_activity_detail(self, after_sleep=0.6):
        """关掉地点详情弹窗、退回卡片页 —— 别直接退出地点，那样就没法改选另一张。

        退出类按钮走 `labels.BUTTONS["exit_hatch"]`（全等匹配，2026-11-01 统一）：
        原来这里写的是 `click_text(("关闭","取消","返回"))`，那是**包含**匹配，
        正文里出现「返回」两个字也会被点；改成全等之后认不出来还有下面的坐标兜底。

        兜底坐标由**调用方**给（通用层不认识这个弹窗长什么样）。
        """
        if self.click_button("exit_hatch", after_sleep=after_sleep, must=False):
            return True
        self.exit_with_coords(self.ACTIVITY_CLOSE, after_sleep=after_sleep,
                              what="地点详情")
        return True

    def mark_visited(self, place):
        """地点内页真的打开后才登记「今天去过」。

        2026-10-26 用户反馈：在地图上点了「公园」，页面其实没打开，
        但旧代码点击前就 `visited.add(place)`，结果整天的公园都被跳过。
        现在只在各地点 handler 识别到自己的内页标题时登记。

        这里直接写 `_weekend`，**不**走 `day_state()` —— 后者按顶栏日期判断，
        地点内页如果读不到顶栏会重建整个状态，把 visited 清空。
        （`day_state()` 现在已经修成「读不到日期就沿用旧状态」，但这里
        直接写更稳：登记不该依赖「读得到日期」。）
        """
        self._weekend.setdefault("visited", set()).add(place)

    def leave_street(self, after_sleep=0.5):
        """退出扭蛋页：先找文字退路，再点右上角 ↩。

        2026-10-26 实测（1349x759）：**买完卡的详情页**底部有「放弃」，
        点它立即回到周末首页，「放弃」中心约 (0.346w, 0.896h)。

        退出类按钮走 `labels.BUTTONS["exit_hatch"]`（全等匹配）：两个页面各自
        只出现其中一个词，合成一个集合不改行为；同屏出现时「关闭」优先。

        兜底坐标由**步行街模块**给（`StreetPlace.EXIT_COORDS`）——
        通用层不认识这一页长什么样（2026-11-01）。
        """
        if self.click_button("exit_hatch", after_sleep=after_sleep, must=False):
            return
        self.exit_with_coords(self.EXIT_COORDS, after_sleep=after_sleep,
                              what="步行街")

    def handle_restaurant(self, texts):
        """餐厅内页：打工二选一（当服务员 / 桌游GM）。

        用户 2026-11-03 / 2026-11-13 口径（**通用**，不分流派）：
        0. 卡片上有「急招！」→ **优先那张**（周末餐厅打工双倍）；
        1. 默认选**当服务员**（右边、耗精力、零用钱++）；
        2. **桌游GM**（左边）的队友比当服务员多两个及以上 → 优先桌游GM；
        3. 精力不够那张 → 改选另一张（规则细节在 `places/restaurant.py`）。
        4. 急招卡不可选时：
           - 这一趟是今天的主计划（`current_step.urgent=False`）→ 改打桌游GM；
           - 这一趟是急招临时插队（`urgent=True`）→ 退出，回原计划。
        """
        if not self.restaurant_page(texts):
            return False
        self.mark_visited("餐厅")
        step = self._weekend.get("current_step")
        allow_fallback = not bool(getattr(step, "urgent", False))
        order = self.restaurant_order(texts, allow_fallback=allow_fallback)
        if not order:
            mode = "急招插队" if not allow_fallback else "原计划"
            self.log_warning(f"餐厅：{mode}，没有可选的卡 → 退出餐厅",
                             notify=True)
            self.exit_page()
            return True
        return self.choose_option(texts, order)

    def handle_park(self, texts):
        """公园内页：散步（零用钱+）/ 划船（属性+）/ 野餐（精力++）。

        实机（2026-10-26 debug 包）：三个**圆形图标**按钮，名字和效果都在图标下方
        （「划船 属性+」），页顶有「在公园干点什么好呢?」。
        旧判据 `labels.PARK_TITLE = ("在公园", ...)` 依赖「在公园」三个字，
        但实际文案是「在公园干点什么好呢?」整句 —— 整句 OCR 时「在公园」不是独立框，
        match_text 的子串匹配靠"整段拼接"兜底才能命中，命中后返回的是**最长的框**
        （整句话），click_box 会点到句子中间，什么都点不到 → 卡死。

        现在改成：标题只当**判据**（有「公园」相关字样 + 有三个圆形选项就处理），
        实际点击永远走选项文字框（散步/划船/野餐）。
        """
        if not self.park_page(texts):
            return False
        order = self.park_order(texts, self.style)
        ok = self.choose_option(texts, order)
        if ok:
            self.mark_visited("公园")
        return ok

    def handle_shop(self, texts):
        """商店：按「商店购买策略」买；买得起就多买几件，买不动了才关店。

        实机界面（2026-10-26）：
        - 左侧商品卡：名称 + 剩余数量 + 价格（100 / 60 / 45），**卡片底色 = 品质色**；
        - 右侧是选中商品的详情和**黄色购买条**（价格就在条上）；
        - 右上角 X 关闭；顶栏金币是专注条右边那个独立数字。

        用户口径（2026-11-08）：
        - 第 7 周之前只买 0 元商品（红包）；第 7 周以后按 红包 → 彩 → 金 → 紫；
        - 精力类商品垫底 + 过三类门槛，只买符合条件的（`pick_shop_item`）；
        - **不清空**：只按策略买合适的，买不动了才关店。
        所以这里一拍只买**一件**：买完不关店，下一拍重新 OCR 再决定。
        """
        if not self.shop_page(texts):
            return False

        # 确认真的进到商店页了才记「今天逛过」。点空不算（2026-10-30 15:04:04
        # 点在顶栏「新品上市」上），那样当天该允许再试一次。
        # 手动在店里起任务时 `_shop_try_key` 还是 None → 用当前日期兜底，
        # 否则关店后这一页还会再判一次「有标签 → 进店」。
        self._shop_day = (getattr(self, "_shop_try_key", None)
                          or self.current_day(texts))

        policy = self.config.get("商店购买策略")
        money = self.read_money(texts)
        items = self.shop_items(texts)
        # 商品卡不显示效果：逐张点选读右侧详情（只选中、不花钱），
        # 把「类别」从图鉴表换成游戏自己给的效果文案（2026-11-12）。
        self.read_shop_details(items, money)
        bar = self.top_bar(texts)
        self.log_info("商店：" + "、".join(
            f"{i['name']}({i['price']}/{i['quality']})" for i in items)
            + f"｜零用钱 {money}｜策略 {policy}")

        # 未登记商品：按品质色兜底的同时留一份 unmark 样本（一局每个名字只存一次），
        # 人工看 json/txt 就能往 `constants.SHOP_ITEMS` 补一行。
        fresh = [i for i in items if not self.shop_known(i["name"])]
        seen = getattr(self, "_shop_unmark_seen", None)
        if seen is None:
            seen = self._shop_unmark_seen = set()
        fresh = [i for i in fresh if i["name"] not in seen]
        if fresh:
            seen.update(i["name"] for i in fresh)
            self.log_info("商店：未登记商品 " + "、".join(
                "%s(%s/%s)" % (i["name"], i["price"], i["quality"])
                for i in fresh))
            if hasattr(self, "dump_unmark"):
                self.dump_unmark(
                    texts, "shop_item",
                    cards=[{"name": i["name"], "price": i["price"],
                            "quality": i["quality"],
                            "detail": i.get("detail")} for i in fresh],
                    note="商店未登记商品")

        info = roster.lookup(self.configured_captain())
        pick, reason = self.pick_shop_item(
            items, money, policy, stamina=bar.get("stamina"),
            stamina_max=bar.get("stamina_max"),
            required=attributes.required_attrs(info.get("position")),
            attrs=dict(self.info.get("attrs") or {}),
            caps=getattr(self, "_attr_caps", None) or {},
            soft_cap=int(self.config.get("属性减半门槛")),
            gap_limit=int(self.config.get("缺口排除阈值")))
        if pick is None:
            self.log_info(f"商店：{reason}，关店")
            self.finish_shop(texts)
            return True

        if self.buy_stalled("商店", money, (pick["name"], pick["price"]),
                            counter=self.buy_counter("商店")):
            self.log_warning(
                f"商店：「{pick['name']}」连续 {self.BUY_RETRY_LIMIT} 次点了没成交"
                "（零用钱没变、卡片还在）→ 今天不再买，关店")
            self.reset_buy_counter(self.buy_counter("商店"))
            self.finish_shop(texts)
            return True

        self.click(pick["x"], pick["y"], after_sleep=0.6)
        # 右侧黄色购买条（实机相对位置 0.79, 0.84）
        self.click(int(self.width * 0.79), int(self.height * 0.84), after_sleep=0.8)
        self.log_info(f"商店：买「{pick['name']}」{pick['price']}（{pick['quality']}｜零用钱 {money}）")

        confirm = self.find_text(labels.BUTTONS["confirm"], region_name="confirm_bar")
        if confirm is not None:
            self.click_box(confirm, after_sleep=0.6)
            self.log_info("商店：确认购买")
        return True

    # 右侧详情面板（书页）：x1, y1, x2, y2（画面比例）。
    # 实测 1920x1080（docs/shots/2026-10-26-shop.png）：标题 y≈0.246、
    # 效果文本 y≈0.58；下界避开数量选择器/购买条（就算读到纯数字也会被滤掉）。
    SHOP_DETAIL_REGION = (0.55, 0.12, 0.90, 0.72)

    def read_shop_details(self, items, money):
        """逐张点选商品卡 → 读右侧详情，把效果文本/类别挂到 item 上。

        2026-11-12 用户口径：商品卡只有名字/价格/品质色，**效果在右侧详情**；
        靠 `SHOP_ITEMS` 推类别要求图鉴点亮，而每个账号点亮进度不同。
        选中不花钱（购买是右下黄色条），一天最多 6 件。

        只看值得看的：买不起的、明确蓝/白的跳过（点它们纯浪费）。
        同一局内按名字缓存 —— 买完一件下一拍重新 OCR 时不再重复点。
        """
        cache = getattr(self, "_shop_detail_cache", None)
        if cache is None:
            cache = self._shop_detail_cache = {}
        for item in items:
            if item["price"] == 0:
                continue                        # 0 元（红包）直接买，不用看详情
            # 未登记商品**一律读详情**（不管买不买得起）：详情文本会进
            # `unmark/shop_item` 样本，之后补 `SHOP_ITEMS` 不用再回头截图。
            if self.shop_known(item["name"]):
                if money is not None and item["price"] > money:
                    continue
                if item.get("quality") in self.SHOP_SKIP_QUALITIES:
                    continue
            name = item["name"]
            if name in cache:
                detail = cache[name]            # None = 上次就没读到，别重复点
                if detail is None:
                    continue
            else:
                detail = self.probe_shop_detail(item)
                cache[name] = detail
                if detail is None:
                    continue                    # 读不到 → 退回名字表/前缀
            item["detail"] = detail
            item["detail_category"] = self.parse_shop_detail(detail)
            item["stamina_amount"] = self.parse_shop_stamina(detail)
            self.log_info(f"商店：详情「{name}」→ {detail[:40]}"
                          f"（{item['detail_category'] or '认不出'}）")

    def probe_shop_detail(self, item):
        """点一下卡片（只选中），读右侧详情面板的效果文本；读不到 None。

        **必须校验标题**：售罄卡点不动时右侧还停在上一个商品，不校验就会把
        上一件的效果安到这一件头上（商店列表里「剩余」不参与解析）。
        """
        self.click(item["x"], item["y"], after_sleep=0.25)
        texts = self._ocr_region(box=self.box_of_screen(*self.SHOP_DETAIL_REGION))
        if not texts:
            return None
        name = item["name"]
        title = None
        for t in texts:
            s = (t.name or "").strip()
            if s and (s == name or name in s or s in name):
                title = s
                break
        if title is None:
            self.log_debug(f"商店：点「{name}」后详情标题对不上 → 不采信")
            return None
        parts = []
        for t in texts:
            s = (t.name or "").strip()
            if not s or s == title or s.isdigit() or len(s) < 2:
                continue
            parts.append(s)
        detail = "".join(parts)
        # 卫生检查：详情区只有一句效果文案。混进「商店/上新时间/剩余/出行」
        # 说明这块区域不是详情面板（或 OCR 没按区域裁剪、拿到整帧），
        # 宁可退回名字表，也不要把整页文本当效果（假阳性会改错类别）。
        if not detail or len(detail) > 40 or any(
                w in detail for w in ("商店", "上新时间", "剩余", "出行")):
            self.log_debug(f"商店：详情文本不像效果文案 → 不采信：{detail[:60]}")
            return None
        return detail

    def finish_shop(self, texts=None):
        """关店。一天只真进一次由 `_shop_day`（handle_shop 确认商店页后记账）负责。"""
        self.close_shop(texts)

    def close_shop(self, texts=None):
        """点右上角 X 关商店。"""
        texts = texts or self._ocr_region()
        target = self.match_text(self.texts_in_region(texts, "top_right"), "X")
        if target is not None:
            # 2026-10-29 用户反馈「X 点得有点歪」：实测帧（docs/shots/2026-10-26-shop.png）
            # 里 OCR 的 X 框是 (1748,133,80,81)、中心 (1788,173)，但 X 字形的视觉中心
            # 在 y≈180（框中心偏下约 8% 框高）。所以往下压 10% 框高再点。
            cx = target.x + target.width / 2
            cy = target.y + target.height / 2 + target.height * 0.10
            self.click(int(cx), int(cy), after_sleep=0.8)
        else:
            self.click(int(self.width * 0.94), int(self.height * 0.11), after_sleep=0.8)
        self.log_info("商店：关闭")

    @staticmethod
    def _gacha_prices_ready(cards):
        """这组卡的价格都读到了吗？

        2026-11-01 15:09：卡面先渲染、价格后渲染。价格是 None 时买不了
        （买不起点「确定」会卡在扭蛋页反复点，见 handle_street 的注释），
        所以价格没出来就不能算「卡片已稳定」。
        """
        return bool(cards) and all(c.get("price") is not None for c in cards)

    @staticmethod
    def _gacha_signature(cards):
        """一组扭蛋卡的指纹：名字 + 价格 + 品质 + 队友标记。空组返回 None。

        2026-10-31 23:41 实跑（用户：「没有识别到中间的卡片有队友，又选择了左边
        的卡片」）：只比「名字 + 价格」不够 —— 卡片淡入期的半透明画面上，OCR 读
        出的名字和价格**完全正确**，但品质/队友是从透出来的背景取的：暖色店面被
        判成金卡、蓝色货架被判成队友标记。于是「两帧一致」立刻通过，左边的
        「运控增加」以金+队友 321 分和中间的「防守升级包++」打平，max() 按顺序
        取了左边。把品质和队友也放进指纹，画面没定下来时就会继续等。
        """
        if not cards:
            return None
        return tuple(sorted((c["name"], c["price"], c["quality"], bool(c["mate"]))
                            for c in cards))

    def _merge_mate_cache(self, cards):
        """用当天缓存补 mate：读不到徽章的卡沿用「今天见过它带队友」的事实。

        2026-11-07 19:16 实跑（第3周周日第2次扭蛋买错）：「再买一次！」橙色
        横幅（x 210~382, y 203~280）**物理盖住**左卡徽章位置（x 215~356,
        y 212~266），那一拍任何颜色判据都读不到徽章 —— 左「投射增加+」
        实际带队友（首次出卡时判到过 mate=True），被当成无队友，
        同品质下输给右边「精力恢复+」的恢复分。所以：
          - 识别到 mate=True 的卡，按**卡名**记进当天状态（day_state 隔天清空，
            不会把昨天的事实带过来）；
          - 读不到（False）的卡先查缓存，查到就用 True。
        不担心同名不同货：扭蛋页同名卡同品质（「+」只代表档位、档位由品质色
        区分），带不带队友是**当天刷新时**定的属性，一天内不会变。
        """
        cache = None
        for c in cards:
            if c.get("mate"):
                if cache is None:
                    cache = self.day_state().setdefault("gacha_mate", set())
                cache.add(c["name"])
        if cache:
            for c in cards:
                if not c.get("mate") and c["name"] in cache:
                    c["mate"] = True
        return cards

    def settle_gacha_cards(self, texts):
        """读扭蛋三张卡，**等开场动画稳定**后再返回。

        2026-10-27 实测（第 4 局 10:07:52）：卡片飞入/翻转期间 `read_gacha_cards`
        也能返回 3 张卡（中间态），脚本 0.3 秒就点了「确定」，买下中间那张紫卡；
        1.7 秒后重进才看清同价位的金卡（防守增加++/80 币），等于白扔一次机会。
        所以连续两次识别一致才认账；空结果继续等（旧版只处理了"空"这一种）。
        同一组卡确认过就不再重复等，避免每拍多花 1.6 秒；一直不稳时
        最后还会多等一拍 1.2 秒（见函数末尾），最多约 3.6 秒定不下来才放弃。

        2026-11-01 15:09 新增：**价格还没渲染出来时不算「稳定」**。
        卡面文字先画出来、价格数字后画出来，那一拍卡是「读到了但价格是 None」；
        旧判据只看指纹（名字/价格/品质/队友），价格从 None 变成 80 这一下
        会被当成「变了」→ 继续等；但反过来「两拍都是 None」就等于认账了，
        于是拿不到价格、买不了。现在价格未出就**继续等**，直到价格出来。

        2026-11-07 新增：返回前把 mate 过一遍**当天缓存**（`_merge_mate_cache`）
        ——「再买一次！」横幅会盖住左卡徽章，读不到时沿用当天见过的事实。
        """
        cards = self.read_gacha_cards(texts)
        sig = self._gacha_signature(cards)
        prev_sig = getattr(self, "_gacha_sig", None)
        if sig and sig == prev_sig and time.monotonic() - getattr(self, "_gacha_at", 0.0) < 3.0:
            if self._gacha_prices_ready(cards):
                return self._merge_mate_cache(cards)
        last_cards, empty_streak = cards, 0
        for _ in range(3):
            self.sleep(0.8)
            try:
                self.next_frame()
            except Exception:                     # noqa: BLE001
                pass
            nxt = self.read_gacha_cards(self._ocr_region() or [])
            nsig = self._gacha_signature(nxt)
            if nsig and nsig == sig and self._gacha_prices_ready(nxt):
                if sig != prev_sig:
                    self.log_info("扭蛋：卡片已稳定（" + "、".join(
                        c["name"] for c in nxt) + "）")
                self._gacha_sig, self._gacha_at = nsig, time.monotonic()
                return self._merge_mate_cache(nxt)
            if nsig:
                last_cards, empty_streak = nxt, 0
            else:
                empty_streak += 1
                if empty_streak >= self.GACHA_EMPTY_LIMIT:
                    # 2026-11-01 15:09 实跑：卡面文字先画出来、价格数字后画出来，
                    # 旧判据「连续两拍读不到卡就退出」正好卡在这个**中间态**上：
                    # 19.5s 那拍卡面（训练增强/运控增加+++）已经在了，只是没有价格，
                    # 于是被判成「页面是空的」→ 退出 → 白丢一次扭蛋。
                    # 现在放宽到 4 拍（≈3.2 秒）；末尾还有「再等一拍 1.2 秒」的兜底。
                    self._gacha_sig, self._gacha_at = None, time.monotonic()
                    return []
            cards, sig = nxt, nsig
        # 3 拍都还在变（打折数字滚动 + 卡片淡入）：再等一拍**慢的**，用画面定下来
        # 以后的那一帧决定。快速循环里最后那一帧往往正好是淡入中间态
        # （2026-10-31 23:41 就是这么买错的，见 `_gacha_signature`）。
        self.sleep(1.2)
        try:
            self.next_frame()
        except Exception:                         # noqa: BLE001
            pass
        final = self.read_gacha_cards(self._ocr_region() or [])
        if final:
            if self._gacha_signature(final) != self._gacha_signature(last_cards):
                self.log_info("扭蛋：又等一拍（1.2 秒）画面才定下来（"
                              + "、".join(c["name"] for c in final) + "）")
            last_cards = final
        elif last_cards:
            self.log_warning("扭蛋：卡片一直在变（动画/刷新），按最后一次识别结果处理")
        self._gacha_sig = self._gacha_signature(last_cards)
        self._gacha_at = time.monotonic()
        return self._merge_mate_cache(last_cards)

    def handle_street(self, texts):
        """步行街扭蛋：**判断在 `training/places/street.py`**（地点模块）。

        这里只负责「认页面 → 问模块 → 照它说的点」。
        步行街模块只管自己的事（买哪张卡、试过没有），不需要知道别的地点。
        """
        if not self.street_page(texts):
            return False
        if not self.street_enabled():
            self.log_info("步行街扭蛋：未开启，退出")
            self.leave_street()
            return True

        cards = self.settle_gacha_cards(texts)
        # 2026-11-01 15:09 事故：`mark_visited("步行街")` 原来写在**读卡之前**，
        # 于是「进步行街 → 没读到卡 → 退出」也算「今天来过步行街」，
        # 回到地图只剩餐厅可去 → 白跑一趟外出（只赚了 110 零用钱）。
        #
        # 但**不能**简单地「没读到卡就不标记 visited」：那样回到地图时
        # todo 里还有步行街 → 又点进去 → 又失败 → 无限循环。
        # 所以分两件事（见 places/street.py 的说明）：
        #   - `visited`：今天**进过**步行街（进过就算来过，防止反复进）；
        #   - `gacha_tried`：今天**试过**抽扭蛋（不管买没买成）。
        # 地图上只按 `gacha_tried` 排除步行街（试过了就不再试），
        # 「买没买成」交给 gacha_spent 决定要不要去餐厅打工。
        self.mark_visited("步行街")
        self.mark_tried()
        if not cards:
            self.log_warning("扭蛋：三张卡都没认出来，退出不买")
            self.leave_street()
            return True

        bar = self.top_bar(texts)
        stamina = bar.get("stamina")
        stamina_max = bar.get("stamina_max") or 130
        money = self.read_money(texts)
        if money is None:
            # 读不到零用钱就不能判断买不买得起：点「确定」买不起时游戏不会扣钱，
            # 页面也不关，旧代码会一直「选卡 → 确定 → 没反应」（2026-10-26 实测）。
            self.log_warning("扭蛋：读不到零用钱，保守退出不买")
            self.leave_street()
            return True

        info = roster.lookup(self.configured_captain())
        best, detail, _all = self.gacha_candidates(
            cards, stamina, stamina_max, money,
            required=attributes.required_attrs(info.get("position")),
            attrs=dict(self.info.get("attrs") or {}),
            caps=getattr(self, "_attr_caps", None) or {},
            gap_limit=int(self.config.get("缺口排除阈值")))
        if best is None:
            self.log_info(f"扭蛋：{detail} → 没有可买的"
                          f"（零用钱 {money}，精力 {stamina}），退出")
            self.leave_street()
            return True

        self.click(best["x"], best["y"], after_sleep=0.5)
        self.log_info(f"扭蛋：{detail} → 选「{best['name']}」"
                      f"（{best['quality']}，{best['price']}币，"
                      f"零用钱 {money}，精力 {stamina}）")

        if self.click_button("confirm", after_sleep=0.8, must=False):
            self.log_info("扭蛋：确定")
            # 今天花过钱了：扭蛋流周末不必再去餐厅打工（flow.weekend_priority 用）
            self.mark_spent()
        else:
            self.click(int(self.width * 0.5), int(self.height * 0.93), after_sleep=0.8)
            self.log_warning("扭蛋：没认出「确定」，按底部中间兜底")
        return True

    def handle_arcade(self, texts):
        """游戏厅：优先「跳过」已三星的小游戏；否则花钱进**带「自动」标记**的小游戏。

        界面字段与选卡规则的**单一真相**在 `places/arcade.py` 的模块 docstring，
        这里不再抄第二份 —— 2026-11-10 发现两份已经漂了
        （本页写「没有蓝」，模块里还写着「彩 > 金 > 紫 > 蓝」）。

        本 handler 只做动作：认页面 → 标「已去过」→ 跳过已三星 → 问
        `pick_arcade_card` 该买哪张 → 点「确定」。判断全在 places/arcade.py。

        付完钱小游戏会**自己打完**（探针实测：零用钱 219→155，之后走普通剧情/结算），
        后续交给通用 handler 推进。
        """
        if not self.arcade_page(texts):
            return False
        self.mark_visited("游戏厅")
        skip = self.arcade_skip(texts)
        if skip is not None:
            self.click_box(skip, after_sleep=0.4)
            confirm = self.match_text(self._ocr_region("confirm_bar"), labels.CONFIRM_TEXTS)
            if confirm is not None:
                self.click_box(confirm, after_sleep=0.45)
            self.log_info("游戏厅：跳过已三星小游戏")
            return True

        if not self.arcade_paid_enabled():
            self.log_info("游戏厅：未开启付费，退出")
            self.exit_page()
            return True

        money = self.read_money(texts)
        cards = self.arcade_cards(texts)
        info = roster.lookup(self.configured_captain())
        pick, reason = self.pick_arcade_card(
            cards, money,
            required=attributes.required_attrs(info.get("position")),
            attrs=dict(self.info.get("attrs") or {}),
            caps=getattr(self, "_attr_caps", None) or {},
            gap_limit=int(self.config.get("缺口排除阈值")))
        if pick is None:
            self.log_warning(f"游戏厅：{reason}，退出")
            self.exit_page()
            return True

        confirm = self.exact_text(texts, labels.BUTTONS["confirm"])
        if confirm is not None:
            if self.buy_stalled("游戏厅", money, (pick["attr"], pick["price"]),
                                counter=self.buy_counter("游戏厅")):
                self.log_warning(
                    f"游戏厅：「{pick['attr']}」连续 {self.BUY_RETRY_LIMIT} 次点了没成交"
                    "（零用钱没变、卡片还在）→ 今天不再买，退出")
                self.reset_buy_counter(self.buy_counter("游戏厅"))
                self.exit_page()
                return True
            self.click_box(pick["box"], after_sleep=0.4)
            self.click_box(confirm, after_sleep=1.0)
            # 小游戏会自己打完（用户 2026-10-29 口径），中间只有一段没有按钮的
            # 动画：交给 run() 的等待分支，不要点、也不要让卡死看门狗误判。
            self._arcade_wait_since = time.time()
            self.log_info(f"游戏厅：买了「{pick['attr']}」小游戏"
                          f"（{pick['quality']}，奖励上限 {pick['hi']}，"
                          f"价格 {pick['price']}，零用钱 {money}，"
                          f"队友 {self._arcade_mates(pick)} 人），"
                          "等它自己打完")
            return True

        self.log_warning("游戏厅：没有「确定」按钮 → 退出")
        self.exit_page()
        return True

    def handle_go_out(self, texts):
        """周末首页：点「出行」进地图。

        实机（2026-10-25 周六）：出行页只有「出行」两个字，没有任何地点名，
        所以 handle_weekend 认不出来，必须先点这一下。

        2026-10-29 口径：周末首页**不再进店**（进店统一由
        `training/daycycle.py: late_shop_step` 判断：看到「新品上市」标签才进），
        这里只负责点出行。

        **判断在 `training/daycycle.py`**（与训练日那个入口同级）：
        `outing_offer(..., block_if_used=False)` —— 周末出行是**主流程**，
        不是野上泷周五那种「额外外出」，所以**不受** `outing_done` 限制。
        """
        # 地图上没有「出行」按钮（2026-10-27 修正）：旧判据是「有地点名就是地图」，
        # 但周末首页的台词也含地点名（「告诉她公园的草坪是假的」），
        # 结果首页被判成地图、永远不点「出行」。现在统一用「有出行 = 首页」。
        if self.match_text(texts, "出行") is None:
            return False

        # 训练日里的额外出行（野上泷：周五也能出行）：一天只用一次。
        # 用过之后如果又停在一个「有出行」的页面（进地图又退回来的首页），
        # 再点就是「出行 → 地图 → 退回首页」打转，所以这里直接退出去。
        # 注意：**只对非周末生效** —— 周末出行是主流程，每次进地图都要点。
        # `outing_done` 由 `handle_weekend` 在**真的看到地图**那一拍置位
        # （2026-11-03），所以「点了出行但没进地图」时这里还会再点一次，
        # 不会出现「周五点空一次就整天进不去」。
        st = self.day_state()
        day = st["date"][1] if st["date"] else None
        if st.get("outing_done") and day not in ("六", "日"):
            self.exit_page()
            self.log_info("训练日的额外出行已经用过 → 返回主界面")
            return True

        # 2026-10-30 用户截图（野上泷周五额外周末）：对话框里有一句
        # 「今天可以出行哦！要不…」，click_text("出行") 会命中这句**台词**
        # （日志 `left_click 今天可以出行哦！要不 (950, 182)`），不是右侧按钮。
        # 2026-10-31：判据收进 parsers.find_go_out_button（只认整词/短词，
        # 多个候选时优先右下按钮区），训练日那边也用它，别各写一份。
        action, reason = self.outing_offer(texts, block_if_used=False)
        if action == "点按钮":
            target = self.find_go_out_button(texts)
            self.click_box(target, after_sleep=0.6)
            self.log_info(f"周末：点「出行」进地图"
                          f"@({target.x + target.width // 2},"
                          f"{target.y + target.height // 2})")
        else:
            # 只有台词、没有按钮：这一拍什么都不点，等下一拍按钮出来。
            # 注意：这里**不点台词** —— 周末首页的台词不是按钮，
            # 点它就是对话框里的空点击（2026-10-30、2026-11-02 实跑）。
            return False
        return True

    def venue_on_discount(self, texts, label_box):
        """地点名字**左边**有没有红色大号「打折」字样。

        用户 2026-10-30：打折标记是地点名左侧的红色大号文字「打折」。
        用 OCR 文本判（游戏里这两个字够大，OCR 能读到——2026-10-30 01:32 的
        handle_weekend 事件里就出现了「打折」）；位置要求在同一行、且在名字左边，
        避免把别处的「打折」算进来。
        """
        if label_box is None:
            return False
        cx = label_box.x + label_box.width / 2
        cy = label_box.y + label_box.height / 2
        for t in texts or []:
            name = t.name.strip()
            if "打折" not in name:
                continue
            tcx = t.x + t.width / 2
            tcy = t.y + t.height / 2
            if tcx < cx and abs(tcy - cy) <= self.height * 0.12:
                return True
        return False

    def handle_weekend(self, texts):
        """出行地图（周末 + 训练日的额外出行）：去今天该去的地点。

        用户 2026-10-29 / 2026-11-10 / 2026-11-11 口径
        （见 constants.WEEKEND_PLAN / WEEKEND_MAIN_PLACE 和
        `places/map.py: training_weekend_places`）：
        - **训练流**：按精力缺口选动作 ——
          缺口 ≥ 周末划船缺口（30）→ 公园（缺口 ≥ 周末野餐阈值 70 野餐，
          否则划船）；缺口 < 30 → 餐厅打工（零用钱留给商店的牛奶/秘籍，
          不去步行街/游戏厅）；缺口读不到 → 回退「餐厅 → 公园」；
        - **商店流**：周六/周日去餐厅打工；
        - **扭蛋流 / 小游戏流**：候选固定「主地点 + 餐厅」，**与星期无关**
          （2026-11-10：训练日的额外出行日也走同一条门槛）——
          零用钱 ≥ 360 → 主地点，< 360 → 餐厅打工，队友和标签都不参与
          （`places/map.py: prefer_venue`）。
        """
        # 周末首页有「出行」大按钮，地图上没有（2026-10-27 核对 run2 地图页三帧 OCR：
        # 地图只有「公园/公园+」「游戏厅/属性+」「步行街/零用钱+」，无「出行」）。
        # 台词常含地点名（「告诉她公园的草坪是假的」「游戏厅地狱挑战开始了！」），
        # 只靠地点名会把首页当地图：点中台词 → 误 mark_visited → todo 空 → 点右上角。
        if not self.weekend_map_page(texts):
            return False
        st = self.day_state()
        # 2026-11-03：**真的看到地图**这一刻才记「今天的出行机会用掉了」。
        # 记账点原来在 `handle_training_day` 的点击处，于是点空了也算用过 ——
        # 周五（野上泷额外周末）当天就再也进不去地图（`handle_go_out` 对非周末
        # 的 `outing_done` 是直接返回）。放这里同时也保住了「出图 → 回首页 →
        # 再点出行」的打转守卫。
        self.mark_outing_used()
        visited = st.setdefault("visited", set())
        day = st["date"][1] if st["date"] else None

        # 今天要走的步骤：地图模块算主计划 + 急招 detour。
        # visited / gacha_tried 的过滤已经收进 `weekend_plan`。
        plan = self.weekend_plan(day, st, texts)
        if not plan:
            # 地点都去过：停在周末地图上什么都不会发生（2026-10-26 卡死：
            # 地图被 story_arrow 当成剧情页连点 ▼，反复 50 秒）。
            # 点右上角返回周末首页，让当天结算。
            st["current_step"] = None
            self.log_info(f"周末：{sorted(visited)} 都去过了 → 返回")
            self.exit_page()
            return True

        # 日志打**真实**星期：旧写法 `'日' if day == '日' else '六'` 把周五也印成
        # 「周末六」，排查「周五没进出行地图」时会把周五看成周六（2026-11-03）。
        self.log_info(f"周末（星期{day or '读不到'}）：待走 "
                      f"{[s.place for s in plan]}（已去 {sorted(visited)}）")
        for step in plan:
            place = step.place
            # 只认「公园」「餐厅」这种**整框就是地点名**的按钮。
            # 2026-10-27 事故：地图页有对白「告诉她公园的草坪是假的」「要去野餐」，
            # match_text 的包含匹配会命中长句，click_box 点到句子中间（图上什么都没发生），
            # 下一拍又判「都去过了」→ 返回死循环。
            target = self.exact_text(texts, (place,))
            if target is None:
                target = next((t for t in texts
                               if place in t.name and len(t.name.strip()) <= 4), None)
            if target:
                # 不在这里记 visited：要等地点的内页 handler 真认到标题才登记，
                # 否则「点了没打开」也会被当成去过（2026-10-26 公园没打开就被跳过）。
                # current_step 让餐厅 handler 知道这趟是主计划还是急招插队。
                st["current_step"] = step
                self.click_box(target, after_sleep=0.5)
                self.log_info(f"周末点「{place}」（{self.style}）")
                return True
        return False
