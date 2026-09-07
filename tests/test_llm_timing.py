import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from wepeiyang_agent.config import LlmConfig
from wepeiyang_agent.llm import LlmController, LlmError
from wepeiyang_agent.trace import Trace
from test_llm import FakeResponse


class StreamResponse(FakeResponse):
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}

    def __init__(self, events, clock, fail=False):
        self.clock = clock
        self.fail = fail
        self.lines = io.BytesIO((": heartbeat\r\n\r\n" + "".join(
            "data: " + (event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)) + "\r\n\r\n"
            for event in events
        )).encode("utf-8"))

    def readline(self):
        self.clock[0] += 0.25
        value = self.lines.readline()
        if not value and self.fail:
            raise TimeoutError("stream timeout")
        return value


class LlmTimingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.trace = Trace(Path(self.folder.name), ("test-key",))
        self.llm = LlmController(LlmConfig("https://example.invalid", "test-key", "test", "responses"), self.trace)
        self.clock = [100.0]
        self.stderr = io.StringIO()
        self.stdout = io.StringIO()
        self.enterContext(patch("wepeiyang_agent.llm.time.monotonic", side_effect=lambda: self.clock[0]))
        self.enterContext(patch("time.sleep"))
        self.enterContext(redirect_stderr(self.stderr))
        self.enterContext(redirect_stdout(self.stdout))

    def rows(self):
        return [json.loads(line) for line in (self.trace.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]

    def timings(self):
        return [row for row in self.rows() if "ttft_seconds" in row]

    def response_stream(self, terminal="completed"):
        return StreamResponse([
            {"type": "response.created", "response": {}},
            {"type": "response.output_text.delta", "delta": ""},
            {"type": "response.output_text.delta", "delta": '{"x":'},
            {"type": "response.output_text.delta", "delta": '"中文"}'},
            {"type": "response." + terminal, "response": {"output_text": '{"x":"中文"}', "usage": {"total_tokens": 9}}},
        ], self.clock)

    def test_responses_ttft_ignores_metadata_empty_deltas_and_heartbeat(self):
        with patch("urllib.request.urlopen", return_value=self.response_stream()) as request:
            result = self.llm.request_json("test", {}, {"type": "object"}, "vision")
        self.assertEqual(result, {"x": "中文"})
        self.assertTrue(json.loads(request.call_args.args[0].data)["stream"])
        row = self.timings()[0]
        self.assertEqual(row["ttft_seconds"], 2.0)
        self.assertEqual(row["total_duration_seconds"], 3.0)
        self.assertEqual(row["elapsed_seconds"], 3.0)
        self.assertEqual(row["usage"]["total_tokens"], 9)
        self.assertEqual(len(row["stream_events"]), 5)
        self.assertIn("TTFT: 2.000s", self.stderr.getvalue())
        self.assertIn("总时长: 3.000s", self.stderr.getvalue())
        self.assertEqual(self.stdout.getvalue(), "")

    def test_chat_assembles_chunks_and_preserves_trailing_usage(self):
        self.llm.config = LlmConfig("https://example.invalid", "test-key", "test", "chat_completions")
        stream = StreamResponse([
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]},
            {"choices": [{"index": 0, "delta": {"content": '{"x":'}}]},
            {"choices": [{"index": 0, "delta": {"content": '"中文"}'}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"total_tokens": 8}}, "[DONE]",
        ], self.clock)
        with patch("urllib.request.urlopen", return_value=stream) as request:
            self.assertEqual(self.llm.request_json("test", {}, {"type": "object"}, "plan"), {"x": "中文"})
        self.assertEqual(self.timings()[0]["ttft_seconds"], 1.5)
        self.assertEqual(self.timings()[0]["usage"]["total_tokens"], 8)
        self.assertTrue(json.loads(request.call_args.args[0].data)["stream_options"]["include_usage"])

    def test_non_streaming_and_embedding_do_not_invent_ttft(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse({"output_text": "{}"})):
            self.llm.request_json("test", {}, {"type": "object"}, "plan")
            self.llm.request_payload({"input": ["test"]}, "memory.embedding")
        self.assertEqual([r["ttft_status"] for r in self.timings()], ["non_streaming_response", "not_applicable"])
        self.assertTrue(all(r["ttft_seconds"] is None for r in self.timings()))
        self.assertEqual(self.stderr.getvalue().count("TTFT: N/A"), 2)

    def test_each_retry_has_independent_timings_and_request_id(self):
        def send(*args, **kwargs):
            self.clock[0] += 4
            if self.llm.request_count == 1:
                raise urllib.error.URLError("temporary test-key")
            return self.response_stream()
        with patch("urllib.request.urlopen", side_effect=send):
            self.llm.request_json("test", {}, {"type": "object"}, "plan")
        first, second = self.timings()
        self.assertIsNone(first["ttft_seconds"])
        self.assertEqual(first["total_duration_seconds"], 4)
        self.assertEqual(second["ttft_seconds"], 6)
        self.assertEqual(second["total_duration_seconds"], 7)
        self.assertNotEqual(first["request_id"], second["request_id"])
        self.assertNotIn("test-key", str(self.rows()) + self.stderr.getvalue())
        self.assertEqual(self.stderr.getvalue().count("[LLM 耗时]"), 2)

    def test_failure_and_cancellation_always_record_timing(self):
        failures = [TimeoutError("timeout"), KeyboardInterrupt(),
                    urllib.error.HTTPError("url", 400, "bad", {}, io.BytesIO(b"bad"))]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.llm.max_requests = self.llm.request_count + 1
                with patch("urllib.request.urlopen", side_effect=failure):
                    with self.assertRaises((LlmError, KeyboardInterrupt)):
                        self.llm.request_json("test", {}, {"type": "object"}, "plan")
        self.assertEqual(len(self.timings()), 3)
        self.assertTrue(all(r["ttft_seconds"] is None for r in self.timings()))

    def test_partial_output_keeps_ttft_on_timeout_and_eof(self):
        for fail in (True, False):
            self.llm.max_requests = self.llm.request_count + 1
            stream = StreamResponse([{"type": "response.output_text.delta", "delta": "{"}], self.clock, fail=fail)
            with patch("urllib.request.urlopen", return_value=stream):
                with self.assertRaises(LlmError):
                    self.llm.request_json("test", {}, {"type": "object"}, "plan")
        for row in self.timings():
            self.assertEqual(row["ttft_seconds"], 1)
            self.assertGreater(row["total_duration_seconds"], row["ttft_seconds"])
            self.assertEqual(row["event"], "llm.error")

    def test_incomplete_and_malformed_stream_logged(self):
        for stream in (self.response_stream("incomplete"), StreamResponse(["broken"], self.clock)):
            with patch("urllib.request.urlopen", return_value=stream):
                with self.assertRaises(LlmError):
                    self.llm.request_json("test", {}, {"type": "object"}, "plan")
        self.assertEqual(len(self.timings()), 2)
        self.assertTrue(all(r["event"] == "llm.error" for r in self.timings()))

    def test_invalid_json_still_prints_and_logs(self):
        response = FakeResponse({})
        response.read = lambda: b"not json"
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(LlmError):
                self.llm.request_json("test", {}, {"type": "object"}, "plan")
        self.assertEqual(self.timings()[0]["raw"], "not json")
        self.assertEqual(self.stderr.getvalue().count("[LLM 耗时]"), 1)
