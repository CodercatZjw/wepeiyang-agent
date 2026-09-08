import asyncio
import io
import json
import tempfile
import unittest
import urllib.error
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from wepeiyang_agent.config import AppConfig, AgentConfig, LlmConfig
from wepeiyang_agent.console_agent import ConsoleAgent, run_console
from wepeiyang_agent.providers import Provider, ProviderStore, ModelDiscovery, NoRedirect, endpoints
from wepeiyang_agent.slash_menu import SlashMenu
from wepeiyang_agent.terminal_ui import PickerState, SlashCompleter, picker_application
from wepeiyang_agent.trace import Trace


class FakeUI:
    def __init__(self, choices=(), texts=()):
        self.choices, self.texts = iter(choices), iter(texts)
        self.menus, self.messages = [], []

    def choose(self, title, choices, default=None, subtitle=""):
        self.menus.append({"title": title, "choices": choices, "default": default})
        result = next(self.choices)
        if callable(result):
            return result()
        return result

    def text(self, *args, **kwargs):
        return next(self.texts)

    def message(self, value):
        self.messages.append(value)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        config = AppConfig(LlmConfig("https://old.invalid/v1/responses", "old-key", "shared", "responses"), AgentConfig("test"))
        self.agent = ConsoleAgent(config, self.root, config_path=self.root / "custom.json")
        self.agent.llm.trace = Trace(self.root / "traces", ("old-key",))
        self.store = ProviderStore(self.root / "custom.providers.local.json", config.llm)
        self.other = Provider.create("https://other.invalid/v1/chat/completions", "new-key")
        self.discovery = Mock(cache={})
        self.discovery.fetch.return_value = ["a", "shared", "z"]

    def menu(self, choices, texts=()):
        self.ui = FakeUI(choices, texts)
        return SlashMenu(self.agent, self.ui, self.store, self.discovery)

    def test_url_normalization_and_format(self):
        cases = [("https://x.invalid", "https://x.invalid/v1/responses", "https://x.invalid/v1/models", "responses"),
                 ("https://x.invalid/v1/", "https://x.invalid/v1/responses", "https://x.invalid/v1/models", "responses"),
                 ("https://x.invalid/api/v1/chat/completions", "https://x.invalid/api/v1/chat/completions", "https://x.invalid/api/v1/models", "chat_completions")]
        for url, generation, models, protocol in cases:
            self.assertEqual(endpoints(url), (generation, models, protocol))
        for bad in ("ftp://x.invalid", "https://user:key@x.invalid", "https://x.invalid?api_key=secret", "http://remote.invalid"):
            with self.assertRaises(ValueError):
                endpoints(bad)

    def test_model_change_affects_next_generation_without_resetting_session(self):
        menu = self.menu(["model:z"])
        original = self.agent.llm
        menu.handle("/model")
        self.assertIs(self.agent.llm, original)
        self.assertEqual(self.agent.llm.config.model, "z")
        self.assertEqual(self.agent.config.llm.model, "z")
        self.assertEqual(self.agent.llm.config.api_key, "old-key")
        self.assertEqual(self.agent.session, "default")
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.ui.menus[0]["default"], "model:shared")
        from test_llm import FakeResponse
        with patch("urllib.request.urlopen", return_value=FakeResponse({"output_text": "{}"})) as request:
            self.agent.llm.request_json("test", {}, {"type": "object"}, "test")
        self.assertEqual(json.loads(request.call_args.args[0].data)["model"], "z")

    def test_provider_change_is_pending_until_model_enter(self):
        self.store.add(self.other)
        def confirm():
            self.assertEqual(self.agent.llm.config.api_key, "old-key")
            self.assertEqual(self.agent.config.llm.model, "shared")
            return "model:shared"
        menu = self.menu([self.other.id, confirm])
        menu.handle("/provider")
        self.assertEqual(self.ui.menus[-1]["default"], "model:shared")
        self.assertEqual(self.agent.config.llm.api_key, "new-key")
        self.assertEqual(self.agent.config.llm.api_format, "chat_completions")
        self.assertEqual(self.agent.config.llm.url, self.other.url)

    def test_matching_is_exact_not_casefold(self):
        self.discovery.fetch.return_value = ["Shared"]
        menu = self.menu([None])
        menu.handle("/model")
        self.assertIsNone(self.ui.menus[0]["default"])
        self.assertEqual(self.agent.config.llm.model, "shared")

    def test_cancel_model_rolls_back_provider_and_key(self):
        self.store.add(self.other)
        previous = self.agent.config
        self.menu([self.other.id, None]).handle("/provider")
        self.assertIs(self.agent.config, previous)

    def test_cancel_new_provider_does_not_save_secret(self):
        self.menu(["@add", None], ["https://new.invalid/v1/responses", "new-key", ""]).handle("/provider")
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.agent.config.llm.api_key, "old-key")

    def test_add_persists_profile_but_does_not_overwrite_original_config(self):
        original = self.root / "custom.json"
        original.write_text("original")
        self.menu(["@add", "model:shared"], ["https://new.invalid/v1/responses", "new-key", ""]).handle("/provider")
        self.assertEqual(original.read_text(), "original")
        saved = self.store.list()
        self.assertEqual(saved[-1].name, "new.invalid")
        self.assertEqual(saved[-1].api_key, "new-key")
        self.assertNotIn("new-key", str(self.ui.messages))
        self.assertNotIn("new-key", (self.agent.llm.trace.directory / "events.jsonl").read_text())

    def test_save_failure_does_not_change_active_provider(self):
        previous = self.agent.config
        with patch.object(self.store, "add", side_effect=OSError("disk failure")):
            self.menu(["@add", "model:shared"], ["https://new.invalid/responses", "new-key", "New"]).handle("/provider")
        self.assertIs(self.agent.config, previous)

    def test_remote_embedding_retains_its_old_credential(self):
        self.agent.config = replace(self.agent.config, memory=replace(self.agent.config.memory,
            embedding_provider="remote", embedding_url="https://old.invalid/v1/embeddings"))
        self.store.add(self.other)
        self.menu([self.other.id, "model:shared"]).handle("/provider")
        self.assertEqual(self.agent.config.memory.embedding_api_key, "old-key")

    def test_discovery_failure_cancel_and_manual_fallback(self):
        self.discovery.fetch.side_effect = ValueError("offline")
        self.menu([None]).handle("/model")
        self.assertEqual(self.agent.config.llm.model, "shared")
        self.menu(["manual", "confirm"], ["private-model"]).handle("/model")
        self.assertEqual(self.agent.config.llm.model, "private-model")

    def test_slash_commands_never_reach_llm_or_forum(self):
        with patch.object(self.agent, "run", side_effect=AssertionError("不得请求 LLM")), \
             patch("wepeiyang_agent.slash_menu.TerminalUI", return_value=FakeUI([None])):
            self.assertEqual(run_console(self.agent, ask="/"), 0)
            self.assertEqual(run_console(self.agent, ask="/typo"), 0)

    def test_profile_parse_error_does_not_overwrite_file(self):
        self.store.path.write_text("broken")
        with self.assertRaises(ValueError):
            self.store.add(self.other)
        self.assertEqual(self.store.path.read_text(), "broken")

    def test_discovery_headers_parsing_and_redacted_error(self):
        discovery = ModelDiscovery(self.agent.llm.trace)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"data":[{"id":"z"},{"id":"a"},{"id":"z"}]}'
        opener = Mock()
        opener.open.return_value = response
        with patch("urllib.request.build_opener", return_value=opener):
            self.assertEqual(discovery.fetch(self.other), ["a", "z"])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://other.invalid/v1/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer new-key")
        opener.open.side_effect = urllib.error.HTTPError(request.full_url, 401, "new-key", {}, io.BytesIO(b"new-key"))
        with patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "HTTP 401"):
                discovery.fetch(self.other)
        self.assertNotIn("new-key", (self.agent.llm.trace.directory / "events.jsonl").read_text())
        self.assertIsNone(NoRedirect().redirect_request(request, None, 302, "", {}, "https://evil.invalid"))


class PickerTests(unittest.IsolatedAsyncioTestCase):
    async def run_picker(self, text, default="shared", choices=None):
        with create_pipe_input() as pipe:
            app = picker_application("Models", choices or [("a", "a"), ("shared", "shared"), ("z", "z")],
                                     default=default, input=pipe, output=DummyOutput())
            task = asyncio.create_task(app.run_async())
            await asyncio.sleep(.05)
            self.assertFalse(task.done(), "高亮不能自动确认")
            pipe.send_text(text)
            return await asyncio.wait_for(task, timeout=3)

    async def test_enter_confirms_default(self):
        self.assertEqual(await self.run_picker("\r"), "shared")

    async def test_arrow_changes_highlight_and_enter_confirms(self):
        self.assertEqual(await self.run_picker("\x1b[B\r"), "z")

    async def test_filter_then_enter(self):
        self.assertEqual(await self.run_picker("z\r"), "z")

    async def test_escape_and_ctrl_c_cancel(self):
        self.assertIsNone(await self.run_picker("\x1b"))
        self.assertIsNone(await self.run_picker("\x03"))

    async def test_no_matches_does_not_select_unrelated_model(self):
        self.assertIsNone(await self.run_picker("nomatch\r"))

    def test_slash_completer_and_preferred_model_filter(self):
        completions = list(SlashCompleter().get_completions(Document("/"), None))
        self.assertEqual([c.text for c in completions], ["/model", "/provider"])
        self.assertEqual(list(SlashCompleter().get_completions(Document("你好"), None)), [])
        state = PickerState([("a", "alpha"), ("s", "shared")], default="s")
        state.filter("a")
        self.assertEqual(state.selected(), "s")
