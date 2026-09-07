---
name: forum-search
description: 明确主题与关键词检索，使用论坛原生搜索。
---

调用 forum.search，keywords 中用合理同义词、全称或简称。forum.next 只能延续最后搜索词的结果页。无结果可换词；不得退回信息流匹配关键词。结果中 source=native_search。取原文应进一步调用 forum.detail。
