"""Offline performance benchmark for the refactor_V2 query pipeline.

The benchmark uses a temporary answer cache and disables the LLM client, so it
does not alter the project's persisted cache or consume API tokens.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

QUESTIONS = [
    "焊接热影响区的组织分布是什么",
    "热裂纹形成机理",
    "冷裂纹与氢致裂纹的关系",
    "焊接接头强韧性影响因素",
    "熔池结晶过程",
    "焊缝中气孔的形成原因",
    "固态相变对焊缝的影响",
    "层状撕裂产生条件",
    "焊接热循环主要参数",
    "活性钎焊的基本原理",
    "不锈钢焊接热裂纹怎么防止",
    "Q345钢焊接预热温度",
    "铝合金焊接常见缺陷",
    "异种材料焊接为什么难",
    "扩散焊中间层材料选择",
    "弧焊机器人焊接3mm钢板参数怎么选",
    "板厚10mm坡口设计",
    "TIG焊不锈钢保护气体流量",
    "埋弧焊焊接电流与焊速的关系",
    "CO2气保焊短路过渡参数范围",
    "304不锈钢TIG焊热裂纹防止措施及焊材选择",
    "Q690高强钢焊接冷裂纹预防",
    "铸铁冷焊法焊条选择和操作要点",
    "双相不锈钢2205焊接工艺要点",
    "铝合金MIG脉冲焊参数选择",
    "焊接冶金",
    "焊接热影响区",
    "异种材料焊接",
    "焊接缺陷",
    "焊接工艺评定",
]


class OfflineLLM:
    available = False
    model = "offline"


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[round((len(ordered) - 1) * p / 100)], 2)


def _summary(rows: list[dict]) -> dict:
    elapsed = [row["elapsed_ms"] for row in rows]
    return {
        "count": len(rows),
        "avg_ms": round(statistics.mean(elapsed), 2) if elapsed else 0.0,
        "p50_ms": _percentile(elapsed, 50),
        "p95_ms": _percentile(elapsed, 95),
        "cache_hits": sum(bool(row["cache_hit"]) for row in rows),
        "models": dict(sorted({
            model: sum(row["model_used"] == model for row in rows)
            for model in {row["model_used"] for row in rows}
        }.items())),
    }


def _run_pass(server, questions: list[str]) -> list[dict]:
    rows = []
    for index, question in enumerate(questions, 1):
        started = time.perf_counter()
        payload = server.process_query(question)
        elapsed_ms = (time.perf_counter() - started) * 1000
        rows.append({
            "query": question,
            "elapsed_ms": round(elapsed_ms, 2),
            "model_used": payload.get("model_used", "unknown"),
            "cache_hit": bool(payload.get("cache_hit")),
            "intent": (payload.get("route") or {}).get("intent"),
            "confidence": payload.get("confidence", 0.0),
        })
        print(
            f"  [{index:02d}/{len(questions)}] {elapsed_ms:8.2f} ms  "
            f"{rows[-1]['model_used']:<22} {question}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="焊接专家系统离线性能基准")
    parser.add_argument("--questions", type=int, default=len(QUESTIONS),
                        help="运行前 N 道题（默认 30）")
    parser.add_argument("--save", nargs="?", const="benchmark_result.json",
                        help="保存 JSON，可选指定输出路径")
    args = parser.parse_args()
    questions = QUESTIONS[:max(1, min(args.questions, len(QUESTIONS)))]

    import server
    from app.answer_cache import AnswerCache
    from app.metrics_service import get_metrics

    with tempfile.TemporaryDirectory(prefix="welding-benchmark-") as temp_dir:
        server._cache = AnswerCache(
            max_entries=max(50, len(questions) * 2), ttl_seconds=3600,
            path=str(Path(temp_dir) / "answer_cache.json"))
        server._llm_client = OfflineLLM()
        get_metrics().reset()

        started = time.perf_counter()
        server._ensure_index()
        index_init_ms = (time.perf_counter() - started) * 1000

        print(f"\n索引加载: {index_init_ms:.2f} ms")
        print("\n第一轮（本地检索/路由/组装）")
        first = _run_pass(server, questions)
        print("\n第二轮（相同查询，验证缓存）")
        cached = _run_pass(server, questions)

        report = {
            "mode": "offline-no-llm",
            "question_count": len(questions),
            "index_init_ms": round(index_init_ms, 2),
            "first_pass": {"summary": _summary(first), "rows": first},
            "cached_pass": {"summary": _summary(cached), "rows": cached},
            "metrics": get_metrics().snapshot(),
            "cache": server._get_cache().stats(),
        }

    print("\n结果摘要")
    print(json.dumps({
        "index_init_ms": report["index_init_ms"],
        "first_pass": report["first_pass"]["summary"],
        "cached_pass": report["cached_pass"]["summary"],
    }, ensure_ascii=False, indent=2))

    if args.save:
        output = Path(args.save)
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已保存：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
