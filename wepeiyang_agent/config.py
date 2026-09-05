from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LlmConfig:
    url: str
    api_key: str
    model: str
    api_format: str = "chat_completions"
    timeout_seconds: float = 60


@dataclass(frozen=True, slots=True)
class AgentConfig:
    goal: str
    max_pages: int = 8
    stop_after_stale_pages: int = 2
    settle_seconds: float = 1.8
    save_screenshots: bool = True
    send_body_chars: int = 240


@dataclass(frozen=True, slots=True)
class AppConfig:
    llm: LlmConfig
    agent: AgentConfig


def _required_string(section: dict, key: str, label: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"配置项 {label} 不能为空。")
    return value.strip()


def load_config(path: Path, require_llm: bool = True) -> AppConfig:
    if not path.exists():
        raise ConfigError(
            f"配置文件不存在：{path}。请复制 config.example.json 为 config.json 后填写。"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取配置文件 {path}：{exc}") from exc

    llm_data = payload.get("llm", {})
    agent_data = payload.get("agent", {})
    if not isinstance(llm_data, dict) or not isinstance(agent_data, dict):
        raise ConfigError("config.json 中的 llm 和 agent 必须是对象。")

    if require_llm:
        url = _required_string(llm_data, "url", "llm.url")
        api_key = _required_string(llm_data, "api_key", "llm.api_key")
        model = _required_string(llm_data, "model", "llm.model")
    else:
        url = str(llm_data.get("url", "")).strip()
        api_key = str(llm_data.get("api_key", "")).strip()
        model = str(llm_data.get("model", "")).strip()

    api_format = str(llm_data.get("api_format", "chat_completions")).strip()
    if api_format not in {"chat_completions", "responses"}:
        raise ConfigError("llm.api_format 只能是 chat_completions 或 responses。")

    goal = str(agent_data.get("goal", "浏览微北洋最新帖子并决定何时停止。")).strip()
    config = AppConfig(
        llm=LlmConfig(
            url=url,
            api_key=api_key,
            model=model,
            api_format=api_format,
            timeout_seconds=float(llm_data.get("timeout_seconds", 60)),
        ),
        agent=AgentConfig(
            goal=goal,
            max_pages=int(agent_data.get("max_pages", 8)),
            stop_after_stale_pages=int(agent_data.get("stop_after_stale_pages", 2)),
            settle_seconds=float(agent_data.get("settle_seconds", 1.8)),
            save_screenshots=bool(agent_data.get("save_screenshots", True)),
            send_body_chars=int(agent_data.get("send_body_chars", 240)),
        ),
    )
    if config.agent.max_pages < 1 or config.agent.stop_after_stale_pages < 1:
        raise ConfigError("agent.max_pages 和 agent.stop_after_stale_pages 必须大于 0。")
    if config.agent.send_body_chars < 0 or config.agent.send_body_chars > 4000:
        raise ConfigError("agent.send_body_chars 必须在 0 到 4000 之间。")
    return config

