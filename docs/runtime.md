# v0.5 Agent 运行指南

双击 `wepeiyang-agent.cmd`，或运行 `python -m wepeiyang_agent chat`。普通对话不连接模拟器；需要 App 页面或论坛时才初始化 ADB。同一数据目录同时只允许一个新 Agent/Skill 实例控制设备与向量库。更新代码后重新打开 CMD 窗口。

## 可以组合的任务

```text
查查国创赛最近的通知，看一下通知图片，记住有用的报名信息，然后告诉我要准备什么。
我之前让你记住的竞赛资料有哪些？再搜索一下有没有更新。
在美食区看看近期大家推荐什么，整理到 food.md，你判断信息够了就停。
记住我更关注创新竞赛和课程资料。
查一下我的成绩。
把当前页面向上滑动一次。
```

第一轮 `agent_decide` 同时决定直接回答或执行任务，不额外增加分类请求。普通聊天、问候、基于当前对话即可作答的解释使用空任务图，一次请求直接返回，不调用 Checker、工具或记忆维护。混合消息里有操作要求时仍进入执行流程。

需要行动时 Planner 给出动态任务图和下一项 Skill，Executor 校验参数后执行。App 导航采用本地校验和新页面回读，下一轮 Planner 直接读取截图判断进展，不为每次点击/滑动额外调用 LLM Checker；最终事实回答及其他 Skill 结果保留独立检查。Checker 修订后的答案直接交付，不因旧答案中的错误引用而重复规划。依赖没有完成的任务不能执行；只会串行控制同一个模拟器。计划、观察和检查持续写到 `state.json`。

普通对话可直接回答。具体主题调用原生搜索，可以扩展多个关键词；仅搜索结果页允许为搜索目的滚动。泛美食、学习探索和明确刷帖可进入栏目。一屏是一项操作；未指定帖子数量时不会自动以三篇为目标。用户指定数量时还要完成其他子任务。

## 请求记录和过程输出

每个 HTTP 尝试发送前记录 `llm.request`，其中包含完整提示、请求体、目的、请求 ID 与重试编号。成功记录 `llm.response`、服务返回的用量和耗时；失败记录 `llm.error`。JSON/schema 错误另有记录。接口未提供用量时保留为未知，不估造数字。

每次请求结束（包括失败、超时、取消和每次重试）都会在 CMD 打印 `TTFT` 和 `总时长`，并在 `data/traces/<RUN_ID>/events.jsonl` 对应的 `llm.response` / `llm.error` 中保存 `ttft_seconds`、`total_duration_seconds`、`ttft_status`。`elapsed_seconds` 保留为总时长的兼容字段；通过 `request_id` 关联请求，`attempt` 标记重试次数。计时输出写入 stderr，不污染 Skill CLI 的 stdout JSON。

文本和 Vision 生成使用流式响应，兼容 Responses 和 Chat Completions。TTFT 定义为发出本次 HTTP 请求至客户端收到第一个非空文本（或拒绝文本）增量的时间，包含网络与网关延迟；不把响应头、心跳、角色、推理摘要事件算作首输出。总时长截至完整响应/流完成事件或失败，不包含重试等待、本地日志落盘与后续结果校验。流事件随终态日志保存，流中断不会当作成功。

如果服务忽略 `stream=true` 返回普通 JSON，TTFT 显示 `N/A (non_streaming_response)`，日志保存 `null`；未收到输出就失败为 `no_output_received`。远程 Embedding 没有文本生成，TTFT 为 `N/A (not_applicable)`，但仍记录总时长。不会用总时长伪造 TTFT。流式格式依据 [OpenAI Docs](https://developers.openai.com/api/docs/guides/streaming-responses)。

API Key 和 Authorization 不写日志。图像请求不重复写入长 Base64 字符串，而是把相同字节保存到 `attachments/`，在请求体对应位置保留文件路径、哈希和 data URL 头，可重建输入。正文和用户数据仍是本地敏感资料，不应公开提交。若服务返回推理摘要，该响应字段会随 API 响应保存；程序不依赖或声称能获取服务没有公开的隐藏思维链。

```powershell
python -m wepeiyang_agent traces
python -m wepeiyang_agent traces RUN_ID
python -m wepeiyang_agent chat --resume RUN_ID
python -m wepeiyang_agent chat --session my-campus
```

恢复任务重新读取已保存的目标、计划、证据和检查结论，再生成下一步，不直接重放旧工具调用。人工操作模拟器后，原有页面游标可能失效，Agent 需通过搜索或栏目入口重新定位。恢复状态能减少重复工作，但突发断电发生在工具写入与检查点之间时不承诺任意操作恰好一次。

## Skills CLI

`python -m wepeiyang_agent skills` 输出能力及其参数 schema；`skills 名称` 查看独立说明。调用格式：

```powershell
python -m wepeiyang_agent skill memory.status
python -m wepeiyang_agent skill memory.maintain --args '{"rebuild":true}'
python -m wepeiyang_agent skill memory.search --args '{"query":"竞赛报名"}'
python -m wepeiyang_agent skill forum.search --args '{"keywords":["国创赛","国创","大创"]}'
python -m wepeiyang_agent skill forum.next
python -m wepeiyang_agent skill app.observe
python -m wepeiyang_agent skill app.open
python -m wepeiyang_agent skill app.tap --args '{"screen_id":"实际返回的ID","node_id":"n12"}'
python -m wepeiyang_agent skill app.swipe --args '{"screen_id":"最新返回的ID","direction":"up"}'
python -m wepeiyang_agent skill app.back --args '{"screen_id":"最新返回的ID"}'
python -m wepeiyang_agent skill vision.inspect --args-file vision.json
```

`vision.json` 示例（图片必须位于当前 data 目录中）：

```json
{"paths":["traces/RUN_ID/screen.png"],"question":"提取报名截止时间、地点、材料，列出看不清的地方"}
```

CMD 的引号规则与 PowerShell 不同，复杂参数优先使用 `--args-file`。中文通过 ADB 输入组件写入模拟器，回读验证后用 ADB 回车提交搜索；不使用电脑键盘、焦点或剪贴板。首次搜索自动安装随项目附带的 13 KB 输入组件，输入后恢复原输入法。组件没有网络或存储权限，仅接收 ADB shell 广播并限定天外天输入框；源码和构建脚本在 `android-input/`。每个关键词的第一页都会采集，`forum.next` 延续最后一个词。

## App 导航与隐私

`app.*` 在整个微北洋/天外天 App 内工作，不局限论坛。查当前成绩、课表等先观察 App，再按真实页面找入口；问已保存的历史记录才优先检索本地。没有内置或猜测固定成绩菜单坐标。旧对话中“无法操作 App”的答复不代表当前工具能力。

`app.observe` 返回 `screen_id`、截图、像素宽高和控件 `node_id`；CLI 结果位于 `result` 字段。`app.tap` 使用 `node_id` 或 `x`+`y`，二选一。点击、返回、滑动必须引用最新快照（5 分钟内），执行前重新检查前台包名与页面节点指纹；页面变化则拒绝旧坐标。`app.swipe` 使用可滚动区域，方向是手指方向，`up` 为向上滑看下方内容。每次动作重新截图并返回 `page_changed`；变化不等于目标完成。App 操作会失效旧论坛游标；之后论坛采集需重新搜索/进入栏目。

只允许 App 内导航和阅读，不暴露任意 shell、输入密码或发布/互动工具。拦截可识别的危险标签、输入框、开关和帖子内互动区域；系统弹窗、登录和验证码交给用户。自绘图标无法保证全部识别，模型仍须先看图确认用途；用途不明不能试探。工具范围及检查是防护，不是对任意坐标绝对只读的承诺。

最新 App 截图自动发送给同一配置的图像模型参与规划及最终检查，不为普通导航额外调用 Vision。成绩、学号等私密信息不自动保存长期记忆；但截图、页面文本、LLM 请求与结果仍按审计要求保存在本地 traces 中，请勿公开上传。

## 记忆与维护

默认配置无需另配嵌入 API。首次加载本地中文模型需要下载权重。模型加载/索引失败时，正文继续保存到 SQLite，并明确返回 `indexed=false`；检索可降级为关键词检索，返回 `mode=keyword_only`。修复后会恢复向量与关键词混合召回。

记忆保留原文、来源、主题、时间、过期策略；长文分块向量化。精确去重会记录新增来源，不重复创建记录。明确修订可用 `supersedes` 保留旧版本；同主题不同内容列为待检查项，不自动认为全部矛盾或自动选边。过期记忆立即停止参与召回；维护会清理无效向量、修复缺失索引、导出 JSON，并留下 SQLite 备份。归档可通过 `memory.restore` 恢复，恢复会清除过期时间。

`memory.maintain` 延迟到任务确实需要执行工具时检查间隔，纯对话不触发；默认每 24 小时触发一次，程序关闭时不会自行运行。可单独调用它，或以后接入定时服务。`rebuild=true` 使用当前配置重新嵌入。更换模型使用新的向量集合，避免混合不同维度；旧集合保留用于排查，需要时人工清理。

## 配置和停止

`config.example.json` 给出了 `runtime` 和 `memory` 的完整可选项。旧配置仍可加载。`max_steps`、`max_seconds`、`max_requests` 是异常和资源保护；达到上限返回 `budget_exhausted`，不会伪报完成。已发出的 HTTP 请求按剩余时间设置超时；单次 ADB 操作与首次本地模型加载可能使实际墙钟时间略超预算。

若使用远程向量接口，在 `memory` 中设置 `embedding_provider=remote`、完整 `embedding_url`、`embedding_model` 和可选独立 `embedding_api_key`。未填写独立密钥时复用 LLM Key。向量 API 请求同样逐次记录。不能把聊天模型名直接当成嵌入模型名。

当前限制：界面结构变化仍可能需要修复；图片是屏幕采集而非服务器原图；同主题冲突需进一步证据判断；结果检查不能保证真实世界事实一定正确。支持限定微北洋 App 的观察式导航，但不支持其他 App、任意系统控制、账号登录或写操作。每日后台采集与手机推送、并行执行没有在本版实现。
