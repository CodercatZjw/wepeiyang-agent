import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from wepeiyang_agent.config import LlmConfig
from wepeiyang_agent.llm import LlmController, LlmError
from wepeiyang_agent.trace import Trace
from test_llm import FakeResponse


class TraceTests(unittest.TestCase):
    def test_each_retry_recorded_before_sending_and_secrets_redacted(self):
        with tempfile.TemporaryDirectory() as d:
            trace = Trace(Path(d), ("test-key",))
            llm = LlmController(LlmConfig("https://test.invalid", "test-key", "test", "responses"), trace)
            count = 0
            def request(*args, **kwargs):
                nonlocal count
                count += 1
                events = [json.loads(line) for line in (trace.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(events[-1]["event"], "llm.request")
                if count == 1:
                    raise urllib.error.URLError("failed test-key")
                return FakeResponse({"output_text": '{"action":"stop","reason":"完成"}', "usage": {"total_tokens": 10}})
            with patch("urllib.request.urlopen", side_effect=request), patch("time.sleep"):
                llm.decide({"posts": [], "api_key": "test-key"})
            content = (trace.directory / "events.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("test-key", content)
            rows = [json.loads(line) for line in content.splitlines()]
            self.assertEqual(sum(r["event"] == "llm.request" for r in rows), 2)
            self.assertEqual(rows[-1]["usage"]["total_tokens"], 10)

    def test_vision_input_preserved_as_attachment(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.png"
            Image.new("RGB", (8, 8), "red").save(path)
            trace = Trace(Path(d) / "traces")
            llm = LlmController(LlmConfig("https://test.invalid", "secret", "test", "responses"), trace)
            with patch("urllib.request.urlopen", return_value=FakeResponse({"output_text": '{"x":"ok"}'})) as request:
                llm.request_json("test", {}, {"type": "object"}, "vision", images=[path])
            payload = json.loads(request.call_args[0][0].data)
            self.assertEqual(payload["input"][0]["content"][1]["type"], "input_image")
            attachments = list((trace.directory / "attachments").glob("*.png"))
            self.assertEqual(attachments[0].read_bytes(), path.read_bytes())

    def test_incomplete_response_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            llm = LlmController(LlmConfig("https://test.invalid", "secret", "test", "responses"), Trace(Path(d)))
            with patch("urllib.request.urlopen", return_value=FakeResponse({"status": "incomplete", "output_text": "{}"})):
                with self.assertRaises(LlmError):
                    llm.decide({})
