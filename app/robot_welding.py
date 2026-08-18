"""
机器人焊接规则库 — 面向仿真 / 路径规划 / 机器人操控
===================================================
解决手工焊 vs 机器人焊差异：
- 默认焊丝工艺（GMAW/MIG、FCAW），非手工焊条
- 焊丝/送丝/气体/TCP/枪姿态 分解
- 船型焊（PA 位置）最稳定姿态
- 几层几道具体电流电压序列
- 管道/圆弧场景分段焊接策略
"""

from typing import Dict, List, Optional

# ============================================================
# 焊丝选择（按母材）— 机器人 GMAW/FCAW 用焊丝
# ============================================================
_WIRE_MAP = {
    "低碳钢": {"wire": "ER50-6", "diameter": "1.2mm", "gas": "80%Ar+20%CO₂",
              "gas_flow": "15-20 L/min", "mode": "MAG 富氩混合气"},
    "Q345 (16Mn)": {"wire": "ER50-6 / ER70S-6", "diameter": "1.2mm", "gas": "80%Ar+20%CO₂",
                    "gas_flow": "15-20 L/min", "mode": "MAG 富氩混合气"},
    "中碳钢": {"wire": "ER50-6", "diameter": "1.2mm", "gas": "80%Ar+20%CO₂",
              "gas_flow": "15-20 L/min", "mode": "MAG 富氩混合气"},
    "高强钢": {"wire": "ER70-G / ER80-G", "diameter": "1.2mm", "gas": "80%Ar+20%CO₂",
              "gas_flow": "18-22 L/min", "mode": "MAG 富氩混合气"},
    "奥氏体不锈钢": {"wire": "ER308L / ER316L", "diameter": "1.0-1.2mm", "gas": "98%Ar+2%CO₂",
                   "gas_flow": "15-20 L/min", "mode": "MIG 氩气保护"},
    "双相不锈钢": {"wire": "ER2209", "diameter": "1.2mm", "gas": "98%Ar+2%CO₂",
                  "gas_flow": "15-20 L/min", "mode": "MIG 氩气保护"},
    "铝合金": {"wire": "ER5356 / ER4043", "diameter": "1.2mm", "gas": "纯Ar(99.99%)",
               "gas_flow": "18-25 L/min", "mode": "MIG 脉冲"},
}

# 机器人默认工艺（无指定时）
ROBOT_DEFAULT_PROCESS = "GMAW/MIG (熔化极氩弧焊)"


def _wire_for_material(material: str) -> dict:
    if not material:
        return {"wire": "ER50-6", "diameter": "1.2mm", "gas": "80%Ar+20%CO₂",
                "gas_flow": "15-20 L/min", "mode": "MAG 富氩混合气"}
    # 匹配材料键（含基础名）
    for key, val in _WIRE_MAP.items():
        base = key.split("(")[0].strip()
        if material in key or key in material or material == base or base in material:
            return val
    return {"wire": "ER50-6", "diameter": "1.2mm", "gas": "80%Ar+20%CO₂",
            "gas_flow": "15-20 L/min", "mode": "MAG 富氩混合气"}


# ============================================================
# 船型焊 / 位置姿态
# ============================================================
def _ship_position(thickness: float) -> dict:
    """船型焊（PA/1G）最稳定姿态：坡口朝上，熔池水平"""
    return {
        "position": "PA/1G 船型焊",
        "angle": "坡口旋转至 45°±5° 朝上",
        "gun_work_angle": "90°±5°（垂直于坡口面）",
        "gun_travel_angle": "10-15°推枪（前进方向）",
        "note": "工件旋转使焊缝水平，重力不干扰熔池，成形最稳",
    }


def _position_strategy(joint: str = "") -> dict:
    """位置策略（默认船型焊最优）"""
    return {
        "strategy": "优先船型焊(PA)，工件旋转至坡口朝上",
        "fallback": "若无法旋转：平焊(1G)直枪，工作角90°",
        "pipe_fixed": "管道固定焊→分段半圈焊接（6点→12点）",
    }


# ============================================================
# 层道序列（几层几道 + 具体电流电压）— 按板厚
# ============================================================
def pass_sequence(thickness: float, wire_dia: str = "1.2mm", mode: str = "MAG") -> List[dict]:
    """按板厚生成层道序列：每道 电流/电压/焊速/宽度/送丝
    规则：打底焊透 → 填充逐层加宽 → 盖面成形"""
    if thickness is None:
        thickness = 12
    seq = []
    if thickness <= 3:
        seq = [{"pass": "单道(打底+成形)", "layer": 1, "bead": 1,
                "current_a": "80-110A", "voltage_v": "16-18V",
                "speed_cm_min": "30-40", "wire_feed": "4-6 m/min", "bead_width": "3-4mm"}]
    elif thickness <= 6:
        seq = [{"pass": "打底", "layer": 1, "bead": 1,
                "current_a": "100-130A", "voltage_v": "17-19V",
                "speed_cm_min": "28-35", "wire_feed": "5-7 m/min", "bead_width": "4-5mm"},
               {"pass": "盖面", "layer": 2, "bead": 1,
                "current_a": "120-150A", "voltage_v": "19-21V",
                "speed_cm_min": "25-30", "wire_feed": "6-8 m/min", "bead_width": "5-6mm"}]
    elif thickness <= 12:
        seq = [{"pass": "打底", "layer": 1, "bead": 1,
                "current_a": "120-140A", "voltage_v": "18-20V",
                "speed_cm_min": "25-35", "wire_feed": "6-8 m/min", "bead_width": "4-5mm"},
               {"pass": "填充", "layer": 2, "bead": 1,
                "current_a": "160-190A", "voltage_v": "21-24V",
                "speed_cm_min": "28-35", "wire_feed": "7-9 m/min", "bead_width": "6-7mm"},
               {"pass": "盖面", "layer": 3, "bead": 1,
                "current_a": "180-210A", "voltage_v": "23-25V",
                "speed_cm_min": "25-30", "wire_feed": "8-10 m/min", "bead_width": "7-9mm"}]
    elif thickness <= 25:
        seq = [{"pass": "打底", "layer": 1, "bead": 1,
                "current_a": "130-160A", "voltage_v": "19-22V",
                "speed_cm_min": "25-30", "wire_feed": "7-9 m/min", "bead_width": "5-6mm"},
               {"pass": "填充1", "layer": 2, "bead": 1,
                "current_a": "180-220A", "voltage_v": "23-25V",
                "speed_cm_min": "28-35", "wire_feed": "8-10 m/min", "bead_width": "7-8mm"},
               {"pass": "填充2", "layer": 3, "bead": 2,
                "current_a": "190-230A", "voltage_v": "24-26V",
                "speed_cm_min": "28-35", "wire_feed": "8-11 m/min", "bead_width": "6-7mm"},
               {"pass": "盖面", "layer": 4, "bead": 2,
                "current_a": "180-210A", "voltage_v": "23-25V",
                "speed_cm_min": "25-30", "wire_feed": "8-10 m/min", "bead_width": "6-8mm"}]
    else:
        seq = [{"pass": "打底", "layer": 1, "bead": 1,
                "current_a": "140-170A", "voltage_v": "20-22V",
                "speed_cm_min": "22-28", "wire_feed": "8-10 m/min", "bead_width": "5-6mm"},
               {"pass": "填充1-3", "layer": "2-4", "bead": "2-3",
                "current_a": "200-240A", "voltage_v": "24-27V",
                "speed_cm_min": "25-32", "wire_feed": "9-12 m/min", "bead_width": "6-8mm"},
               {"pass": "盖面", "layer": "5", "bead": "2-3",
                "current_a": "190-220A", "voltage_v": "23-25V",
                "speed_cm_min": "22-28", "wire_feed": "8-11 m/min", "bead_width": "6-8mm"}]
    return seq


# ============================================================
# TCP / 导电嘴 / 干伸长
# ============================================================
def _tcp(mode: str = "MAG") -> dict:
    """导电嘴/TCP 参数（机器人坐标参考）"""
    if mode == "MIG":
        return {"contact_tip_to_work": "10-15mm", "stick_out": "12-15mm",
                "approach": "焊枪轴线垂直坡口，TCP 指向坡口中心"}
    if mode == "脉冲":
        return {"contact_tip_to_work": "10-14mm", "stick_out": "12-15mm",
                "approach": "焊枪轴线垂直坡口，TCP 指向坡口中心"}
    return {"contact_tip_to_work": "12-15mm", "stick_out": "15-18mm",
            "approach": "焊枪轴线垂直坡口，TCP 指向坡口中心"}


# ============================================================
# 管道 / 圆弧场景
# ============================================================
def _pipe_strategy(is_pipe: bool = False, is_fixed: bool = True) -> dict:
    """管道焊接策略"""
    if not is_pipe:
        return {}
    if not is_fixed:
        return {
            "pipe_mode": "管道旋转焊",
            "strategy": "工件旋转，焊枪固定（最稳定，重力恒定）",
            "rotation": "旋转速度 = 线速度 / 管径周长（cm/min ÷ πD）",
            "gun_pose": "船型焊位，焊枪固定近似垂直",
        }
    return {
        "pipe_mode": "管道固定焊",
        "strategy": "焊枪沿圆周分段焊接：6点→12点两个半圈",
        "segments": [
            {"seg": "上半圈(12点→6点)", "position": "斜平焊(PC/2G)转仰焊",
             "gun_work": "75-85°", "gun_travel": "10-15°",
             "current_adj": "比平焊降 10-15%"},
            {"seg": "下半圈(6点→12点)", "position": "斜仰焊转斜立焊",
             "gun_work": "85-95°", "gun_travel": "10-15°",
             "current_adj": "比平焊降 10-15%"},
        ],
        "6G_note": "45°固定管(6G)：分两个半圈，6点起焊→12点收弧，每半圈含斜仰/斜立/斜平",
    }


# ============================================================
# 综合构建机器人字段
# ============================================================
def build_robot_block(material: str, thickness: float, process: str,
                      is_pipe: bool = False, pipe_fixed: bool = True) -> dict:
    """构建工艺卡片 robot 字段块"""
    wire = _wire_for_material(material)
    seq = pass_sequence(thickness, wire.get("diameter", "1.2mm"), wire.get("mode", "MAG"))
    tcp = _tcp(wire.get("mode", "MAG"))
    return {
        "weld_mode": wire.get("mode", "MAG"),
        "wire": {"type": wire.get("wire", ""), "diameter": wire.get("diameter", "1.2mm")},
        "gas": {"type": wire.get("gas", ""), "flow": wire.get("gas_flow", "15-20 L/min")},
        "tcp": tcp,
        "gun_pose": {
            "work_angle": "75-85°（垂直坡口偏工作角）",
            "travel_angle": "10-15° 推枪",
            "axis_rotation": "0°（保持焊枪轴线指向熔池）",
            "note": "工作角绕焊接方向，行走角沿焊接方向，绕枪轴保持0°",
        },
        "ship_position": _ship_position(thickness),
        "pass_sequence": seq,
        "pipe": _pipe_strategy(is_pipe, pipe_fixed),
    }


# ============================================================
# 卡诺普机器人焊接真值库（weld_cases.json）
# ============================================================
import json as _json
from pathlib import Path as _Path

_weld_cases = None


def _load_weld_cases():
    """加载卡诺普真值库（懒加载）"""
    global _weld_cases
    if _weld_cases is not None:
        return _weld_cases
    p = _Path(__file__).resolve().parent.parent / "data" / "weld_cases.json"
    if p.exists():
        try:
            _weld_cases = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            _weld_cases = []
    else:
        _weld_cases = []
    return _weld_cases


def _normalize_material(material: str) -> str:
    """材料归一化：低碳钢/碳钢/镀锌板/不锈钢 映射"""
    if not material:
        return ""
    m = material.strip()
    if "镀锌" in m:
        return "镀锌板"
    if "不锈钢" in m or "奥氏体" in m or "双相" in m:
        return "不锈钢"
    if "碳钢" in m or "低碳钢" in m or "Q" in m or "低合金" in m or "高强" in m:
        return "碳钢"
    if "铝" in m:
        return "铝合金"
    return m


def _normalize_joint(joint: str) -> str:
    """焊缝形式归一化：船型/平拼接/平搭接/平内角/立内角/平外角/立外角 等"""
    if not joint:
        return ""
    j = joint.strip()
    if "船" in j:
        return "船型"
    if "平" in j:
        if "拼" in j or "对" in j:
            return "平拼接"
        if "搭" in j:
            return "平搭接"
        if "内角" in j:
            return "平内角"
        if "外角" in j:
            return "平外角"
        if "接" in j:
            return "平拼接"
    if "立" in j:
        if "拼" in j or "对" in j:
            return "立拼接"
        if "搭" in j:
            return "立搭接"
        if "内角" in j:
            return "立内角"
        if "外角" in j:
            return "立外角"
    # 无平/立前缀的兜底：默认平焊位置（搭接/内角/外角/拼/对接）
    if "搭" in j:
        return "平搭接"
    if "内角" in j:
        return "平内角"
    if "外角" in j:
        return "平外角"
    if "拼" in j or "对" in j or "接" in j:
        return "平拼接"
    return j


def find_weld_case(material: str, thickness, joint: str = "", variant: str = "基准") -> Optional[dict]:
    """查卡诺普真值：材料+板厚（+焊缝形式+variant）→ 最匹配的工艺参数。
    variant: 基准/电流小/电流大/电压小/电压大/速度快/速度慢/角度范围。
    优先精确匹配 variant，无则回退基准。"""
    cases = _load_weld_cases()
    if not cases:
        return None
    mat = _normalize_material(material)
    jt = _normalize_joint(joint)
    v_target = variant or "基准"

    def _search(variant_filter: str):
        best = None
        for c in cases:
            if c.get("material") != mat:
                continue
            if variant_filter and c.get("variant") != variant_filter:
                continue
            if jt and c.get("joint") != jt:
                continue
            ct = c.get("thickness")
            if ct is None or thickness is None:
                continue
            delta = abs(ct - thickness)
            if best is None or delta < best[0]:
                best = (delta, c)
        return best

    # 优先目标 variant，无则回退基准
    best = _search(v_target)
    if best is None and v_target != "基准":
        best = _search("基准")
    if best and best[0] <= 2.0:  # 板厚偏差≤2mm 视为命中
        return best[1]
    return None
