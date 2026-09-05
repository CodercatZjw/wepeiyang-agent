from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from wepeiyang_agent.console_agent import CommandPlanner, _print_posts
from wepeiyang_agent.llm import LlmError


def valid_payload(**updates) -> dict:
    payload = {
        "action": "find",
        "query": None,
        "section": "湖底",
        "min_likes": 11,
        "count": 3,
        "since": None,
        "exclude_pinned": False,
        "only_images": False,
        "include_images": False,
        "include_comments": False,
        "source": "hybrid",
        "max_pages": 20,
        "max_seconds": 300,
        "reason": "查找高赞帖子",
    }
    payload.update(updates)
    return payload


class FakeLlm:
    def __init__(self, payload: dict):
        self.payload = payload
        self.input_payload = None

    def request_json(self, system_prompt, input_payload, schema, schema_name, max_output_tokens):
        self.input_payload = input_payload
        return self.payload


class CommandPlannerTests(unittest.TestCase):
    def test_creates_bounded_read_only_plan(self) -> None:
        llm = FakeLlm(valid_payload(count=99, max_pages=999, max_seconds=9999))
        plan = CommandPlanner(llm).plan("给我找帖子")
        self.assertEqual(llm.input_payload, {"instruction": "给我找帖子"})
        self.assertEqual(plan.action, "find")
        self.assertEqual(plan.count, 20)
        self.assertEqual(plan.max_pages, 50)
        self.assertEqual(plan.max_seconds, 900)

    def test_rejects_unapproved_action(self) -> None:
        with self.assertRaises(LlmError):
            CommandPlanner._validate(valid_payload(action="like"))

    def test_search_requires_query(self) -> None:
        with self.assertRaises(LlmError):
            CommandPlanner._validate(valid_payload(action="search", query=None))

    def test_normalizes_section_suffix(self) -> None:
        plan = CommandPlanner._validate(valid_payload(section="学习区"))
        self.assertEqual(plan.section, "学习")

    def test_prints_post_body_and_comments(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            _print_posts(
                [
                    {
                        "post_id": "MP1",
                        "title": "测试帖",
                        "author": "甲",
                        "body": "原文内容",
                        "likes": 2,
                        "replies": 1,
                        "views": 9,
                        "comments": [{"author": "乙", "body": "评论内容"}],
                    }
                ]
            )
        rendered = output.getvalue()
        self.assertIn("原文内容", rendered)
        self.assertIn("评论内容", rendered)


if __name__ == "__main__":
    unittest.main()
