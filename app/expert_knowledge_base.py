"""
专家基座知识库 — 概念条目库
============================
由基座常量（TERM_ALIAS_MAP / DEEP_ANALYSIS / SCIENCE_POPULARIZATION /
CROSS_DOMAIN_KNOWLEDGE / WELDING_PROCESS_PARAMS / MATERIAL_PARAM_MAP）
+ 已学书籍章节 构建"概念 → 解析/应用/工艺类型/来源"条目。

每个概念条目：
- definition      概念解析（DEEP_ANALYSIS → 章节摘要 → 兜底）
- application     应用及拓展（CROSS_DOMAIN 实践指导 + 章节实践要点）
- process_types   支持的大体工艺类型（基座 6 工艺）
- process_params  工艺参数范围（若该概念是某工艺）
- material_params 材料参数（若该概念是某材料）
- sources         引用 PDF 上传书籍来源（章节标题 + 页码）

持久化到 saved_knowledge/expert_kb.json。
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("expert_kb")

try:
    from app.welding_knowledge_base import (
        TERM_ALIAS_MAP,
        DEEP_ANALYSIS,
        SCIENCE_POPULARIZATION,
        CROSS_DOMAIN_KNOWLEDGE,
        WELDING_PROCESS_PARAMS,
        MATERIAL_PARAM_MAP,
    )
except ImportError:
    TERM_ALIAS_MAP = {}
    DEEP_ANALYSIS = {}
    SCIENCE_POPULARIZATION = {}
    CROSS_DOMAIN_KNOWLEDGE = {}
    WELDING_PROCESS_PARAMS = {}
    MATERIAL_PARAM_MAP = {}


# ------------------------------------------------------------
# 工艺 / 材料 元数据（用于概念↔工艺/材料 交叉匹配）
# ------------------------------------------------------------
_PROCESS_LIST: List[dict] = []
for key, params in WELDING_PROCESS_PARAMS.items():
    if not isinstance(params, dict):
        continue
    # 从 "SMAW (焊条电弧焊)" 提取英文缩写 + 中文名
    m = re.match(r'([A-Z/]+)\s*[（(]\s*([^）)]+)\s*[)）]', key)
    abbr = m.group(1).strip() if m else key
    cn = m.group(2).strip() if m else key
    aliases = {key, abbr, cn, cn.replace('焊', ''), cn.replace('（', '').replace('）', '')}
    # 常见工程别名
    extra = {
        'SMAW': {'手工电弧焊', '手弧焊', '焊条焊'},
        'SAW': {'埋弧焊', '埋弧自动焊接', '埋弧'},
        'GTAW': {'TIG', '氩弧焊', '钨极氩弧焊', '钨极惰性气体焊'},
        'GMAW': {'MIG', 'MAG', '熔化极氩弧焊', 'CO2焊', 'CO₂焊', '二保焊', '气体保护焊'},
        'FCAW': {'药芯焊丝', '药芯焊丝焊', '自保护焊'},
        'PAW': {'等离子焊', '等离子弧', '微束等离子'},
    }.get(abbr.split('/')[0], set())
    aliases |= extra
    _PROCESS_LIST.append({
        "key": key, "abbr": abbr, "name": cn, "params": params, "aliases": {a for a in aliases if a},
    })

_MATERIAL_LIST: List[dict] = []
for key, params in MATERIAL_PARAM_MAP.items():
    if not isinstance(params, dict):
        continue
    aliases = {key, key.replace('（', '').replace('）', ''), key.split('(')[0].strip()}
    for brand in params.get("牌号", []):
        aliases.add(str(brand).split('(')[0].strip())
        aliases.add(str(brand))
    _MATERIAL_LIST.append({
        "key": key, "name": key, "params": params,
        "aliases": {a for a in aliases if a and len(str(a)) >= 2},
    })


class ExpertKnowledgeBase:
    """专家基座知识库 — 概念条目查询与构建"""

    def __init__(self, path: str = "saved_knowledge/expert_kb.json"):
        self.path = Path(path)
        self.concepts: Dict[str, dict] = {}  # canonical -> entry
        self.built_from: dict = {}
        self._alias_index: Dict[str, str] = {}  # alias -> canonical

    # ------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------
    def build(self, store=None) -> dict:
        """从基座常量 + 已学书籍构建概念条目库，返回统计"""
        canonicals = list(TERM_ALIAS_MAP.keys())
        if not canonicals:
            canonicals = ["焊接", "焊缝", "熔池", "电弧"]
        self.concepts = {}
        self._alias_index = {}

        # 预取章节（一次读取所有书所有章，避免每次 search_across_sources）
        chapters_by_source = self._collect_chapters(store)

        for canonical in canonicals:
            canonical_s = str(canonical).strip()
            if not canonical_s:
                continue
            entry = self._build_concept(canonical_s, chapters_by_source, store)
            self.concepts[canonical_s] = entry
            for al in entry.get("aliases", []):
                self._alias_index.setdefault(str(al).strip(), canonical_s)

        self.save()
        return {
            "concepts": len(self.concepts),
            "alias_index": len(self._alias_index),
            "built_from": self.built_from,
        }

    def _collect_chapters(self, store) -> Dict[str, list]:
        """{source_name: [chapter_dict]}"""
        out = {}
        if store is None:
            return out
        try:
            for src in store.list_sources():
                chs = store.get_chapters(src["id"])
                if chs:
                    out[src["filename"]] = chs
        except Exception as e:
            logger.warning(f"collect chapters failed: {e}")
        return out

    def _build_concept(self, canonical: str, chapters_by_source: dict, store) -> dict:
        aliases = [str(a) for a in TERM_ALIAS_MAP.get(canonical, []) if str(a).strip()]
        all_terms = [canonical] + aliases
        id_ = f"concept_{canonical}"

        # ---- 1. 概念解析 definition ----
        definition = self._gather_definition(canonical, all_terms, chapters_by_source, store)

        # ---- 2. 应用及拓展 application ----
        application = self._gather_application(canonical, all_terms, chapters_by_source)

        # ---- 3. 支持的大体工艺类型 ----
        process_types = self._match_processes(all_terms)

        # ---- 4. 工艺/材料参数 ----
        process_params = None
        material_params = None
        for p in _PROCESS_LIST:
            if self._terms_overlap(all_terms, p["aliases"] | {p["name"], p["abbr"], p["key"]}):
                process_params = p["params"]
                break
        for mat in _MATERIAL_LIST:
            if self._terms_overlap(all_terms, mat["aliases"] | {mat["name"]}):
                material_params = mat["params"]
                break

        # ---- 5. 来源（引用 PDF 上传书籍章节） ----
        sources = self._gather_sources(canonical, all_terms, chapters_by_source)

        # ---- 6. 关键词 ----
        keywords = all_terms + [a for a in aliases if len(a) >= 2]
        keywords = list(dict.fromkeys(keywords))[:20]

        return {
            "id": id_,
            "name": f"{canonical}",
            "canonical": canonical,
            "aliases": aliases,
            "definition": definition,
            "application": application,
            "process_types": process_types,
            "process_params": process_params,
            "material_params": material_params,
            "keywords": keywords,
            "sources": sources,
        }

    def _gather_definition(self, canonical, all_terms, chapters_by_source, store) -> str:
        # 1) DEEP_ANALYSIS 深度分析
        for topic_key, data in DEEP_ANALYSIS.items():
            if not isinstance(data, dict):
                continue
            title = str(data.get("title", ""))
            if any(t in canonical or canonical in t for t in [topic_key, title]) or any(
                    a in title or title in a for a in all_terms if len(a) >= 2):
                parts = [f"## {data.get('title', canonical)}", data.get("overview", "")]
                for sec_title, sec in (data.get("sections", {}) or {}).items():
                    parts.append(f"### {sec_title}\n{sec}")
                return "\n\n".join([p for p in parts if p])

        # 2) 章节内容匹配：找 canonical 本身出现最多的章（避免被同章其他术语抢走），提取上下文
        terms = [t for t in all_terms if len(str(t)) >= 2]
        best = None  # (count, src_name, chapter)
        for src_name, chs in chapters_by_source.items():
            for ch in chs:
                if self._is_noise_title(ch.get("title", "")):
                    continue
                content = ch.get("content", "") or ""
                if self._is_garbled(content):
                    continue
                # 优先 canonical 精确计数；canonical 不出现才用 aliases
                count = content.count(str(canonical))
                if count == 0:
                    count = sum(content.count(str(t)) for t in terms if t != str(canonical))
                if count > 0 and (best is None or count > best[0]):
                    best = (count, src_name, ch)
        if best:
            count, src_name, ch = best
            snippet = self._extract_context(ch.get("content", "") or "", canonical, terms)
            return f"据《{src_name}》「{ch.get('title','')}」：{snippet}"

        # 3) 章节关键词/摘要命中（内容未命中时回退）
        hits = []
        for src_name, chs in chapters_by_source.items():
            for ch in chs:
                if self._is_noise_title(ch.get("title", "")):
                    continue
                ch_kws = ch.get("keywords", []) or []
                if any(t in ch_kws for t in terms):
                    hits.append(f"据《{src_name}》「{ch.get('title','')}」：{ch.get('summary','')[:200]}")
                if len(hits) >= 3:
                    break
            if len(hits) >= 3:
                break
        if hits:
            return "\n\n".join(hits)

        # 4) 兜底：跨源搜索
        if store is not None:
            try:
                matches = store.search_across_sources(canonical)
                if matches:
                    top = matches[0]
                    return f"据《{top['source']}》「{top['chapter']}」：{top.get('summary','')[:300]}"
            except Exception:
                pass
        return "（暂无该概念的权威定义，可参阅《材料焊接原理》相关章节。）"

    @staticmethod
    def _extract_context(content: str, canonical: str, terms: list, width: int = 280) -> str:
        """从章节内容中提取概念词附近的上下文，作为定义片段。
        用滑动窗口找 canonical 出现最密集的窗口（主题展开处，而非总论顺带提及）。"""
        target = canonical if canonical in content else next((t for t in terms if t in content), None)
        if not target:
            return content[:width]
        # 找 canonical 出现最密集的 500 字窗口
        positions = []
        start_i = 0
        while True:
            i = content.find(target, start_i)
            if i < 0:
                break
            positions.append(i)
            start_i = i + 1
        if not positions:
            return content[:width]
        # 统计每个位置前后 250 字窗口内 target 出现次数，取最密集
        best_pos, best_cnt = positions[0], 0
        for pos in positions:
            win = content[max(0, pos - 200):pos + 300]
            cnt = win.count(target)
            if cnt > best_cnt:
                best_cnt, best_pos = cnt, pos
        idx = best_pos
        # 向前截到最近句号/换行（避免带上不同主题前文）
        start = max(0, idx - 60)
        head = content[max(0, idx - 100):idx]
        for sep in ("。", "；", "\n"):
            pos = head.rfind(sep)
            if pos >= 0:
                start = max(0, idx - 100 + pos + 1)
                break
        end = min(len(content), idx + len(target) + width - 60)
        return content[start:end].strip()

    def _gather_application(self, canonical, all_terms, chapters_by_source) -> str:
        parts = []
        # 1) CROSS_DOMAIN 实践指导
        for cross_key, data in CROSS_DOMAIN_KNOWLEDGE.items():
            if not isinstance(data, dict):
                continue
            name = str(data.get("name", ""))
            if any(t in name or name in t for t in all_terms if len(t) >= 2):
                parts.append(f"## {name}\n{data.get('description','')}")
                pg = data.get("practical_guidance", {})
                if pg:
                    parts.append("### 实践指导\n" + "\n".join(f"- {k}：{v}" for k, v in pg.items()))
        # 2) 章节实践要点（摘要 + 关键词）— 过滤乱码
        seen = set()
        for src_name, chs in chapters_by_source.items():
            for ch in chs:
                if self._is_noise_title(ch.get("title", "")):
                    continue
                ch_kws = ch.get("keywords", []) or []
                summary = ch.get("summary", "") or ""
                if self._is_garbled(summary):
                    continue
                if any(t in ch_kws for t in all_terms if len(t) >= 2):
                    title = ch.get("title", "")
                    if title in seen:
                        continue
                    seen.add(title)
                    parts.append(f"据《{src_name}》「{title}」：{summary[:150]}")
        return "\n\n".join(parts)[:2000]

    def _match_processes(self, all_terms) -> list:
        out = []
        for p in _PROCESS_LIST:
            if self._terms_overlap(all_terms, p["aliases"] | {p["name"], p["abbr"], p["key"]}):
                out.append(p["key"])
        return out

    def _gather_sources(self, canonical, all_terms, chapters_by_source) -> list:
        """来源：章节关键词命中（优先）+ 章节内容命中（召回）。
        过滤乱码章节，关键词命中优先排序，限制数量。"""
        sources = []
        seen = set()
        terms = [t for t in all_terms if len(str(t)) >= 2]
        scored = []
        for src_name, chs in chapters_by_source.items():
            for ch in chs:
                title = ch.get("title", "")
                if title in seen or self._is_noise_title(title):
                    continue
                ch_kws = ch.get("keywords", []) or []
                content = ch.get("content", "") or ""
                if self._is_garbled(content):
                    continue
                kw_hit = any(t in ch_kws for t in terms)
                content_hit = any(t in content for t in terms)
                if kw_hit or content_hit:
                    seen.add(title)
                    # 相关度分：关键词命中权重高，内容命中次之
                    kw_count = sum(1 for t in terms if t in ch_kws)
                    content_count = sum(content.count(str(t)) for t in terms if t != str(canonical))
                    score = kw_count * 3 + content_count
                    scored.append((score, {
                        "book": src_name,
                        "chapter": title,
                        "page_hint": ch.get("page_hint", ""),
                        "match": "keyword" if kw_hit else "content",
                    }))
        # 按相关度降序，取前8
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:8]]

    @staticmethod
    def _is_noise_title(title: str) -> bool:
        """OCR 噪声标题过滤：中文占比过低且较短（如 '1800      下     KL'）"""
        if not title:
            return True
        t = str(title).strip()
        cn = sum(1 for c in t if '一' <= c <= '鿿')
        ratio = cn / max(len(t), 1)
        return ratio < 0.3 and len(t) < 20

    @staticmethod
    def _is_garbled(text: str, min_cn_ratio: float = 0.55) -> bool:
        """OCR 乱码过滤：中文占比低于阈值视为乱码（如焊接结构原理的噪声页）"""
        if not text:
            return True
        t = str(text)
        # 只统计有意义的片段（去空白）
        meaningful = re.sub(r'\s+', '', t)
        if not meaningful:
            return True
        cn = sum(1 for c in meaningful if '一' <= c <= '鿿')
        ratio = cn / len(meaningful)
        return ratio < min_cn_ratio

    @staticmethod
    def _terms_overlap(a: list, b: set) -> bool:
        for t in a:
            ts = str(t).strip()
            if not ts or len(ts) < 2:
                continue
            if ts in b:
                return True
            for bb in b:
                if len(str(bb)) >= 2 and (ts in str(bb) or str(bb) in ts):
                    return True
        return False

    # ------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------
    def get_concept(self, canonical: str) -> Optional[dict]:
        return self.concepts.get(canonical)

    def lookup(self, keywords: list) -> Optional[dict]:
        """按关键词命中概念条目：先规范词，再别名反查，再子串模糊"""
        for kw in keywords or []:
            kw_s = str(kw).strip()
            if kw_s in self.concepts:
                return self.concepts[kw_s]
        for kw in keywords or []:
            kw_s = str(kw).strip()
            if kw_s in self._alias_index:
                return self.concepts.get(self._alias_index[kw_s])
        # 模糊：概念名是关键词子串
        for kw in keywords or []:
            kw_s = str(kw).strip()
            if len(kw_s) < 2:
                continue
            for canonical in self.concepts:
                if len(canonical) >= 2 and canonical in kw_s:
                    return self.concepts[canonical]
        return None

    def match_concepts(self, fragments: list) -> list:
        """模糊匹配：返回 [{concept, score}]"""
        out = []
        for frag in fragments or []:
            fs = str(frag).strip()
            if len(fs) < 2:
                continue
            for canonical, entry in self.concepts.items():
                if fs in canonical:
                    out.append({"canonical": canonical, "score": 1.0})
                elif any(fs in str(a) for a in entry.get("aliases", [])):
                    out.append({"canonical": canonical, "score": 0.9})
        # 去重
        uniq = {}
        for o in out:
            c = o["canonical"]
            if c not in uniq or o["score"] > uniq[c]["score"]:
                uniq[c] = o
        return sorted(uniq.values(), key=lambda x: -x["score"])[:10]

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------
    def save(self, path: str = None):
        p = Path(path) if path else self.path
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "concepts": self.concepts,
            "built_from": self.built_from,
        }
        tmp = p.with_suffix(".tmp.json")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    def load(self) -> bool:
        if not self.path.exists():
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.concepts = data.get("concepts", {})
            self.built_from = data.get("built_from", {})
            self._alias_index = {}
            for canonical, entry in self.concepts.items():
                for al in entry.get("aliases", []):
                    self._alias_index.setdefault(str(al).strip(), canonical)
            return bool(self.concepts)
        except Exception as e:
            logger.warning(f"expert_kb load failed: {e}")
            return False

    def stats(self) -> dict:
        return {"concepts": len(self.concepts), "alias_index": len(self._alias_index)}


# ------------------------------------------------------------
# 单例
# ------------------------------------------------------------
_kb: Optional[ExpertKnowledgeBase] = None


def get_expert_kb(path: str = "saved_knowledge/expert_kb.json") -> ExpertKnowledgeBase:
    global _kb
    if _kb is None:
        _kb = ExpertKnowledgeBase(path)
    return _kb
