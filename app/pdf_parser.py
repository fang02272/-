"""
PDF 解析器 — 文本 + 表格 + 图片全解析
依赖：PyMuPDF (fitz) + pdfplumber
"""

import base64
import io
import json
import logging
import os
import re
from typing import List, Dict, Optional, Tuple
from pathlib import Path

logger = logging.getLogger("pdf_parser")

# ============================================================
# OCR 纠错字典（基于本项目实测乱码样本，v2.5 新增）
# 应用顺序：短语级(长串) 先于 字符级(单字)；要求 len(原文)>=2 防误伤
# ============================================================
OCR_TERM_FIXES = [
    # 工艺/方法
    ("氢对焊", "氩弧焊"), ("氢弧焊", "氩弧焊"), ("筑弧焊", "氩弧焊"), ("氨弧焊", "氩弧焊"),
    ("铭极", "钨极"), ("鸽极", "钨极"), ("鸽弧焊", "钨极氩弧焊"), ("铬极", "钨极"),
    ("烛接", "焊接"), ("焊栩", "焊机"), ("焊颖", "焊缝"), ("焊烽", "焊缝"),
    ("乙烽焰", "乙炔焰"), ("乙炔烽", "乙炔焰"), ("乙焕焰", "乙炔焰"),
    ("切币", "切割"), ("气荐", "气割"), ("电孤", "电弧"), ("电济", "电弧"),
    ("焊曰", "焊条"), ("焊余", "焊条"), ("焊丝曰", "焊丝"),
    # 材料/冶金
    ("铺基合金", "镍基合金"), ("镍某合金", "镍基合金"), ("锲基", "镍基"),
    ("铀及钢合金", "铜及铜合金"), ("铜某合金", "铜合金"),
    ("合人金元泰", "合金元素"), ("合金兀素", "合金元素"),
    ("麻损", "磨损"), ("采杭", "磨损"), ("脯损", "磨损"),
    ("铸铁热焊", "铸铁热焊"), ("锈铁", "铸铁"), ("馈", "铝"),
    ("碳素结钢", "碳素结构钢"), ("合金结构钢", "合金结构钢"),
    # 参数/其他
    ("保护气休", "保护气体"), ("气体保护焊", "气体保护焊"),
    ("预热温彦", "预热温度"), ("预热温焉", "预热温度"),
    ("片坡旧", "坡口"), ("坡日", "坡口"),
    ("埋驱", "埋弧"), ("自动焊", "自动焊"), ("氢弧", "氩弧"),
]
OCR_SYMBOL_FIXES = [
    ("CO;", "CO₂"), ("CO2", "CO₂"), ("C0,", "CO₂"), ("CO,", "CO₂"),
    ("0. 5", "0.5"), ("3 2", "32"), ("~ ~", "~"),
]
# 单字级（低频，命中整词才替换，防误伤）
OCR_CHAR_FIXES = [("馈", "铝"), ("铭", "钨")]


class PDFParser:
    """PDF 全文解析器：提取文本、表格、图片"""

    def __init__(self, upload_dir: str = "uploads",
                 ocr_dpi: float = 2.0, ocr_min_confidence: float = 0.5):
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._last_warning = ""
        # OCR 配置（定向表格重建用）
        self.ocr_dpi = ocr_dpi
        self.ocr_min_confidence = ocr_min_confidence

    # ============================================================
    # OCR 缓存合成 / 表格候选页定位（参考 relearn_tables 算法）
    # ============================================================
    @staticmethod
    def _compose_ocr_text(cache_path, total_pages: int = None) -> str:
        """从 OCR 缓存文件合成全文：{页号: 行列表} → [Page N] 格式文本"""
        pages = {}
        cur = None
        buf = []
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\[Page (\d+)\]", line)
            if m:
                if cur is not None:
                    pages[cur] = buf
                cur, buf = int(m.group(1)), []
            elif cur is not None:
                buf.append(line)
        if cur is not None:
            pages[cur] = buf
        parts = []
        for k in sorted(pages):
            text = "\n".join(pages[k]).strip()
            if text:
                parts.append(f"[Page {k}]\n{text}")
        return "\n\n".join(parts)

    def find_table_pages(self, cache_path, total_pages: int = None) -> list:
        """定位表格候选页：数字行多(>=8) 或 含「表 X-Y」标题"""
        cache_path = Path(cache_path)
        pages = {}
        cur = None
        buf = []
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\[Page (\d+)\]", line)
            if m:
                if cur is not None:
                    pages[cur] = buf
                cur, buf = int(m.group(1)), []
            elif cur is not None:
                buf.append(line)
        if cur is not None:
            pages[cur] = buf
        cands = []
        for pno, lines in pages.items():
            digit_lines = sum(1 for l in lines if re.search(r"\d", l))
            has_caption = any(re.match(r"\s*表\s*\d+(\.\d+)*", l) for l in lines)
            if digit_lines >= 8 or has_caption:
                cands.append(pno)
        return cands

    # ============================================================
    # 表格重建 — 由 OCR 文本框坐标聚类还原表格
    # ============================================================
    def _reconstruct_tables_from_boxes(self, page_boxes: dict) -> list:
        """
        由每页的 OCR 文本框 (bbox, text, score) 重建表格。
        page_boxes = {页号: [(x0,y0,x1,y1, text, score), ...]}
        返回 [{page, index, headers, rows, markdown}]
        """
        tables = []
        for pno, boxes in page_boxes.items():
            if not boxes or len(boxes) < 6:
                continue
            valid = [b for b in boxes if (b[2] - b[0]) > 15 and len(b[4]) >= 1]
            if len(valid) < 6:
                continue
            cols = self._cluster_cols(valid)
            if len(cols) < 2:
                continue
            rows = self._cluster_rows(valid)
            if len(rows) < 2:
                continue
            grid = self._build_grid(valid, rows, cols)
            if not grid or len(grid) < 2:
                continue
            # 质量过滤：表头行多列 + 平均每行非空格≥2（过滤散文/页眉误判）
            avg_filled = sum(1 for r in grid for c in r if c.strip()) / len(grid)
            header_filled = sum(1 for c in grid[0] if c.strip())
            if avg_filled < 1.8 or header_filled < 2:
                continue
            md = self._grid_to_markdown(grid)
            if not md:
                continue
            tables.append({
                "page": pno,
                "index": len(tables),
                "headers": grid[0],
                "rows": grid[1:],
                "markdown": md,
            })
        return tables

    @staticmethod
    def _cluster_cols(boxes: list, tol: float = 24) -> list:
        """列聚类：按 x 区间重叠度 + 中心接近度合并（比仅中心距离更鲁棒）"""
        cols = []  # [{center, min_x, max_x, items}] items 存 box 索引
        for idx, b in enumerate(boxes):
            x0, _, x1, _, _, _ = b
            cx = (x0 + x1) / 2
            best_i, best_overlap = None, 0
            for i, col in enumerate(cols):
                overlap = min(x1, col["max_x"]) - max(x0, col["min_x"])
                center_close = abs(cx - col["center"]) <= tol
                if overlap > best_overlap and (overlap > 0 or center_close):
                    best_overlap, best_i = overlap, i
            if best_i is None:
                cols.append({"center": cx, "min_x": x0, "max_x": x1, "items": [idx]})
            else:
                col = cols[best_i]
                col["items"].append(idx)
                n = len(col["items"])
                col["center"] = (col["center"] * (n - 1) + cx) / n
                col["min_x"] = min(col["min_x"], x0)
                col["max_x"] = max(col["max_x"], x1)
        cols.sort(key=lambda c: c["center"])
        return cols

    @staticmethod
    def _cluster_rows(boxes: list, tol: float = 14) -> list:
        """行聚类：按 y 区间重叠 + 中心接近合并"""
        rows = []  # [{center, min_y, max_y, items}] items 存 box 索引
        for idx, b in enumerate(boxes):
            _, y0, _, y1, _, _ = b
            cy = (y0 + y1) / 2
            best_i, best_overlap = None, 0
            for i, row in enumerate(rows):
                overlap = min(y1, row["max_y"]) - max(y0, row["min_y"])
                center_close = abs(cy - row["center"]) <= tol
                if overlap > best_overlap and (overlap > 0 or center_close):
                    best_overlap, best_i = overlap, i
            if best_i is None:
                rows.append({"center": cy, "min_y": y0, "max_y": y1, "items": [idx]})
            else:
                row = rows[best_i]
                row["items"].append(idx)
                n = len(row["items"])
                row["center"] = (row["center"] * (n - 1) + cy) / n
                row["min_y"] = min(row["min_y"], y0)
                row["max_y"] = max(row["max_y"], y1)
        rows.sort(key=lambda r: r["center"])
        return rows

    def _build_grid(self, boxes: list, rows: list, cols: list) -> list:
        """把每个 box 归入 (行, 列)，构建二维网格"""
        # 计算每个 box 所属列索引（按 x 中心最近）
        box_col = {}
        for i, b in enumerate(boxes):
            cx = (b[0] + b[2]) / 2
            best, best_d = 0, 1e9
            for ci, col in enumerate(cols):
                d = abs(col["center"] - cx)
                if d < best_d:
                    best_d, best = d, ci
            box_col[i] = best
        # 按行分组：每个 box → row_idx
        row_of = {}
        for ri, row in enumerate(rows):
            for i in row["items"]:
                row_of[i] = ri
        grid = {}  # (r, c) -> text
        for i, b in enumerate(boxes):
            r, c = row_of[i], box_col[i]
            prev = grid.get((r, c), "")
            grid[(r, c)] = (prev + " " + b[4]).strip() if prev else b[4]
        n_rows = max(row_of.values()) + 1
        n_cols = len(cols)
        # 稀疏度过滤：填充率过低则丢弃该页（表格常有空格，阈值放宽到 0.22）
        total_cells = n_rows * n_cols
        filled = len(grid)
        if total_cells == 0 or filled / total_cells < 0.22:
            return []
        out = []
        for r in range(n_rows):
            row = []
            for c in range(n_cols):
                row.append(grid.get((r, c), ""))
            # 全空行跳过
            if not any(cell.strip() for cell in row):
                continue
            out.append(row)
        return out

    @staticmethod
    def _grid_to_markdown(grid: list) -> str:
        if not grid:
            return ""
        n_cols = max(len(row) for row in grid)
        lines = ["| " + " | ".join(grid[0]) + " |",
                 "|" + "|".join([" --- " for _ in range(n_cols)]) + "|"]
        for row in grid[1:]:
            padded = row + [""] * (n_cols - len(row))
            lines.append("| " + " | ".join(padded[:n_cols]) + " |")
        return "\n".join(lines)

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
            ocr_text = self._ocr_extract(filepath)  # 全页OCR，支持断点续传
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

    def _ocr_extract(self, filepath: str, max_pages: Optional[int] = None) -> str:
        """
        扫描件 OCR（v2.5 重写）：
        1. PaddleOCR 优先 — 中文识别率高(~95%)，内置角度校正，配合 cv2 预处理
        2. Tesseract 回退 — 仅当 Paddle 不可用时
        共用同一 {stem}_full_ocr.txt 缓存，断点续传，不会重复追加。
        """
        # ======== 引擎 1: PaddleOCR（优先）========
        try:
            paddle_text = self._paddle_ocr(filepath, max_pages)
            if paddle_text and len(paddle_text.strip()) > 50:
                return paddle_text
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"PaddleOCR failed: {e}")

        # ======== 引擎 2: Tesseract（回退）========
        return self._tesseract_ocr(filepath, max_pages)

    # PaddleOCR 实例（懒加载，CPU 初始化慢只做一次）
    _paddle_ocr_instance = None

    def _paddle_ocr(self, filepath: str, max_pages: Optional[int] = None) -> str:
        """PaddleOCR 全页识别 — 共用 out_txt 缓存 + 断点续传"""
        import fitz

        if PDFParser._paddle_ocr_instance is None:
            from paddleocr import PaddleOCR
            import paddle
            # 自动检测 GPU：CUDA 编译且设备可用 → 用 GPU；否则 CPU
            use_gpu = False
            try:
                use_gpu = bool(paddle.device.is_compiled_with_cuda()) and \
                          int(paddle.device.cuda.device_count()) > 0
            except Exception:
                use_gpu = False
            PDFParser._paddle_ocr_instance = PaddleOCR(
                lang='ch',
                use_angle_cls=True,          # 角度校正
                device='gpu:0' if use_gpu else 'cpu',
                show_log=False,
                det_db_thresh=0.3,
                det_db_box_thresh=0.5,
                rec_batch_num=6,
            )
        ocr = PDFParser._paddle_ocr_instance

        out_txt, progress_json = self._ocr_output_paths(filepath)
        done_pages, failed_pages = self._load_ocr_progress(progress_json)

        doc = fitz.open(filepath)
        total_pages = len(doc)
        try:
            # 已完成 → 直接读回（幂等，绝不重写）
            if len(done_pages) >= total_pages and out_txt.exists():
                full = out_txt.read_text(encoding="utf-8")
                if full.strip():
                    return full
            # 无进度但 out_txt 已含全部页标记 → 也直接读回（旧引擎产出）
            if len(done_pages) == 0 and self._ocr_complete_pages(out_txt) >= total_pages:
                full = out_txt.read_text(encoding="utf-8")
                if full.strip():
                    return full

            # 未完成 → 从零/断点续写（覆盖或追加，取决于进度是否为空）
            mode = "w" if len(done_pages) == 0 else "a"
            pages_text = []
            with open(out_txt, mode, encoding="utf-8") as fout:
                for page_num, page in enumerate(doc):
                    if max_pages is not None and page_num >= max_pages:
                        break
                    if page_num in done_pages:
                        continue
                    try:
                        img = self._preprocess_page(page)
                        result = ocr.ocr(img, cls=True)
                        if result and result[0]:
                            lines = [line[1][0] for line in result[0] if line and len(line) > 1]
                            text = self._clean_ocr_text("\n".join(lines))
                            if text.strip():
                                page_block = f"[Page {page_num + 1}]\n{text.strip()}"
                                pages_text.append(page_block)
                                fout.write(page_block + "\n\n")
                                fout.flush()
                        done_pages.add(page_num)
                    except Exception as e:
                        failed_pages[page_num] = failed_pages.get(page_num, 0) + 1
                        if failed_pages[page_num] >= 2:
                            done_pages.add(page_num)
                            logger.warning(f"Paddle OCR page {page_num + 1} failed twice, skip: {e}")
                    self._save_ocr_progress(progress_json, done_pages, failed_pages, total_pages)
        finally:
            doc.close()

        if pages_text or out_txt.exists():
            full_text = out_txt.read_text(encoding="utf-8") if out_txt.exists() else "\n\n".join(pages_text)
            logger.info(f"PaddleOCR: {len(done_pages)}/{total_pages} pages -> {out_txt.name}")
            return full_text
        return ""

    def _preprocess_page(self, page, scale: float = 3.0):
        """cv2 预处理流水线：高DPI渲染 → 灰度 → 去噪 → CLAHE增强 → 纠偏 → 放大小字"""
        import fitz
        import numpy as np
        import cv2
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n >= 3:
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        else:
            gray = img
        # 去噪（保留文字边缘）
        gray = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
        # 对比度增强（CLAHE，不用暴力二值化，Paddle 检测器对自然图像更鲁棒）
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        # 纠偏
        gray = self._deskew(gray)
        # 小字放大
        h, w = gray.shape[:2]
        if w < 1500:
            gray = cv2.resize(gray, (int(w * 2), int(h * 2)), interpolation=cv2.INTER_CUBIC)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    def _deskew(self, gray):
        """文本块倾角估计 + 纠偏（|θ|在 0.5°~15° 才旋转，防误转）"""
        try:
            import cv2
            thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
            coords = cv2.findNonZero(thresh)
            if coords is None or len(coords) < 100:
                return gray
            angle = cv2.minAreaRect(coords)[-1]
            if angle < -45:
                angle = -(90 + angle)
            else:
                angle = -angle
            if abs(angle) < 0.5 or abs(angle) > 15:
                return gray
            h, w = gray.shape[:2]
            M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
            return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        except Exception:
            return gray

    def _ocr_complete_pages(self, out_txt) -> int:
        """统计 out_txt 中已出现的不同页码数"""
        if not out_txt.exists():
            return 0
        try:
            text = out_txt.read_text(encoding="utf-8")
            return len(set(int(m) for m in re.findall(r'\[Page\s*(\d+)\]', text)))
        except Exception:
            return 0

    def _tesseract_ocr(self, filepath: str, max_pages: Optional[int] = None) -> str:
        """Tesseract OCR 快速引擎 — 全页OCR + 断点续传 + 进度保存"""
        import fitz
        from PIL import Image
        import io as io_mod
        try:
            import pytesseract
            tesseract_path = self._find_tesseract()
            if tesseract_path != "tesseract":
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
            pytesseract.get_tesseract_version()

            out_txt, progress_json = self._ocr_output_paths(filepath)
            done_pages, failed_pages = self._load_ocr_progress(progress_json)

            doc = fitz.open(filepath)
            total_pages = len(doc)
            try:
                # 进度完整 → 直接读回结果（幂等，绝不重写）
                if len(done_pages) >= total_pages and out_txt.exists():
                    full = out_txt.read_text(encoding="utf-8")
                    if full.strip():
                        logger.info(f"OCR 已完成: {out_txt.name} ({total_pages}页)")
                        return full
                # 无进度但 out_txt 已含全部页标记 → 直接读回（防 [Page N] 重复追加）
                if len(done_pages) == 0 and self._ocr_complete_pages(out_txt) >= total_pages:
                    full = out_txt.read_text(encoding="utf-8")
                    if full.strip():
                        logger.info(f"OCR 已完成(读缓存): {out_txt.name} ({total_pages}页)")
                        return full

                # 未完成 → 覆盖(全新)或追加(断点续传)
                mode = "w" if len(done_pages) == 0 else "a"
                pages_text = []
                with open(out_txt, mode, encoding="utf-8") as fout:
                    for page_num, page in enumerate(doc):
                        if max_pages is not None and page_num >= max_pages:
                            break
                        if page_num in done_pages:
                            continue
                        try:
                            mat = fitz.Matrix(2.5, 2.5)  # 180 DPI
                            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
                            img = Image.open(io_mod.BytesIO(pix.tobytes("png")))
                            # 轻量预处理：CLAHE 增强 + 适度二值化
                            img = self._tesseract_preprocess(img)
                            text = pytesseract.image_to_string(
                                img, lang='chi_sim+eng', config='--psm 6'
                            )
                            text = self._clean_ocr_text(text)
                            if text.strip():
                                page_block = f"[Page {page_num + 1}]\n{text.strip()}"
                                pages_text.append(page_block)
                                fout.write(page_block + "\n\n")
                                fout.flush()
                            done_pages.add(page_num)
                        except Exception as e:
                            failed_pages[page_num] = failed_pages.get(page_num, 0) + 1
                            if failed_pages[page_num] >= 2:
                                done_pages.add(page_num)  # 连续失败标记完成，避免死循环
                                logger.warning(f"OCR page {page_num + 1} failed twice, skip: {e}")
                        # 每页保存进度（断点续传；完成后保留作"已完成"标记，防重复）
                        self._save_ocr_progress(progress_json, done_pages, failed_pages, total_pages)
            finally:
                doc.close()

            if pages_text or out_txt.exists():
                full_text = out_txt.read_text(encoding="utf-8") if out_txt.exists() else "\n\n".join(pages_text)
                logger.info(f"Tesseract: {len(done_pages)}/{total_pages} pages -> {out_txt.name}")
                return full_text
        except Exception as e:
            logger.warning(f"Tesseract failed: {e}")

        return ""

    def _tesseract_preprocess(self, img):
        """Tesseract 用预处理：灰度 → CLAHE → 温和二值化"""
        try:
            import cv2
            import numpy as np
            arr = np.array(img.convert("L"))
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            arr = clahe.apply(arr)
            # 温和阈值：保留弱文字，避免暴力二值化丢字
            _, arr = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            from PIL import Image as _PIL
            return _PIL.fromarray(arr)
        except Exception:
            return img

    # ============================================================
    # OCR 断点续传辅助
    # ============================================================
    def _ocr_output_paths(self, filepath: str) -> Tuple[Path, Path]:
        """OCR 结果文件与进度文件路径: uploads/{stem}_full_ocr.txt / {stem}_ocr_progress.json"""
        stem = Path(filepath).stem
        return (self.upload_dir / f"{stem}_full_ocr.txt",
                self.upload_dir / f"{stem}_ocr_progress.json")

    def _load_ocr_progress(self, progress_json: Path) -> Tuple[set, dict]:
        """加载OCR进度：已完成页集合 + 失败页计数"""
        if progress_json.exists():
            try:
                prog = json.loads(progress_json.read_text(encoding="utf-8"))
                return set(prog.get("done_pages", [])), prog.get("failed_pages", {})
            except Exception:
                pass
        return set(), {}

    def _save_ocr_progress(self, progress_json: Path, done_pages: set,
                           failed_pages: dict, total_pages: int):
        progress_json.write_text(
            json.dumps({"total_pages": total_pages, "done_pages": sorted(done_pages),
                        "failed_pages": failed_pages}, ensure_ascii=False),
            encoding="utf-8")

    def _clean_ocr_text(self, text: str) -> str:
        """清洗OCR文本：去字间空格、装饰点线、孤立符号等常见噪声"""
        if not text:
            return text
        text = text.replace('\u00a0', ' ')  # 不间断空格统一为普通空格
        text = re.sub(r'(?<=\d)\s*\.\s*(?=\d)', '.', text)   # "0. 5" -> "0.5"
        text = re.sub(r'(?<=\d)\s+(?=\d)', '', text)          # "3 2" -> "32"
        text = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff，。；：、！？）】》」])', '', text)
        text = re.sub(r'(?<=[（【「《])\s+(?=[\u4e00-\u9fff])', '', text)
        lines = []
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if re.fullmatch(r'[\.\-\—_·•*#\s]+', s):  # 纯装饰点线行
                continue
            if len(s) <= 2 and not re.search(r'[\u4e00-\u9fff]', s):  # 孤立符号
                continue
            lines.append(s)
        text = "\n".join(lines)
        # ---- 纠错字典（v2.5）----
        for bad, good in OCR_SYMBOL_FIXES:
            text = text.replace(bad, good)
        for bad, good in OCR_TERM_FIXES:
            if bad in text:
                text = text.replace(bad, good)
        for bad, good in OCR_CHAR_FIXES:
            # 单字级：仅当整词独立出现（前后非中文）才替换，防误伤
            if len(bad) == 1:
                text = re.sub(
                    rf'(?<=[^一-鿿]){re.escape(bad)}(?=[^一-鿿])',
                    good, text)
        return text

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
