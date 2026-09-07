---
name: memory-maintenance
description: 维护长期记忆的过期、冲突、索引及归档。
---

memory.status 查看状态，memory.maintain 检查维护，rebuild=true 重新嵌入当前模型索引。conflicts_to_review 是同主题潜在冲突，不表示所有记录互相矛盾；需来源判断。archive 可撤回，restore 会清除过期时间。只对用户要求或明确过时/重复的信息做有据可查的维护。
