from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wepeiyang_agent.config import AppConfig, AgentConfig, LlmConfig, RuntimeConfig
from wepeiyang_agent.runtime import AgentRuntime
from wepeiyang_agent.skills import SkillRegistry
from wepeiyang_agent.llm import LlmTransportError


def step(skill="", arguments=None, complete=False, answer="", task_id="t1"):
    return {"decision_summary": "验证后执行下一步", "tasks": [{"id": task_id, "title": "用户任务",
        "status": "done" if complete else "pending", "depends_on": []}], "task_id": task_id,
        "skill": skill, "arguments_json": json.dumps(arguments or {}), "answer": answer,
        "status": "complete" if complete else "continue"}


def check(complete=False, passed=True):
    return {"decision_summary": "证据已核对", "passed": passed, "complete": complete,
            "next_step": "继续" if not complete else "", "evidence_ids": []}


class FakeLlm:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.inputs = []
        self.request_count = 0

    def request_json(self, system, payload, schema, schema_name, **kwargs):
        self.inputs.append((schema_name, payload))
        self.request_count += 1
        return next(self.responses)


class RuntimeTests(unittest.TestCase):
    def config(self, steps=20):
        return AppConfig(LlmConfig("https://example.invalid", "secret", "test"), AgentConfig("test"), RuntimeConfig(max_steps=steps))

    def test_chat_never_opens_emulator_and_persists_history(self):
        with tempfile.TemporaryDirectory() as d:
            llm = FakeLlm([{**step(complete=True, answer="你好"), "tasks": [], "task_id": ""}])
            runtime = AgentRuntime(self.config(), Path(d), llm, lambda: self.fail("不应启动模拟器"), emit=None)
            with patch("wepeiyang_agent.memory.MemoryStore.maintain", side_effect=AssertionError("闲聊不做维护")):
                result = runtime.run("你好")
            self.assertEqual(result["status"], "complete")
            self.assertEqual(llm.request_count, 1)
            self.assertEqual(llm.inputs[0][0], "agent_decide")
            self.assertEqual(json.loads((Path(d) / "sessions/default.json").read_text(encoding="utf-8"))[-1]["content"], "你好")

    def test_composes_file_write_read_and_answer_with_check(self):
        responses = [step("file.write", {"path": "note.md", "content": "测试信息"}), check(),
                     step("file.read", {"path": "note.md"}), check(),
                     step(complete=True, answer="已保存并核对 [E1][E2]"), check(True)]
        with tempfile.TemporaryDirectory() as d:
            llm = FakeLlm(responses)
            result = AgentRuntime(self.config(), Path(d), llm, lambda: None, emit=None).run("保存测试信息并检查")
            self.assertEqual(result["status"], "complete")
            state = json.loads((Path(result["trace_dir"]) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["observations"]), 2)
            self.assertTrue(state["observations"][0]["result"]["verified"])

    def test_not_stopped_by_three_posts_and_checker_can_reject_finish(self):
        responses = []
        for _ in range(4):
            responses.extend([step("forum.next"), check()])
        responses.extend([step(complete=True, answer="第一次回答"), check(False, False),
                          step(complete=True, answer="补充后的答案"), check(True)])
        with tempfile.TemporaryDirectory() as d, patch.object(SkillRegistry, "invoke", return_value={"posts": [{"post_id": "MP1"}]}):
            result = AgentRuntime(self.config(), Path(d), FakeLlm(responses), lambda: None, emit=None).run("刷帖收集")
            self.assertEqual(result["answer"], "补充后的答案")

    def test_budget_is_incomplete(self):
        with tempfile.TemporaryDirectory() as d:
            result = AgentRuntime(self.config(1), Path(d), FakeLlm([step("file.list"), check()]), lambda: None, emit=None).run("持续查找")
            self.assertEqual(result["status"], "budget_exhausted")

    def test_checker_can_deliver_verified_answer_without_extra_planning(self):
        with tempfile.TemporaryDirectory() as d:
            llm = FakeLlm([step("file.write", {"path": "ok.txt", "content": "ok"}),
                           {**check(True), "answer": "已保存并回读验证 [E1]"}])
            result = AgentRuntime(self.config(), Path(d), llm, lambda: None, emit=None).run("保存ok")
            self.assertEqual(result["status"], "complete")
            self.assertEqual(llm.request_count, 2)

    def test_resume_preserves_evidence_without_replaying_file_write(self):
        with tempfile.TemporaryDirectory() as d:
            first = AgentRuntime(self.config(1), Path(d), FakeLlm([
                step("file.write", {"path": "note.md", "content": "保存一次"}), check()]), lambda: None, emit=None).run("保存后读取")
            resumed = AgentRuntime(self.config(), Path(d), FakeLlm([
                step("file.read", {"path": "note.md"}), check(),
                step(complete=True, answer="已核对 [E1][E2]"), check(True)]), lambda: None, emit=None).run("", resume=first["run_id"])
            state = json.loads((Path(resumed["trace_dir"]) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["resumed_from"], first["run_id"])
            self.assertEqual([r["skill"] for r in state["observations"]], ["file.write", "file.read"])
            self.assertFalse((Path(d) / "files/.history").exists())

    def test_transport_failure_does_not_restart_an_outer_retry_loop(self):
        with tempfile.TemporaryDirectory() as d:
            llm = FakeLlm([])
            with patch.object(llm, "request_json", side_effect=LlmTransportError("service unavailable")) as request:
                with self.assertRaises(LlmTransportError):
                    AgentRuntime(self.config(), Path(d), llm, lambda: None, emit=None).run("你好")
            self.assertEqual(request.call_count, 1)

    def test_corrected_final_answer_does_not_recheck_old_citation(self):
        with tempfile.TemporaryDirectory() as d:
            llm = FakeLlm([step("file.list"), check(),
                step(complete=True, answer="错误旧引用 [E999]"),
                {**check(True), "answer": "目录为空 [E1]", "evidence_ids": ["E1"]}])
            result = AgentRuntime(self.config(), Path(d), llm, lambda: None, emit=None).run("看看文件")
            self.assertEqual(result["answer"], "目录为空 [E1]")
            self.assertEqual(llm.request_count, 4)

    def test_app_navigation_skips_per_click_llm_check_but_checks_final(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            image = Path(d) / "screen.png"
            Image.new("RGB", (20, 20)).save(image)
            llm = FakeLlm([step("app.observe"), step("app.tap", {"screen_id": "s1", "node_id": "n1"}),
                step(complete=True, answer="页面已打开 [E2]"), {**check(True), "answer": "已核对新页面 [E2]"}])
            with patch.object(SkillRegistry, "invoke", return_value={"image": str(image), "screen_id": "s1"}), \
                 patch.object(llm, "request_json", wraps=llm.request_json) as request:
                result = AgentRuntime(self.config(), Path(d), llm, lambda: None, emit=None).run("打开页面")
            self.assertEqual([name for name, _ in llm.inputs], ["agent_decide", "agent_plan", "agent_plan", "agent_check"])
            self.assertEqual(request.call_args_list[1].kwargs["images"], [image.resolve()])
            self.assertEqual(result["answer"], "已核对新页面 [E2]")

    def test_app_error_is_observed_and_budget_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as d, patch.object(SkillRegistry, "invoke", side_effect=ValueError("旧页面")):
            result = AgentRuntime(self.config(1), Path(d), FakeLlm([step("app.observe")]), lambda: None, emit=None).run("打开页面")
            self.assertEqual(result["status"], "budget_exhausted")
            state = json.loads((Path(result["trace_dir"]) / "state.json").read_text(encoding="utf-8"))
            self.assertFalse(state["checks"][-1]["passed"])

    def test_pure_reply_cannot_cite_nonexistent_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            llm = FakeLlm([{**step(complete=True, answer="你好 [E1]"), "tasks": []},
                           {**step(complete=True, answer="你好"), "tasks": []}])
            result = AgentRuntime(self.config(), Path(d), llm, lambda: None, emit=None).run("你好")
            self.assertEqual(result["answer"], "你好")
            self.assertEqual(llm.request_count, 2)

    def test_rejects_cyclic_task_graph(self):
        value = step("file.list")
        value["tasks"][0]["depends_on"] = ["t1"]
        with self.assertRaises(ValueError):
            AgentRuntime._validate_plan(value)

    def test_rejects_outside_path_and_unknown_skill(self):
        with tempfile.TemporaryDirectory() as d:
            registry = SkillRegistry(Path(d), None, None, lambda: None)
            with self.assertRaises(ValueError):
                registry.invoke("file.write", {"path": "../outside.txt", "content": "bad"})
            with self.assertRaises(ValueError):
                registry.invoke("forum.like", {})


if __name__ == "__main__":
    unittest.main()
