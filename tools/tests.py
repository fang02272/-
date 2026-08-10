"""
焊接工艺知识库 — 测试用例
=========================
用法:
  python tests.py              # 运行全部测试
  python tests.py --match      # 仅关键词匹配测试
  python tests.py --knowledge  # 仅知识库完整性测试
  python tests.py --e2e        # 仅端到端测试
  python tests.py --tokenize   # 仅Jieba分词与检索测试
  python tests.py --rag-rebuild # 仅持久化知识库RAG重建测试

所有测试不调用大模型API，纯本地验证
"""

import sys
import os
import io
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


# ============================================================
# 测试用例定义
# ============================================================

# 关键词匹配测试：(查询, 必须匹配的原书类别, 必须匹配的外部书籍名片段, 期望最小关键词数)
MATCH_TESTS = [
    ("弧焊机器人焊接参数选择", ["cross_robot_welding"], [], 2),
    ("焊缝结晶裂纹怎么防止", ["第1章_焊缝"], [], 2),
    ("Q345钢板预热温度焊接冷裂纹", ["第3章_焊接热影响区"], [], 3),
    ("异种材料焊接不锈钢与铝合金", ["第5章_不同材料焊接概论"], [], 2),
    ("活性钎焊Ti元素陶瓷连接", ["第7章_表面活性化焊接"], [], 2),
    ("焊接热影响区t8/5冷却时间", ["第3章_焊接热影响区"], [], 3),
    ("扩散焊中间层材料选择", ["第9章_固相液相扩散焊接"], [], 2),
    ("板厚12mm坡口设计", ["cross_welding_params"], [], 2),
]

# 端到端测试：(查询, 期望最小内容长度, 是否期望交叉领域)
E2E_TESTS = [
    ("焊缝结晶裂纹机理", 200, False),
    ("弧焊机器人5mm钢板参数", 500, True),
    ("异种材料焊接为什么难", 300, False),
]


def green(s): return f"\033[92m{s}\033[0m"
def red(s): return f"\033[91m{s}\033[0m"
def yellow(s): return f"\033[93m{s}\033[0m"


def test_jieba_tokenization():
    """Jieba分词测试 — 验证专业词保留且不产生机械双字噪声"""
    from rag_retriever import SimpleRetriever

    retriever = SimpleRetriever()
    tokens = retriever._tokenize("Q345钢板预热温度")

    expected_terms = ["q345", "钢板", "预热", "温度"]
    forbidden_terms = ["板预", "热温"]
    checks = [
        *[(f"保留专业词: {term}", term in tokens) for term in expected_terms],
        *[(f"不产生机械双字: {term}", term not in tokens) for term in forbidden_terms],
    ]

    retriever.add_document(
        "relevant",
        "Q345钢板厚度超过25mm时，应根据碳当量和拘束度确定预热温度。",
        "test",
    )
    retriever.add_document(
        "unrelated",
        "弧焊机器人需要进行轨迹规划、焊缝跟踪和运动速度控制。",
        "test",
    )
    results = retriever.search("Q345钢板预热温度", top_k=2)
    checks.append(("相关文档排在首位", bool(results) and results[0]["id"] == "relevant"))

    print(f"\n{'='*50}")
    print("✂️ Jieba分词与检索测试")
    print(f"{'='*50}")
    print(f"\n  输入: {yellow('Q345钢板预热温度')}")
    print(f"  分词: {', '.join(tokens)}")

    passed = 0
    failed = 0
    for desc, ok in checks:
        status = green("✓") if ok else red("✗")
        print(f"    {status} {desc}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n  {green('通过') if failed == 0 else red('失败')}: {passed}通过, {failed}失败")
    return failed == 0


def test_rag_rebuild_from_saved_knowledge():
    """验证 saved_knowledge 可完整重建，且重复重建不会产生重复分块。"""
    from tempfile import TemporaryDirectory
    from knowledge_store import KnowledgeStore
    from rag_retriever import RAGContextBuilder

    with TemporaryDirectory() as temp_dir:
        store = KnowledgeStore(temp_dir)
        test_text = (
            "第1章 Q999试验钢焊接工艺\n"
            + "Q999试验钢在高拘束条件下应采用180℃预热温度，并控制层间温度。\n" * 120
        )
        source_id = store.learn_book(
            filename="Q999焊接手册.pdf",
            full_text=test_text,
            tables=[],
            page_count=5,
            images=[],
        )

        rag = RAGContextBuilder()
        built_in_count = rag.retriever.doc_count
        first_stats = rag.rebuild_from_store(store)
        first_pdf_docs = [
            doc for doc in rag.retriever.documents
            if doc["source"] == "pdf_upload"
        ]
        search_results = rag.retriever.search("Q999预热温度", top_k=5)

        second_stats = rag.rebuild_from_store(store)
        second_pdf_docs = [
            doc for doc in rag.retriever.documents
            if doc["source"] == "pdf_upload"
        ]

        checks = [
            ("识别到1个持久化知识源", first_stats["sources"] == 1),
            ("成功索引1个知识源", first_stats["indexed_sources"] == 1),
            ("生成至少1个PDF分块", first_stats["chunks"] > 0),
            ("索引过程无错误", not first_stats["errors"]),
            ("保留内置知识索引", rag.retriever.doc_count == built_in_count + second_stats["chunks"]),
            ("重复重建不增加分块", len(second_pdf_docs) == len(first_pdf_docs)),
            ("分块ID不重复", len({doc["id"] for doc in second_pdf_docs}) == len(second_pdf_docs)),
            ("分块保留真实书名", all("Q999焊接手册.pdf" in doc["text"] for doc in second_pdf_docs)),
            ("查询能命中重建后的知识源", any(source_id in item["id"] for item in search_results)),
            ("可按文件名定位知识源", store.get_source_by_filename("Q999焊接手册.pdf")["id"] == source_id),
        ]

        store.unregister(source_id)
        empty_stats = rag.rebuild_from_store(store)
        checks.extend([
            ("删除后持久化知识源为空", empty_stats["sources"] == 0),
            ("删除后外部RAG分块被清除", not any(
                doc["source"] == "pdf_upload" for doc in rag.retriever.documents
            )),
        ])

    print(f"\n{'='*50}")
    print("♻️ saved_knowledge RAG重建测试")
    print(f"{'='*50}")
    print(
        f"\n  首次重建: {first_stats['indexed_sources']}个知识源, "
        f"{first_stats['chapters']}个章节, {first_stats['chunks']}个分块"
    )

    passed = 0
    failed = 0
    for desc, ok in checks:
        status = green("✓") if ok else red("✗")
        print(f"    {status} {desc}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n  {green('通过') if failed == 0 else red('失败')}: {passed}通过, {failed}失败")
    return failed == 0


def test_knowledge_integrity():
    """知识库完整性测试 — 检查每本已学习书籍的数据完整性"""
    from app.knowledge_store import get_store
    store = get_store()
    sources = store.list_sources()

    print(f"\n{'='*50}")
    print(f"📋 知识库完整性测试 ({len(sources)}本书)")
    print(f"{'='*50}")

    passed = 0
    failed = 0

    for src in sources:
        chs = store.get_chapters(src["id"])
        kws = store.get_keywords(src["id"])
        full_text = store.get_full_text(src["id"])

        checks = [
            ("章节数 >= 1", len(chs) >= 1),
            ("关键词数 >= 10", len(kws) >= 10),
            ("全文长度 >= 1000", len(full_text) >= 1000),
            ("registry有chapter_count", src.get("chapter_count", 0) > 0),
            ("registry有keyword_count", src.get("keyword_count", 0) > 0),
        ]

        print(f"\n  📄 《{src['filename']}》")
        for desc, ok in checks:
            status = green("✓") if ok else red("✗")
            print(f"    {status} {desc}")
            if ok:
                passed += 1
            else:
                failed += 1
                if "章节" in desc:
                    print(f"      ⚠️ 可能是扫描版PDF，需要Tesseract OCR")

        # 显示章节详情
        print(f"    📑 {len(chs)}章, {len(kws)}关键词, {len(full_text):,}字")
        for ch in chs[:3]:
            kc = len(ch.get("keywords", []))
            cl = ch.get("content_length", 0)
            print(f"      - {ch['title'][:50]} [{kc}kw, {cl:,}字]")

    print(f"\n  {green('通过') if failed == 0 else red('失败')}: {passed}通过, {failed}失败")
    return failed == 0


def test_keyword_matching():
    """关键词匹配测试 — 验证查询能正确匹配到预期的知识源"""
    from app.welding_qa_system import WeldingQASystem
    from app.knowledge_store import get_store

    store = get_store()
    qa = WeldingQASystem()

    # 注入外部知识
    sources = store.list_sources()
    external = []
    for src in sources:
        external.append({
            "filename": src["filename"],
            "keywords": store.get_keywords(src["id"]),
            "chapters": store.get_chapters(src["id"]),
        })
    qa.load_external_knowledge(external)

    print(f"\n{'='*50}")
    print(f"🔍 关键词匹配测试 ({len(MATCH_TESTS)}条)")
    print(f"{'='*50}")

    passed = 0
    failed = 0

    for query, expected_book_cats, expected_ext_names, min_kw in MATCH_TESTS:
        kws = qa.extract_keywords(query)
        cats = qa.match_categories(kws)

        checks = [
            ("关键词 >= 最小数", len(kws) >= min_kw),
        ]
        for exp_cat in expected_book_cats:
            checks.append((f"匹配原书类别: {exp_cat}", exp_cat in cats))
        for exp_ext in expected_ext_names:
            found = any(exp_ext in k for k in cats)
            checks.append((f"匹配外部书籍: {exp_ext}", found))

        print(f"\n  查询: {yellow(query)}")
        print(f"  关键词({len(kws)}): {', '.join(kws[:10])}")
        for desc, ok in checks:
            status = green("✓") if ok else red("✗")
            print(f"    {status} {desc}")
            if ok:
                passed += 1
            else:
                failed += 1

    print(f"\n  {green('通过') if failed == 0 else red('失败')}: {passed}通过, {failed}失败")
    return failed == 0


def test_e2e():
    """端到端测试 — 验证完整问答链路（不调LLM）"""
    from app.welding_qa_system import WeldingQASystem
    from app.knowledge_store import get_store

    store = get_store()
    qa = WeldingQASystem()

    sources = store.list_sources()
    external = []
    for src in sources:
        external.append({
            "filename": src["filename"],
            "keywords": store.get_keywords(src["id"]),
            "chapters": store.get_chapters(src["id"]),
        })
    qa.load_external_knowledge(external)

    print(f"\n{'='*50}")
    print(f"🔗 端到端测试 ({len(E2E_TESTS)}条)")
    print(f"{'='*50}")

    passed = 0
    failed = 0

    for query, min_len, expect_cross in E2E_TESTS:
        result = qa.generate_structured(query)
        sci = result['sections']['science']
        recs = result['sections']['recommendations']
        srcs = result['sections']['sources']

        checks = [
            ("关键词非空", len(result['keywords']) > 0),
            ("科普内容 >= 最小长度", len(sci.get('content', '')) >= min_len),
            ("推荐非空", len(recs.get('items', [])) > 0),
            ("来源非空", len(srcs.get('primary', [])) > 0),
        ]
        if expect_cross:
            checks.append(("交叉领域", result['is_cross_domain']))

        print(f"\n  查询: {yellow(query)}")
        for desc, ok in checks:
            status = green("✓") if ok else red("✗")
            print(f"    {status} {desc}")
            if ok:
                passed += 1
            else:
                failed += 1

    print(f"\n  {green('通过') if failed == 0 else red('失败')}: {passed}通过, {failed}失败")
    return failed == 0


def test_cross_source_search():
    """跨知识源搜索测试"""
    from app.knowledge_store import get_store
    store = get_store()

    print(f"\n{'='*50}")
    print("🔎 跨知识源搜索测试")
    print(f"{'='*50}")

    test_queries = [
        "焊接参数电流电压",
        "预热温度Q345",
        "不锈钢焊接工艺",
    ]

    for q in test_queries:
        results = store.search_across_sources(q)
        print(f"\n  查询: {yellow(q)}")
        print(f"  匹配章节: {len(results)}")
        for r in results[:3]:
            print(f"    [{r['score']}分] 《{r['source']}》「{r['chapter'][:50]}」")
            if r.get('matched_keywords'):
                print(f"      匹配: {', '.join(r['matched_keywords'][:5])}")

    return True


def test_jieba_tokenization():
    """Jieba分词测试（融合 origin/main 分支）— 验证专业词保留且不产生机械双字"""
    from app.rag_retriever import SimpleRetriever

    retriever = SimpleRetriever()
    tokens = retriever._tokenize("Q345钢板预热温度")

    expected_terms = ["q345", "钢板", "预热", "温度"]
    forbidden_terms = ["板预", "热温"]
    checks = [
        *[(f"保留专业词: {term}", term in tokens) for term in expected_terms],
        *[(f"不产生机械双字: {term}", term not in tokens) for term in forbidden_terms],
    ]

    retriever.add_document(
        "relevant",
        "Q345钢板厚度超过25mm时，应根据碳当量和拘束度确定预热温度。",
        "test",
    )
    retriever.add_document(
        "unrelated",
        "弧焊机器人需要进行轨迹规划、焊缝跟踪和运动速度控制。",
        "test",
    )
    results = retriever.search("Q345钢板预热温度", top_k=2)
    checks.append(("相关文档排在首位", bool(results) and results[0]["id"] == "relevant"))

    print(f"\n{'='*50}")
    print("✂️ Jieba分词与检索测试")
    print(f"{'='*50}")
    print(f"\n  输入: {yellow('Q345钢板预热温度')}")
    print(f"  分词: {', '.join(tokens)}")

    passed = 0
    failed = 0
    for desc, ok in checks:
        status = green("✓") if ok else red("✗")
        print(f"    {status} {desc}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n  {green('通过') if failed == 0 else red('失败')}: {passed}通过, {failed}失败")
    return failed == 0


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    run_all = len(sys.argv) == 1
    results = {}

    if run_all or "--tokenize" in sys.argv:
        results['tokenization'] = test_jieba_tokenization()

    if run_all or "--rag-rebuild" in sys.argv:
        results['rag_rebuild'] = test_rag_rebuild_from_saved_knowledge()

    if run_all or "--knowledge" in sys.argv:
        results['knowledge'] = test_knowledge_integrity()

    if run_all or "--match" in sys.argv:
        results['match'] = test_keyword_matching()

    if run_all or "--cross" in sys.argv:
        results['cross'] = test_cross_source_search()

    if run_all or "--e2e" in sys.argv:
        results['e2e'] = test_e2e()

    if run_all or "--tokenize" in sys.argv:
        results['tokenization'] = test_jieba_tokenization()

    # Summary
    print(f"\n{'='*50}")
    print(f"📊 总结果")
    print(f"{'='*50}")
    all_pass = True
    for name, passed in results.items():
        s = green("PASS") if passed else red("FAIL")
        print(f"  {s}  {name}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print(green("✅ 全部测试通过"))
    else:
        print(red("❌ 存在未通过的测试，请检查上述红色项目"))
    sys.exit(0 if all_pass else 1)
