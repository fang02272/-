"""
工艺卡片生成器 — 结构化、机器可读的焊接工艺规范
================================================
面向仿真、机器人路径规划、机器人操控（减少示教时间）：
- 输出确定性 JSON（母材/板厚/工艺/坡口/焊材/电参数/焊道/机器人参数/质量评估）
- 电参数/热参数优先取 基座三表（WELDING_PROCESS_PARAMS / MATERIAL_PARAM_MAP / ELECTRODE_PARAM_TABLE）
- 坡口/间隙/焊道/机器人参数/缺陷预防 由规则知识库生成
"""

from typing import Dict, Optional

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

try:
    from app.robot_welding import build_robot_block, ROBOT_DEFAULT_PROCESS, find_weld_case
except ImportError:
    build_robot_block = None
    ROBOT_DEFAULT_PROCESS = "GMAW/MIG (熔化极氩弧焊)"
    find_weld_case = None


# ============================================================
# 规则知识库
# ============================================================
def _groove_rule(t: float) -> str:
    """坡口形式（按板厚，标准焊接经验）"""
    if t is None:
        return "按板厚确定（V/X坡口）"
    if t <= 3:
        return "不开坡口（薄板单面焊）"
    if t <= 6:
        return "不开坡口或单V坡口（钝边≤1mm）"
    if t <= 12:
        return "单V坡口 60°±5°（钝边1-2mm）"
    if t <= 25:
        return "X坡口 60°±5°（钝边1-2mm）"
    return "X坡口 60°±5°（双面多层焊，钝边1-2mm）"


def _gap_rule(t: float) -> str:
    if t is None:
        return "1-3mm"
    if t <= 3:
        return "0-1mm"
    if t <= 6:
        return "1-2mm"
    if t <= 12:
        return "2-3mm"
    return "2-3mm（装配错边量≤0.5mm）"


def _layer_rule(t: float) -> str:
    if t is None:
        return "按板厚分层（每层约3-5mm焊肉）"
    if t <= 3:
        return "1层1道"
    if t <= 6:
        return "1-2层"
    if t <= 12:
        return "2层（打底+盖面）"
    if t <= 25:
        return "3-4层（打底/填充/盖面）"
    return "5层以上（多层多道，层间清渣）"


def _weave_rule(t: float, electrode_mm: Optional[str] = None) -> str:
    base = "锯齿形运条"
    if electrode_mm:
        d = electrode_mm.replace("Φ", "").replace("mm", "").strip()
        try:
            w = float(d) * 3
            return f"{base}，摆动宽度≤{w:g}mm（≤3倍焊条直径）"
        except ValueError:
            pass
    if t and t > 12:
        return f"{base}，摆动宽度6-10mm（多道焊每道宽度≤3倍焊条直径）"
    return f"{base}，摆动宽度4-6mm"


def _gun_angle(process: str) -> str:
    """焊枪/焊条倾角（机器人位姿）"""
    if "SMAW" in process:
        return "70-80°（后倾5-10°）"
    if "GTAW" in process:
        return "75-85°（钨极与工件夹角）"
    if "GMAW" in process or "FCAW" in process:
        return "80-90°（平焊推枪10-15°）"
    if "SAW" in process:
        return "75-85°（焊丝后倾）"
    if "PAW" in process:
        return "80-90°"
    return "75-85°"


def _stick_out(process: str) -> str:
    """干伸长 / 导电嘴距离（机器人TCP参考）"""
    if "SMAW" in process:
        return "焊条干伸长 15-20mm"
    if "GTAW" in process:
        return "钨极伸出长度 3-5mm（保护气覆盖）"
    if "GMAW" in process:
        return "导电嘴到工件 10-15mm"
    if "FCAW" in process:
        return "导电嘴到工件 15-25mm（药芯焊丝）"
    if "SAW" in process:
        return "焊丝伸出 25-40mm"
    if "PAW" in process:
        return "喷嘴到工件 3-8mm"
    return "10-20mm"


# 各工艺机器人默认参数
_ROBOT_BASE = {
    "SMAW (焊条电弧焊)": {"weave_freq": "0.5-1.0 Hz", "weave_dwell": "两侧停留0.3-0.5s"},
    "GTAW/TIG (钨极氩弧焊)": {"weave_freq": "1.0-2.0 Hz", "weave_dwell": "无（连续送丝）"},
    "GMAW/MIG (熔化极氩弧焊)": {"weave_freq": "1.0-2.0 Hz", "weave_dwell": "两侧停留0.2-0.3s"},
    "FCAW (药芯焊丝CO₂焊)": {"weave_freq": "1.0-2.0 Hz", "weave_dwell": "两侧停留0.2-0.3s"},
    "PAW (等离子弧焊)": {"weave_freq": "1.0-3.0 Hz", "weave_dwell": "无"},
}


# 缺陷预防表（质量评估）——按工艺
_DEFECT_PREVENTION = {
    "SMAW (焊条电弧焊)": [
        {"defect": "烧穿", "cause": "电流过大、间隙过大", "prevention": "控制电流在推荐范围内，间隙≤2mm，薄板用短弧"},
        {"defect": "咬边", "cause": "电流过大、运条不当", "prevention": "减小电流，焊条角度正确，适当摆动，两侧停留"},
        {"defect": "气孔", "cause": "焊件不洁、电弧过长", "prevention": "清理焊件，短弧操作，焊条按要求烘干"},
        {"defect": "未熔合", "cause": "电流过小、速度过快", "prevention": "适当增大电流，降低焊接速度，控制熔池边缘"},
        {"defect": "变形", "cause": "热输入过大", "prevention": "采用对称焊、分段退焊法，控制层间温度"},
    ],
    "GTAW/TIG (钨极氩弧焊)": [
        {"defect": "气孔", "cause": "保护气不足、焊件不洁", "prevention": "加大气体流量(10-15L/min)，清理焊件，提前送气/滞后停气"},
        {"defect": "钨极烧损", "cause": "电流过大、操作不当", "prevention": "按板厚选钨极直径，控制电流，避免短路"},
        {"defect": "咬边", "cause": "电流过大、焊速过快", "prevention": "减小电流，控制焊接速度，加填充丝"},
        {"defect": "未熔合", "cause": "热输入不足", "prevention": "增大电流或降低焊速，保证熔透"},
    ],
    "GMAW/MIG (熔化极氩弧焊)": [
        {"defect": "气孔", "cause": "保护气不足、风速大", "prevention": "增大气体流量(15-20L/min)，防风，清理焊件"},
        {"defect": "飞溅大", "cause": "电压/电流不匹配", "prevention": "调节电压电流匹配，采用短路/脉冲过渡"},
        {"defect": "未熔合", "cause": "热输入不足、干伸长过大", "prevention": "控制干伸长10-15mm，适当增大电流"},
        {"defect": "咬边", "cause": "焊速过快", "prevention": "降低焊接速度，控制摆动"},
    ],
    "FCAW (药芯焊丝CO₂焊)": [
        {"defect": "气孔", "cause": "CO₂气流量不足、焊件有锈", "prevention": "气体流量15-20L/min，清理坡口"},
        {"defect": "飞溅", "cause": "参数不当", "prevention": "调节电压电流，药芯焊丝选择合适牌号"},
        {"defect": "夹渣", "cause": "多层焊清渣不净", "prevention": "每层焊后彻底清渣"},
        {"defect": "变形", "cause": "热输入大", "prevention": "分段焊、对称焊，控制线能量"},
    ],
    "SAW (埋弧自动焊)": [
        {"defect": "夹渣", "cause": "焊剂清洁度差、焊速快", "prevention": "使用洁净焊剂，控制焊速，注意焊剂覆盖"},
        {"defect": "气孔", "cause": "坡口有锈、焊剂受潮", "prevention": "清理坡口，焊剂烘干，防止焊剂层过厚"},
        {"defect": "裂纹", "cause": "拘束大、预热不足", "prevention": "按板厚预热，控制冷却速度"},
        {"defect": "焊偏", "cause": "焊丝对中不良", "prevention": "调整焊丝对中，使用导向"},
    ],
    "PAW (等离子弧焊)": [
        {"defect": "气孔", "cause": "保护气/离子气流量不当", "prevention": "调整离子气与保护气比例，清理焊件"},
        {"defect": "双弧", "cause": "喷嘴损坏、电流过大", "prevention": "检查喷嘴，控制电流上限"},
        {"defect": "未熔透", "cause": "穿孔焊电流不足", "prevention": "增大电流，保证穿孔稳定"},
    ],
}

_DEFECT_FALLBACK = [
    {"defect": "气孔", "cause": "焊件不洁、保护不良", "prevention": "清理焊件，保证气体保护/药皮干燥"},
    {"defect": "未熔合", "cause": "热输入不足", "prevention": "适当增大电流，降低焊接速度"},
    {"defect": "咬边", "cause": "参数不当、运条不当", "prevention": "减小电流，规范运条/摆动"},
    {"defect": "变形", "cause": "热输入过大", "prevention": "对称焊、分段退焊，控制层间温度"},
]

# 焊接位置（机器人姿态规划参考）
_POSITION_HINT = "平焊(1G)为主，建议机器人姿态：焊枪垂直于坡口、倾角见gun_angle"


# ============================================================
# 卡片构建
# ============================================================
def _find_rec_entry(param_match: dict, name: str) -> dict:
    """从推荐行中取完整记录（保留参数来源）。"""
    for r in (param_match or {}).get("recommendations", []):
        rp = str(r.get("param", ""))
        if name in rp or rp in name:
            return r
    return {}


_SOURCE_LABELS = {
    "user_input": "用户输入",
    "measured": "卡诺普实测",
    "knowledge_base": "知识库参数",
    "rule_recommendation": "工艺规则推荐",
    "system_default": "系统默认，需确认",
    "pending": "待补充/待确认",
}


def _source_info(source_type: str, detail: str = "") -> dict:
    """构造统一的参数来源标识，供前端、打印和机器人接口共用。"""
    return {
        "type": source_type,
        "label": _SOURCE_LABELS[source_type],
        "detail": detail,
        "requires_confirmation": source_type in ("system_default", "pending"),
    }


def _input_completeness(extracted: dict) -> dict:
    """按生成可靠工艺卡所需的母材、板厚、工艺检查输入完整性。"""
    specs = (
        ("material", "母材", bool(extracted.get("materials")), (extracted.get("materials") or [None])[0]),
        ("thickness", "板厚", extracted.get("thickness") is not None, extracted.get("thickness")),
        ("process", "焊接工艺", bool(extracted.get("process")), extracted.get("process")),
    )
    provided = [
        {"field": field, "label": label, "value": value}
        for field, label, present, value in specs if present
    ]
    missing = [
        {"field": field, "label": label, "reason": f"未从问题中识别到{label}"}
        for field, label, present, _ in specs if not present
    ]
    score = len(provided) / len(specs)
    status = "complete" if not missing else ("partial" if provided else "insufficient")
    confidence = "high" if score == 1 else ("medium" if score >= 2 / 3 else "low")
    assumptions = []
    if any(item["field"] == "material" for item in missing):
        assumptions.append("母材未知，材料相关的预热、焊材与热处理参数只能作为通用建议。")
    if any(item["field"] == "thickness" for item in missing):
        assumptions.append("板厚未知，坡口、装配间隙和层道规划采用通用规则。")
    if any(item["field"] == "process" for item in missing):
        assumptions.append("工艺未知，暂用 GMAW/MIG 机器人焊接基线生成参数。")
    return {
        "score": round(score, 2),
        "status": status,
        "confidence": {"level": confidence, "score": round(score, 2)},
        "provided_fields": provided,
        "missing_fields": missing,
        "assumptions": assumptions,
        "requires_confirmation": bool(missing),
        "message": (
            "输入完整，可进入工艺评审。"
            if not missing else
            "当前卡片包含假设或默认值，请补充缺失信息后重新生成，生产使用前必须确认。"
        ),
    }


def _process_params(process_key: str) -> dict:
    return WELDING_PROCESS_PARAMS.get(process_key, {}) or {}


def _material_params(material_key: str) -> dict:
    return MATERIAL_PARAM_MAP.get(material_key, {}) or {}


def _guess_joint(query_text: str) -> str:
    """从查询原文猜焊缝形式（卡诺普真值匹配用）"""
    if not query_text:
        return ""
    if "船" in query_text:
        return "船型"
    if "内角" in query_text:
        return "内角"
    if "外角" in query_text:
        return "外角"
    if "搭接" in query_text:
        return "搭接"
    if "拼" in query_text or "对接" in query_text:
        return "平拼接"
    return ""


def build_process_card(extracted: dict, param_match: dict, query: str = "") -> Optional[dict]:
    """由 意图抽取 + 参数匹配 结果构建结构化工艺卡片。
    返回机器可读 dict；数据不足时返回 None（调用方回退到常规回答）。"""
    # 有材料+板厚 且 真值库能命中 → 即使 match_parameters 未匹配也生成卡片
    thickness = extracted.get("thickness")
    material0 = (param_match or {}).get("material") or (extracted.get("materials") or [None])[0]
    _has_weld = False
    if find_weld_case is not None and material0 and thickness is not None:
        try:
            _has_weld = find_weld_case(material0, thickness, _guess_joint(query), "基准") is not None
        except Exception:
            _has_weld = False
    if (not param_match or not param_match.get("matched")) and not _has_weld:
        return None

    completeness = _input_completeness(extracted)
    thickness = extracted.get("thickness")
    material = param_match.get("material") or (extracted.get("materials") or [None])[0]
    process = param_match.get("process")
    process_assumed = False
    # 工艺缺省：有材料/板厚但未指定工艺时，默认 GMAW/MIG（机器人焊接基线），并标注 assumed
    if not process and (thickness is not None or material or extracted.get("param_terms")):
        process = ROBOT_DEFAULT_PROCESS
        process_assumed = True
    electrode = extracted.get("electrode") or param_match.get("electrode")
    mp = _material_params(material) if material else {}
    pp = _process_params(process) if process else {}

    # 焊条直径（从 electrode 或 按板厚选）——统一为纯数字 mm
    electrode_mm = None
    if electrode:
        electrode_mm = electrode.replace("焊条", "").replace("Φ", "").replace("mm", "").strip()
    if not electrode_mm and thickness is not None:
        # 按板厚匹配电极参数表
        for ek, ep in ELECTRODE_PARAM_TABLE.items():
            tr = re_range(ep.get("适用板厚", ""))
            if tr and tr[0] <= thickness <= tr[1]:
                electrode_mm = ek.replace("焊条", "").replace("Φ", "").replace("mm", "").strip()
                break

    robot = dict(_ROBOT_BASE.get(process or "", {}))

    # [v2.6] 卡诺普真值优先：材料+板厚+焊缝形式 → 真实电流/电压/速度/角度
    # 焊缝形式优先从 query 原文提取（match_parameters 不返回 joint）
    query_for_variant = query or ""
    _joint_guess = param_match.get("joint") or _guess_joint(query_for_variant)
    if any(k in query_for_variant for k in ("电流大", "电流最大", "电流上限", "大电流")):
        _variant = "电流大"
    elif any(k in query_for_variant for k in ("电流小", "电流最小", "小电流")):
        _variant = "电流小"
    elif any(k in query_for_variant for k in ("电压大", "电压高")):
        _variant = "电压大"
    elif any(k in query_for_variant for k in ("电压小", "电压低")):
        _variant = "电压小"
    elif any(k in query_for_variant for k in ("速度快", "焊速快", "高速焊")):
        _variant = "速度快"
    elif any(k in query_for_variant for k in ("速度慢", "焊速慢", "低速焊")):
        _variant = "速度慢"
    else:
        _variant = "基准"
    weld_case = None
    if find_weld_case is not None:
        try:
            weld_case = find_weld_case(material, thickness, _joint_guess, _variant)
        except Exception:
            weld_case = None

    current_rec = _find_rec_entry(param_match, "电流")
    voltage_rec = _find_rec_entry(param_match, "电压")
    speed_rec = _find_rec_entry(param_match, "焊速")
    preheat_rec = _find_rec_entry(param_match, "预热")
    interpass_rec = _find_rec_entry(param_match, "层间温度")
    postheat_rec = _find_rec_entry(param_match, "后热")
    consumables_rec = _find_rec_entry(param_match, "推荐焊材")

    travel_speed = str(speed_rec.get("value", "")) or pp.get("焊速范围", "")
    current = str(current_rec.get("value", "")) or pp.get("电流范围", "")
    voltage = str(voltage_rec.get("value", "")) or pp.get("电压范围", "")
    if weld_case:
        if weld_case.get("current"):
            current = f"{weld_case['current']:g}A"
        if weld_case.get("voltage"):
            voltage = f"{weld_case['voltage']:g}V"
        if weld_case.get("speed"):
            travel_speed = f"{weld_case['speed']:g} cm/min"
    preheat = str(preheat_rec.get("value", "")) or (mp.get("预热") if isinstance(mp.get("预热"), str) else "")
    interpass = str(interpass_rec.get("value", "")) or mp.get("层间温度", "")
    postheat = str(postheat_rec.get("value", "")) or mp.get("后热", "")
    shielding = ""
    if "GTAW" in (process or "") or "GMAW" in (process or "") or "PAW" in (process or ""):
        shielding = pp.get("保护方式", "Ar 气体保护") or "Ar 气体保护"
    elif "FCAW" in (process or ""):
        shielding = pp.get("保护方式", "药芯自保护或CO₂") or "药芯自保护或CO₂"
    elif "SAW" in (process or ""):
        shielding = pp.get("保护方式", "焊剂保护（HJ431/SJ101）") or "焊剂保护（HJ431/SJ101）"
    else:
        shielding = pp.get("保护方式", "焊条药皮造渣造气") or "焊条药皮造渣造气"

    def recommendation_source(entry: dict, fallback_detail: str = "") -> dict:
        if entry:
            return _source_info("knowledge_base", str(entry.get("source", "")))
        if fallback_detail:
            return _source_info("knowledge_base", fallback_detail)
        return _source_info("pending")

    material_source = (
        _source_info("user_input", "问题中识别的母材") if extracted.get("materials") else
        (_source_info("knowledge_base", "材料参数表匹配") if material else _source_info("pending"))
    )
    thickness_source = (
        _source_info("user_input", "问题中识别的板厚") if thickness is not None else _source_info("pending")
    )
    process_source = (
        _source_info("user_input", "问题中识别的焊接工艺") if extracted.get("process") else
        (_source_info("system_default", "GMAW/MIG 机器人焊接基线") if process_assumed else _source_info("pending"))
    )
    current_source = recommendation_source(current_rec, process if current else "")
    voltage_source = recommendation_source(voltage_rec, process if voltage else "")
    speed_source = recommendation_source(speed_rec, process if travel_speed else "")
    if weld_case:
        if weld_case.get("current"):
            current_source = _source_info("measured", "材料+板厚+焊缝形式匹配")
        if weld_case.get("voltage"):
            voltage_source = _source_info("measured", "材料+板厚+焊缝形式匹配")
        if weld_case.get("speed"):
            speed_source = _source_info("measured", "材料+板厚+焊缝形式匹配")

    thickness_rule_source = (
        _source_info("rule_recommendation", "按板厚规则计算")
        if thickness is not None else _source_info("system_default", "板厚缺失，采用通用规则")
    )
    electrode_source = (
        _source_info("user_input", "问题中指定的焊条/焊丝直径") if extracted.get("electrode") else
        (_source_info("rule_recommendation", "电极参数表按板厚匹配") if electrode_mm else _source_info("pending"))
    )
    consumables_source = recommendation_source(consumables_rec)
    shielding_source = (
        _source_info("knowledge_base", process or "工艺参数表")
        if pp.get("保护方式") else _source_info("system_default", "按工艺类型给出的通用保护方式")
    )
    preheat_source = recommendation_source(preheat_rec, material if preheat else "")
    interpass_source = recommendation_source(interpass_rec, material if interpass else "")
    postheat_source = recommendation_source(postheat_rec, material if postheat else "")
    if not preheat:
        preheat_source = _source_info("system_default", "通用热管理建议")
    if not interpass:
        interpass_source = _source_info("system_default", "通用热管理建议")
    if not postheat:
        postheat_source = _source_info("system_default", "通用热管理建议")

    parameter_sources = {
        "base_material": material_source,
        "thickness_mm": thickness_source,
        "process": process_source,
        "groove": thickness_rule_source,
        "joint_gap_mm": thickness_rule_source,
        "welding_position": _source_info("rule_recommendation", "机器人焊接位置规则"),
        "consumables": consumables_source,
        "electrode_diameter": electrode_source,
        "electrical.current_a": current_source,
        "electrical.voltage_v": voltage_source,
        "electrical.travel_speed_cm_min": speed_source,
        "thermal.preheat": preheat_source,
        "thermal.interpass_temp": interpass_source,
        "thermal.postheat": postheat_source,
        "shielding_gas": shielding_source,
        "pass_plan.layers_passes": thickness_rule_source,
        "pass_plan.weaving": thickness_rule_source,
        "robot_params.travel_speed": speed_source,
        "robot_params.gun_angle": _source_info("rule_recommendation", "按焊接工艺计算"),
        "robot_params.stick_out": _source_info("rule_recommendation", "按焊接工艺计算"),
    }
    parameter_labels = {
        "process": "焊接工艺",
        "consumables": "焊材",
        "electrode_diameter": "焊条/焊丝直径",
        "electrical.current_a": "焊接电流",
        "electrical.voltage_v": "电弧电压",
        "electrical.travel_speed_cm_min": "焊接速度",
        "thermal.preheat": "预热",
        "thermal.interpass_temp": "层间温度",
        "thermal.postheat": "后热",
        "shielding_gas": "保护气体",
        "groove": "坡口形式",
        "joint_gap_mm": "装配间隙",
        "pass_plan.layers_passes": "层道规划",
    }
    pending_items = [
        {
            "parameter": path,
            "label": parameter_labels.get(path, path),
            "source": source["label"],
            "detail": source.get("detail", ""),
        }
        for path, source in parameter_sources.items()
        if source.get("requires_confirmation") and path in parameter_labels
    ]
    completeness["pending_confirmation_items"] = pending_items
    completeness["requires_confirmation"] = bool(completeness["missing_fields"] or pending_items)
    if pending_items and not completeness["missing_fields"]:
        completeness["message"] = "输入项完整，但卡片仍含默认值或待定参数，生产使用前必须确认。"

    return {
        "base_material": material,
        "thickness_mm": thickness,
        "process": process,
        "process_assumed": process_assumed,
        "input_completeness": completeness,
        "parameter_sources": parameter_sources,
        "groove": _groove_rule(thickness),
        "joint_gap_mm": _gap_rule(thickness),
        "welding_position": _POSITION_HINT,
        "consumables": str(consumables_rec.get("value", "")) or "",
        "electrode_diameter": f"Φ{electrode_mm}mm" if electrode_mm else "",
        "electrical": {
            "current_a": current,
            "voltage_v": voltage,
            "travel_speed_cm_min": travel_speed,
        },
        "thermal": {
            "preheat": preheat or "按母材牌号与板厚确定（一般不需预热）",
            "interpass_temp": interpass or "≤250°C",
            "postheat": postheat or "一般不需要（厚板受压件按规范）",
        },
        "shielding_gas": shielding,
        "joint_prep": {
            "cleaning": "焊前清除坡口两侧20mm范围内的油污、锈蚀、水分",
            "tack_weld": "采用与正式焊缝相同的焊材，定位焊缝长度10-15mm，间距200-300mm",
            "gap": _gap_rule(thickness),
        },
        "pass_plan": {
            "layers_passes": _layer_rule(thickness),
            "weaving": _weave_rule(thickness, electrode_mm),
        },
        "robot_params": {
            "travel_speed": travel_speed,
            "gun_angle": _gun_angle(process or ""),
            "stick_out": _stick_out(process or ""),
            "weave_width": _weave_rule(thickness, electrode_mm),
            "weave_frequency": robot.get("weave_freq", "1.0 Hz"),
            "weave_dwell": robot.get("weave_dwell", ""),
        },
        "quality": {
            "checks": _DEFECT_PREVENTION.get(process or "", _DEFECT_FALLBACK),
            "inspection": "外观检验（咬边/裂纹/焊瘤）+ 按需无损检测（RT/UT/PT）",
        },
        "equipment": _load_equipment(),
        "robot": _build_robot_field(material, thickness, process, query, weld_case),
        "application": param_match.get("application", ""),
    }


def _build_robot_field(material, thickness, process, query_text: str = "", weld_case: dict = None):
    """构建机器人焊接字段（robot block）：焊丝/TCP/枪姿态/船型焊/层道/管道策略。
    weld_case: 卡诺普真值（优先用其焊枪角度/摆幅/频率）。"""
    if build_robot_block is None:
        return {}
    try:
        # 管道/圆弧检测（从用户查询原文）
        query_text = query_text or ""
        is_pipe = any(k in query_text for k in ("管道", "管", "圆弧", "圆管", "固定管", "6G", "5G", "45°管"))
        pipe_fixed = not any(k in query_text for k in ("旋转", "滚轮", "转动"))
        block = build_robot_block(material, thickness, process, is_pipe, pipe_fixed)
        # 卡诺普真值覆盖：焊枪角度/摆幅/频率
        if weld_case and isinstance(block, dict):
            if weld_case.get("gun_angle_fb") is not None or weld_case.get("gun_angle_lr") is not None:
                fb = weld_case.get("gun_angle_fb")
                lr = weld_case.get("gun_angle_lr")
                block["gun_pose"] = {
                    "work_angle": f"{fb:g}°" if fb is not None else "80°",
                    "travel_angle": f"{lr:g}°" if lr is not None else "90°",
                    "axis_rotation": "0°",
                    "source": "卡诺普实测",
                }
            if weld_case.get("weave_width") is not None:
                block.setdefault("robot_params", {})["weave_width"] = f"{weld_case['weave_width']:g}mm"
            if weld_case.get("weave_freq") is not None:
                block.setdefault("robot_params", {})["weave_frequency"] = f"{weld_case['weave_freq']:g} Hz"
            block["data_source"] = "卡诺普实测" if weld_case.get("current") else "规则经验值"
        return block
    except Exception:
        return {}


def _load_equipment() -> dict:
    """读取 config.yaml 的机器人装备配置（工艺卡片引用）"""
    try:
        from app.llm_service import load_config
        cfg = load_config()
        return cfg.get("equipment", {}) or {}
    except Exception:
        return {}


def re_range(s: str):
    """解析 '3-12mm' 区间，返回 (min, max) 或 None"""
    if not s:
        return None
    import re
    nums = re.findall(r'\d+(?:\.\d+)?', s)
    if len(nums) >= 2:
        try:
            return float(nums[0]), float(nums[1])
        except ValueError:
            return None
    return None
