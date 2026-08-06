"""
意图路由 — 概念 vs 工艺参数
============================
内部两步思考（不展示在前端 UI）：
  思考① 意图解析：analyze_intent / extract_params —— 判断问题类型 + 抽取材料/板厚/工艺/参数词
  思考② 工艺匹配：match_parameters —— 从基座三表 + 手册人工标注 匹配出选型参数建议

对外只暴露 route.intent + route.confidence（用于前端徽标），
extracted / param_match / 候选评分 等思考细节一律不进入响应 JSON。
"""

import re
from enum import Enum
from typing import Dict, List, Optional

try:
    from app.welding_knowledge_base import (
        WELDING_PROCESS_PARAMS,
        MATERIAL_PARAM_MAP,
        ELECTRODE_PARAM_TABLE,
    )
except ImportError:
    WELDING_PROCESS_PARAMS = {}
    MATERIAL_PARAM_MAP = {}
    ELECTRODE_PARAM_TABLE = {}


class QueryIntent(str, Enum):
    CONCEPT = "concept"       # 基本概念问题
    PARAMETER = "parameter"   # 工艺参数问题
    MIXED = "mixed"           # 概念+参数 混合
    OTHER = "other"           # 其他（走 LLM/通用兜底）


# ------------------------------------------------------------
# 信号词表
# ------------------------------------------------------------
_PARAM_TERMS = [
    "电流", "电压", "焊速", "焊接速度", "送丝速度", "预热", "预热温度", "层间温度",
    "后热", "焊后热处理", "热输入", "线能量", "保护气流量", "气体流量", "干伸长",
    "板厚", "焊条直径", "焊丝直径", "焊材", "焊条", "焊丝", "牌号", "参数",
    "怎么选", "选多大", "电流多大", "电压多大", "焊接参数", "工艺参数",
    "电流范围", "电压范围", "选用", "选择", "推荐", "怎么焊", "如何焊", "焊接规范",
]

_CONCEPT_MARKERS = [
    "是什么", "什么是", "定义", "概念", "原理", "机理", "为什么", "为何",
    "区别", "分类", "介绍", "解释", "讲一下", "包括", "作用", "含义",
    "指什么", "什么意思", "有哪些", "什么叫", "何为", "啥是", "特点是",
]

# 材料：基座 9 材料 + 牌号 + 基础标识（Q345/16Mn）+ 常见名
_MATERIAL_NAMES = []
_MATERIAL_BRANDS = []
for _m_key, _m_params in MATERIAL_PARAM_MAP.items():
    _MATERIAL_NAMES.append(_m_key)
    # 基础标识：括号前 'Q345 (16Mn)' → 'Q345'、括号内 '16Mn'
    _base = str(_m_key).split('(')[0].strip()
    if len(_base) >= 2:
        _MATERIAL_BRANDS.append(_base)
    _paren = re.search(r'[（(]([^）)]+)[)）]', str(_m_key))
    if _paren:
        for _inner in re.split(r'[、/]', _paren.group(1)):
            _inner = _inner.strip()
            if 2 <= len(_inner) <= 12:
                _MATERIAL_BRANDS.append(_inner)
    for _b in _m_params.get("牌号", []):
        _b = str(_b)
        _MATERIAL_BRANDS.append(_b.split('(')[0].strip())
        _MATERIAL_BRANDS.append(_b)
_MATERIAL_NAMES += ["碳钢", "高强钢", "不锈钢", "铝合金", "铸铁", "耐热钢",
                    "低合金钢", "双相不锈钢", "奥氏体不锈钢", "低碳钢", "中碳钢"]
_MATERIAL_NAMES = sorted(set(_MATERIAL_NAMES), key=len, reverse=True)
_MATERIAL_BRANDS = sorted(set(_MATERIAL_BRANDS), key=len, reverse=True)

# 工艺：基座 6 工艺 + 别名
_PROCESS_NAMES = []
for _p_key, _p_params in WELDING_PROCESS_PARAMS.items():
    _PROCESS_NAMES.append(_p_key)
_PROCESS_ALIASES = [
    "手工电弧焊", "手弧焊", "焊条电弧焊", "埋弧焊", "埋弧自动焊", "氩弧焊",
    "钨极氩弧焊", "熔化极氩弧焊", "二保焊", "CO2焊", "CO₂焊", "药芯焊丝焊",
    "等离子焊", "等离子弧焊", "气保焊", "气体保护焊", "SMAW", "SAW", "GTAW",
    "TIG", "GMAW", "MIG", "MAG", "FCAW", "PAW",
]
_PROCESS_NAMES = sorted(set(_PROCESS_NAMES + _PROCESS_ALIASES), key=len, reverse=True)

_THICKNESS_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:mm|㎜)')
_UNIT_RE = re.compile(r'\d+(?:\.\d+)?\s*(?:A|V|W|kW|°C|℃|MPa|GPa|L/min|cm/min|m/min|kJ/mm|mm/s)')
_ELECTRODE_RE = re.compile(r'(?:焊条|焊丝|焊材)?\s*[A-Z]{1,3}\d{2,4}[A-Z0-9\-]*')


class QARouter:
    """意图路由 + 参数抽取 + 工艺匹配"""

    # ------------------------------------------------------------
    # 思考①：意图解析
    # ------------------------------------------------------------
    def analyze_intent(self, query: str, keywords: list = None) -> Dict:
        """返回 {intent, confidence_floor, signals, extracted}"""
        extracted = self.extract_params(query)
        sig = self._signals(query, extracted)

        if sig["param"] and sig["concept"]:
            intent = QueryIntent.MIXED
        elif sig["param"]:
            intent = QueryIntent.PARAMETER
        elif sig["concept"]:
            intent = QueryIntent.CONCEPT
        else:
            intent = QueryIntent.OTHER

        return {
            "intent": intent,
            "signals": sig,
            "extracted": extracted,
        }

    def extract_params(self, query: str) -> Dict:
        """抽取材料/板厚/工艺/焊材/参数词（内部思考第一步产物）"""
        materials = []
        for m in _MATERIAL_NAMES:
            if m in query and m not in materials:
                materials.append(m)
        # 牌号（大小写不敏感，Q345/q345）
        q_lower = query.lower()
        for b in _MATERIAL_BRANDS:
            bl = b.lower()
            if bl and bl in q_lower and b not in materials:
                materials.append(b)
                break  # 命中一个牌号即可（避免刷屏）

        process = None
        for p in _PROCESS_NAMES:
            if p in query:
                process = p
                break

        thickness = None
        m = _THICKNESS_RE.search(query)
        if m:
            try:
                thickness = float(m.group(1))
            except ValueError:
                thickness = None

        param_terms = [t for t in _PARAM_TERMS if t in query]
        electrode = None
        em = _ELECTRODE_RE.search(query)
        if em:
            electrode = em.group(0).strip()

        return {
            "materials": materials[:3],
            "thickness": thickness,
            "process": process,
            "electrode": electrode,
            "param_terms": param_terms,
        }

    def _signals(self, query: str, extracted: Dict) -> Dict:
        has_param_term = len(extracted["param_terms"]) >= 1
        has_material = bool(extracted["materials"])
        has_thickness = extracted["thickness"] is not None
        has_process = extracted["process"] is not None
        has_unit = bool(_UNIT_RE.search(query))
        # 参数信号：
        #  ① 显式参数词 + 材料/板厚/工艺/单位 任一
        #  ② 工艺 + (材料或板厚) 组合（如 "304不锈钢 3mm TIG焊"）
        #  ③ 材料 + 板厚 组合（如 "Q345 12mm"）
        param = (has_param_term and (has_material or has_thickness or has_process or has_unit)) \
                or (has_process and (has_material or has_thickness)) \
                or (has_material and has_thickness)
        concept = any(marker in query for marker in _CONCEPT_MARKERS)
        return {
            "param": param,
            "concept": concept,
            "has_param_term": has_param_term,
            "has_material": has_material,
            "has_thickness": has_thickness,
            "has_process": has_process,
            "has_unit": has_unit,
        }

    # ------------------------------------------------------------
    # 置信度（结合专家库命中，阈值由 config 提供）
    # ------------------------------------------------------------
    def confidence(self, intent: QueryIntent, extracted: Dict, concept: Optional[dict],
                   routing_cfg: dict) -> float:
        if intent == QueryIntent.OTHER:
            return 0.0  # 硬护栏：无关查询绝不本地假回答
        cfg = routing_cfg or {}
        base = float(cfg.get("base", 0.20))
        if intent in (QueryIntent.PARAMETER, QueryIntent.MIXED):
            materials = extracted.get("materials", [])
            has_material = bool(materials)
            has_process = extracted.get("process") is not None
            has_thickness = extracted.get("thickness") is not None
            if has_material and has_process and has_thickness:
                return float(cfg.get("param_complete", 0.90))
            if has_material or has_process:
                return min(float(cfg.get("param_partial", 0.55)),
                           float(cfg.get("local_medium", 0.55)))
            # 材料/工艺都没识别 → 压到 medium 以下，需要 LLM
            return base
        if intent == QueryIntent.CONCEPT and concept:
            return float(cfg.get("concept_exact", 0.85))
        return base

    # ------------------------------------------------------------
    # 思考②：工艺匹配（内部，不对外展示）
    # ------------------------------------------------------------
    def match_parameters(self, extracted: Dict, query: str, expert_kb=None,
                         vector_hits: list = None) -> Dict:
        """从基座三表 + 手册标注 匹配出选型参数建议。
        返回 {matched, material, process, thickness, recommendations, application, sources}"""
        recs = []
        sources = []
        application_parts = []

        material_key = None
        material_params = None
        for m in extracted.get("materials", []):
            # 定位 MATERIAL_PARAM_MAP 键（含牌号）
            hit = self._find_material(m)
            if hit:
                material_key, material_params = hit
                break

        process_key = None
        process_params = None
        p = extracted.get("process")
        if p:
            hit = self._find_process(p)
            if hit:
                process_key, process_params = hit

        thickness = extracted.get("thickness")
        electrode_choice = None
        if thickness is not None:
            for ek, ep in ELECTRODE_PARAM_TABLE.items():
                if isinstance(ep, dict):
                    tr = self._parse_thickness_range(ep.get("适用板厚", ""))
                    if tr and tr[0] <= thickness <= tr[1]:
                        electrode_choice = (ek, ep)
                        break

        # ---- 组装推荐行 ----
        if process_params:
            for pk in ("电流范围", "电压范围", "焊速范围", "适用板厚", "熔敷效率", "保护方式"):
                if pk in process_params:
                    recs.append({"param": pk, "value": str(process_params[pk]),
                                 "source": process_key})
            app_protect = process_params.get('保护方式', '')
            if app_protect:
                application_parts.append(f"{process_key}：保护方式 {app_protect}")
        if material_params:
            for mk in ("焊接性", "预热", "层间温度", "后热", "热输入"):
                if mk in material_params and material_params[mk]:
                    recs.append({"param": mk, "value": self._fmt_value(material_params[mk]),
                                 "source": material_key})
            fb = material_params.get("推荐焊材")
            if fb:
                recs.append({"param": "推荐焊材", "value": self._fmt_filler(fb),
                             "source": material_key})
            if material_params.get("焊接性"):
                application_parts.append(f"{material_key}焊接性：{material_params['焊接性']}")
        if electrode_choice:
            ek, ep = electrode_choice
            for pk in ("焊接电流", "适用板厚", "焊条类型", "焊接位置"):
                if pk in ep:
                    recs.append({"param": f"{ek}·{pk}", "value": str(ep[pk]),
                                 "source": "电极参数表"})
            application_parts.append(f"板厚{thickness:g}mm 建议选用 {ek}")

        # ---- 来源：专家库概念 + 向量命中 ----
        if expert_kb is not None:
            for term in (extracted.get("materials", []) + [extracted.get("process") or ""]):
                c = expert_kb.lookup([term])
                if c:
                    sources.extend(c.get("sources", []))
        for h in (vector_hits or [])[:4]:
            meta = h.get("meta", {})
            if meta.get("kind") == "book":
                sources.append({"book": meta.get("book", ""),
                                "chapter": meta.get("chapter", ""),
                                "page_hint": meta.get("page_hint", "")})

        # 去重来源
        seen = set()
        uniq_sources = []
        for s in sources:
            k = (s.get("book", ""), s.get("chapter", ""))
            if k not in seen and s.get("book"):
                seen.add(k)
                uniq_sources.append(s)
        sources = uniq_sources[:8]

        matched = bool(recs)
        return {
            "matched": matched,
            "material": material_key,
            "process": process_key,
            "thickness": thickness,
            "electrode": electrode_choice[0] if electrode_choice else None,
            "recommendations": recs,
            "application": "\n".join(application_parts),
            "sources": sources,
        }

    # ------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------
    @staticmethod
    def _find_material(term: str) -> Optional[tuple]:
        """按材料键/牌号精确或边界匹配，避免子串误配（Q345 vs Q345B）"""
        for key, params in MATERIAL_PARAM_MAP.items():
            if not isinstance(params, dict):
                continue
            base = str(key).split('(')[0].strip()
            if term == base or term == key or term in key or base in term:
                return key, params
            for b in params.get("牌号", []):
                b = str(b).split('(')[0].strip()
                if not b:
                    continue
                if term == b or term in b or (b in term and len(term) <= 12):
                    return key, params
        return None

    @staticmethod
    def _find_process(term: str) -> Optional[tuple]:
        for key, params in WELDING_PROCESS_PARAMS.items():
            if not isinstance(params, dict):
                continue
            if term in key or key in term:
                return key, params
            abbr = key.split(' ')[0].replace('(', '').strip()
            if term.upper() == abbr.upper():
                return key, params
        # 别名
        alias_map = {
            "手工电弧焊": "SMAW (焊条电弧焊)", "手弧焊": "SMAW (焊条电弧焊)",
            "焊条电弧焊": "SMAW (焊条电弧焊)", "埋弧焊": "SAW (埋弧自动焊)",
            "埋弧自动焊": "SAW (埋弧自动焊)", "氩弧焊": "GTAW/TIG (钨极氩弧焊)",
            "钨极氩弧焊": "GTAW/TIG (钨极氩弧焊)", "二保焊": "FCAW (药芯焊丝CO₂焊)",
            "CO2焊": "FCAW (药芯焊丝CO₂焊)", "CO₂焊": "FCAW (药芯焊丝CO₂焊)",
            "药芯焊丝焊": "FCAW (药芯焊丝CO₂焊)", "等离子焊": "PAW (等离子弧焊)",
            "等离子弧焊": "PAW (等离子弧焊)",
        }
        canon = alias_map.get(term)
        if canon and canon in WELDING_PROCESS_PARAMS:
            return canon, WELDING_PROCESS_PARAMS[canon]
        return None

    @staticmethod
    def _parse_thickness_range(s: str) -> Optional[tuple]:
        if not s:
            return None
        nums = re.findall(r'\d+(?:\.\d+)?', s)
        if len(nums) >= 2:
            try:
                return float(nums[0]), float(nums[1])
            except ValueError:
                return None
        if len(nums) == 1:
            try:
                v = float(nums[0])
                return v, v
            except ValueError:
                return None
        return None

    @staticmethod
    def _fmt_filler(fb: dict) -> str:
        if isinstance(fb, dict):
            parts = []
            for k, v in fb.items():
                if v:
                    parts.append(f"{k}: {v}")
            return "；".join(parts)
        return str(fb)

    @staticmethod
    def _fmt_value(v) -> str:
        """把嵌套 dict 参数值格式化为可读文本（如 预热: {'板厚<25mm': '不需预热', ...}）"""
        if isinstance(v, dict):
            parts = []
            for k, val in v.items():
                if val:
                    parts.append(f"{k}: {val}")
            return "；".join(parts)
        return str(v)


# ------------------------------------------------------------
# 单例
# ------------------------------------------------------------
_router: Optional[QARouter] = None


def get_router() -> QARouter:
    global _router
    if _router is None:
        _router = QARouter()
    return _router
