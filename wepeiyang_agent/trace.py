"""Durable request and execution records; credentials never enter the trace."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import uuid
import os
from datetime import datetime, timezone
from pathlib import Path


class Trace:
    def __init__(self, root: Path, secrets=(), emit=None):
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        self.directory = root / self.run_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.secrets = tuple(value for value in secrets if value)
        self.emit = emit
        self.lock = threading.Lock()

    def clean(self, value):
        if isinstance(value, dict):
            return {k: "[REDACTED]" if re.search(r"api[_-]?key|authorization|password|secret", k, re.I)
                    else self.clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            if value.startswith("data:image/") and ";base64," in value:
                header, encoded = value.split(",", 1)
                raw = base64.b64decode(encoded)
                digest = hashlib.sha256(raw).hexdigest()
                folder = self.directory / "attachments"
                folder.mkdir(exist_ok=True)
                filename = digest + "." + header.split("/")[1].split(";")[0]
                (folder / filename).write_bytes(raw)
                return {"attachment": "attachments/" + filename, "sha256": digest,
                        "bytes": len(raw), "data_url_header": header}
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            return re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[REDACTED]", value)
        return value

    def record(self, event: str, **values):
        with self.lock:
            row = self.clean({"time": datetime.now(timezone.utc).isoformat(),
                              "run_id": self.run_id, "event": event, **values})
            with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        if self.emit:
            self.emit(row)
        return row
