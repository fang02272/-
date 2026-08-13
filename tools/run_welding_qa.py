"""
焊接工艺专家系统 - 命令行交互（与 Web 端共用同一问答引擎）
==========================================================
使用方式:
  python run_welding_qa.py "查询内容"   # 单次查询（参数问题输出工艺卡片）
  python run_welding_qa.py              # 交互式模式
  python run_welding_qa.py --demo       # 演示模式
  python run_welding_qa.py --list       # 列出知识分类体系
"""

import sys
import os
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(str(Path(__file__).resolve().parent.parent))

if sys.platform == 'win32':
    for _s in (sys.stdout, sys.stderr):
        try:
            if hasattr(_s, 'reconfigure'):
                _s.reconfigure(encoding='utf-8', errors='replace')
        except (ValueError, AttributeError):
            pass

from server import process_query
from app.welding_qa_system import WeldingQASystem


DEMO_QUERIES = [
    ("Q345钢板12mm预热温度", "参数问题 → 工艺卡片"),
    ("304不锈钢 3mm TIG焊", "参数问题 → 工艺卡片"),
    ("焊接热影响区冷裂纹怎么防止", "纯理论概念问题"),
    ("什么是氩弧焊", "基本概念问题"),
]


def print_card(c: dict):
    """命令行打印工艺卡片（机器可读结构化数据）"""
    if not c:
        return
    eq = c.get("equipment", {})
    rp = c.get("robot_params", {})
    el = c.get("electrical", {})
    th = c.get("thermal", {})
    jp = c.get("joint_prep", {})
    pp = c.get("pass_plan", {})

    line = "═" * 58
    print(f"\n{line}")
    print("🏷️  工 艺 卡 片")
    print(line)
    print(f"🧱 母材: {c.get('base_material','')}　📏 板厚: {c.get('thickness_mm','')}mm　🔥 工艺: {c.get('process','')}")
    if c.get("process_assumed"):
        print("⚠️  工艺未指定，默认焊条电弧焊基线")
    if eq:
        print(f"🤖 装备: 机器人 {eq.get('robot_model','')} / 焊枪 {eq.get('torch_model','')}"
              f" / 焊机 {eq.get('welder_model','')} / 作业幅宽 {eq.get('work_area_m2','')}㎡")

    print(f"\n⚙️ 焊接参数")
    for k, v in [("焊接电流", el.get("current_a")), ("电弧电压", el.get("voltage_v")),
                 ("焊接速度", el.get("travel_speed_cm_min")), ("焊材", c.get("consumables")),
                 ("焊条/焊丝直径", c.get("electrode_diameter")), ("保护气体", c.get("shielding_gas"))]:
        if v:
            print(f"  {k}: {v}")

    print(f"\n🤖 机器人参数")
    for k, v in [("焊接速度", rp.get("travel_speed")), ("焊枪倾角", rp.get("gun_angle")),
                 ("干伸长", rp.get("stick_out")), ("摆动宽度", rp.get("weave_width")),
                 ("摆动频率", rp.get("weave_frequency"))]:
        if v:
            print(f"  {k}: {v}")

    # 机器人焊接方案（焊丝/TCP/枪姿态/船型焊/层道/管道）
    rb = c.get("robot", {}) or {}
    if rb.get("wire") or rb.get("pass_sequence"):
        print(f"\n🦾 机器人焊接方案")
        if rb.get("weld_mode"):
            print(f"  模式: {rb['weld_mode']}")
        if rb.get("wire"):
            print(f"  焊丝: {rb['wire'].get('type','')} {rb['wire'].get('diameter','')}")
        if rb.get("gas"):
            print(f"  保护气: {rb['gas'].get('type','')} {rb['gas'].get('flow','')}")
        if rb.get("tcp"):
            print(f"  TCP: {rb['tcp'].get('contact_tip_to_work','')} / 干伸长{rb['tcp'].get('stick_out','')}")
        if rb.get("gun_pose"):
            print(f"  枪姿态: 工作角{rb['gun_pose'].get('work_angle','')} | 行走角{rb['gun_pose'].get('travel_angle','')}")
        if rb.get("ship_position"):
            print(f"  船型焊: {rb['ship_position'].get('position','')} — {rb['ship_position'].get('angle','')}")
        for p in rb.get("pass_sequence", []):
            print(f"  · {p.get('pass','')}: {p.get('current_a','')} / {p.get('voltage_v','')} / "
                  f"{p.get('speed_cm_min','')}cm/min / 宽{p.get('bead_width','')}")
        pipe = rb.get("pipe", {})
        if pipe.get("pipe_mode"):
            print(f"  管道: {pipe.get('pipe_mode')} — {pipe.get('strategy','')[:40]}")

    print(f"\n📋 工艺方案")
    print(f"  坡口: {c.get('groove','')}")
    print(f"  装配间隙: {c.get('joint_gap_mm','')}")
    print(f"  清理: {jp.get('cleaning','')}")
    print(f"  定位焊: {jp.get('tack_weld','')}")
    print(f"  层道: {pp.get('layers_passes','')}")
    print(f"  摆动: {pp.get('weaving','')}")

    print(f"\n🌡️ 热管理")
    print(f"  预热: {th.get('preheat','')}")
    print(f"  层间温度: {th.get('interpass_temp','')}")
    print(f"  后热: {th.get('postheat','')}")

    print(f"\n🛡️ 质量评估（缺陷预防）")
    for ck in (c.get("quality", {}) or {}).get("checks", []):
        print(f"  • {ck.get('defect','')}: {ck.get('cause','')} → {ck.get('prevention','')}")

    print(f"\n💾 机器可读 JSON 字段: base_material / thickness_mm / process / groove / "
          f"electrical / robot_params / quality / equipment")
    print(line)


def print_payload(p: dict):
    """打印查询结果：有工艺卡片打卡片，否则打 sections/content"""
    if p.get("process_card"):
        print_card(p["process_card"])
        return
    if p.get("content"):
        print(p["content"])
        return
    for key, sec in (p.get("sections", {}) or {}).items():
        if not sec or sec.get("visible", True) is False:
            continue
        title = sec.get("title", key)
        print(f"\n## {sec.get('icon','')} {title}")
        if sec.get("content"):
            print(sec["content"])
        if sec.get("items"):
            for it in sec["items"]:
                print(f"  • {it}")


def run_interactive():
    qa = WeldingQASystem()
    print("=" * 60)
    print("🔬 焊接工艺专家系统 — 命令行")
    print("   与 Web 端共用同一问答引擎（概念/工艺卡片/缓存）")
    print("=" * 60)
    print()
    print("📋 使用说明:")
    print("   · 参数问题（材料/板厚/工艺）→ 输出工艺卡片")
    print("   · 概念问题 → 概念解析 + 来源")
    print("   · 输入 'categories' 查看知识分类体系")
    print("   · 输入 'exit' 退出")
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
        if query.lower() in ("categories", "cat", "分类", "目录"):
            print("\n" + qa.list_categories() + "\n")
            continue
        try:
            payload = process_query(query)
        except Exception as e:
            print(f"❌ {e}")
            continue
        print_payload(payload)
        print()


def run_single_query(query: str):
    payload = process_query(query)
    print_payload(payload)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--demo":
            for q, desc in DEMO_QUERIES:
                print(f"\n{'═'*60}\n🔍 {q}（{desc}）")
                run_single_query(q)
        elif arg == "--list":
            print(WeldingQASystem().list_categories())
        elif arg in ("--help", "-h"):
            print(__doc__)
        else:
            run_single_query(arg)
    else:
        run_interactive()
