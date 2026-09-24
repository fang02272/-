"""运行固定 50 题，输出检索、缺失输入、引用和缓存性能报告。"""

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


class OfflineLLM:
    available = False
    model = "offline"


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return round(values[round((len(values) - 1) * p / 100)], 2)


def _latency_summary(rows: list[dict]) -> dict:
    values = [row["elapsed_ms"] for row in rows]
    return {
        "count": len(values),
        "avg_ms": round(statistics.mean(values), 2) if values else 0.0,
        "p50_ms": _percentile(values, 50),
        "p95_ms": _percentile(values, 95),
        "cache_hits": sum(bool(row.get("cache_hit")) for row in rows),
        "llm_calls": sum(bool(row.get("llm_attempted")) for row in rows),
    }


def _evidence_variants(term: str) -> set[str]:
    """扩展繁简、缩写和焊接领域别名，减少自动评估的同义词误报。"""
    variants = {str(term).lower()}
    try:
        from app.welding_qa_system import WeldingQASystem
        normalized = WeldingQASystem._normalize_text(str(term)).lower()
        variants.add(normalized)
    except Exception:
        pass
    try:
        from app.welding_knowledge_base import TERM_ALIAS_MAP
        raw = str(term).lower()
        for canonical, aliases in TERM_ALIAS_MAP.items():
            names = [str(canonical).lower(), *(str(alias).lower() for alias in aliases)]
            if raw in names or any(name in raw or raw in name for name in names):
                variants.update(names)
    except Exception:
        pass
    return {item for item in variants if item}


def _run_pass(server, store, questions: list[dict]) -> list[dict]:
    rows = []
    for item in questions:
        query = item["query"]
        started = time.perf_counter()
        payload = server.process_query(query)
        elapsed = (time.perf_counter() - started) * 1000
        cross = store.search_across_sources(query)
        top5 = cross[:5]
        evidence_blob = "\n".join(
            " ".join([
                str(hit.get("chapter", "")),
                " ".join(hit.get("matched_keywords", [])),
                str(hit.get("summary", "")),
                str(hit.get("content_preview", "")),
            ]) for hit in top5
        ).lower()
        evidence_terms = [str(term).lower() for term in item.get("evidence_terms", [])]
        matched_terms = [term for term in evidence_terms if any(variant in evidence_blob for variant in _evidence_variants(term))]
        retrieval_hit = bool(matched_terms) if item.get("evidence_required", True) else None

        card = payload.get("process_card") or {}
        completeness = card.get("input_completeness") or {}
        missing_fields = {field.get("field") for field in completeness.get("missing_fields", [])}
        expected_missing = set(item.get("missing_fields", []))
        missing_ok = (not expected_missing) or expected_missing.issubset(missing_fields)
        references = payload.get("references", []) or []
        citation_supported = None if not item.get("evidence_required", True) else bool(references) and bool(matched_terms)
        rows.append({
            "id": item["id"],
            "category": item["category"],
            "query": query,
            "elapsed_ms": round(elapsed, 2),
            "model_used": payload.get("model_used", "unknown"),
            "cache_hit": bool(payload.get("cache_hit")),
            "llm_attempted": payload.get("model_used") not in {"cache", "process_card", "local_expert", "local_knowledge_base"},
            "top5": [{
                "book": hit.get("source"),
                "chapter": hit.get("chapter"),
                "score": hit.get("score"),
                "quality_score": hit.get("quality_score"),
                "quality_issues": hit.get("quality_issues", []),
            } for hit in top5],
            "matched_evidence_terms": matched_terms,
            "retrieval_hit": retrieval_hit,
            "references": references[:5],
            "citation_supported": citation_supported,
            "expected_missing_fields": sorted(expected_missing),
            "actual_missing_fields": sorted(missing_fields),
            "missing_input_ok": missing_ok,
            "input_completeness": completeness,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="运行固定50题本地验收与性能对比")
    parser.add_argument("--questions", type=int, default=50, help="运行前 N 题，默认50")
    parser.add_argument("--output", default="reports/fixed_questions_evaluation.json")
    args = parser.parse_args()

    questions_path = PROJECT_ROOT / "tools" / "fixed_questions.json"
    questions = json.loads(questions_path.read_text(encoding="utf-8"))[:max(1, min(args.questions, 50))]

    import server
    from app.answer_cache import AnswerCache
    from app.metrics_service import get_metrics

    old_cache, old_llm = server._cache, server._llm_client
    old_fp = server._kb_fingerprint
    try:
        with tempfile.TemporaryDirectory(prefix="welding-fixed-eval-") as temp_dir:
            server._cache = AnswerCache(
                max_entries=max(100, len(questions) * 3), ttl_seconds=3600,
                path=str(Path(temp_dir) / "answer_cache.json"),
            )
            server._llm_client = OfflineLLM()
            get_metrics().reset()
            server._ensure_index()
            store = server.get_store()

            first = _run_pass(server, store, questions)
            cached = _run_pass(server, store, questions)
            # 模拟知识源更新后的安全边界：清空答案缓存，再跑同一题集。
            server._get_cache().invalidate()
            after_invalidation = _run_pass(server, store, questions)

            evidence_rows = [row for row in first if row["retrieval_hit"] is not None]
            retrieval_hits = sum(bool(row["retrieval_hit"]) for row in evidence_rows)
            citation_rows = [row for row in first if row["citation_supported"] is not None]
            citation_hits = sum(bool(row["citation_supported"]) for row in citation_rows)
            missing_rows = [row for row in first if row["expected_missing_fields"]]
            missing_ok = sum(bool(row["missing_input_ok"]) for row in missing_rows)
            report = {
                "schema_version": 1,
                "mode": "offline-no-llm",
                "question_count": len(questions),
                "source": str(questions_path.relative_to(PROJECT_ROOT)),
                "retrieval": {
                    "top5_hit_count": retrieval_hits,
                    "top5_evidence_question_count": len(evidence_rows),
                    "top5_hit_rate": round(retrieval_hits / len(evidence_rows), 4) if evidence_rows else 0.0,
                    "missed_question_ids": [row["id"] for row in evidence_rows if not row["retrieval_hit"]],
                    "definition": "Top-5章节的标题、关键词、摘要或可读预览命中至少一个人工标注证据词。",
                },
                "citations": {
                    "supported_count": citation_hits,
                    "evidence_question_count": len(citation_rows),
                    "supported_rate": round(citation_hits / len(citation_rows), 4) if citation_rows else 0.0,
                    "unsupported_question_ids": [row["id"] for row in citation_rows if not row["citation_supported"]],
                    "definition": "响应存在来源且Top-5检索结果命中人工标注证据词；需人工抽查原文与页码。",
                },
                "missing_input": {
                    "passed": missing_ok,
                    "total": len(missing_rows),
                    "coverage": round(missing_ok / len(missing_rows), 4) if missing_rows else 0.0,
                    "failed_question_ids": [row["id"] for row in missing_rows if not row["missing_input_ok"]],
                },
                "performance": {
                    "first_pass": _latency_summary(first),
                    "cached_pass": _latency_summary(cached),
                    "after_cache_invalidation": _latency_summary(after_invalidation),
                    "index_stats": server._get_vector().stats(),
                    "metrics": get_metrics().snapshot(),
                },
                "rows": first,
                "cached_rows": cached,
                "after_invalidation_rows": after_invalidation,
                "limitations": [
                    "固定题集的证据词评估是自动初筛，引用正确性仍需人工对照原文和页码。",
                    "本报告为无LLM本地模式；LLM模式需配置有效接口后单独运行。",
                    "资料更新场景使用缓存失效模拟，不改写正式 saved_knowledge。",
                ],
            }
    finally:
        server._cache, server._llm_client, server._kb_fingerprint = old_cache, old_llm, old_fp

    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "question_count": report["question_count"],
        "top5_hit_rate": report["retrieval"]["top5_hit_rate"],
        "citation_supported_rate": report["citations"]["supported_rate"],
        "missing_input_coverage": report["missing_input"]["coverage"],
        "first_pass": report["performance"]["first_pass"],
        "cached_pass": report["performance"]["cached_pass"],
    }, ensure_ascii=False, indent=2))
    print(f"报告已保存：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
