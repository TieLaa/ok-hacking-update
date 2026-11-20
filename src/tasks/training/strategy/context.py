"""各事件的只读输入（ctx）。

ctx = 这一帧重新读一遍的当场数据，不跨天保存、不可变。
每个事件一个：只放该事件判断需要的东西，不放整个任务对象。

为什么不用 dict：字段名拼错时要立刻报错（namedtuple 少一个字段就构造失败，
多一个字段就取不到），而不是在方法里静默拿到 None。
"""

from typing import NamedTuple


class ParkContext(NamedTuple):
    """公园选什么（`Style.pick_park`）的输入。

    两个日期字段**故意不合并**：
      - `week` / `day` 来自 `current_day()`（有缓存，读不到任一就是 None）；
      - `top_week` 来自 `top_bar()`（只读到周也算）。
    现有逻辑里「海边前」用前者、「最后一周」用后者，合并会改变行为。
    """

    style: str
    week: int | None             # 缓存日期：海边前判断用
    day: str | None
    top_week: int | None         # 顶栏这一帧的周数：最后一周判断用
    stamina: int | None
    stamina_max: int | None
    vacation_target: int | None  # 第3周海边目标；不是那一周 = None
    mate_boats: int | None       # 划船队友数；没数 = None
    mate_picnic: int | None      # 野餐队友数
    mate_how: str                # 队友怎么数的（日志用：脸 / 深蓝）
    boat_race: bool              # 「划船比赛！」贴在划船选项上
    eat_threshold: int           # 配置「训练流-周末野餐缺口」


class WorkContext(NamedTuple):
    """餐厅这张工接不接（`Style.accept_work_card`）的输入。"""

    style: str
    allow_fallback: bool         # True = 主计划趟；False = 急招插队趟
    week: int | None
    gap: int                     # 精力缺口（上限−当前）；读不到按 0
    row_gap: int                 # 配置「训练流-周末划船缺口」
    mate_counts: tuple[int, ...] # 两张卡的队友数


class RestContext(NamedTuple):
    """练还是休（`Style.should_rest`）的输入。"""

    style: str
    day: str | None
    stamina: int | None
    threshold: int               # 这一天的精力阈值（`flow.rest_threshold`）


class WeekendContext(NamedTuple):
    """今天去哪（`Style.weekend_places`）的输入。"""

    style: str
    week: int | None
    day: str | None
    money: int | None
    stamina: int | None
    stamina_max: int | None
    visited: frozenset[str]      # 今天去过哪些地点
    gacha_spent: bool            # 今天在步行街买到卡了没有
    row_gap: int                 # 配置「训练流-周末划船缺口」


class VenueContext(NamedTuple):
    """今天先去哪个（`Style.prefer_venue`）的输入。"""

    style: str
    day: str
    week: int | None
    money: int | None
    places: tuple[str, ...]
