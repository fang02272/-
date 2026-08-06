"""
GPU 重跑《实用焊接工艺手册》第二版 OCR
========================================
在 GPU 环境（.venv-gpu / py3.12 + paddlepaddle-gpu）下运行：
  .venv-gpu/Scripts/python.exe tools/reocr_handbook.py

流程：
1. 删除旧 OCR 缓存（full_ocr.txt + progress.json）
2. 用 PaddleOCR(GPU) + cv2 预处理 逐页识别
3. 写入 uploads/实用焊接工艺手册_第二版_full_ocr.txt（与 pdf_parser 共用缓存）
4. 断点续传：进度写入 progress.json
"""

import sys
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(str(Path(__file__).resolve().parent.parent))

import fitz
from app.pdf_parser import PDFParser

HANDBOOK = r"uploads/实用焊接工艺手册_第二版.pdf"


def main():
    # 修复 Windows GBK 控制台 emoji 打印问题
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    import paddle
    from paddleocr import PaddleOCR

    cuda = paddle.device.is_compiled_with_cuda()
    gpus = paddle.device.cuda.device_count() if cuda else 0
    print(f"Paddle {paddle.__version__} | CUDA编译: {cuda} | GPU数量: {gpus}")
    if not cuda or gpus == 0:
        print("⚠️ 未检测到可用 GPU，将使用 CPU（很慢）。请确认 paddlepaddle-gpu 已正确安装。")
        device = "cpu"
    else:
        device = "gpu:0"
        print(f"✅ 使用 GPU 进行 OCR: {device}")

    ocr = PaddleOCR(
        lang="ch",
        use_angle_cls=True,
        device=device,
        show_log=False,
        det_db_thresh=0.3,
        det_db_box_thresh=0.5,
        rec_batch_num=6,
    )

    parser = PDFParser()
    out_txt, progress_json = parser._ocr_output_paths(HANDBOOK)

    # 删除旧缓存，全新 OCR
    out_txt.unlink(missing_ok=True)
    progress_json.unlink(missing_ok=True)

    done_pages, failed_pages = parser._load_ocr_progress(progress_json)
    doc = fitz.open(HANDBOOK)
    total = len(doc)
    t0 = time.time()
    print(f"开始 OCR：{total} 页 → {out_txt.name}")

    with open(out_txt, "w", encoding="utf-8") as fout:
        for pn, page in enumerate(doc):
            try:
                img = parser._preprocess_page(page)
                result = ocr.ocr(img, cls=True)
                if result and result[0]:
                    lines = [line[1][0] for line in result[0] if line and len(line) > 1]
                    text = parser._clean_ocr_text("\n".join(lines))
                    if text.strip():
                        fout.write(f"[Page {pn + 1}]\n{text.strip()}\n\n")
                        fout.flush()
                done_pages.add(pn)
            except Exception as e:
                failed_pages[pn] = failed_pages.get(pn, 0) + 1
                if failed_pages[pn] >= 2:
                    done_pages.add(pn)
                    print(f"  ⚠️ 第{pn+1}页连续失败跳过: {e}")
            if (pn + 1) % 20 == 0 or (pn + 1) == total:
                el = time.time() - t0
                print(f"  {pn + 1}/{total} 页，耗时 {el:.0f}s，平均 {el / max(pn + 1, 1):.1f}s/页", flush=True)
            parser._save_ocr_progress(progress_json, done_pages, failed_pages, total)
    doc.close()

    full = out_txt.read_text(encoding="utf-8")
    print(f"✅ OCR 完成：{total} 页，耗时 {time.time() - t0:.0f}s，总字数 {len(full)}")
    # 保留 progress.json 作为"已完成"标记（pdf_parser 读到全齐直接复用，不会重复追加）


if __name__ == "__main__":
    main()
