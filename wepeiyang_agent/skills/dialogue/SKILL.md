---
name: dialogue
description: 处理普通对话、解释已有证据和组织中间回答。
---

普通对话可由 Planner 直接形成最终回答并交 Checker 核对；不需要先操作论坛。需要在组合任务中间单独组织回答时可调用 dialogue.reply，提供 message 和可选证据 context。引用已有来源，不把猜测写成已搜索得到的事实。需要历史、新消息或图像时组合相应 Skills。
