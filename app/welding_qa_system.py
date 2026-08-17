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
import re
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
from typing import List, Dict, Set, Optional
from app.welding_knowledge_base import (
    KNOWLEDGE_CATEGORIES,
    KEYWORD_CATEGORY_MAP,
    CROSS_DOMAIN_KNOWLEDGE,
    SCIENCE_POPULARIZATION,
    DEEP_ANALYSIS,
    TERM_ALIAS_MAP,
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
    # ----------------------------------------------------------------
    # 1. 关键词提取与匹配（六阶段流水线，参考焊接知识库融合版）
    # ----------------------------------------------------------------
    # 繁简/异体字归一化映射（焊接材料领域 235 字）
    # [v2.6] 82→235 扩充：补齐紋/異/藥/絲/氬/銑/鍍 等焊接材料领域高频字，
    #        query 侧逐字转简体后整词命中
    _CHAR_NORMALIZE = str.maketrans({
        # === 原有 85 字（材料/元素/电热/组织/技术）===
        '脫': '脱', '氣': '气', '鋼': '钢', '鐵': '铁', '鋁': '铝',
        '銅': '铜', '鈦': '钛', '鎳': '镍', '鎂': '镁', '鉻': '铬',
        '錳': '锰', '鎢': '钨', '釺': '钎', '鋅': '锌', '鉛': '铅',
        '錫': '锡', '銀': '银', '鉬': '钼', '鈷': '钴', '鈮': '铌',
        '鋯': '锆', '鉭': '钽', '電': '电', '熱': '热', '層': '层',
        '區': '区', '連': '连', '極': '极', '溫': '温', '濕': '湿',
        '潤': '润', '燒': '烧', '壓': '压', '擊': '击', '斷': '断',
        '結': '结', '縫': '缝', '線': '线', '質': '质', '體': '体',
        '變': '变', '點': '点', '離': '离', '擴': '扩', '構': '构',
        '組': '组', '織': '织', '細': '细', '顯': '显', '鏡': '镜',
        '顆': '颗', '狀': '状', '態': '态', '維': '维', '銲': '焊',
        '機': '机', '複': '复', '雜': '杂', '數': '数', '據': '据',
        '處': '处', '術': '术', '藝': '艺', '範': '范', '圍': '围',
        '標': '标', '準': '准', '確': '确', '驗': '验', '測': '测',
        '試': '试', '證': '证', '認': '认', '識': '识', '設': '设',
        '備': '备', '裝': '装', '關': '关', '係': '系', '應': '应',
        '響': '响', '與': '与', '為': '为', '會': '会', '銹': '锈',
        # === 扩充：焊接材料/元素（+30）===
        '紋': '纹', '異': '异', '藥': '药', '絲': '丝', '氬': '氩',
        '銑': '铣', '鈍': '钝', '鍍': '镀', '鈹': '铍', '鋰': '锂',
        '鈉': '钠', '鉀': '钾', '鋇': '钡', '鍶': '锶', '釔': '钇',
        '鈧': '钪', '釷': '钍', '釩': '钒', '鉿': '铪', '鑭': '镧',
        '鐒': '铹', '鏑': '镝', '鈥': '钬', '鉺': '铒', '銩': '铥',
        '鐿': '镱', '鑥': '镥', '鉑': '铂', '釕': '钌', '銠': '铑',
        # === 扩充：加工/表面（+18）===
        '滲': '渗', '蝕': '蚀', '剝': '剥', '拋': '抛', '鏜': '镗',
        '鉋': '刨', '鋸': '锯', '鑽': '钻', '鉚': '铆', '淬': '淬',
        '鍥': '锲', '錘': '锤', '鍬': '锹', '鏟': '铲', '銼': '锉',
        '鉗': '钳', '銷': '销', '錨': '锚',
        # === 扩充：性能/物理（+20）===
        '輕': '轻', '強': '强', '軟': '软', '淺': '浅', '寬': '宽',
        '長': '长', '緊': '紧', '鬆': '松', '圓': '圆', '彎': '弯',
        '徑': '径', '軸': '轴', '邊': '边', '緣': '缘', '穩': '稳',
        '脆': '脆', '韌': '韧', '濃': '浓', '純': '纯', '淨': '净',
        # === 扩充：结构/接头（+5）===
        '縱': '纵', '橫': '横', '橢': '椭', '錐': '锥', '楔': '楔',
        # === 扩充：检验/管理（+20）===
        '檢': '检', '評': '评', '審': '审', '核': '核', '記': '记',
        '錄': '录', '報': '报', '單': '单', '檔': '档', '號': '号',
        '規': '规', '則': '则', '條': '条', '項': '项', '節': '节',
        '級': '级', '個': '个', '種': '种', '類': '类', '冊': '册',
        # === 扩充：过程/状态（+12）===
        '產': '产', '進': '进', '轉': '转', '動': '动', '靜': '静',
        '凝': '凝', '溶': '溶', '沸': '沸', '蒸': '蒸', '縮': '缩',
        '脹': '胀', '乾': '干',
        # === 扩充：方向/形状/通用（+14）===
        '後': '后', '內': '内', '外': '外', '頂': '顶', '底': '底',
        '積': '积', '弧': '弧', '熔': '熔', '割': '割', '餘': '余',
        '渣': '渣', '飛': '飞', '濺': '溅', '隙': '隙',
        # === 扩充：更多材料元素（+15）===
        '銻': '锑', '鉍': '铋', '鎘': '镉', '鎵': '镓', '銦': '铟',
        '鉈': '铊', '鈾': '铀', '鈽': '钚', '鋦': '锔', '鎇': '镅',
        '鉲': '锎', '鑀': '锿', '鐨': '镄', '鍆': '钔', '鎶': '鿔',
        # === 扩充：通用高频（+16）===
        '務': '务', '費': '费', '資': '资', '價': '价', '實': '实',
        '現': '现', '對': '对', '時': '时', '間': '间', '萬': '万',
        '億': '亿', '塗': '涂', '護': '护', '損': '损', '傷': '伤',
        '舊': '旧',
        # === 补足：测试覆盖到的漏字（+7）===
        '鏽': '锈', '屬': '属', '奧': '奥', '導': '导',
        '傳': '传', '運': '运', '馬': '马',
    })
    _SUBSCRIPT_NORMALIZE = str.maketrans({
        '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
        '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
        '₊': '+', '₋': '-',
    })
    _CANONICAL_MAP: Dict[str, str] = {}
    _REVERSE_ALIAS_MAP: Dict[str, list] = {}
    _STOP_WORDS = {
        '焊', '焊接', '保护', '气体', '工艺', '方法', '技术', '材料',
        '应用', '参数', '质量', '缺陷', '结构', '性能', '标准', '设备',
        '气体保护', '焊缝金属', '焊接接头', '焊接工艺', '焊接方法',
        '焊接材料', '焊接技术', '焊接参数', '焊接结构',
        'CO2', 'CO₂', 'CO', 'Ar', 'He', 'N2', 'O2', 'H2',
    }

    @staticmethod
    def _normalize_text(text: str) -> str:
        """繁简/异体字归一化 + 上下标数字归一化"""
        text = text.translate(WeldingQASystem._CHAR_NORMALIZE)
        text = text.translate(WeldingQASystem._SUBSCRIPT_NORMALIZE)
        return text

    @classmethod
    def _init_canonical_map(cls):
        """从 TERM_ALIAS_MAP 构建双向索引（别名→规范词 / 规范词→别名）"""
        if cls._CANONICAL_MAP:
            return
        for canonical, aliases in TERM_ALIAS_MAP.items():
            for alias in aliases:
                if alias not in cls._CANONICAL_MAP:
                    cls._CANONICAL_MAP[alias] = canonical
            cls._REVERSE_ALIAS_MAP[canonical] = aliases
        for canonical in TERM_ALIAS_MAP:
            cls._CANONICAL_MAP.setdefault(canonical, canonical)

    @classmethod
    def _is_stop_word(cls, word: str) -> bool:
        return word in cls._STOP_WORDS

    def extract_keywords(self, query: str) -> List[str]:
        """
        六阶段流水线提取焊接领域关键词（融合焊接知识库算法）：
        1. 归一化(繁简/上下标) + 数值参数  2. 候选收集(子串+ngram+去标点)
        3. 吞并过滤(长术语吞并短子串)  4. 规范词扩展(双向别名)
        5. 噪声过滤(单字符/元素/停用词)  6. 长度降序输出
        """
        self._init_canonical_map()
        candidates: Dict[str, int] = {}

        # 阶段1：归一化 + 数值参数提取
        query = self._normalize_text(query)
        query_lower = query.lower()
        _PARAM_PATTERNS = [
            r'\d+\.?\d*\s*mm', r'\d+\.?\d*\s*A\b', r'\d+\.?\d*\s*V\b',
            r'\d+\.?\d*\s*[°℃]C?', r'\d+\.?\d*\s*cm/min', r'\d+\.?\d*\s*m/min',
            r'\d+\.?\d*\s*L/min', r'\d+\.?\d*\s*kJ/mm', r'\d+\.?\d*\s*kJ/cm',
            r'\d+\.?\d*\s*MPa', r'\d+\.?\d*\s*kW', r'\d+\.?\d*\s*kg',
            r'\d+\.?\d*\s*mm/s', r'Φ\s*\d+\.?\d*\s*mm', r'φ\s*\d+\.?\d*\s*mm',
            r'\d+\.?\d*\s*μm',
        ]
        for pattern in _PARAM_PATTERNS:
            for m in re.finditer(pattern, query, re.IGNORECASE):
                param_str = m.group(0).strip()
                if param_str and param_str not in candidates:
                    candidates[param_str] = len(param_str)

        # 阶段2：候选收集 — 子串 + ngram + 去标点 三通道
        for keyword in self.keyword_map:
            if keyword.lower() in query_lower:
                candidates[keyword] = max(candidates.get(keyword, 0), len(keyword))
        # [v2.6] TERM_ALIAS_MAP 别名匹配：query 含别名 → 加入候选（阶段4扩展规范词）
        self._init_canonical_map()
        for alias, canonical in self._CANONICAL_MAP.items():
            if len(alias) >= 2 and alias.lower() in query_lower:
                candidates[alias] = max(candidates.get(alias, 0), len(alias))
                if canonical and canonical not in candidates:
                    candidates[canonical] = max(candidates.get(canonical, 0), len(canonical))
        chinese_runs = re.findall(r'[一-鿿]{2,}', query)
        all_ngrams = set()
        for run in chinese_runs:
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    all_ngrams.add(run[i:i + n])
        for ngram in all_ngrams:
            if ngram in self.keyword_map:
                candidates[ngram] = max(candidates.get(ngram, 0), len(ngram))
        query_normalized = re.sub(
            r'[\s,，、。/；;：:！!？?（）()【】\[\]{}"\'""''\\\\/+\\-\\*=<>]', '', query
        )
        if query_normalized != query:
            for keyword in self.keyword_map:
                if keyword not in candidates and keyword.lower() in query_normalized.lower():
                    candidates[keyword] = max(candidates.get(keyword, 0), len(keyword))

        # 外部PDF关键词
        for src_name, src_info in self.external_sources.items():
            for kw in src_info.get("keywords", []):
                if kw.lower() in query_lower and kw not in candidates:
                    candidates[kw] = len(kw)

        # [v2.6] 本地提取词兜底：英文缩写大小写兼容（query 大写 UT/RT 匹配小写关键词）
        #   + 章节标题/摘要全文兜底（query 含章内词但不在关键词表）
        query_no_space = re.sub(r'[\s　]+', '', query_lower)
        for src_name, src_info in self.external_sources.items():
            for ch in src_info.get("chapters", []):
                title = str(ch.get("title", ""))
                summary = str(ch.get("summary", ""))
                ch_kws = ch.get("keywords", []) or []
                # 大小写兼容：query 中的大写缩写（UT/RT/ET）与章节内容小写匹配
                for kw in ch_kws:
                    kwl = str(kw).lower()
                    if kwl and kwl in query_no_space and kw not in candidates:
                        candidates[kw] = max(candidates.get(kw, 0), len(kw))
                # 正文兜底：query 的词出现在章节标题/摘要，也作为候选
                if len(query) >= 2:
                    for qt in re.findall(r'[一-鿿]{2,4}', query):
                        if qt and (qt in title or qt in summary) and qt not in candidates:
                            candidates[qt] = max(candidates.get(qt, 0), len(qt))

        # 阶段3：吞并过滤 — 长术语吞并短子串（长度比 ≤ 66% 或停用词）
        sorted_candidates = sorted(candidates.items(), key=lambda x: -x[1])
        filtered: List[str] = []
        filtered_lengths: List[int] = []
        for kw, kw_len in sorted_candidates:
            if len(kw) <= 1:
                continue
            parent_len = 0
            for sel, sl in zip(filtered, filtered_lengths):
                if kw != sel and kw in sel:
                    parent_len = max(parent_len, sl)
            if parent_len > 0:
                is_fragment = (kw_len / parent_len <= 0.66) or self._is_stop_word(kw)
                if is_fragment:
                    continue
            filtered.append(kw)
            filtered_lengths.append(kw_len)

        # 阶段4：规范词扩展（双向）
        expanded: List[str] = []
        added_set: Set[str] = set()
        for kw in filtered:
            if kw not in added_set:
                expanded.append(kw)
                added_set.add(kw)
            canonical = self._CANONICAL_MAP.get(kw, '')
            if canonical and canonical != kw and canonical not in added_set:
                expanded.append(canonical)
                added_set.add(canonical)
            if kw in self._REVERSE_ALIAS_MAP:
                for alias in self._REVERSE_ALIAS_MAP[kw]:
                    if alias not in added_set and 3 <= len(alias) <= 5 and alias.isupper() and alias.isalpha():
                        expanded.append(alias)
                        added_set.add(alias)

        # 阶段5：后处理 — 单字符/元素符号/停用词过滤
        expanded = [k for k in expanded if len(k) >= 2 or '一' <= k <= '鿿']
        expanded = [
            k for k in expanded
            if not (len(k) == 2 and k.isascii() and k[0].isupper() and k[1].islower()
                    and k not in query.split() and f' {k} ' not in query)
        ]
        specific = [k for k in expanded if not self._is_stop_word(k) and len(k) >= 2]
        if len(specific) >= 2:
            expanded = [k for k in expanded if not self._is_stop_word(k)]

        # 阶段6：长度降序
        expanded.sort(key=lambda x: -len(x))
        return expanded

    @staticmethod
    def _partial_match(keyword: str, query_chars: set) -> bool:
        """中文关键词的字符包含度匹配（兜底，供外部调用）"""
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
