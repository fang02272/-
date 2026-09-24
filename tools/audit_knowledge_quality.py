"""生成可重复的知识数据质量巡检报告。

报告只读扫描 saved_knowledge，不凭猜测改写书籍正文或章节标题；明确的 OCR
标题和缺失页码会被列为待人工对照原 PDF 的问题。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _sample_indices(size: int, limit: int = 20) -> list[int]:
    if size <= limit:
        return list(range(size))
    # 固定步长采样，报告每次运行可比较。
    return sorted(set(round(i * (size - 1) / (limit - 1)) for i in range(limit)))


def build_report() -> dict:
    from app.knowledge_store import get_store
    from app.retrieval_quality import assess_content_quality

    store = get_store()
    books = []
    issues = []
    sampled = 0
    for source in store.list_sources():
        chapters = store.get_chapters(source["id"])
        sampled_indices = _sample_indices(len(chapters), 20)
        sampled += len(sampled_indices)
        book_issues = []
        for index in sampled_indices:
            chapter = chapters[index]
            title = chapter.get("title", "")
            content = chapter.get("content", "")
            quality = assess_content_quality(title, content, chapter.get("summary", ""))
            page_hint = chapter.get("page_hint", "")
            row = {
                "chapter_index": index,
                "title": title,
                "content_length": len(content),
                "page_hint": page_hint,
                "quality_score": quality["score"],
                "quality_issues": quality["issues"],
                "metrics": quality["metrics"],
            }
            if quality["metrics"].get("title_garbled"):
                row["action"] = "回看原 PDF 目录，确认标题后修复元数据"
                book_issues.append({"type": "ocr_title", **row})
            if not page_hint:
                row["action"] = "回看原 PDF 页码后补录 page_hint；当前不生成伪页码"
                book_issues.append({"type": "missing_page_hint", **row})
        issues.extend({"book": source["filename"], **item} for item in book_issues)
        books.append({
            "book": source["filename"],
            "source_id": source["id"],
            "chapter_count": len(chapters),
            "sampled_chapters": len(sampled_indices),
            "ocr_title_issues": sum(item["type"] == "ocr_title" for item in book_issues),
            "missing_page_hints": sum(item["type"] == "missing_page_hint" for item in book_issues),
        })
    return {
        "schema_version": 1,
        "mode": "read_only_metadata_and_content_quality_audit",
        "sample_size": sampled,
        "books": books,
        "issues": issues,
        "summary": {
            "issue_count": len(issues),
            "ocr_title_count": sum(i["type"] == "ocr_title" for i in issues),
            "missing_page_hint_count": sum(i["type"] == "missing_page_hint" for i in issues),
            "note": "OCR标题和页码问题需依据原PDF人工核对，报告不会自动猜测或改写正文。",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="巡检 saved_knowledge 的章节质量和引用元数据")
    parser.add_argument("--output", default="reports/knowledge_quality_audit.json")
    args = parser.parse_args()
    report = build_report()
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"报告已保存：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
