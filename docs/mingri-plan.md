# Scripto — mingri 项目规划蓝图

> 此文件是 mingri 中 "Scripto" 项目的里程碑/任务蓝图。
> 已于 2026-07-16 通过 API 同步进 mingri（7 个里程碑、42 个任务）；后续以 mingri 为准，本文件只作初始蓝图存档。

## 里程碑

| code | name | version |
|------|------|---------|
| M1 | 骨架与基础设施 | v0.1 |
| M2 | 媒体与引擎层 | v0.2 |
| M3 | 批处理流水线 | v0.3 |
| M4 | 翻译层 | v0.4 |
| M5 | GUI | v0.5 |
| M6 | 启动器与发布 | v0.6 |
| M7 | 性能与质量 | v1.0 |

## 任务

### M1 骨架与基础设施（v0.1）

| 任务 | type | 优先级 |
|------|------|--------|
| 初始化仓库脚手架：uv + pyproject + src 布局 + pytest | chore | p0 |
| 实现 config 服务（平台数据目录、原子读写、默认值合并） | feat | p0 |
| 实现事件总线 events.py（进度/状态/日志，多订阅者） | feat | p0 |
| i18n 框架与 zh/en 界面文案（面板中英文） | feat | p0 |
| 日志系统：滚动文件日志 + 内存环形缓冲 | feat | p1 |

### M2 媒体与引擎层（v0.2）

| 任务 | type | 优先级 |
|------|------|--------|
| ffmpeg 封装：探测、抽音频（16k mono）、超时与 kill、临时文件清理 | feat | p0 |
| 定义 TranscribeEngine 协议（transcribe/release/进度回调） | feat | p0 |
| 实现 mlx-whisper 引擎（含 per-file clear_cache 内存治理） | feat | p0 |
| 实现 faster-whisper 引擎（CPU int8 / CUDA 自动选择） | feat | p0 |
| 模型注册表 + 本地探测 + 一键下载/删除（HF 缓存单次扫描） | feat | p1 |
| 平台自动选择引擎（Apple Silicon → mlx，其余 → faster-whisper） | feat | p1 |

### M3 批处理流水线（v0.3）

| 任务 | type | 优先级 |
|------|------|--------|
| scanner：多目录混合输入、递归、去重、路径规范化（shell 转义/iCloud） | feat | p0 |
| 流水线调度器：extract 预提取(有界2) → transcribe 串行 → translate 队列 | feat | p0 |
| stop/cancel 语义贯穿全部阶段（5 秒内回到空闲） | feat | p0 |
| 输出规则：同目录默认 / export 可选 / .en.srt .cn.srt 后缀映射 | feat | p0 |
| 内存模式开关：balanced / low（先转录后翻译） | feat | p1 |
| 失败记录与单文件容错（不阻断批次） | feat | p1 |
| 超长文件分段转录 + 时间轴拼接 | feat | p2 |
| History JSON 索引：原子写入、记录每次产出 | feat | p1 |

### M4 翻译层（v0.4）

| 任务 | type | 优先级 |
|------|------|--------|
| SRT 解析/重建/marker 批次协议（复刻并修正旧实现） | feat | p0 |
| Ollama 客户端：显式 num_ctx、keep_alive 管理、流式读取、超时 | feat | p0 |
| 批次管理：默认 ~40 块/3000 字符、失败二分重试、单块兜底 | feat | p0 |
| 语言注册表：zh/en + 扩展接口（新语言只加一条映射） | feat | p1 |
| Ollama 模型管理：探测、pull 进度、删除 | feat | p1 |

### M5 GUI（v0.5）

| 任务 | type | 优先级 |
|------|------|--------|
| Flet 应用壳：主视图 + 底部固定操作栏（开始/停止/总进度/ETA） | feat | p0 |
| 文件列表：增量渲染、节流刷新、每行状态与进度 | feat | p0 |
| 输入区：粘贴 / 文件选择 / osascript 文件夹兜底 / debounce 扫描 | feat | p0 |
| 界面中英文切换：设置项即时生效，覆盖全部面板文案 | feat | p0 |
| 错误直显：失败行内原因 + 一键重试 + 失败自动提示 | feat | p1 |
| 事件日志面板：环形上限 + 过滤 + 导出 | feat | p1 |
| 模型管理器 UI（whisper 双引擎 + Ollama，一键下载） | feat | p1 |
| History 页：读取 JSON 索引、打开预览、"已被删除"态、清理失效 | feat | p1 |
| 设置页 + 首次运行向导 + 暗色模式 | feat | p1 |

### M6 启动器与发布（v0.6）

| 任务 | type | 优先级 |
|------|------|--------|
| macOS Scripto.app 壳（bundle + 启动脚本 + 图标） | feat | p0 |
| Windows Scripto.bat / .vbs 启动器 | feat | p0 |
| 首启 bootstrap：uv sync + doctor（ffmpeg/引擎/Ollama 检查与指引） | feat | p0 |
| README 与用户文档（安装、启动、FAQ） | docs | p1 |

### M7 性能与质量（v1.0）

| 任务 | type | 优先级 |
|------|------|--------|
| benchmark 脚本：批量吞吐与阶段耗时分解 | test | p1 |
| 内存曲线验证：连跑 20 长文件 RSS 平稳 | test | p0 |
| 翻译单批成功率统计（目标 >95%） | test | p1 |
| e2e 冒烟：CLI 与 GUI 主流程 | test | p1 |
| 500 文件批次 UI 流畅度验证 | test | p2 |
