---
name: wepeiyang-forum
description: Search, browse, filter, and collect text, images, or comments from the WePeiYang campus forum in the BlueStacks TianWaiTian app. Use when the user asks to find or inspect 微北洋、湖底、学习区、校务区 or other forum posts. Do not use for posting, replying, liking, voting, or account changes.
---

# WePeiYang Forum

Use the project's read-only CLI for forum work. Read [references/cli.md](references/cli.md) before choosing a command.

## Workflow

1. Translate the request into section, keywords, minimum likes, time range, target count, and media/comment requirements.
2. Use `search --source hybrid` for keyword requests. Use `find` for live feed conditions such as section, likes, or image presence.
3. Always set finite `--count`, `--max-pages`, and `--max-seconds` budgets for live work. The feed is infinite.
4. Request `--json` and interpret the returned `posts`, `stopped_reason`, and `run_dir` fields.
5. If fewer posts are returned than requested, report the actual count and stopping reason; do not silently expand the budget.
6. Present saved image paths as local images when the user asks to see them.

Map “超过 N 赞” to `--min-likes N+1`; map “至少 N 赞” to `--min-likes N`.

Use `--include-images` only when the user asks for images or image-bearing posts. Use `--include-comments` when replies materially answer the request. Images remain local and are not sent to a vision model.

Do not read, print, or expose `config.json` API keys. Do not bypass the CLI with arbitrary ADB taps. The CLI intentionally exposes no write or engagement actions.

