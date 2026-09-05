from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import patch

from wepeiyang_agent.config import LlmConfig
from wepeiyang_agent.llm import LlmController


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class LlmControllerTests(unittest.TestCase):
    def test_chat_completions_decision(self) -> None:
        config = LlmConfig(
            url="https://example.invalid/v1/chat/completions",
            api_key="secret",
            model="test-model",
            api_format="chat_completions",
        )
        response = {"choices": [{"message": {"content": '{"action":"scroll","reason":"有新帖"}'}}]}
        with patch("urllib.request.urlopen", return_value=FakeResponse(response)):
            decision = LlmController(config).decide({"posts": []})
        self.assertEqual(decision.action, "scroll")
        self.assertEqual(decision.reason, "有新帖")

    def test_responses_decision(self) -> None:
        config = LlmConfig(
            url="https://example.invalid/v1/responses",
            api_key="secret",
            model="test-model",
            api_format="responses",
        )
        response = {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": '{"action":"stop","reason":"足够"}'}
                    ]
                }
            ]
        }
        with patch("urllib.request.urlopen", return_value=FakeResponse(response)):
            decision = LlmController(config).decide({"posts": []})
        self.assertEqual(decision.action, "stop")

    def test_retries_transient_connection_error(self) -> None:
        config = LlmConfig(
            url="https://example.invalid/v1/responses",
            api_key="secret",
            model="test-model",
            api_format="responses",
        )
        response = {"output_text": '{"action":"stop","reason":"完成"}'}
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=[urllib.error.URLError("temporary"), FakeResponse(response)],
            ) as request,
            patch("time.sleep"),
        ):
            decision = LlmController(config).decide({"posts": []})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(decision.action, "stop")


if __name__ == "__main__":
    unittest.main()
