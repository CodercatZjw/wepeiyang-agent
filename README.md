<div align="center">

# WePeiYang Agent

**让 AI 在安卓模拟器里安全地阅读、搜索和采集微北洋帖子。**

<img src="assets/banner.webp" alt="WePeiYang Agent — Read, Search, Collect" width="100%">

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%20%2B%20BlueStacks-555555)
![Mode](https://img.shields.io/badge/Mode-Read--only-C9A45C)

</div>

WePeiYang Agent 是一个运行在蓝叠模拟器外部的校园论坛 Agent。v0.5 将多轮对话、原生搜索、栏目浏览、图像理解、本地文件和长期记忆放在同一个 Plan–Executor 循环中：规划下一步、调用 Skill、观察结果、独立检查，再决定继续或回答。

它不依赖 OCR，也不调用或逆向论坛私有接口；公开能力中没有发帖、回复、点赞、点踩或收藏操作。

## 为什么做它

校园论坛的信息价值往往埋在持续刷新的信息流里：课程资料、竞赛消息、校园服务和偶发趣事都很分散。这个项目先把“稳定、安全地读帖”做成通用底座，为后续的定时摘要和手机推送提供结构化数据。

当前可以按指令采集、总结、对话和维护记忆。无人值守每日调度、手机推送尚未接入；记忆维护在运行任务时检查是否到期，也可通过 CLI 单独运行。

## 快速开始

### 1. 准备环境

你需要：

- Windows 与蓝叠模拟器
- 已安装并登录的天外天 App（包名 `com.twt.service`）
- 在蓝叠设置中开启 ADB
- Python 3.10 或更高版本

安装项目：

```powershell
python -m pip install -e .
if (-not (Test-Path config.json)) { Copy-Item config.example.json config.json }
```

### 2. 配置 LLM API

编辑 `config.json`：

```json
{
  "llm": {
    "url": "https://api.openai.com/v1/responses",
    "api_key": "你的 API Key",
    "model": "你的模型名",
    "api_format": "responses",
    "timeout_seconds": 180
  }
}
```

`url` 必须是完整请求地址。OpenAI Responses API 使用 `responses`；兼容 `/v1/chat/completions` 的服务使用 `chat_completions`。真实的 `config.json` 已被 Git 忽略。

验证蓝叠与模型连接：

```powershell
python -m wepeiyang_agent doctor
python -m wepeiyang_agent llm-test
```

### 3. 开始刷帖

最简单的方式是双击项目根目录的 `wepeiyang-agent.cmd`。在打开的 CMD 窗口中直接输入自然语言，Agent 会显示执行计划，并在同一窗口返回帖子正文、互动数据、评论和图片保存路径。

也可以从 PowerShell 启动交互窗口：

```powershell
python -m wepeiyang_agent chat
```

执行单条自然语言指令后退出：

```powershell
python -m wepeiyang_agent chat --ask "给我在湖底找 3 篇超过 10 赞的帖子"
```

只查看 LLM 生成的计划、不操作模拟器：

```powershell
python -m wepeiyang_agent chat --ask "搜索有关国创赛的最新内容" --plan-only
```

传统的自动刷帖入口仍然保留：

让配置的 LLM 根据当前页面决定继续滚动或停止：

```powershell
python -m wepeiyang_agent browse
```

LLM 在这个模式里只能返回 `scroll` 或 `stop`。即使模型输出异常，也不会获得互动或发布帖子的入口。

## 用自然语言调用

项目附带 [`wepeiyang-forum` Skill](skill/wepeiyang-forum/SKILL.md)。安装到 Codex 后，可以直接描述目标：

```powershell
Copy-Item -Recurse skill\wepeiyang-forum "$env:USERPROFILE\.codex\skills\wepeiyang-forum"
```

```text
给我在湖底找 3 篇超过 10 赞的帖子
搜索有关国创赛的最新内容
从学习区找两篇带图帖，并把评论一起带回来
```

项目内的 Agent Skills 位于 `wepeiyang_agent/skills/`，同一套能力可由模型组合调用，也可通过 `skill` CLI 直接使用。信息流没有尽头；未指定数量时，Agent 依据目标覆盖与信息增益决定停止。达到步数、时间或请求预算时会报告未完成并保留可恢复状态。

```powershell
python -m wepeiyang_agent skills
python -m wepeiyang_agent skills vision.inspect
python -m wepeiyang_agent skill memory.status
python -m wepeiyang_agent skill memory.maintain
python -m wepeiyang_agent chat --session campus
python -m wepeiyang_agent chat --resume RUN_ID
```

复杂参数可保存为 JSON 文件，使用 `skill 名称 --args-file 参数文件.json`，避免 CMD 与 PowerShell 的引号差异。详细说明见 [运行与日志指南](docs/runtime.md)。

## CLI 示例

实时筛选湖底中超过 10 赞的帖子：

```powershell
python -m wepeiyang_agent find `
  --section 湖底 `
  --min-likes 11 `
  --count 3 `
  --max-pages 30 `
  --max-seconds 300 `
  --json
```

搜索国创赛相关内容；`|` 表示任一同义词命中：

```powershell
python -m wepeiyang_agent search `
  --query "国创赛|国创|创新大赛|大创" `
  --section 全部 `
  --source hybrid `
  --count 5 `
  --json
```

打开带图帖详情，保存可见图片并读取评论：

```powershell
python -m wepeiyang_agent find `
  --section 湖底 `
  --only-images `
  --include-images `
  --include-comments `
  --count 3 `
  --json
```

常用筛选项包括 `--since 7d`、`--exclude-pinned`、`--min-likes`、`--only-images`。运行 `python -m wepeiyang_agent --help` 查看完整命令。

## 它如何工作

`对话与历史 → 动态计划 → Skill Executor → 结构化观察 → Result Checker → 继续规划 / 回答`

完整方向见[目标架构图](docs/architecture/intelligent-agent-architecture.svg)。当前已实现串行执行的动态依赖任务图、独立结果检查、Vision、RAG、记忆维护与持久化会话；图中的并行执行、后台定时服务和推送仍属于后续目标。

- **页面读取**：解析安卓 UI hierarchy，而不是识别截图文字。
- **原生搜索**：逐词进入 App 搜索框，回读关键词，再解析结果；不回退到信息流找关键词。`hybrid` 合并本地与实时搜索结果。
- **图片模式**：保存屏幕中可见的图片区域，Vision Skill 将所选图片发送给已配置模型，返回文字、事实、不确定性与来源。图片采集不是下载服务器原图。
- **长期记忆**：SQLite 保存正文、来源、时效与版本；Qdrant 保存分块向量。默认本地 `BAAI/bge-small-zh-v1.5`，首次使用自动下载模型，也可配置远程 Embeddings。
- **执行透明**：CMD 显示计划、行动依据摘要、工具结果与检查结论。所有 LLM 请求在发送前记录，每次重试独立编号，响应、用量、耗时和错误均可追溯。不会把过程摘要冒充模型内部隐藏思维链。

## 数据产物

默认数据目录为 `data/`：

| 路径 | 内容 |
| --- | --- |
| `posts.jsonl` | 跨运行累计的新帖子 |
| `state.json` | 已见帖子编号与去重状态 |
| `index.json` | 本地全文检索索引 |
| `runs/<时间>/` | 一次 LLM 刷帖的帖子、决策、统计与截图 |
| `queries/<时间>/result.json` | 一次筛选或搜索的结构化结果 |
| `queries/<时间>/media/` | 从帖子详情保存的可见图片 |
| `traces/<run_id>/events.jsonl` | 每次模型请求/响应、重试、工具执行和检查记录（密钥脱敏） |
| `traces/<run_id>/state.json` | 当前目标、任务图、完整工具证据及恢复状态 |
| `traces/<run_id>/attachments/` | 图像请求的原始附件，以 SHA-256 索引 |
| `sessions/<名称>.json` | 跨启动持久化的对话历史 |
| `memory/` | SQLite、Qdrant、记忆 JSON 与维护备份 |
| `files/` | Agent 可读写的笔记；覆盖前备份到 `.history/` |

单篇帖子会尽量包含作者、等级、发布时间、帖子编号、标题、正文、分区、点赞数、回复数、浏览量、图片路径和评论。

## 安全与隐私边界

- 论坛工具只暴露读取、搜索、打开详情和返回操作；记忆与文件有独立本地写入能力。
- 如果页面结构改变或落在未知页面，程序会停止，而不是继续盲点。
- 论坛内容可能包含联系方式等个人信息；采集结果默认只保存在本机，请勿直接公开上传。
- `chat` 会将对话和相关工具证据发送给已配置模型，Vision 会发送所选图像；日志和采集数据默认保存在本机并被 Git 忽略。
- `browse` 兼容入口可通过 `send_body_chars=0` 限制为标题；这个设置不限制新 Agent 的工具证据和 Vision 输入。

## 免责声明

- 本项目仅供**个人学习、技术交流**，禁止用于违规行为。
- **用户需自行遵守微北洋《用户协议》《隐私政策》等相关规定。**
- 本项目**与微北洋、天外天工作室、天津大学无任何关联**。

## 开发与验证

```powershell
python -m unittest discover -s tests -v
```

当前测试覆盖帖子结构解析、图片节点识别、评论解析、筛选规则，以及 Responses / Chat Completions 两种 LLM 返回格式。

## 路线图

- 按时间窗口聚合本轮新增帖子
- 让 LLM 区分“趣事”与“有用信息”并生成摘要
- 接入定时任务与手机推送

## 许可证

当前仓库尚未声明开源许可证。对外发布前，请根据你的发布方式补充合适的许可证文件。
