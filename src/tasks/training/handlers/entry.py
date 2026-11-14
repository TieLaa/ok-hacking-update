"""大厅、剧情、选队长、组队、器材、确认编队。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""


from src.tasks.training import roster
from src.tasks.training import page_signatures
from src.tasks.ui import Defs
from src.tasks.ui import labels


class EntryHandlers:

    def handle_game_title(self, texts):
        """游戏标题页（NBA / 账号 / 版本号 / 适龄提示）：点「点击进入」进游戏。

        2026-11-10 19:05 实跑：切窗口/开快速比赛把游戏打回标题页，兜底点的是
        屏幕中央 (0.5w,0.5h)，点不中底部 (0.46w,0.83h) 的「点击进入」，
        7 秒后才由用户手动点进去。判据要求「点击进入」+ 任一标题页特征词，
        避免剧情正文里的「点击进入」被误认。
        """
        enter = self.exact_text(texts, (labels.GAME_TITLE_ENTER,))
        if enter is None:
            return False
        if self.match_text(texts, labels.GAME_TITLE_MARKS) is None:
            return False
        self.click_box(enter, after_sleep=1.2)
        self.log_info("游戏标题页：点「点击进入」")
        return True

    def handle_dialog_continue(self, texts):
        """剧情/提示：右上角有「跳过」就点，否则交给 handle_story_arrow 连点推进。

        例外一：屏幕中部是竖排选项（三选一）时，「跳过」通常是灰的，点了不推进。
        这时必须让位给 handle_event_choice（用户 2026-10-26 反馈：
        机器人一直点跳过，不点第一个选项）。

        例外二（2026-11-05 实机，logs/debug/1105-172329_noresp3s）：**右下角有
        白色 ▼ 的过场页**（状态事件「恢复精力」的奖励展示，先斗月绮立绘），
        「跳过」是个不响应的摆设，连点 12 秒指纹不变 → 看门狗接管 3 次 → 停任务。
        真正的推进控件是右下角 ▼。所以：has_story_arrow() 为真时让位给
        handle_story_arrow（它连点 ▼ 推进），本 handler 只处理「没有 ▼ 的真对话框」。
        """
        if (len(self.choice_candidates(texts)) >= 2
                or len(self.options_not_paragraph(texts)) >= 2):
            return False
        if self.has_story_arrow():
            return False
        skip = self.match_text(self.texts_in_region(texts, "top_right"),
                              ("跳过", labels.TAP_TO_CONTINUE))
        if skip is not None:
            self.click_box(skip, after_sleep=0.35)
            return True
        return False

    def handle_club_members(self, texts):
        """社团成员 / 角色列表页：误入时退出（左上角 <<）。"""
        # 必须整词：「她说要给社团成员们疏通经脉」这种正文也会命中包含匹配，
        # 结果按摩事件页被当成社团成员页，一直点左上角返回（2026-10-28 18:13 卡死）
        if self.exact_text(texts, ("社团成员",)) is None:
            return False
        self.click(int(self.width * 0.035), int(self.height * 0.06), after_sleep=0.8)
        self.log_info("社团成员页：点左上角返回")
        return True

    def handle_story_arrow(self, texts):
        """剧情对话：点一下、确认界面变了就停，没变才补点（最多 3 下）。

        判据几经修正（2026-10-25 实机）：
        1. 不能只靠「白色 ▼」——**很多剧情页根本没有 ▼**，点屏幕任意位置就推进；
        2. 也不能靠「背景必须暗」——浅色场景的剧情页大量存在；
        3. 也不能靠「白色像素够多」——扭蛋页的黄色按钮/白底卡片会误判（实训死循环）；
        4. 唯一可靠的特征是**底部台词条里的一行长中文**：y 0.72~0.96、12 字以上。

        训练日/科目页/赛程页底部也有文字（属性、按钮），所以先排除这些关键词。

        2026-11-14 阶段 2 验证结论（50 张回归图逐张对比，赢家+动作全等）：
        - 「失败率 / 决定 / 休息 / 赢得比赛 / VS」这组**正文关键词**已删 ——
          它们在别的页面也出现，删掉不影响分派（专属 handler 排在本 handler 前面，
          顺序本身就保证专属的先赢）。
        - 「关卡加成 + 奖励预览」「地点名×2」这两条**不能删** —— 它们是
          **页面级强特征**，区分的是两个不同的页面（全国大赛关卡列表页 /
          周末地图页）。删掉后 2026-10-27-race-ladder.png 会被当成剧情页，
          去点「奖励预览」附近的空白（2026-10-27 实跑就是每 0.5 秒点右下角）。
          这条经验就是阶段 2 的判据：**排除条件分两类** ——
          「正文关键词」可删（靠顺序兜底），「页面级强特征」不可删。
        """
        # 周末地图：地点名是导航，不是可推进的剧情。
        # 2026-10-26 卡死：地图上被当成剧情页连点 ▼，反复 50 秒才被看门狗救回来。
        # （阶段 2：这条和上面的「关卡加成+奖励预览」都收口到 `page_signatures`。）
        if page_signatures.page_is_other(self, texts, "story_arrow"):
            return False

        # 训练事件 / 只有 ▼ 的剧情页（实机 2026-10-26）：右下角白色 ▼ 是最可靠的信号。
        # 这类页面没有长台词，旧判据认不出来，会一直卡在「没见过」。
        # 竖向三选一（如「更衣室加设空衣柜 / JellyRabbit毛绒玩具 / 了解千圣的想法」）
        # 右上角是「跳过」按钮，也没有 ▼ —— 有白底圆角横条组就**直接点第一个选项**。
        # 2026-11-06 修正：旧版在这里点右下角 ▼，但「周末三选一」（挑选时尚服饰 /
        # 去猫咖玩 / KTV练歌）这类页**右下角没有 ▼**（白色像素 0），点▼没反应 →
        # 3 秒后接管点屏幕中央 (0.5,0.5) → 正好压中第 2 个选项「去猫咖玩」
        # （用户看到「3选1选了第2个、恢复30精力」就是这个盲点，三局全中）。
        # 现在找到了选项组就直接点第一个，不再隔空点▼。
        vchoices = self._vertical_choice(texts)
        if (self.match_text(texts, "训练事件") is not None
                or self.has_story_arrow()
                or vchoices is not None):
            if vchoices is not None and not self.has_story_arrow():
                pick = min(vchoices, key=lambda t: t.y)
                self.log_info("竖向三选一（无▼）：直接点第一个「"
                              + pick.name.strip() + "」")
                self.click_box(pick, after_sleep=0.5)
                return True
            self.click(int(self.width * 0.96), int(self.height * 0.93), after_sleep=0.5)
            self.log_info("训练事件/剧情：点右下角 ▼")
            return True

        x1, y1, x2, y2 = Defs.region("dialog_text")
        lines = []
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                continue
            s = t.name.strip()
            # 台词是**纯文字长句**；属性面板的「弹跳B404/1350」含数字和斜杠，要排除
            if len(s) < 12:
                continue
            if any(ch.isdigit() for ch in s) or "/" in s:
                continue
            if any("\u4e00" <= ch <= "\u9fff" for ch in s):
                lines.append(s)

        if not lines:
            return False
        # 一次只点一下。主循环 ~0.4 秒后就会回来重新 OCR、重新判断，
        # 本身就是重试器；在 handler 里连点反而会把「点不动」藏起来，
        # 让 run() 的「点 3 次没变化 → debug」看门狗发现不了。
        # 旧版无条件连点 3 下，对话一下就能推进时，第 2、3 下会点在空处，
        # 甚至点到刚弹出来的下一层 UI（加固训练卡片 / 确定）上
        # （2026-10-26 用户观察：屏幕中间多点两下）。
        self.click(int(self.width * 0.5), int(self.height * 0.70), after_sleep=0.45)
        self.log_info(f"剧情对话：点一下推进（{lines[0][:16] if lines else '白字兜底'}）")
        return True

    def has_story_arrow(self):
        """右下角有没有白色 ▼（剧情/训练事件的推进箭头）。"""
        frame = self.frame
        if frame is None:
            return False
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = Defs.region("story_arrow")
        roi = frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
        if roi.size == 0:
            return False
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        white = (b > 225) & (g > 225) & (r > 225)
        return int(white.sum()) > 30

    def handle_lobby(self, texts):
        # 大厅判据：社团GO / 赛事，或底栏的「新人抽奖 / 转一转 / 冠军之路」。
        # 新版大厅的「社团GO」有时 OCR 不到，只靠它会在主界面空转（实训 2026-10-26）。
        if self.match_text(texts, ("赛事", "社团GO", "新人抽奖", "转一转", "冠军之路")) is None:
            return False
        if self.no_captain_mode:
            return self.stop_for_no_captain("大厅")
        # 专注不够就**主动**去补（用户 2026-11-04：「专注不够用的时候主动去恢复专注」）。
        # 只在大厅/训练剧本这类「顶栏有 +」的页面点，训练内的顶栏药丸没有「+」。
        if self.ensure_stamina():
            return True
        action = self.texts_in_region(texts, "main_action")
        target = self.match_text(action, "训练")
        if target is not None:
            ok = self.click_and_expect(
                int(target.x + target.width / 2),
                int(target.y + target.height / 2), ("训练剧本",), timeout=8)
        else:
            x, y = self.flow_point("lobby_training")
            ok = self.click_and_expect(x, y, ("训练剧本",), timeout=8)
        self.log_info("进入训练剧本" if ok else "点「训练」没反应")
        return True

    def handle_training_entry(self, texts):
        if self.match_text(texts, "训练剧本") is None:
            return False
        if self.no_captain_mode:
            return self.stop_for_no_captain("训练剧本")
        # 启程也要扣专注：先看够不够，不够就去恢复页喝饮料（禁止钻石）
        if self.ensure_stamina():
            return True
        ok = self.click_text_and_expect(texts, "启程", ("训练核心",), timeout=10)
        if not ok:
            # 兜底：万一 OCR 没认出「启程」，再用实测固定坐标
            x, y = self.flow_point("training_start")
            ok = self.click_and_expect(x, y, ("训练核心",), timeout=6)
        self.log_info("进入训练核心" if ok else "点「启程」没反应")
        return True

    def handle_captain(self, texts):
        """训练核心页：选队长。

        实机（2026-10-25）：右侧 2x2 四张卡，选中的人名字显示在底部中间
        （「SSR 佐久间 雨乃」）。配置 `指定队长` 填名字时，逐张点开比对，
        找到就点「下一步」；留空时沿用旧逻辑（点第一张，等级最高）。
        """
        # 必须**整词**：`match_text` 的「整段拼接」兜底会把训练日主界面里的
        # 「…都给我好好训练」+「核心评分提升！」拼成「训练核心」，于是主界面被当成
        # 选队长页 → 扫 16 张空卡后报「没有这个队长」停任务
        # （2026-11-13 12:44 实跑：同一局第一次选队长成功，第二天就误入并停任务）。
        # 真正的选队长页有一个独立的 `训练核心` 框（四次 OCR 都是 1.00 分）。
        if self.exact_text(texts, ("训练核心",)) is None:
            return False

        if self.no_captain_mode:
            return self.stop_for_no_captain("训练核心（选队长）")

        want = self.configured_captain()
        if want and not getattr(self, "_captain_filter_done", False):
            # 2026-11-13 用户流程：选队长前先「显示设定 → 筛选」，按稀有度
            # 和位置把卡池缩小，再走原来的逐张找名字（不在池里才停）。
            target = self.match_text(texts, "筛选")
            if target is not None:
                self.click_box(target, after_sleep=0.5)
                self._captain_filter_stage = "tab"
                self.log_info("选队长：打开筛选")
                return True
            self.log_warning("选队长：没认出「筛选」按钮 → 直接逐张找")
        if want and self.pick_captain_by_name(want):
            self._captain_matched_config = True
            return True
        if want:
            # 2026-10-30 用户口径：**指定队长找不到就停，不许回落**。
            # 旧版回落成「点当前光标位置」，翻页 4 屏后光标停在最后一张卡上，
            # 实际选到了佐久间雨乃（日志：16 张卡里没有近江奏 → 选了佐久间），
            # 用户明确反对这种自作主张。
            self._captain_matched_config = False
            self._abort_reason = f"队长池里没有「{want}」"
            seen = "、".join(getattr(self, "_captain_seen", []) or []) or "（一个都没认出）"
            self.log_error(f"队长池里没有「{want}」→ 停止任务，不自动选别人。"
                           f"这一轮读到的卡：{seen}", notify=True)
            self.alert("没找到指定的队长",
                       f"队长列表（4 屏共 16 张）里没有「{want}」。\n\n"
                       f"读到过的卡：{seen}\n\n"
                       "任务已停止，不会自动改选其他角色。\n"
                       "请刷新卡池或换一个队长后重跑。")
            return True

        self.flow_click("captain_card", after_sleep=0.5)
        self.log_info("选第一位队长（列表按等级降序，第一张最强）")
        # 选完队长后按钮可能移动，重新 OCR 再点「下一步」
        ok = self.click_button_and_expect("next", ("选择队友",), timeout=8)
        self.log_info("进入选择队友" if ok else "点「下一步」没反应")
        return True

    def handle_captain_filter(self, texts):
        """选队长前的「显示设定 → 筛选」：重置 → 按稀有度/位置缩池 → 确定。

        用户 2026-11-13 流程：启程 → 筛选 → 筛选页签 → 重置 →
        按队长稀有度点 SR/SSR + 位置 5 选 1 → 确定 → 原来的逐张找名字。
        分拍执行（每拍一个动作），避免面板动画/OCR 抖动时连点错。
        """
        if self.match_text(texts, "显示设定") is None:
            return False
        stage = getattr(self, "_captain_filter_stage", None)
        if stage is None:
            return False        # 用户手动打开的面板，不碰

        if stage == "tab":
            if self.match_text(texts, "显示顺序") is not None:
                tab = self._filter_text(texts, "筛选", region="settings_tabs")
                if tab is not None:
                    self.click_box(tab, after_sleep=0.3)
            self._captain_filter_stage = "reset"
            return True

        if stage == "reset":
            reset = self._filter_text(texts, "重置")
            if reset is not None:
                self.click_box(reset, after_sleep=0.4)
            self._captain_filter_stage = "choose"
            return True

        if stage == "choose":
            self._choose_captain_filters(texts)
            self._captain_filter_stage = "confirm"
            return True

        if stage == "confirm":
            confirm = self._filter_text(texts, "确定")
            if confirm is not None:
                self.click_box(confirm, after_sleep=0.5)
            else:
                cancel = self._filter_text(texts, "取消")
                if cancel is not None:
                    self.click_box(cancel, after_sleep=0.4)
                self.log_warning("选队长筛选：没找到「确定」，已取消面板")
            self._captain_filter_done = True
            self._captain_filter_stage = None
            self.log_info("选队长：筛选完成，回训练核心")
            return True
        return False

    def _choose_captain_filters(self, texts):
        """重置后是**全不选**：只勾队长对应的稀有度和位置。

        2026-11-14 实跑：近江奏 SR/分卫，旧逻辑按「重置后全选、取消其他」
        写，结果点 R/SSR 反而把它们勾上，SR 没勾。重置的真实语义是清空。
        """
        info = roster.lookup(self.configured_captain())
        rarity = (info.get("rarity") or "").upper()
        position = info.get("position") or ""
        if rarity in ("SSR", "SR", "R"):
            self._click_filter_text(texts, rarity)
        if position in ("控卫", "分卫", "小前", "大前", "中锋"):
            self._click_filter_text(texts, position)
        else:
            self.log_warning(
                f"选队长筛选：roster 里查不到「{self.configured_captain()}」"
                f"（rarity={rarity or '?'} position={position or '?'}）"
                "→ 只勾了稀有度，位置保持全不选")

    def _filter_text(self, texts, name, region=None):
        """筛选面板里的文字框；OCR 没认出时用截图实测坐标兜底。"""
        source = texts if region is None else self.texts_in_region(texts, region)
        target = self.exact_text(source, (name,))
        if target is not None:
            return target
        coords = None
        if name in Defs.CAPTAIN_FILTER["rarity"]:
            coords = Defs.CAPTAIN_FILTER["rarity"][name]
        elif name in Defs.CAPTAIN_FILTER["position"]:
            coords = Defs.CAPTAIN_FILTER["position"][name]
        elif name == "重置":
            coords = Defs.CAPTAIN_FILTER["reset"]
        elif name == "确定":
            coords = Defs.CAPTAIN_FILTER["confirm"]
        elif name == "筛选":
            coords = Defs.CAPTAIN_FILTER["tab"]
        if coords is None:
            return None
        return self.box_of_screen(coords[0], coords[1], coords[0], coords[1])

    def _click_filter_text(self, texts, name):
        """点复选框的小方块中心（文字本身不切换勾选）。

        稀有度/位置：OCR 找到标签文字时，点文字框左侧的小方块中心
        （截图实测偏移约 0.0137w）；OCR 没找到时用截图坐标兜底。
        """
        box = self._filter_text(texts, name)
        if box is None:
            self.log_warning(f"选队长筛选：没找到「{name}」")
            return False
        if (box.width > 0
                and (name in Defs.CAPTAIN_FILTER["rarity"]
                     or name in Defs.CAPTAIN_FILTER["position"])):
            from ok.feature.Box import Box
            cx = int(box.x - self.width * 0.0137)
            cy = int(box.y + box.height / 2)
            box = Box(x=cx - 2, y=cy - 2, width=4, height=4,
                      name=f"{name}复选框")
        self.click_box(box, after_sleep=0.15)
        return True

    def pick_captain_by_name(self, want, pages=4):
        """按名字挑队长；一屏 4 张没找到就**往下翻一页**继续找。

        2026-10-30 用户提醒：选队长列表能往下翻，春澄永远不在第一屏的 4 张里；
        旧版只读 4 张，找不到就回落第一张，等于永远选不到她。

        翻页方式（2026-10-30 第二次修正，用户实机反馈「几乎只移动了几个像素」）：
        **拖动优先**，用卡池脚本 tools/collect_ssr.py 验证过的参数
        （屏幕中央竖拖、拖 0.32 屏高、按住 0.1s 先让游戏进入 drag 状态）。
        滚轮在选队长页实测几乎不动，只留作兜底。

        判「有没有真的翻」不能看 OCR 指纹（_signature）：滚几像素时文字内容不变，
        但 OCR 的个别框会抖动，指纹照样变 → 旧版因此从不触发兜底。
        现在改成**比较整屏 4 张卡的名字**，完全相同就再加力拖一次。

        2026-10-31 修「匹配不上」：`read_captain_name` 读到的是
        「SR-亚理纱贾斯敏」（带稀有度前缀、间隔点被 OCR 吃掉），
        而配置/列表里写的是「亚理纱·贾斯敏」；旧判据 `want in name` 直接比字符串，
        于是 16 张卡全被当成「不是她」→ 报「队长池里没有」并停任务
        （logs/mcp/sento_summary.txt 就是这一条）。现在统一走 `labels.name_matches`。
        """
        # 2026-10-30 用户实机：接手的局，队长列表**停在最下面**，
        # 只往下翻会从底部继续到底，上面的角色全漏掉（这一轮就漏了近江奏）。
        # 所以先往上翻到顶，再逐屏往下找。
        self._captain_scroll_to_top()

        tried = 0
        prev_names = None
        seen = []
        for page in range(pages):
            page_names = []
            for idx in (1, 2, 3, 4):
                rx, ry = Defs.CAPTAIN_CARD[idx]
                self.click(int(self.width_of_screen(rx)), int(self.height_of_screen(ry)),
                           after_sleep=0.7)
                name = self.read_captain_name()
                page_names.append(name or "")
                tried += 1
                self.log_info(f"队长卡 {tried}（第{page + 1}屏）：{name or '（没认出名字）'}")
                if name and labels.name_matches(want, name):
                    ok = self.click_button_and_expect("next", ("选择队友",), timeout=8)
                    self.log_info(f"选定队长「{name}」"
                                  f"（匹配「{want}」）" + ("" if ok else "（点下一步没反应）"))
                    return True
            # 名字全读不到时 page_names 是 ["","","",""]，比较必然相等 →
            # 每屏都误报「翻页没动」（2026-11-10 实跑 3 条全是这种）。
            # 空结果不参与翻页判断，交给下面的正常翻页。
            if (prev_names is not None and any(page_names)
                    and page_names == prev_names):
                self.log_warning(
                    f"第{page + 1}屏和上一屏读到的 4 张卡完全相同 → 上次翻页没动，加大幅度重拖")
                self._captain_scroll_page(big=True)
            elif page < pages - 1:
                self.log_info(f"第{page + 1}屏没有「{want}」→ 列表往下翻")
                self._captain_scroll_page(big=False)
            prev_names = page_names
            seen.extend(page_names)
        self._captain_seen = [n for n in seen if n]
        return False

    def _captain_scroll_to_top(self, max_tries=6):
        """把队长列表翻回**顶部**（接手时可能停在最下面）。

        判据用**卡片区**的文字集合：往上拖一屏后集合不再变化，说明到顶。
        不能再用整屏集合 —— 2026-11-10 19:07 实跑：顶栏专注值/日期每拍在变，
        整屏集合永远不相等，拖满 6 次都判不到顶（和 _signature 同一个坑）。
        不点卡（点卡每屏要多花 3 秒）。
        """
        x = int(self.width * 0.82)
        prev = self._captain_list_texts()
        for i in range(max_tries):
            # 手指从上往下拖 = 内容向下滚 = 显示更上面的角色
            self.swipe(x, int(self.height * 0.30), x, int(self.height * 0.62),
                       duration=0.30, after_sleep=0.7)
            cur = self._captain_list_texts()
            if cur == prev:
                self.log_info(f"队长列表已翻到顶（往上翻 {i} 次）")
                return
            prev = cur
        self.log_warning(f"往上翻了 {max_tries} 次还没到顶，按当前位置继续")

    def _captain_list_texts(self):
        """卡片区的文字集合，只用来判断列表有没有真的滚动。

        卡片区一个文字都读不到时（纯头像卡），退回整屏但**排除顶栏**：
        顶栏专注值/日期每拍在变，不排除就永远判不到顶（这局的原 bug）。
        """
        texts = self._ocr_region()
        picked = self.texts_in_region(texts, "captain_list")
        if picked:
            return frozenset(t.name.strip() for t in picked)
        return frozenset(t.name.strip() for t in texts
                         if (t.y + t.height / 2) / self.height >= 0.12)

    def _captain_scroll_page(self, big=False):
        """选队长列表往下翻一页：拖动为主，滚轮兜底。

        big=True 时拖得更远（上一屏和这一屏完全相同，说明普通幅度不够）。
        """
        # 2026-10-30 实机：拖拽的起点必须落在**右侧卡片区**（x≈0.82w），
        # 屏幕中央是角色立绘，列表不响应。实测同一组参数：
        #   x=0.5w  → 文字 y 位移 0（完全没动）
        #   x=0.82w → 按住拖动时列表就跟手，松手不回弹（像素差 6.7/8.7，卡名换人）
        x = int(self.width * 0.82)
        if big:
            self.swipe(x, int(self.height * 0.72), x, int(self.height * 0.20),
                       duration=0.35, after_sleep=0.8)
        else:
            self.swipe(x, int(self.height * 0.62), x, int(self.height * 0.30),
                       duration=0.30, after_sleep=0.7)


    def handle_team(self, texts):
        if self.match_text(texts, "选择队友") is None:
            return False
        if self.no_captain_mode:
            return self.stop_for_no_captain("选择队友")
        auto = self.match_text(texts, "自动选择")
        if auto is not None:
            self.click_box(auto, after_sleep=0.7)
            self.log_info("队友：自动选择")
        # 自动选择后队伍从 1 人变 5 人，底部面板会整体下移 40~50px，
        # 必须重新 OCR「下一步」，不能复用点击前的坐标（实训 2026-10-26）。
        # 2026-10-29：再等 0.8 秒让面板动画走完，否则新帧读到的还是旧位置，
        # 于是每次都要等 8 秒超时、白打一条 stall5s。
        # 2026-10-30 实测新流程：队长 → 队友 → 选择笔记 → 器材 → 确认编队。
        # 这里原来只等「选择器材」，而下一屏其实是「选择笔记」→ 每次白等 8 秒
        # 超时 + 5 秒兜底（用户感受到的「发呆十几秒」）。两个都算成功。
        self.sleep(0.8)
        ok = self.click_button_and_expect("next", ("选择笔记", "选择器材"), timeout=8)
        if not ok:
            x, y = self.flow_point("next_step")
            ok = self.click_and_expect(x, y, ("选择笔记", "选择器材"), timeout=5)
        self.log_info("进入选择笔记/器材" if ok else "点「下一步」没反应")
        return True

    def handle_note_select(self, texts):
        """「选择笔记」页：先点「自动选择」，再点「下一步」。

        用户 2026-10-29 截图：标题「选择笔记」，右边一网格笔记（评分/属性），
        底部「重置 / 自动选择」，右下黄色「下一步」。
        口径变更（2026-10-30）：**先自动选择，再下一步**（旧口径是不选直接过）。
        判据用标题整词，避免和「器材」页混。
        """
        # 必须**整词**：确认编队页有「未选择笔记 / 请携带笔记一起训练吧」，
        # 包含匹配会把那一页也当成选择笔记页，反复点下一步（2026-10-29 实机）。
        if self.exact_text(texts, ("选择笔记",)) is None:
            return False
        # 「确认编队」页有「未选择笔记 / 请携带笔记一起训练吧」——
        # 阶段 2：收口到签名表（`confirm_team`），各页面只声明一次。
        if page_signatures.page_has(self, texts, "confirm_team"):
            return False
        if self.no_captain_mode:
            return self.stop_for_no_captain("选择笔记")

        # 2026-10-30 用户反馈「选择笔记页一直有多余点击」：
        # 页面切换有动画，handler 每拍都会重新命中同一页 → 自动选择被反复点、
        # 下一步连点好几次。用两个运行期标记限流（reset_run_state 会清零）：
        #   * 自动选择：整局只点 1 次（点完卡片高亮会变，指纹去重不可靠）；
        #   * 下一步：连续最多 2 次，之后只等切页。
        if not getattr(self, "_note_auto_done", False):
            auto = self.exact_text(texts, ("自动选择",))
            if auto is not None:
                self.click_box(auto, after_sleep=0.5)
                self._note_auto_done = True
                self.log_info("选择笔记：自动选择")
                texts = self._ocr_region()
            else:
                self.log_info("选择笔记：没找到「自动选择」，直接下一步")

        tries = getattr(self, "_note_next_tries", 0)
        if tries >= 2:
            self.log_debug("选择笔记：本页已点过下一步，等切页（不再点）")
            return True

        target = self.exact_text(texts, labels.BUTTONS["next"])
        if target is not None:
            x = int(target.x + target.width / 2)
            y = int(target.y + target.height / 2)
        else:
            x, y = self.flow_point("next_step")
        self._note_next_tries = tries + 1
        ok = self.click_and_expect(x, y, ("确认编队", "选择器材"), timeout=6)
        self.log_info("选择笔记：下一步" + ("（已切页）" if ok else "（等切页）"))
        return True

    def handle_equipment(self, texts):
        if self.match_text(texts, "选择器材") is None:
            return False
        if self.no_captain_mode:
            return self.stop_for_no_captain("选择器材")
        auto = self.match_text(texts, "自动选择")
        if auto is not None:
            self.click_box(auto, after_sleep=0.7)
            self.log_info("器材：自动选择")
        self.sleep(0.8)          # 同上：等卡片刷新完再找「下一步」
        # 2026-10-30：新流程里器材页之后是「选择笔记」，不是直接「确认编队」。
        # 只等确认编队会白等满 8 秒超时（用户实测「发呆 16 秒」就是这里 +
        # 后面 5 秒兜底）。两个目标都算成功。
        ok = self.click_button_and_expect("next", ("选择笔记", "确认编队"), timeout=8)
        if not ok:
            x, y = self.flow_point("next_step")
            ok = self.click_and_expect(x, y, ("选择笔记", "确认编队"), timeout=5)
        self.log_info("器材：下一步" if ok else "点「下一步」没反应")
        return True

    def handle_confirm_team(self, texts):
        """确认编队：开始训练会扣专注且中途取消不返还，默认要用户先允许。"""
        if self.match_text(texts, "确认编队") is None:
            return False
        if self.no_captain_mode:
            return self.stop_for_no_captain("确认编队（开始训练）")
        if not self.config.get("允许开始训练"):
            self.log_info("已禁用自动开始训练，停在这里等你确认")
            return True
        # 注意：确认框正文里也有「开始训练」四个字（整句会被 OCR 成一个框），
        # 必须用**整词**匹配，否则会点到正文中间 (511,923)，按钮反而没点到。
        target = next((t for t in texts if t.name.strip() == "开始训练"), None)
        if target is not None:
            self.click_box(target, after_sleep=0.8)
        else:
            self.flow_click("confirm_start", after_sleep=0.8)
        # 新的一局：队长可能换了 —— 配置里填了队长就按它写流派，否则等日历解析
        self.reset_style()
        # 只有「真的选到了配置里那个队长」才套用配置流派；
        # 选的是别的卡（配置队长不在池子里）就等日历读实际队长。
        if getattr(self, "_captain_matched_config", False):
            self.apply_configured_captain()
        # 选择笔记页的限流标记清零，否则下一轮不会自动选择
        self._note_auto_done = False
        self._note_next_tries = 0
        self.log_info("开始训练（已扣专注，不可退）｜流派：" + self.style
                      + ("" if self.style_known else "（待日历识别队长）"))
        return True

    def _vertical_choice(self, texts):
        """竖排三选一的选项组（剧情三选一，右上角有「跳过」）。

        与 handle_event_choice 的竖向分支同源，但这里只**找**不点：
        剧情三选一在 story_arrow 之前处理，点屏幕右下角即可推进（点了就选第一个）。
        返回选项列表（>=3 个才算）。
        """
        if self.match_text(texts, "训练计划") is not None:
            return None
        if self.match_text(self.texts_in_region(texts, "top_right"), "跳过") is None:
            return None
        ui_words = {"下一场", "下一步", "跳过", "确定", "取消",
                    "关闭", "继续", "领取", "开始训练"}
        vopts = []
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            s = t.name.strip()
            if s in ui_words or not s or self.is_decor(s):
                continue
            # 竖排选项是**居中**的一列（实测 story-choice: cx=0.50）；贴边的列是
            # 表格列或字幕（周结算左列 cx≈0.26、周末前对话字幕 cx≈0.68）。
            if not (0.38 <= cx <= 0.62 and 0.15 <= cy <= 0.75):
                continue
            if len(s) < 3 or len(s) > 20:
                continue
            if not self._has_white_band(t):
                continue
            vopts.append(t)
        if len(vopts) < 3:
            return None
        groups = []
        for t in sorted(vopts, key=lambda x: x.y):
            placed = False
            for g in groups:
                gx = (g[-1].x + g[-1].width / 2) / self.width
                cx = (t.x + t.width / 2) / self.width
                if abs(gx - cx) < 0.10 and 0 < (t.y - g[-1].y) / self.height < 0.25:
                    g.append(t)
                    placed = True
                    break
            if not placed:
                groups.append([t])
        groups = [g for g in groups if len(g) >= 3]
        return max(groups, key=len) if groups else None
