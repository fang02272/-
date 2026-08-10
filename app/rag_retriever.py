"""
RAG 检索增强生成 — 从知识库和上传PDF中检索相关内容，构建LLM prompt上下文
使用 TF-IDF + 余弦相似度进行简单但有效的检索
"""

import math
import os
import re
from collections import defaultdict
from typing import List, Dict, Tuple, Optional

# ============================================================
# 简易 TF-IDF 检索器
# ============================================================
class SimpleRetriever:
    """轻量级 TF-IDF 文本检索器，无需外部依赖"""

    def __init__(self):
        self.documents: List[dict] = []       # [{"id": str, "text": str, "source": str}, ...]
        self.doc_freq: Dict[str, int] = defaultdict(int)  # 词 → 出现文档数
        self.term_index: Dict[str, List[int]] = defaultdict(list)  # 词 → 文档ID列表
        self.doc_count = 0
        # 融合 git jieba 分支：术语高频注册，防专业词被错误拆分
        try:
            import jieba
            self._tokenizer = jieba.Tokenizer()
            from app.welding_knowledge_base import KEYWORD_CATEGORY_MAP, TERM_ALIAS_MAP
            for term in KEYWORD_CATEGORY_MAP:
                if len(str(term)) >= 2:
                    try:
                        self._tokenizer.add_word(str(term), freq=50000)
                    except Exception:
                        pass
            for aliases in TERM_ALIAS_MAP.values():
                for a in aliases:
                    if len(str(a)) >= 2:
                        try:
                            self._tokenizer.add_word(str(a), freq=30000)
                        except Exception:
                            pass
        except Exception:
            self._tokenizer = None

    def _tokenize(self, text: str) -> List[str]:
        """中文+英文混合分词（jieba 搜索模式多粒度 + bigram 兜底）"""
        tokens = []
        # jieba 搜索模式（多粒度，提升召回）
        if self._tokenizer is not None:
            try:
                for w in self._tokenizer.cut_for_search(text):
                    w = w.strip().lower()
                    if not w:
                        continue
                    if re.fullmatch(r'[一-鿿]{2,}', w) or re.fullmatch(r'[a-z0-9]{2,}', w):
                        tokens.append(w)
            except Exception:
                pass
        # bigram 兜底（未登录词重叠特征）
        chinese = re.findall(r'[一-鿿]+', text)
        for seg in chinese:
            for i in range(len(seg) - 1):
                tokens.append(seg[i:i+2])
        # 英文/数字词
        english = re.findall(r'[a-zA-Z0-9]+', text)
        tokens.extend([t.lower() for t in english])
        return tokens

    def add_document(self, doc_id: str, text: str, source: str = ""):
        """添加文档到索引"""
        tokens = self._tokenize(text)
        unique_tokens = set(tokens)
        doc_idx = len(self.documents)

        for token in unique_tokens:
            self.doc_freq[token] += 1
            self.term_index[token].append(doc_idx)

        self.documents.append({"id": doc_id, "text": text, "source": source, "tokens": tokens})
        self.doc_count = len(self.documents)

    def clear_external(self):
        """清除上传的PDF文档（保留内置知识库文档）"""
        self.documents = [d for d in self.documents if d["source"] not in ("pdf_upload",)]
        self.doc_count = len(self.documents)
        # 重建索引
        self.doc_freq.clear()
        self.term_index.clear()
        for i, doc in enumerate(self.documents):
            unique_tokens = set(doc["tokens"])
            for token in unique_tokens:
                self.doc_freq[token] += 1
                self.term_index[token].append(i)

    def _tfidf_vector(self, tokens: List[str]) -> Dict[int, float]:
        """计算词的 TF-IDF 向量 {token_id: weight}"""
        tf = defaultdict(int)
        for t in tokens:
            tf[t] += 1
        max_tf = max(tf.values()) if tf else 1
        vec = {}
        for t, f in tf.items():
            df = self.doc_freq.get(t, 0)
            if df > 0:
                vec[t] = (f / max_tf) * math.log(self.doc_count / (df + 1)) + 1
        return vec

    def search(self, query: str, top_k: int = 10) -> List[dict]:
        """检索与查询最相关的文档片段"""
        if not self.documents:
            return []

        query_tokens = self._tokenize(query)
        query_vec = self._tfidf_vector(query_tokens)
        if not query_vec:
            return []

        # 计算余弦相似度
        scores = []
        query_norm = math.sqrt(sum(w * w for w in query_vec.values()))
        for i, doc in enumerate(self.documents):
            doc_vec = self._tfidf_vector(doc["tokens"])
            if not doc_vec:
                continue
            dot = sum(query_vec.get(t, 0) * doc_vec.get(t, 0) for t in query_vec)
            doc_norm = math.sqrt(sum(w * w for w in doc_vec.values()))
            if doc_norm > 0 and query_norm > 0:
                sim = dot / (doc_norm * query_norm)
                if sim > 0.01:
                    scores.append((i, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        top_docs = scores[:top_k]
        return [{
            "id": self.documents[i]["id"],
            "text": self.documents[i]["text"],
            "source": self.documents[i]["source"],
            "score": round(s, 4),
        } for i, s in top_docs]


# ============================================================
# RAG Context Builder
# ============================================================
class RAGContextBuilder:
    """从知识库和上传文档构建 LLM prompt 上下文"""

    def __init__(self):
        self.retriever = SimpleRetriever()
        self._init_book_knowledge()

    def _init_book_knowledge(self):
        """索引《材料焊接原理》知识库内容"""
        try:
            from welding_knowledge_base import (
                SCIENCE_POPULARIZATION,
                DEEP_ANALYSIS,
                CROSS_DOMAIN_KNOWLEDGE,
            )
            # 索引科普内容
            for key, content in SCIENCE_POPULARIZATION.items():
                if content.strip():
                    self.retriever.add_document(
                        f"book_science_{key}",
                        content.strip(),
                        "book_science"
                    )
            # 索引深度分析
            for key, data in DEEP_ANALYSIS.items():
                if isinstance(data, dict):
                    text = data.get("overview", "")
                    for sec_title, sec_content in data.get("sections", {}).items():
                        text += f"\n{sec_title}\n{sec_content}"
                    self.retriever.add_document(
                        f"book_deep_{key}",
                        text.strip(),
                        "book_deep"
                    )
            # 索引交叉领域知识
            for key, data in CROSS_DOMAIN_KNOWLEDGE.items():
                if isinstance(data, dict):
                    text = data.get("description", "")
                    for k, v in data.get("practical_guidance", {}).items():
                        text += f"\n{k}: {v}"
                    for item in data.get("related_book_content", []):
                        text += f"\n{item}"
                    self.retriever.add_document(
                        f"book_cross_{key}",
                        text.strip(),
                        "book_cross"
                    )
        except ImportError:
            pass

    def add_pdf_document(self, filename: str, text: str):
        """添加上传PDF的文本到索引"""
        # 分块索引
        chunk_size = 800
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
        for i, chunk in enumerate(chunks):
            self.retriever.add_document(
                f"pdf_{filename}_chunk{i}",
                chunk.strip(),
                "pdf_upload"
            )

    def remove_pdf_documents(self):
        """移除所有PDF上传文档"""
        self.retriever.clear_external()

    def build_context(self, query: str, top_k: int = 10) -> str:
        """
        构建 RAG 上下文 — PDF内容优先展示
        """
        results = self.retriever.search(query, top_k=top_k)
        if not results:
            return ""

        parts = []
        book_parts = []
        pdf_parts = []

        for r in results:
            source_type = r["source"]
            entry = f"[相关度: {r['score']}] {r['text'][:600]}"
            if "pdf" in source_type:
                pdf_parts.append(entry)
            else:
                book_parts.append(entry)

        # PDF内容优先放在前面
        if pdf_parts:
            parts.append("## 📄 上传工艺资料相关内容（优先参考）\n" + "\n\n---\n\n".join(pdf_parts[:5]))
        if book_parts:
            parts.append("## 📖 《材料焊接原理》相关内容\n" + "\n\n---\n\n".join(book_parts[:5]))

        return "\n\n".join(parts)


# ============================================================
# Singleton
# ============================================================
_rag: Optional[RAGContextBuilder] = None


def get_rag() -> RAGContextBuilder:
    global _rag
    if _rag is None:
        _rag = RAGContextBuilder()
    return _rag
