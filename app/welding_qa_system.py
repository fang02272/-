"""
焊接知识科普与智能问答系统
============================
基于《材料焊接原理》(王宗杰, 2024)构建的科普内容生成系统。

功能：
1. 关键词匹配 → 类别识别
2. 科普内容生成（先科普 → 后推荐 → 附来源 → 促迁移）
3. 交叉领域智能判断（如"弧焊机器人+板厚"）
4. 多轮对话知识补充
"""

import io
import sys

# 修复Windows控制台编码问题
if sys.platform == 'win32':
    try:
        if hasattr(sys.stdout, 'buffer') and sys.stdout.buffer:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except (ValueError, AttributeError):
        pass
    try:
        if hasattr(sys.stderr, 'buffer') and sys.stderr.buffer:
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except (ValueError, AttributeError):
        pass
from typing import List, Dict
from app.welding_knowledge_base import (
    KNOWLEDGE_CATEGORIES,
    KEYWORD_CATEGORY_MAP,
    CROSS_DOMAIN_KNOWLEDGE,
    SCIENCE_POPULARIZATION,
    DEEP_ANALYSIS,
    get_recommendations,
    get_source_reference,
    get_knowledge_transfer,
)


class WeldingQASystem:
    """焊接知识科普与问答系统"""

    def __init__(self):
        self.knowledge_base = KNOWLEDGE_CATEGORIES
        self.keyword_map = dict(KEYWORD_CATEGORY_MAP)  # 可变的副本
        self.cross_domain_kb = CROSS_DOMAIN_KNOWLEDGE
        self.science_content = SCIENCE_POPULARIZATION
        # 外部知识源（上传PDF）的关键词和内容
        self.external_sources: Dict[str, dict] = {}  # {source_name: {keywords, chapters}}

    # ----------------------------------------------------------------
    # 0. 加载外部知识源
    # ----------------------------------------------------------------
    def load_external_knowledge(self, sources: list):
        """加载上传PDF的关键词和章节到系统，每个上传的书作为独立知识源"""
        self.external_sources = {}
        for src in sources:
            name = src.get("filename", "")
            self.external_sources[name] = {
                "keywords": src.get("keywords", []),
                "chapters": src.get("chapters", []),
            }

    # ----------------------------------------------------------------
    # 1. 关键词提取与匹配
    # ----------------------------------------------------------------
    def extract_keywords(self, query: str) -> List[str]:
        """从用户输入中提取焊接相关关键词（同时搜索原书+所有上传PDF）。
        v2.5：精确子串匹配 + 部分匹配（字符包含度），提高组合词命中率。"""
        found_keywords = []
        query_lower = query.lower()
        query_chars = {c for c in query_lower if '一' <= c <= '鿿'}

        # 1. 精确子串匹配（原书）
        for keyword in self.keyword_map:
            if keyword.lower() in query_lower:
                found_keywords.append(keyword)

        # 2. 部分匹配：中文关键词字符包含度 ≥70%（至少2字重合）
        #    "热影响区冷裂纹怎么防止" → 命中"热影响区"/"冷裂纹"这类组合词
        partial_added = 0
        for keyword in self.keyword_map:
            if partial_added >= 6:
                break
            kw = keyword.lower()
            if kw in query_lower or kw in found_keywords:
                continue
            if len(kw) >= 3 and self._partial_match(kw, query_chars):
                found_keywords.append(keyword)
                partial_added += 1

        # 3. 搜索所有上传PDF的关键词（精确 + 部分）
        for src_name, src_info in self.external_sources.items():
            for kw in src_info.get("keywords", []):
                if kw in found_keywords:
                    continue
                kwl = kw.lower()
                if kwl in query_lower:
                    found_keywords.append(kw)
                elif len(kwl) >= 3 and partial_added < 10 and self._partial_match(kwl, query_chars):
                    found_keywords.append(kw)
                    partial_added += 1

        return found_keywords

    @staticmethod
    def _partial_match(keyword: str, query_chars: set) -> bool:
        """中文关键词的字符包含度匹配（去重后字符 ≥70% 在查询中出现）"""
        kw_chars = {c for c in keyword if '一' <= c <= '鿿'}
        if len(kw_chars) < 2:
            return False
        present = sum(1 for c in kw_chars if c in query_chars)
        return present >= 2 and present / len(kw_chars) >= 0.7

    def match_categories(self, keywords: List[str]) -> Dict[str, List[str]]:
        """
        将关键词映射到书籍类别（原书+所有上传PDF）
        返回 {类别: [匹配的关键词]}
        """
        category_matches: Dict[str, List[str]] = {}

        for kw in keywords:
            # 先查原书
            cat = self.keyword_map.get(kw, "")
            if cat:
                category_matches.setdefault(cat, []).append(kw)

            # 再查每个上传PDF
            for src_name, src_info in self.external_sources.items():
                if kw in src_info.get("keywords", []):
                    src_cat = f"📄_{src_name}"
                    category_matches.setdefault(src_cat, []).append(kw)

        return category_matches

    # ----------------------------------------------------------------
    # 2. 交叉领域判断
    # ----------------------------------------------------------------
    def detect_cross_domain(self, category_matches: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """检测是否存在交叉领域关键词组合"""
        cross_domains = {}
        cross_prefix = "cross_"
        regular_cats = {}

        for cat, kws in category_matches.items():
            if cat.startswith(cross_prefix):
                cross_domains[cat] = kws
            else:
                regular_cats[cat] = kws

        return cross_domains

    def is_cross_domain_query(self, category_matches: Dict[str, List[str]]) -> bool:
        """判断是否为交叉领域查询（书中内容+扩展工程知识）"""
        has_book_content = False
        has_cross_content = False

        for cat in category_matches:
            if cat.startswith("cross_"):
                has_cross_content = True
            else:
                has_book_content = True

        return has_book_content and has_cross_content

    # ----------------------------------------------------------------
    # 3. 分析查询类型
    # ----------------------------------------------------------------
    def analyze_query(self, query: str) -> dict:
        """综合分析查询，返回结构化的分析结果"""
        keywords = self.extract_keywords(query)
        category_matches = self.match_categories(keywords)
        cross_domains = self.detect_cross_domain(category_matches)

        # 纯书籍内容类别（排除交叉领域和外部来源）
        book_cats = {k: v for k, v in category_matches.items()
                     if not k.startswith("cross_") and not k.startswith("📄_")}

        # 外部知识源匹配
        external_cats = {k: v for k, v in category_matches.items()
                        if k.startswith("📄_")}

        # 判断查询类型
        is_cross = self.is_cross_domain_query(category_matches)
        is_book_only = len(book_cats) > 0 and len(cross_domains) == 0
        is_cross_only = len(book_cats) == 0 and len(cross_domains) > 0
        is_empty = len(category_matches) == 0
        has_external = len(external_cats) > 0

        # 确定主要部分(第一篇/第二篇)
        parts = set()
        for cat in book_cats:
            if cat in ["第1章_焊缝", "第2章_焊接材料熔化区", "第3章_焊接热影响区", "第4章_焊接接头强韧性"]:
                parts.add("第1篇")
            elif cat in ["第5章_不同材料焊接概论", "第6章_表面改性焊接",
                         "第7章_表面活性化焊接", "第8章_加中间层的焊接",
                         "第9章_固相液相扩散焊接"]:
                parts.add("第2篇")

        # 合并部分级别的类别（如果只有一个部分被匹配，添加部分级引用）
        all_cats = set(book_cats.keys()) | set(parts)

        return {
            "keywords": keywords,
            "category_matches": category_matches,
            "book_categories": book_cats,
            "cross_domains": cross_domains,
            "external_categories": external_cats,
            "all_categories": list(all_cats),
            "is_cross_domain": is_cross,
            "is_book_only": is_book_only,
            "is_cross_only": is_cross_only,
            "is_empty": is_empty,
            "has_external": has_external,
            "matched_parts": list(parts),
        }

    # ----------------------------------------------------------------
    # 4. 主内容生成
    # ----------------------------------------------------------------
    def generate(self, query: str) -> str:
        """
        根据用户查询生成结构化回答：
        1️⃣ 科普  → 书中相关理论知识通俗化解释
        2️⃣ 交叉分析 → (如有) 结合工程实际补充分析
        3️⃣ 推荐  → 阅读和实践建议
        4️⃣ 来源  → 精确引用出处
        5️⃣ 迁移  → 知识迁移与前沿拓展
        """
        analysis = self.analyze_query(query)

        output_parts = []

        # ========== 标题 ==========
        output_parts.append("=" * 60)
        output_parts.append(f"🔬 焊接知识科普系统 — 基于《材料焊接原理》")
        output_parts.append(f"📝 查询: {query}")
        output_parts.append("=" * 60)

        # ========== 识别结果概览 ==========
        if analysis["is_empty"]:
            output_parts.append("")
            output_parts.append("⚠️ 未识别到与本书内容直接相关的焊接关键词。")
            output_parts.append("💡 请尝试输入更具体的焊接相关术语，例如：")
            output_parts.append("   • 焊缝、熔池、热影响区、焊接裂纹")
            output_parts.append("   • 异种材料、扩散焊、活性钎焊")
            output_parts.append("   • 高强钢、不锈钢、铝合金焊接")
            output_parts.append("   • 弧焊机器人、板厚选择、焊接参数")
            return "\n".join(output_parts)

        # 关键词识别
        if analysis["keywords"]:
            output_parts.append(f"\n🔑 识别关键词: {' | '.join(analysis['keywords'])}")
        matched_chapters = [c for c in analysis["all_categories"] if not c.startswith("cross_")]
        if matched_chapters:
            output_parts.append(f"📂 匹配章节: {' | '.join(matched_chapters)}")

        if analysis["is_cross_domain"]:
            output_parts.append(f"🔄 检测到交叉领域查询（焊接理论 + 工程应用）")

        # ========== 1️⃣ 科 普 ==========
        output_parts.append(f"\n{'─' * 60}")
        output_parts.append("1️⃣  科 普 — 焊接理论基础")
        output_parts.append(f"{'─' * 60}")

        if analysis["book_categories"]:
            displayed = set()
            # 判断是否有具体章节匹配（而非只有篇级匹配）
            chapter_cats = [c for c in analysis["book_categories"]
                          if c.startswith("第") and "章" in c]
            part_cats = [c for c in analysis["book_categories"]
                       if c.startswith("第") and "篇" in c]

            if chapter_cats:
                # 有具体章节匹配 → 只显示章节级科普（更精准）
                for cat in chapter_cats:
                    if cat in self.science_content and cat not in displayed:
                        output_parts.append(self.science_content[cat].strip())
                        displayed.add(cat)
                # 也显示匹配到的篇级科普作为补充
                for part in analysis["matched_parts"]:
                    if part in self.science_content and part not in displayed:
                        output_parts.append(self.science_content[part].strip())
                        displayed.add(part)
            elif part_cats:
                # 只有篇级匹配 → 显示篇级科普
                for cat in part_cats:
                    if cat in self.science_content and cat not in displayed:
                        output_parts.append(self.science_content[cat].strip())
                        displayed.add(cat)
            else:
                # 其他情况
                for cat in analysis["book_categories"]:
                    if cat in self.science_content and cat not in displayed:
                        output_parts.append(self.science_content[cat].strip())
                        displayed.add(cat)
        elif analysis["is_cross_only"]:
            output_parts.append("📌 您的问题涉及工程实践领域，《材料焊接原理》中以下相关理论可作为基础知识补充：")
            # 从交叉领域知识中找到关联的书籍内容
            for cross_cat in analysis["cross_domains"]:
                if cross_cat in self.cross_domain_kb:
                    related = self.cross_domain_kb[cross_cat].get("related_book_content", [])
                    for item in related:
                        output_parts.append(f"   · {item}")

        # ========== 2️⃣ 交叉分析 ==========
        if analysis["cross_domains"] or analysis["is_cross_domain"]:
            output_parts.append(f"\n{'─' * 60}")
            output_parts.append("2️⃣  交叉分析 — 书中理论 × 工程实践")
            output_parts.append(f"{'─' * 60}")

            for cross_cat, cross_kws in analysis["cross_domains"].items():
                if cross_cat in self.cross_domain_kb:
                    kb = self.cross_domain_kb[cross_cat]
                    output_parts.append(f"\n📌 {kb['name']}:")
                    output_parts.append(f"   {kb['description']}")

                    if "practical_guidance" in kb:
                        output_parts.append(f"\n   【实践指导】")
                        for key, value in kb["practical_guidance"].items():
                            output_parts.append(f"   ▸ {key}: {value}")

                    if "related_book_content" in kb:
                        output_parts.append(f"\n   【书中关联基础】")
                        for item in kb["related_book_content"]:
                            output_parts.append(f"   · {item}")

            # 如何将书中知识导入交叉分析
            output_parts.append(f"\n   【知识导入分析】")
            book_chapters = [c for c in analysis["all_categories"] if not c.startswith("cross_")]
            if book_chapters:
                output_parts.append(f"   您的交叉查询可借助《材料焊接原理》以下章节的理论进行解析：")
                chapter_names = {
                    "第1篇": "金属电弧熔焊完整理论→指导机器人焊接全过程工艺参数设计与缺陷预防",
                    "第2篇": "异种材料焊接策略→指导多材料/梯度结构件的自动化焊接方案设计",
                    "第1章_焊缝": "焊缝冶金与熔池行为→指导机器人焊接参数选择(电流/电压/送丝速度)",
                    "第2章_焊接材料熔化区": "材料熔化区与碳扩散→指导多材料焊接策略与中间层选择",
                    "第3章_焊接热影响区": "焊接热循环与冷却控制→预防自动化焊接中的冷裂纹和变形",
                    "第4章_焊接接头强韧性": "接头强韧性→评估机器人焊接接头的力学性能合格判定",
                    "第5章_不同材料焊接概论": "异种材料焊接性→指导变厚度/变材质工件的焊接策略",
                    "第6章_表面改性焊接": "堆焊与热喷涂→自动化表面工程的工艺基础",
                    "第7章_表面活性化焊接": "活性钎焊→机器人辅助异质连接(陶瓷-金属)",
                    "第8章_加中间层的焊接": "中间层设计→多材料结构件的自动化焊接方案",
                    "第9章_固相液相扩散焊接": "扩散焊→精密零件的自动化热压连接",
                }
                for ch in book_chapters:
                    if ch in chapter_names:
                        output_parts.append(f"   · {chapter_names[ch]}")

        # ========== 3️⃣ 推 荐 ==========
        output_parts.append(f"\n{'─' * 60}")
        output_parts.append("3️⃣  推 荐 — 进一步阅读与实践")
        output_parts.append(f"{'─' * 60}")

        all_cats = analysis["all_categories"] + list(analysis["cross_domains"].keys())
        recommendations = get_recommendations(all_cats)
        for rec in recommendations:
            output_parts.append(f"   {rec}")

        # ========== 4️⃣ 来 源 ==========
        output_parts.append(f"\n{'─' * 60}")
        output_parts.append("4️⃣  来 源 — 引用出处")
        output_parts.append(f"{'─' * 60}")

        # 确保book_categories非空时显示来源
        if analysis["book_categories"] or analysis["matched_parts"]:
            source_cats = list(analysis["book_categories"].keys()) + analysis["matched_parts"]
        else:
            source_cats = []

        sources = get_source_reference(source_cats) if source_cats else [
            "《材料焊接原理》王宗杰主编, 化学工业出版社, 2024, ISBN 978-7-122-44318-2"
        ]
        for src in sources:
            output_parts.append(f"   📚 {src}")

        # 交叉领域额外来源
        if analysis["cross_domains"]:
            output_parts.append(f"\n   【扩展参考】")
            output_parts.append(f"   🔧 中国焊接学会, 《焊接手册》(第3版), 机械工业出版社")
            output_parts.append(f"   🔧 AWS D1.1/D1.1M, Structural Welding Code — Steel")
            output_parts.append(f"   🔧 ISO 15614, Specification and qualification of welding procedures")
            output_parts.append(f"   🔧 GB 50661, 钢结构焊接规范")

        # ========== 5️⃣ 迁 移 ==========
        output_parts.append(f"\n{'─' * 60}")
        output_parts.append("5️⃣  迁 移 — 知识拓展与前沿关联")
        output_parts.append(f"{'─' * 60}")

        transfers = get_knowledge_transfer(
            list(analysis["book_categories"].keys()) + analysis["matched_parts"],
            list(analysis["cross_domains"].keys())
        )
        for t in transfers:
            output_parts.append(f"   {t}")

        if not transfers:
            output_parts.append("   💡 焊接基础理论是理解所有焊接工艺、自动化、智能化的基石。")
            output_parts.append("   💡 建议系统学习《材料焊接原理》全书，建立完整知识框架后，")
            output_parts.append("      再深入弧焊机器人、激光焊接、增材制造等前沿领域。")

        # ========== 结尾 ==========
        output_parts.append(f"\n{'=' * 60}")
        output_parts.append("📌 以上内容基于《材料焊接原理》(王宗杰主编,化学工业出版社,2024)")
        output_parts.append("   结合焊接工程实践知识生成，如需更深入学习请阅读原著。")
        output_parts.append("=" * 60)

        return "\n".join(output_parts)

    # ----------------------------------------------------------------
    # 4.5 结构化JSON输出（供前端使用）
    # ----------------------------------------------------------------
    def _detect_broad_topic(self, query: str) -> dict | None:
        """检测是否为宽泛主题查询，返回 DEEP_ANALYSIS 条目或 None"""
        # 先检查 DEEP_ANALYSIS 的别名映射
        redirect = DEEP_ANALYSIS.get(query.strip())
        if isinstance(redirect, str) and redirect in DEEP_ANALYSIS:
            return DEEP_ANALYSIS[redirect]

        # 直接匹配
        for topic_key, topic_data in DEEP_ANALYSIS.items():
            if isinstance(topic_data, dict) and topic_key in query:
                return topic_data

        # 短查询检测: <=8字, 无问号, 且匹配了知识库关键词
        is_short = len(query.strip()) <= 8
        has_question = any(c in query for c in '？?吗呢什么怎么如何在哪')
        is_broad = is_short and not has_question

        if is_broad:
            # 尝试基于关键词匹配深度分析
            analysis = self.analyze_query(query)
            for kw in analysis["keywords"]:
                for topic_key, topic_data in DEEP_ANALYSIS.items():
                    if isinstance(topic_data, dict) and kw in topic_key:
                        return topic_data

        return None

    def generate_structured(self, query: str) -> dict:
        """
        返回结构化 dict 供前端渲染，包含5个 section 的全部数据
        支持 DeepSeek 风格的深度主题分析
        """
        analysis = self.analyze_query(query)

        # ---- 检测是否需要深度分析 ----
        deep_topic = self._detect_broad_topic(query)

        # ---- 科普内容 ----
        science_parts = []
        # 如果匹配到深度分析主题，将深度分析内容作为科普
        if deep_topic:
            science_parts.append(f"## {deep_topic['title']}\n\n{deep_topic['overview']}")
            for sec_title, sec_content in deep_topic.get("sections", {}).items():
                science_parts.append(f"### {sec_title}\n\n{sec_content}")

        # ---- 外部知识源匹配内容（每本上传的书独立展示） ----
        external_content = []
        if analysis.get("has_external"):
            for ext_cat, ext_kws in analysis.get("external_categories", {}).items():
                source_name = ext_cat.replace("📄_", "")
                if source_name in self.external_sources:
                    ext_info = self.external_sources[source_name]
                    chapters = ext_info.get("chapters", [])
                    # 找到匹配关键词最多的章节（加权评分）
                    scored = []
                    for ch in chapters:
                        ch_kws = ch.get("keywords", [])
                        matched = [k for k in ext_kws if k in ch_kws]
                        if matched:
                            scored.append((len(matched), ch))
                    scored.sort(key=lambda x: x[0], reverse=True)

                    if scored:
                        parts = [f"## 📄 《{source_name}》— 匹配章节"]
                        for score, ch in scored[:5]:
                            summary = ch.get("summary", "")[:200]
                            kws_preview = ", ".join(ch.get("keywords", [])[:8])
                            parts.append(
                                f"### {ch['title']} [匹配度:{score}]\n"
                                f"**关键词**: {kws_preview}\n"
                                f"**摘要**: {summary}\n"
                            )
                        external_content.append("\n".join(parts))
                    elif chapters:
                        # 没有精确关键词匹配时，列出前几章概览
                        parts = [f"## 📄 《{source_name}》— 全书章节概览"]
                        for ch in chapters[:6]:
                            parts.append(f"- {ch['title']}: {ch.get('summary', '')[:120]}")
                        external_content.append("\n".join(parts))

        if analysis["book_categories"] and not deep_topic:
            displayed = set()
            chapter_cats = [c for c in analysis["book_categories"]
                          if c.startswith("第") and "章" in c]
            part_cats = [c for c in analysis["book_categories"]
                       if c.startswith("第") and "篇" in c]

            if chapter_cats:
                for cat in chapter_cats:
                    if cat in self.science_content and cat not in displayed:
                        science_parts.append(self.science_content[cat].strip())
                        displayed.add(cat)
                for part in analysis["matched_parts"]:
                    if part in self.science_content and part not in displayed:
                        science_parts.append(self.science_content[part].strip())
                        displayed.add(part)
            elif part_cats:
                for cat in part_cats:
                    if cat in self.science_content and cat not in displayed:
                        science_parts.append(self.science_content[cat].strip())
                        displayed.add(cat)
            else:
                for cat in analysis["book_categories"]:
                    if cat in self.science_content and cat not in displayed:
                        science_parts.append(self.science_content[cat].strip())
                        displayed.add(cat)

        related_book = []
        if analysis["is_cross_only"]:
            for cross_cat in analysis["cross_domains"]:
                if cross_cat in self.cross_domain_kb:
                    related_book.extend(
                        self.cross_domain_kb[cross_cat].get("related_book_content", [])
                    )

        # ---- 交叉分析 ----
        cross_sections = []
        knowledge_import = []
        for cross_cat, cross_kws in analysis["cross_domains"].items():
            if cross_cat in self.cross_domain_kb:
                kb = self.cross_domain_kb[cross_cat]
                cross_sections.append({
                    "name": kb["name"],
                    "description": kb["description"],
                    "practice_guide": kb.get("practical_guidance", {}),
                    "book_links": kb.get("related_book_content", []),
                })

        if cross_sections:
            book_chapters = [c for c in analysis["all_categories"] if not c.startswith("cross_")]
            chapter_names = {
                "第1篇": "金属电弧熔焊完整理论→指导机器人焊接全过程工艺参数设计与缺陷预防",
                "第2篇": "异种材料焊接策略→指导多材料/梯度结构件的自动化焊接方案设计",
                "第1章_焊缝": "焊缝冶金与熔池行为→指导机器人焊接参数选择(电流/电压/送丝速度)",
                "第2章_焊接材料熔化区": "材料熔化区与碳扩散→指导多材料焊接策略与中间层选择",
                "第3章_焊接热影响区": "焊接热循环与冷却控制→预防自动化焊接中的冷裂纹和变形",
                "第4章_焊接接头强韧性": "接头强韧性→评估机器人焊接接头的力学性能合格判定",
                "第5章_不同材料焊接概论": "异种材料焊接性→指导变厚度/变材质工件的焊接策略",
                "第6章_表面改性焊接": "堆焊与热喷涂→自动化表面工程的工艺基础",
                "第7章_表面活性化焊接": "活性钎焊→机器人辅助异质连接(陶瓷-金属)",
                "第8章_加中间层的焊接": "中间层设计→多材料结构件的自动化焊接方案",
                "第9章_固相液相扩散焊接": "扩散焊→精密零件的自动化热压连接",
            }
            for ch in book_chapters:
                if ch in chapter_names:
                    knowledge_import.append(chapter_names[ch])

        # ---- 推荐 ----
        if deep_topic and "recommendations" in deep_topic:
            recommendations = deep_topic["recommendations"]
        else:
            all_cats = analysis["all_categories"] + list(analysis["cross_domains"].keys())
            recommendations = get_recommendations(all_cats)

        # ---- 来源 ----
        if deep_topic and "book_foundation" in deep_topic:
            sources = [
                "《材料焊接原理》王宗杰主编, 化学工业出版社, 2024, ISBN 978-7-122-44318-2"
            ] + deep_topic["book_foundation"]
        elif analysis["book_categories"] or analysis["matched_parts"]:
            source_cats = list(analysis["book_categories"].keys()) + analysis["matched_parts"]
            sources = get_source_reference(source_cats)
        else:
            sources = [
                "《材料焊接原理》王宗杰主编, 化学工业出版社, 2024, ISBN 978-7-122-44318-2"
            ]
        extended_sources = []
        if analysis["cross_domains"]:
            extended_sources = [
                "中国焊接学会, 《焊接手册》(第3版), 机械工业出版社",
                "AWS D1.1/D1.1M, Structural Welding Code — Steel",
                "ISO 15614, Specification and qualification of welding procedures",
                "GB 50661, 钢结构焊接规范",
            ]

        # ---- 迁移 ----
        if deep_topic and "transfer" in deep_topic:
            transfers = deep_topic["transfer"]
        else:
            transfers = get_knowledge_transfer(
                list(analysis["book_categories"].keys()) + analysis["matched_parts"],
                list(analysis["cross_domains"].keys())
            )
        if not transfers:
            transfers = [
                "💡 焊接基础理论是理解所有焊接工艺、自动化、智能化的基石。",
                "💡 建议系统学习《材料焊接原理》全书，建立完整知识框架后，再深入弧焊机器人、激光焊接、增材制造等前沿领域。",
            ]

        # ---- 章节名称映射 ----
        chapter_labels = {
            "第1章_焊缝": "第1章 焊缝",
            "第2章_焊接材料熔化区": "第2章 焊接材料熔化区",
            "第3章_焊接热影响区": "第3章 焊接热影响区",
            "第4章_焊接接头强韧性": "第4章 焊接接头强韧性",
            "第5章_不同材料焊接概论": "第5章 不同材料焊接概论",
            "第6章_表面改性焊接": "第6章 表面改性焊接",
            "第7章_表面活性化焊接": "第7章 表面活性化焊接",
            "第8章_加中间层的焊接": "第8章 加中间层的焊接",
            "第9章_固相液相扩散焊接": "第9章 固相液相扩散焊接",
            "第1篇": "第1篇 金属材料的电弧熔化焊接原理",
            "第2篇": "第2篇 不同材料的焊接原理",
        }
        matched_labels = [chapter_labels.get(c, c) for c in analysis["all_categories"]
                         if c in chapter_labels]

        return {
            "query": query,
            "keywords": analysis["keywords"],
            "matched_categories": matched_labels,
            "is_cross_domain": analysis["is_cross_domain"] or analysis["is_cross_only"],
            "has_cross": bool(analysis["cross_domains"]),
            "is_empty": analysis["is_empty"],
            "sections": {
                "science": {
                    "title": "科普",
                    "icon": "🔬",
                    "content": "\n\n".join(science_parts + external_content) if (science_parts or external_content) else "",
                    "related_book": related_book,
                },
                "cross_analysis": {
                    "title": "交叉分析",
                    "icon": "🔄",
                    "cross_sections": cross_sections,
                    "knowledge_import": knowledge_import,
                    "visible": bool(cross_sections),
                },
                "recommendations": {
                    "title": "推荐",
                    "icon": "📖",
                    "items": recommendations,
                },
                "sources": {
                    "title": "来源",
                    "icon": "📚",
                    "primary": sources,
                    "extended": extended_sources,
                },
                "transfer": {
                    "title": "知识迁移",
                    "icon": "💡",
                    "items": transfers,
                },
            },
        }

    # ----------------------------------------------------------------
    # 5. 批量知识分类
    # ----------------------------------------------------------------
    def classify_only(self, query: str) -> dict:
        """仅分类不生成完整内容"""
        return self.analyze_query(query)

    def list_categories(self) -> str:
        """列出本书全部知识分类体系"""
        lines = []
        lines.append("=" * 60)
        lines.append("📚 《材料焊接原理》知识分类体系")
        lines.append("=" * 60)

        for part_key, part_info in self.knowledge_base.items():
            lines.append(f"\n{'█' * 50}")
            lines.append(f"  {part_key}: {part_info['name']}")
            lines.append(f"  {part_info['description']}")
            lines.append(f"{'█' * 50}")

            for ch_key, ch_info in part_info["chapters"].items():
                lines.append(f"\n  📖 {ch_key}: {ch_info['title']}")
                for sec_key, sec_desc in ch_info["sections"].items():
                    lines.append(f"      {sec_key} — {sec_desc[:120]}")

        lines.append(f"\n{'=' * 60}")
        lines.append("📌 共2篇9章，覆盖金属电弧熔焊到异种材料固相连接的全谱系焊接理论")
        lines.append("=" * 60)

        return "\n".join(lines)


# ============================================================
# 交互式入口
# ============================================================
if __name__ == "__main__":
    qa = WeldingQASystem()

    print("=" * 60)
    print("🔬 焊接知识科普系统")
    print("   基于《材料焊接原理》(王宗杰, 2024)")
    print("=" * 60)
    print()
    print("使用说明:")
    print("  • 输入焊接相关关键词获取科普内容")
    print("  • 支持交叉领域查询 (如: 弧焊机器人+板厚)")
    print("  • 输入 'categories' 查看知识分类体系")
    print("  • 输入 'exit' 退出")
    print()

    # 演示样例
    demo_queries = [
        "焊接热影响区冷裂纹怎么防止",
        "弧焊机器人焊接3mm薄板怎么选参数",
        "异种材料焊接为什么难",
        "活性钎焊是什么",
    ]

    print("📋 输入示例:")
    for dq in demo_queries:
        print(f"  > {dq}")
    print()

    while True:
        try:
            query = input("🔍 请输入查询: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见!")
            break

        if not query:
            continue

        if query.lower() in ("exit", "quit", "q"):
            print("👋 再见!")
            break

        if query.lower() in ("categories", "cat", "分类"):
            print(qa.list_categories())
            continue

        if query.lower().startswith("classify:"):
            result = qa.classify_only(query[9:].strip())
            print(f"\n📊 分类结果:")
            print(f"   关键词: {result['keywords']}")
            print(f"   书籍章节: {list(result['book_categories'].keys())}")
            print(f"   交叉领域: {list(result['cross_domains'].keys())}")
            print(f"   交叉查询: {result['is_cross_domain']}")
            print()
            continue

        # 生成完整回答
        response = qa.generate(query)
        print("\n" + response + "\n")
