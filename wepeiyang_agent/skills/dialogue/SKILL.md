---
name: dialogue
description: 处理普通对话、解释已有证据和组织中间回答。
---

普通对话直接回答：tasks=[]、task_id=""、skill=""、status=complete，不创建计划、不调用 dialogue.reply、不经过 Checker。无需从文件/记忆验证一句问候。需要在组合任务中间单独组织回答时才可调用 dialogue.reply，提供 message 和可选证据 context。需要工具的混合请求不能走纯对话快捷路径；引用已有来源，不把猜测写成已操作的事实。问当前账号成绩/课表先观察 App，问历史记录才检索记忆。
