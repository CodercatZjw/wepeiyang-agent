<div align="center">

# WePeiYang Agent

**让 AI 在安卓模拟器里安全地阅读、搜索和采集微北洋帖子。**

<img src="assets/banner.webp" alt="WePeiYang Agent — Read, Search, Collect" width="100%">

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%20%2B%20BlueStacks-555555)
![Mode](https://img.shields.io/badge/Mode-Read--only-C9A45C)

</div>

WePeiYang Agent 是一个运行在蓝叠模拟器外部的只读校园论坛 Agent。它通过 ADB 驱动天外天 App，从安卓无障碍页面结构中读取帖子，再由 CLI 或 Codex Skill 完成分区浏览、条件筛选、关键词搜索、图片保存和评论采集。

它不依赖 OCR，也不调用或逆向论坛私有接口；公开能力中没有发帖、回复、点赞、点踩或收藏操作。

## 为什么做它

校园论坛的信息价值往往埋在持续刷新的信息流里：课程资料、竞赛消息、校园服务和偶发趣事都很分散。这个项目先把“稳定、安全地读帖”做成通用底座，为后续的定时摘要和手机推送提供结构化数据。

当前版本聚焦采集。自动总结、定时运行和手机推送尚未接入。

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
Copy-Item config.example.json config.json
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
    "timeout_seconds": 60
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

Skill 会把自然语言转换为有限、只读的 CLI 参数，并始终设置数量、页数和运行时间上限。信息流没有尽头，Agent 不以“刷完”为完成条件。

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

`自然语言 → Codex Skill → 只读 CLI → ADB → 天外天界面 → 结构化帖子 → 本地索引 / JSON`

- **页面读取**：解析安卓 UI hierarchy，而不是识别截图文字。
- **混合搜索**：`hybrid` 先查本地索引，不足时再进入 App 实时浏览。
- **图片模式**：打开详情并保存屏幕中可见的图片区域；不下载原图，也不发送给视觉模型。
- **安全停止**：目标数量、最大页数、最长时间和连续无新帖共同限制运行范围。

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

单篇帖子会尽量包含作者、等级、发布时间、帖子编号、标题、正文、分区、点赞数、回复数、浏览量、图片路径和评论。

## 安全与隐私边界

- CLI 只暴露读取、搜索、打开详情和返回操作。
- 如果页面结构改变或落在未知页面，程序会停止，而不是继续盲点。
- 论坛内容可能包含联系方式等个人信息；采集结果默认只保存在本机，请勿直接公开上传。
- `browse` 会把受配置长度限制的标题和正文片段发送给你配置的 LLM 服务；`find` 和 `search` 的过滤逻辑本身不要求调用 LLM。
- 可把 `send_body_chars` 设为 `0`，让刷帖决策只发送标题。

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
