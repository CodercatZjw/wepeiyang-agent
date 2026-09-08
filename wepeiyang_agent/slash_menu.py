"""Local-only slash commands. A pending provider never replaces an active one."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from .providers import ModelDiscovery, Provider, ProviderStore, clean_field, endpoints
from .terminal_ui import COMMANDS, TerminalUI


class SlashMenu:
    def __init__(self, agent, ui=None, store=None, discovery=None):
        self.agent = agent
        self.ui = ui or TerminalUI()
        config_path = agent.config_path
        path = config_path.with_name(config_path.stem + ".providers.local.json")
        self.store = store or ProviderStore(path, agent.config.llm)
        self.active = self.store.initial
        self.discovery = discovery or ModelDiscovery(agent.llm.trace)

    def handle(self, instruction):
        if not instruction.startswith("/"):
            return False
        try:
            command = instruction.strip().lower()
            if command == "/":
                command = self.ui.choose("命令菜单", COMMANDS)
            if command is None:
                return True
            if command not in {"/model", "/provider"}:
                self.ui.message("未知斜杠命令。可用：/model、/provider；输入 / 查看菜单。")
                return True
            # Use the current trace after an agent run has replaced the old one.
            self.discovery.trace = self.agent.llm.trace
            target = self.active
            adding = False
            if command == "/provider":
                providers = self.store.list()
                choices = []
                for provider in providers:
                    models = self.discovery.cache.get(provider.id)
                    count = f"{len(models)} models" if models is not None else "模型待加载"
                    current = " ← current" if provider.id == self.active.id else ""
                    choices.append((provider.id, f"{provider.name} ({count}) [{provider.id[:6]}]{current}"))
                choices.append(("@add", "+ 添加 Provider"))
                chosen = self.ui.choose("Model Picker — Select Provider", choices,
                    default=self.active.id, subtitle=self._current())
                if chosen is None:
                    return True
                if chosen == "@add":
                    target = self._add_form()
                    if target is None:
                        return True
                    adding = True
                else:
                    target = next(p for p in providers if p.id == chosen)
            model = self._model(target)
            if model is None:
                self.ui.message("已取消；当前 Provider 和模型未改变。")
                return True
            # Validate and persist first. On cancellation or save failure, keep both old values.
            new_llm = target.llm_config(model, self.agent.config.llm)
            if adding:
                self.store.add(target)
            memory = self.agent.config.memory
            if memory.embedding_provider == "remote" and not memory.embedding_api_key:
                # Switching chat providers must not send the new key to the old embedding host.
                memory = replace(memory, embedding_api_key=self.agent.config.llm.api_key)
            self.agent.config = replace(self.agent.config, llm=new_llm, memory=memory)
            self.agent.llm.config = new_llm
            self.agent.llm.trace.secrets = tuple(set((*self.agent.llm.trace.secrets, target.api_key, memory.embedding_api_key)))
            self.active = target
            self.agent.llm.trace.record("console.model_changed", provider=target.name, model=model,
                                        api_format=target.api_format, scope="session")
            self.ui.message(f"已切换：{model} on {target.name}（当前 CMD 会话生效；对话历史保留）")
            if adding:
                self.ui.message(f"Provider 已保存到 {self.store.path}（含本地 API Key，请勿分享）。")
        except (KeyboardInterrupt, EOFError):
            self.ui.message("已取消菜单，当前选择未改变。")
        except (OSError, ValueError) as exc:
            # Never emit arbitrary provider/key data from failures.
            self.ui.message("菜单操作未完成：" + str(self.agent.llm.trace.clean(str(exc))))
        return True

    def _current(self):
        return f"Current: {self.agent.config.llm.model} on {self.active.name}\n仅本会话切换；方向键选择，Enter 确认，Esc 取消"

    def _add_form(self):
        self.ui.message("添加 OpenAI-compatible Provider；支持 Responses / Chat Completions，Ctrl+C 取消。")
        url = self.ui.text("URL（基础地址或完整接口地址）").strip()
        _, _, api_format = endpoints(url, self.agent.config.llm.api_format)
        if not url.rstrip("/").endswith(("/responses", "/chat/completions")):
            api_format = self.ui.choose("接口类型", [("responses", "Responses"),
                ("chat_completions", "Chat Completions")], default=api_format,
                subtitle="只给基础地址时请选择该 Provider 支持的接口类型")
            if api_format is None:
                return None
        key = clean_field(self.ui.text("API Key（隐藏输入）", secret=True), "API Key", 4096)
        self.agent.llm.trace.secrets = tuple(set((*self.agent.llm.trace.secrets, key)))
        name = self.ui.text("显示名称", default=urlsplit(url).hostname or "自定义 Provider").strip()
        return Provider.create(url, key, name, api_format)

    def _model(self, target):
        previous = self.agent.config.llm.model
        while True:
            self.ui.message(f"正在获取 {target.name} 的模型列表…")
            try:
                models = self.discovery.fetch(target)
            except ValueError as exc:
                self.ui.message(str(exc))
                option = self.ui.choose("模型列表不可用", [("retry", "重试"), ("manual", "手动输入模型 ID")],
                                        subtitle="尚未切换 Provider 或模型；手动输入不代表服务已验证兼容性")
                if option == "retry":
                    continue
                if option == "manual":
                    return self._manual_model(target)
                return None
            default = "model:" + previous if previous in models else None
            choices = [("model:" + model, model + (" ← 同名 / current" if model == previous else "")) for model in models]
            choices.append(("@manual", "手动输入模型 ID…"))
            chosen = self.ui.choose(f"Model Picker — {target.name}", choices, default=default,
                subtitle=f"{self._current()}\n{len(models)} available · 同名精确匹配仅高亮，必须按 Enter 确认")
            if chosen is None:
                return None
            return self._manual_model(target) if chosen == "@manual" else chosen[len("model:"):]

    def _manual_model(self, target):
        model = clean_field(self.ui.text("模型 ID（原样填写）"), "模型名称")
        confirmed = self.ui.choose("确认模型", [("confirm", f"使用 {model} on {target.name}")],
                                   subtitle="手动填写：尚未验证模型存在或支持当前接口。Enter 确认，Esc 取消。")
        return model if confirmed else None
