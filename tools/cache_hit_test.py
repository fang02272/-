# -*- coding: utf-8 -*-
"""缓存命中率测试（Day5 任务2）：python tools/cache_hit_test.py

口径（独立定义，待与赵剑乔复核）：
  第一轮：10 条代表性原题（冷缓存，预热写入）
  第二轮：① 原题重问 → 精确命中率；② 20 条近义改写 → 相似命中率（目标 >30%）
          ③ 3 条防误伤对照题（同格式不同内容）→ 必须全部 miss（调参红线）

退出码：相似命中率 < MIN_SIM_RATE 或防误伤有命中时返回 1。
"""
import os
import sys
sys.path.insert(0, ".")

# 相似命中率下限（团队目标 >30%，这里留余量）
MIN_SIM_RATE = 30.0

# 10 条原题：概念 ×5 + 工艺参数 ×5（本地可答题，保证会写入缓存）
ORIGINALS = [
    "什么是氩弧焊", "什么是热影响区", "什么是扩散焊", "什么是焊接变形",
    "RT射线探伤是什么",
    "碳钢 5mm 船型焊", "不锈钢 2mm 平搭接", "镀锌板 1mm 船型焊",
    "Q345钢板12mm对接焊", "碳钢 3mm 平拼接 电流大",
]

# 20 条近义改写：与原题相似但不完全相同（语序调换/±1字/同义微调）
REWRITES = [
    # 概念题改写
    "氩弧焊是什么", "什么是氩弧焊呀", "氩弧焊是什么呀",
    "热影响区是什么", "什么是热影响区呢",
    "扩散焊是什么", "什么是扩散焊接",
    "焊接变形是什么", "什么是焊接变形呢",
    # 工艺参数题改写
    "5mm碳钢船型焊", "碳钢5mm的船型焊", "碳钢5mm船型焊接",
    "不锈钢2mm平搭接焊", "2mm不锈钢平搭接",
    "镀锌板1mm船型焊接", "1mm镀锌板船型焊",
    "Q345钢板12mm对接焊接", "12mmQ345钢板对接焊",
    "碳钢3mm平拼接电流调大", "碳钢3mm平拼接大电流",
]

# 3 条防误伤对照：字符集与某原题高度重合但含义不同，必须不命中
FALSE_FRIENDS = [
    "碳钢5mm平拼接",        # vs 碳钢5mm船型焊（接头不同）
    "热输入是什么",          # vs 热影响区（概念不同）
    "不锈钢1mm平拼接",       # vs 不锈钢2mm平搭接（板厚+接头不同）
]


def main():
    import server

    # 冷缓存：删掉旧缓存文件再启动（保证第一轮全 miss）
    cache_p = os.path.join("saved_knowledge", "answer_cache.json")
    if os.path.exists(cache_p):
        os.remove(cache_p)

    server._ensure_index()
    from app.answer_cache import get_cache

    c = get_cache()
    print(f"缓存配置: sim={c.sim_threshold} jaccard={c.jaccard_floor} "
          f"ttl={c.ttl}s max={c.max_entries}")

    # ---- 第一轮：原题预热（冷缓存，应全 miss）----
    cold = sum(1 for q in ORIGINALS if server.process_query(q).get("cache_hit"))
    print(f"\n[预热] 原题 {len(ORIGINALS)} 条，冷缓存误命中 {cold} 条（应=0）")

    # ---- 第二轮①：原题重问 → 精确命中 ----
    exact_ok = sum(1 for q in ORIGINALS if server.process_query(q).get("cache_hit"))
    print(f"[精确命中] {exact_ok}/{len(ORIGINALS)}")

    # ---- 第二轮②：近义改写 → 相似命中 ----
    sim_ok = 0
    for q in REWRITES:
        hit = server.process_query(q).get("cache_hit")
        sim_ok += 1 if hit else 0
        print(f"  {'HIT ' if hit else 'MISS'} {q}")
    sim_rate = sim_ok / len(REWRITES) * 100
    print(f"[相似命中] {sim_ok}/{len(REWRITES)} = {sim_rate:.1f}%  (目标 >{MIN_SIM_RATE:.0f}%)")

    # ---- 第二轮③：防误伤对照 ----
    fp_hits = 0
    for q in FALSE_FRIENDS:
        hit = server.process_query(q).get("cache_hit")
        fp_hits += 1 if hit else 0
        print(f"  {'❌ 误命中!' if hit else 'OK(未命中)'} {q}")
    print(f"[防误伤] {fp_hits}/3 误命中（应=0）")

    # ---- 汇总 ----
    ok = (sim_rate >= MIN_SIM_RATE) and (fp_hits == 0)
    print(f"\n结论: {'✅ 达标' if ok else '❌ 未达标'} "
          f"(相似命中 {sim_rate:.1f}% ≥{MIN_SIM_RATE:.0f}% 且 防误伤 {fp_hits}/3)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
