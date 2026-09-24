# -*- coding: utf-8 -*-
"""近义检索命中测试（Day4 任务5）：python tools/near_hit_test.py

结构：每组 = 1 条基准问 + 2~3 条近义改写（繁体/缩写/口语/别称）。
口径：改写问走 vi.search(q, top_k=3)，若 top3 包含该组基准问的 top1 doc_id → 命中。
指标：近义一致率 = 命中改写数 / 改写总数（衡量"同一语义是否检索到同一篇知识"）。

调优（app/vector_store.py search() 融合权重/tokenize 加权）前后各跑一次对比。
退出码：命中率低于 THRESHOLD 时返回 1（可接入回归）。
"""
import sys
sys.path.insert(0, ".")

# 命中率下限（低于则退出码 1）。实测：基线(0.7/0.3+原tokenize) 87.8% →
# 别名大小写修复 + 融合权重 0.5/0.5 → 98.0% (49/50)。留 3pp 安全余量。
THRESHOLD = 95.0

# (基准问, [近义改写…]) —— 种子取自 SYNONYM_TESTS / 链路测试 / TERM_ALIAS_MAP
GROUPS = [
    ("氩弧焊是什么", ["什么是氩弧焊", "氬弧焊是什么", "TIG焊是什么", "钨极氩弧焊的原理"]),    ("什么是热输入", ["热输入是什么意思", "焊接热输入是啥", "热输入的定义"]),
    ("焊接变形有哪些类型", ["焊接变形有哪几种", "钢结构变形分类", "焊接变形的种类"]),
    ("CE碳当量计算", ["碳当量怎么算", "CE值计算公式", "碳当量计算方法"]),
    ("焊道跟踪", ["焊缝跟踪是什么", "焊道跟踪系统", "什么是焊缝跟踪"]),
    ("RT射线探伤", ["射线探伤怎么做", "X射线检测原理", "RT检测是什么"]),
    ("什么是冷裂纹敏感性指数", ["Pcm冷裂评估", "Pcm值怎么算", "冷裂纹怎么评估", "冷裂纹敏感性指数"]),
    ("双相不锈钢焊接", ["2205双相钢怎么焊", "双相钢的焊接特点", "双相不锈钢组织"]),
    ("铸铁焊接", ["球墨铸铁怎么焊", "灰铸铁焊补", "铸铁件的焊接"]),
    ("耐热钢焊接", ["铬钼钢焊接", "Cr-Mo钢焊接要点", "珠光体耐热钢怎么焊"]),
    ("电渣焊", ["ESW是什么", "电渣焊接原理", "什么是电渣焊"]),
    ("船形焊姿态", ["船型焊姿势", "船形位置焊接", "船型焊是什么"]),
    ("手把焊电流", ["焊条电弧焊电流", "手工电弧焊参数", "手弧焊电流选择"]),
    ("氩气", ["纯氩是什么", "Ar气是啥", "氩气纯度要求"]),
    ("运条方法", ["锯齿形运条", "运条方式", "焊条运条手法"]),
    ("什么是保护气流量", ["保护气体流量怎么调", "气体流量多大合适", "保护气流量是啥"]),
]


def main():
    import server
    server._ensure_index()
    vi = server._get_vector()

    total = ok = skipped = 0
    for q0, variants in GROUPS:
        hits0 = vi.search(q0, top_k=3)
        if not hits0:
            skipped += 1
            print(f"⚠️ 跳过（基准无命中）: {q0}")
            continue
        d0 = hits0[0]["doc_id"]
        print(f"· 基准: {q0}  →  {d0}")
        for q in variants:
            total += 1
            got = [h["doc_id"] for h in vi.search(q, top_k=3)]
            if d0 in got:
                ok += 1
                print(f"  OK   {q}")
            else:
                print(f"  MISS {q}")
                print(f"        实际top3: {got}")

    rate = ok / total * 100 if total else 0.0
    print(f"\n近义一致率: {rate:.1f}% ({ok}/{total})  目标: ≥{THRESHOLD:.1f}%")
    if skipped:
        print(f"跳过组数: {skipped}（基准无命中，不计入）")
    return 0 if rate >= THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
