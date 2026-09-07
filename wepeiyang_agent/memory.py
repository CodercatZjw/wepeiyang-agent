"""SQLite is authoritative; Qdrant is a repairable semantic index."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
import sys
from contextlib import closing
from contextlib import redirect_stdout
from pathlib import Path

from .config import MemoryConfig


class Embeddings:
    def __init__(self, config: MemoryConfig, llm):
        self.config, self.llm, self.model = config, llm, None
        self.identity = config.embedding_provider + ":" + config.embedding_model + ":" + config.embedding_url

    def __call__(self, texts: list[str]) -> list[list[float]]:
        if self.config.embedding_provider == "remote":
            result = self.llm.request_payload(
                {"model": self.config.embedding_model, "input": texts}, "memory.embedding",
                url=self.config.embedding_url,
                api_key=self.config.embedding_api_key or self.llm.config.api_key,
            )
            data = sorted(result["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in data]
        else:
            if self.model is None:
                self.llm.trace.record("memory.embedding_load", model=self.config.embedding_model)
                from sentence_transformers import SentenceTransformer
                with redirect_stdout(sys.stderr):
                    self.model = SentenceTransformer(self.config.embedding_model)
            with redirect_stdout(sys.stderr):
                vectors = self.model.encode(texts, normalize_embeddings=True).tolist()
        if len(vectors) != len(texts) or not vectors or not vectors[0]:
            raise ValueError("嵌入服务返回的向量数量或维度无效")
        if any(len(v) != len(vectors[0]) or not all(math.isfinite(x) for x in v) for v in vectors):
            raise ValueError("嵌入服务返回不一致或非有限向量")
        return vectors


class MemoryStore:
    def __init__(self, directory: Path, embedder, trace=None):
        self.directory, self.embedder, self.trace = directory, embedder, trace
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "memory.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, content TEXT NOT NULL, digest TEXT NOT NULL,
                source TEXT NOT NULL, topic TEXT NOT NULL, created REAL NOT NULL,
                expires REAL, status TEXT NOT NULL DEFAULT 'active',
                supersedes TEXT, indexed INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS memory_digest ON memories(digest);
            CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, time REAL, action TEXT, details TEXT);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        """)
        self._vectors = None

    @property
    def vectors(self):
        if self._vectors is None:
            from qdrant_client import QdrantClient
            self._vectors = QdrantClient(path=str(self.directory / "qdrant"))
        return self._vectors

    @property
    def collection(self):
        name = getattr(self.embedder, "identity", "test")
        return "memory_" + hashlib.sha256(name.encode()).hexdigest()[:16]

    def audit(self, action, **details):
        self.db.execute("INSERT INTO audit(time,action,details) VALUES (?,?,?)",
                        (time.time(), action, json.dumps(details, ensure_ascii=False)))
        self.db.commit()
        if self.trace:
            self.trace.record("memory." + action, **details)

    def _active(self):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM memories WHERE status='active' AND (expires IS NULL OR expires>?)", (time.time(),))]

    def get(self, memory_id):
        row = self.db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise ValueError("记忆不存在：" + memory_id)
        return dict(row)

    def write(self, content: str, source: str, topic: str = "", ttl_days: int | None = None,
              supersedes: str | None = None):
        content = content.strip()
        if not content or len(content) > 20000 or not source.strip():
            raise ValueError("记忆需要 1–20000 字正文和来源")
        if ttl_days is not None and (type(ttl_days) is not int or ttl_days < 1):
            raise ValueError("ttl_days 必须为正整数")
        digest = hashlib.sha256(re.sub(r"\s+", "", content).encode()).hexdigest()
        duplicate = next((r for r in self._active() if r["digest"] == digest), None)
        if duplicate:
            self.audit("duplicate", memory_id=duplicate["id"], additional_source=source)
            return {"memory": duplicate, "duplicate": True}
        if supersedes:
            self.get(supersedes)
        memory_id = str(uuid.uuid4())
        with self.db:
            self.db.execute("INSERT INTO memories(id,content,digest,source,topic,created,expires,supersedes) VALUES (?,?,?,?,?,?,?,?)",
                            (memory_id, content, digest, source, topic, time.time(),
                             time.time() + ttl_days * 86400 if ttl_days else None, supersedes))
            if supersedes:
                self.db.execute("UPDATE memories SET status='superseded' WHERE id=?", (supersedes,))
        self.audit("write", memory_id=memory_id, supersedes=supersedes)
        self._export(memory_id)
        result = {"memory": self.get(memory_id), "duplicate": False, "verified": True}
        try:
            self.sync()
            result["indexed"] = True
            result["memory"] = self.get(memory_id)
        except Exception as exc:
            result.update(indexed=False, index_error=str(exc))
            self.audit("index_error", error=str(exc))
        return result

    def _export(self, memory_id):
        folder = self.directory / "archive"
        folder.mkdir(exist_ok=True)
        row = self.get(memory_id)
        (folder / (memory_id + ".json")).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")

    def sync(self, rebuild=False):
        rows = self._active()
        identity = self.collection
        previous = self.db.execute("SELECT value FROM metadata WHERE key='collection'").fetchone()
        changed = previous is None or previous[0] != identity
        if not rows:
            return 0
        from qdrant_client.models import VectorParams, Distance, PointStruct
        pending = rows if rebuild or changed else [r for r in rows if not r["indexed"]]
        if not self.vectors.collection_exists(identity):
            pending = rows
        elif not rebuild and not changed:
            pending_ids = {r["id"] for r in pending}
            for row in rows:
                if row["id"] in pending_ids:
                    continue
                expected = [str(uuid.uuid5(uuid.NAMESPACE_URL, row["id"] + ":" + str(i)))
                            for i, _ in enumerate(range(0, len(row["content"]), 1000))]
                present = self.vectors.retrieve(identity, ids=expected, with_payload=False, with_vectors=False)
                if len(present) != len(expected):
                    pending.append(row)
        count = 0
        for row in pending:
            chunks = [row["content"][i:i + 1200] for i in range(0, len(row["content"]), 1000)]
            vectors = self.embedder(chunks)
            if not self.vectors.collection_exists(identity):
                self.vectors.create_collection(identity, vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE))
            self.vectors.upsert(identity, points=[PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, row["id"] + ":" + str(i))), vector=v,
                payload={"memory_id": row["id"], "chunk": chunks[i]},
            ) for i, v in enumerate(vectors)])
            self.db.execute("UPDATE memories SET indexed=1 WHERE id=?", (row["id"],))
            count += 1
        self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('collection',?)", (identity,))
        self.db.commit()
        return count

    def search(self, query: str, limit: int = 8):
        if not query.strip() or not 1 <= limit <= 50:
            raise ValueError("查询不能为空，limit 需要在 1–50 之间")
        active = {r["id"]: r for r in self._active()}
        if not active:
            return {"memories": [], "mode": "empty"}
        scores, warnings = {}, []
        try:
            self.sync()
            from qdrant_client.models import Filter, FieldCondition, MatchAny
            points = self.vectors.query_points(self.collection, query=self.embedder([query])[0],
                query_filter=Filter(must=[FieldCondition(key="memory_id", match=MatchAny(any=list(active)))]),
                limit=max(limit * 4, 30)).points
            for point in points:
                memory_id = point.payload["memory_id"]
                if memory_id in active:
                    scores[memory_id] = max(scores.get(memory_id, 0), float(point.score))
        except Exception as exc:
            warnings.append(str(exc))
            self.audit("retrieval_degraded", error=str(exc))
        terms = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{1,2}", query.lower()))
        for memory_id, row in active.items():
            text = (row["content"] + " " + row["topic"]).lower()
            lexical = sum(term in text for term in terms) / max(len(terms), 1)
            if lexical:
                scores[memory_id] = scores.get(memory_id, 0) + lexical * .5
        ranked = sorted(scores, key=lambda k: scores[k], reverse=True)[:limit]
        return {"memories": [{**active[k], "score": scores[k]} for k in ranked],
                "mode": "keyword_only" if warnings else "hybrid", "warnings": warnings}

    def maintain(self, rebuild=False):
        backup = self.directory / ("backup-" + str(time.time_ns()) + ".sqlite3")
        with closing(sqlite3.connect(backup)) as destination:
            self.db.backup(destination)
        expired = self.db.execute("UPDATE memories SET status='expired' WHERE status='active' AND expires<=?", (time.time(),)).rowcount
        self.db.commit()
        indexed = self.sync(rebuild=rebuild)
        active = self._active()
        topics = {}
        for row in active:
            if row["topic"]:
                topics.setdefault(row["topic"], []).append(row["id"])
        # Different assertions on the same topic require review, not silent overwriting.
        conflicts = {k: ids for k, ids in topics.items() if len(ids) > 1}
        if self._vectors is not None and self.vectors.collection_exists(self.collection):
            from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector
            for row in self.db.execute("SELECT id FROM memories WHERE status!='active'"):
                self.vectors.delete(self.collection, points_selector=FilterSelector(filter=Filter(must=[
                    FieldCondition(key="memory_id", match=MatchValue(value=row[0]))])))
        for row in self.db.execute("SELECT id FROM memories"):
            self._export(row[0])
        self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('last_maintenance',?)", (str(time.time()),))
        self.db.commit()
        result = {"expired": expired, "indexed": indexed, "conflicts_to_review": conflicts,
                  "backup": str(backup), "sqlite_integrity": self.db.execute("PRAGMA integrity_check").fetchone()[0]}
        self.audit("maintenance", **result)
        return result

    def status(self):
        last = self.db.execute("SELECT value FROM metadata WHERE key='last_maintenance'").fetchone()
        return {"counts": dict(self.db.execute("SELECT status,count(*) FROM memories GROUP BY status")),
                "pending_index": self.db.execute("SELECT count(*) FROM memories WHERE indexed=0 AND status='active'").fetchone()[0],
                "last_maintenance": float(last[0]) if last else 0,
                "embedding": getattr(self.embedder, "identity", "test")}

    def archive(self, memory_id):
        before = self.get(memory_id)
        self.db.execute("UPDATE memories SET status='archived' WHERE id=?", (memory_id,))
        self.audit("archive", before=before)
        self._export(memory_id)
        return self.get(memory_id)

    def restore(self, memory_id):
        before = self.get(memory_id)
        self.db.execute("UPDATE memories SET status='active',expires=NULL,indexed=0 WHERE id=?", (memory_id,))
        self.audit("restore", before=before)
        self._export(memory_id)
        return self.get(memory_id)

    def close(self):
        if self._vectors is not None:
            self._vectors.close()
        self.db.close()
