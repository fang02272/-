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
def _find_rec(param_match: dict, name: str) -> str:
    """从推荐行中取值（匹配参数名包含关系）"""
    for r in (param_match or {}).get("recommendations", []):
        rp = str(r.get("param", ""))
        if name in rp or rp in name:
            return str(r.get("value", ""))
    return ""


def _process_params(process_key: str) -> dict:
    return WELDING_PROCESS_PARAMS.get(process_key, {}) or {}


def _material_params(material_key: str) -> dict:
    return MATERIAL_PARAM_MAP.get(material_key, {}) or {}


def build_process_card(extracted: dict, param_match: dict) -> Optional[dict]:
    """由 意图抽取 + 参数匹配 结果构建结构化工艺卡片。
    返回机器可读 dict；数据不足时返回 None（调用方回退到常规回答）。"""
    if not param_match or not param_match.get("matched"):
        return None

    thickness = extracted.get("thickness")
    material = param_match.get("material") or (extracted.get("materials") or [None])[0]
    process = param_match.get("process")
    process_assumed = False
    # 工艺缺省：有材料/板厚但未指定工艺时，默认 焊条电弧焊（机器人焊接基线），并标注 assumed
    if not process and (thickness is not None or material):
        process = "SMAW (焊条电弧焊)"
        process_assumed = True
    electrode = param_match.get("electrode")
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
    travel_speed = _find_rec(param_match, "焊速") or pp.get("焊速范围", "")
    current = _find_rec(param_match, "电流") or pp.get("电流范围", "")
    voltage = _find_rec(param_match, "电压") or pp.get("电压范围", "")
    preheat = _find_rec(param_match, "预热") or (mp.get("预热") if isinstance(mp.get("预热"), str) else "")
    interpass = _find_rec(param_match, "层间温度") or mp.get("层间温度", "")
    postheat = _find_rec(param_match, "后热") or mp.get("后热", "")
    shielding = ""
    if "GTAW" in (process or "") or "GMAW" in (process or "") or "PAW" in (process or ""):
        shielding = pp.get("保护方式", "Ar 气体保护") or "Ar 气体保护"
    elif "FCAW" in (process or ""):
        shielding = pp.get("保护方式", "药芯自保护或CO₂") or "药芯自保护或CO₂"
    elif "SAW" in (process or ""):
        shielding = pp.get("保护方式", "焊剂保护（HJ431/SJ101）") or "焊剂保护（HJ431/SJ101）"
    else:
        shielding = pp.get("保护方式", "焊条药皮造渣造气") or "焊条药皮造渣造气"

    return {
        "base_material": material,
        "thickness_mm": thickness,
        "process": process,
        "process_assumed": process_assumed,
        "groove": _groove_rule(thickness),
        "joint_gap_mm": _gap_rule(thickness),
        "welding_position": _POSITION_HINT,
        "consumables": _find_rec(param_match, "推荐焊材") or "",
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
        "application": param_match.get("application", ""),
    }


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
