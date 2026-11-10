"""输入链路自检：按**文字**点一下，再对比前后画面。

为什么按文字而不是坐标：
2026-10-25 实测发现 `PostMessage` 完全无效、真实点击有效，而硬编码像素坐标会因为
UI 布局/窗口位置而点偏（实测点「训练」点成了「赛事」）。所以正式流程一律走
「OCR 找文字框 → 点框中心」。这个任务就是验证这条链路。

用法：任务页点「开始」。默认点「赛事」——它会打开玩法目录，按返回即可恢复，
不会消耗任何游戏资源。
"""

import numpy as np

from ok import og
from src.tasks.BaseGirlsTask import BaseGirlsTask


class InputTestTask(BaseGirlsTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "输入测试"
        self.description = "OCR 找到指定文字并点击，对比前后画面，验证输入链路"
        self.default_config.update({
            "目标文字": "赛事",
            "期望出现文字": "玩法目录",
            "点击后等待(秒)": 1.5,
            "差异阈值(%)": 1.0,
            "坐标模式X": 0,
            "坐标模式Y": 0,
            "打印所有OCR": True,
        })
        self.config_description.update({
            "目标文字": "留空则改用下面的坐标点击；推荐用文字，能同时验证 OCR 和点击",
            "坐标模式X": "只在目标文字为空时生效（客户区像素坐标）",
            "打印所有OCR": "把这一帧识别到的所有文字和它们的框打到日志，用来核对坐标",
        })

    def run(self):
        self.begin_attempt()
        text = str(self.config.get("目标文字", "")).strip()
        wait = float(self.config.get("点击后等待(秒)", 1.5))
        threshold = float(self.config.get("差异阈值(%)", 1.0))

        before = self.next_frame()
        if before is None:
            self.log_info("拿不到画面，检查截图方式/游戏窗口", notify=True)
            return False

        interaction = "?"
        try:
            interaction = og.device_manager.interaction.__class__.__name__
        except Exception:
            pass

        if text:
            texts = self._ocr_region()
            if self.config.get("打印所有OCR", True):
                for t in sorted(texts, key=lambda b: (b.y, b.x)):
                    self.log_info(f"OCR: 「{t.name}」 @({t.x},{t.y},{t.width},{t.height})")
            target = self.match_text(texts, text)
            if target is None:
                self.log_info(f"没找到文字「{text}」，检查游戏是否停在对应界面", notify=True)
                self.screenshot("input_test_not_found", before)
                return False
            self.log_info(f"[{interaction}] 点击文字「{target.name}」 框=({target.x},{target.y},{target.width},{target.height})")
            self.click_box(target)
        else:
            x = int(self.config.get("坐标模式X", 0))
            y = int(self.config.get("坐标模式Y", 0))
            self.log_info(f"[{interaction}] 点击坐标 ({x}, {y})")
            self.click(x, y)

        self.sleep(wait)

        after = self.next_frame()
        if after is None:
            self.log_info("点击后拿不到画面", notify=True)
            return False
        self.screenshot("input_test_after", after)

        # 首选判据：点完之后**该出现的文字**出现了（3D 大厅一直在动，靠像素差会误判）
        expect = str(self.config.get("期望出现文字", "")).strip()
        if expect:
            if self.find_text(expect) is not None:
                self.info_set("输入测试", f"生效（出现「{expect}」）")
                self.log_info(f"点击生效：界面出现「{expect}」", notify=True)
                return True
            self.info_set("输入测试", "无效（未出现预期文字）")
            self.log_info(f"点击无效：没等到「{expect}」", notify=True)
            return False

        if before.shape != after.shape:
            self.log_info("分辨率变化，点击大概率生效", notify=True)
            self.info_set("输入测试", "生效（画面结构变化）")
            return True

        changed = (np.abs(before.astype(np.int16) - after.astype(np.int16)).max(axis=2) > 24).mean() * 100
        self.log_info(f"画面变化 {changed:.2f}%（阈值 {threshold}%）")
        if changed >= threshold:
            self.info_set("输入测试", f"生效 {changed:.1f}%")
            self.log_info("点击生效", notify=True)
            return True
        self.info_set("输入测试", f"疑似无效 {changed:.1f}%")
        self.log_info("画面几乎没变：可能点空了，或这个按钮不改变画面", notify=True)
        return False
