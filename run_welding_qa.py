"""
焊接知识科普系统 - 交互式运行脚本
==================================
基于《材料焊接原理》(王宗杰, 2024, ISBN 978-7-122-44318-2) 构建

使用方式:
  python run_welding_qa.py           # 交互式模式
  python run_welding_qa.py "查询内容"  # 单次查询
  python run_welding_qa.py --demo    # 演示模式
  python run_welding_qa.py --list    # 列出知识分类体系
"""

import sys
import io
import os

if sys.platform == 'win32':
    try:
        if hasattr(sys.stdout, 'buffer') and sys.stdout.buffer:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except (ValueError, AttributeError):
        pass
    try:
        if hasattr(sys.stderr, 'buffer') and sys.stderr.buffer:
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except (ValueError, AttributeError):
        pass

from welding_qa_system import WeldingQASystem


DEMO_QUERIES = [
    ("焊接热影响区冷裂纹怎么防止",
     "纯理论查询：检测HAZ、冷裂纹等书本核心内容的科普能力"),

    ("弧焊机器人焊接5mm钢板，板厚和焊接参数怎么选择",
     "交叉领域查询：弧焊机器人 + 板厚 → 书本理论+工程实践的融合输出"),

    ("异种材料焊接为什么难，怎么解决",
     "跨章节查询：异种材料 → 第5章+第8章+第9章的综合回答"),

    ("焊缝结晶裂纹的产生机理",
     "精准章节查询：结晶裂纹 → 第1章1.13节"),

    ("活性钎焊Ti元素反应连接陶瓷",
     "前沿技术查询：活性钎焊 → 第7章+材料科学前沿"),

    ("铝合金和不锈钢能焊在一起吗",
     "实用问答：异种金属 → 第5章焊接性+第8章中间层策略"),
]


def run_demo(qa: WeldingQASystem):
    """演示模式：逐个展示典型查询的输出"""
    print("=" * 60)
    print("🔬 焊接知识科普系统 — 演示模式")
    print("   基于《材料焊接原理》(王宗杰, 2024)")
    print("=" * 60)

    for i, (query, desc) in enumerate(DEMO_QUERIES, 1):
        print(f"\n{'█' * 60}")
        print(f"📋 演示 {i}/{len(DEMO_QUERIES)}: {desc}")
        print(f"{'█' * 60}")
        print(f"\n🔍 查询: {query}\n")
        response = qa.generate(query)
        print(response)
        if i < len(DEMO_QUERIES):
            input("\n⏎ 按回车继续下一个演示...")


def run_interactive(qa: WeldingQASystem):
    """交互模式"""
    print("=" * 60)
    print("🔬 焊接知识科普系统")
    print("   基于《材料焊接原理》(王宗杰, 2024)")
    print("=" * 60)
    print()
    print("📋 使用说明:")
    print("   · 直接输入焊接相关问题获取科普内容")
    print("   · 支持纯理论、交叉领域、异种材料等各类查询")
    print("   · 输出格式: 科普 → 交叉分析 → 推荐 → 来源 → 迁移")
    print("   · 输入 'categories' 查看知识分类体系")
    print("   · 输入 'demo' 运行演示模式")
    print("   · 输入 'classify: <查询>' 仅分类不生成内容")
    print("   · 输入 'exit' / 'quit' 退出")
    print()
    print("📋 示例查询:")
    for q, _ in DEMO_QUERIES[:3]:
        print(f"  > {q}")
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

        if query.lower() in ("demo", "演示"):
            run_demo(qa)
            continue

        if query.lower().startswith("classify:") or query.lower().startswith("分类:"):
            target = query.split(":", 1)[1].strip()
            if target:
                c = qa.classify_only(target)
                print(f"\n📊 分类结果:")
                print(f"   关键词: {c['keywords']}")
                print(f"   匹配书籍章节: {list(c['book_categories'].keys())}")
                print(f"   匹配交叉领域: {list(c['cross_domains'].keys())}")
                print(f"   是否交叉查询: {c['is_cross_domain']}")
                print(f"   综合类别: {c['all_categories']}")
                print()
            continue

        # 生成完整回答
        print()
        response = qa.generate(query)
        print(response)
        print()


def run_single_query(qa: WeldingQASystem, query: str):
    """单次查询模式"""
    response = qa.generate(query)
    print(response)


if __name__ == "__main__":
    qa = WeldingQASystem()

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--demo":
            run_demo(qa)
        elif arg == "--list":
            print(qa.list_categories())
        elif arg == "--help" or arg == "-h":
            print(__doc__)
        else:
            # 单次查询
            run_single_query(qa, arg)
    else:
        run_interactive(qa)
