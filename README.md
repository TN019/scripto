# Scripto

本地、免费的批量音视频转录 + 字幕翻译工具（macOS / Windows）。把课程视频、讲座录音变成 `.srt` / `.txt` / `.vtt` / `.json`，可选生成中文翻译字幕。**全程本地运行，不上传任何数据。**

- 转录：mlx-whisper（Apple Silicon，Metal 加速）/ faster-whisper（Windows 及其他，CPU int8 或 CUDA）
- 翻译：本地 [Ollama](https://ollama.com)，中 ⇄ 英（`.en.srt` / `.zh.srt`），架构可扩展更多语言
- 输出默认写在**源文件旁边**；支持跨目录批量、断点跳过、历史回溯

*(English: local, free batch transcription & subtitle translation. Install steps below are language-neutral — follow the commands.)*

---

## 安装（一次性）

**macOS**

```bash
brew install ffmpeg uv          # 系统依赖：音频抽取 + Python 环境管理
brew install ollama             # 可选：需要中文字幕翻译时
git clone https://github.com/TN019/scripto.git && cd scripto
```

**Windows**（PowerShell）

```powershell
winget install ffmpeg
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# 可选翻译：winget install Ollama.Ollama
git clone https://github.com/TN019/scripto.git; cd scripto
```

依赖（mlx-whisper / faster-whisper / flet 等）会在**首次启动时由 uv 自动安装**，无需手动 pip。

## 启动

**macOS — 双击启动**：先构建一次 .app 壳（干净透明，内部只是一段可读脚本）：

```bash
bash launchers/macos/build_app.sh    # 生成 dist/Scripto.app
```

把 `dist/Scripto.app` 拖进「应用程序」，以后双击即可。也可以直接命令行启动：`uv run scripto`

**Windows — 双击启动**：双击 `launchers\Scripto.bat`（想不显示黑窗口就用 `Scripto.vbs`，可右键发送快捷方式到桌面）。命令行等价：`uv run scripto`

首次打开会有两步向导（界面语言 + Whisper 模型），模型在**设置 → 模型管理**里一键下载。

## 使用

1. **转录页**：粘贴文件/文件夹路径（每行一个，支持混合来源），或用「选择文件 / 选择文件夹」
2. 需要翻译就打开底栏的**翻译**开关并选目标语言（需要 Ollama 在运行：`ollama serve`）
3. 点**开始**——底栏实时显示进度、当前文件与预计剩余时间；随时可停止（当前文件完成后干净退出）
4. 产物写在源文件旁：`lecture.mp4 → lecture.en.srt`（+ `lecture.zh.srt`）；想集中输出就在设置里填导出目录
5. **历史页**：每个文件一个入口，可切换语言查看；缺失语言可就地翻译；文件被删会明确标注

命令行同样全功能：

```bash
uv run scripto-cli run ~/Videos/课程 --model large-v3-turbo --translate --target zh
uv run scripto-cli doctor        # 环境体检：ffmpeg / 引擎 / 模型 / Ollama
uv run scripto-cli info          # 数据与日志位置
```

## 常见问题

**"Operation not permitted"（macOS）** — 终端/应用没有目标文件夹的访问权限：系统设置 → 隐私与安全性 → 文件和文件夹，给你的终端或 Scripto 授权；或把文件移出 桌面/文稿/下载。

**iCloud 文件转录失败** — 文件还是云端占位符：Finder 里右键 →「立即下载」后重试（界面会明确提示这种情况）。

**翻译按钮提示 Ollama 未运行** — 先 `ollama serve`，再在 设置 → 模型管理 里 pull 一个模型（推荐 `qwen3:8b`，内存紧张用 `qwen3:4b`）。

**16GB 内存机器同时转录+翻译很吃力** — 设置 → 内存模式 切到「低内存」：转录全部完成、whisper 卸载后才开始翻译，任一时刻只有一个大模型在内存里。

**已有字幕的文件被跳过** — 默认不覆盖；要重新生成就在设置里打开「覆盖已有输出」。

## 开发

```bash
uv sync && uv run pytest                       # 快测试（不下载模型）
SCRIPTO_ENGINE_SMOKE=1 uv run pytest           # + 真实引擎冒烟（下载 tiny 模型）
SCRIPTO_OLLAMA_SMOKE=1 uv run pytest           # + 真实 Ollama 翻译冒烟
```

架构与设计决策见 [docs/PLAN.md](docs/PLAN.md)；路线图蓝图存档在 [docs/mingri-plan.md](docs/mingri-plan.md)。

## License

[MIT](LICENSE)
