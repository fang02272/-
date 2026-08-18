"""
知识库持久化存储 v3.1 — 完整学习上传PDF的知识体系
==================================================
每本上传的PDF都经过：目录提取 → 章节拆分 → 摘要生成 → 关键词映射 → 注册为知识源
新知识源与原书同等对待，参与全部项目流程
v3.1 增强:
- 关键词提取直接使用 welding_knowledge_base 的 KEYWORD_CATEGORY_MAP (1500+术语) + 手册关键词
- 修复 relearn_keywords 中 '_generic_words' in dir() 恒为 False 的过滤失效 bug
- 学习去重：同名PDF(含/不含.pdf后缀)重新学习时替换旧源，保留人工标注文件
- OCR容错：章节标题归一化(第 1 章→第1章)，行首标题优先匹配
- 关键词提取增加英文型号(焊材牌号/钢号)提取
"""

import json
import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import List, Dict, Optional, Set

logger = logging.getLogger("knowledge_store")

# 导入焊接领域知识库，用于构建统一术语词库
from app.welding_knowledge_base import (
    KEYWORD_CATEGORY_MAP,
    PRACTICAL_HANDBOOK_KEYWORDS,
)


class KnowledgeStore:
    """持久化知识库，管理原书 + 所有已学习的上传PDF"""

    # 焊接领域通用词 — 高频但无辨识度，关键词入库前剔除
    # [调优] 仅保留 OCR 碎片词和无意义填充词（非真实焊接术语）
    #        真实焊接术语（气孔/预热/氧化等）改为在 search_across_sources 中用 IDF 降权
    _GENERIC_WORDS = {
        "焊接", "工艺", "方法", "参数", "材料", "结构", "性能",
        "特点", "应用", "过程", "技术", "原理", "分析", "研究", "实验",
        "焊接工艺", "实用焊接", "手册",  # OCR页眉噪声词
        # [调优] OCR 碎片/截断词（无意义，非完整术语）
        "中间层材", "等离子喷", "缝金属中", "的钢加热", "保温一段",
        "有很强的", "化焊接", "材金属之", "与基体材", "间的结合",
        "度的影响", "的影响", "一层",
        # [调优] 焊接语境下的无意义填充词
        "一般来说", "这种方法", "可以看到", "也可以采用", "主要用于",
        "由于中间", "作为中间", "备注", "原因", "名称", "配方",
    }

    def __init__(self, store_dir: str = "saved_knowledge"):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.store_dir / "registry.json"
        self.registry = self._load_registry()

    def _load_registry(self) -> dict:
        if self.registry_path.exists():
            try:
                return json.loads(self.registry_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"sources": []}

    def _save_registry(self):
        self.registry_path.write_text(
            json.dumps(self.registry, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    # ============================================================
    # 核心：完整学习一本新PDF
    # ============================================================
    def learn_book(self, filename: str, full_text: str, tables: list,
                   page_count: int, images: list) -> str:
        """
        完整学习一本新工艺书/手册：
        1. 提取目录结构
        2. 按章节拆分全文
        3. 提取关键词
        4. 生成每个章节的摘要
        5. 持久化存储
        返回 source_id
        """
        # 学习去重：若已存在同名知识源（含/不含.pdf后缀），复用其ID并替换旧数据
        # （保留人工标注文件 structure/electrode_params/process_params）
        norm_name = filename.replace('.pdf', '').strip()
        existing_id = None
        for s in self.registry["sources"]:
            if s["id"] == norm_name or \
                    s.get("filename", "").replace('.pdf', '').strip() == norm_name:
                existing_id = s["id"]
                break
        if existing_id:
            source_dir = self.store_dir / existing_id
            keep_files = []
            for keep_name in ("structure.json", "electrode_params.json", "process_params.json"):
                p = source_dir / keep_name
                if p.exists():
                    keep_files.append(p)
            self.registry["sources"] = [s for s in self.registry["sources"] if s["id"] != existing_id]
            # 清理旧数据文件（保留人工标注）
            if source_dir.exists():
                for f in source_dir.iterdir():
                    if f.is_file() and f not in keep_files:
                        try:
                            f.unlink()
                        except Exception:
                            pass
            source_id = existing_id
        else:
            source_id = self._make_id(filename)

        # Step 1: 提取完整目录（优先扫描"目录"页）
        toc = self._extract_toc(full_text)

        # Step 2: 按目录拆分章节
        chapters = self._split_chapters(full_text, toc)

        # Step 2.5: 表格内容并入章节正文（提升检索命中率 + 关键词解析率）
        chapters = self._merge_tables_into_chapters(chapters, tables)

        # Step 3: 为每个章节提取关键词和摘要
        # 通用词已在类级别定义（见 _GENERIC_WORDS）
        enriched_chapters = []
        all_keywords = set()
        for ch in chapters:
            kw = self._extract_keywords(ch.get("content", ""))
            kw_filtered = {k for k in kw if k not in self._GENERIC_WORDS}
            summary = self._generate_summary(ch.get("content", ""), ch.get("title", ""))
            all_keywords.update(kw_filtered)
            enriched_chapters.append({
                "title": ch["title"],
                "page_hint": ch.get("page_hint", ""),
                "content": ch.get("content", ""),
                "content_length": len(ch.get("content", "")),
                # [调优] 50→30 + 按章节内出现频次降序（讨论最多的术语最相关）
                "keywords": sorted(kw_filtered, key=lambda k: -ch.get("content", "").count(k))[:30],
                "summary": summary,
            })

        # Step 3.5: 表格关键词 — 表格含大量工艺参数/牌号词，并入全书关键词
        # （参考 relearn_tables 思路：恢复表格 → 关键词解析率提升）
        table_text = "\n".join(
            t.get("markdown", "") for t in tables if isinstance(t, dict) and "markdown" in t
        )
        if table_text.strip():
            table_kws = self._extract_keywords(table_text) - self._GENERIC_WORDS
            all_keywords.update(table_kws)

        # Step 4: 提取全书的焊接参数数据
        data_points = self._extract_welding_params(full_text)

        # Step 5: 持久化
        source_dir = self.store_dir / source_id
        source_dir.mkdir(parents=True, exist_ok=True)

        (source_dir / "full_text.txt").write_text(full_text, encoding="utf-8")
        (source_dir / "toc.json").write_text(
            json.dumps(toc, ensure_ascii=False, indent=2), encoding="utf-8")
        (source_dir / "chapters.json").write_text(
            json.dumps(enriched_chapters, ensure_ascii=False, indent=2), encoding="utf-8")
        (source_dir / "keywords.json").write_text(
            json.dumps(list(all_keywords), ensure_ascii=False, indent=2), encoding="utf-8")
        (source_dir / "data_points.json").write_text(
            json.dumps(data_points, ensure_ascii=False, indent=2), encoding="utf-8")

        # 保存提取的表格
        for ti, table in enumerate(tables):
            if isinstance(table, dict) and "markdown" in table:
                (source_dir / f"table_{ti+1}.md").write_text(
                    table["markdown"], encoding="utf-8")

        # Step 6: 注册
        entry = {
            "id": source_id,
            "filename": filename,
            "page_count": page_count,
            "text_length": len(full_text),
            "chapter_count": len(chapters),
            "keyword_count": len(all_keywords),
            "table_count": len(tables),
            "image_count": len(images),
            "data_point_count": len(data_points),
            "chapters": [{"title": c["title"], "page_hint": c.get("page_hint", ""),
                          "keyword_count": len(c["keywords"]),
                          "content_length": c.get("content_length", 0),
                          "summary": c.get("summary", "")[:200],
                          "keywords": c.get("keywords", [])[:10]}
                         for c in enriched_chapters],
            "all_keywords": list(all_keywords),
        }
        self.registry["sources"].append(entry)
        self._save_registry()

        return source_id

    def relearn_keywords(self, source_id: str) -> dict:
        """用最新代码重新提取已有书籍的关键词（无需原始 PDF）
        读取已存储的 full_text.txt，重新走关键词提取流程，更新 keywords.json 和 chapters.json
        返回 {old_count, new_count, chapters_updated}
        """
        source_dir = self.store_dir / source_id
        full_text_path = source_dir / "full_text.txt"
        if not full_text_path.exists():
            raise FileNotFoundError(f"缺少 full_text.txt: {full_text_path}")

        full_text = full_text_path.read_text(encoding="utf-8")

        # 重新提取目录和章节（章节拆分逻辑不变，但关键词需要重建）
        chapters = self.get_chapters(source_id)
        if not chapters:
            # 如果 chapters.json 损坏，重新拆分
            toc = self._extract_toc(full_text)
            chapters = self._split_chapters(full_text, toc)

        # 用新代码重新提取关键词
        enriched_chapters = []
        all_keywords = set()
        for ch in chapters:
            content = ch.get("content", "")
            kw = self._extract_keywords(content)
            kw_filtered = {k for k in kw if k not in self._GENERIC_WORDS}
            all_keywords.update(kw_filtered)
            summary = ch.get("summary", "") or self._generate_summary(content, ch.get("title", ""))
            enriched_chapters.append({
                "title": ch["title"],
                "page_hint": ch.get("page_hint", ""),
                "content": content,
                "content_length": len(content),
                # [调优] 50→30 + 按章节内出现频次降序（讨论最多的术语最相关）
                "keywords": sorted(kw_filtered, key=lambda k: -content.count(k))[:30],
                "summary": summary,
            })

        # 保存
        (source_dir / "chapters.json").write_text(
            json.dumps(enriched_chapters, ensure_ascii=False, indent=2), encoding="utf-8")
        (source_dir / "keywords.json").write_text(
            json.dumps(list(all_keywords), ensure_ascii=False, indent=2), encoding="utf-8")

        # 更新注册信息
        old_count = 0
        for s in self.registry["sources"]:
            if s["id"] == source_id:
                old_count = s.get("keyword_count", 0)
                s["keyword_count"] = len(all_keywords)
                s["all_keywords"] = list(all_keywords)
                s["chapters"] = [
                    {"title": c["title"], "page_hint": c.get("page_hint", ""),
                     "keyword_count": len(c["keywords"]),
                     "content_length": c.get("content_length", 0),
                     "summary": c.get("summary", "")[:200],
                     "keywords": c.get("keywords", [])[:10]}
                    for c in enriched_chapters
                ]
                break
        self._save_registry()

        return {"old_count": old_count, "new_count": len(all_keywords),
                "chapters_updated": len(enriched_chapters)}

    def _make_id(self, filename: str) -> str:
        """生成唯一知识源ID"""
        sid = re.sub(r'[^\w一-鿿\-]', '_', filename.replace('.pdf', ''))[:40]
        base = sid
        counter = 1
        while any(s["id"] == sid for s in self.registry["sources"]):
            sid = f"{base}_{counter}"; counter += 1
        return sid

    # ============================================================
    # 目录提取
    # ============================================================
    # 章节标题OCR容错归一化："第 1 章" / "第1 章" → "第1章"
    _TITLE_NORM = re.compile(r'(第)\s*([一二三四五六七八九十百\d]+)\s*([篇章节])')

    # 中文数字 → 阿拉伯数字（用于章号比较/去重）
    _CN_DIGITS = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6,
                  '七': 7, '八': 8, '九': 9, '十': 10, '百': 100}

    # 章节标题OCR噪声纠正（仅用于标题清洗，避免正文误伤）
    _OCR_TITLE_FIXES = [('锈铁', '铸铁'), ('馈', '铝'), ('切币', '切割'),
                        ('烛接', '焊接'), ('乙烽焰', '乙炔焰')]

    @classmethod
    def _num_cn(cls, n: int) -> str:
        """阿拉伯数字 → 中文数字（1→一, 10→十, 13→十三）"""
        digits = '一二三四五六七八九'
        if n <= 0:
            return str(n)
        if n < 10:
            return digits[n - 1]
        if n == 10:
            return '十'
        if n < 20:
            return '十' + digits[n - 11]
        t, o = divmod(n, 10)
        return digits[t - 1] + '十' + (digits[o - 1] if o else '')

    @staticmethod
    def _normalize_titles(text: str) -> str:
        """OCR标题归一化：'第 1 章' → '第1章'（仅处理篇章级别标题）"""
        return KnowledgeStore._TITLE_NORM.sub(r'\1\2\3', text)

    @classmethod
    def _cn_num(cls, s: str) -> int:
        """中文数字/阿拉伯数字字符串 → int（一→1, 十三→13, 23→23）"""
        s = s.strip()
        if s.isdigit():
            return int(s)
        if '十' in s:
            a, b = s.split('十', 1)
            tens = (cls._CN_DIGITS.get(a, 0) or 1) * 10
            ones = cls._CN_DIGITS.get(b, 0)
            return tens + ones
        return cls._CN_DIGITS.get(s, 0)

    def _extract_toc(self, text: str) -> list:
        """
        从全文提取目录结构。
        策略：先扫描全文找到所有「第X章」出现位置，去重排序后作为章节列表
        """
        text = self._normalize_titles(text)
        toc_entries = []

        # 策略1：定位 TOC 标记区域，提取格式化的章节目录
        toc_marker = re.search(
            r'(?:^|\n)\s*(?:目\s*录|CONTENTS|目\s*次)\s*(?:\n|$)', text, re.IGNORECASE
        )
        if toc_marker:
            toc_section = text[toc_marker.start():toc_marker.start() + 20000]
            # 匹配目录中的章节行：「第X章 标题 页码」 或 「X.X 标题 页码」
            toc_lines = re.findall(
                r'(?:第[一二三四五六七八九十\d]+[篇章节][^\n]{0,80})|(?:\d+\.\d+(?:\.\d+)?\s+[^\n]{2,60}\d{1,4})',
                toc_section
            )
            for line in toc_lines:
                line = line.strip()
                # 去掉末尾页码
                title = re.sub(r'\s*\d{1,4}\s*$', '', line).strip()
                title = re.sub(r'\.{3,}', '', title).strip()
                if 2 < len(title) < 120 and title not in {t["title"] for t in toc_entries}:
                    toc_entries.append({"title": title, "page_hint": ""})

        # 策略2：全文扫描 — 找到每个「第X章」的首次出现（行首优先，正文开头）
        if len(toc_entries) < 3:
            # OCR中章节标题通常独立成行，行首匹配可过滤"见第3章"等正文引用
            chapter_heads = list(re.finditer(
                r'(?:^|\n)\s*第[一二三四五六七八九十\d]+章\s*[^\d\n]{2,60}',
                text
            ))
            if len(chapter_heads) < 3:
                chapter_heads = re.finditer(
                    r'第[一二三四五六七八九十\d]+章\s*[^\d\n]{2,60}',
                    text
                )
            seen_titles = set()
            seen_nums = set()  # 按章号去重：目录+页眉重复只保留首个
            for m in chapter_heads:
                title = m.group(0).strip()
                # 过滤页眉格式（太短的、纯数字后缀的）
                if len(title) < 8:
                    continue
                # 同一章号（如页眉每页重复、目录页与正文页重复）只保留第一条
                num_m = re.search(r'第([一二三四五六七八九十百\d]+)章', title)
                num = self._cn_num(num_m.group(1)) if num_m else 0
                if num and num in seen_nums:
                    continue
                if num:
                    seen_nums.add(num)
                normalized = re.sub(r'\d+$', '', title).strip()
                if normalized not in seen_titles:
                    seen_titles.add(normalized)
                    toc_entries.append({"title": title[:100], "page_hint": ""})

        # 策略3：找篇级标题
        part_heads = re.findall(r'第[一二三四五六七八九十\d]+篇[^\n]{0,60}', text)
        for p in part_heads:
            p = p.strip()
            if 4 < len(p) < 80 and p not in {t["title"] for t in toc_entries}:
                toc_entries.insert(0, {"title": p, "page_hint": ""})

        return toc_entries[:60]

    # ============================================================
    # 表格并入章节（提升检索命中 + 关键词解析率）
    # ============================================================
    def _merge_tables_into_chapters(self, chapters: list, tables: list) -> list:
        """把表格 markdown 并入所属章节正文：
        - 扫描版表格带 page 号 → 插入到章节内容中 [Page N] 标记之后
        - 无页码匹配 → 追加到末尾章节
        效果：章节内容含表格数值 → 关键词提取命中表格词、检索/工艺卡片可引用。"""
        if not tables:
            return chapters
        for ch in chapters:
            ch.setdefault("content", "")
        merged = 0
        for table in tables:
            if not isinstance(table, dict) or not table.get("markdown"):
                continue
            md = "\n\n【表格】\n" + table["markdown"] + "\n"
            page = table.get("page")
            marker = f"[Page {page}]" if page else ""
            target = None
            if marker:
                for ch in chapters:
                    if marker in ch.get("content", ""):
                        target = ch
                        break
            if target:
                idx = target["content"].find(marker)
                nl = target["content"].find("\n", idx)
                ins = (nl + 1) if nl > 0 else (idx + len(marker))
                target["content"] = target["content"][:ins] + md + target["content"][ins:]
                merged += 1
            else:
                # 无页码 → 内容匹配：按表头/单元格术语与章节内容重合度并入最相关章节
                best = self._best_chapter_for_table(table, chapters)
                if best is not None:
                    best["content"] = best["content"].rstrip() + "\n" + md
                    merged += 1
                elif chapters:
                    chapters[-1]["content"] = chapters[-1]["content"].rstrip() + "\n" + md
                    merged += 1
        if merged:
            logger.info(f"表格并入章节: {merged}/{len(tables)}")
        return chapters

    @staticmethod
    def _best_chapter_for_table(table: dict, chapters: list):
        """按表格术语与章节内容重合度，找最相关章节（无页码时的内容匹配兜底）"""
        md = table.get("markdown", "")
        # 提取表格中的中文术语（2-6字）
        terms = set(re.findall(r'[一-鿿]{2,6}', md))
        if not terms:
            return None
        # 取频率最高的20个术语作为特征
        from collections import Counter
        term_freq = Counter(re.findall(r'[一-鿿]{2,6}', md))
        features = [t for t, _ in term_freq.most_common(20)]
        best_ch, best_score = None, 0
        for ch in chapters:
            content = ch.get("content", "")
            if not content:
                continue
            score = sum(1 for t in features if t in content)
            if score > best_score:
                best_score, best_ch = score, ch
        if best_ch is not None and best_score >= 2:
            return best_ch
        return None

    # ============================================================
    # 章节拆分
    # ============================================================
    # ============================================================
    # OCR扫描版专用章节拆分（按页眉分章）
    # ============================================================
    def _split_chapters_ocr(self, text: str) -> list:
        """
        OCR扫描版专用章节拆分：
        扫描版手册每页顶部有"第X章"页眉（每页重复），目录页一页含多个章号。
        利用 [Page N] 标记分页：
        - 每页取行首"第X章"标题作为页眉章号（行首优先，非行首回退）
        - 一页出现多个不同章号 → 目录页，跳过
        - 页眉章号递增变化处 → 新章起点
        标题取该章范围内中文字符最多的"第X章"行首标题（消除OCR噪声变体）
        """
        parts = re.split(r'\[Page\s*(\d+)\]', text)
        # parts[0] 为前导文本; 之后交替出现 [页码, 页内容]
        pages = []
        for i in range(1, len(parts) - 1, 2):
            pages.append((int(parts[i]), parts[i + 1]))
        if len(pages) < 10:
            return []

        # 目录页标题映射：行首"第X章…页码"的目录行（格式规整，无正文粘连）
        # 注意：OCR中引号可能是左/右双引号；页码前至少2空格防正文行污染
        toc_map = {}
        for m in re.finditer(
            r'(?:^|\n)\s*(?:目\s*录)?\s*第([一二三四五六七八九十百\d]+)章\s*["“”「」『』]?\s*'
            r'([^"“”「」『』\n]{2,16}?)\s{2,}[\dA-Za-z.\s]{1,24}\s*$',
            text, re.MULTILINE
        ):
            num = self._cn_num(m.group(1))
            if num and num not in toc_map:
                title = m.group(2).strip().rstrip('。，、. ')
                if 2 <= len(title) <= 16:
                    toc_map[num] = title

        def _page_head(page_text: str):
            """该页行首第一个'第X章'标题（页眉）；无行首匹配时整页回退"""
            m = re.search(r'(?:^|\n)\s*(第[一二三四五六七八九十百\d]+章[^\n]{0,60})', page_text)
            if m:
                return m.group(1).strip()
            m = re.search(r'第[一二三四五六七八九十百\d]+章[^\n]{0,60}', page_text)
            return m.group(0).strip() if m else None

        page_nums = []   # 每页章号；-1=目录页，None=无页眉
        page_titles = [] # 每页页眉标题
        prev_num = None  # 前一有效章号（用于非行首回退的归属判定）
        for _, content in pages:
            num = None
            title = None
            # 行首页眉（优先）：过滤以空格+数字结尾的目录行（如"第二章… 36"）
            heads = re.findall(
                r'(?:^|\n)\s*(第[一二三四五六七八九十百\d]+章[^\n]{0,60})', content
            )
            heads = [h.strip() for h in heads
                     if not re.search(r'\s\d{1,4}\s*$', h)]
            if heads:
                nums = set()
                for h in heads[:20]:
                    mm = re.search(r'第([一二三四五六七八九十百\d]+)章', h)
                    if mm:
                        nums.add(self._cn_num(mm.group(1)))
                if len(nums) == 1:
                    num = nums.pop()
                    title = heads[0]
                elif len(nums) > 1:
                    num = -1  # 目录页：一页出现多个章号
            else:
                # 非行首回退：只补全当前章的归属，绝不创建新边界
                # （避免前言"第五章…"、正文引用等污染章号序列）
                m = re.search(r'第([一二三四五六七八九十百\d]+)章[^\n]{0,60}', content)
                if m and prev_num is not None and self._cn_num(m.group(1)) == prev_num:
                    num = prev_num
                    title = m.group(0).strip()
            page_nums.append(num)
            page_titles.append(title)
            if num is not None and num > 0:
                prev_num = num

        # 章节边界：章号递增变化处（重复页眉并入当前章，倒退视为OCR噪声）
        bounds = []  # [章号, 起始页索引, 页眉标题]
        for i, num in enumerate(page_nums):
            if num is None or num <= 0:
                continue
            # 正文锚定：正文从第一章开始，之前目录/前言中的大章号全部作废
            if num == 1 and bounds and bounds[-1][0] > 1:
                bounds = []
            if not bounds or num > bounds[-1][0]:
                bounds.append([num, i, page_titles[i] or ""])
        if not bounds:
            return []

        chapters = []
        for j, (num, start, title0) in enumerate(bounds):
            end = bounds[j + 1][1] if j + 1 < len(bounds) else len(pages)
            content = "\n".join(
                f"[Page {pages[i][0]}] {pages[i][1]}" for i in range(start, end)
            ).strip()
            if len(content) < 300:
                continue
            # 标题优化：目录标题优先；否则页眉清洗（去正文粘连），多候选取最短
            def _clip(s):
                """引号未闭合/超长候选：按章节终止词截断正文粘连（只看前14字）"""
                head = s[:14]
                ends = [m.end() for m in
                        re.finditer(r'的?(?:焊接|堆焊|工艺|方法|切割|电弧焊)', head)]
                return head[:max(ends)] if ends else s

            def _clean_title(h):
                m = re.match(r'第([一二三四五六七八九十百\d]+)章', h)
                if not m:
                    return None
                num_s = m.group(1)
                rest = h[m.end():].strip(' \t')
                # 引号/书名号包裹内容优先（左/右引号都可能，闭合或未闭合）
                q = re.match(r'["“”「『《]([^"“”「」『』》]{2,30}?)(?:["”」』》]|$)', rest)
                s = ''
                if q:
                    s = q.group(1).strip()
                    # 未闭合引号=页眉与正文粘连，用章节终止词截断
                    if not re.search(r'["”」』》]\s*$', q.group(0)):
                        s = _clip(s)
                else:
                    cn = re.match(r'[一-鿿]{2,14}', rest.lstrip('"“”「『《、，：: '))
                    s = cn.group(0) if cn else ''
                    # 无引号且超长（>10字）几乎必为粘连正文，截断
                    if len(s) > 10:
                        s = _clip(s)
                if not s:
                    return None
                # 去尾部"第X节"/重复"第X章"（正文小节标题或页眉噪声粘连）
                s = re.sub(r'第[一二三四五六七八九十\d]+[节章]$', '', s)
                s = re.sub(r'(?:第)?[一二三四五六七八九十]+章$', '', s)
                # 去尾部正文粘连字（正确标题多以"接/艺/法/知"等收尾，不受影响）
                s = s.rstrip('了而在的和与或、，。')
                if len(s) < 2:
                    return None
                # OCR噪声纠正（仅标题级）
                for old, new in self._OCR_TITLE_FIXES:
                    s = s.replace(old, new)
                return '第' + num_s + '章' + s

            cands = [title0] if title0 else []
            cands += re.findall(
                r'(?:^|\n)\s*(第[一二三四五六七八九十百\d]+章[^\n]{0,60})', content[:4000]
            )
            cleaned = []
            for c in cands:
                if not c:
                    continue
                t = _clean_title(c)
                if t and t not in cleaned:
                    cleaned.append(t)
            toc_title = toc_map.get(num)
            if toc_title:
                best = f"第{self._num_cn(num)}章{toc_title}"
            elif cleaned:
                # 最短优先：粘连正文的候选更长，最短者最接近真实页眉标题
                best = min(cleaned, key=len)
            else:
                best = title0 or f"第{num}章"
            # OCR噪声纠正（toc_map/回退标题同样适用）
            for old, new in self._OCR_TITLE_FIXES:
                best = best.replace(old, new)
            chapters.append({
                "title": best[:40],
                "content": content,
                "page_hint": f"第{pages[start][0]}页",
            })
        return chapters

    def _split_chapters(self, text: str, toc: list) -> list:
        """
        用标准章节标题在正文中查找位置，按位置拆分。
        只保留每个唯一章节标题的首次出现（过滤页眉重复）。
        OCR扫描版（带[Page N]页标记）优先使用按页眉分章的专用逻辑。
        """
        # OCR容错：归一化"第 1 章" → "第1章"（正文与标题统一处理）
        text = self._normalize_titles(text)

        # 扫描版增强：页眉分章比目录/标题匹配更抗OCR噪声
        if text.count('[Page ') >= 50:
            ocr_chapters = self._split_chapters_ocr(text)
            if len(ocr_chapters) >= 3:
                return ocr_chapters

        # 从TOC提取标准章节标题
        chapter_titles = []
        for entry in toc:
            t = entry["title"]
            # 只保留"第X章"格式的标题
            if re.match(r'第[一二三四五六七八九十\d]+章', t):
                # 取干净的章节名（去页码、去后缀）
                clean = re.split(r'\s+\d{2,4}\s*$', t)[0].strip()
                if clean not in chapter_titles:
                    chapter_titles.append(clean)

        # 如果TOC不够，从文本直接找唯一章节标题
        if len(chapter_titles) < 3:
            # 先尝试"第X章"格式（行首优先）
            raw_titles = re.findall(
                r'(?:^|\n)\s*第[一二三四五六七八九十\d]+章[^\d\n]{1,40}',
                text
            )
            if len(raw_titles) < 3:
                raw_titles = re.findall(
                    r'第[一二三四五六七八九十\d]+章[^\d\n]{1,40}',
                    text
                )
            seen = set()
            for rt in raw_titles:
                rt = rt.strip()
                if len(rt) < 8: continue
                if rt not in seen:
                    seen.add(rt)
                    chapter_titles.append(rt)

            # 如果"第X章"也找不到，用数字编号格式（如"1. 焊接作用效应"）
            if len(chapter_titles) < 3:
                numbered = re.findall(
                    r'^\s*(\d+\.?\s+\S[^\n]{2,60})', text, re.MULTILINE
                )
                for nt in numbered:
                    nt = nt.strip()
                    if len(nt) > 4 and nt not in chapter_titles:
                        chapter_titles.append(nt)
                chapter_titles = chapter_titles[:25]  # 限制数量

        # 在正文中定位每个章节（取首次出现，过滤页眉）
        positions = []
        for ct in chapter_titles:
            # 过滤伪标题（如"第3章中详细讨论"这种引用格式）
            if re.search(r'第[一二三四五六七八九十\d]+章(中|的|是|有|可|将)', ct):
                continue
            if len(ct) < 5:  # "第X章XXX" 至少5字符
                continue
            pos = text.find(ct)
            if pos < 0:
                pos = text.find(ct[:8])
            if pos >= 0 and pos > 100:
                positions.append({"title": ct, "pos": pos})

        # 按位置排序
        positions.sort(key=lambda x: x["pos"])

        # 如果某章标题位置太近（<500字符），保留第一个
        filtered = []
        for p in positions:
            if not filtered or p["pos"] - filtered[-1]["pos"] > 500:
                filtered.append(p)

        # 拆分 + 质量过滤
        chapters = []
        for i, cp in enumerate(filtered):
            start = cp["pos"]
            end = filtered[i + 1]["pos"] if i + 1 < len(filtered) else len(text)
            content = text[start:end].strip()

            # 质量过滤：跳过明显是OCR噪声的"标题"
            title = cp["title"]
            chinese_ratio = sum(1 for c in title if '一' <= c <= '鿿') / max(len(title), 1)
            if chinese_ratio < 0.3 and len(title) < 15:
                continue  # 标题不含足够中文，可能是OCR噪声
            if len(content) < 300:
                continue  # 内容太少

            chapters.append({
                "title": title,
                "content": content,
                "page_hint": "",
            })

        return chapters if chapters else [{"title": "全文", "content": text, "page_hint": ""}]

    # ============================================================
    # 关键词提取
    # ============================================================
    # 停用词 JSON 文件路径（相对于项目根目录）
    _STOP_WORDS_PATH = Path(__file__).resolve().parent / "stop_words.json"

    # 焊接特征字 — 从术语库动态生成
    _WELDING_CHARS: Set[str] = None

    @classmethod
    def _load_stop_words(cls) -> set:
        """加载停用词：优先读外部 JSON，文件不存在时用内置默认值"""
        try:
            if cls._STOP_WORDS_PATH.exists():
                return set(json.loads(cls._STOP_WORDS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
        # 内置默认停用词
        return {
            "因此", "所以", "然后", "并且", "而且", "但是", "不过", "虽然", "因为",
            "如图", "所示", "其中", "进行", "使用", "采用", "通过", "可以", "需要",
            "一个", "一种", "这个", "那个", "它们", "我们", "他们",
            "包括", "以及", "或者", "还有", "同时", "另外", "此外",
            "一般", "通常", "不同", "主要", "重要", "基本",
            "问题", "方法", "过程", "情况", "结果", "影响", "作用", "目的",
            "由于", "对于", "关于", "根据", "按照", "经过",
            "明显", "显著", "较高", "较低", "较大", "较小", "较好", "较差",
            "目前", "现在", "以前", "以后", "将来",
            "可能", "是否", "是否", "能够", "不能", "不会",
            "各种", "所有", "一些", "许多", "部分", "全部",
            "相关", "相应", "有关", "涉及", "具有", "具备",
            "增加", "减少", "提高", "降低", "改变", "变化",
            "形成", "产生", "发生", "出现", "存在",
            "采用", "应用", "利用", "使", "能", "会", "要", "可", "已", "的", "在", "和", "与", "或",
        }

    @classmethod
    def _get_welding_chars(cls) -> set:
        """从 KEYWORD_CATEGORY_MAP 动态生成焊接特征字集合（所有术语中出现过的汉字）"""
        if cls._WELDING_CHARS is not None:
            return cls._WELDING_CHARS
        chars = set()
        for term in KEYWORD_CATEGORY_MAP:
            for c in term:
                if '一' <= c <= '鿿':
                    chars.add(c)
        cls._WELDING_CHARS = chars
        return chars

    def _extract_keywords(self, text: str) -> set:
        """从文本中提取焊接相关关键词（增强版：1500+领域词库 + 型号提取 + 噪声过滤）"""
        if not text:
            return set()

        # 焊接领域词库 — 直接使用 KEYWORD_CATEGORY_MAP（1500+ 术语，含手册关键词与同义词扩展）
        domain_terms = [k for k in KEYWORD_CATEGORY_MAP if len(k) >= 2]
        stop_words = self._load_stop_words()
        welding_chars = self._get_welding_chars()

        found = set()
        text_lower = text.lower()

        # 1. 领域词库精确匹配
        for term in domain_terms:
            if term.lower() in text_lower:
                found.add(term)

        # 2. 高频中文短语提取（2-4字，过滤噪声）
        chinese_phrases = re.findall(r'[一-鿿]{2,4}', text)
        phrase_counter = Counter(chinese_phrases)
        for phrase, count in phrase_counter.most_common(50):  # [调优] 80→50，收紧候选数
            if count >= 5 and phrase not in found and phrase not in stop_words:  # [调优] 3→5，提高频次门槛
                if any(c in phrase for c in welding_chars):
                    found.add(phrase)

        # 3. 英文/数字型号提取（焊材牌号、钢号等：E4303, Q345, ER50-6, HJ431...）
        # [调优] 正则中 \s→显式空格/tab，防止换行噪声（A\n40, F\n200, ESW\n309L 等）被误匹配
        models = re.findall(r'\b[A-Z]{1,4}[ \t]?[- \t]?\d{2,5}[A-Z0-9-]{0,6}\b', text)
        for m in models:
            m = m.replace(' ', '').upper()
            if 2 <= len(m) <= 12 and m not in found:
                found.add(m)
            if len(found) > 500:
                break

        return found

    def _generate_summary(self, text: str, title: str) -> str:
        """为章节生成简要摘要（规则提取前几段关键句）"""
        if not text or len(text) < 50:
            return title

        lines = [l.strip() for l in text.split('\n') if len(l.strip()) > 20]
        # 取前 5 个有意义的句子
        sentences = []
        for line in lines[:20]:
            # 按句号/分号/换行拆分
            parts = re.split(r'[。；;.\n]', line)
            for p in parts:
                p = p.strip()
                if 15 < len(p) < 200:
                    # [v2.6] 乱码过滤：中文占比过低（<0.55）的句子跳过
                    meaningful = re.sub(r'\s+', '', p)
                    if not meaningful:
                        continue
                    cn = sum(1 for c in meaningful if '一' <= c <= '鿿')
                    if cn / len(meaningful) < 0.55:
                        continue
                    sentences.append(p)
                if len(sentences) >= 4:
                    break
            if len(sentences) >= 4:
                break

        summary = "。".join(sentences[:4]) + "。"
        return summary[:500]

    def _extract_welding_params(self, text: str) -> list:
        """提取文本中的焊接工艺参数（数值+单位模式）"""
        patterns = [
            r'(?:电流|焊接电流|电压|电弧电压|功率|温度|预热温度|层间温度|后热温度|压力|速度|焊接速度|焊速|厚度|板厚|直径|焊条直径|焊丝直径|流量|气体流量|线能量|热输入)\s*[：:＝=]?\s*(\d+\.?\d*\s*(?:～|~|-)\s*\d+\.?\d*\s*(?:A|V|W|kW|°C|℃|MPa|GPa|mm|cm|m|mm/s|cm/min|m/min|L/min|kJ|kJ/mm))',
            r'(\d+\.?\d*)\s*[～~-]\s*(\d+\.?\d*)\s*(?:A|V|W|kW|°C|℃|MPa|mm|mm/s|cm/min|m/min|L/min|kJ/mm)',
            r'(\d+\.?\d*)\s*(?:A|V|W|kW|°C|℃|MPa|GPa|mm|cm/min|kJ/mm|L/min|mm/s|m/min)',
        ]
        data = []
        for pat in patterns:
            data.extend(re.findall(pat, text))
        # 去重 + 限制数量
        seen = set()
        unique = []
        for d in data:
            d_str = str(d).strip()
            if d_str and d_str not in seen and len(d_str) < 80:
                seen.add(d_str)
                unique.append(d_str)
        return unique[:150]

    # ============================================================
    # 知识源管理
    # ============================================================
    def unregister(self, source_id: str):
        source_dir = self.store_dir / source_id
        if source_dir.exists():
            import shutil
            shutil.rmtree(source_dir)
        self.registry["sources"] = [s for s in self.registry["sources"] if s["id"] != source_id]
        self._save_registry()

    def get_source(self, source_id: str) -> Optional[dict]:
        for s in self.registry["sources"]:
            if s["id"] == source_id:
                return s
        return None

    def list_sources(self) -> List[dict]:
        return self.registry["sources"]

    def get_full_text(self, source_id: str) -> str:
        path = self.store_dir / source_id / "full_text.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def get_chapters(self, source_id: str) -> list:
        path = self.store_dir / source_id / "chapters.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return []

    def get_keywords(self, source_id: str) -> list:
        path = self.store_dir / source_id / "keywords.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return []

    def get_all_full_texts(self) -> str:
        """所有知识源全文拼接（用于RAG索引）"""
        parts = []
        for src in self.registry["sources"]:
            text = self.get_full_text(src["id"])
            if text:
                parts.append(f"【来源：《{src['filename']}》】\n{text}")
        return "\n\n".join(parts)

    # ============================================================
    # 为LLM构建完整知识目录
    # ============================================================
    def build_knowledge_catalog(self) -> str:
        """构建所有已学习书籍的完整目录，供LLM了解知识库全貌"""
        parts = []
        for src in self.registry["sources"]:
            parts.append(
                f"📄 《{src['filename']}》— {src.get('page_count', '?')}页, "
                f"{src.get('chapter_count', 0)}章, "
                f"{src.get('text_length', 0)}字, "
                f"{src.get('keyword_count', 0)}个关键词, "
                f"{src.get('table_count', 0)}个表格"
            )
            # 列出目录结构
            chapters = src.get("chapters", [])
            if chapters:
                ch_list = "\n".join(
                    f"    {i+1}. {c['title']}"
                    + (f" [摘要: {c.get('summary', '')[:80]}...]" if c.get('summary') else "")
                    for i, c in enumerate(chapters[:25])
                )
                parts[-1] += f"\n  完整目录:\n{ch_list}"

            # 列出关键词
            kws = src.get("all_keywords", [])
            if kws:
                parts[-1] += f"\n  关键词: {', '.join(kws[:40])}"

        return "\n\n".join(parts)

    def search_across_sources(self, query: str) -> list:
        """
        跨所有知识源搜索匹配的章节 — 使用关键词匹配+标题匹配
        返回带评分的排序结果
        """
        import math

        # [调优] 预计算每个关键词的 IDF 权重（跨章频率越低 → 权重越高）
        kw_ch_freq: Dict[str, int] = {}
        for src in self.registry["sources"]:
            chapters = self.get_chapters(src["id"])
            for ch in chapters:
                for kw in set(ch.get("keywords", [])):
                    kw_ch_freq[kw] = kw_ch_freq.get(kw, 0) + 1

        results = []
        query_lower = query.lower()

        for src in self.registry["sources"]:
            source_name = src["filename"]
            chapters = self.get_chapters(src["id"])
            for ch in chapters:
                content = ch.get("content", "")
                title = ch.get("title", "")
                ch_keywords = ch.get("keywords", [])

                # 评分：IDF 加权关键词 + 标题命中 + 内容命中
                score = 0.0
                matched_kws = []
                for kw in ch_keywords:
                    if kw.lower() in query_lower:
                        freq = kw_ch_freq.get(kw, 1)
                        # [调优] IDF 加权：1章独有词=3.0分, 10章共享词≈1.0分, 20章≈0.7分
                        weight = 3.0 / math.log2(1 + freq)
                        score += weight
                        matched_kws.append(kw)
                # 标题命中加分
                title_words = set(title.replace('第', '').replace('章', '').replace('节', '').split())
                for tw in title_words:
                    if len(tw) >= 2 and tw in query:
                        score += 5
                # 术语命中（查询词出现在章节内容中）
                query_terms = re.findall(r'[\w一-鿿]{2,6}', query)
                for qt in query_terms:
                    if qt in content[:8000]:  # [调优] 2000→8000，覆盖更深的章节内容
                        score += 1

                if score > 0:
                    results.append({
                        "source": source_name,
                        "chapter": title,
                        "score": round(score, 1),
                        "matched_keywords": matched_kws[:10],
                        "chapter_keywords": ch_keywords[:15],
                        "summary": ch.get("summary", ""),
                        "content_preview": content[:200],
                    })

        # 按评分排序
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:15]


# 全局实例
_store: Optional[KnowledgeStore] = None


def get_store(store_dir: str = "saved_knowledge") -> KnowledgeStore:
    global _store
    if _store is None:
        _store = KnowledgeStore(store_dir)
    return _store
