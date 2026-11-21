"""配置项的**唯一声明**：键名 / 默认值 / 类型 / GUI 控件 / 说明。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）第 5 步。

**为什么要有这一层**（2026-11-01 实测的三个问题）：

    ① **默认值写在两处**。`default_config` 声明了 1300，而调用处又写了一遍
       `self.config.get("属性减半门槛", 1300)` —— 那个 1300 **永远不生效**
       （config 一定命中 default_config）。但它让人以为「默认值在这里」，
       改它没用。实测 21 处调用点里 20 处是这种死代码。

    ② **有配置项用户在 GUI 里配不到**。`扭蛋清空精力阈值`（weekend.py 读了）
       和 `技能按键`（当时由模板演示任务读了，任务已下线）**没在 default_config 里声明** →
       GUI 上没有这一项，用户改不了。

    ③ **有配置项声明了但没人读**。`扭蛋优先属性` 在 default_config 里，
       但 `src/` 里**从来没读过** —— GUI 上是个无效选项，
       而且说明文字还写着老口径（「会消耗所有精力」，现在是危险的旧文案）。

**这一层只做「声明 + 生成 + 门禁」，不参与运行时** ——
`ok.util.config.Config` 已经有 `verify_config` 做校验，
我再写一套就是第二份真相。

用法：

    TrainingTask.default_config  ← 从这里生成
    tools/check_handlers.py       ← 门禁（默认值只许写在这里）
"""

from src.tasks.training import prep
from src.tasks.ui import labels

# 配置项类型（对齐 ok-script 的 config_type）
BOOL = "bool"
INT = "int"
FLOAT = "float"
STR = "str"
LIST = "list"
DROP = "drop_down"


class Spec:
    """一个配置项的声明。"""

    __slots__ = ("key", "default", "type", "desc", "options", "hidden")

    def __init__(self, key, default, type=BOOL, desc="", options=None,
                 hidden=False):
        self.key = key
        self.default = default
        self.type = type
        self.desc = desc
        self.options = options
        self.hidden = hidden

    def config_type(self):
        """ok-script 的 config_type 条目（None = 用默认控件）。

        `hidden=True` 的项只写进 default_config（代码能读、老存档值保留），
        GUI 不渲染（ok-script 的 ConfigCard 认 `hidden` 标记）。
        """
        out = {}
        if self.hidden:
            out["hidden"] = True
        if self.type == DROP:
            out.update({"type": "drop_down", "options": list(self.options or [])})
        return out or None


# ---------- 训练 ----------
TRAINING = [
    Spec("失败率上限(%)", 1, INT, "训练科目页按分数排第一的科目判：失败率高于这个值（或读不到）就休息，不再退而求其次练低分科目；第 4 周周三还要附加训练后精力 ≥ 50"),
    Spec("指定队长", "", STR,
         "填角色名（如「真崎辉世」「佐久间」）→ 按这个角色的流派跑，开局就定好、不点日历；"
         "留空 = 快速接手：进训练后点一次日历，按「XX的训练计划」里队长的流派跑"),
]

# ---------- 周末 / 地点 ----------
WEEKEND = [
    Spec("训练流-周末野餐缺口", 70, INT,
         "训练流周末：缺口（精力上限−当前精力）≥ 这个值去野餐；< 这个值去划船。"
         "野餐基础回 70，所以默认 70；其他流派不读这个键"),
    Spec("训练流-周末划船缺口", 30, INT,
         "训练流周末：缺口 < 这个值就不再进公园，改去餐厅打工（公园只能回 30，缺口小会溢出）；"
         "这时的零用钱留给商店的牛奶/秘籍，周末不去步行街/游戏厅"),
    # 2026-11-18：开关保留，但不在 GUI 展示（玩家口径：不显露，不是删功能）。
    # 代码仍读这个键；默认 True，老存档里改过 False 的照样生效。
    Spec("允许进步行街", True, BOOL, "关掉后周末不去步行街抽扭蛋", hidden=True),
    # 2026-11-18：游戏厅开关是小游戏流专用，键名加前缀。
    Spec("小游戏流-允许进游戏厅", True, BOOL,
         "关掉后小游戏流周末不去游戏厅玩小游戏"),
]

# ---------- 商店 ----------
# 2026-11-08 用户口径：商店/扭蛋/小游戏**各自独立**配置，不再共用一个「购买策略」。
# 2026-11-12 用户口径（在 11-11 的类别顺序上补品质）：
# 现金 → 牛奶 → 精力 → 其余按 彩 → 金 → 紫（蓝/白不买）→ 同品质档位
# （教程/指导书 → 手册 → 教科书 → 属性；2026-11-13 定稿）。
SHOP = [
    Spec("商店购买策略", "红包→彩→金→紫", DROP,
         "只作用于商店：现金（老板的馈赠/大乐透）→ 牛奶 → 精力 → "
         "其余按品质 彩 → 金 → 紫（蓝/白不买），同品质内再按 "
         "器材指导教程/指导书 → 器材手册 → 教科书 → 属性"
         "（属性内 全属性 → 需求属性 → 专属饮料 → 奇妙秘籍 → 其他）；"
         "精力商品要过三类门槛（溢出 / 缺口 ≤ 100 / 精力过半都拒买）；"
         "店内顺序对所有流派相同，流派只决定进店时机；"
         "扭蛋流/小游戏流前 6 周只拿必拿。不买 = 只逛不买",
         options=["红包→彩→金→紫", "不买"]),
]

# ---------- 游戏厅（小游戏）----------
ARCADE = [
    # 2026-11-18：游戏厅付费是小游戏流专用，键名加前缀（预留开关）。
    Spec("小游戏流-游戏厅付费（预留开关）", True, BOOL,
         "游戏厅没有「跳过」时，花钱（≥64 零用钱）买一个「自动」小游戏换属性；"
         "关掉就沿用旧逻辑直接退出。小游戏流专用"),
]

# ---------- 扭蛋（步行街）----------
# 2026-11-01 用户口径：训练等级提升后解锁了**彩色**扭蛋，
# 品质优先级 彩色 > 金色 > 紫色；同分先「增强款」再有队友（都相同比价格）。
GACHA = [
    Spec("扭蛋品质优先", ["彩", "金", "紫"], LIST,
         "扭蛋只买这些品质，靠前的优先：默认 彩 > 金 > 紫（彩色是训练等级提升后解锁的）；"
         "同等情况先「增强款」、再有队友加成的，都相同才买便宜的；蓝卡/其他一律不买"),
    Spec("扭蛋清空精力阈值", 30, INT,
         "精力低于这个值才买「消耗/清空所有精力」的扭蛋（拿精力换属性）；精力够时一律不买"),
]

# ---------- 比赛 / 技能 ----------
MATCH = [
    # 2026-11-18：训练任务不需要选，固定走「能跳过就跳过，跳不了进比赛快进」。
    # 仍然保留内部键给旧存档/QuickMatchTask 用，但 GUI 不显示。
    Spec("比赛日处理", "自动（先跳过，跳不了就进比赛加速）", DROP,
         "没打赢过的比赛不能跳过，只能进比赛后点左下角快进",
         options=list(labels.MATCH_DAY_OPTIONS), hidden=True),
    # 2026-11-18：技能标签只在品质/道具都打平时才起作用，训练任务不暴露；
    # 键和读取逻辑保留（QuickMatchTask 也有自己的同名配置）。
    Spec("技能标签优先", ["得分手"], LIST,
         "比赛内三选一：优先带这些标签的技能", hidden=True),
    # 2026-11-18：只给监控页「分数预测」用，不影响自动训练；
    # 键和读取逻辑保留，训练任务 GUI 不显示。
    Spec("技能-彩色", 1, INT,
         "监控页「分数预测」用：技能品质数量（彩/金/白），这里填**开局自带**的数量，"
         "本局三选一拿到的由 info[\"skill_gained\"] 实时累加。"
         "评分 = 六维总和 + 75×彩 + 50×金 + 25×白（docs/score-model）", hidden=True),
    Spec("技能-金色", 0, INT, "同上（开局自带的金技能数量）", hidden=True),
    Spec("技能-白色", 1, INT, "同上（开局自带的白技能数量）", hidden=True),
]

# ---------- 准备页（队友 / 笔记 / 器材）----------
# 2026-11-21：模式由监控页的「准备页模式」卡片写回；任务配置卡里隐藏，
# 避免玩家在两个地方各改一次。
PREP = [
    Spec(prep.KEY, prep.MODE_AUTO, DROP,
         "选择队友 / 选择笔记 / 选择器材时："
         "自动选择 = 直接点自动选择；半自动 = 有空位停下来等用户；"
         "预配置 = 阶段 1 占位（等同自动选择）",
         options=list(prep.MODES), hidden=True),
]

# ---------- 流程 / 运行时 ----------
FLOW = [
    # 2026-11-18：内部卡住保护阈值，训练任务 GUI 不暴露；代码默认 3 秒。
    Spec("点击无响应判定(秒)", 3, INT,
         "卡住判据①（用户 2026-11-04 口径：「点击 3 秒无响应要立刻接管」）：界面文字/"
         "指纹超过这么多秒没变化、而且这段时间里**点过** → 判定没响应 → 存证本局 debug/、"
         "点屏幕中央接管继续；同一天接管 3 次还推不动才停", hidden=True),
    # 2026-11-18：内部防死循环阈值，训练任务 GUI 不暴露；用户要求从 100 调到 50。
    Spec("单日最大点击数", 50, INT,
         "卡住判据②（用户 2026-11-04 口径：「同一天点击超过 100 下要立刻接管」）："
         "同一天累计点击超过这么多下、日期还没更新 → 判定原地空转 → 存证 + 点中央接管；"
         "**日期一更新计数器就归零**。默认为 50（2026-11-18 用户调整）", hidden=True),
    # 2026-11-18：速度调节是内部参数，训练任务 GUI 不暴露；默认 0.3 秒。
    Spec("操作间隔(秒)", 0.3, FLOAT,
         "每拍之间的等待", hidden=True),
    # 2026-11-18：只给 MCP 调试注入临时上限用，正常玩家不需要；GUI 隐藏，默认 0。
    Spec("单局最大操作数", 0, INT,
         "**默认 0 = 不限制**（用户 2026-11-04 口径：「2000 步的限制不要了」）。"
         "只给 MCP 的 tick harness（`tools/mcp/training_tick.py`）注入用：填 N 就只跑 N 拍", hidden=True),
    Spec("空闲等待(秒)", 3, INT, "你动鼠标或敲键盘时脚本立刻停手，等你停手这么多秒后才重新拉起游戏继续挂机（期间绝不抢前台）。0 = 不等（用户操作时也照抢）"),
    Spec("保存调试存证", True, BOOL,
         "把 events/unknown/unmark/debug 的截图与 txt 落盘到 logs/<局ID>/，并写 clicks.jsonl。"
         "**安装包默认关**（源码直跑默认开）：这些文件很占地方（实测约 350 MB/小时），"
         "玩家侧不需要；真需要排查时在界面上打开。关闭后旧目录仍按「日志保留天数」清理"),
    Spec("日志保留天数", 3, INT,
         "每次开局清一次：删掉这么多天之前的**整局存证目录**（logs/<局ID>/ 与 "
         "logs/mcp/<run_id>/<局ID>/）。存证图很占地方（实测约 350 MB/小时），"
         "默认 3 天；0 = 永不清理。只删带 events/unknown/unmark/debug 子目录的局目录，"
         "clicks.jsonl、reports、mcp/connections 都不动"),
]

# ---------- 多角色队列（2026-11-04 引入；2026-11-07 降级为外部参数）----------
# 2026-11-07 用户口径：**CLI / MCP 启动时传进来的那个队列串**是一次性参数，
# 不是配置项 —— 「多角色队列」「队长队列」两个配置键**废弃**（留着只为不炸
# 老存档的 verify_config，任务不再读）；`最大局数` 仍生效（兜底防止无限刷）。
# 达标判据不变 = 注册球员页该球员的评分（按位置对列）；钻石恢复依旧写死禁止。
# 注入通道见 `tools/mcp/training.py`（logs/mcp/next_captain.json 的 "queue" 字段）。
#
# 2026-11-09 用户口径（纠正上面被扩大解释的部分）：一次性只限**启动参数**，
# 界面第二栏的几个控件是**设置**，必须落盘、启动装载 ——
# `指定队长` / `队长下拉选择` / `刷到目标分` / `目标分`（快速比赛在
# `configs/QuickMatchTask.json` 的 `_enabled`，由 TriggerTask 自己落盘）。
# 目标分队列不再由界面写内存参数，而是 `TrainingFlowMixin.captain_queue_spec()`
# 从这几个键派生；`_launch_queue`（CLI 注入）优先级更高。
QUEUE = [
    # 2026-11-18：已废弃、无人读取；用户口径「先隐藏，备注待删除」。
    Spec("多角色队列", False, BOOL,
         "（已废弃，待删除，仅兼容旧存档）多角色改为外部注入参数：在监控页指定队长即单跑，"
         "队列由 agent 通过 MCP 参数传入", hidden=True),
    Spec("队长队列", "", STR,
         "（已废弃，待删除，仅兼容旧存档）队列串由 agent 通过 MCP 参数传入，这里留空", hidden=True),
    Spec("刷到目标分", False, BOOL,
         "监控页「刷到目标分」勾选：勾上后反复练同一队长，直到一局结束的训练评分"
         "≥「目标分」才停（复用多角色队列的提前终止；不勾 = 跑完一局就结束）。"
         "**落盘**，重启后勾选状态保留。"),
    Spec("目标分", 0, INT,
         "「刷到目标分」的分数线（训练评分）。0 = 没填：勾着也只按单队长一直练，"
         "不会自动停。**落盘**，重启后回填到输入框。"),
    # 2026-11-18：只是监控页下拉框记忆，不是业务配置；GUI 隐藏，键保留。
    Spec("队长下拉选择", "", STR,
         "（界面记忆）队长下拉框上次选中的角色。勾选框没勾时也记住，"
         "重启后下拉直接回到这个人，不会一勾就退回列表第一个。"
         "业务不读这个键 —— 真正的队长是「指定队长」。", hidden=True),
    Spec("最大局数", 5, INT,
         "多角色队列的兜底：连跑这么多局还没全部达标就停下，防止无限刷"),
    Spec("自动恢复专注", True, BOOL,
         "大厅/训练剧本页的专注低于「开一局所需 30」时，自动点顶栏的「+」进专注恢复页喝饮料。"
         "只喝带「使用」按钮的饮料（+30 / +60），**绝不用钻石购买**。"
         "（专注和训练里的精力是**两个资源**：专注用于「开」训练、顶栏上限 150；"
         "精力用于每一次训练、训练条上限 130，只能靠商品/扭蛋/事件/休息恢复。"
         "2026-11-15 用户口径；旧键名「自动恢复精力」由 LEGACY_KEYS 搬过来）"),
]

ALL = TRAINING + WEEKEND + SHOP + GACHA + ARCADE + MATCH + PREP + FLOW + QUEUE


def _packaged():
    """安装包运行时 pyappify 会注入 app_version；源码直跑为空/None。"""
    try:
        import pyappify
        return bool(getattr(pyappify, "app_version", "") or "")
    except Exception:                                # noqa: BLE001
        return False


def build_default_config():
    """生成 `default_config` 的内容（键 → 默认值）。

    唯一的动态默认：安装包里「保存调试存证」默认关（玩家侧不写 350 MB/小时的存证），
    源码直跑默认开（开发要看存证）。
    """
    cfg = {s.key: s.default for s in ALL}
    if _packaged():
        cfg["保存调试存证"] = False
    return cfg


def build_config_type():
    """生成 `config_type` 的内容（只有下拉框需要显式声明）。"""
    out = {}
    for s in ALL:
        ct = s.config_type()
        if ct:
            out[s.key] = ct
    return out


def build_description():
    """生成 `config_description` 的内容（键 → 说明）。"""
    return {s.key: s.desc for s in ALL if s.desc}


# ---------- 老键名 → 新键名（2026-11-02 规范用词）----------
# 旧配置里的键名改过好几次，改名后值不能丢：`Config.verify_config()` 只保留
# `default_config` 里有的键，旧键连同值会被直接扔掉，所以必须在 Config
# 构造**之前**把它搬过去。一个键有多个旧名时按顺序取第一个存在的值。
#
# 2026-11-15 用户口径（纠正）：**专注和精力是两个资源** ——
#   专注：用来「开」一局训练，顶栏上限 150，靠顶栏「+」→ 专注恢复页的饮料补；
#   精力：每次训练扣的那个，训练条上限 130，只能靠商品/扭蛋/事件/休息补。
# 所以专注相关的配置键叫「专注」，训练相关的叫「精力」，两者不再混用。
LEGACY_KEYS = {
    # 2026-11-15：这两个键实际管的是专注，旧名带「精力」。
    "自动恢复专注": ("自动恢复精力",),
    # 2026-11-14：「休息精力阈值」升为策略参数（`strategy/params.json` 的
    # `rest.stamina_threshold`），不再是配置项，旧键由 verify_config 丢弃。
    "扭蛋清空精力阈值": ("扭蛋清空体力阈值",),
    # 2026-11-08：商店/扭蛋/小游戏分开配置，旧的通用「购买策略」只服务过商店。
    "商店购买策略": ("购买策略",),
    # 2026-11-18：两个缺口阈值是**训练流专用**，键名加前缀避免误以为全流派生效。
    "训练流-周末野餐缺口": ("周末野餐缺口",),
    "训练流-周末划船缺口": ("周末划船缺口",),
    # 2026-11-18：游戏厅开关是**小游戏流专用**，同样加前缀。
    "小游戏流-允许进游戏厅": ("允许进游戏厅",),
    # 2026-11-18：游戏厅付费也是小游戏流专用（预留开关）。
    "小游戏流-游戏厅付费（预留开关）": ("游戏厅付费",),
}

# 旧选项值 → 新选项值（键必须先出现在 LEGACY_KEYS 里，或本身已是新键）。
# 2026-11-08：商店改成固定顺序，旧策略一律落到「红包→彩→金→紫」。
LEGACY_VALUES = {
    "商店购买策略": {
        "只买彩金紫": "红包→彩→金→紫",
        "只买器材手册+": "红包→彩→金→紫",
        "买最贵": "红包→彩→金→紫",
    },
    # 2026-11-18：「总是跳过」没实现（和自动一样），下拉里隐藏；旧值落到自动。
    "比赛日处理": {
        "总是跳过": "自动（先跳过，跳不了就进比赛加速）",
    },
}

# 旧键只删除、不搬值：语义单位变了，旧值没有对应值。
# 2026-11-11 起「周末野餐阈值」表示「缺口 ≥ N」（旧口径是「周日精力 < N」），
# 旧值 60 是绝对精力，直接搬过去会被当成「缺口 ≥ 60」。
LEGACY_DROP = {
    "周末野餐阈值": "训练流-周末野餐缺口",
    # 2026-11-18：恢复专注下限不再存在，固定为开一局所需 30。
    "恢复专注下限": "恢复专注固定按开一局所需 30 判断",
    "恢复精力下限": "恢复专注固定按开一局所需 30 判断",
}


def migrate_saved_config(name, folder="configs"):
    """把 `configs/<name>.json` 里的旧键名改成新键名。

    返回变更说明（列表）：空列表 = 没改。读不到文件（第一次跑）/
    JSON 坏了都当「不用迁移」。
    """
    import json
    import os

    path = os.path.join(os.getcwd(), folder, f"{name}.json")
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(saved, dict):
        return []
    changed = []
    for new_key, old_keys in LEGACY_KEYS.items():
        # 新键已经有值（用户改过）就保留新键，旧键只负责清掉
        old_present = [k for k in old_keys if k in saved]
        values = [saved.pop(k) for k in old_present]
        if not values:
            continue
        changed.append(f"「{' / '.join(old_present)}」已改写为「{new_key}」，值保留")
        if new_key not in saved:
            saved[new_key] = values[0]
    for new_key, mapping in LEGACY_VALUES.items():
        if saved.get(new_key) in mapping:
            old_value = saved[new_key]
            saved[new_key] = mapping[old_value]
            changed.append(f"「{new_key}」的旧选项「{old_value}」已改为「{saved[new_key]}」")
    for old_key in LEGACY_DROP:
        if old_key in saved:
            old_value = saved.pop(old_key)
            changed.append(
                f"「{old_key}」的旧值 {old_value} 已丢弃（语义从精力改为缺口）")
    if not changed:
        return []
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(saved, f, ensure_ascii=False, indent=4)
    except OSError:
        return []
    return changed
