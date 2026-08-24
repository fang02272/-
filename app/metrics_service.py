"""In-process performance metrics for the query pipeline.

The counters intentionally live in memory: they are cheap enough for the request
path and reset when the service restarts.  Timings are kept in bounded deques so a
long-running service cannot grow memory without limit.
"""

from __future__ import annotations

import statistics
import threading
from collections import deque
from typing import Deque, Optional


class PerformanceMetrics:
    """Thread-safe request, routing and latency metrics."""

    _LOCAL_MODELS = {"local_expert", "local_knowledge_base", "process_card"}

    def __init__(self, sample_limit: int = 10_000):
        self.sample_limit = sample_limit
        self._lock = threading.RLock()
        self._reset_unlocked()

    def _samples(self) -> Deque[float]:
        return deque(maxlen=self.sample_limit)

    def _reset_unlocked(self) -> None:
        self._total_requests = 0
        self._llm_calls = 0
        self._llm_answers = 0
        self._llm_failures = 0
        self._local_answers = 0
        self._cache_hits = 0
        self._errors = 0
        self._total_elapsed = self._samples()
        self._local_elapsed = self._samples()
        self._rag_elapsed = self._samples()
        self._llm_elapsed = self._samples()
        self._assembly_elapsed = self._samples()
        self._ttft = self._samples()

    def record_request(
        self,
        *,
        model_used: str,
        total_ms: float,
        local_ms: float = 0.0,
        rag_ms: float = 0.0,
        llm_ms: float = 0.0,
        assembly_ms: float = 0.0,
        error: bool = False,
        llm_attempted: bool = False,
        llm_succeeded: bool = False,
        ttft_ms: Optional[float] = None,
    ) -> None:
        """Record one completed request.

        LLM usage is explicit rather than inferred from a model name.  This keeps
        custom model names and LLM-to-local fallbacks from corrupting call rates.
        """
        with self._lock:
            self._total_requests += 1
            self._errors += int(error)
            self._llm_calls += int(llm_attempted)
            self._llm_answers += int(llm_succeeded)
            self._llm_failures += int(llm_attempted and not llm_succeeded)

            if model_used == "cache":
                self._cache_hits += 1
            elif model_used in self._LOCAL_MODELS:
                self._local_answers += 1

            self._total_elapsed.append(max(0.0, float(total_ms)))
            for value, target in (
                (local_ms, self._local_elapsed),
                (rag_ms, self._rag_elapsed),
                (llm_ms, self._llm_elapsed),
                (assembly_ms, self._assembly_elapsed),
            ):
                if value > 0:
                    target.append(float(value))
            if ttft_ms is not None:
                self._ttft.append(max(0.0, float(ttft_ms)))

    @staticmethod
    def _percentile(data: Deque[float], percentile: int) -> float:
        if not data:
            return 0.0
        values = sorted(data)
        index = round((len(values) - 1) * percentile / 100)
        return round(values[index], 1)

    @staticmethod
    def _average(data: Deque[float]) -> float:
        return round(statistics.mean(data), 1) if data else 0.0

    def snapshot(self) -> dict:
        with self._lock:
            total = self._total_requests
            return {
                "total_requests": total,
                "llm_calls": self._llm_calls,
                "llm_answers": self._llm_answers,
                "llm_failures": self._llm_failures,
                "llm_call_rate": round(self._llm_calls / total, 4) if total else 0.0,
                "local_answers": self._local_answers,
                "cache_hits": self._cache_hits,
                "cache_hit_rate": round(self._cache_hits / total, 4) if total else 0.0,
                "errors": self._errors,
                "response_time_ms": {
                    "avg": self._average(self._total_elapsed),
                    "p50": self._percentile(self._total_elapsed, 50),
                    "p95": self._percentile(self._total_elapsed, 95),
                },
                "stage_ms": {
                    "local_avg": self._average(self._local_elapsed),
                    "rag_avg": self._average(self._rag_elapsed),
                    "llm_avg": self._average(self._llm_elapsed),
                    "assembly_avg": self._average(self._assembly_elapsed),
                },
                "ttft_ms": {
                    "avg": self._average(self._ttft),
                    "p50": self._percentile(self._ttft, 50),
                    "p95": self._percentile(self._ttft, 95),
                    "count": len(self._ttft),
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._reset_unlocked()


_metrics: Optional[PerformanceMetrics] = None
_metrics_lock = threading.Lock()


def get_metrics() -> PerformanceMetrics:
    global _metrics
    if _metrics is None:
        with _metrics_lock:
            if _metrics is None:
                _metrics = PerformanceMetrics()
    return _metrics
