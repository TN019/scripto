# Scripto

本地、免费、跨平台（macOS / Windows）的批量音视频转录 + 字幕翻译工具。

- 转录：mlx-whisper（Apple Silicon）/ faster-whisper（Windows 及其他），多模型可选，一键下载
- 翻译：本地 Ollama，当前支持中 ⇄ 英（`.en.srt` / `.cn.srt`），可扩展
- 输出默认写回源文件同目录；支持跨目录批量输入
- 历史记录：轻量 JSON 索引，可回溯每次产出

**当前状态：规划阶段。** 完整产品与技术规划见 [docs/PLAN.md](docs/PLAN.md)，路线图任务蓝图见 [docs/mingri-plan.md](docs/mingri-plan.md)。

前身项目 my-transcriptor 仅作参考，不迁移代码；其架构教训已沉淀在 PLAN.md §2。
