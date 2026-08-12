"""
性能基线测试脚本
================
用法:
  python benchmark.py              # 运行全部30题，输出基线报告
  python benchmark.py --local      # 仅测本地回答（不调LLM）
  python benchmark.py --save       # 结果保存到 benchmark_result.json

测试不依赖运行中的服务器，直接调用内部模块。
"""

import sys
import time
import json
import argparse
import statistics
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================
# 固定测试问题集（30题）
# ============================================================

TEST_QUESTIONS = [
    # 书本理论类（期望本地充分）
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
    # 材料类（期望本地充分）
    "不锈钢焊接热裂纹怎么防止",
    "Q345钢焊接预热温度",
    "铝合金焊接常见缺陷",
    "异种材料焊接为什么难",
    "扩散焊中间层材料选择",
    # 工艺参数类（跨域，期望LLM）
    "弧焊机器人焊接3mm钢板参数怎么选",
    "板厚10mm坡口设计",
    "TIG焊不锈钢保护气体流量",
    "埋弧焊焊接电流与焊速的关系",
    "CO2气保焊短路过渡参数范围",
    # 复杂综合类（期望LLM）
    "304不锈钢TIG焊热裂纹防止措施及焊材选择",
    "Q690高强钢焊接冷裂纹预防",
    "铸铁冷焊法焊条选择和操作要点",
    "双相不锈钢2205焊接工艺要点",
    "铝合金MIG脉冲焊参数选择",
    # 宽泛主题（深度分析模式）
    "焊接冶金",
    "焊接热影响区",
    "异种材料焊接",
    "焊接缺陷",
    "焊接工艺评定",
]

assert len(TEST_QUESTIONS) == 30, "测试题目应为30题"


def green(s): return f"\033[92m{s}\033[0m"
def red(s): return f"\033[91m{s}\033[0m"
def yellow(s): return f"\033[93m{s}\033[0m"
def cyan(s): return f"\033[96m{s}\033[0m"


def run_local_only(questions: list) -> list:
    """仅测本地分析耗时（不调LLM、不做RAG）"""
    from welding_qa_system import WeldingQASystem
    from knowledge_store import get_store

    qa = WeldingQASystem()
    store = get_store()
    sources = store.list_sources()
    ext = [{"filename": s["filename"], "keywords": store.get_keywords(s["id"]),
            "chapters": store.get_chapters(s["id"])} for s in sources]
    qa.load_external_knowledge(ext)

    results = []
    for q in questions:
        t0 = time.perf_counter()
        result = qa.generate_structured(q)
        elapsed = (time.perf_counter() - t0) * 1000
        results.append({
            "query": q,
            "stage": "local",
            "elapsed_ms": round(elapsed, 2),
            "keywords": result.get("keywords", []),
            "categories": result.get("matched_categories", []),
            "is_empty": result.get("is_empty", False),
            "content_len": len(result.get("sections", {}).get("science", {}).get("content", "")),
        })
    return results


def run_rag_only(questions: list) -> list:
    """仅测RAG检索耗时"""
    from rag_retriever import get_rag
    from knowledge_store import get_store

    rag = get_rag()
    store = get_store()
    rag.rebuild_from_store(store)

    results = []
    for q in questions:
        t0 = time.perf_counter()
        ctx = rag.build_context(q)
        elapsed = (time.perf_counter() - t0) * 1000
        results.append({
            "query": q,
            "stage": "rag",
            "elapsed_ms": round(elapsed, 2),
            "context_len": len(ctx),
        })
    return results


def run_full(questions: list) -> list:
    """完整流程：本地 + RAG + LLM（有LLM时调用）"""
    from welding_qa_system import WeldingQASystem
    from rag_retriever import get_rag
    from llm_service import get_client
    from knowledge_store import get_store

    qa = WeldingQASystem()
    store = get_store()
    rag = get_rag()
    rag.rebuild_from_store(store)
    llm = get_client()

    sources = store.list_sources()
    ext = [{"filename": s["filename"], "keywords": store.get_keywords(s["id"]),
            "chapters": store.get_chapters(s["id"])} for s in sources]
    qa.load_external_knowledge(ext)
    uploaded_names = [s["filename"] for s in sources]

    results = []
    for i, q in enumerate(questions, 1):
        print(f"  [{i:02d}/{len(questions)}] {q[:40]}...", end="", flush=True)
        t_total = time.perf_counter()

        t1 = time.perf_counter()
        result = qa.generate_structured(q)
        local_ms = (time.perf_counter() - t1) * 1000

        t2 = time.perf_counter()
        rag_ctx = rag.build_context(q)
        cross = store.search_across_sources(q)
        rag_ms = (time.perf_counter() - t2) * 1000

        llm_ms = 0.0
        model_used = "local_knowledge_base"
        if llm.available:
            t3 = time.perf_counter()
            try:
                llm_text = llm.chat_sync(q, context=rag_ctx, uploaded_files=uploaded_names,
                                         cross_source_matches=cross)
                llm_ms = (time.perf_counter() - t3) * 1000
                if llm_text and len(llm_text) > 50:
                    model_used = llm.model
            except Exception as e:
                llm_ms = (time.perf_counter() - t3) * 1000
                print(f" LLM ERROR: {e}")

        total_ms = (time.perf_counter() - t_total) * 1000
        print(f" {round(total_ms)}ms [{model_used[:10]}]")

        results.append({
            "query": q,
            "stage": "full",
            "total_ms": round(total_ms, 1),
            "local_ms": round(local_ms, 1),
            "rag_ms": round(rag_ms, 1),
            "llm_ms": round(llm_ms, 1),
            "model_used": model_used,
            "keywords": result.get("keywords", []),
            "is_empty": result.get("is_empty", False),
            "is_cross_domain": result.get("is_cross_domain", False),
        })
    return results


def print_report(results: list, title: str):
    print(f"\n{'=' * 60}")
    print(cyan(f"  {title}"))
    print(f"{'=' * 60}")

    totals = [r.get("total_ms", r.get("elapsed_ms", 0)) for r in results]
    avg = statistics.mean(totals)
    p50 = sorted(totals)[len(totals) // 2]
    p95 = sorted(totals)[int(len(totals) * 0.95)]

    print(f"  样本数:  {len(results)}")
    print(f"  平均耗时: {yellow(f'{avg:.1f}ms')}")
    print(f"  P50:     {yellow(f'{p50:.1f}ms')}")
    print(f"  P95:     {yellow(f'{p95:.1f}ms')}")

    # 分阶段（仅 full 模式有）
    if "local_ms" in results[0]:
        local_avg = statistics.mean(r["local_ms"] for r in results)
        rag_avg = statistics.mean(r["rag_ms"] for r in results)
        llm_avg = statistics.mean(r["llm_ms"] for r in results if r["llm_ms"] > 0) if any(r["llm_ms"] > 0 for r in results) else 0
        llm_count = sum(1 for r in results if r["model_used"] not in ("local_knowledge_base",))
        print(f"\n  阶段平均耗时:")
        print(f"    本地分析: {local_avg:.1f}ms")
        print(f"    RAG检索:  {rag_avg:.1f}ms")
        print(f"    LLM调用:  {llm_avg:.1f}ms")
        print(f"\n  LLM调用: {llm_count}/{len(results)} ({llm_count/len(results)*100:.0f}%)")
        empty = sum(1 for r in results if r["is_empty"])
        cross = sum(1 for r in results if r.get("is_cross_domain"))
        print(f"  空匹配:  {empty}/{len(results)}")
        print(f"  跨域问题: {cross}/{len(results)}")

    print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(description="性能基线测试")
    parser.add_argument("--local", action="store_true", help="仅测本地分析")
    parser.add_argument("--rag", action="store_true", help="仅测RAG检索")
    parser.add_argument("--save", action="store_true", help="保存结果到 benchmark_result.json")
    args = parser.parse_args()

    print(cyan("\n🔬 焊接工艺专家系统 — 性能基线测试"))
    print(f"   测试题数: {len(TEST_QUESTIONS)}")

    all_results = {}

    if args.local:
        print("\n⏱  [1/1] 本地分析...")
        r = run_local_only(TEST_QUESTIONS)
        print_report(r, "本地分析阶段")
        all_results["local"] = r
    elif args.rag:
        print("\n⏱  [1/1] RAG检索...")
        r = run_rag_only(TEST_QUESTIONS)
        print_report(r, "RAG检索阶段")
        all_results["rag"] = r
    else:
        print("\n⏱  [1/3] 本地分析...")
        local_r = run_local_only(TEST_QUESTIONS)
        print_report(local_r, "① 本地分析阶段")
        all_results["local"] = local_r

        print("⏱  [2/3] RAG检索...")
        rag_r = run_rag_only(TEST_QUESTIONS)
        print_report(rag_r, "② RAG检索阶段")
        all_results["rag"] = rag_r

        print("⏱  [3/3] 完整流程（含LLM）...")
        full_r = run_full(TEST_QUESTIONS)
        print_report(full_r, "③ 完整流程基线")
        all_results["full"] = full_r

    if args.save:
        out = PROJECT_ROOT / "benchmark_result.json"
        out.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ 结果已保存: {out}")


if __name__ == "__main__":
    main()
