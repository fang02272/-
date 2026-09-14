"""Regression tests for cache statistics, metrics and SSE integration."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class CacheTests(unittest.TestCase):
    def test_normalized_hit_and_statistics(self):
        from app.answer_cache import AnswerCache

        with tempfile.TemporaryDirectory() as directory:
            cache = AnswerCache(path=str(Path(directory) / "cache.json"))
            cache.put("什么是氩弧焊？", {"content": "x" * 40}, "fp")
            self.assertIsNotNone(cache.get(" 什么是氩弧焊 ", "fp"))
            self.assertIsNone(cache.get("完全不同的问题", "fp"))
            stats = cache.stats()
            self.assertEqual(stats["hits"], 1)
            self.assertEqual(stats["misses"], 1)
            self.assertEqual(stats["hit_rate"], 0.5)

    def test_expiry_and_lru_eviction_are_counted(self):
        from app.answer_cache import AnswerCache

        with tempfile.TemporaryDirectory() as directory:
            cache = AnswerCache(
                max_entries=1, ttl_seconds=1,
                path=str(Path(directory) / "cache.json"))
            cache.put("问题一", {"content": "a" * 40}, "fp")
            cache.put("问题二", {"content": "b" * 40}, "fp")
            self.assertEqual(cache.stats()["evictions"], 1)
            with cache._lock:
                cache._entries[cache.normalize("问题二")]["ts"] = time.time() - 2
            self.assertIsNone(cache.get("问题二", "fp"))
            self.assertEqual(cache.stats()["expirations"], 1)


class MetricsTests(unittest.TestCase):
    def test_llm_attempts_are_explicit(self):
        from app.metrics_service import PerformanceMetrics

        metrics = PerformanceMetrics()
        metrics.record_request(model_used="process_card", total_ms=5)
        metrics.record_request(
            model_used="local_knowledge_base", total_ms=20,
            llm_attempted=True, llm_succeeded=False)
        metrics.record_request(
            model_used="custom-model", total_ms=100,
            llm_attempted=True, llm_succeeded=True, ttft_ms=30)
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["total_requests"], 3)
        self.assertEqual(snapshot["local_answers"], 2)
        self.assertEqual(snapshot["llm_calls"], 2)
        self.assertEqual(snapshot["llm_answers"], 1)
        self.assertEqual(snapshot["llm_failures"], 1)


class StreamingTests(unittest.TestCase):
    def test_local_stream_finishes_with_structured_payload(self):
        import server
        from app.answer_cache import AnswerCache
        from app.metrics_service import get_metrics

        class OfflineLLM:
            available = False
            model = "offline"

        old_cache, old_llm = server._cache, server._llm_client
        try:
            with tempfile.TemporaryDirectory() as directory:
                server._cache = AnswerCache(path=str(Path(directory) / "cache.json"))
                server._llm_client = OfflineLLM()
                get_metrics().reset()
                events = list(server.stream_query_events("Q345钢板12mm MIG焊参数"))
            event_text = "".join(events)
            self.assertIn("event: token", event_text)
            done = next(block for block in events if block.startswith("event: done"))
            data_line = next(line for line in done.splitlines() if line.startswith("data: "))
            payload = json.loads(data_line[6:])["response"]
            self.assertTrue(payload.get("process_card") or payload.get("sections"))
            self.assertIn(payload["model_used"], {
                "process_card", "local_expert", "local_knowledge_base"})
            self.assertEqual(get_metrics().snapshot()["total_requests"], 1)
        finally:
            server._cache, server._llm_client = old_cache, old_llm

    def test_observability_routes_are_registered(self):
        import server

        paths = {route.path for route in server.app.routes}
        self.assertTrue({
            "/api/query/stream", "/api/metrics", "/api/metrics/reset",
            "/api/cache/stats", "/api/cache/invalidate",
        }.issubset(paths))


def run_tests() -> bool:
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    raise SystemExit(0 if run_tests() else 1)
