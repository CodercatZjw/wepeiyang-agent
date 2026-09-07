---
name: memory-write
description: 保存长期有用的信息、来源和版本。
---

memory.write 要提供 content 和 source（帖子ID/证据编号/用户指令）。限时活动按截止时间设置 ttl_days。重复内容复用记忆。只有明确确认修订时使用 supersedes，否则保留不同说法。检查 verified 与 indexed；索引失败时文字已保存，不要重复写，使用维护修复。
