"""
焊接工艺专家系统 Web 服务
==========================
FastAPI 后端 — LLM驱动 + RAG检索 + PDF导入 + 书籍知识库
"""

import sys
import os
import json
import time
import threading
import subprocess
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

from app.welding_qa_system import WeldingQASystem
from app.welding_knowledge_base import KNOWLEDGE_CATEGORIES
from app.knowledge_store import get_store

# ---- v2.5 新组件 ----
from app.answer_cache import get_cache, sources_fingerprint
from app.qa_router import get_router, QueryIntent
from app.expert_knowledge_base import get_expert_kb
from app.vector_store import get_index, index_book_chapters
from app.process_card import build_process_card

# ---- FastAPI ----
app = FastAPI(
    title="焊接工艺专家系统",
    description="LLM驱动的焊接知识智能问答 + 专家知识库 + 本地向量库 + 意图路由",
    version="2.5.0",
)
app.add_middleware(GZipMiddleware, minimum_size=500)

# ---- 全局初始化 ----
qa_system = WeldingQASystem()

# LLM / RAG / PDF 组件延迟加载
_llm_client = None
_rag_builder = None
_pdf_parser = None

# ---- v2.5 新组件（延迟加载）----
_cache = None
_router = None
_expert_kb = None
_vector = None
_kb_fingerprint = ""
_routing_cfg = {}


def _get_cache():
    global _cache
    if _cache is None:
        _cache = get_cache()
    return _cache


def _get_router():
    global _router
    if _router is None:
        _router = get_router()
    return _router


def _get_expert_kb():
    global _expert_kb
    if _expert_kb is None:
        _expert_kb = get_expert_kb()
    return _expert_kb


def _get_vector():
    global _vector
    if _vector is None:
        _vector = get_index()
    return _vector


def _load_routing_cfg() -> dict:
    try:
        from app.llm_service import load_config
        cfg = load_config()
        return cfg.get("routing", {}) or {}
    except Exception:
        return {}


# ============================================================
# v2.5 GPU 入库任务管理（前端上传扫描版 → .venv-gpu 子进程处理）
# ============================================================
_ingest_jobs = {}
_ingest_jobs_lock = threading.Lock()
_job_counter = [0]


def _new_job_id() -> str:
    _job_counter[0] += 1
    return f"ingest_{int(time.time())}_{_job_counter[0]}"


def _set_job(job_id: str, status: str, message: str, filename: str = "", detail: str = ""):
    with _ingest_jobs_lock:
        _ingest_jobs[job_id] = {
            "status": status, "message": message,
            "filename": filename, "detail": detail,
        }


def _is_scanned_pdf(filepath: str) -> bool:
    """快速判断扫描版：>80%页面无文字"""
    try:
        import fitz
        doc = fitz.open(filepath)
        total = len(doc)
        empty = sum(1 for p in doc if not p.get_text("text").strip())
        doc.close()
        return total > 3 and empty / max(total, 1) > 0.8
    except Exception:
        return False


def _gpu_python() -> str:
    """返回 .venv-gpu 的 python.exe 路径（存在才可用）"""
    p = PROJECT_ROOT / ".venv-gpu" / "Scripts" / "python.exe"
    return str(p) if p.exists() else ""


def _server_has_gpu() -> bool:
    """当前服务进程自身是否带 GPU 版 paddle（在 .venv-gpu 里运行时为 True）"""
    try:
        import paddle
        return bool(paddle.device.is_compiled_with_cuda()) and int(paddle.device.cuda.device_count()) > 0
    except Exception:
        return False


def _run_gpu_ingest_job(job_id: str, filename: str):
    """后台线程：调用 .venv-gpu 的 gpu_ingest.py 完成 GPU OCR + 入库 + 索引重建"""
    try:
        gpu_py = _gpu_python()
        if not gpu_py:
            _set_job(job_id, "error", "未找到 .venv-gpu 环境，请先搭建 GPU 环境（安装 Python3.12 + paddlepaddle-gpu）")
            return
        proc = subprocess.run(
            [gpu_py, str(PROJECT_ROOT / "tools" / "gpu_ingest.py"), filename],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(PROJECT_ROOT), timeout=7200,
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode == 0:
            _set_job(job_id, "done", "GPU 识别并入库完成", filename, out[-2500:])
        else:
            _set_job(job_id, "error", f"GPU 处理失败 (exit {proc.returncode})", filename, out[-1500:])
    except subprocess.TimeoutExpired:
        _set_job(job_id, "error", "GPU 处理超时（超过2小时）", filename)
    except Exception as e:
        _set_job(job_id, "error", str(e), filename)


def _ensure_index():
    """加载 专家知识库 + 向量库 + 外部知识源，刷新缓存指纹。
    在启动/上传/删除后调用一次，替代每查询全量重放。"""
    global _kb_fingerprint, _routing_cfg
    store = get_store()
    _routing_cfg = _load_routing_cfg()

    kb = _get_expert_kb()
    if not kb.load():
        print("   ⚠️ 专家知识库未构建，请运行: python build_expert_kb.py")
    _get_vector().load()

    # 注入所有已学习 PDF 的关键词到匹配系统（一次）
    external_kw_list = []
    for src in store.list_sources():
        kws = store.get_keywords(src["id"])
        chapters = store.get_chapters(src["id"])
        external_kw_list.append({
            "filename": src["filename"],
            "keywords": kws,
            "chapters": [{"title": c["title"], "summary": c.get("summary", ""),
                          "keywords": c.get("keywords", [])} for c in chapters],
        })
    qa_system.load_external_knowledge(external_kw_list)

    _kb_fingerprint = sources_fingerprint(store)


# ------------------------------------------------------------
# v2.5 组装助手：概念 / 参数 答案 sections
# ------------------------------------------------------------
def _disclaimer() -> str:
    store = get_store()
    names = [s["filename"] for s in store.list_sources()]
    base = "以上内容由本地专家知识库 + 已学习工艺资料生成"
    if names:
        base += f"（{', '.join(names)}）"
    return base + "，由 AI 焊接工艺专家统一检索后生成。"


def _format_sources(src_list: list) -> list:
    out = []
    for s in src_list or []:
        book = s.get("book", "") or ""
        chapter = s.get("chapter", "") or ""
        ph = s.get("page_hint", "") or ""
        if not book:
            continue
        item = f"📄《{book}》「{chapter}」" + (f" {ph}" if ph else "")
        if item not in out:
            out.append(item)
    return out[:10]


def _recs_to_md(recs: list) -> str:
    if not recs:
        return "未匹配到直接的参数建议，请补充材料/工艺/板厚信息后重试。"
    lines = ["| 参数 | 建议值 |", "|---|---|"]
    for r in recs:
        lines.append(f"| {r.get('param', '')} | {r.get('value', '')} |")
    return "\n".join(lines)


def _assemble_concept_sections(plan: dict, result: dict) -> dict:
    """概念答案 sections：概念解析 + 应用及拓展 + 工艺类型 + 来源"""
    concept = plan.get("concept")
    sections = {}
    if concept:
        sections["concept_definition"] = {
            "title": "概念解析", "icon": "📖",
            "content": concept.get("definition", ""), "visible": True}
        sections["concept_application"] = {
            "title": "应用及拓展", "icon": "🔧",
            "content": concept.get("application", ""), "visible": True}
        if concept.get("process_types"):
            sections["process_types"] = {
                "title": "支持的大体工艺类型", "icon": "⚙️",
                "items": concept["process_types"], "visible": True}
        sections["sources"] = {
            "title": "参考基座知识库与PDF来源", "icon": "📚",
            "primary": _format_sources(concept.get("sources", [])), "visible": True}
    else:
        # 专家库未命中 → 复用现有 generate_structured 的本地内容
        base = result.get("sections", {}) or {}
        for k, v in base.items():
            if isinstance(v, dict) and v.get("visible", True) is not False:
                sections[k] = dict(v)
        if "sources" not in sections:
            sections["sources"] = {"title": "来源", "icon": "📚",
                                   "primary": _format_sources(plan.get("vector_hits", [])),
                                   "visible": True}
    return sections


def _assemble_param_sections(plan: dict, result: dict) -> tuple:
    """参数答案 sections：选型参数建议 + 应用拓展 + 应用来源。返回 (sections, param_md)"""
    pm = plan.get("param_match") or {}
    recs = pm.get("recommendations", [])
    param_md = _recs_to_md(recs)
    app_md = pm.get("application", "")
    if not app_md:
        app_md = "基于基座工艺参数表、材料参数表与已学手册章节匹配得出，具体需结合母材牌号、坡口形式与工况复核。"
    sections = {
        "param_suggestion": {
            "title": "⚙️ 选型参数建议", "icon": "⚙️",
            "content": param_md, "visible": True},
        "param_application": {
            "title": "应用拓展", "icon": "🔧",
            "content": app_md, "visible": True},
        "sources": {
            "title": "应用来源", "icon": "📚",
            "primary": _format_sources(pm.get("sources", [])), "visible": True},
    }
    return sections, param_md


def _assemble_card_sections(plan: dict, result: dict) -> dict:
    """工艺卡片驱动的展示 sections：工艺方案 + 质量评估 + 应用来源（推理不展示）"""
    card = plan.get("process_card") or {}
    sections = {}
    ele = card.get("electrical", {})
    th = card.get("thermal", {})
    jp = card.get("joint_prep", {})
    pp = card.get("pass_plan", {})

    assume_note = "（工艺未指定，默认焊条电弧焊基线）" if card.get("process_assumed") else ""
    plan_md = (
        f"**母材**：{card.get('base_material','')}　**板厚**：{card.get('thickness_mm','')}mm　"
        f"**工艺**：{card.get('process','')}{assume_note}\n\n"
        f"### 坡口与装配\n"
        f"- 坡口形式：{card.get('groove','')}\n"
        f"- 装配间隙：{card.get('joint_gap_mm','')}\n"
        f"- 清理要求：{jp.get('cleaning','')}\n"
        f"- 定位焊：{jp.get('tack_weld','')}\n"
        f"- 焊接位置：{card.get('welding_position','')}\n\n"
        f"### 焊接参数\n"
        f"| 参数 | 建议值 |\n|---|---|\n"
        f"| 焊接电流 | {ele.get('current_a','')} |\n"
        f"| 电弧电压 | {ele.get('voltage_v','')} |\n"
        f"| 焊接速度 | {ele.get('travel_speed_cm_min','')} |\n"
        f"| 焊材 | {card.get('consumables','')} |\n"
        f"| 焊条/焊丝直径 | {card.get('electrode_diameter','')} |\n"
        f"| 保护气体 | {card.get('shielding_gas','')} |\n\n"
        f"### 热管理\n"
        f"- 预热：{th.get('preheat','')}\n"
        f"- 层间温度：{th.get('interpass_temp','')}\n"
        f"- 后热：{th.get('postheat','')}\n\n"
        f"### 焊道规划\n"
        f"- 层道：{pp.get('layers_passes','')}\n"
        f"- 摆动：{pp.get('weaving','')}"
    )
    sections["process_plan"] = {"title": "工艺方案", "icon": "⚙️",
                                "content": plan_md, "visible": True}

    qc = card.get("quality", {})
    q_md = "| 常见缺陷 | 产生原因 | 预防措施 |\n|---|---|---|\n"
    for c in (qc.get("checks", []) or []):
        q_md += f"| {c.get('defect','')} | {c.get('cause','')} | {c.get('prevention','')} |\n"
    q_md += f"\n**检验要点**：{qc.get('inspection','')}"
    sections["quality_assessment"] = {"title": "质量评估", "icon": "🛡️",
                                      "content": q_md, "visible": True}

    pm = plan.get("param_match") or {}
    sections["sources"] = {"title": "应用来源", "icon": "📚",
                           "primary": _format_sources(pm.get("sources", [])), "visible": True}
    return sections


def _payload_is_valid(payload: dict) -> bool:
    """判断 payload 是否有实际内容（避免缓存/返回空答案）"""
    if not payload:
        return False
    if payload.get("content") and len(str(payload.get("content")).strip()) >= 30:
        return True
    secs = payload.get("sections") or {}
    for sec in secs.values():
        if not isinstance(sec, dict):
            continue
        if sec.get("content") and len(str(sec.get("content")).strip()) >= 30:
            return True
        if sec.get("items"):
            return True
        if sec.get("primary"):
            return True
    # process_card 也是有效输出
    if payload.get("process_card"):
        return True
    return False


def _assemble_local_payload(plan: dict, result: dict, q: str, t0) -> dict:
    """按意图组装本地负载。content 必须为空，前端才走 sections 渲染。"""
    intent = plan["intent"]
    sections = {}
    if intent in (QueryIntent.CONCEPT, QueryIntent.MIXED):
        sections.update(_assemble_concept_sections(plan, result))
    if intent in (QueryIntent.PARAMETER, QueryIntent.MIXED):
        if plan.get("process_card"):
            sections.update(_assemble_card_sections(plan, result))
            plan["_has_card"] = True
        else:
            psecs, param_md = _assemble_param_sections(plan, result)
            sections.update(psecs)
            plan["param_md"] = param_md
    if not sections:
        sections = dict(result.get("sections", {}) or {})
        if "cross_analysis" in sections:
            sections.pop("cross_analysis")
    # [v2.6] OTHER/空内容兜底：用跨源检索的章节摘要生成"相关知识"
    if not sections:
        try:
            from app.knowledge_store import get_store
            cross = get_store().search_across_sources(q)
        except Exception:
            cross = []
        if cross:
            related = []
            for m in cross[:5]:
                rel = f"《{m['source']}》「{m['chapter']}」：{m.get('summary', '')[:150]}"
                if rel not in related:
                    related.append(rel)
            if related:
                sections["related_knowledge"] = {
                    "title": "相关知识", "icon": "📖",
                    "content": "\n\n".join(related), "visible": True}

    references = []
    src_section = sections.get("sources")
    if isinstance(src_section, dict):
        references = src_section.get("primary", []) or references

    conf = plan.get("confidence", 0.0)
    payload = {
        "query": q,
        "keywords": result.get("keywords", []),
        "matched_categories": result.get("matched_categories", []),
        "is_cross_domain": result.get("is_cross_domain", False),
        "is_empty": result.get("is_empty", False),
        "model_used": "process_card" if plan.get("_has_card") else "local_expert",
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "content": "",
        "sections": sections,
        "mermaid_blocks": [],
        "tables": [],
        "references": references,
        "disclaimer": _disclaimer(),
        "route": {"intent": intent.value, "confidence": round(conf, 2)},
        "cache_hit": False,
        "confidence": round(conf, 2),
        "process_card": plan.get("process_card"),
    }
    return payload


def _build_thin_context(q: str, plan: dict, top_k: int = 5) -> str:
    """薄上下文：只用跨源匹配的命中章节（不再全量知识目录）"""
    store = get_store()
    try:
        matches = store.search_across_sources(q)
    except Exception:
        return ""
    if not matches:
        return ""
    parts = []
    for m in matches[:top_k]:
        parts.append(
            f"《{m['source']}》「{m['chapter']}」\n{m.get('summary', '')}\n{m.get('content_preview', '')}")
    return "\n\n".join(parts)


def _build_thin_catalog(plan: dict) -> str:
    entries = []
    concept = plan.get("concept")
    if concept:
        for s in concept.get("sources", [])[:5]:
            entries.append(f"《{s.get('book', '')}》「{s.get('chapter', '')}」")
    pm = plan.get("param_match")
    if pm:
        for s in pm.get("sources", [])[:5]:
            entries.append(f"《{s.get('book', '')}》「{s.get('chapter', '')}」")
    return "\n".join(dict.fromkeys(e for e in entries if e))


def _param_primary_sources(plan: dict) -> list:
    out = []
    pm = plan.get("param_match")
    if pm:
        out = _format_sources(pm.get("sources", []))
    if not out:
        out = _format_sources(plan.get("concept", {}).get("sources", []))
    return out


def _llm_payload(parsed: dict, result: dict, q: str, model: str, t0,
                 intent: QueryIntent, conf: float, plan: dict) -> dict:
    refs = parsed.get("references", []) or _param_primary_sources(plan)
    sections = {
        "expert_analysis": {
            "title": "🔍 专家分析", "icon": "🔍",
            "content": parsed.get("raw_markdown", ""), "visible": True},
        "recommendations": {
            "title": "📋 延伸建议", "icon": "📋",
            "items": result.get("sections", {}).get("recommendations", {}).get("items", []),
            "visible": True},
        "sources": {
            "title": "📚 参考来源", "icon": "📚",
            "primary": refs, "visible": True},
    }
    return {
        "query": q,
        "keywords": result.get("keywords", []),
        "matched_categories": result.get("matched_categories", []),
        "is_cross_domain": result.get("is_cross_domain", False),
        "is_empty": result.get("is_empty", False),
        "model_used": model,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "content": parsed.get("raw_markdown", ""),
        "sections": sections,
        "mermaid_blocks": parsed.get("mermaid_blocks", []),
        "tables": parsed.get("tables", []),
        "references": refs,
        "disclaimer": _disclaimer(),
        "route": {"intent": intent.value, "confidence": round(conf, 2)},
        "cache_hit": False,
        "confidence": round(conf, 2),
    }


def ingest_book(source_id: str, filename: str):
    """增量入库：向量库追加/替换 → 专家库重建 → 缓存失效 → 刷新索引。
    替代原上传端点的 RAG 全量重建。"""
    store = get_store()
    try:
        vi = _get_vector()
        vi.remove_by_source(filename)
        index_book_chapters(store, source_id, vi)
        vi.save()
    except Exception as e:
        print(f"   ⚠️ 向量索引失败: {e}")
    try:
        _get_expert_kb().build(store)
    except Exception as e:
        print(f"   ⚠️ 专家库重建失败: {e}")
    _get_cache().invalidate()
    _ensure_index()


def _get_llm():
    global _llm_client
    if _llm_client is None:
        from app.llm_service import get_client
        _llm_client = get_client()
    return _llm_client


def _get_rag():
    global _rag_builder
    if _rag_builder is None:
        from app.rag_retriever import get_rag
        _rag_builder = get_rag()
    return _rag_builder


def _get_parser():
    global _pdf_parser
    if _pdf_parser is None:
        from app.pdf_parser import get_parser
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
    route: dict = None
    cache_hit: bool = False
    confidence: float = 0.0
    process_card: dict | None = None


# ============================================================
# API 端点
# ============================================================

@app.get("/api/health")
async def health():
    llm = _get_llm()
    return {
        "status": "ok",
        "service": "焊接工艺专家系统 v2.5",
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



def process_query(q: str) -> dict:
    """核心问答流程（HTTP 端点 与 命令行 run_welding_qa 共用）。
    返回 QueryResponse 同结构的 dict（含 process_card 工艺卡片）。
    流程：缓存 → 意图路由 → 工艺卡片/本地优先 → LLM 兜底。"""
    if not q or not q.strip():
        raise ValueError("查询内容不能为空")
    q = q.strip()
    t0 = time.perf_counter()

    # --- 首次/知识库变化后加载 ---
    store = get_store()
    current_fp = sources_fingerprint(store)
    if current_fp != _kb_fingerprint or not _kb_fingerprint:
        _ensure_index()
    fp = _kb_fingerprint

    # --- Step 1: 结果缓存 ---
    cached = _get_cache().get(q, fp)
    if cached:
        cached = dict(cached)
        cached["model_used"] = "cache"
        cached["cache_hit"] = True
        cached["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return cached

    # --- Step 2: 本地分析（无 LLM / 无网络） ---
    result = qa_system.generate_structured(q)
    keywords = result.get("keywords", [])
    categories = result.get("matched_categories", [])

    # 跨源匹配补充关键词/类别
    try:
        cross_source_matches = store.search_across_sources(q)
        for src in store.list_sources():
            for kw in src.get("all_keywords", []):
                if kw in q and kw not in keywords:
                    keywords.append(kw)
        if cross_source_matches:
            matched_sources = list(set(m["source"] for m in cross_source_matches))
            categories = categories + [f"📄 {s}" for s in matched_sources]
    except Exception:
        pass

    # --- Step 3: 向量检索 + 专家库概念命中（关键词优先，向量增强） ---
    vector_hits = _get_vector().search(q, top_k=8)
    concept = _get_expert_kb().lookup(keywords) if keywords else None
    # [v2.6] 三层融合：关键词没命中概念时，用向量 top 命中补（语义关联）
    if concept is None:
        for vh in vector_hits[:5]:
            cid = vh.get("doc_id", "")
            if cid.startswith("expert/"):
                cname = cid[len("expert/"):]
                c = _get_expert_kb().get_concept(cname)
                if c:
                    concept = c
                    break

    # --- Step 4: 意图路由（内部思考①·意图解析，不展示） ---
    plan = _get_router().analyze_intent(q, keywords)
    intent = plan["intent"]
    plan["concept"] = concept
    plan["vector_hits"] = vector_hits

    # --- Step 5: 工艺匹配（内部思考②）+ 工艺卡片（机器可读，供仿真/机器人） ---
    if intent in (QueryIntent.PARAMETER, QueryIntent.MIXED):
        plan["param_match"] = _get_router().match_parameters(
            plan["extracted"], q, expert_kb=_get_expert_kb(), vector_hits=vector_hits)
        plan["process_card"] = build_process_card(plan["extracted"], plan["param_match"], q)

    conf = _get_router().confidence(intent, plan["extracted"], concept, _routing_cfg)
    plan["confidence"] = conf
    local_high = float(_routing_cfg.get("local_high", 0.78))

    # --- Step 6: 组装本地负载（概念/工艺卡片/参数 sections） ---
    local_payload = _assemble_local_payload(plan, result, q, t0)

    # --- Step 7: 本地匹配优先 — 高置信 或 工艺卡片就绪 → 直接返回，跳过 LLM ---
    if intent != QueryIntent.OTHER and (conf >= local_high or plan.get("process_card")):
        if _payload_is_valid(local_payload):
            _get_cache().put(q, local_payload, fp)
        return local_payload

    # --- Step 8: LLM 兜底（意图感知 + 瘦身上下文） ---
    llm = _get_llm()
    llm_text = None
    if llm.available:
        try:
            rag_context = _build_thin_context(q, plan, top_k=5)
            thin_catalog = _build_thin_catalog(plan)
            llm_text = llm.chat_intent(
                q,
                intent=intent.value,
                rag_context=rag_context,
                thin_catalog=thin_catalog,
                concept=concept,
                local_payload={"param_md": plan.get("param_md", "")},
                max_tokens=2000,
            )
        except Exception:
            llm_text = None

    if llm_text and len(llm_text.strip()) > 50:
        from app.llm_service import parse_llm_response
        parsed = parse_llm_response(llm_text, q, keywords, categories)
        payload = _llm_payload(parsed, result, q, llm.model, t0, intent, conf, plan)
    else:
        payload = local_payload
        payload["model_used"] = "local_knowledge_base"

    # 空答案不缓存（避免缓存"0字空回复"导致永远命中空答案）
    if _payload_is_valid(payload):
        _get_cache().put(q, payload, fp)
    return payload


@app.post("/api/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """主问答接口 — 复用 process_query（HTTP 入口）"""
    try:
        return QueryResponse(**process_query(req.query))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# PDF 上传与管理
# ============================================================
@app.post("/api/upload-pdfs")
async def upload_pdfs(files: List[UploadFile] = File(...)):
    """批量上传多个工艺PDF。
    - 文字版：进程内学习入库
    - 扫描版：提交 .venv-gpu 后台 GPU 识别（job_id 轮询）
    """
    results = []
    store = get_store()
    parser = _get_parser()
    learned_ids = []

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

            # 扫描版：始终走 .venv-gpu 子进程（全文OCR + 表格重建）
            if _is_scanned_pdf(filepath) and _gpu_python():
                job_id = _new_job_id()
                _set_job(job_id, "processing", "GPU 识别中...", file.filename)
                threading.Thread(target=_run_gpu_ingest_job, args=(job_id, file.filename), daemon=True).start()
                results.append({"filename": file.filename, "status": "processing",
                                "job_id": job_id, "mode": "gpu_ocr",
                                "message": "已提交GPU识别，完成后自动入库"})
                continue

            parsed = parser.parse(filepath)
            source_id = store.learn_book(
                filename=file.filename,
                full_text=parsed["full_text"],
                tables=parsed["tables"],
                page_count=parsed["page_count"],
                images=parsed["images"],
            )
            learned_ids.append(source_id)
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

    # 进程内学习的书 → 增量入向量库 + 专家库重建 + 缓存失效
    for source_id in learned_ids:
        src = store.get_source(source_id)
        if src:
            ingest_book(source_id, src["filename"])

    return {
        "total": len(files),
        "learned": sum(1 for r in results if r["status"] == "ok"),
        "processing": sum(1 for r in results if r["status"] == "processing"),
        "results": results,
        "catalog": store.build_knowledge_catalog(),
    }


@app.post("/api/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    """上传工艺PDF。
    - 文字版：进程内快速解析入库
    - 扫描版：交给 .venv-gpu 的 gpu_ingest.py 后台 GPU 识别，返回 job_id 供轮询
    """
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

        # ---- 扫描版：始终走 .venv-gpu 子进程（全文OCR + 表格重建 + 进度轮询）----
        if _is_scanned_pdf(filepath) and _gpu_python():
            job_id = _new_job_id()
            _set_job(job_id, "processing", "GPU 识别中...", file.filename)
            threading.Thread(target=_run_gpu_ingest_job, args=(job_id, file.filename), daemon=True).start()
            return {
                "status": "processing",
                "job_id": job_id,
                "filename": file.filename,
                "mode": "gpu_ocr",
                "message": "扫描版PDF已提交GPU识别（含表格重建），完成后自动入库（前端将自动刷新）",
            }

        # ---- 文字版 / 服务自带GPU / 纯CPU回退：进程内处理 ----
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

        # 增量入向量库 + 专家库重建 + 缓存失效
        ingest_book(source_id, file.filename)

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


@app.get("/api/upload-status/{job_id}")
async def upload_status(job_id: str):
    """查询 GPU 入库任务状态（含 OCR 进度）"""
    with _ingest_jobs_lock:
        job = _ingest_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    res = dict(job)
    # 处理中 → 附带 OCR 页进度
    if res.get("status") == "processing" and res.get("filename"):
        stem = Path(res["filename"]).stem
        prog = PROJECT_ROOT / "uploads" / f"{stem}_ocr_progress.json"
        if prog.exists():
            try:
                d = json.loads(prog.read_text(encoding="utf-8"))
                res["pages_done"] = len(d.get("done_pages", []))
                res["pages_total"] = d.get("total_pages", 0)
            except Exception:
                pass
    return res


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

    # 向量库删除该书 + 专家库重建 + 缓存失效
    try:
        _get_vector().remove_by_source(filename)
        _get_vector().save()
    except Exception:
        pass
    try:
        _get_expert_kb().build(store)
    except Exception:
        pass
    _get_cache().invalidate()
    _ensure_index()

    return {"status": "ok", "message": f"已删除 {filename}，知识库已更新"}


@app.delete("/api/uploaded-files")
async def clear_all_uploads():
    """清除所有上传文件"""
    parser = _get_parser()
    for f in parser.list_uploads():
        parser.delete_upload(f["name"])
    _get_vector().clear_all()
    _get_vector().save()
    _get_cache().invalidate()
    _ensure_index()
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
    return JSONResponse({"message": "焊接工艺专家系统 API v2.5", "docs": "/docs"}, status_code=200)


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

    # 加载 专家知识库 + 向量库 + 外部知识源（v2.5）
    _ensure_index()

    llm = _get_llm()
    print()
    print("=" * 60)
    print("🔬 焊接工艺专家系统 v2.5")
    print("   基于《材料焊接原理》(王宗杰, 2024)")
    print("=" * 60)
    print(f"   🤖 LLM: {'已配置 (' + llm.model + ')' if llm.available else '未配置 (本地模式)'}")
    print(f"   📂 静态文件: {STATIC_DIR}")
    print(f"   📄 PDF上传: {UPLOADS_DIR}")
    print(f"   🌐 访问地址: http://localhost:8000")
    print(f"   📖 API 文档: http://localhost:8000/docs")
    print("=" * 60)
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
