---
name: post-detail
description: 采集当前可见帖子正文、图片及评论。
---

forum.detail 的 post_id 必须出现在当前屏幕结果。图片是屏幕采集区域，不称为服务器原图。detail_text_blocks 是页面逐块文字，可能包括导航与评论；不可直接宣称其为完整原文。需要看图则将 images 交给 vision.inspect。
