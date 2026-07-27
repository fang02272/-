"""
焊接工艺专家系统 Web 服务
==========================
FastAPI 后端 — LLM驱动 + RAG检索 + PDF导入 + 书籍知识库
"""

import sys
import os
import time
from pathlib import Path
from typing import List, Dict

# 将项目根目录加入 path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

from welding_qa_system import WeldingQASystem
from welding_knowledge_base import KNOWLEDGE_CATEGORIES
from knowledge_store import get_store

# ---- FastAPI ----
app = FastAPI(
    title="焊接工艺专家系统",
    description="LLM驱动的焊接知识智能问答 + PDF知识库扩展 + RAG检索增强",
    version="2.0.0",
)
app.add_middleware(GZipMiddleware, minimum_size=500)

# ---- 全局初始化 ----
qa_system = WeldingQASystem()

# LLM / RAG / PDF 组件延迟加载
_llm_client = None
_rag_builder = None
_pdf_parser = None


def _get_llm():
    global _llm_client
    if _llm_client is None:
        from llm_service import get_client
        _llm_client = get_client()
    return _llm_client


def _get_rag():
    global _rag_builder
    if _rag_builder is None:
        from rag_retriever import get_rag
        _rag_builder = get_rag()
    return _rag_builder


def _get_parser():
    global _pdf_parser
    if _pdf_parser is None:
        from pdf_parser import get_parser
        _pdf_parser = get_parser()
    return _pdf_parser


# ---- Pydantic Models ----
class QueryRequest(BaseModel):
    query: str


class QueryResponse(BaseModel):
    query: str
    keywords: List[str]
    matched_categories: List[str]
    is_cross_domain: bool
    is_empty: bool
    model_used: str
    elapsed_ms: float
    content: str = ""
    sections: Dict[str, dict] = {}
    mermaid_blocks: List[str] = []
    tables: List[str] = []
    references: List[str] = []
    disclaimer: str = ""


# ============================================================
# API 端点
# ============================================================

@app.get("/api/health")
async def health():
    llm = _get_llm()
    return {
        "status": "ok",
        "service": "焊接工艺专家系统 v2.0",
        "llm_available": llm.available,
        "llm_model": llm.model if llm.available else None,
    }


# ============================================================
# 知识库自检 (无需LLM)
# ============================================================
@app.get("/api/knowledge/inspect")
async def inspect_knowledge(q: str = ""):
    """
    检查知识库状态 — 不调用LLM，纯本地检查
    可选参数 q: 测试关键词匹配
    """
    store = get_store()
    sources = store.list_sources()

    books = []
    for src in sources:
        chs = store.get_chapters(src["id"])
        books.append({
            "name": src["filename"],
            "id": src["id"],
            "pages": src.get("page_count", "?"),
            "chapters": src.get("chapter_count", 0),
            "keywords": src.get("keyword_count", 0),
            "tables": src.get("table_count", 0),
            "chapter_list": [
                {
                    "title": c["title"],
                    "keywords": c.get("keywords", [])[:10],
                    "content_len": c.get("content_length", 0),
                }
                for c in chs[:30]
            ],
        })

    # 如果带了查询参数，测试匹配
    match_test = None
    if q:
        qa_system.load_external_knowledge([
            {
                "filename": s["filename"],
                "keywords": store.get_keywords(s["id"]),
                "chapters": store.get_chapters(s["id"]),
            }
            for s in sources
        ])
        kws = qa_system.extract_keywords(q)
        cats = qa_system.match_categories(kws)
        cross = store.search_across_sources(q)
        match_test = {
            "query": q,
            "matched_keywords": kws,
            "categories": {k: v for k, v in cats.items()},
            "top_chapters": [
                {
                    "book": m["source"],
                    "chapter": m["chapter"],
                    "score": m["score"],
                    "matched_keywords": m.get("matched_keywords", []),
                }
                for m in cross[:10]
            ],
        }

    return {
        "total_books": len(books),
        "books": books,
        "match_test": match_test,
    }


@app.get("/api/config/status")
async def config_status():
    llm = _get_llm()
    pdfs = _get_parser().list_uploads()
    return {
        "llm_available": llm.available,
        "llm_model": llm.model,
        "llm_endpoint": llm.api_base,
        "uploaded_pdfs": len(pdfs),
        "pdf_files": [p["name"] for p in pdfs],
    }


@app.get("/api/categories")
async def get_categories():
    """返回完整知识目录：原书 + 已学习的上传PDF"""
    tree = []
    # 原书结构
    for part_key, part_info in KNOWLEDGE_CATEGORIES.items():
        part_node = {
            "key": part_key,
            "name": part_info["name"],
            "description": part_info["description"],
            "chapters": [],
            "source": "book",
        }
        for ch_key, ch_info in part_info["chapters"].items():
            ch_node = {
                "key": ch_key,
                "title": ch_info["title"],
                "sections": [
                    {"id": sec_id, "summary": desc[:80]}
                    for sec_id, desc in ch_info["sections"].items()
                ],
            }
            part_node["chapters"].append(ch_node)
        tree.append(part_node)

    # 已学习的上传PDF — 完整展示每章元数据
    store = get_store()
    for src in store.list_sources():
        chapters_data = src.get("chapters", [])
        src_node = {
            "key": f"uploaded_{src['id']}",
            "name": f"📄 {src['filename']}",
            "description": f"{src.get('page_count', '?')}页 · {src.get('chapter_count', 0)}章 · {src.get('keyword_count', 0)}关键词 · {src.get('table_count', 0)}表格",
            "chapters": [],
            "source": "uploaded",
        }
        for ch in chapters_data[:30]:
            kw_count = ch.get("keyword_count", 0)
            cl = ch.get("content_length", 0)
            summary = ch.get("summary", "")[:80]
            meta = []
            if kw_count: meta.append(f"{kw_count}关键词")
            if cl: meta.append(f"{(cl/1000):.0f}K字")
            src_node["chapters"].append({
                "key": f"uploaded_{src['id']}_{ch.get('title', '')[:20]}",
                "title": ch.get("title", "")[:80],
                "sections": [],
                "meta": " · ".join(meta) if meta else "",
                "summary": summary,
                "keywords": ch.get("keywords", [])[:8],
            })
        tree.append(src_node)

    return {"parts": tree, "total_chapters": sum(len(p["chapters"]) for p in tree),
            "uploaded_sources": len(store.list_sources())}



@app.post("/api/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """主问答接口 — RAG + LLM 生成专家级回答"""
    q = req.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="查询内容不能为空")

    t0 = time.perf_counter()

    # --- Step 0: 注入外部知识源到匹配系统 ---
    store = get_store()
    uploaded_files = store.list_sources()
    uploaded_names = [s["filename"] for s in uploaded_files]

    # 将所有已学习PDF的关键词注入QA系统，实现统一匹配
    external_kw_list = []
    for src in uploaded_files:
        kws = store.get_keywords(src["id"])
        chapters = store.get_chapters(src["id"])
        external_kw_list.append({
            "filename": src["filename"],
            "keywords": kws,
            "chapters": [{"title": c["title"], "summary": c.get("summary", ""),
                          "keywords": c.get("keywords", [])} for c in chapters],
        })
    qa_system.load_external_knowledge(external_kw_list)

    # --- Step 1: 本地分析（现在匹配原书 + 所有已学习PDF的关键词）---
    try:
        result = qa_system.generate_structured(q)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"知识分析出错: {str(e)}")

    keywords = result.get("keywords", [])
    categories = result.get("matched_categories", [])

    # --- Step 2: RAG 检索 ---
    rag = _get_rag()
    rag_context = rag.build_context(q)

    # 获取已学习知识源信息
    store = get_store()
    knowledge_catalog = store.build_knowledge_catalog()
    uploaded_files = store.list_sources()
    uploaded_names = [s["filename"] for s in uploaded_files]

    # 跨知识源搜索匹配内容
    cross_source_matches = store.search_across_sources(q)
    # 将匹配到的上传资料关键词加入关键词列表
    for src in uploaded_files:
        for kw in src.get("all_keywords", []):
            if kw in q and kw not in keywords:
                keywords.append(kw)
    if cross_source_matches:
        matched_sources = list(set(m["source"] for m in cross_source_matches))
        categories = categories + [f"📄 {s}" for s in matched_sources]

    # --- Step 3: LLM 生成 ---
    llm = _get_llm()
    llm_text = None
    if llm.available:
        try:
            llm_text = llm.chat_sync(
                q,
                context=rag_context,
                uploaded_files=uploaded_names,
                knowledge_catalog=knowledge_catalog,
                cross_source_matches=cross_source_matches,
            )
        except Exception:
            llm_text = None

    # --- Step 4: 构建响应 ---
    if llm_text and len(llm_text.strip()) > 50:
        # LLM 成功生成 → 用专家回答
        from llm_service import parse_llm_response
        parsed = parse_llm_response(llm_text, q, keywords, categories)
        model_used = llm.model

        sections = {
            "expert_analysis": {
                "title": "🔍 专家分析",
                "icon": "🔍",
                "content": llm_text,
                "visible": True,
            },
            "recommendations": {
                "title": "📋 延伸建议",
                "icon": "📋",
                "items": result.get("sections", {}).get("recommendations", {}).get("items", []),
                "visible": True,
            },
            "sources": {
                "title": "📚 参考来源",
                "icon": "📚",
                "primary": parsed.get("references", []) or result.get("sections", {}).get("sources", {}).get("primary", []),
                "extended": [],
                "visible": True,
            },
        }
        mermaid_blocks = parsed.get("mermaid_blocks", [])
        tables = parsed.get("tables", [])
        references = parsed.get("references", [])
        content = llm_text
    else:
        # LLM 不可用或失败 → 降级为本地知识库模式
        model_used = "local_knowledge_base"
        sections = result.get("sections", {})
        # 移除交叉分析(本地模式无此需求)
        if "cross_analysis" in sections:
            sections.pop("cross_analysis")
        mermaid_blocks = []
        tables = []
        references = sections.get("sources", {}).get("primary", [])
        content = sections.get("science", {}).get("content", "")

    elapsed = (time.perf_counter() - t0) * 1000

    disc = "以上内容基于《材料焊接原理》(王宗杰主编, 化学工业出版社, 2024, ISBN 978-7-122-44318-2)"
    if uploaded_names:
        disc += f" + 已学习资料({', '.join(uploaded_names)})"
    disc += "，由AI焊接工艺专家统一检索所有知识源后生成。"

    return QueryResponse(
        query=q,
        keywords=keywords,
        matched_categories=categories,
        is_cross_domain=result.get("is_cross_domain", False),
        is_empty=result.get("is_empty", False),
        model_used=model_used,
        elapsed_ms=round(elapsed, 1),
        content=content,
        sections=sections,
        mermaid_blocks=mermaid_blocks,
        tables=tables,
        references=references,
        disclaimer=disc,
    )


# ============================================================
# PDF 上传与管理
# ============================================================
@app.post("/api/upload-pdfs")
async def upload_pdfs(files: List[UploadFile] = File(...)):
    """批量上传多个工艺PDF，逐一完整学习"""
    results = []
    store = get_store()
    parser = _get_parser()
    rag = _get_rag()

    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            results.append({"filename": file.filename, "status": "skipped", "reason": "非PDF文件"})
            continue
        try:
            content = await file.read()
            if len(content) < 100:
                results.append({"filename": file.filename, "status": "skipped", "reason": "文件过小"})
                continue

            filepath = parser.save_upload(content, file.filename)
            parsed = parser.parse(filepath)
            source_id = store.learn_book(
                filename=file.filename,
                full_text=parsed["full_text"],
                tables=parsed["tables"],
                page_count=parsed["page_count"],
                images=parsed["images"],
            )
            src = store.get_source(source_id)
            results.append({
                "filename": file.filename,
                "status": "ok",
                "source_id": source_id,
                "page_count": parsed["page_count"],
                "chapters": src.get("chapter_count", 0) if src else 0,
                "keywords": src.get("keyword_count", 0) if src else 0,
                "tables": parsed["tables_count"],
                "message": f"已学习《{file.filename}》— {src.get('chapter_count', 0)}章, {src.get('keyword_count', 0)}关键词",
            })
        except Exception as e:
            results.append({"filename": file.filename, "status": "error", "reason": str(e)})

    # 一次性重建 RAG（索引所有书）
    rag.remove_pdf_documents()
    all_texts = store.get_all_full_texts()
    if all_texts:
        rag.add_pdf_document("all_uploads", all_texts)
    # 每本书每章也索引
    for src in store.list_sources():
        chapters = store.get_chapters(src["id"])
        for ch in chapters:
            chunk = f"《{src['filename']}》 {ch['title']}\n{ch.get('summary', '')}\n{ch.get('content', '')[:1000]}"
            rag.add_pdf_document(f"{src['id']}_{ch['title'][:30]}", chunk)

    return {
        "total": len(files),
        "learned": sum(1 for r in results if r["status"] == "ok"),
        "results": results,
        "catalog": store.build_knowledge_catalog(),
    }


@app.post("/api/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    """上传工艺PDF，解析并加入RAG索引"""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持PDF文件")

    try:
        content = await file.read()
        if len(content) < 100:
            raise HTTPException(status_code=400, detail="文件内容为空或过小")
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="文件过大(最大50MB)")

        parser = _get_parser()
        filepath = parser.save_upload(content, file.filename)

        # 全解析
        parsed = parser.parse(filepath)

        # === 完整学习流程 ===
        store = get_store()
        source_id = store.learn_book(
            filename=file.filename,
            full_text=parsed["full_text"],
            tables=parsed["tables"],
            page_count=parsed["page_count"],
            images=parsed["images"],
        )

        # 重建 RAG 索引
        rag = _get_rag()
        rag.remove_pdf_documents()
        # 将每章的摘要 + 内容分块索引
        chapters = store.get_chapters(source_id)
        for ch in chapters:
            chunk_text = f"《{file.filename}》 {ch['title']}\n{ch.get('summary', '')}\n{ch.get('content', '')[:1000]}"
            rag.add_pdf_document(f"{source_id}_{ch['title'][:30]}", chunk_text)
        # 也索引完整全文
        all_texts = store.get_all_full_texts()
        if all_texts:
            rag.add_pdf_document("combined_uploads", all_texts)

        catalog = store.build_knowledge_catalog()
        source_info = store.get_source(source_id)

        return {
            "status": "ok",
            "filename": parsed["filename"],
            "source_id": source_id,
            "page_count": parsed["page_count"],
            "text_length": parsed["text_length"],
            "tables_count": parsed["tables_count"],
            "tables": parsed["tables"],
            "images_count": parsed["images_count"],
            "images": parsed["images"][:10],
            "chapters": source_info.get("chapters", []) if source_info else [],
            "chapter_count": source_info.get("chapter_count", 0) if source_info else 0,
            "keyword_count": source_info.get("keyword_count", 0) if source_info else 0,
            "all_keywords": (source_info.get("all_keywords", [])[:50]) if source_info else [],
            "knowledge_catalog": catalog,
            "message": f"✅ 已完整学习《{parsed['filename']}》— {parsed['page_count']}页, 提取{source_info.get('chapter_count', 0)}章节, {source_info.get('keyword_count', 0)}关键词, {parsed['tables_count']}表格, {parsed['text_length']}字数据全部入库",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF解析失败: {str(e)}")


@app.get("/api/uploaded-files")
async def list_uploads():
    """列出已上传的PDF文件"""
    parser = _get_parser()
    return {"files": parser.list_uploads()}


@app.delete("/api/uploaded-files/{filename}")
async def delete_upload(filename: str):
    """删除已上传PDF并从知识库移除"""
    store = get_store()
    parser = _get_parser()

    # 从知识库中移除
    source_id = filename.replace('.pdf', '').replace(' ', '_')[:40]
    store.unregister(source_id)

    # 从上传目录移除
    parser.delete_upload(filename)

    # 重建 RAG 索引
    rag = _get_rag()
    rag.remove_pdf_documents()
    all_texts = store.get_all_full_texts()
    if all_texts:
        rag.add_pdf_document("combined_uploads", all_texts)

    return {"status": "ok", "message": f"已删除 {filename}，知识库已更新"}


@app.delete("/api/uploaded-files")
async def clear_all_uploads():
    """清除所有上传文件"""
    parser = _get_parser()
    for f in parser.list_uploads():
        parser.delete_upload(f["name"])
    rag = _get_rag()
    rag.remove_pdf_documents()
    return {"status": "ok", "message": "所有上传文件已清除"}


# ============================================================
# 静态文件
# ============================================================
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/")
async def root():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"message": "焊接工艺专家系统 API v2.0", "docs": "/docs"}, status_code=200)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

UPLOADS_DIR = PROJECT_ROOT / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
if UPLOADS_DIR.exists():
    app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")


# ---- 启动入口 ----
if __name__ == "__main__":
    import uvicorn
    import subprocess, platform

    # 自动释放端口
    try:
        if platform.system() == "Windows":
            result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, shell=True)
            for line in result.stdout.split("\n"):
                if ":8000" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, shell=True)
                    print(f"   🔄 释放端口 8000 (旧进程 PID={pid})")
                    break
    except Exception:
        pass

    # 自动学习 uploads/ 中的PDF
    from start import auto_learn_uploads
    auto_learn_uploads()

    llm = _get_llm()
    print()
    print("=" * 60)
    print("🔬 焊接工艺专家系统 v2.0")
    print("   基于《材料焊接原理》(王宗杰, 2024)")
    print("=" * 60)
    print(f"   🤖 LLM: {'已配置 (' + llm.model + ')' if llm.available else '未配置 (本地模式)'}")
    print(f"   📂 静态文件: {STATIC_DIR}")
    print(f"   📄 PDF上传: {UPLOADS_DIR}")
    print(f"   🌐 访问地址: http://localhost:8000")
    print(f"   📖 API 文档: http://localhost:8000/docs")
    print("=" * 60)
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
