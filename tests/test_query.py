from __future__ import annotations

import unittest
from datetime import datetime

from wepeiyang_agent.query import QuerySpec, matches, parse_since


class QueryTests(unittest.TestCase):
    def test_filters_section_likes_and_keyword(self) -> None:
        post = {
            "section": "湖底",
            "title": "国创赛组队",
            "body": "寻找队友",
            "likes": 15,
            "published_at": "2026-09-05 20:00:00",
            "is_pinned": False,
            "image_bounds": [],
        }
        spec = QuerySpec(query="国创", section="湖底", min_likes=10, count=3)
        self.assertTrue(matches(post, spec))
        self.assertTrue(matches(post, QuerySpec(query="大创|国创", section="湖底", count=3)))
        self.assertFalse(matches(post, QuerySpec(section="学习", count=3)))

    def test_parse_since_days(self) -> None:
        value = parse_since("7d")
        self.assertIsInstance(value, datetime)
