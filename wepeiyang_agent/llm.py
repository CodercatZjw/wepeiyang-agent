from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import LlmConfig


class LlmError(RuntimeError):
    pass


ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["scroll", "stop"]},
        "reason": {"type": "string"},
    },
    "required": ["action", "reason"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """你是一个只读的安卓论坛浏览决策器。
你不能发帖、回复、点赞、点踩、收藏、搜索或打开用户资料。
执行层只允许两个动作：
- scroll：继续向下滚动一屏。
- stop：结束本次浏览。
结合目标、页数、去重情况和当前帖子摘要决定动作。到达信息饱和、连续没有新帖或继续滚动价值很低时停止。
只输出符合给定结构的 JSON，不要输出 Markdown。"""


@dataclass(frozen=True, slots=True)
class LlmDecision:
    action: str
    reason: str


def _extract_json(text: str) -> dict:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, flags=re.DOTALL)
        if not match:
            raise LlmError(f"模型没有返回 JSON：{text[:300]}")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LlmError(f"模型返回了无法解析的 JSON：{text[:300]}") from exc
    if not isinstance(parsed, dict):
        raise LlmError("模型返回的顶层结果不是 JSON 对象。")
    return parsed


class LlmController:
    def __init__(self, config: LlmConfig):
        self.config = config

    def _request_payload(self, observation: dict) -> dict:
        prompt = json.dumps(observation, ensure_ascii=False, separators=(",", ":"))
        if self.config.api_format == "responses":
            return {
                "model": self.config.model,
                "instructions": SYSTEM_PROMPT,
                "input": prompt,
                "max_output_tokens": 200,
                "store": False,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "browse_action",
                        "strict": True,
                        "schema": ACTION_SCHEMA,
                    }
                },
            }
        return {
            "model": self.config.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }

    def _response_text(self, payload: dict) -> str:
        if self.config.api_format == "chat_completions":
            try:
                content = payload["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LlmError("Chat Completions 响应中没有 choices[0].message.content。") from exc
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            raise LlmError("Chat Completions 响应正文格式无法识别。")

        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]
        texts: list[str] = []
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    texts.append(str(content.get("text", "")))
        if not texts:
            raise LlmError("Responses API 响应中没有 output_text。")
        return "".join(texts)

    def decide(self, observation: dict) -> LlmDecision:
        request_body = json.dumps(
            self._request_payload(observation), ensure_ascii=False
        ).encode("utf-8")
        request = urllib.request.Request(
            self.config.url,
            data=request_body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "wepeiyang-browse-agent/0.2",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", "replace")[:1000]
            raise LlmError(f"LLM API 返回 HTTP {exc.code}：{details}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LlmError(f"无法连接 LLM API：{exc}") from exc
        try:
            response_payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LlmError("LLM API 没有返回有效 JSON 响应。") from exc

        decision_payload = _extract_json(self._response_text(response_payload))
        action = str(decision_payload.get("action", "")).strip().lower()
        reason = str(decision_payload.get("reason", "")).strip()
        if action not in {"scroll", "stop"}:
            raise LlmError(f"模型要求了未授权动作：{action or '(空)'}")
        if not reason:
            reason = "模型未提供原因"
        return LlmDecision(action=action, reason=reason)

