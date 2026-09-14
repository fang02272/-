# -*- coding: utf-8 -*-
"""性能基线测试 — 量化查询响应时间/缓存命中/产物类型

用法:
  python tools/perf_baseline.py                # 首次（无缓存）基线
  python tools/perf_baseline.py --with-cache   # 二次（含缓存命中）

输出: 平均耗时/缓存命中率/各产物类型占比
"""
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 典型查询（概念/卡片/OTHER）
QUERIES = [
    "什么是氩弧焊", "什么是热影响区", "什么是扩散焊",
    "碳钢 5mm 船型焊", "不锈钢 2mm 平搭接", "镀锌板 1mm 船型焊",
    "Q345钢板12mm对接焊", "碳钢 3mm 平拼接 电流大",
    "焊机器人系统组成", "异种材料焊接为什么难",
]


def main():
    import server
    server._ensure_index()

    total = 0
    cache_hits = 0
    products = {}

    print(f"=== 性能基线（{len(QUERIES)} 条查询）===")
    times = []
    for q in QUERIES:
        t0 = time.perf_counter()
        p = server.process_query(q)
        dt = (time.perf_counter() - t0) * 1000
        times.append(dt)
        total += 1
        if p.get("cache_hit"):
            cache_hits += 1
        prod = "card" if p.get("process_card") else (
            "concept" if p.get("sections", {}).get("concept_definition")
            else "knowledge" if any(
                isinstance(s, dict) and len(str(s.get("content", "") or "")) > 30
                for s in (p.get("sections") or {}).values()
            ) else "llm"
        )
        products[prod] = products.get(prod, 0) + 1
        print(f"  [{dt:6.0f}ms] {q[:20]:22s} {prod:10s} cache={p.get('cache_hit', False)}")

    avg = sum(times) / len(times)
    cache_rate = cache_hits / total * 100
    print(f"\n=== 汇总 ===")
    print(f"  平均耗时: {avg:.0f}ms")
    print(f"  最慢: {max(times):.0f}ms | 最快: {min(times):.0f}ms")
    print(f"  缓存命中率: {cache_hits}/{total} ({cache_rate:.0f}%)")
    print(f"  产物类型: {products}")


if __name__ == "__main__":
    main()
