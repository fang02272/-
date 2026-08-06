"""
GPU 新书入库工具 — 一条命令完成：GPU OCR → 学习入库 → 专家库/向量库重建
==========================================================================
在 GPU 环境（.venv-gpu / py3.12 + paddlepaddle-gpu）下运行：
  .venv-gpu/Scripts/python.exe tools/gpu_ingest.py <uploads中的PDF文件名>

例：
  .venv-gpu/Scripts/python.exe tools/gpu_ingest.py 焊接手册.pdf
  .venv-gpu/Scripts/python.exe tools/gpu_ingest.py            # 默认重跑《实用焊接工艺手册_第二版》

说明：
- 扫描版 PDF 会用 PaddleOCR(GPU) 逐页识别（断点续传，缓存到 uploads/{书名}_full_ocr.txt）
- 文字版 PDF 直接提取，不走 OCR
- 学习入库后自动重建 专家知识库 + 本地向量库，前端检索立刻可用
- 命令行不用切到 .venv-gpu 也能跑（回退 CPU，扫描版会很慢）
"""

import sys
import os
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_PDF = "实用焊接工艺手册_第二版.pdf"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # ---- 确定目标 PDF ----
    name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    pdf_path = PROJECT_ROOT / "uploads" / name
    if not pdf_path.exists():
        print(f"❌ 找不到 {pdf_path}")
        print("   请把 PDF 放到 uploads/ 目录后重试。")
        sys.exit(1)

    # ---- 判断是否有 GPU ----
    try:
        import paddle
        cuda = paddle.device.is_compiled_with_cuda() and int(paddle.device.cuda.device_count()) > 0
    except Exception:
        cuda = False
    print(f"{'✅ GPU 模式' if cuda else '⚠️ CPU 模式（扫描版会很慢，建议用 .venv-gpu）'}")

    # ---- 1) 解析 + OCR ----
    from app.pdf_parser import get_parser
    from app.knowledge_store import get_store

    t0 = time.time()
    print(f"⏳ 解析 {name} ...")
    parser = get_parser()
    parsed = parser.parse(str(pdf_path))
    print(f"   页数 {parsed['page_count']} | 文本 {parsed['text_length']} 字 | "
          f"表格 {parsed['tables_count']} | 扫描版: {parsed.get('is_scanned', False)}")
    if parsed.get("warning"):
        print(f"   ⚠️ {parsed['warning'][:120]}")

    # ---- 2) 学习入库（自动替换同名旧书） ----
    store = get_store()
    source_id = store.learn_book(
        filename=name,
        full_text=parsed["full_text"],
        tables=parsed["tables"],
        page_count=parsed["page_count"],
        images=parsed["images"],
    )
    src = store.get_source(source_id)
    print(f"📚 已入库《{name}》— {src.get('chapter_count', 0)}章, "
          f"{src.get('keyword_count', 0)}关键词, {src.get('table_count', 0)}表格")

    # ---- 3) 重建 专家知识库 + 向量库 ----
    from app.expert_knowledge_base import ExpertKnowledgeBase
    from app.vector_store import VectorIndex, index_book_chapters

    kb = ExpertKnowledgeBase()
    kb.build(store)
    vi = VectorIndex()
    vi.clear_all()
    for canonical, entry in kb.concepts.items():
        vi.add_document(
            f"expert/{canonical}",
            f"概念：{entry['name']}（{'，'.join(entry['aliases'][:6])}）\n{entry['definition']}\n{entry['application']}",
            meta={"source": "expert", "kind": "expert", "chapter": f"概念：{entry['name']}"},
        )
    for s in store.list_sources():
        index_book_chapters(store, s["id"], vi)
    vi.save()

    # ---- 4) 清缓存（旧答案失效） ----
    try:
        from app.answer_cache import get_cache
        get_cache().invalidate()
    except Exception:
        pass

    print(f"\n🧠 专家知识库 {len(kb.concepts)} 概念 + 向量库 {len(vi.ids)} 行")
    print(f"✅ 完成！耗时 {time.time() - t0:.0f}s。前端直接提问即可检索到新书内容。")
    print("   （若是在主环境 python 下运行，服务需重启；.venv-gpu 只用于本工具）")


if __name__ == "__main__":
    main()
