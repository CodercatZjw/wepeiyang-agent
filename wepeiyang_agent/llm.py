from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
import base64
import mimetypes
from pathlib import Path
from dataclasses import dataclass

from .config import LlmConfig
from .trace import Trace


class LlmError(RuntimeError):
    pass


class LlmTransportError(LlmError):
    pass


class LlmBudgetError(LlmError):
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
    def __init__(self, config: LlmConfig, trace: Trace | None = None):
        self.config = config
        self.trace = trace or Trace(Path(__file__).resolve().parents[1] / "data" / "traces", (config.api_key,))
        self.deadline = None
        self.request_count = 0
        self.max_requests = None

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

    def request_json(
        self,
        system_prompt: str,
        input_payload: dict,
        schema: dict,
        schema_name: str,
        max_output_tokens: int = 600,
        images: list[Path] | None = None,
    ) -> dict:
        prompt = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
        if self.config.api_format == "responses":
            payload = {
                "model": self.config.model,
                "instructions": system_prompt,
                "input": prompt,
                "max_output_tokens": max_output_tokens,
                "store": False,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
            }
        else:
            payload = {
                "model": self.config.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            }

        if images:
            parts = []
            for path in images:
                mime = mimetypes.guess_type(str(path))[0]
                if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                    raise ValueError("不支持的图像格式")
                if path.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError("单张图片不能超过 20 MB")
                data_url = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
                parts.append({"type": "input_image", "image_url": data_url} if self.config.api_format == "responses"
                             else {"type": "image_url", "image_url": {"url": data_url}})
            if self.config.api_format == "responses":
                payload["input"] = [{"role": "user", "content": [{"type": "input_text", "text": prompt}, *parts]}]
            else:
                payload["messages"][-1]["content"] = [{"type": "text", "text": prompt}, *parts]
        response_payload = self.request_payload(payload, schema_name)
        try:
            result = _extract_json(self._response_text(response_payload))
            from jsonschema import validate, ValidationError
            try:
                validate(result, schema)
            except ValidationError as exc:
                raise LlmError(f"模型结果不符合 {schema_name} 参数结构：{exc.message}") from exc
            return result
        except (LlmError, ValueError) as exc:
            self.trace.record("llm.validation_error", purpose=schema_name, error=str(exc))
            raise

    def request_payload(self, payload: dict, purpose: str, url: str | None = None,
                        api_key: str | None = None) -> dict:
        key = self.config.api_key if api_key is None else api_key
        self.trace.secrets = tuple(value for value in set((*self.trace.secrets, key)) if value)
        request = urllib.request.Request(
            url or self.config.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "wepeiyang-agent/0.4",
            },
        )
        raw: bytes | None = None
        last_error: BaseException | None = None
        for attempt in range(3):
            retry_delay = attempt + 1
            remaining = self.deadline - time.monotonic() if self.deadline else self.config.timeout_seconds
            if remaining <= 0 or (self.max_requests is not None and self.request_count >= self.max_requests):
                raise LlmBudgetError("运行预算已耗尽，尚未完成的工作已保留在日志中。")
            self.request_count += 1
            request_id = uuid.uuid4().hex
            self.trace.record("llm.request", request_id=request_id, attempt=attempt + 1,
                              purpose=purpose, url=url or self.config.url, payload=payload)
            started = time.monotonic()
            try:
                with urllib.request.urlopen(
                    request, timeout=min(self.config.timeout_seconds, remaining)
                ) as response:
                    raw = response.read()
                decoded = raw.decode("utf-8", "replace")
                try:
                    response_payload = json.loads(decoded)
                except json.JSONDecodeError:
                    self.trace.record("llm.response", request_id=request_id, raw=decoded,
                                      elapsed_seconds=time.monotonic() - started)
                    raise LlmError("LLM API 没有返回有效 JSON 响应。")
                self.trace.record("llm.response", request_id=request_id, payload=response_payload,
                                  usage=response_payload.get("usage") if isinstance(response_payload, dict) else None,
                                  elapsed_seconds=time.monotonic() - started)
                if not isinstance(response_payload, dict):
                    raise LlmError("LLM 响应不是对象")
                if response_payload.get("error") or response_payload.get("status") in {"failed", "incomplete"}:
                    raise LlmError(f"LLM 未完成响应：{response_payload.get('error') or response_payload.get('incomplete_details')}")
                break
            except urllib.error.HTTPError as exc:
                details = exc.read().decode("utf-8", "replace")
                self.trace.record("llm.error", request_id=request_id, status=exc.code, error=details,
                                  elapsed_seconds=time.monotonic() - started)
                retryable = exc.code in {408, 429} or exc.code >= 500
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    if retry_after is None:
                        retry_after = json.loads(details).get("retry_after")
                    if retry_after is not None:
                        retry_delay = min(300, max(retry_delay, float(retry_after)))
                except (ValueError, TypeError, AttributeError):
                    pass
                if not retryable or attempt == 2:
                    raise LlmTransportError(f"LLM API 返回 HTTP {exc.code}：{self.trace.clean(details[:1000])}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                self.trace.record("llm.error", request_id=request_id, error=str(exc),
                                  elapsed_seconds=time.monotonic() - started)
                if attempt == 2:
                    raise LlmTransportError(f"无法连接 LLM API：{self.trace.clean(str(exc))}") from exc
                last_error = exc
            except KeyboardInterrupt:
                self.trace.record("llm.error", request_id=request_id, error="cancelled",
                                  elapsed_seconds=time.monotonic() - started)
                raise
            if self.deadline and time.monotonic() + retry_delay >= self.deadline:
                raise LlmBudgetError("重试等待将超过运行预算；保留状态后退出。")
            self.trace.record("llm.retry_wait", purpose=purpose, seconds=retry_delay)
            time.sleep(retry_delay)
        if raw is None:
            raise LlmError(f"无法连接 LLM API：{last_error}")
        try:
            response_payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LlmError("LLM API 没有返回有效 JSON 响应。") from exc
        return response_payload

    def decide(self, observation: dict) -> LlmDecision:
        decision_payload = self.request_json(
            SYSTEM_PROMPT,
            observation,
            ACTION_SCHEMA,
            "browse_action",
            max_output_tokens=200,
        )
        action = str(decision_payload.get("action", "")).strip().lower()
        reason = str(decision_payload.get("reason", "")).strip()
        if action not in {"scroll", "stop"}:
            raise LlmError(f"模型要求了未授权动作：{action or '(空)'}")
        if not reason:
            reason = "模型未提供原因"
        return LlmDecision(action=action, reason=reason)
