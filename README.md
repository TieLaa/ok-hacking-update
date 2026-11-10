# ok-hacking

《篮球少女：飞跃巅峰》Windows 客户端的养成自动化，基于 [ok-script](https://github.com/ok-oldking/ok-script) 构建；感谢 [ok-oldking](https://github.com/ok-oldking) 开源。

- 「hacking」 就是「打手犯规」的意思，双关解放双手，很直白了。
- 纯vibe coding，代码质量有问题不是我的问题。
- 目前只跑通了训练流和扭蛋流，事件的识别远没有达到100%。
- 训练优先选有经理的或者队友多的，道具强化看运气。
- 随机事件默认选第一个；有数值的会判断数学期望。
- 根据精力和零花钱决定周末去处和消费，有逻辑但不多。
- 小游戏无法代劳，还得亲自打3星才能自动消费。
- 刷分效果跟手操也差不太多了，反正我是一点都不手操；定向强化等我找个群里老师学一下逻辑。
- 适合没时间又菜又爱玩的我们。
- 强制前台抢鼠标是因为Unity的问题，这一点跟ok-Endfield是一样的。
- 已经设置了键鼠操作3秒内不会抢鼠标，要干什么要抓紧。

[English](README_en.md) · [文档](docs/index.md)

> 本项目与游戏官方无关，仅供学习交流。使用自动化工具可能违反游戏用户服务条款，存在封号风险，后果自负。

## 以下都是AI自己写的

## 它能做什么

- **自动训练**：自动跑完一局养成——训练日选科目、周末活动、比赛日处理，循环到赛季结束
- **监控页**：一屏看当前周次、精力、六维、分数预测；布局改 `configs/monitor_layout.json`、样式改 `assets/ui/monitor.*.qss`，存盘即生效不用重启
- **29 项任务配置**：失败率上限、休息精力阈值、指定队长、公园选项、商店购买策略、扭蛋品质优先、比赛日处理、技能标签优先……全部在 GUI 里逐项可调，不用改代码
- **卡死自恢复**：点击后界面指纹不变会累计计数，同一天超限就接管；接管 3 次仍不动就停下存证
- **MCP 开发循环**：改任务源码 → `reload_tasks` → 不重启 GUI 直接验证

## 环境要求

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11 |
| 游戏 | 客户区 16:9，最低 1280×720（开发/实测在 1920×1080） |
| 权限 | **管理员**。游戏进程是管理员权限，非管理员进程发不进输入 |
| 安装路径 | **纯英文**。ok-script 拒绝非 ASCII 路径 |
| 窗口 | **必须可见**。完全遮挡或最小化时截图后端拿不到新帧（日志 `no frame for 10 sec`） |

## 安装

### 方式一：安装包（推荐）

到 [Releases](https://github.com/TieLaa/ok-hacking/releases) 下载：

- `ok-hacking-win32-China-setup.exe` —— 依赖走国内镜像
- `ok-hacking-win32-Global-setup.exe` —— 依赖走 PyPI 官方源

安装时系统会要管理员权限（`uac: true`）。首次启动会初始化 OCR 模型（OpenVINO），需要等一会儿。

**第一次用先手动开一次游戏**：新装的应用不知道游戏装在哪（`configs/devices.json` 的 `pc_full_path` 是空的），
点【启动】会提示 `Game path does not exist, Please open game manually!`。
手动打开游戏后，应用刷新设备时会自己记住路径，之后就能一键拉起。别装在纯中文路径下（ok-script 会拒绝）。

### 方式二：从源码跑

```powershell
git clone https://github.com/TieLaa/ok-hacking.git
cd ok-hacking
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-deps -r requirements.txt
```

`requirements.txt` 是 pip-compile 生成的完整锁定文件，所以安装必须带 `--no-deps`。

## 运行

三个入口：

| 入口 | 用途 |
| --- | --- |
| `main.py` | 正式模式，不画识别框 |
| `main_debug.py` | 调试模式，多日志 + 识别框 |
| `main_gui_mcp.py` | GUI + MCP 桥（`config['mcp']['enabled']=True`），开发热重载用 |

```powershell
# 普通权限：只能截图，不能操作游戏（可以先看界面）
.\.venv\Scripts\python.exe main_debug.py

# 管理员权限：能操作游戏（在管理员终端里跑同一条命令）
Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "main_debug.py" -WorkingDirectory (Get-Location) -Verb RunAs
```

## 使用

1. 启动后先到「截图方式」页确认抓到了游戏画面（截一张图看看）；
2. 到「任务」页展开「自动训练」，按需改配置。**队长留空 = 快速接手**：只跑你手动开好的局，遇到开局页面会停下，不替你开局；
3. 点开始，切到「监控」页看进度；
4. 卡住时先读文本：`logs/debug/<时间>_<tag>/` 里有 `ocr.txt`（文字+坐标）和 `actions.txt`，够判断卡在哪一步；只有 OCR 漏判（按钮没识别出来）时才需要看 `frame.jpg`。

运行时的配置以 `configs/TrainingTask.json` 为唯一真相（GUI 改的就是它）。

## 目录结构

| 路径 | 作用 |
| --- | --- |
| `src/config.py` | 应用配置：窗口、截图/输入后端、任务注册、MCP |
| `src/tasks/training/` | 养成循环的全部业务代码（flow / parsers / scoring / handlers / places） |
| `src/tasks/training/handlers/__init__.py` | handler 的**唯一**优先级表 |
| `src/interaction/GirlsInteraction.py` | 本游戏专用的真鼠标输入后端（管理员 + 前台置前） |
| `src/ui/` | 监控页（`MonitorTab.py` 取数据，`monitor_view.py` 渲染） |
| `configs/` | 运行时配置（不入库） |
| `docs/` | 工程文档；`docs/shots/` 是 handler 回归的期望帧 |
| `tools/` | 运维与调试脚本（抓帧、看日志、MCP 探针）；不放业务判据 |
| `logs/` | 运行日志、调试包（不入库） |

## 开发

```powershell
# 结构/文案/日期入口/配置单一来源等门禁，exit 0 才算过
.\.venv\Scripts\python.exe tools\check_handlers.py --cache

# 单元测试
Get-ChildItem tests\*.py | ForEach-Object { .\.venv\Scripts\python.exe -m unittest $_.FullName }
```

- 架构：`docs/ARCHITECTURE.md`
- 工程约定（业务代码只放 `src/tasks/training/`）：`docs/ENGINEERING.md`
- MCP 开发循环（改 handler 不重启 GUI）：`docs/MCP_DEV.md`
- 踩坑复盘的提示词手册：`docs/PROMPT_PLAYBOOK.md`

## 许可证

本项目代码采用 [MIT](LICENSE)。

用到的第三方组件（各自遵循其许可）：

- [ok-script](https://github.com/ok-oldking/ok-script) —— Apache License 2.0 + Commons Clause 及附加条款，自动化框架本体（[项目主页](https://ok-script.com/)）
- [PyAppify](https://github.com/ok-oldking/pyappify) —— GPL-3.0，安装包里的启动器与更新器
- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) / PySide6 —— GUI 组件

## 免责声明

本项目与游戏官方无关，仅供学习与个人研究。使用自动化工具可能违反游戏用户服务条款，可能导致账号被处罚，风险与后果由使用者自负。请勿用于商业代练、账号出租、外挂制作等用途。
