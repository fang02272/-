"""
知识库持久化存储 v3.0 — 完整学习上传PDF的知识体系
==================================================
每本上传的PDF都经过：目录提取 → 章节拆分 → 摘要生成 → 关键词映射 → 注册为知识源
新知识源与原书同等对待，参与全部项目流程
"""

import json
import os
import re
from pathlib import Path
from typing import List, Dict, Optional


class KnowledgeStore:
    """持久化知识库，管理原书 + 所有已学习的上传PDF"""

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
        source_id = self._make_id(filename)

        # Step 1: 提取完整目录（优先扫描"目录"页）
        toc = self._extract_toc(full_text)

        # Step 2: 按目录拆分章节
        chapters = self._split_chapters(full_text, toc)

        # Step 3: 为每个章节提取关键词和摘要
        enriched_chapters = []
        all_keywords = set()
        for ch in chapters:
            kw = self._extract_keywords(ch.get("content", ""))
            summary = self._generate_summary(ch.get("content", ""), ch.get("title", ""))
            all_keywords.update(kw)
            enriched_chapters.append({
                "title": ch["title"],
                "page_hint": ch.get("page_hint", ""),
                "content": ch.get("content", ""),
                "content_length": len(ch.get("content", "")),
                "keywords": list(kw)[:20],
                "summary": summary,
            })

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
    def _extract_toc(self, text: str) -> list:
        """
        从全文提取目录结构。
        策略：先扫描全文找到所有「第X章」出现位置，去重排序后作为章节列表
        """
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

        # 策略2：全文扫描 — 找到每个「第X章」的首次出现（正文开头，不是页眉）
        if len(toc_entries) < 3:
            chapter_heads = re.finditer(
                r'第[一二三四五六七八九十\d]+章\s*[^\d\n]{2,60}',
                text
            )
            seen_titles = set()
            for m in chapter_heads:
                title = m.group(0).strip()
                # 过滤页眉格式（太短的、纯数字后缀的）
                if len(title) < 8:
                    continue
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
    # 章节拆分
    # ============================================================
    def _split_chapters(self, text: str, toc: list) -> list:
        """
        用标准章节标题在正文中查找位置，按位置拆分。
        只保留每个唯一章节标题的首次出现（过滤页眉重复）。
        """
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
            # 先尝试"第X章"格式
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
    def _extract_keywords(self, text: str) -> set:
        """从文本中提取焊接相关关键词（增强版：300+领域词库 + 噪声过滤）"""
        if not text:
            return set()

        # 焊接领域词库（融合原书 + 手册，共300+专业术语）
        domain_terms = [
            # --- 焊接方法与工艺 (50+) ---
            "焊接", "焊缝", "熔池", "电弧", "焊丝", "焊条", "焊剂", "焊枪", "焊材",
            "保护气", "预热", "后热", "焊后热处理", "层间温度", "线能量", "热输入", "冷却速度",
            "MIG", "MAG", "TIG", "GTAW", "GMAW", "SAW", "SMAW", "FCAW", "PAW", "ESW",
            "等离子", "等离子焊", "激光焊", "埋弧焊", "电阻焊", "钎焊", "扩散焊", "摩擦焊",
            "脉冲焊", "脉冲MIG", "短路过渡", "射流过渡", "熔滴过渡", "药芯焊丝", "实心焊丝",
            "CO2焊", "CO₂焊", "二保焊", "氩弧焊", "气焊", "气割", "电渣焊", "碳弧气刨",
            "平焊", "立焊", "横焊", "仰焊", "打底焊", "填充焊", "盖面焊",
            "单面焊双面成形", "堆焊", "焊补", "手工堆焊", "带极堆焊",
            # --- 材料与牌号 (80+) ---
            "低碳钢", "中碳钢", "高碳钢", "不锈钢", "高强钢", "低合金钢", "低合金高强钢",
            "铝合金", "钛合金", "铜合金", "镍基合金", "镁合金", "异种材料", "异种金属",
            "陶瓷", "复合材料", "铸铁", "灰铸铁", "球墨铸铁", "双相不锈钢", "耐热钢",
            "低温钢", "耐候钢", "调质钢", "正火钢", "珠光体钢", "奥氏体钢",
            "Q235", "Q345", "Q390", "Q420", "Q460", "Q690", "X70", "X80",
            "16Mn", "16MnR", "16MnDR", "09MnNiDR",
            "304", "304L", "316L", "321", "2205", "2507", "430",
            "15CrMo", "12Cr1MoV", "HT200", "HT250", "QT400",
            "6061", "6063", "5083", "5A06", "ER4043", "ER5356", "ER50-6", "H08A",
            "SJ101", "HJ431",
            # --- 缺陷与性能 (30+) ---
            "裂纹", "气孔", "未熔合", "未焊透", "咬边", "飞溅", "变形",
            "冷裂纹", "热裂纹", "延迟裂纹", "结晶裂纹", "层状撕裂", "再热裂纹",
            "韧性", "强度", "硬度", "疲劳", "冲击韧性", "CTOD", "脆性断裂",
            "晶间腐蚀", "应力腐蚀", "白口组织", "氢致裂纹", "氢脆",
            # --- 工艺参数与设备 (40+) ---
            "焊接电流", "电弧电压", "焊接速度", "送丝速度", "t8/5", "热循环",
            "HAZ", "热影响区", "熔合线", "熔深", "余高", "焊缝宽度", "碳当量",
            "弧焊机器人", "焊接电源", "变位机", "送丝机构", "焊缝跟踪",
            "激光视觉", "电弧传感", "离线编程", "示教器", "坡口",
            "V形坡口", "X形坡口", "U形坡口", "钝边", "根部间隙", "坡口角度",
            "焊条直径", "焊条牌号", "焊丝牌号", "焊剂牌号",
            "消氢处理", "消除应力", "反变形", "刚性固定", "焊接工艺评定",
            "WPS", "PQR", "PWHT", "焊接变形", "焊接应力",
        ]

        # 噪声词（高频但无意义的通用中文词）
        stop_words = {
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

        found = set()
        text_lower = text.lower()

        # 1. 领域词库精确匹配
        for term in domain_terms:
            if term.lower() in text_lower:
                found.add(term)

        # 2. 高频中文短语提取（2-4字，过滤噪声）
        chinese_phrases = re.findall(r'[一-鿿]{2,4}', text)
        from collections import Counter
        phrase_counter = Counter(chinese_phrases)
        for phrase, count in phrase_counter.most_common(80):
            if count >= 3 and phrase not in found and phrase not in stop_words:
                # 只保留可能是有意义的术语（含焊接特征字或专有名词格式）
                welding_chars = {'焊', '接', '钢', '铝', '铜', '铁', '钛', '镍', '弧', '电', '热', '缝', '板', '管', '丝', '条', '剂', '气', '渣', '晶', '裂', '硬', '韧', '塑', '温', '压', '流', '速', '层'}
                if any(c in phrase for c in welding_chars):
                    found.add(phrase)

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
            r'(?:电流|电压|功率|温度|压力|速度|厚度|直径|流量|线能量|热输入)\s*[：:＝=]?\s*(\d+\.?\d*\s*(?:～|~|-)\s*\d+\.?\d*\s*(?:A|V|W|kW|°C|℃|MPa|GPa|mm|cm|m|mm/s|cm/min|m/min|L/min|kJ|kJ/mm))',
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
        results = []
        query_lower = query.lower()

        for src in self.registry["sources"]:
            source_name = src["filename"]
            chapters = self.get_chapters(src["id"])
            for ch in chapters:
                content = ch.get("content", "")
                title = ch.get("title", "")
                ch_keywords = ch.get("keywords", [])

                # 评分：关键词命中数 + 标题命中 + 术语命中
                score = 0
                matched_kws = []
                for kw in ch_keywords:
                    if kw.lower() in query_lower:
                        score += 3
                        matched_kws.append(kw)
                # 标题命中加分
                title_words = set(title.replace('第', '').replace('章', '').replace('节', '').split())
                for tw in title_words:
                    if len(tw) >= 2 and tw in query:
                        score += 5
                # 术语命中（查询词出现在章节内容中）
                query_terms = re.findall(r'[\w一-鿿]{2,6}', query)
                for qt in query_terms:
                    if qt in content[:2000]:  # 只检查前2000字
                        score += 1

                if score > 0:
                    results.append({
                        "source": source_name,
                        "chapter": title,
                        "score": score,
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
