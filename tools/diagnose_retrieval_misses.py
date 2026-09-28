"""检索未命中归因诊断：术语别名缺失 / 章节关键词不足 / 排序不合理。

用法：
    python tools/diagnose_retrieval_misses.py --output reports/retrieval_miss_analysis.md

对固定题集中 retrieval_hit=False 的题目，逐条判断其标注证据词在语料中的分布位置，
据此归因到以下三类之一：
  A. alias_missing  —— 证据词及其别名在全部章节关键词中都不存在（词典/归一化缺口）
  B. keyword_insufficient —— 章节正文含该词，但该章节 keywords 未收录（切分/关键词抽取缺口）
  C. ranking_unreasonable —— 含该词的章节存在且关键词也收录了，但未进入 Top-5（排序问题）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _variants(term: str) -> set[str]:
    """证据词所属同义词组的全部表达（规范词 + 别名 + 缩写 + 口语说法）。

    只收集「该词确实是组内成员」的那些组，不做跨组传递扩展 —— 否则
    「熔池」会被扩成几十个泛词，得出「28/28 章节全部命中」这类假结论。
    """
    raw = str(term).lower().strip()
    variants = {raw}
    try:
        from app.welding_qa_system import WeldingQASystem
        variants.add(WeldingQASystem._normalize_text(str(term)).lower().strip())
    except Exception:
        pass
    try:
        from app.welding_knowledge_base import TERM_ALIAS_MAP
        for canonical, aliases in TERM_ALIAS_MAP.items():
            group = {str(canonical).lower(), *(str(a).lower() for a in aliases)}
            if raw in group:
                variants |= group
    except Exception:
        pass
    return {v for v in variants if len(v) >= 2}


def _build_corpus(store) -> list[dict]:
    chapters = []
    for src in store.registry["sources"]:
        for ch in store.get_chapters(src["id"]):
            chapters.append({
                "source": src["filename"],
                "title": ch.get("title", ""),
                "keywords": [str(k).lower() for k in ch.get("keywords", [])],
                "content": str(ch.get("content", "")),
                "summary": str(ch.get("summary", "")),
            })
    return chapters


def _classify(term: str, chapters: list[dict], ranked: list[dict]) -> dict:
    """判断单个证据词在语料中的可达性，并据真实检索排名归因。

    三类归因各有明确整改动作：
      alias_missing        —— 词及其同义词在语料中都不存在，需补别名/同义规则
      keyword_insufficient —— 正文有该词，但所属章节的 keywords 没收录，需补章节关键词
      ranking_unreasonable —— 含该词的章节存在且关键词也收录了，但排名掉出 Top-5，需调排序
    """
    variants = _variants(term)

    # 检索时章节关键词需作为子串出现在（扩展后的）查询中，因此这里判断
    # 章节关键词是否落在证据词的同义表达内，与 kw in query 同向。
    kw_chapters = [
        c for c in chapters
        if any(k in v for k in c["keywords"] for v in variants)
    ]
    content_chapters = [
        c for c in chapters
        if any(v in c["content"][:8000].lower() for v in variants)
    ]

    # 真实排名：含该词的章节在检索结果中拿到的最好名次（1 起算）
    ranks = []
    for c in content_chapters or kw_chapters:
        for idx, hit in enumerate(ranked, start=1):
            if hit.get("source") == c["source"] and hit.get("chapter") == c["title"]:
                ranks.append(idx)
    best_rank = min(ranks) if ranks else None

    if not content_chapters and not kw_chapters:
        verdict = "alias_missing"
        reason = (f"该词及同义词组（{', '.join(sorted(variants)[:4])}）在全部 "
                  f"{len(chapters)} 个章节的正文和关键词中均不存在，检索无从命中")
    elif not kw_chapters:
        verdict = "keyword_insufficient"
        reason = (f"正文含该词但所属 {len(content_chapters)} 个章节均未把它收进 keywords，"
                  f"关键词层无法贡献分数")
    else:
        verdict = "ranking_unreasonable"
        rank_note = f"最好名次第 {best_rank} 名" if best_rank else "未进入检索结果前 15 名"
        reason = (f"{len(kw_chapters)} 个章节的关键词命中该词，"
                  f"{rank_note}，掉出 Top-5")
    return {
        "term": term,
        "variants": sorted(variants),
        "verdict": verdict,
        "reason": reason,
        "kw_chapter_count": len(kw_chapters),
        "content_chapter_count": len(content_chapters),
        "best_rank": best_rank,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检索未命中归因诊断")
    parser.add_argument("--report", default="reports/fixed_questions_evaluation.json")
    parser.add_argument("--output", default="reports/retrieval_miss_analysis.md")
    args = parser.parse_args()

    report = json.loads((PROJECT_ROOT / args.report).read_text(encoding="utf-8"))
    questions = {q["id"]: q for q in json.loads(
        (PROJECT_ROOT / "tools" / "fixed_questions.json").read_text(encoding="utf-8"))}

    import server
    server._ensure_index()
    store = server.get_store()
    chapters = _build_corpus(store)

    missed = [r for r in report["rows"] if r.get("retrieval_hit") is False]
    tally = {"alias_missing": 0, "keyword_insufficient": 0, "ranking_unreasonable": 0, "hit": 0}

    lines = ["# 检索未命中归因分析", "",
             f"- 题集：`{report['source']}`，共 {report['question_count']} 题",
             f"- 检索未命中：{len(missed)} 题（Top-5 证据词命中率 "
             f"{report['retrieval']['top5_hit_rate']:.1%}）",
             f"- 语料章节总数：{len(chapters)}", "",
             "三类归因口径：**术语别名缺失**（词表/归一化查不到）、"
             "**章节关键词不足**（正文有词但 keywords 没收）、"
             "**排序不合理**（keywords 有词但没排进 Top-5）。", ""]

    for row in missed:
        q = questions[row["id"]]
        # 完整排名结果（评测报告只存了 Top-5 的标题），用于判定真实名次
        ranked = store.search_across_sources(q["query"])

        lines.append(f"## {row['id']}　{q['query']}　`[{q['category']}]`")
        lines.append("")
        lines.append(f"- 期望证据词：{'、'.join(q.get('evidence_terms', []))}")
        lines.append(f"- Top-5 命中章节：" + ("、".join(
            f"{h.get('book','')}/{h.get('chapter','')}({h.get('score')})"
            for h in row.get("top5", [])[:5]) or "**空**"))
        lines.append("")
        lines.append("| 证据词 | 归因 | 依据 |")
        lines.append("|---|---|---|")
        for term in q.get("evidence_terms", []):
            res = _classify(term, chapters, ranked)
            tally[res["verdict"]] += 1
            lines.append(f"| {term} | `{res['verdict']}` | {res['reason']} |")
        lines.append("")

    lines += ["## 归因汇总", "",
              "| 归因类别 | 证据词数 | 占比 |",
              "|---|---|---|"]
    total = sum(tally.values())
    label = {"alias_missing": "术语别名缺失", "keyword_insufficient": "章节关键词不足",
             "ranking_unreasonable": "排序不合理", "hit": "已命中"}
    for key in ("alias_missing", "keyword_insufficient", "ranking_unreasonable", "hit"):
        pct = f"{tally[key] / total:.1%}" if total else "—"
        lines.append(f"| {label[key]} | {tally[key]} | {pct} |")
    lines.append("")

    out = PROJECT_ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"missed_questions": len(missed), "tally": tally}, ensure_ascii=False, indent=2))
    print(f"报告已保存：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
