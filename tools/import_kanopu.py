# -*- coding: utf-8 -*-
"""导入卡诺普机器人焊接工艺图谱 Excel → 真值库 weld_cases.json

用法:
  python tools/import_kanopu.py <Excel路径>
  例: python tools/import_kanopu.py "C:/xxx/机器人焊接工艺图谱生成(实际电压11.14).xlsx"

产出:
  data/weld_cases.json — 机器人焊接真值库（材料/板厚/焊缝形式 → 电流/电压/速度/干伸长/角度）
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_angle(v):
    """焊枪角度：可能为数字 80 或字符串 '80'，转 float；None 保留"""
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def parse_num(v):
    """数字：'<60' → 60；190 → 190；None → None"""
    if v is None:
        return None
    s = str(v).strip()
    m = re.search(r'\d+(\.\d+)?', s)
    return float(m.group(0)) if m else None


def main():
    if len(sys.argv) < 2:
        print("用法: python tools/import_kanopu.py <Excel路径>")
        sys.exit(1)
    xlsx = sys.argv[1]
    import openpyxl

    wb = openpyxl.load_workbook(xlsx, data_only=True)
    cases = []
    seen = set()

    # 主参数表：字段为 材料/气体/焊丝直径/板厚或焊脚/焊缝形式/电流/电压/速度/...
    for sheet_name, thickness_key in [
        ("碳钢中厚板参数", "焊脚"),
        ("碳钢薄板参数", "板厚"),
        ("镀锌板参数", "板厚"),
        ("不锈钢参数 ", "板厚"),
    ]:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(min_row=1, values_only=True))
        # 找表头行（含 '材料'）
        header_idx = None
        for i, row in enumerate(rows[:5]):
            if row and row[0] == '材料':
                header_idx = i
                break
        if header_idx is None:
            continue
        headers = [str(h).strip() if h else "" for h in rows[header_idx]]
        # 字段索引
        idx = {name: headers.index(name) for name in
               ["材料", "气体", "焊丝直径", thickness_key, "焊缝形式",
                "电流", "电压", "焊接速度", "干伸长"] if name in headers}
        # 摆幅/频率/停留时间（中厚板有）
        for extra in ["摆幅（mm）", "频率", "停留时间（s）"]:
            if extra in headers:
                idx[extra] = headers.index(extra)

        # 焊枪角度"前后/左右"：表头"焊枪角度"列位置即"前后"（数据行该列=前后角度），+1=左右
        angle_fb_col = angle_lr_col = None
        if "焊枪角度" in headers:
            angle_idx = headers.index("焊枪角度")
            angle_fb_col = angle_idx
            angle_lr_col = angle_idx + 1

        for row in rows[header_idx + 2:]:  # 跳过表头+单位行
            if not row or not row[idx.get("材料", 0)]:
                continue
            material = str(row[idx["材料"]]).strip()
            if material in ("材料", ""):
                continue
            fb = parse_angle(row[angle_fb_col]) if angle_fb_col is not None and len(row) > angle_fb_col else None
            lr = parse_angle(row[angle_lr_col]) if angle_lr_col is not None and len(row) > angle_lr_col else None
            case = {
                "material": material,
                "gas": str(row[idx["气体"]]).strip() if "气体" in idx and row[idx["气体"]] else "80%Ar+20%CO2",
                "wire_dia": str(row[idx["焊丝直径"]]).strip() if "焊丝直径" in idx and row[idx["焊丝直径"]] else "1.2mm",
                "thickness": parse_num(row[idx[thickness_key]]),
                "joint": str(row[idx["焊缝形式"]]).strip() if "焊缝形式" in idx and row[idx["焊缝形式"]] else "",
                "current": parse_num(row[idx["电流"]]),
                "voltage": parse_num(row[idx["电压"]]),
                "speed": parse_num(row[idx["焊接速度"]]),
                "stick_out": parse_num(row[idx["干伸长"]]) if "干伸长" in idx else 15,
                "gun_angle_fb": fb,
                "gun_angle_lr": lr,
            }
            if "摆幅（mm）" in idx:
                case["weave_width"] = parse_num(row[idx["摆幅（mm）"]])
            if "频率" in idx:
                case["weave_freq"] = parse_num(row[idx["频率"]])
            if "停留时间（s）" in idx:
                case["weave_dwell"] = parse_num(row[idx["停留时间（s）"]])
            # 去重（同材料+厚度+焊缝形式）
            key = (case["material"], case["thickness"], case["joint"])
            if key in seen:
                continue
            seen.add(key)
            cases.append(case)

    # 保存
    out = PROJECT_ROOT / "data" / "weld_cases.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 导入 {len(cases)} 条 → {out}")
    # 统计
    from collections import Counter
    mats = Counter(c["material"] for c in cases)
    print(f"   材料分布: {dict(mats)}")
    for m in mats:
        tc = Counter(c["thickness"] for c in cases if c["material"] == m)
        print(f"   {m}: 板厚档 {sorted(tc.keys())}")


if __name__ == "__main__":
    main()
