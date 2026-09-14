"""RAG 检索结果质量评估、摘要选取与去重。"""

import hashlib
import re
from typing import Dict, Iterable, List


_CN_RE = re.compile(r"[\u4e00-\u9fff]")
_USEFUL_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_SPACE_RE = re.compile(r"\s+")
_PAGE_TITLE_RE = re.compile(r"^(?:第?\s*\d+\s*页|page\s*\d+|\d+)$", re.IGNORECASE)
_MOJIBAKE_MARKERS = ("�", "锟斤拷", "烫烫烫", "屯屯屯", "\x00")


def _compact(text: str) -> str:
    return _SPACE_RE.sub(" ", str(text or "")).strip()


def _ratio(count: int, total: int) -> float:
    return count / total if total else 0.0


def assess_content_quality(title: str, content: str, summary: str = "") -> Dict:
    """返回 0~1 质量分、是否过滤及原因；规则偏保守，避免误删技术文本。"""
    title_text = _compact(title)
    content_text = _compact(content)
    sample = f"{title_text} {summary or ''} {content_text[:6000]}"
    meaningful = len(_USEFUL_RE.findall(sample))
    cn_count = len(_CN_RE.findall(sample))
    useful_ratio = _ratio(meaningful, len(sample))
    cn_ratio = _ratio(cn_count, meaningful)
    marker_count = sum(sample.count(marker) for marker in _MOJIBAKE_MARKERS)
    marker_ratio = _ratio(marker_count, max(len(sample), 1))

    issues: List[str] = []
    score = 1.0
    filtered = False

    if meaningful < 40:
        issues.append("内容过短")
        score -= 0.65
        filtered = meaningful < 20
    elif meaningful < 100:
        issues.append("内容偏短")
        score -= 0.20

    if not title_text or _PAGE_TITLE_RE.fullmatch(title_text):
        issues.append("标题信息不足")
        score -= 0.12

    if marker_count:
        issues.append("疑似乱码")
        score -= min(0.75, 0.25 + marker_ratio * 8)
        if marker_ratio >= 0.03:
            filtered = True

    if useful_ratio < 0.35:
        issues.append("有效字符比例低")
        score -= 0.35
        if useful_ratio < 0.20:
            filtered = True

    # 焊接书籍允许型号、公式和英文较多；仅在中文极少且标题也不可靠时过滤。
    if cn_ratio < 0.08 and cn_count < 20:
        issues.append("中文内容比例低")
        score -= 0.25
        if not title_text or _PAGE_TITLE_RE.fullmatch(title_text):
            filtered = True

    lines = [line.strip() for line in str(content or "").splitlines() if len(line.strip()) >= 8]
    if len(lines) >= 5:
        unique_ratio = _ratio(len(set(lines)), len(lines))
        if unique_ratio < 0.45:
            issues.append("重复文本较多")
            score -= 0.25

    score = max(0.0, min(1.0, score))
    if score < 0.22:
        filtered = True
    return {
        "score": round(score, 3),
        "filtered": filtered,
        "issues": issues,
        "metrics": {
            "meaningful_chars": meaningful,
            "chinese_ratio": round(cn_ratio, 3),
            "useful_ratio": round(useful_ratio, 3),
        },
    }


def _query_terms(query: str) -> List[str]:
    return list(dict.fromkeys(
        term.lower() for term in re.findall(r"[\u4e00-\u9fff]{2,10}|[A-Za-z][A-Za-z0-9./+-]{1,15}", query or "")
    ))


def best_content_preview(content: str, query: str = "", limit: int = 260) -> str:
    """优先返回包含查询词且可读的一段，而不是机械截取章节开头。"""
    raw = str(content or "")
    if not raw.strip():
        return ""
    terms = _query_terms(query)
    candidates = []
    for paragraph in re.split(r"(?:\r?\n){1,}|(?<=[。！？；])", raw):
        text = _compact(paragraph)
        meaningful = len(_USEFUL_RE.findall(text))
        if meaningful < 15 or any(marker in text for marker in _MOJIBAKE_MARKERS):
            continue
        hits = sum(1 for term in terms if term in text.lower())
        cn_count = len(_CN_RE.findall(text))
        readability = _ratio(cn_count, max(meaningful, 1))
        length_score = min(1.0, meaningful / 120)
        candidates.append((hits * 4 + readability + length_score, text))
    selected = max(candidates, key=lambda item: item[0])[1] if candidates else _compact(raw)
    if len(selected) <= limit:
        return selected
    return selected[:limit].rstrip("，,；;。 ") + "…"


def _result_key(result: dict) -> str:
    source = _compact(result.get("source") or result.get("meta", {}).get("source", "")).lower()
    chapter = _compact(result.get("chapter") or result.get("meta", {}).get("chapter", "")).lower()
    if source or chapter:
        return f"{source}|{chapter}"
    preview = _compact(result.get("content_preview", ""))[:180].lower()
    return hashlib.md5(preview.encode("utf-8")).hexdigest()


def deduplicate_results(results: Iterable[dict], limit: int = 15) -> List[dict]:
    """按来源+章节合并重复命中，保留调整后分数最高的一条。"""
    best = {}
    for result in results:
        key = _result_key(result)
        previous = best.get(key)
        if previous is None or float(result.get("score", 0)) > float(previous.get("score", 0)):
            best[key] = result
    ordered = sorted(best.values(), key=lambda item: float(item.get("score", 0)), reverse=True)
    return ordered[:max(0, limit)]
