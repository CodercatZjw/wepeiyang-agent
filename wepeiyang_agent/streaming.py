"""Decode SSE without mistaking headers, keepalives or role deltas for tokens."""
from __future__ import annotations

import json


def _events(response):
    lines = []
    while True:
        raw = response.readline()
        if not raw:
            if lines:
                yield "\n".join(lines)
            return
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if lines:
                yield "\n".join(lines)
                lines = []
        elif line.startswith("data:"):
            value = line[5:]
            lines.append(value[1:] if value.startswith(" ") else value)


def read_stream(response, api_format, first_text, events, check_budget):
    """Return the normal response shape; incomplete streams must never succeed."""
    chat = {"choices": []}
    choices = {}
    for data in _events(response):
        check_budget()
        if data == "[DONE]":
            if api_format != "chat_completions" or not choices or any(
                item["finish_reason"] is None for item in choices.values()
            ):
                raise EOFError("LLM 流结束，但没有完整响应。")
            chat["choices"] = [choices[index] for index in sorted(choices)]
            return chat
        event = json.loads(data)
        if not isinstance(event, dict):
            raise ValueError("LLM 流事件不是对象。")
        events.append(event)
        if event.get("error") or event.get("type") == "error":
            return {"error": event.get("error") or event}
        if api_format == "responses":
            kind = event.get("type")
            if kind in {"response.output_text.delta", "response.refusal.delta"} and event.get("delta"):
                first_text()
            if kind in {"response.completed", "response.failed", "response.incomplete", "response.cancelled"}:
                result = event.get("response")
                if not isinstance(result, dict):
                    raise ValueError("LLM 流结束事件缺少 response。")
                return {**result, "status": kind.split(".", 1)[1]}
        else:
            for key in ("id", "model", "created", "usage", "system_fingerprint"):
                if event.get(key) is not None:
                    chat[key] = event[key]
            for delta in event.get("choices", []):
                index = delta.get("index", 0)
                choice = choices.setdefault(index, {
                    "index": index, "message": {"role": "assistant", "content": ""}, "finish_reason": None,
                })
                part = delta.get("delta", {})
                for field in ("content", "refusal"):
                    text = part.get(field)
                    if isinstance(text, str) and text:
                        first_text()
                        choice["message"][field] = choice["message"].get(field, "") + text
                if delta.get("finish_reason") is not None:
                    choice["finish_reason"] = delta["finish_reason"]
                    if delta["finish_reason"] != "stop":
                        chat["status"] = "incomplete"
                        chat["incomplete_details"] = {"reason": delta["finish_reason"]}
    raise EOFError("LLM 流意外断开，未收到完成事件。")
