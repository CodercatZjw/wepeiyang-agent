"""Local provider profiles and read-only OpenAI-compatible model discovery."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from .config import LlmConfig


def clean_field(value, label, maximum=256):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{label}不能为空、过长或包含控制字符。")
    return value.strip()


def endpoints(url, api_format="responses"):
    url = clean_field(url, "URL", 2048).rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("URL 必须为 HTTP(S) 地址，不能含用户名、密码、查询参数或片段。")
    if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程 Provider 请使用 HTTPS，避免明文传输 API Key。")
    if api_format not in {"responses", "chat_completions"}:
        raise ValueError("接口类型必须为 responses 或 chat_completions。")
    for suffix, detected in (("/chat/completions", "chat_completions"), ("/responses", "responses")):
        if parts.path.endswith(suffix):
            base = url[:-len(suffix)]
            return url, base + "/models", detected
    base = url if parts.path else url + "/v1"
    suffix = "/responses" if api_format == "responses" else "/chat/completions"
    return base + suffix, base + "/models", api_format


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    url: str
    api_key: str = field(repr=False)
    api_format: str = "responses"

    @classmethod
    def create(cls, url, api_key, name="", api_format="responses", id=None):
        endpoint, _, api_format = endpoints(url, api_format)
        return cls(id or uuid.uuid4().hex,
            clean_field(name or urllib.parse.urlsplit(endpoint).hostname, "显示名称", 80), endpoint,
            clean_field(api_key, "API Key", 4096), api_format)

    def llm_config(self, model, previous: LlmConfig):
        return replace(previous, url=self.url, api_key=self.api_key, api_format=self.api_format,
                       model=clean_field(model, "模型名称"))


class ProviderStore:
    def __init__(self, path: Path, initial: LlmConfig):
        self.path = path
        self.initial = Provider.create(initial.url, initial.api_key, api_format=initial.api_format, id="config")

    def list(self):
        if not self.path.exists():
            return [self.initial]
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("version") != 1 or not isinstance(payload.get("providers"), list):
                raise ValueError("未知格式")
            result = [self.initial]
            for row in payload["providers"]:
                provider = Provider.create(**row)
                if provider.id in {p.id for p in result}:
                    raise ValueError("重复 ID")
                result.append(provider)
            return result
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError("本地 Provider 文件格式错误；未覆盖原文件。") from exc

    def add(self, provider):
        # Serialize independent console windows without losing either one's profiles.
        import portalocker
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(self.path) + ".lock", timeout=2):
            rows = self.list()
            if provider.id in {p.id for p in rows}:
                raise ValueError("Provider ID 已存在。")
            rows.append(provider)
            payload = {"version": 1, "providers": [asdict(p) for p in rows if p.id != "config"]}
            temporary = self.path.with_name(self.path.name + ".tmp")
            try:
                with temporary.open("w", encoding="utf-8") as stream:
                    os.chmod(temporary, 0o600)
                    json.dump(payload, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward provider credentials to a redirected host.
        return None


class ModelDiscovery:
    def __init__(self, trace):
        self.trace = trace
        self.cache = {}

    def fetch(self, provider):
        _, url, _ = endpoints(provider.url, provider.api_format)
        self.trace.secrets = tuple(set((*self.trace.secrets, provider.api_key)))
        request_id = uuid.uuid4().hex
        self.trace.record("provider.models.request", request_id=request_id, provider=provider.name, url=url)
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + provider.api_key,
                                                      "Accept": "application/json"})
        started = time.monotonic()
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("模型列表过大")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ValueError("模型列表格式不是 data 数组")
            models = sorted({clean_field(row["id"], "模型名称") for row in payload["data"]
                             if isinstance(row, dict) and isinstance(row.get("id"), str)})
            if not models:
                raise ValueError("Provider 返回了空模型列表")
            self.cache[provider.id] = models
            self.trace.record("provider.models.response", request_id=request_id, provider=provider.name,
                              models=models, total_duration_seconds=time.monotonic() - started)
            return models
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            message = f"获取模型列表失败（HTTP {code}），请检查地址和密钥。"
        except KeyboardInterrupt:
            self.trace.record("provider.models.error", request_id=request_id, provider=provider.name,
                              error="cancelled", total_duration_seconds=time.monotonic() - started)
            raise
        except (OSError, ValueError, urllib.error.URLError):
            # Do not echo raw gateway bodies, credentials or URLs embedded in errors.
            message = "无法读取模型列表：接口不兼容、网络异常、超时或返回空列表。"
        self.trace.record("provider.models.error", request_id=request_id, provider=provider.name,
                          error=message, total_duration_seconds=time.monotonic() - started)
        raise ValueError(message)
