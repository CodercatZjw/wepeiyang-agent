# 微北洋只读 Agent

这个版本通过蓝叠中的天外天 App，只读浏览、筛选和搜索微北洋帖子。既可以直接使用 CLI，也可以由 Codex 的 `wepeiyang-forum` Skill 根据自然语言调用。

它不会发帖、回复、点赞、点踩或收藏。帖子通过安卓页面的无障碍结构读取，不依赖 OCR，也不会直接调用或逆向论坛接口。

## 当前能力

- 自动发现蓝叠中国版/国际版自带的 `HD-Adb.exe`
- 检查模拟器与 `com.twt.service` 安装状态
- 自动冷启动天外天并进入底部第二个“微北洋”入口
- 通过配置的 OpenAI 兼容 LLM API 决定每一屏继续滚动还是停止
- LLM 只能选择 `scroll` 或 `stop`，无法点赞、回复、收藏或发帖
- 读取作者、等级、发布时间、帖子编号、标题、正文、互动数和浏览量
- 逐屏滚动、按帖子编号去重
- 连续多屏没有新帖时自动停止
- 保存每次运行的帖子 JSONL、运行摘要和页面截图
- 保存跨运行去重状态，便于后续接入“每天总结”
- 按分区、关键词、点赞数、时间和是否带图筛选
- hybrid 搜索：先查本地索引，不足时实时浏览补充
- 打开帖子详情，保存可见图片区域并采集评论

## 首次准备

1. 启动蓝叠和天外天。
2. 在天外天中手动完成登录、用户协议等一次性操作。
3. 在蓝叠“设置 → 高级”中打开 Android 调试桥（ADB）。本机上的蓝叠已为本项目打开该开关。
4. 在本目录打开 PowerShell。

本项目只使用 Python 标准库，不需要安装第三方依赖。

## 配置 LLM API

编辑本目录的 `config.json`：

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

- `url` 必须是完整请求地址，不是只有域名。
- OpenAI Responses API 使用 `api_format: "responses"`。
- 兼容 `/v1/chat/completions` 的服务使用 `api_format: "chat_completions"`。
- `config.json` 已被 Git 忽略，API Key 不会进入提交。
- 完整配置字段可参考 `config.example.json`。

测试 LLM 连接：

```powershell
python -m wepeiyang_agent llm-test
```

## 使用

先检查环境：

```powershell
python -m wepeiyang_agent doctor
```

启动 LLM 控制的刷帖 Agent（最大页数读取 `config.json`）：

```powershell
python -m wepeiyang_agent browse
```

列出分区：

```powershell
python -m wepeiyang_agent sections
```

在湖底实时寻找 3 篇至少 11 赞的帖子：

```powershell
python -m wepeiyang_agent find --section 湖底 --min-likes 11 --count 3 --max-pages 30 --json
```

搜索国创赛相关最新内容；`|` 表示同义词任选其一：

```powershell
python -m wepeiyang_agent search --query "国创赛|国创|创新大赛|大创" --section 全部 --source hybrid --count 5 --json
```

只找带图帖，进入详情保存图片并采集评论：

```powershell
python -m wepeiyang_agent find --section 湖底 --only-images --include-images --include-comments --count 3 --json
```

所有实时命令都有硬预算：`--count`、`--max-pages` 和 `--max-seconds`。信息流不需要、也不可能“刷完”。

快速试跑 3 屏，并且不保存截图：

```powershell
python -m wepeiyang_agent browse --pages 3 --no-screenshots
```

如果同时开了多个模拟器，用下面的命令指定设备：

```powershell
python -m wepeiyang_agent --serial emulator-5554 browse --pages 8
```

## 输出

默认写入 `data/`：

- `data/posts.jsonl`：跨运行累计的新帖子，一行一篇
- `data/state.json`：已见帖子编号，用于去重
- `data/runs/<时间>/posts.jsonl`：某次运行看到的全部帖子
- `data/runs/<时间>/run.json`：本次运行统计
- `data/runs/<时间>/decisions.jsonl`：每屏 LLM 的动作与原因
- `data/runs/<时间>/screens/`：每屏截图
- `data/index.json`：本地全文检索索引
- `data/queries/<时间>/result.json`：筛选或搜索结果
- `data/queries/<时间>/media/<帖子编号>/`：详情页裁剪出的图片

这些数据已经为下一阶段预留好了接口：定时运行后，可以只取本轮新帖，交给模型筛选“趣事”和“有用信息”，再推送到手机。

## 边界与注意事项

- CLI 只开放浏览、搜索、详情和返回动作，不开放发帖、回复、点赞、点踩或收藏。
- 如果天外天升级并改变底部导航或论坛页面标识，程序会停止并报错，不会在未知页面盲点。
- 采集内容可能包含联系方式等个人信息；当前结果只保存在本机，请勿公开上传。
- 为了决策，程序会把帖子标题和正文前 `send_body_chars` 个字符发送给你配置的 LLM 服务；可将该值设为 `0`，只发送标题。
- 图片模式只保存本机截图裁剪，不发送给视觉模型，也不下载或逆向原图地址。
- 建议控制频率，例如每隔数小时运行一次，每次 5–15 屏，避免给应用和服务造成不必要负担。
