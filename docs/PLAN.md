# Scripto — 产品与技术规划

> 本地、免费、跨平台的批量音视频转录 + 字幕翻译工具。
> my-transcriptor 的完全重写（原仓库仅作参考，不迁移代码）。

- 仓库：https://github.com/TN019/scripto.git
- 平台：macOS（Apple Silicon 优先）+ Windows
- 原则：**纯本地、零付费、零部署**；性能和内存行为是一等公民

---

## 1. 产品需求（来自 2026-07-16 立项讨论）

| # | 需求 | 说明 |
|---|------|------|
| R1 | 转录免费、多模型 | 支持多个 Whisper 系模型；本地没有时**一键辅助下载** |
| R2 | Mac / Windows 双平台 | 不做网站部署，纯本地；可接受"壳 .app"，但必须**一键启动且方法干净** |
| R3 | 翻译 | 当前只需 中文 ⇄ 英文，架构上**保留语言与后端的可扩展性** |
| R4 | 输出规则 | 默认输出到**源文件同目录**（除非用户显式 export 到别处）；支持批量、支持来自不同目录的混合输入；命名为 `.en.srt` / `.cn.srt` 这样的语言后缀 |
| R5 | 历史可追溯 | 轻量化：**单个 JSON** 作为地址索引；History 页读取该索引 → 找到当年的 srt 等文件在 app 内打开显示；文件已删除则显示"已被删除" |
| R6 | 性能与效率 | 转录/翻译要兼顾性能、效率与质量；要有缓存与请求管理；**不是一个个排队，也不是一堆一起轮换**——目标是阶段化流水线 |
| R7 | 界面中英文 | 面板（GUI 全部文案）支持中文/英文，设置中切换即时生效 |

---

## 2. 从 my-transcriptor 吸取的教训（设计约束）

重写不是为了换名字，是为了修掉旧架构里结构性的问题。以下每一条都转化为 Scripto 的硬性设计约束：

| 旧问题 | Scripto 约束 |
|--------|--------------|
| MLX Metal 缓存在批处理中持续累积，内存只升不降 | 每个文件转录完成后强制 `mx.clear_cache()` + GC；引擎实现必须提供 `release()` 钩子 |
| whisper + qwen3 双模型并行常驻，16GB 机器压力大 | 内存模式开关：`balanced`（流水线并行）/ `low`（先转录后翻译，任一时刻单模型驻留）；管理 Ollama `keep_alive` |
| Ollama 批次（100 块 / 8000 字符）大概率超默认 `num_ctx=4096`，滑窗丢 marker → 对齐失败 → 反复分裂重试，隐性极慢 | 请求显式设置 `num_ctx`；默认批次缩小（约 40 块 / 3000 字符）；批次大小可自适应 |
| GUI 事件日志每条消息全量重建控件（O(n²)），文件列表每个 tick 全量重建 | UI 更新必须**增量**且**节流**（≤4 Hz）；事件日志内存内环形上限（完整日志落盘） |
| 路径输入每个键击同步扫盘（最长阻塞 120s） | 所有磁盘/网络探测一律后台线程 + debounce；UI 线程零 IO |
| Ctrl+C / 停止后卡在翻译队列等待全部完成 | `stop_event` 贯穿所有阶段：阶段边界 + 翻译批次边界均检查；ffmpeg 子进程带超时可 kill |
| 三份翻译 worker 实现（CLI 一份、GUI 两份）逻辑漂移 | **单一 core 管线**，CLI 和 GUI 都只是壳（订阅事件总线） |
| ffmpeg 无超时，坏文件挂整批 | 所有子进程有超时；单文件失败只记录不阻断 |
| 启动时同步做 HF 缓存扫描 + Ollama 网络探测（重复两次） | 探测异步化、结果缓存、单次复用 |
| ffmpeg 提取与转录完全串行，CPU/GPU 互相干等 | 预提取流水线：转录第 i 个时提取第 i+1 个（有界队列，容量 1–2） |

---

## 3. 技术栈（已确认）

| 层 | 选型 | 理由 |
|----|------|------|
| 语言 | Python 3.12+，`uv` 管理 | 转录/翻译生态都在 Python；uv 保证可复现且首启快 |
| GUI | Flet | 延续既有经验；性能问题源于旧用法而非框架，用增量/节流规范解决 |
| 转录引擎 (macOS Apple Silicon) | `mlx-whisper` | 免费、本地、Metal 加速 |
| 转录引擎 (Windows / 其他) | `faster-whisper` (CTranslate2) | 免费、CPU int8 可用、有 NVIDIA GPU 自动加速 |
| 音频抽取 | `ffmpeg`（系统依赖） | 稳定通用；启动时 doctor 检查并给安装指引 |
| 翻译后端 | Ollama（本地） | 免费本地推理；`Translator` 协议保留扩展位（未来 LM Studio / llama.cpp / 云 API） |
| 启动方式 | 轻量启动器（非 PyInstaller） | macOS：壳 `.app`（内部执行 `uv run scripto`）；Windows：`Scripto.bat`（可选 .vbs 隐藏控制台）。首次启动自动 `uv sync`。透明、干净、体积小 |
| 历史存储 | 单个 JSON 文件（原子写入） | R5 要求的轻量方案 |

**引擎自动选择**：启动时探测平台——Apple Silicon → mlx-whisper；其余 → faster-whisper（有 CUDA 用 GPU，否则 CPU int8）。两者实现同一 `TranscribeEngine` 协议，模型清单各自维护、UI 统一呈现。

---

## 4. 架构

```
scripto/
├── pyproject.toml              # uv / hatchling；console scripts: scripto (gui), scripto-cli
├── src/scripto/
│   ├── core/                   # 纯逻辑，禁止 import flet
│   │   ├── config.py           # 配置读写（平台数据目录）
│   │   ├── scanner.py          # 多目录批量媒体发现（去重、混合来源）
│   │   ├── jobs.py             # Job / JobStatus / Batch 数据模型
│   │   ├── pipeline.py         # 阶段化流水线调度器（见 §5）
│   │   ├── events.py           # 事件总线：进度/状态/日志，UI 与 CLI 订阅
│   │   ├── history.py          # JSON 索引（R5）
│   │   └── output.py           # 输出路径规则（同目录 / export / 语言后缀）
│   ├── engines/
│   │   ├── base.py             # TranscribeEngine 协议 + release()/进度回调
│   │   ├── mlx_engine.py
│   │   ├── fw_engine.py        # faster-whisper
│   │   └── models.py           # 模型注册表 + 探测 + 一键下载（R1）
│   ├── translate/
│   │   ├── base.py             # Translator 协议；语言注册表（R3 扩展位）
│   │   ├── srt.py              # SRT 解析/重建/批次切分（marker 协议）
│   │   └── ollama.py           # 请求管理：num_ctx、keep_alive、流式、重试
│   ├── media/
│   │   └── ffmpeg.py           # 探测、抽音频（16k mono wav）、超时、清理
│   ├── gui/                    # Flet 壳：订阅 events，增量渲染
│   └── i18n/                   # zh / en 界面文案
├── launchers/
│   ├── macos/                  # Scripto.app 壳（Info.plist + 启动脚本）
│   └── windows/                # Scripto.bat / Scripto.vbs
├── docs/
│   ├── PLAN.md                 # 本文档
│   └── mingri-plan.md          # 里程碑/任务蓝图（与 mingri 同步）
└── tests/
```

关键规则：

- **core 无 UI 依赖**，全部通过 `events.py` 对外发进度；GUI/CLI 只是订阅者。彻底避免旧项目"三份 worker"的漂移。
- 所有对外 IO（HF 下载、Ollama、ffmpeg、扫盘）都在 core 的后台线程里，永不阻塞 UI 线程。
- 配置与历史存在平台数据目录（macOS `~/Library/Application Support/Scripto/`，Windows `%APPDATA%/Scripto/`），不再依赖"从项目根目录运行"。

---

## 5. 流水线设计（R6 核心）

```
scan ──► extract 队列(容量2) ──► transcribe(串行×1) ──► translate 队列(有界) ──► done
              CPU: ffmpeg             GPU/NPU: whisper          Ollama worker
              预提取 i+1              一次一个（内存约束）        与转录并行(balanced)
```

- **阶段化流水线，非轮换**：每个文件按 extract → transcribe → translate 流动，不同文件的不同阶段并行。转录 GPU 串行（这是内存与吞吐的正确约束），但 CPU 的 ffmpeg 提取和 Ollama 翻译不陪它排队。
- **有界队列 = 背压**：extract 最多领先转录 2 个文件（磁盘上最多 2 个临时 WAV）；翻译队列有界，防止内存堆积。
- **内存模式**（设置项）：
  - `balanced`（默认）：如上并行，whisper 与 Ollama 模型同时驻留；
  - `low`：翻译阶段推迟到全部转录完成、whisper 释放之后，任一时刻只有一个大模型在内存里。
- **缓存与跳过**：输出已存在且未开 overwrite → 跳过（转录与翻译分别判断）；模型保持常驻（批内不重复加载）；Ollama `keep_alive` 在 balanced 模式设为批次生命周期、批后归零。
- **取消语义**：stop 请求后——当前转录文件跑完即停（whisper 不可中断），翻译在下一个批次边界停，队列剩余标记为"未处理"，进程 5 秒内回到空闲。ffmpeg 直接 kill。
- **健壮性**：单文件失败记录原因继续跑；ffmpeg/下载/翻译全部有超时；历史 JSON 原子写；崩溃后重启可从历史看到已完成部分。
- **超长文件**（>1h，可配置）：按 30 分钟切段转录后拼接时间轴，压平单文件内存峰值。

**验收指标**（M7 用 benchmark 验证）：

1. 批量 N 文件总时长 ≈ max(转录总时长, 翻译总时长) + 首个提取时间（balanced 模式）；
2. 连跑 20 个长文件，进程 RSS 曲线平稳不阶梯上涨；
3. 翻译单批一次成功率 > 95%（不靠分裂重试兜底）；
4. UI 在 500 文件批次下操作无卡顿；
5. stop 后 5 秒内回到空闲。

---

## 6. 输出与命名（R4）

- 默认写到**源文件同目录**；用户显式选择 export 目录时才集中输出（保持源目录相对结构可选）。
- 语言后缀：转录 `lecture.en.srt`（英文音频）/ `lecture.cn.srt`（中文音频）；翻译产物同规则。后缀映射表可配置（`en → .en`，`zh → .cn`），新增语言只加一行。
- 支持 srt / txt / vtt / json 输出格式；翻译仅对 srt。
- 批量输入：文件与文件夹混合、来自任意不同目录，统一去重后进列表。

## 7. 历史（R5）

`history.json`（平台数据目录，原子写入，追加为主）：

```json
{
  "version": 1,
  "entries": [
    {
      "id": "uuid",
      "source": "/path/to/lecture.mp4",
      "outputs": [{"lang": "en", "format": "srt", "path": "/path/to/lecture.en.srt"}],
      "model": "whisper-large-v3-turbo",
      "engine": "mlx",
      "duration_sec": 5400,
      "created_at": "2026-07-16T12:00:00Z",
      "status": "done"
    }
  ]
}
```

History 页：按时间倒序列出 → 点击某条 → 逐个检查 `outputs[].path` 是否存在 → 存在则在 app 内预览打开，不存在显示"已被删除"（并可一键清理失效条目）。

## 8. GUI 要点（吸取旧 UI 教训）

- **主操作流优先**：主视图 = 输入区（粘贴/选择/拖入）+ 文件列表 + **底部固定操作栏**（开始/停止/总进度/当前文件/ETA 永远可见）；设置收进独立页/抽屉。
- 当前文件有进度与已用时；翻译进度可视化（不是只进日志）。
- 失败行内直接显示原因摘要 + 一键重试；有失败时事件面板自动提示。
- 事件日志：内存环形上限（如 500 行）+ 增量渲染 + 节流；完整日志始终落盘。
- 模型管理器：列出两类引擎的可用模型、本地是否已装、一键下载/删除（R1）。
- macOS 文件夹选择用原生 `osascript` 兜底（旧项目 Flet picker 拿不到路径的问题）。
- i18n（R7）：全部面板文案 zh/en 双语，设置中切换即时生效；暗色模式。

## 9. 启动器（R2）

- **macOS**：`Scripto.app` 壳——标准 bundle 结构，`Contents/MacOS/scripto` 是一个短 shell 脚本：定位仓库目录 → 无 uv 则提示安装（或引导 `brew install uv`）→ `uv run scripto`。无 PyInstaller、无二进制黑盒，代码可读即"干净"。
- **Windows**：`Scripto.bat`（同逻辑）+ 可选 `Scripto.vbs` 隐藏控制台窗口；提供创建桌面快捷方式的说明。
- 首次启动：`uv sync` 自动装依赖 → doctor 检查（ffmpeg / 引擎可用性 / Ollama 可选）→ 缺什么给一键指引。

---

## 10. 路线图（与 mingri 项目同步，详见 mingri-plan.md）

| 里程碑 | 内容 | 版本 |
|--------|------|------|
| M1 骨架与基础设施 | 仓库脚手架、config、events、i18n、日志、测试框架 | v0.1 |
| M2 媒体与引擎层 | ffmpeg 封装、引擎协议、mlx/faster-whisper 双实现、模型管理与一键下载 | v0.2 |
| M3 批处理流水线 | scanner、流水线调度、预提取、停止/取消、输出规则、失败记录、内存治理 | v0.3 |
| M4 翻译层 | Ollama 客户端（num_ctx/keep_alive/流式）、SRT 批次管理、zh/en、扩展注册表 | v0.4 |
| M5 GUI | Flet 壳、主流程、文件列表与进度、错误直显、模型管理器、设置、History、i18n | v0.5 |
| M6 启动器与发布 | mac .app 壳、win .bat、doctor、首启引导、README | v0.6 |
| M7 性能与质量 | benchmark、内存曲线验证、e2e 测试、§5 验收指标达标 | v1.0 |

依赖关系：M1 → M2 → M3 → M4 → M5 → M6 → M7（M4 可与 M3 后半并行）。

## 11. 旧仓库处置

`my-transcriptor` 保留为只读参考（教训已沉淀进本文档 §2）；Scripto 达到 v0.5 功能对齐后，由 TN 决定归档或删除。不迁移任何代码文件。
