"""一键生成成果展示数据 —— PPT 素材的唯一入口。

用法：
    python tools/demo_results.py

依次执行：
  1. 固定 50 题检索/引用/缺失输入评测   → reports/fixed_questions_evaluation.json
  2. 检索未命中三类归因分析            → reports/retrieval_miss_analysis.md
  3. 集中联调四场景 + 前端按钮文案核对  → 控制台
  4. 与基线对比汇总                    → reports/optimization_summary.md

基线（reports/baseline_evaluation.json）若存在则自动对比；不存在时只输出当前值。
基线由 `python tools/evaluate_fixed_questions.py --output reports/baseline_evaluation.json`
在优化前的代码上生成。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS = PROJECT_ROOT / "tools"
REPORTS = PROJECT_ROOT / "reports"

CURRENT = REPORTS / "fixed_questions_evaluation.json"
BASELINE = REPORTS / "baseline_evaluation.json"


def _run(script: str, *args: str) -> int:
    cmd = [sys.executable, str(TOOLS / script), *args]
    print(f"\n▶ 运行 {script} {' '.join(args)}".rstrip())
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _row(label: str, before, after, render) -> str:
    def safe(value):
        return "—" if value is None else render(value)
    return f"| {label} | {safe(before)} | {safe(after)} |"


def _pct(value) -> str:
    return f"{value:.1%}"


def _ms(value) -> str:
    return f"{value:.1f} ms"


def _build_summary(base: dict | None, cur: dict) -> str:
    b_ret = (base or {}).get("retrieval", {})
    b_cit = (base or {}).get("citations", {})
    b_mis = (base or {}).get("missing_input", {})
    b_perf = ((base or {}).get("performance", {}) or {}).get("first_pass", {})
    b_cached = ((base or {}).get("performance", {}) or {}).get("cached_pass", {})

    c_ret = cur.get("retrieval", {})
    c_cit = cur.get("citations", {})
    c_mis = cur.get("missing_input", {})
    c_perf = (cur.get("performance", {}) or {}).get("first_pass", {})
    c_cached = (cur.get("performance", {}) or {}).get("cached_pass", {})

    lines = [
        "# 焊接工艺智能问答系统 — 优化成果汇总",
        "",
        f"- 题集：`{cur.get('source', 'tools/fixed_questions.json')}`，"
        f"共 {cur.get('question_count', 0)} 题（概念/参数/缺失输入/同义词/跨书 各 10 题）",
        f"- 评测模式：`{cur.get('mode', 'offline-no-llm')}`（离线本地，不调用大模型，结果可复现）",
        f"- 基线报告：{'`reports/baseline_evaluation.json`' if base else '**未找到，仅输出当前值**'}",
        "",
        "## 一、核心指标对比",
        "",
        "| 指标 | 优化前 | 优化后 |",
        "|---|---|---|",
        _row("Top-5 检索证据词命中率",
             b_ret.get("top5_hit_rate"), c_ret.get("top5_hit_rate"), _pct),
        _row("引用支持率",
             b_cit.get("supported_rate"), c_cit.get("supported_rate"), _pct),
        _row("缺失输入覆盖率",
             b_mis.get("coverage"), c_mis.get("coverage"), _pct),
        "",
        "## 二、性能对比（首次请求，无缓存）",
        "",
        "| 指标 | 优化前 | 优化后 |",
        "|---|---|---|",
        _row("P50 延迟", b_perf.get("p50_ms"), c_perf.get("p50_ms"), _ms),
        _row("P95 延迟", b_perf.get("p95_ms"), c_perf.get("p95_ms"), _ms),
        "",
        "## 三、缓存命中（重复提问）",
        "",
        "| 指标 | 优化前 | 优化后 |",
        "|---|---|---|",
        _row("缓存命中数", b_cached.get("cache_hits"), c_cached.get("cache_hits"),
             lambda v: str(v) if v is not None else "—"),
        _row("缓存后 P50 延迟", b_cached.get("p50_ms"), c_cached.get("p50_ms"), _ms),
        "",
        "> 缓存路径两者均为亚毫秒级，差异在测量噪声范围内；优化后缓存键额外纳入"
        "专家库文件状态，以保证专家库重建后旧答案立即失效（Day5 盲区修复）。",
        "",
    ]

    if base:
        miss_before = b_ret.get("missed_question_ids", [])
        miss_after = c_ret.get("missed_question_ids", [])
        lines += [
            "## 四、检索未命中题目",
            "",
            f"- 优化前未命中 {len(miss_before)} 题：{'、'.join(miss_before) if miss_before else '无'}",
            f"- 优化后未命中 {len(miss_after)} 题：{'、'.join(miss_after) if miss_after else '无 ✓'}",
            "",
            "三类归因明细见 `reports/retrieval_miss_analysis_baseline.md`（优化前）"
            "与 `reports/retrieval_miss_analysis.md`（优化后）。",
            "",
        ]

    lines += [
        "## 五、复现命令",
        "",
        "```powershell",
        "# 一键生成全部成果数据",
        "python tools\\demo_results.py",
        "",
        "# 分项运行",
        "python tools\\evaluate_fixed_questions.py --output reports\\fixed_questions_evaluation.json",
        "python tools\\diagnose_retrieval_misses.py --output reports\\retrieval_miss_analysis.md",
        "python tools\\test_integration_scenarios.py",
        "",
        "# 网页端实景演示",
        "python start.py   # 打开 http://localhost:8000",
        "```",
        "",
        "## 六、口径说明（避免PPT夸大）",
        "",
        "- 检索命中 = Top-5 章节的标题/关键词/摘要/可读预览中命中至少一个人工标注证据词，属**自动初筛**，不等于人工确认了原文语义。",
        "- 引用支持 = 响应存在来源，且 Top-5 命中人工标注证据词；正式引用仍需对照原 PDF 与页码。",
        "- 性能为无 LLM 的本地模式；接入真实大模型后端到端耗时以 LLM 生成为主。",
        "- 覆盖的语料为 `saved_knowledge/` 下当前 3 本资料、28 个章节。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    results = {}
    results["评测"] = _run("evaluate_fixed_questions.py",
                          "--output", "reports/fixed_questions_evaluation.json")
    results["归因"] = _run("diagnose_retrieval_misses.py",
                          "--output", "reports/retrieval_miss_analysis.md")
    results["联调"] = _run("test_integration_scenarios.py")

    cur = _load(CURRENT)
    if not cur:
        print("\n✗ 未找到评测报告，无法生成汇总")
        return 1
    base = _load(BASELINE)

    summary = _build_summary(base, cur)
    out = REPORTS / "optimization_summary.md"
    out.write_text(summary, encoding="utf-8")

    print("\n" + "=" * 58)
    print("📊 成果数据生成完毕")
    print("=" * 58)
    for name, code in results.items():
        print(f"  {'✓' if code == 0 else '✗'} {name}（exit {code}）")
    print(f"\n  汇总报告：{out}")
    print(f"  未命中归因：{REPORTS / 'retrieval_miss_analysis.md'}")
    if base:
        print(f"  基线对比：{BASELINE}")
    else:
        print("  （无基线报告，汇总中不含对比列）")
    return 0 if all(code == 0 for code in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
