"""
专家知识库 + 向量数据库 构建脚本
================================
把 基座常量 + 所有已学书籍 压缩为：
  1. 专家知识库  saved_knowledge/expert_kb.json    （概念 → 解析/应用/工艺类型/来源）
  2. 向量数据库  saved_knowledge/vector_index/     （numpy 特征向量，增量可追加）

用法：
  python build_expert_kb.py          # 全量重建（幂等，毫秒级）
  python build_expert_kb.py --query 氩弧焊   # 重建后跑一个检索样例
"""


import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sys
import time


def fix_console():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main():
    fix_console()
    t0 = time.time()

    from app.knowledge_store import get_store
    from app.expert_knowledge_base import ExpertKnowledgeBase
    from app.vector_store import VectorIndex, index_book_chapters

    store = get_store()
    sources = store.list_sources()
    print(f"📚 已学书籍 {len(sources)} 本：")
    for s in sources:
        print(f"   · {s['filename']}（{s.get('chapter_count', 0)}章 / {s.get('keyword_count', 0)}关键词）")

    # ---- 1. 专家知识库 ----
    kb = ExpertKnowledgeBase()
    kb_stats = kb.build(store)
    print(f"\n🧠 专家知识库：{kb_stats['concepts']} 概念 / {kb_stats['alias_index']} 别名 → expert_kb.json")

    # ---- 2. 向量数据库（全量重建） ----
    vi = VectorIndex()
    vi.clear_all()
    # 专家概念条目
    for canonical, entry in kb.concepts.items():
        text = (
            f"概念：{entry['name']}（{ '，'.join(entry['aliases'][:6]) }）\n"
            f"{entry['definition']}\n{entry['application']}\n"
            f"工艺类型：{'、'.join(entry['process_types'])}"
        )
        vi.add_document(
            f"expert/{canonical}",
            text,
            meta={"source": "expert", "kind": "expert", "chapter": f"概念：{entry['name']}"},
        )
    # 每书每章
    book_rows = 0
    for s in sources:
        before = len(vi.ids)
        index_book_chapters(store, s["id"], vi)
        book_rows += len(vi.ids) - before
    vi.save()

    print(f"\n🔎 向量数据库：{len(vi.ids)} 行向量（专家 {len(vi.ids) - book_rows} + 书籍章节 {book_rows}）→ vector_index/")
    print(f"   sources: {vi._source_counts()}")

    # ---- 3. 检索样例 ----
    queries = sys.argv[sys.argv.index("--query") + 1] if "--query" in sys.argv else None
    if queries:
        for q in queries.split("|"):
            print(f"\n❓ 检索示例：{q}")
            for hit in vi.search(q, top_k=3):
                meta = hit["meta"]
                print(f"   [{hit['score']:.3f}] {hit['doc_id']}  (来源: {meta.get('source','')} / {meta.get('chapter','')})")

    print(f"\n✅ 完成，耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
