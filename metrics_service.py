"""
性能监控服务 — 统计请求耗时、LLM调用率、缓存命中率、TTFT
=============================================================
所有数据存在内存里，重启清零，可通过 /api/metrics 查看，
也可通过 /api/metrics/reset 重置测试数据。
"""

import time
import statistics
import logging
from typing import List, Optional

logger = logging.getLogger("welding_qa.metrics")


class PerformanceMetrics:
    """线程安全简易版（单进程FastAPI不需要锁）"""

    def __init__(self):
        self._total_requests: int = 0
        self._llm_calls: int = 0
        self._local_answers: int = 0
        self._cache_hits_in_requests: int = 0
        self._errors: int = 0

        # 各阶段耗时列表（毫秒），用于算 P50/P95
        self._total_elapsed: List[float] = []
        self._local_elapsed: List[float] = []
        self._rag_elapsed: List[float] = []
        self._llm_elapsed: List[float] = []
        self._assembly_elapsed: List[float] = []

        # TTFT（首字响应时间，仅流式接口填充）
        self._ttft: List[float] = []

    # ----------------------------------------------------------
    # 记录接口
    # ----------------------------------------------------------

    def record_request(
        self,
        *,
        model_used: str,       # "cache" | "local_knowledge_base" | 任意LLM模型名如"deepseek-chat"
        total_ms: float,
        local_ms: float = 0.0,
        rag_ms: float = 0.0,
        llm_ms: float = 0.0,
        assembly_ms: float = 0.0,
        error: bool = False,
        llm_attempted: bool = False,   # LLM 尝试调用但失败时置 True
        ttft_ms: Optional[float] = None,
    ):
        self._total_requests += 1
        if error:
            self._errors += 1
        if model_used == "cache":
            self._cache_hits_in_requests += 1
        elif model_used == "local_knowledge_base":
            self._local_answers += 1
        else:
            # 任何非 cache / local 的值都视为 LLM 成功调用（如 "deepseek-chat"）
            self._llm_calls += 1
        # LLM 尝试但失败（降级为本地）也计入 llm_calls
        if llm_attempted and model_used == "local_knowledge_base":
            self._llm_calls += 1

        self._total_elapsed.append(total_ms)
        if local_ms:
            self._local_elapsed.append(local_ms)
        if rag_ms:
            self._rag_elapsed.append(rag_ms)
        if llm_ms:
            self._llm_elapsed.append(llm_ms)
        if assembly_ms:
            self._assembly_elapsed.append(assembly_ms)
        if ttft_ms is not None:
            self._ttft.append(ttft_ms)

    # ----------------------------------------------------------
    # 计算接口
    # ----------------------------------------------------------

    @staticmethod
    def _percentile(data: List[float], p: int) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = max(0, int(len(sorted_data) * p / 100) - 1)
        return round(sorted_data[idx], 1)

    @staticmethod
    def _avg(data: List[float]) -> float:
        return round(statistics.mean(data), 1) if data else 0.0

    def snapshot(self) -> dict:
        total = self._total_requests
        llm_rate = round(self._llm_calls / total, 4) if total > 0 else 0.0
        cache_rate = round(self._cache_hits_in_requests / total, 4) if total > 0 else 0.0

        return {
            "total_requests": total,
            "llm_calls": self._llm_calls,
            "llm_call_rate": llm_rate,
            "local_answers": self._local_answers,
            "cache_hits": self._cache_hits_in_requests,
            "cache_hit_rate": cache_rate,
            "errors": self._errors,
            "response_time_ms": {
                "avg": self._avg(self._total_elapsed),
                "p50": self._percentile(self._total_elapsed, 50),
                "p95": self._percentile(self._total_elapsed, 95),
            },
            "stage_ms": {
                "local_avg": self._avg(self._local_elapsed),
                "rag_avg": self._avg(self._rag_elapsed),
                "llm_avg": self._avg(self._llm_elapsed),
                "assembly_avg": self._avg(self._assembly_elapsed),
            },
            "ttft_ms": {
                "avg": self._avg(self._ttft),
                "p50": self._percentile(self._ttft, 50),
                "p95": self._percentile(self._ttft, 95),
                "count": len(self._ttft),
            },
        }

    def reset(self):
        """重置所有统计，保留对象本身（不影响缓存）"""
        self.__init__()
        logger.info("Metrics reset")


# ============================================================
# 单例
# ============================================================

_metrics_instance: Optional[PerformanceMetrics] = None


def get_metrics() -> PerformanceMetrics:
    global _metrics_instance
    if _metrics_instance is None:
        _metrics_instance = PerformanceMetrics()
    return _metrics_instance
