"""中文文案表（key 必须与 en.py 一一对应）。"""

CATALOG: dict[str, str] = {
    "app.name": "Scripto",
    "app.tagline": "本地批量转录与字幕翻译",
    "app.gui_placeholder": "Scripto 图形界面将在里程碑 M5 提供，当前请先使用 `scripto-cli`。",
    "cli.description": "Scripto 命令行工具",
    "cli.info.help": "显示数据位置与当前设置",
    "cli.info.header": "Scripto {version}",
    "cli.info.config": "配置文件： {path}",
    "cli.info.data_dir": "数据目录： {path}",
    "cli.info.log_dir": "日志目录： {path}",
    "cli.info.language": "界面语言： {value}",
    "cli.language.not_set": "（未设置，默认英文）",
}
