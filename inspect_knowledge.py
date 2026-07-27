"""
知识库自检工具 — 不调用大模型，纯本地检查
===========================================
用法:
  python inspect_knowledge.py              # 查看所有已学习的书
  python inspect_knowledge.py "焊接参数"    # 测试关键词匹配
  python inspect_knowledge.py --chapters   # 显示每本书的完整目录
"""

import sys
import io
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Use UTF-8 mode if available
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def main():
    from knowledge_store import get_store
    from welding_qa_system import WeldingQASystem

    store = get_store()
    sources = store.list_sources()

    if not sources:
        print("❌ 知识库为空。请将PDF放入 uploads/ 目录，然后运行 start.py")
        print("   start.py 启动时会自动学习 uploads/ 中的所有PDF")
        return

    show_chapters = "--chapters" in sys.argv
    test_query = None
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            test_query = arg
            break

    # ============================================================
    # 1. 知识库概览
    # ============================================================
    print("=" * 60)
    print("📚 焊接工艺知识库 — 自检报告")
    print("=" * 60)
    print(f"\n已学习书籍: {len(sources)} 本\n")

    total_chapters = 0
    total_keywords = set()
    for i, src in enumerate(sources, 1):
        chs = store.get_chapters(src["id"])
        total_chapters += len(chs)
        for ch in chs:
            total_keywords.update(ch.get("keywords", []))

        print(f"{'─' * 50}")
        print(f"  📄 第{i}本: 《{src['filename']}》")
        print(f"     ID: {src['id']}")
        print(f"     页数: {src.get('page_count', '?')}页")
        print(f"     章节: {src.get('chapter_count', 0)}章")
        print(f"     关键词: {src.get('keyword_count', 0)}个")
        print(f"     表格: {src.get('table_count', 0)}个")
        print(f"     总字数: {src.get('text_length', 0):,}字")
        print(f"     存储路径: saved_knowledge/{src['id']}/")

        if show_chapters and chs:
            print(f"\n     📑 完整目录:")
            for j, ch in enumerate(chs[:50], 1):
                kws = ch.get("keywords", [])[:8]
                cl = ch.get("content_length", 0)
                summary = ch.get("summary", "")[:80]
                print(f"       {j:2d}. {ch['title'][:60]}")
                print(f"           {cl:,}字 | 关键词: {', '.join(kws[:6])}")
                if summary:
                    print(f"           摘要: {summary}...")

    print(f"\n{'─' * 50}")
    print(f"  📊 合计: {len(sources)}本书, {total_chapters}章, {len(total_keywords)}个不重复关键词")
    print(f"{'─' * 50}")

    # ============================================================
    # 2. 关键词匹配测试（如果提供了查询词）
    # ============================================================
    if test_query:
        print(f"\n{'=' * 60}")
        print(f"🔍 关键词匹配测试: \"{test_query}\"")
        print(f"{'=' * 60}")

        qa = WeldingQASystem()
        qa.load_external_knowledge([
            {
                "filename": s["filename"],
                "keywords": store.get_keywords(s["id"]),
                "chapters": store.get_chapters(s["id"]),
            }
            for s in sources
        ])

        kws = qa.extract_keywords(test_query)
        cats = qa.match_categories(kws)
        cross = store.search_across_sources(test_query)

        print(f"\n  匹配关键词 ({len(kws)}个):")
        for kw in kws[:30]:
            print(f"    · {kw}")

        print(f"\n  匹配类别:")
        for cat, cat_kws in sorted(cats.items()):
            src_type = "📘原书" if not cat.startswith("📄_") else "📄外部"
            name = cat.replace("📄_", "")
            print(f"    [{src_type}] {name}: {', '.join(cat_kws[:8])}")

        if cross:
            print(f"\n  📖 最相关章节 (Top {min(8, len(cross))}):")
            for j, m in enumerate(cross[:8], 1):
                print(f"    {j}. [{m['score']}分] 《{m['source']}》「{m['chapter']}」")
                if m.get("matched_keywords"):
                    print(f"       匹配: {', '.join(m['matched_keywords'][:6])}")

    # ============================================================
    # 3. 上传目录检查
    # ============================================================
    upload_dir = PROJECT_ROOT / "uploads"
    if upload_dir.exists():
        pdfs = list(upload_dir.glob("*.pdf"))
        known_names = {s["filename"] for s in sources}
        unlearned = [p for p in pdfs if p.name not in known_names]
        print(f"\n{'─' * 50}")
        print(f"  📂 uploads/ 目录: {len(pdfs)} 个PDF")
        if unlearned:
            print(f"  ⚠️  未学习: {len(unlearned)} 个 (运行 start.py 自动学习)")
            for p in unlearned:
                size_kb = p.stat().st_size / 1024
                print(f"       · {p.name} ({size_kb:.0f}KB)")
        else:
            print(f"  ✅ 全部已学习")
        print(f"{'─' * 50}")

    print(f"\n💡 提示: 将新PDF放入 uploads/ 目录 → 运行 start.py → 自动学习")
    print(f"💡 浏览器访问 http://localhost:8000/api/knowledge/inspect?q=关键词 可在线查看\n")


if __name__ == "__main__":
    main()
