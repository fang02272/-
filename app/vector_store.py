"""
本地增量向量库 — 特征哈希 + numpy 余弦相似度
============================================
零外部依赖（仅 numpy），用于把多本焊接书籍知识压缩为向量索引。

设计要点：
- 特征：领域术语 / 别名反查规范词 / 型号钢号 / 中文bigram / 英文数字 五类加权
- 哈希：md5(feature) → 固定维度索引，符号取哈希字节奇偶，L2 归一化
- 检索：matrix @ qvec 一次 matmul 得全库余弦
- 持久化：saved_knowledge/vector_index/（index.npy + ids.json + meta.json + manifest.json）
- 增量：新书只 add_document；重学同名书 remove_by_source 替换，不上传不重建
"""

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

try:
    import numpy as np
except ImportError:
    np = None

# ------------------------------------------------------------
# 焊接领域词表（用于特征加权 + 别名反查）
# ------------------------------------------------------------
_DOMAIN_TERMS: set = set()
_ALIAS_REVERSE: Dict[str, str] = {}
try:
    from app.welding_knowledge_base import KEYWORD_CATEGORY_MAP, TERM_ALIAS_MAP
    _DOMAIN_TERMS = {t for t in KEYWORD_CATEGORY_MAP if len(str(t)) >= 2}
    for _canonical, _aliases in TERM_ALIAS_MAP.items():
        for _a in _aliases:
            _a = str(_a).strip()
            if _a and _a not in _ALIAS_REVERSE:
                _ALIAS_REVERSE[_a] = str(_canonical)
except ImportError:
    pass


# ------------------------------------------------------------
# 特征化
# ------------------------------------------------------------
_MODEL_RE = re.compile(r'[A-Z]{1,4}[\-\s]?\d{2,5}[A-Z0-9\-]{0,6}', re.IGNORECASE)
_EN_RE = re.compile(r'[a-zA-Z0-9]+')
_CN_RE = re.compile(r'[一-鿿]+')


# jieba 分词器（融合 git jieba 分支：注册焊接术语防误切 + 词级特征）
_jieba = None


def _get_jieba():
    """懒加载 jieba.Tokenizer，并把焊接领域术语注册进去（避免专业词被错误拆分）"""
    global _jieba
    if _jieba is None:
        import jieba
        _jieba = jieba.Tokenizer()
        for term in _DOMAIN_TERMS:
            try:
                _jieba.add_word(term)
            except Exception:
                pass
        for alias in _ALIAS_REVERSE:
            try:
                _jieba.add_word(alias)
            except Exception:
                pass
    return _jieba


def tokenize(text: str) -> List[str]:
    """文本 → 加权 token 流（jieba 词级 + 领域术语 + 型号，保留重复用于频次加权）"""
    if not text:
        return []
    text = text.lower()
    tokens: List[str] = []
    # 1. 领域术语精确匹配（加权×3）
    for term in _DOMAIN_TERMS:
        if term in text:
            tokens.extend([f"T:{term}"] * 3)
    # 2. 别名反查规范词（加权×3）
    for alias, canonical in _ALIAS_REVERSE.items():
        if alias in text:
            tokens.extend([f"A:{canonical}"] * 3)
    # 3. 型号/钢号/牌号（大写去空格，加权×2）
    for m in _MODEL_RE.findall(text):
        tokens.extend([f"M:{m.upper().replace(' ', '')}"] * 2)
    # 4. jieba 词级特征（融合 jieba 分支，替代纯 bigram，专业词已注册不会被拆分）
    try:
        for w in _get_jieba().lcut(text):
            w = w.strip().lower()
            if not w:
                continue
            if _CN_RE.fullmatch(w) and len(w) >= 2:
                tokens.append(f"W:{w}")
            elif re.fullmatch(r'[a-z0-9]{2,}', w):
                tokens.append(f"W:{w}")
    except Exception:
        pass
    # 5. 中文 bigram 兜底（×1，保证未登录词仍有重叠特征）
    for seg in _CN_RE.findall(text):
        for i in range(len(seg) - 1):
            tokens.append(f"B:{seg[i:i + 2]}")
    # 6. 英文/数字词兜底
    for w in _EN_RE.findall(text):
        if not w.isdigit() or len(w) >= 3:
            tokens.append(f"W:{w}")
    return tokens


def _hash_idx(token: str, dim: int) -> tuple:
    """token → (索引, 符号)。符号取哈希第二字节奇偶，降低哈希碰撞同号偏差"""
    h = hashlib.md5(token.encode("utf-8")).hexdigest()
    idx = int(h[:8], 16) % dim
    sign = 1.0 if (ord(h[16]) % 2 == 0) else -1.0
    return idx, sign


def feature_vec(text: str, dim: int = 4096):
    """文本 → L2 归一化特征向量（numpy.ndarray, float32）"""
    if np is None:
        return None
    vec = np.zeros(dim, dtype=np.float32)
    for tok in tokenize(text):
        idx, sign = _hash_idx(tok, dim)
        vec[idx] += sign
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec


# ------------------------------------------------------------
# 向量索引
# ------------------------------------------------------------
class VectorIndex:
    """增量向量库：add/search/remove/save/load"""

    VERSION = 1

    def __init__(self, dim: int = 4096, index_dir: str = "saved_knowledge/vector_index"):
        self.dim = dim
        self.index_dir = Path(index_dir)
        self.ids: List[str] = []
        self.meta: Dict[str, dict] = {}
        self._vecs: List = []  # list[np.ndarray]
        self._lock = threading.RLock()

    # ---------- 写入 ----------
    def add_document(self, doc_id: str, text: str, meta: dict = None) -> None:
        """新增一个文档向量（幂等：同 doc_id 先删旧）"""
        with self._lock:
            if doc_id in self.ids:
                self.remove_document(doc_id)
            vec = feature_vec(text, self.dim)
            if vec is None:
                return
            self.ids.append(doc_id)
            self._vecs.append(vec)
            self.meta[doc_id] = meta or {}

    def add_batch(self, docs: List[dict]) -> None:
        """docs=[{doc_id, text, meta}]"""
        for d in docs:
            self.add_document(d["doc_id"], d["text"], d.get("meta"))

    def remove_document(self, doc_id: str) -> None:
        with self._lock:
            if doc_id in self.ids:
                i = self.ids.index(doc_id)
                self.ids.pop(i)
                self._vecs.pop(i)
                self.meta.pop(doc_id, None)

    def remove_by_source(self, source: str) -> None:
        """删除某本书的所有向量（source 为 meta['source']，重学/删书时替换）"""
        with self._lock:
            doomed_ids = [d for d in self.ids if self.meta.get(d, {}).get("source") == source]
            for d in doomed_ids:
                self.remove_document(d)

    # ---------- 检索 ----------
    def search(self, query: str, top_k: int = 8) -> List[dict]:
        """余弦检索 → [{doc_id, score, meta}]"""
        if np is None or not self._vecs:
            return []
        qvec = feature_vec(query, self.dim)
        if qvec is None or np.linalg.norm(qvec) == 0:
            return []
        matrix = np.stack(self._vecs).astype(np.float32)
        scores = matrix @ qvec
        top = int(min(top_k, len(self.ids)))
        if top <= 0:
            return []
        idx = np.argsort(-scores)[:top]
        return [
            {
                "doc_id": self.ids[i],
                "score": round(float(scores[i]), 4),
                "meta": self.meta.get(self.ids[i], {}),
            }
            for i in idx
        ]

    # ---------- 持久化 ----------
    def save(self) -> None:
        """原子写盘：临时文件 + os.replace"""
        self.index_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            matrix = np.stack(self._vecs).astype(np.float32) if self._vecs else np.zeros((0, self.dim), dtype=np.float32)
            tmp_npy = self.index_dir / "index.tmp.npy"
            np.save(tmp_npy, matrix)
            os.replace(tmp_npy, self.index_dir / "index.npy")

            for name, data in (
                ("ids", self.ids),
                ("meta", self.meta),
                ("manifest", {
                    "version": self.VERSION,
                    "dim": self.dim,
                    "rows": len(self.ids),
                    "sources": self._source_counts(),
                    "built_at": _now_iso(),
                }),
            ):
                tmp = self.index_dir / f"{name}.tmp.json"
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, self.index_dir / f"{name}.json")

    def load(self) -> bool:
        """加载索引，成功返回 True。索引缺失/版本不符返回 False"""
        npy = self.index_dir / "index.npy"
        ids_p = self.index_dir / "ids.json"
        meta_p = self.index_dir / "meta.json"
        if not (npy.exists() and ids_p.exists()):
            return False
        try:
            matrix = np.load(npy)
            self.ids = json.loads(ids_p.read_text(encoding="utf-8"))
            self.meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
            if matrix.shape[0] != len(self.ids) or matrix.shape[1] != self.dim:
                return False
            self._vecs = [matrix[i] for i in range(matrix.shape[0])]
            return True
        except Exception:
            return False

    def clear_all(self) -> None:
        with self._lock:
            self.ids = []
            self.meta = {}
            self._vecs = []

    def stats(self) -> dict:
        return {
            "rows": len(self.ids),
            "dim": self.dim,
            "sources": self._source_counts(),
            "kinds": _kind_counts(self.ids),
        }

    def _source_counts(self) -> Dict[str, int]:
        cnt = {}
        for d in self.ids:
            src = self.meta.get(d, {}).get("source", "unknown")
            cnt[src] = cnt.get(src, 0) + 1
        return cnt


def _now_iso() -> str:
    """当前时间字符串（不用 datetime.now 也会正常工作）"""
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def _kind_counts(ids: list) -> Dict[str, int]:
    cnt = {}
    for d in ids:
        kind = d.split("/")[0] if "/" in d else "other"
        cnt[kind] = cnt.get(kind, 0) + 1
    return cnt


# ------------------------------------------------------------
# 便捷函数：把一本书的章节批量入库
# ------------------------------------------------------------
def index_book_chapters(store, source_id: str, vi: Optional[VectorIndex] = None) -> VectorIndex:
    """把一本书的章节文本向量化入库。返回 VectorIndex（复用或新建）"""
    if vi is None:
        vi = VectorIndex()
    src = store.get_source(source_id)
    if not src:
        return vi
    vi.remove_by_source(src["filename"])
    chapters = store.get_chapters(source_id)
    for i, ch in enumerate(chapters):
        text = f"《{src['filename']}》 {ch.get('title','')}\n{ch.get('summary','')}\n{ch.get('content','')[:1500]}"
        vi.add_document(
            f"book/{source_id}/{i}",
            text,
            meta={
                "source": src["filename"],
                "book": src["filename"],
                "chapter": ch.get("title", ""),
                "page_hint": ch.get("page_hint", ""),
                "kind": "book",
            },
        )
    return vi


# ------------------------------------------------------------
# 单例
# ------------------------------------------------------------
_index: Optional[VectorIndex] = None


def get_index(index_dir: str = "saved_knowledge/vector_index") -> VectorIndex:
    global _index
    if _index is None:
        _index = VectorIndex(index_dir=index_dir)
        _index.load()
    return _index
