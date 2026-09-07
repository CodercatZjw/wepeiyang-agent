import tempfile
import time
import unittest
from pathlib import Path

from wepeiyang_agent.memory import MemoryStore


class Embedding:
    identity = "test-v1"
    def __call__(self, texts):
        return [[1.0, float("比赛" in t), float("食堂" in t)] for t in texts]


class MemoryTests(unittest.TestCase):
    def test_persistence_dedup_search_expiry_and_restore(self):
        with tempfile.TemporaryDirectory() as d:
            memory = MemoryStore(Path(d), Embedding())
            first = memory.write("比赛报名本周截止", "MP1", topic="比赛", ttl_days=1)
            self.assertTrue(first["indexed"])
            self.assertTrue(memory.write("比赛报名本周截止", "MP2")["duplicate"])
            key = first["memory"]["id"]
            self.assertEqual(memory.search("比赛")["memories"][0]["id"], key)
            memory.db.execute("UPDATE memories SET expires=? WHERE id=?", (time.time() - 1, key))
            memory.db.commit()
            self.assertEqual(memory.search("比赛")["memories"], [])
            result = memory.maintain()
            self.assertEqual(result["expired"], 1)
            self.assertTrue(Path(result["backup"]).exists())
            memory.restore(key)
            memory.close()
            memory = MemoryStore(Path(d), Embedding())
            self.assertEqual(memory.search("比赛")["memories"][0]["id"], key)
            memory.close()

    def test_repair_deleted_index_and_preserve_conflicts(self):
        with tempfile.TemporaryDirectory() as d:
            memory = MemoryStore(Path(d), Embedding())
            memory.write("比赛周三截止", "MP1", topic="比赛")
            memory.write("比赛周五截止", "MP2", topic="比赛")
            from qdrant_client.models import Filter, FilterSelector
            memory.vectors.delete(memory.collection, points_selector=FilterSelector(filter=Filter()))
            report = memory.maintain()
            self.assertEqual(report["indexed"], 2)
            self.assertEqual(len(report["conflicts_to_review"]["比赛"]), 2)
            self.assertEqual(memory.status()["counts"]["active"], 2)
            memory.close()

    def test_index_failure_keeps_text_and_reports_degraded_retrieval(self):
        def broken(texts):
            raise RuntimeError("embedding unavailable")
        with tempfile.TemporaryDirectory() as d:
            memory = MemoryStore(Path(d), broken)
            result = memory.write("食堂今天营业", "MP1")
            self.assertFalse(result["indexed"])
            self.assertTrue(result["verified"])
            result = memory.search("食堂")
            self.assertEqual(result["mode"], "keyword_only")
            self.assertEqual(len(result["memories"]), 1)
            memory.close()
