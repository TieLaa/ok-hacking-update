"""养成（训练）自动化的任务入口。

分层（详见 docs/ENGINEERING.md）：

    training/flow.py      单拍状态机、存证、卡死检测
    training/parsers.py   只读解析（OCR/取色），不点击
    training/scoring.py   纯打分
    training/handlers/    按界面域分的 handler，__init__.py 里的 HANDLERS 是唯一优先级表

本文件只负责：任务元数据、默认配置、状态初始化。
"""

from collections import deque

from src.tasks.BaseGirlsTask import BaseGirlsTask
from src.tasks.training import config_spec
from src.tasks.training.flow import TrainingFlowMixin
from src.tasks.training.parsers import TrainingParsersMixin
from src.tasks.training.scoring import TrainingScoringMixin
from src.tasks.training.daycycle import DayCycleMixin
from src.tasks.training.systemui import SystemUIHandlers
from src.tasks.training.interactions import InteractionsMixin
from src.tasks.training.places import (ArcadePlace, MapPlace, ParkPlace,
                                       RestaurantPlace, ShopPlace, StreetPlace)
from src.tasks.training.handlers import HANDLERS
from src.tasks.training.handlers.popup import PopupHandlers
from src.tasks.training.handlers.stamina import StaminaHandlers
from src.tasks.training.handlers.training import TrainingHandlers
from src.tasks.training.handlers.weekend import WeekendHandlers
from src.tasks.training.handlers.match import MatchHandlers
from src.tasks.training.handlers.entry import EntryHandlers
from src.tasks.training.handlers.talent import TalentHandlers
from src.tasks.training.handlers.wheel import WheelHandlers
from src.tasks.training.handlers.unmarked import UnmarkedHandlers


class TrainingTask(TrainingFlowMixin, TrainingParsersMixin, TrainingScoringMixin,
                   DayCycleMixin, SystemUIHandlers, InteractionsMixin,
                   MapPlace, StreetPlace, RestaurantPlace, ParkPlace,
                   ArcadePlace, ShopPlace,
                   PopupHandlers, TrainingHandlers, WeekendHandlers,
                   MatchHandlers, EntryHandlers, TalentHandlers, WheelHandlers,
                   StaminaHandlers, UnmarkedHandlers, BaseGirlsTask):

    def load_config(self):
        """加载配置前先迁移老存档：旧键名搬值 / 旧选项换值 / 语义变了的旧值丢弃。

        时机不能改：`Config.__init__` → `verify_config()` 会**丢掉**所有
        `default_config` 里没有的键，旧键在那之后就找不回来了。
        见 `training/config_spec.migrate_saved_config`。
        """
        try:
            changed = config_spec.migrate_saved_config(self.__class__.__name__)
            if changed:
                self.log_info("配置迁移：" + "；".join(changed))
        except Exception as e:                      # noqa: BLE001
            self.log_warning(f"配置迁移跳过（{type(e).__name__}: {e}）")
        super().load_config()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动训练"
        self.description = "自动跑完一局养成：训练 / 周末活动 / 比赛日跳过，循环到赛季结束"
        self.default_config.update(config_spec.build_default_config())
        self.config_type.update(config_spec.build_config_type())
        self.config_description.update(config_spec.build_description())
        self._steps = 0
        self.init_match_state()
        self._last_state = None
        self._picked_subject = False
        self._picked_index = 0
        self._match_flags = {"manual_fixed": False, "fast_clicked": False, "last_fast_at": 0.0}
        # 「点了没反应」检测：用户口径 —— 连续点 3 次界面没变化就要开始 debug
        self._recent_actions = deque(maxlen=10)
        self._no_change = 0             # 连续几次点击后界面指纹没变
        self._sig_after_click = None    # 上一次点击后那一帧的指纹
        self._stuck_rounds = 0          # 已经被判定「点不动」的轮数
        # 周末状态：按「第N周+星期X」区分，跨天自动重置
        self._weekend = {"date": None, "shop_checked": False, "bought": False,
                         "park_choice": None}
        # 进店只在「比赛日、进比赛之前」（训练流/商店流各一次）+ 第 7 周以后的
        # 「商店上新」；_shop_day 记当天已经逛过的日期键。
        self._shop_day = None
        self._shop_at = 0.0
        self._shop_nobadge_key = None   # 第 7 周以后：这一天已经报过「没上新」了
        # 科目页：记录上次探测结果，防止同一天重复探测
        self._subject_cache = {"date": None, "best": None}
        # 商店：第一次遇到只存证，不买东西（等复盘确认界面）
        self._shop_seen = False
        # 失败率超限时置 True：退出科目页后强制休息
        self._force_rest = False
        # 最近一次点击的墙钟时间：只做复盘（点击流水 / 空档统计），
        # 卡住判据**不再看时间**（2026-11-04 改成数点击/数操作，见 flow.day_stuck_step）。
        self._last_click_at = 0.0
        # 卡住判据的计数器（2026-11-04 用户口径：按「同一天」算，日期一更新就归零）：
        #   `_day_key`    = 当前日期键 (周, 星期)；
        #   `_day_clicks` = 这一天点了多少下（判据②：超过 `单日最大点击数` 就接管）；
        #   `_day_fixes`  = 这一天接管过几次（到 3 就停）；
        #   `_sig_changed_at` = 界面指纹最近一次变化的时刻（判据①：点击 3 秒无响应）。
        self._day_key = None
        self._day_clicks = 0
        self._day_fixes = 0
        self._sig_changed_at = 0.0
        # 当前流派（用户 2026-10-29）：**只由队长决定**。配置里填了队长，
        # run()/点开始训练时直接查表写入；留空则由第一个训练日点日历解析。
        self._style = None
        self._style_name = None         # 日历上读到的队长名（日志/复盘用）
        # 2026-11-01：识别队长的计数器搬到 daycycle（_style_return_tries /
        # _style_calendar_tries），旧的 _style_probe_* 随 handle_style_probe 删了。

    # 顺序见 training/handlers/__init__.py
    HANDLERS = HANDLERS
