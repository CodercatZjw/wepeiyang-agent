# WePeiYang Agent

通过 ADB 操作蓝叠中的微北洋（天外天）App，在 CLI 里搜索、读帖、整理信息。

Python 实现。LLM 选择 Skill，执行层负责参数校验、页面操作和结果记录。论坛搜索、App 导航、Vision、本地文件和 RAG 共用这套工具，也可以直接从 CLI 调用。

## 快速开始

运行环境：Windows、蓝叠、Python 3.10+。先在模拟器里安装并登录天外天 App（`com.twt.service`），然后在蓝叠设置中开启 ADB。

在仓库根目录安装依赖并创建配置：

```powershell
python -m pip install -e .
if (-not (Test-Path config.json)) { Copy-Item config.example.json config.json }
```

编辑 `config.json` 的 `llm` 字段，其余配置可先保留默认值：

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

配置文件中的 `url` 填完整请求地址。支持 `responses` 和 `chat_completions` 两种格式；后者对应 `/v1/chat/completions`。App 导航和 Vision 需要模型支持图像输入。完整配置见 [config.example.json](config.example.json)。

检查连接，再启动交互模式：

```powershell
python -m wepeiyang_agent doctor
python -m wepeiyang_agent llm-test
python -m wepeiyang_agent chat
```

也可以双击 [wepeiyang-agent.cmd](wepeiyang-agent.cmd)。修改代码或安装依赖后，重新打开窗口。

## 交互模式

直接输入任务，例如：

```text
你好啊
搜索有关国创赛的最新内容
给我在湖底找 3 篇超过 10 赞的帖子
从学习区找两篇带图帖，并把评论一起带回来
把当前页面向上滑动一次
把刚才找到的报名信息记下来，再告诉我要准备哪些材料
```

普通聊天直接回答。需要操作时，窗口会显示计划、Skill 调用和检查结果，最后返回整理后的内容及证据引用。

输入 `/` 打开命令菜单：

| 命令 | 用途 |
| --- | --- |
| `/model` | 获取当前 Provider 的模型列表并切换模型 |
| `/provider` | 切换 Provider，或填写 URL、API Key 和显示名称添加一个 |

菜单支持方向键、输入筛选、Enter 确认和 Esc 取消。切换 Provider 后会进入模型选择；如果有与原模型 ID 完全一致的项，默认高亮它，仍需按 Enter 确认。

切换只对当前 CMD 会话生效。新增 Provider 保存在配置文件旁的 `*.providers.local.json`，下次可继续选择。显示名称默认使用域名。模型列表获取失败时，可以重试或手动输入 ID。

`help` 查看示例，`exit` 退出。Provider 的地址格式和保存规则见 [运行指南](docs/runtime.md)。

## CLI

执行一条自然语言任务后退出：

```powershell
python -m wepeiyang_agent chat --ask "给我在湖底找 3 篇超过 10 赞的帖子"
```

只看计划：

```powershell
python -m wepeiyang_agent chat --ask "搜索有关国创赛的最新内容" --plan-only
```

按条件采集，输出 JSON：

```powershell
python -m wepeiyang_agent find --section 湖底 --min-likes 11 --count 3 --max-pages 30 --max-seconds 300 --json
python -m wepeiyang_agent find --section 学习 --only-images --include-images --include-comments --count 2 --json
```

搜索多个关键词，`|` 分隔。`live` 使用 App 搜索框，`local` 查本地索引，`hybrid` 合并两者：

```powershell
python -m wepeiyang_agent search --query "国创赛|国创|大创" --source hybrid --count 5 --json
```

其他筛选项包括 `--since 7d`、`--exclude-pinned`。旧的 `browse` 入口也保留着，这个模式只让 LLM 决定 `scroll` 或 `stop`：

```powershell
python -m wepeiyang_agent browse --pages 8
python -m wepeiyang_agent --help
```

### 直接调用 Skill

```powershell
python -m wepeiyang_agent skills
python -m wepeiyang_agent skills app.tap
python -m wepeiyang_agent skill app.observe
python -m wepeiyang_agent skill memory.status
python -m wepeiyang_agent skill memory.maintain
python -m wepeiyang_agent skill forum.search --args-file search.json
```

上面的 `search.json` 内容：

```json
{"keywords": ["国创赛", "国创", "大创"]}
```

复杂参数建议用 `--args-file`，省去 CMD 和 PowerShell 的引号差异。Skill 的 schema 与说明由 `skills 名称` 输出，实现统一注册在 [skills.py](wepeiyang_agent/skills.py)。

## 实现

### 执行循环

[runtime.py](wepeiyang_agent/runtime.py) 的第一轮请求决定直接回答还是调用工具。操作任务维护一个带依赖的任务图，每次执行一个 Skill。App 导航后回读页面，交给下一轮 Planner；其他工具结果和最终事实回答由 Checker 核对。

未指定帖子数量时，Agent 根据已收集信息决定继续还是结束。`runtime.max_steps`、`max_seconds`、`max_requests` 是运行预算，耗尽时返回 `budget_exhausted` 并保存检查点。同一数据目录下的设备操作和本地向量库访问由进程锁串行化。

[目标架构图](docs/architecture/intelligent-agent-architecture.svg) 描述了后续方向，其中并行执行、后台调度和推送尚未实现。

### ADB 与页面

论坛文本来自 UI hierarchy。搜索逐词输入 App 原生搜索框，回读关键词后解析结果页；栏目浏览用于刷帖或美食、学习等泛主题搜集。`hybrid` 还会查询 `index.json` 中的本地记录。

中文输入使用随项目附带的 ADB 输入组件，输入完成后恢复原输入法。源码在 [android-input/](android-input/)。

`app.observe` 返回页面节点、截图和 `screen_id`。`app.tap` 按节点或坐标点击，`app.swipe` 滑动，`app.back` 返回。每次操作前检查前台包名和页面指纹，页面变了就重新观察。登录和验证码由用户处理。

### Vision

帖子详情中的图片按屏幕可见区域保存，`vision.inspect` 将图片交给配置的模型，返回文字、事实和不确定项。当前 App 导航还会把最新截图附给 Planner 和最终 Checker。XML 优先、按需附图的策略仍待实现。

### RAG

SQLite 保存正文、来源、过期时间和修订关系，Qdrant 保存分块向量。检索结合向量与关键词。默认 Embeddings 为本地 `BAAI/bge-small-zh-v1.5`，首次使用会下载权重，也可以换成远程接口。

`memory.maintain` 处理过期记录、缺失索引和备份。维护间隔在任务开始调用工具时检查，默认 24 小时；纯聊天跳过维护。笔记读写限定在 `data/files/`，覆盖前保存历史版本。

### 日志与恢复

每次 LLM 请求在发送前写入日志，重试单独编号。请求体、响应、用量、耗时和错误都会单独记录，方便调试和复现。CMD 同时打印 TTFT 和总时长；总时长包含 TTFT，每次重试分别计时。非流式响应或首输出前失败时，TTFT 记为 `null` 并说明原因。

使用命名会话、查看日志或恢复任务：

```powershell
python -m wepeiyang_agent chat --session campus
python -m wepeiyang_agent traces
python -m wepeiyang_agent traces RUN_ID
python -m wepeiyang_agent chat --session campus --resume RUN_ID
```

目前会话文件保留最近 40 条消息，每次请求带入最近 12 条。中断任务的计划和工具结果保存在 `state.json`，需要通过 `--resume` 显式恢复。完整历史检索、滚动摘要和输入“继续”自动续接尚未接入；长期记忆检索与会话历史是两套存储。

日志字段、恢复行为和配置细节见 [运行指南](docs/runtime.md)。

## 本地数据

默认写入 `data/`：

| 路径 | 内容 |
| --- | --- |
| `posts.jsonl`、`state.json` | 旧 `browse` 入口累计的帖子与去重状态 |
| `index.json` | 本地帖子索引 |
| `runs/`、`queries/` | 刷帖、筛选和搜索结果，以及详情图片 |
| `traces/<run_id>/events.jsonl` | 模型请求、响应、工具调用与检查记录 |
| `traces/<run_id>/state.json` | 任务图、工具结果和恢复检查点 |
| `traces/<run_id>/attachments/` | LLM 图像请求附件 |
| `sessions/` | 命名会话历史 |
| `memory/` | SQLite、Qdrant 和维护备份 |
| `files/` | 本地笔记，旧版本放在 `.history/` |

帖子记录包含可读取的作者、发布时间、帖子 ID、标题、正文、分区和互动数；详情采集可附图片路径与评论。

对话、相关工具结果和图像会发送给所选 Provider。日志中的凭证做脱敏处理，但正文、成绩和截图仍可能含个人信息。分享日志前请检查内容。`config.json`、Provider 文件和 `data/` 已加入 Git 忽略；配置文件中的 API Key 是本地明文保存。

## 当前限制

- UI 适配依赖天外天的页面结构。自绘控件、遮挡和改版可能需要重新定位；点击前的标签检查也有识别范围。App 操作限于只读导航，发布、互动、账号修改留给用户。
- 采集范围是屏幕上实际读到的内容。列表摘要、分页详情和图片中的模糊文字需要分别处理。
- Provider 使用 OpenAI-compatible 协议。模型列表可能包含非聊天模型，选择前需确认其支持所用接口、结构化输出和图像输入。
- 后台每日采集、定时摘要和手机推送仍在计划中。

## 开发

```powershell
python -m unittest discover -s tests -v
```

测试覆盖页面解析、搜索输入、导航校验、执行循环与恢复、记忆维护、LLM 流式响应及计时、Provider 切换和菜单键盘操作。

仓库另附一个供外部 Codex 使用的 [wepeiyang-forum Skill](skill/wepeiyang-forum/SKILL.md)。需要从 Codex 调用论坛 CLI 时，可安装它；项目自身的 `chat` 使用 [wepeiyang_agent/skills/](wepeiyang_agent/skills/)，无需额外安装。

```powershell
Copy-Item -Recurse skill\wepeiyang-forum "$env:USERPROFILE\.codex\skills\wepeiyang-forum"
```

## 免责声明

- 本项目仅供**个人学习、技术交流**，禁止用于违规行为。
- **用户需自行遵守微北洋《用户协议》《隐私政策》等相关规定。**
- 本项目**与微北洋、天外天工作室、天津大学无任何关联**。

## 许可证

仓库尚未添加 `LICENSE`。
