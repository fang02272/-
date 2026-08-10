"""
重提取表格 — 只对表格候选页做定向 OCR（文本已缓存，无需全书重跑）
==========================================================================
参考算法思路：文本已存在于 uploads/<书名>_full_ocr.txt 缓存，不会被重新OCR，
只有表格候选页（数字行多 或 含「表 X-Y」标题）需要重新识别以获取文本框坐标，
再用坐标聚类重建表格 → 重新入库（章节/关键词重建 + 表格关键词并入）→ 重建索引。

用法（GPU 环境）：
  .venv-gpu/Scripts/python.exe tools/relearn_tables.py <uploads中的PDF文件名>
  例：.venv-gpu/Scripts/python.exe tools/relearn_tables.py 实用焊接工艺手册_第二版.pdf
"""

import sys
import os
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    name = sys.argv[1] if len(sys.argv) > 1 else "实用焊接工艺手册_第二版.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    stem = Path(name).stem
    cache_path = Path("uploads") / f"{stem}_full_ocr.txt"
    if not cache_path.exists():
        print(f"❌ 找不到 OCR 缓存: {cache_path}")
        print("   请先运行 tools/gpu_ingest.py 生成 OCR 缓存后重试。")
        sys.exit(1)

    import fitz
    import paddle
    from paddleocr import PaddleOCR

    from app.pdf_parser import PDFParser
    from app.knowledge_store import get_store

    # ---- GPU 检测 ----
    cuda = paddle.device.is_compiled_with_cuda() and int(paddle.device.cuda.device_count()) > 0
    print(f"{'✅ GPU 模式' if cuda else '⚠️ CPU 模式（较慢）'}")

    parser = PDFParser("uploads")
    store = get_store()

    # ---- 1) 定位表格候选页 ----
    cands = parser.find_table_pages(cache_path)
    print(f"📊 《{name}》: 表格候选页 {len(cands)} 页")

    # ---- 2) 定向 OCR 获取文本框坐标 ----
    ocr = PaddleOCR(lang="ch", use_angle_cls=True,
                    device="gpu:0" if cuda else "cpu", show_log=False,
                    det_db_thresh=0.3, det_db_box_thresh=0.5, rec_batch_num=6)
    doc = fitz.open(str(Path("uploads") / name))
    total = len(doc)
    page_boxes = {}
    t0 = time.time()
    for i, pno in enumerate(cands, 1):
        page = doc[pno - 1]
        try:
            # scale=3.0（216 DPI）不触发放大，OCR 快且坐标够用
            img = parser._preprocess_page(page, scale=3.0)
            result = ocr.ocr(img, cls=True)
            boxes = []
            if result and result[0]:
                for item in result[0]:
                    if not item or len(item) < 2:
                        continue
                    box_pts, (text, score) = item
                    text = str(text).strip()
                    if not text or len(text) < 1 or float(score) < parser.ocr_min_confidence:
                        continue
                    xs = [pt[0] for pt in box_pts]
                    ys = [pt[1] for pt in box_pts]
                    boxes.append((min(xs), min(ys), max(xs), max(ys), text, float(score)))
            page_boxes[pno] = boxes
        except Exception as e:
            print(f"  ⚠️ 第{pno}页 OCR 失败: {e}")
            page_boxes[pno] = []
        if i % 20 == 0 or i == len(cands):
            el = time.time() - t0
            speed = el / i
            remain = (len(cands) - i) * speed / 60
            print(f"   ⏳ OCR {i}/{len(cands)} 页 ({speed:.1f}s/页, 剩余约{remain:.0f}分钟)")
    doc.close()

    # ---- 3) 重建表格 ----
    tables = parser._reconstruct_tables_from_boxes(page_boxes)
    print(f"   ✅ 重建表格 {len(tables)} 个")

    # ---- 4) 重新入库（章节/关键词重建 + 表格关键词并入）----
    full_text = parser._compose_ocr_text(cache_path, total)
    images = parser.extract_images(str(Path("uploads") / name))
    for s in store.list_sources():
        if s["filename"] == name:
            store.unregister(s["id"])
            import shutil
            shutil.rmtree(Path("saved_knowledge") / s["id"], ignore_errors=True)
    source_id = store.learn_book(filename=name, full_text=full_text, tables=tables,
                                 page_count=total, images=images)
    src = store.get_source(source_id)
    print(f"   ✅ 已入库《{name}》— {src.get('chapter_count', 0)}章, "
          f"{src.get('keyword_count', 0)}关键词, {src.get('table_count', 0)}表格")

    # ---- 5) 重建 专家知识库 + 向量库 ----
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

    # ---- 6) 清缓存 ----
    try:
        from app.answer_cache import get_cache
        get_cache().invalidate()
    except Exception:
        pass

    print(f"\n🧠 专家知识库 {len(kb.concepts)} 概念 + 向量库 {len(vi.ids)} 行")
    print(f"✅ 完成！耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
