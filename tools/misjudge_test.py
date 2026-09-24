# -*- coding: utf-8 -*-
"""意图路由误判率测试（Day3 任务1）：python tools/misjudge_test.py

四类问题各占 1/4：concept（概念）/ parameter（参数）/ mixed（概念+参数）/ other（其他）。
目标：误判率 <5%。"""
import sys
sys.path.insert(0, ".")
from app.qa_router import get_router

# (问题, 期望意图)
CASES = [
    # ---------- 概念（11 条） ----------
    ("什么是氩弧焊", "concept"),
    ("氩弧焊是什么", "concept"),
    ("氩弧焊和二氧化碳焊的区别", "concept"),
    ("焊条电弧焊的原理", "concept"),
    ("为什么焊缝要开坡口", "concept"),
    ("焊接变形有哪些类型", "concept"),
    ("什么是热输入", "concept"),
    ("焊条的分类", "concept"),
    ("什么叫层间温度", "concept"),
    ("金属间化合物的特点", "concept"),
    ("Q345和16Mn的区别", "concept"),
    # ---------- 参数（11 条） ----------
    ("Q345钢板12mm对接焊电流多大", "parameter"),
    ("不锈钢3mm平搭接", "parameter"),
    ("碳钢 5mm 船型焊 电压", "parameter"),
    ("304不锈钢3mmTIG焊电流多大", "parameter"),
    ("Q345预热温度是多少", "parameter"),
    ("铝板2mm用什么焊丝", "parameter"),
    ("Q345 12mm", "parameter"),
    ("镀锌板1mm平拼接电流多大", "parameter"),
    ("不锈钢管子氩弧焊用什么焊丝", "parameter"),
    ("焊接速度一般多少", "parameter"),
    ("焊条直径怎么选", "parameter"),
    # ---------- 概念+参数 混合（11 条） ----------
    ("什么是预热，Q345要预热吗", "mixed"),
    ("为什么焊前要预热，Q345预热多少度", "mixed"),
    ("焊接电流是什么，Q345选多大", "mixed"),
    ("什么是热输入，Q345焊接热输入多少合适", "mixed"),
    ("不锈钢的焊接性是什么，3mm用什么电流", "mixed"),
    ("什么是焊后热处理，Q345需要吗", "mixed"),
    ("保护气流量是什么，怎么调", "mixed"),
    ("层间温度是多少，为什么重要", "mixed"),
    ("什么是热输入，怎么计算", "mixed"),
    ("氩弧焊的保护气体是什么，流量多大", "mixed"),
    ("什么是线能量，Q345线能量多少合适", "mixed"),
    # ---------- 其他（11 条） ----------
    ("今天天气怎么样", "other"),
    ("你好", "other"),
    ("你是谁", "other"),
    ("帮我写一首诗", "other"),
    ("1+1等于几", "other"),
    ("谢谢", "other"),
    ("推荐一家餐厅", "other"),
    ("焊接工资多少一个月", "other"),
    ("什么是爱情", "other"),
    ("明天会下雨吗", "other"),
    ("这个项目什么时候交付", "other"),
]


def main():
    router = get_router()
    wrong = 0
    for q, want in CASES:
        got = router.analyze_intent(q)["intent"].value
        flag = "OK " if got == want else "MISS"
        if got != want:
            wrong += 1
        print(f"{flag} [{got:9s}] 期望={want:9s} {q}")
    rate = wrong / len(CASES) * 100
    print(f"\n误判率: {rate:.1f}% ({wrong}/{len(CASES)})  目标 <5%")
    return 0 if rate < 5 else 1


if __name__ == "__main__":
    sys.exit(main())
