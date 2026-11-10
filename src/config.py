import os

import numpy as np
from ok import ConfigOption

from src.interaction.GirlsInteraction import GirlsInteraction
from src.util.log_noise import install_quiet_filters

# ok 的 TaskExecutor 每个调度拍都打一行 `get_task_by_class ...`（整个任务列表的
# repr），debug 模式下会把控制台刷满。过滤器只丢这一行，见 src/util/log_noise.py。
# 落点放在这里：三个入口脚本和 `python -m ok ... --config src.config:config`
# 都会 import 本模块，是唯一能覆盖全部启动路径的单点（细节见该模块 docstring）。
install_quiet_filters()

version = "dev"
#不需要修改version, Github Action打包会自动修改

gui_config = {
    'type': 'qt',
    'window_size': {
        'width': 1200,
        'height': 800,
        'min_width': 600,
        'min_height': 450,
    },
}

key_config_option = ConfigOption('Game Hotkey Config', { #全局配置示例
    'Echo Key': 'q',
    'Liberation Key': 'r',
    'Resonance Key': 'e',
    'Tool Key': 't',
}, description='In Game Hotkey for Skills')


def make_bottom_right_black(frame): #可选. 某些游戏截图时遮挡UID使用
    """
    Changes a portion of the frame's pixels at the bottom right to black.

    Args:
        frame: The input frame (NumPy array) from OpenCV.

    Returns:
        The modified frame with the bottom-right corner blackened.  Returns the original frame
        if there's an error (e.g., invalid frame).
    """
    try:
        height, width = frame.shape[:2]  # Get height and width

        # Calculate the size of the black rectangle
        black_width = int(0.13 * width)
        black_height = int(0.025 * height)

        # Calculate the starting coordinates of the rectangle
        start_x = width - black_width
        start_y = height - black_height

        # Create a black rectangle (NumPy array of zeros)
        black_rect = np.zeros((black_height, black_width, frame.shape[2]), dtype=frame.dtype)  # Ensure same dtype

        # Replace the bottom-right portion of the frame with the black rectangle
        frame[start_y:height, start_x:width] = black_rect

        return frame
    except Exception as e:
        print(f"Error processing frame: {e}")
        return frame

config = {
    'custom_tasks':True, # enable creating and editing custom tasks
    'debug': False,  # Optional, default: False
    'gui': gui_config,
    # 「监控」页：自定义 Tab，布局在 configs/monitor_layout.json，
    # 样式在 assets/ui/monitor.*.qss，两者改完存盘即生效（不用重启）。
    'custom_tabs': [["src.ui.MonitorTab", "MonitorTab"]],
    'mcp': {'enabled': False, 'port': 8765, 'artifact_directory': 'logs/mcp'}, # MCP 开发服务器, GUI 内默认关闭, 用 python -m ok mcp 单独起
    'config_folder': 'configs', #最好不要修改
    'global_configs': [key_config_option],
    # 'screenshot_processor': make_bottom_right_black, # 可选：截图时修改 frame（本游戏暂不需要）
    'gui_icon': 'icons/icon.png', #窗口图标, 最好不需要修改文件名
    'wait_until_before_delay': 0,
    'wait_until_check_delay': 0,
    'wait_until_settle_time': 0, #调用 wait_until时候, 在第一次满足条件的时候, 会等待再次检测, 以避免某些滑动动画没到预定位置就在动画路径中被检测到
    'ocr': { #可选, 使用的OCR库
        'lib': 'onnxocr',
        'auto_simplify': True, #自动繁体转简体, 需要ppocrv5等可以识别繁体的库
        'params': {
            'use_openvino': True,
        }
    },
    'windows': {  # Windows游戏请填写此设置
        'exe': ['k3_windows_ob.exe'],
        # optional, if set, will search the exe only
        'hwnd_class': 'UnityWndClass', #增加重名检查准确度
        'interaction': [GirlsInteraction], # 只保留实测可用的真鼠标后端（2026-10-29）：PostMessage 对本游戏完全无效，Pynput/PyDirect 需要前台且不能配合 GirlsInteraction 的置前逻辑，列在首页只会让人选错
        'capture_method': ['WGC'],  # 只保留实测可用的 WGC（2026-10-29）：BitBlt 系对 Unity 全屏会拿到黑帧/旧帧；WGC 支持遮挡捕获
        'check_hdr': False, #当用户开启AutoHDR时候提示用户, 但不禁止使用
        'force_no_hdr': False, #True=当用户开启AutoHDR时候禁止使用
        'require_bg': True # 要求使用后台截图
    },
    'adb': {  # 模拟器或Android设备请填写此设置, mumu模拟器使用原生截图和input,速度极快. 其他模拟器和真机使用adb,截图速度较慢
        # optional, if set, will start the pacakge and ensure installed
        #'packages': ['com.abc.efg1', 'com.abc.efg1']
    },
    # 'browser': {  # 浏览器游戏请填写此设置；windows、adb、browser 至少配置一个，也可以同时配置多个
    #     'url': 'https://example.com/game',
    #     'nick': 'Browser',
    #     'resolution': (1280, 720),
    # },
    'start_timeout': 120,  # default 60
    'supported_resolution': {
        'ratio': '16:9', #支持的游戏分辨率
        'min_size': (1280, 720), #支持的最低游戏分辨率
        'resize_to': [(2560, 1440), (1920, 1080), (1600, 900), (1280, 720)], #可选, 如果非16:9自动缩放为 resize_to
    },
    'links': {  # 「关于」页与更新卡片里显示的链接
        'default': {
            'github': 'https://github.com/TieLaa/ok-hacking',
            'download': 'https://github.com/TieLaa/ok-hacking/releases',
        }
    },
    'screenshots_folder': "screenshots", #截图存放目录, 每次重新启动会清空目录
    'gui_title': '篮球少女 OK',  #窗口名
    'template_matching': { # 可选, 如使用OpenCV的模板匹配
        'coco_feature_json': os.path.join('assets', 'coco_annotations.json'), #coco格式标记, 需要png图片, 在debug模式运行后, 会对进行切图仅保留被标记部分以减少图片大小
        'default_horizontal_variance': 0.002, #默认x偏移, 查找不传box的时候, 会根据coco坐标, match偏移box内的
        'default_vertical_variance': 0.002, #默认y偏移
        'default_threshold': 0.8, #默认threshold
    },
    'version': version, #版本
    'my_app': ['src.globals', 'Globals'], #可选. 全局单例对象, 可以存放加载的模型, 使用og.my_app调用
    'onetime_tasks': [  # 用户点击触发的任务
        ["src.tasks.TrainingTask", "TrainingTask"],
        ["src.tasks.InputTestTask", "InputTestTask"],
        ["ok", "DiagnosisTask"],
    ],
    'trigger_tasks': [  # 后台常驻、按间隔自动触发的任务
        ["src.tasks.QuickMatchTask", "QuickMatchTask"],
    ],
}
