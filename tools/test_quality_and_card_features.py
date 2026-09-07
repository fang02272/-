"""检索质量优化与工艺卡可靠性专项回归测试。"""

import json
import tempfile
import time
from pathlib import Path


def _check(results: list, description: str, condition: bool) -> None:
    results.append((description, bool(condition)))


def run_tests() -> bool:
    from app.process_card import build_process_card
    from app.answer_cache import AnswerCache
    from app.qa_router import QARouter
    from app.retrieval_quality import (
        assess_content_quality,
        best_content_preview,
        deduplicate_results,
    )
    from app.vector_store import VectorIndex, index_book_chapters

    checks = []

    good = assess_content_quality(
        "第6章 材料表面改性焊接",
        "Q345钢板采用GMAW焊接时，应按板厚控制焊接电流、电弧电压和层间温度。" * 8,
    )
    bad = assess_content_quality("12", "���\x00" * 80)
    _check(checks, "正常技术章节保留且质量分较高", not good["filtered"] and good["score"] >= 0.8)
    _check(checks, "严重乱码章节被过滤", bad["filtered"] and "疑似乱码" in bad["issues"])

    preview = best_content_preview(
        "材料焊接原理 第12页\n目录与页眉。\n"
        "Q345钢板的焊接电流应结合板厚、焊丝直径和焊接位置调整，以避免未熔合与咬边。",
        "Q345 焊接电流",
    )
    _check(checks, "摘要优先选择包含查询词的可读段落", "Q345" in preview and "焊接电流" in preview)

    deduped = deduplicate_results([
        {"source": "手册.pdf", "chapter": "第1章", "score": 2.0},
        {"source": "手册.pdf", "chapter": "第1章", "score": 5.0},
        {"source": "手册.pdf", "chapter": "第2章", "score": 3.0},
    ])
    _check(checks, "同书同章去重并保留最高分", len(deduped) == 2 and deduped[0]["score"] == 5.0)

    class FakeStore:
        def get_source(self, source_id):
            return {"id": source_id, "filename": "测试手册.pdf"}

        def get_chapters(self, source_id):
            return [
                {"title": "第1章 GMAW参数", "summary": "焊接电流与电压", "content": "GMAW焊接电流和电弧电压需要按板厚调整。" * 10},
                {"title": "2", "summary": "", "content": "���\x00" * 60},
            ]

    with tempfile.TemporaryDirectory() as tmp:
        vi = VectorIndex(dim=256, index_dir=tmp)
        index_book_chapters(FakeStore(), "book-1", vi)
        _check(checks, "向量入库跳过严重乱码章节", len(vi.ids) == 1)
        vi.add_document(
            "duplicate",
            "GMAW焊接电流和电压按板厚调整",
            meta={"source": "测试手册.pdf", "chapter": "第1章 GMAW参数", "quality_score": 0.5},
        )
        hits = vi.search("GMAW焊接电流", top_k=5)
        _check(checks, "向量结果按来源与章节去重", len(hits) == 1)
        vi.save()
        loaded = VectorIndex(dim=256, index_dir=tmp)
        _check(checks, "新版向量索引清单可校验并加载", loaded.load())
        manifest = Path(tmp) / "manifest.json"
        text = manifest.read_text(encoding="utf-8").replace('"version": 2', '"version": 1')
        manifest.write_text(text, encoding="utf-8")
        _check(checks, "旧版向量索引会触发重建", not VectorIndex(dim=256, index_dir=tmp).load())

    with tempfile.TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "cache.json"
        cache_path.write_text(json.dumps({
            "旧问题": {"payload": {"process_card": {"process": "旧结构"}}, "fp": "fp", "ts": time.time()}
        }, ensure_ascii=False), encoding="utf-8")
        _check(checks, "旧结构答案缓存不会在升级后继续返回", AnswerCache(path=str(cache_path)).stats()["entries"] == 0)

    with tempfile.TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "cache.json"
        cache_path.write_text(json.dumps({
            "旧卡片": {
                "payload": {"process_card": {"process": "GMAW"}},
                "fp": "fp", "schema": 2, "ts": time.time(),
            }
        }, ensure_ascii=False), encoding="utf-8")
        _check(checks, "缺少参数签名的旧工艺卡缓存不会加载", AnswerCache(path=str(cache_path)).stats()["entries"] == 0)

    with tempfile.TemporaryDirectory() as tmp:
        cache = AnswerCache(path=str(Path(tmp) / "cache.json"), sim_threshold=0.0, jaccard_floor=0.0)
        cache.put(
            "Q345钢板 12mm GMAW焊接参数",
            {"process_card": {"process": "GMAW", "input_completeness": {"status": "complete"}}},
            "fp",
        )
        unsafe_hit = cache.get("Q345钢板 12mm焊接参数", "fp")
        _check(checks, "工艺输入槽位不同时不复用相似缓存", unsafe_hit is None)

    router = QARouter()

    def card_for(query: str):
        extracted = router.extract_params(query)
        matched = router.match_parameters(extracted, query)
        return build_process_card(extracted, matched, query), matched

    complete_card, _ = card_for("Q345钢板 12mm GMAW焊接参数")
    _check(checks, "母材板厚工艺齐全时完整度为100%", complete_card["input_completeness"]["status"] == "complete")
    _check(checks, "完整卡片包含逐参数来源", complete_card["parameter_sources"]["electrical.current_a"]["type"] in {"measured", "knowledge_base"})

    no_process_card, no_process_match = card_for("Q345钢板 12mm焊接参数")
    no_process_info = no_process_card["input_completeness"]
    _check(checks, "缺工艺时列出待补充字段", any(x["field"] == "process" for x in no_process_info["missing_fields"]))
    _check(checks, "缺工艺时明确采用GMAW/MIG默认基线", no_process_card["process_assumed"] and no_process_card["parameter_sources"]["process"]["type"] == "system_default")

    no_thickness_card, _ = card_for("Q345钢板 GMAW焊接电流")
    _check(checks, "缺板厚时坡口规则标为需确认", no_thickness_card["parameter_sources"]["groove"]["requires_confirmation"])
    _check(checks, "待确认参数汇总到卡片完整度信息", any(x["parameter"] == "groove" for x in no_thickness_card["input_completeness"]["pending_confirmation_items"]))

    import server
    section_text = server._assemble_card_sections(
        {"process_card": no_process_card, "param_match": no_process_match}, {}
    )["process_plan"]["content"]
    _check(checks, "服务端答案展示缺失项和正确默认工艺", "工艺卡待确认" in section_text and "GMAW/MIG" in section_text)

    frontend = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text(encoding="utf-8")
    _check(checks, "前端提供补充信息后重新生成提示", "补充信息后重新生成" in frontend)
    _check(checks, "前端焊接参数表展示来源列", "<th>来源</th>" in frontend and "sourceBadge" in frontend)

    print("\n" + "=" * 50)
    print("🧪 检索质量与工艺卡可靠性专项测试")
    print("=" * 50)
    for description, ok in checks:
        print(f"  {'✓' if ok else '✗'} {description}")
    passed = sum(1 for _, ok in checks if ok)
    print(f"\n  {'通过' if passed == len(checks) else '失败'}: {passed}/{len(checks)}")
    return passed == len(checks)


if __name__ == "__main__":
    raise SystemExit(0 if run_tests() else 1)
