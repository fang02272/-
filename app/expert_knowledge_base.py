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

# ============================================================
# 手工概念定义表（v2.6）
# 覆盖：书中未收录/词太专业/英文缩写/衍生概念。
# 构建时优先用手工定义，其次才从章节提取。
# 格式：{规范词: {"definition": ..., "application": ..., "process_types": [...]}}
# ============================================================
MANUAL_DEFINITIONS = {
    "瞬时液相": {
        "definition": "瞬时液相扩散焊（TLP bonding）：在两待焊表面之间放入熔点低于母材的中间层合金，加热至中间层熔化形成液相，液相传质充填间隙，随后在等温条件下液相向母材扩散、成分改变而使凝固点升高，最终等温凝固形成牢固接头。属于固相/液相扩散焊接（第9章范畴）。",
        "application": "用于高温合金、单晶叶片、陶瓷-金属等难焊材料的连接；相比普通扩散焊可降低温度/压力要求，接头组织均匀。航空发动机叶片、精密零部件修复常用。",
        "process_types": ["扩散焊接"],
    },
    "机器人焊接": {
        "definition": "机器人焊接：利用工业机器人（如 MR2010_1 六轴机器人）夹持焊枪，按预编程轨迹自动完成定位、送丝、焊接与姿态调整的自动化焊接方式。核心是焊缝跟踪、姿态规划（工作角/行走角）、焊接参数与机器人运动的协同控制。",
        "application": "适合大批量、重复性、多道多层或危险环境焊接；与工艺卡片结合可减少示教时间——输入材料/板厚/工艺即可获得电流电压、枪姿态、层道序列。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)", "FCAW (药芯焊丝CO₂焊)"],
    },
    "氢致开裂": {
        "definition": "氢致开裂（HIC/Hydrogen Induced Cracking）：焊接过程中溶解在金属中的氢在应力与显微缺陷处聚集，超过材料容纳能力后形成裂纹。是低合金高强钢、管线钢焊接冷裂纹的主要形式之一，与扩散氢含量、拘束度、冷却速度（t8/5）密切相关。",
        "application": "预防：焊材选用低氢焊条（E5015/E5016）、焊前预热、控制层间温度、后热消氢；厚板高强钢需严格控氢和冷却。",
        "process_types": [],
    },
    "超声探伤": {
        "definition": "超声探伤（UT）：利用超声波在工件内部传播时遇到缺陷界面产生反射回波，检测焊缝内部缺陷（气孔、夹渣、未熔合、裂纹）的无损检测方法。TOFD 是超声衍射时差法，利用缺陷端部衍射波精确定量缺陷尺寸。",
        "application": "厚板焊缝、压力容器、管道环缝的常规检测；TOFD/PAUT 用于要求更高的精确缺陷定量。检验要点：按 GB/T 11345 或 AWS D1.1 执行。",
        "process_types": [],
    },
    "临界区": {
        "definition": "临界区（ICHAZ/Intercritical HAZ）：焊接热影响区中被加热到 Ac1~Ac3 之间（临界温度区间）的窄带区域，该区发生不完全重结晶，原始组织部分奥氏体化、部分保留，晶粒不均匀，常是局部脆化区（LBZ）的组成部分。",
        "application": "对碳钢/低合金钢焊接接头韧性影响大；多层多道焊时临界区反复受热，需控制热输入与层间温度。",
        "process_types": [],
    },
    "活性钎料": {
        "definition": "活性钎料（如 Ag-Cu-Ti）：在传统钎料中加入活性元素 Ti 等，在钎焊过程中活性元素与陶瓷表面的氧化物反应，改善钎料对陶瓷的润湿性，从而实现陶瓷-金属、陶瓷-陶瓷的可靠连接（如 DBC 基板、SiC、AlN、Al2O3、ZrO2）。",
        "application": "用于功率电子封装（DBC 铜基板）、陶瓷与金属密封件、传感器等；活性钎焊温度通常 850-950°C，需真空或保护气氛。",
        "process_types": ["钎焊"],
    },
}

# ============================================================
# 手工应用拓展表（v2.6）
# 覆盖书中能搜到定义但"应用及拓展"提取不足的常见术语。
# 构建时若有此条目则补充 application。
# ============================================================
MANUAL_APPLICATIONS = {
    "熔池": "应用要点：熔池行为直接决定焊缝成形与缺陷（气孔/咬边）；机器人焊接中通过调整电流电压、焊速、送丝速度控制熔池尺寸与流动性，脉冲 MIG 可改善薄板熔池控制。",
    "电弧": "应用要点：电弧稳定性是焊接质量基础；机器人焊接常配脉冲电源（如 NBC-500RP）稳定电弧，电弧电压决定弧长，影响熔深与飞溅。",
    "热影响区": "应用要点：热影响区（HAZ）性能决定接头整体质量；通过控制热输入（线能量）、预热、层间温度、t8/5 冷却时间控制 HAZ 硬度与韧性，高强钢需防冷裂。",
    "裂纹": "应用要点：焊接裂纹分热裂/冷裂/再热裂；按母材选焊材、控制预热层间温度、减少拘束可预防；厚板高强钢需按 Ceq/Pcm 评估冷裂倾向。",
    "贝氏体": "应用要点：HAZ 贝氏体组织影响硬度和韧性；通过冷却速度控制贝氏体形态（粒状贝氏体韧性较好），多层多道焊可改善组织。",
    "咬边": "应用要点：咬边是常见成形缺陷，降低接头强度；预防：控制电流不过大、焊枪角度正确、适当摆动、薄板用短弧。",
    "未熔合": "应用要点：未熔合降低承载能力，是返修主要原因；预防：适当增大电流、降低焊速、保证熔池边缘充分熔化，多层焊控制层间清理。",
    "未焊透": "应用要点：未焊透减薄有效截面，多发生于打底焊；预防：控制坡口角度/间隙、保证打底电流充足、背面清根。",
    "气孔": "应用要点：气孔由氢/氮/CO 气体在熔池凝固前未逸出所致；预防：清理焊件、焊材烘干、气体保护流量充足、短弧操作。",
    "夹杂": "应用要点：夹渣/夹钨降低韧性；预防：多层焊每层清渣、正确运条、选用合适焊材。",
    "等轴晶": "应用要点：焊缝等轴晶区改善抗裂性；通过变质处理、控制冷却、搅拌（如超声波/磁场）细化晶粒。",
    "细晶区": "应用要点：HAZ 细晶区（FCCAZ）韧性最好；多层多道焊使晶粒细化，改善接头韧性。",
    "韧性": "应用要点：接头韧性受组织/杂质/残余应力影响；低温工况需关注 DBTT，通过控制热输入与焊材纯净度保证韧性。",
    "韧性断裂": "应用要点：韧性断裂（延性断裂）伴随明显塑性变形，比脆断安全；通过组织细化、减少夹杂提升抗韧性断裂能力。",
    "碳扩散": "应用要点：异种钢焊接（如珠光体钢+奥氏体钢）中碳从低合金侧向高合金侧扩散形成脱碳层/增碳层，降低接头性能；预防：用中间层（Ni 基）、控制焊后热处理。",
    "扩散焊": "应用要点：固态扩散连接用于高温合金/陶瓷/精密零件；需控制温度/压力/时间/真空度，TLP 用中间层降低要求。",
    "预热": "应用要点：预热降低冷却速度、防冷裂；低碳钢一般不需，高强钢/厚板按板厚与碳当量确定（Q345>38mm 需 100-150°C）。",
    "层间温度": "应用要点：多层多道焊控制层间温度（一般≤200-250°C）防过热与变形；不锈钢防敏化需≤150°C。",
    "焊后热处理": "应用要点：PWHT 消除残余应力、改善组织；受压件厚板按规范 600-650°C 去应力；高强钢需控温防止回火脆化。",
    "保护气": "应用要点：保护气类型影响熔滴过渡与成形：MAG 用 80%Ar+20%CO₂、MIG 用纯Ar/富氩、CO₂ 焊用纯CO₂；流量 15-25L/min 防风。",
    "氩弧焊": "应用要点：氩弧焊（GTAW/TIG）用钨极+氩气保护，成形好、无飞溅，适合薄板/有色金属/根部打底焊；机器人焊接中常用于管道打底、不锈钢薄板；注意钨极烧损、控制气体流量10-15L/min。",
    "电弧焊": "应用要点：电弧焊是熔化焊大类；机器人常用熔化极电弧焊（GMAW/FCAW）因熔敷效率高；控制电弧电压-电流匹配、送丝速度与焊速协同。",
    "药芯焊丝电弧焊": "应用要点：FCAW 药芯焊丝含造渣/造气成分，适合户外、大电流高速焊；机器人常用药芯焊丝提高效率，注意清渣与烟尘。",
    "堆焊": "应用要点：堆焊用于表面修复/耐磨层（D256/D322/D517/D707）；机器人堆焊控制稀释率与层间温度，多层多道逐层堆焊。",
    "镍基合金": "应用要点：镍基合金焊（Inconel 等）需控热输入防热裂、用 ERNiCr-3 等焊材；机器人焊接用于高温/耐蚀部件，控制层间温度≤150°C。",
    "线能量": "应用要点：线能量=电流×电压/焊速（kJ/mm），决定热输入与 HAZ 性能；机器人焊接通过调焊速/电流控制线能量，高强钢需限制上限。",
    "气焊": "应用要点：气焊用氧-乙炔焰，设备简单但效率低、变形大；机器人焊接基本不用，仅薄板/小件/补焊场景。",
    "碳弧气刨": "应用要点：碳弧气刨用于清根/开坡口/返修去除缺陷；机器人可配合气刨实现自动化返修，注意烟尘防护。",
    "摩擦焊": "应用要点：摩擦焊是固态连接，热影响区小、接头强度高，适合轴类/异种金属；机器人摩擦焊用于大批量轴件。",
    "电子束焊": "应用要点：电子束焊真空下高能量密度，深宽比大、热影响区极小，适合厚件/精密件；设备昂贵，机器人多用于焊缝对准。",
    "活性钎焊": "应用要点：活性钎焊（Ag-Cu-Ti）用于陶瓷-金属连接（DBC/SiC/AlN）；机器人精密定位钎焊，真空/气氛控制。",
    "脱碳层": "应用要点：异种钢/高温服役中碳迁移形成脱碳层（母材侧）与增碳层（焊缝侧），降低接头性能；预防：Ni 基中间层、限制焊后热处理温度时间。",
    "焊接速度": "应用要点：焊接速度影响线能量/成形/效率；机器人焊接可精确控制恒速，配合送丝速度/电流电压匹配；速度过快易咬边未熔合，过慢热输入大变形。",
    "焊条直径": "应用要点：焊条直径按板厚选（Φ3.2 常用 3-12mm、Φ4.0 用 5-25mm）；直径决定电流范围与熔敷率；机器人焊多用焊丝，直径 1.0-1.2mm。",
    "焊缝跟踪": "应用要点：焊缝跟踪是机器人焊接关键——通过激光/视觉/电弧传感器实时检测焊缝位置偏差并纠偏；常用激光视觉（前置）+ 电弧传感（后置）组合。",
    "V形坡口": "应用要点：V 形坡口用于中厚板对接，坡口角 60°±5°、钝边 1-2mm；机器人焊按坡口尺寸规划填充层数，保证根部熔透。",
    "X形坡口": "应用要点：X 形坡口用于厚板双面焊，减少填充量/变形；机器人焊先焊一面清根再焊另一面，控制变形对称。",
    "韧脆转变温度": "应用要点：DBTT/FATT 是低温韧性指标，低于转变温度材料变脆；焊接接头通过控热输入/组织细化提升低温韧性，压力容器低温工况关键。",
    "无损检测": "应用要点：焊缝质量检验：RT 射线/UT 超声/MT 磁粉/PT 渗透；厚板用 UT/RT，表面裂纹用 MT/PT；机器人焊后按标准抽检。",
    "氢脆": "应用要点：氢脆由扩散氢导致，高强钢敏感；预防：低氢焊材、焊前预热、焊后消氢处理（250°C×2h）、控制焊接环境湿度。",
    "冷裂纹敏感性": "应用要点：冷裂纹敏感性用碳当量 Ceq/Pcm 评估；高敏感钢需预热+控层间温度+后热，插销试验/Tekken 试验定量评估。",
    "再结晶": "应用要点：再结晶消除加工硬化/细化晶粒；多层多道焊热循环使 HAZ 再结晶改善韧性；控制热输入避免晶粒长大。",
    "热等静压": "应用要点：HIP（热等静压）消除内部孔隙/闭合裂纹，用于铸件/增材件/扩散焊件致密化；机器人焊接件关键承力部位可 HIP 处理。",
    "金相组织": "应用要点：金相检验评估焊缝/HAZ 组织（铁素体/贝氏体/马氏体/M-A）；SEM/TEM/EBSD 分析微观缺陷与析出相。",
    "局部脆化区": "应用要点：LBZ（局部脆化区）在 HAZ 临界区/粗晶区，韧性低；预防：控热输入、多层多道细化组织、选用高韧性焊材。",
    "部分熔化区": "应用要点：PMZ（部分熔化区）在熔合线附近，晶界偏析易产生液化裂纹；控制热输入、选低偏析焊材、异种钢用过渡层。",
}





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

        # ---- 0. 手工定义优先（覆盖书中未收录/专业词/缩写/衍生概念）----
        manual = MANUAL_DEFINITIONS.get(canonical)
        if manual:
            definition = manual.get("definition", "")
            application = manual.get("application", "")
            process_types = manual.get("process_types", [])
        else:
            # ---- 1. 概念解析 definition ----
            definition = self._gather_definition(canonical, all_terms, chapters_by_source, store)

            # ---- 2. 应用及拓展 application ----
            application = self._gather_application(canonical, all_terms, chapters_by_source)

            # ---- 3. 支持的大体工艺类型 ----
            process_types = self._match_processes(all_terms)

        # ---- 手工应用补充：若手工应用表有该概念，优先用手工（保证质量与相关性）----
        if canonical in MANUAL_APPLICATIONS:
            application = MANUAL_APPLICATIONS[canonical]

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
        # 2) 章节实践要点（摘要 + 关键词）— 过滤乱码 + 按相关度排序 + 限条数
        scored = []
        for src_name, chs in chapters_by_source.items():
            for ch in chs:
                if self._is_noise_title(ch.get("title", "")):
                    continue
                ch_kws = ch.get("keywords", []) or []
                summary = ch.get("summary", "") or ""
                if self._is_garbled(summary):
                    continue
                kw_hit = [t for t in all_terms if len(str(t)) >= 2 and t in ch_kws]
                if kw_hit:
                    title = ch.get("title", "")
                    score = len(kw_hit) * 3
                    scored.append((score, f"据《{src_name}》「{title}」：{summary[:150]}"))
        # 按相关度降序，最多取 3 条
        scored.sort(key=lambda x: -x[0])
        parts.extend(s for _, s in scored[:3])
        return "\n\n".join(parts)[:900]

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
