---
name: wepeiyang-forum
description: Search, browse, filter, and collect text, images, or comments from the WePeiYang campus forum in the BlueStacks TianWaiTian app. Use when the user asks to find or inspect 微北洋、湖底、学习区、校务区 or other forum posts. Do not use for posting, replying, liking, voting, or account changes.
---

# WePeiYang Forum

Use the project's read-only CLI for forum work. Read [references/cli.md](references/cli.md) before choosing a command.

## Workflow

1. Translate the request into section, keywords, minimum likes, time range, target count, and media/comment requirements.
2. Use `search --source hybrid` for keyword requests. Use `find` for live feed conditions such as section, likes, or image presence.
3. For legacy `find/search` CLI calls set finite page/time budgets and a count when requested. For open-ended tasks use `chat --ask` or the composable `skill` CLI; the agent decides when coverage is sufficient rather than inventing a three-post goal.
4. Request `--json` and interpret the returned `posts`, `stopped_reason`, and `run_dir` fields.
5. If fewer posts are returned than requested, report the actual count and stopping reason; do not silently expand the budget.
6. Present saved image paths as local images when the user asks to see them.

Map “超过 N 赞” to `--min-likes N+1`; map “至少 N 赞” to `--min-likes N`.

Use `--include-comments` when replies materially answer the request. Use `forum.detail` to collect images and `vision.inspect` when an image bears relevant information; Vision sends selected images to the configured model. The image model returns facts, text, uncertainties and sources.

Do not read, print, or expose `config.json` API keys. Do not bypass the CLI with arbitrary ADB taps. Forum tools have no publishing or engagement actions; local file and memory writes are separate skills. Keyword requests must use native search, never fall back to the feed. Chinese input and search submission use ADB; they do not type into the host computer.

Use `python -m wepeiyang_agent skills` to discover the live registry and schemas. `skill NAME --args-file parameters.json` invokes the same implementation as the agent. Dialogue, memory, Vision and files may be composed in a single `chat --ask` request. Every LLM request and execution step is recorded in `data/traces`; visible process output contains decision summaries, not a claimed hidden chain of thought.
