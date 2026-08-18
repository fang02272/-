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
    """懒加载 jieba.Tokenizer，焊接术语高频注册（freq 高 → 不被错误拆分）"""
    global _jieba
    if _jieba is None:
        import jieba
        _jieba = jieba.Tokenizer()
        # 术语高频注册：freq 大 → jieba 倾向保留整词
        for term in _DOMAIN_TERMS:
            try:
                _jieba.add_word(term, freq=50000)
            except Exception:
                pass
        for alias in _ALIAS_REVERSE:
            try:
                _jieba.add_word(alias, freq=30000)
            except Exception:
                pass
    return _jieba


def _jieba_cut(text: str) -> list:
    """jieba 搜索模式分词：多粒度切分提升召回（保留完整专业词 + 子词）"""
    try:
        return _get_jieba().cut_for_search(text)
    except Exception:
        return []


def tokenize(text: str) -> List[str]:
    """文本 → 加权 token 流（jieba 多粒度 + 领域术语 + 型号，保留重复用于频次加权）"""
    if not text:
        return []
    # 繁简归一化（繁体→简体，检索一致）
    try:
        from app.welding_qa_system import WeldingQASystem
        text = WeldingQASystem._normalize_text(text)
    except Exception:
        pass
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
    # 4. jieba 搜索模式多粒度词（融合 git jieba 分支：jieba 替换 n-gram，专业词已注册不会被拆散）
    for w in _jieba_cut(text):
        w = w.strip().lower()
        if not w:
            continue
        if _CN_RE.fullmatch(w) and len(w) >= 2:
            tokens.append(f"W:{w}")
        elif re.fullmatch(r'[a-z0-9]{2,}', w):
            tokens.append(f"W:{w}")
    # 5. 英文/数字词兜底（保持英文检索，不产生中文机械双字）
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
# 语义向量（bge 中文 embedding，懒加载）
# ------------------------------------------------------------
_sem_model = None
_SEM_DIM = 512


def _get_sem_model():
    """懒加载 bge 语义向量模型（modelscope 下载）"""
    global _sem_model
    if _sem_model is not None:
        return _sem_model
    try:
        from sentence_transformers import SentenceTransformer
        import os
        # 找模型路径
        candidates = [
            "models/models/AI-ModelScope--bge-small-zh-v1.5/snapshots/master",
            "models/AI-ModelScope/bge-small-zh-v1.5",
        ]
        path = next((p for p in candidates if os.path.isdir(p)), None)
        if path:
            _sem_model = SentenceTransformer(path)
            _SEM_DIM = 512
            return _sem_model
    except Exception:
        pass
    return None


def semantic_vec(text: str):
    """文本 → 语义向量（512维），模型不可用返回 None"""
    model = _get_sem_model()
    if model is None or not text:
        return None
    try:
        import numpy as _np
        v = model.encode([text], normalize_embeddings=True)[0]
        return _np.asarray(v, dtype=_np.float32)
    except Exception:
        return None


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
        self._vecs: List = []  # list[np.ndarray] 特征向量
        self._sem_vecs: List = []  # list[np.ndarray|None] 语义向量
        self._lock = threading.RLock()

    # ---------- 写入 ----------
    def add_document(self, doc_id: str, text: str, meta: dict = None) -> None:
        """新增一个文档向量（幂等：同 doc_id 先删旧）。
        存特征向量 + 语义向量（bge），检索时融合。"""
        with self._lock:
            if doc_id in self.ids:
                self.remove_document(doc_id)
            vec = feature_vec(text, self.dim)
            if vec is None:
                return
            sem = semantic_vec(text)  # 语义向量（模型不可用返回 None）
            self.ids.append(doc_id)
            self._vecs.append(vec)
            if not hasattr(self, "_sem_vecs"):
                self._sem_vecs = []
            self._sem_vecs.append(sem)
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
                if hasattr(self, "_sem_vecs") and len(self._sem_vecs) > i:
                    self._sem_vecs.pop(i)
                self.meta.pop(doc_id, None)

    def remove_by_source(self, source: str) -> None:
        """删除某本书的所有向量（source 为 meta['source']，重学/删书时替换）"""
        with self._lock:
            doomed_ids = [d for d in self.ids if self.meta.get(d, {}).get("source") == source]
            for d in doomed_ids:
                self.remove_document(d)

    # ---------- 检索 ----------
    def search(self, query: str, top_k: int = 8) -> List[dict]:
        """余弦检索（特征向量 + 语义向量融合）→ [{doc_id, score, meta}]"""
        if np is None or not self._vecs:
            return []
        qvec = feature_vec(query, self.dim)
        if qvec is None or np.linalg.norm(qvec) == 0:
            return []
        matrix = np.stack(self._vecs).astype(np.float32)
        scores = matrix @ qvec

        # 语义向量融合（模型可用时提升语义相关命中）
        qsem = semantic_vec(query)
        if qsem is not None and hasattr(self, "_sem_vecs") and self._sem_vecs:
            sem_mask = [i for i, v in enumerate(self._sem_vecs) if v is not None]
            if sem_mask:
                sem_matrix = np.stack([self._sem_vecs[i] for i in sem_mask]).astype(np.float32)
                sem_scores = sem_matrix @ qsem
                # 特征 0.7 + 语义 0.3 融合
                for rank, i in enumerate(sem_mask):
                    if len(self.ids) > 0:
                        scores[i] = 0.7 * scores[i] + 0.3 * sem_scores[rank]

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
            # 语义向量：加载后懒重建（模型可用时 batch 编码）
            self._sem_vecs = [None] * len(self.ids)
            return True
        except Exception:
            return False

    def rebuild_semantic_vectors(self, texts: list = None):
        """批量重建语义向量（加载后调用）。texts 长度需与 ids 一致，否则从 meta 内容重建。"""
        if not self.ids:
            return
        model = _get_sem_model()
        if model is None:
            self._sem_vecs = [None] * len(self.ids)
            return
        try:
            if texts is None or len(texts) != len(self.ids):
                texts = [self.meta.get(d, {}).get("chapter", d) for d in self.ids]
            import numpy as _np
            vecs = model.encode(texts, normalize_embeddings=True, batch_size=32)
            self._sem_vecs = [_np.asarray(v, dtype=_np.float32) for v in vecs]
        except Exception:
            self._sem_vecs = [None] * len(self.ids)

    def clear_all(self) -> None:
        with self._lock:
            self.ids = []
            self.meta = {}
            self._vecs = []
            self._sem_vecs = []

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
