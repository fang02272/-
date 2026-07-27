"""
PDF 解析器 — 文本 + 表格 + 图片全解析
依赖：PyMuPDF (fitz) + pdfplumber
"""

import base64
import io
import logging
import os
import re
from typing import List, Dict, Optional, Tuple
from pathlib import Path

logger = logging.getLogger("pdf_parser")


class PDFParser:
    """PDF 全文解析器：提取文本、表格、图片"""

    def __init__(self, upload_dir: str = "uploads"):
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._last_warning = ""

    # ============================================================
    # 文本提取 (含OCR回退)
    # ============================================================
    def extract_text(self, filepath: str) -> str:
        """提取PDF文本 — 文本PDF用PyMuPDF，扫描PDF自动OCR"""
        import fitz
        doc = fitz.open(filepath)
        total_pages = len(doc)

        # 先用 PyMuPDF 提取
        pages_text = []
        empty_pages = 0
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                pages_text.append(f"[Page {page.number + 1}]\n{text.strip()}")
            else:
                empty_pages += 1
        doc.close()

        text_result = "\n\n".join(pages_text)

        # 判断是否为扫描PDF（>80%页面无文字）
        scan_ratio = empty_pages / max(total_pages, 1)
        is_scanned = scan_ratio > 0.8 and total_pages > 3

        if is_scanned or (total_pages > 10 and len(text_result) < 500):
            logger.info(f"检测到扫描PDF ({scan_ratio:.0%}页面无文字)，尝试OCR...")
            ocr_text = self._ocr_extract(filepath, max_pages=min(total_pages, 50))
            if ocr_text and len(ocr_text) > len(text_result) * 2:
                return ocr_text
            if is_scanned and not ocr_text:
                # OCR不可用 — 给出明确提示
                self._last_warning = (
                    f"⚠️ 《{Path(filepath).name}》是扫描版PDF（{total_pages}页全为图片），"
                    f"需要安装Tesseract OCR才能提取文字。\n"
                    f"安装步骤:\n"
                    f"  1. 下载: https://github.com/UB-Mannheim/tesseract/wiki\n"
                    f"  2. 安装时勾选中文语言包 (Chinese Simplified)\n"
                    f"  3. pip install pytesseract Pillow\n"
                    f"  4. 重启终端和本项目"
                )
                logger.warning(self._last_warning)
                return text_result  # 返回空或极少文字

        return text_result

    def _find_tesseract(self) -> str:
        """自动查找 Tesseract 安装路径"""
        import subprocess, os
        # 1. 先试 cmd where
        try:
            result = subprocess.run(["where", "tesseract"], capture_output=True, text=True, shell=True)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split("\n")[0]
        except Exception:
            pass
        # 2. 常见安装位置
        common_paths = [
            r"D:\ProgramData\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expanduser(r"~\AppData\Local\Tesseract-OCR\tesseract.exe"),
        ]
        for p in common_paths:
            if os.path.exists(p):
                return p
        return "tesseract"  # 回退到 PATH

    def _ocr_extract(self, filepath: str, max_pages: int = 80) -> str:
        """
        三级 OCR 引擎（按精度自动选择）：
        1. PaddleOCR — 中文识别率最高(~95%)，内置角度校正
        2. Tesseract — 通用OCR，需预装
        3. 回退空字符串
        """
        import fitz
        from PIL import Image, ImageFilter
        import io as io_mod
        import os

        # ======== 引擎 1: Tesseract（快速，优先）========
        tesseract_result = self._tesseract_ocr(filepath, max_pages)
        if tesseract_result:
            # 如果Tesseract质量不错(平均每页>100字)，直接返回
            pages = tesseract_result.split("[Page ")
            avg_chars = len(tesseract_result) / max(len(pages) - 1, 1)
            if avg_chars > 100:
                return tesseract_result

        # ======== 引擎 2: PaddleOCR（CPU优化：移动端模型 + 降DPI + 角度校正）========
        try:
            from paddleocr import PaddleOCR
            # 使用移动端轻量模型(PP-OCRv4-mobile)，CPU上快3-5倍
            ocr = PaddleOCR(
                lang='ch',
                use_angle_cls=True,          # 角度校正，提高识别率
                det_model_dir=None,            # 使用默认检测模型
                rec_model_dir=None,            # 使用默认识别模型
                use_gpu=False,                 # CPU模式
                show_log=False,
                det_db_thresh=0.3,             # 检测阈值
                det_db_box_thresh=0.5,
                rec_batch_num=6,               # CPU批处理
            )
            doc = fitz.open(filepath)
            pages_text = []
            for page_num, page in enumerate(doc):
                if page_num >= max_pages: break
                try:
                    # 1.5x DPI 加快速度，PaddleOCR检测能力强不需要太高DPI
                    mat = fitz.Matrix(1.5, 1.5)
                    pix = page.get_pixmap(matrix=mat)
                    img_path = f"{filepath}_p{page_num}.png"
                    pix.save(img_path)
                    result = ocr.ocr(img_path, cls=True)
                    try: os.remove(img_path)
                    except: pass
                    if result and result[0]:
                        text = "\n".join(line[1][0] for line in result[0] if line and len(line) > 1)
                        if text.strip():
                            pages_text.append(f"[Page {page_num + 1}]\n{text.strip()}")
                except Exception:
                    pass
            doc.close()
            if pages_text:
                logger.info(f"PaddleOCR: {len(pages_text)}/{max_pages} pages")
                return "\n\n".join(pages_text)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"PaddleOCR: {e}")

        return tesseract_result or ""

    def _tesseract_ocr(self, filepath: str, max_pages: int = 80) -> str:
        """Tesseract OCR 快速引擎"""
        import fitz
        from PIL import Image
        import io as io_mod
        try:
            import pytesseract
            tesseract_path = self._find_tesseract()
            if tesseract_path != "tesseract":
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
            pytesseract.get_tesseract_version()

            doc = fitz.open(filepath)
            pages_text = []
            for page_num, page in enumerate(doc):
                if page_num >= max_pages: break
                try:
                    mat = fitz.Matrix(2.0, 2.0)  # 2x = 144 DPI
                    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
                    img = Image.open(io_mod.BytesIO(pix.tobytes("png")))
                    # 轻量预处理：二值化提高识别速度
                    img = img.point(lambda x: 255 if x > 150 else x)
                    text = pytesseract.image_to_string(
                        img, lang='chi_sim+eng',
                        config='--psm 6'
                    )
                    if text.strip():
                        pages_text.append(f"[Page {page_num + 1}]\n{text.strip()}")
                except Exception:
                    pass
            doc.close()
            if pages_text:
                logger.info(f"Tesseract: {len(pages_text)}/{max_pages} pages extracted")
                return "\n\n".join(pages_text)
        except Exception as e:
            logger.warning(f"Tesseract failed: {e}")

        return ""

    # ============================================================
    # 表格提取
    # ============================================================
    def extract_tables(self, filepath: str) -> List[dict]:
        """使用 pdfplumber 提取表格"""
        tables = []
        try:
            import pdfplumber
            with pdfplumber.open(filepath) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    page_tables = page.extract_tables()
                    for ti, table in enumerate(page_tables):
                        if table and len(table) > 1:  # 至少有表头+1行数据
                            clean_table = []
                            for row in table:
                                clean_row = [str(cell).strip() if cell else "" for cell in row]
                                if any(cell for cell in clean_row):  # 跳过全空行
                                    clean_table.append(clean_row)
                            if clean_table:
                                tables.append({
                                    "page": page_num + 1,
                                    "index": ti,
                                    "headers": clean_table[0] if clean_table else [],
                                    "rows": clean_table[1:] if len(clean_table) > 1 else [],
                                    "markdown": self._table_to_markdown(clean_table),
                                })
        except ImportError:
            logger.warning("pdfplumber not installed, table extraction skipped")
        except Exception as e:
            logger.error(f"Table extraction failed: {e}")
        return tables

    def _table_to_markdown(self, table: List[List[str]]) -> str:
        """将二维数组转为 Markdown 表格"""
        if not table:
            return ""
        lines = []
        # 表头
        lines.append("| " + " | ".join(table[0]) + " |")
        lines.append("|" + "|".join([" --- " for _ in table[0]]) + "|")
        # 数据行
        for row in table[1:]:
            # 补齐列数
            padded = row + [""] * (len(table[0]) - len(row))
            lines.append("| " + " | ".join(padded[:len(table[0])]) + " |")
        return "\n".join(lines)

    # ============================================================
    # 图片提取
    # ============================================================
    def extract_images(self, filepath: str, max_images: int = 20) -> List[dict]:
        """提取PDF中的图片，返回base64编码"""
        images = []
        try:
            import fitz
            doc = fitz.open(filepath)
            img_count = 0
            for page_num, page in enumerate(doc):
                if img_count >= max_images:
                    break
                image_list = page.get_images(full=True)
                for img_index, img in enumerate(image_list):
                    if img_count >= max_images:
                        break
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        ext = base_image["ext"]
                        # 过滤太小的图片（可能是图标、线条等）
                        if len(image_bytes) < 2000:
                            continue
                        b64 = base64.b64encode(image_bytes).decode("ascii")
                        images.append({
                            "page": page_num + 1,
                            "index": img_index,
                            "ext": ext,
                            "width": base_image.get("width", 0),
                            "height": base_image.get("height", 0),
                            "size_bytes": len(image_bytes),
                            "base64": b64,
                            "data_uri": f"data:image/{ext};base64,{b64}",
                        })
                        img_count += 1
                    except Exception as e:
                        logger.warning(f"Image extraction error on page {page_num + 1}: {e}")
            doc.close()
        except ImportError:
            logger.warning("PyMuPDF (fitz) not installed, image extraction skipped")
        except Exception as e:
            logger.error(f"Image extraction failed: {e}")
        return images

    # ============================================================
    # 综合解析
    # ============================================================
    def parse(self, filepath: str) -> dict:
        """
        全面解析 PDF → 返回结构化数据
        """
        filename = os.path.basename(filepath)
        text = self.extract_text(filepath)
        tables = self.extract_tables(filepath)
        images = self.extract_images(filepath)

        # 提取基本统计信息
        page_count = 0
        try:
            import fitz
            doc = fitz.open(filepath)
            page_count = len(doc)
            doc.close()
        except Exception:
            pass

        warning = self._last_warning
        self._last_warning = ""  # reset

        return {
            "filename": filename,
            "filepath": filepath,
            "page_count": page_count,
            "text_length": len(text),
            "text_preview": text[:500] + ("..." if len(text) > 500 else ""),
            "full_text": text,
            "tables_count": len(tables),
            "tables": tables,
            "images_count": len(images),
            "images": images,
            "warning": warning,
            "is_scanned": (page_count > 3 and len(text) < 500),
        }

    # ============================================================
    # 文件管理
    # ============================================================
    def save_upload(self, file_content: bytes, filename: str) -> str:
        """保存上传的文件，返回文件路径"""
        safe_name = re.sub(r'[^\w\.\-一-鿿]', '_', filename)
        filepath = self.upload_dir / safe_name
        # 避免覆盖
        counter = 1
        while filepath.exists():
            stem = Path(safe_name).stem
            suffix = Path(safe_name).suffix
            filepath = self.upload_dir / f"{stem}_{counter}{suffix}"
            counter += 1
        filepath.write_bytes(file_content)
        return str(filepath)

    def list_uploads(self) -> List[dict]:
        """列出已上传的文件"""
        files = []
        for f in self.upload_dir.glob("*.pdf"):
            files.append({
                "name": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "path": str(f),
            })
        return files

    def delete_upload(self, filename: str) -> bool:
        """删除上传的文件"""
        filepath = self.upload_dir / filename
        if filepath.exists():
            filepath.unlink()
            return True
        return False


# ============================================================
# 全局实例
# ============================================================
_parser: Optional[PDFParser] = None


def get_parser(upload_dir: str = "uploads") -> PDFParser:
    global _parser
    if _parser is None:
        _parser = PDFParser(upload_dir)
    return _parser
