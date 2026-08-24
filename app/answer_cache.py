"""
问答结果缓存 — LRU + TTL + 相似度命中
====================================
避免相同/相似问题重复调用 LLM：
- 规范化：全角→半角 / 小写 / 去空白 / 数值区间统一 / 别名→规范词
- 命中：精确相等；或 余弦≥sim_threshold 且 中文bigram Jaccard≥jaccard_floor
- 指纹：已学书集合 hash，上传/删除书后指纹变化 → 缓存视为 miss
- 持久化到 saved_knowledge/answer_cache.json
"""

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from app.vector_store import feature_vec

_CN_CHAR_RE = None  # 延迟构造


def _char_set(s: str) -> set:
    """中文+数字字符集合（用于 Jaccard 二次护栏）。
    用字符集合而非 bigram：对"什么是氩弧焊"↔"氩弧焊是什么"这种语序调换的
    等价问法仍能高相似，同时"氩弧焊工艺"↔"氩弧焊机器人"（3/8≈0.38）被拦截。"""
    global _CN_CHAR_RE
    if _CN_CHAR_RE is None:
        import re
        _CN_CHAR_RE = re.compile(r'[一-鿿0-9]+')
    chars = set()
    for seg in _CN_CHAR_RE.findall(s):
        chars.update(seg)
    return chars


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


class AnswerCache:
    """LRU + TTL + 相似度命中 的问答结果缓存"""

    def __init__(self, max_entries: int = 200, ttl_seconds: int = 3600,
                 sim_threshold: float = 0.80, jaccard_floor: float = 0.80,
                 path: str = "saved_knowledge/answer_cache.json"):
        self.max_entries = max_entries
        self.ttl = ttl_seconds
        self.sim_threshold = sim_threshold
        self.jaccard_floor = jaccard_floor
        self.path = Path(path)
        self._entries: Dict[str, dict] = {}  # norm -> {payload, vec, fp, ts, hits}
        self._lock = threading.Lock()
        self._hits = 0
        self._exact_hits = 0
        self._similar_hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0
        self._invalidations = 0
        self._load()

    # ------------------------------------------------------------
    # 规范化
    # ------------------------------------------------------------
    @staticmethod
    def normalize(query: str) -> str:
        """规范化问题文本，用于缓存键。

        注意：不做别名→规范词替换。TERM_ALIAS_MAP 含大量有损映射
        （如 预热温度→预热），替换会破坏缓存键的区分度、把不同问题混同。
        同义/语序调换的近似问题由 余弦+Jaccard 相似度兜底。
        """
        s = query.strip()
        # 0. 繁简归一化（繁体→简体，缓存键统一，氩弧焊/氬弧焊 命中同一条缓存）
        try:
            from app.welding_qa_system import WeldingQASystem
            s = WeldingQASystem._normalize_text(s)
        except Exception:
            pass
        # 1. 全角→半角
        s = _full_to_half(s)
        # 2. 小写
        s = s.lower()
        # 3. 去空白（含数字/单位间的空格：'90 - 110 A' → '90-110a'）
        s = "".join(s.split())
        # 4. 查询语气标点不应产生不同缓存键；技术符号（- / + °）保留
        s = re.sub(r"[?!.。,，;；:：、'\"“”‘’]+", "", s)
        return s

    # ------------------------------------------------------------
    # 读 / 写
    # ------------------------------------------------------------
    def get(self, query: str, sources_fp: str) -> Optional[dict]:
        """命中返回缓存负载 dict，否则 None"""
        norm = self.normalize(query)
        if not norm:
            return None
        now = time.time()
        with self._lock:
            # 精确命中
            hit = self._entries.get(norm)
            if hit and (now - hit["ts"]) >= self.ttl:
                self._entries.pop(norm, None)
                self._expirations += 1
                hit = None
            if hit and hit["fp"] == sources_fp:
                hit["ts"] = now
                hit["hits"] = hit.get("hits", 0) + 1
                self._hits += 1
                self._exact_hits += 1
                self._touch(norm)
                return hit["payload"]

            # 相似命中（仅长度足够）
            if len(norm) >= 4:
                qvec = feature_vec(norm)
                q_grams = _char_set(norm)
                for key in list(self._entries.keys()):
                    e = self._entries[key]
                    if (now - e["ts"]) >= self.ttl:
                        self._entries.pop(key, None)
                        self._expirations += 1
                        continue
                    if e["fp"] != sources_fp:
                        continue
                    if e.get("vec") is None or qvec is None:
                        continue
                    cos = float(qvec @ e["vec"])
                    if cos >= self.sim_threshold:
                        j = _jaccard(q_grams, e.get("grams", set()))
                        if j >= self.jaccard_floor:
                            e["ts"] = now
                            e["hits"] = e.get("hits", 0) + 1
                            self._hits += 1
                            self._similar_hits += 1
                            self._touch(key)
                            return e["payload"]
            self._misses += 1
        return None

    def put(self, query: str, payload: dict, sources_fp: str) -> None:
        norm = self.normalize(query)
        if not norm:
            return
        with self._lock:
            # LRU 淘汰
            while len(self._entries) >= self.max_entries and norm not in self._entries:
                self._evict_lru()
            self._entries[norm] = {
                "payload": payload,
                "vec": feature_vec(norm),
                "grams": _char_set(norm),
                "fp": sources_fp,
                "ts": time.time(),
                "hits": 0,
            }
            self._touch(norm)
            self._save()

    def invalidate(self) -> int:
        """知识库变化时全清"""
        with self._lock:
            removed = len(self._entries)
            self._entries.clear()
            self._invalidations += 1
            self._save()
            return removed

    def stats(self) -> dict:
        with self._lock:
            requests = self._hits + self._misses
            return {
                "entries": len(self._entries),
                "size": len(self._entries),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl,
                "hits": self._hits,
                "hits_total": self._hits,
                "exact_hits": self._exact_hits,
                "similar_hits": self._similar_hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / requests, 4) if requests else 0.0,
                "evictions": self._evictions,
                "expirations": self._expirations,
                "invalidations": self._invalidations,
                "similarity_threshold": self.sim_threshold,
                "jaccard_floor": self.jaccard_floor,
            }

    def reset_stats(self) -> None:
        """Reset counters without dropping cached answers."""
        with self._lock:
            self._hits = 0
            self._exact_hits = 0
            self._similar_hits = 0
            self._misses = 0
            self._evictions = 0
            self._expirations = 0
            self._invalidations = 0

    # ------------------------------------------------------------
    # LRU 辅助
    # ------------------------------------------------------------
    def _touch(self, key: str):
        """把 key 移到队尾（LRU）——用 last_access 记录，_evict_lru 取最旧"""
        if key in self._entries:
            self._entries[key]["_last"] = time.time()

    def _evict_lru(self):
        if not self._entries:
            return
        oldest = min(self._entries, key=lambda k: self._entries[k].get("_last", 0))
        self._entries.pop(oldest, None)
        self._evictions += 1

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------
    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            ser = {}
            for k, e in self._entries.items():
                ser[k] = {
                    "payload": e["payload"],
                    "fp": e["fp"],
                    "ts": e["ts"],
                    "hits": e.get("hits", 0),
                }
            tmp = self.path.with_suffix(".tmp.json")
            tmp.write_text(json.dumps(ser, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    def _load(self):
        if not self.path.exists():
            return
        try:
            ser = json.loads(self.path.read_text(encoding="utf-8"))
            now = time.time()
            items = sorted(ser.items(), key=lambda item: item[1].get("ts", 0), reverse=True)
            for k, e in items[:self.max_entries]:
                if now - e.get("ts", 0) >= self.ttl:
                    self._expirations += 1
                    continue
                norm = k
                self._entries[norm] = {
                    "payload": e["payload"],
                    "vec": feature_vec(norm),
                    "grams": _char_set(norm),
                    "fp": e["fp"],
                    "ts": e["ts"],
                    "hits": e.get("hits", 0),
                    "_last": 0,
                }
        except Exception:
            self._entries.clear()


# ------------------------------------------------------------
# 全角→半角
# ------------------------------------------------------------
_FULL_HALF = {
    ord(c): ord(c) - 0xFEE0 for c in "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９"
    "（）：；，．！？～％℃＊＋－＝／"
}
_FULL_HALF.update({ord("　"): ord(" ")})
# 中文标点统一为半角便于规范化
_FULL_HALF.update({ord("？"): ord("?"), ord("！"): ord("!"), ord("～"): ord("~"),
                   ord("℃"): ord("°")})


def _full_to_half(s: str) -> str:
    return s.translate(_FULL_HALF)


def normalize(query: str) -> str:
    """模块级便捷函数：规范化问题文本"""
    return AnswerCache.normalize(query)


# ------------------------------------------------------------
# 指纹
# ------------------------------------------------------------
def sources_fingerprint(store) -> str:
    """Fingerprint persisted knowledge metadata, not just source names.

    Relearning a book under the same filename changes its counts/text length and
    must invalidate both cached answers and generated indexes.
    """
    sources = []
    for source in store.list_sources():
        source_dir = Path(getattr(store, "store_dir", "saved_knowledge")) / source.get("id", "")
        file_state = {}
        for filename in ("chapters.json", "full_text.txt", "keywords.json",
                         "data_points.json", "tables.json"):
            path = source_dir / filename
            if path.exists():
                stat = path.stat()
                file_state[filename] = [stat.st_size, stat.st_mtime_ns]
        sources.append({
            "id": source.get("id", ""),
            "filename": source.get("filename", ""),
            "chapter_count": source.get("chapter_count", 0),
            "keyword_count": source.get("keyword_count", 0),
            "table_count": source.get("table_count", 0),
            "text_length": source.get("text_length", 0),
            "learned_at": source.get("learned_at", source.get("updated_at", "")),
            "files": file_state,
        })
    sources.sort(key=lambda item: (item["id"], item["filename"]))
    raw = json.dumps(sources, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# ------------------------------------------------------------
# 单例
# ------------------------------------------------------------
_cache: Optional[AnswerCache] = None


def get_cache() -> AnswerCache:
    global _cache
    if _cache is None:
        try:
            from app.llm_service import load_config
            cfg = load_config().get("cache", {}) or {}
        except Exception:
            cfg = {}
        _cache = AnswerCache(
            max_entries=int(cfg.get("max_entries", 200)),
            ttl_seconds=int(cfg.get("ttl_seconds", 3600)),
            sim_threshold=float(cfg.get("sim_threshold", 0.80)),
            jaccard_floor=float(cfg.get("jaccard_floor", 0.80)),
        )
    return _cache
