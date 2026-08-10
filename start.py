"""
焊接工艺专家系统 — 一键启动脚本
===============================
启动前自动释放端口，启动后自动打开浏览器
访问 http://localhost:8000
"""
import subprocess
import sys
import os
import time
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PORT = 8000


def kill_port(port: int):
    """释放指定端口"""
    try:
        import platform
        if platform.system() == "Windows":
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, shell=True
            )
            for line in result.stdout.split("\n"):
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    subprocess.run(
                        ["taskkill", "/PID", pid, "/F"],
                        capture_output=True, shell=True,
                    )
                    print(f"  🔄 释放端口 {port} (旧进程 PID={pid})")
                    time.sleep(0.5)
                    break
    except Exception:
        pass


def auto_learn_uploads():
    """启动时自动学习 uploads/ 中尚未入库的PDF，并构建向量库 + 专家知识库"""
    from app.knowledge_store import get_store
    from app.pdf_parser import get_parser

    upload_dir = PROJECT_ROOT / "uploads"
    if not upload_dir.exists():
        return

    pdfs = list(upload_dir.glob("*.pdf"))
    if not pdfs:
        return

    store = get_store()
    parser = get_parser()
    # 比较时统一去掉 .pdf 后缀，避免 registry 中 filename 带/不带后缀造成重复学习
    known = {s["filename"].replace('.pdf', '').strip() for s in store.list_sources()}
    new_pdfs = [p for p in pdfs if p.name.replace('.pdf', '').strip() not in known]

    if not new_pdfs:
        print(f"   📂 uploads/: {len(pdfs)} 个PDF，全部已学习")

    if new_pdfs:
        print(f"   📂 发现 {len(new_pdfs)} 本新书，正在自动学习...")
        for pdf_path in new_pdfs:
            try:
                print(f"      ⏳ {pdf_path.name}...")
                parsed = parser.parse(str(pdf_path))
                tables = parsed["tables"]
                # 扫描版 → 自动重建表格（定向OCR候选页）
                if parsed.get("is_scanned") or (parsed["text_length"] > 0 and not tables):
                    print(f"         🔧 扫描版，重建表格...")
                    tables = parser.reconstruct_tables(
                        str(pdf_path),
                        PROJECT_ROOT / "uploads" / f"{Path(pdf_path.name).stem}_full_ocr.txt")
                store.learn_book(
                    filename=pdf_path.name,
                    full_text=parsed["full_text"],
                    tables=tables,
                    page_count=parsed["page_count"],
                    images=parsed["images"],
                )
                src = store.get_source(pdf_path.name.replace('.pdf', '')[:40])
                ch = src.get("chapter_count", 0) if src else 0
                kw = src.get("keyword_count", 0) if src else 0
                tb = src.get("table_count", 0) if src else 0
                print(f"      ✅ {pdf_path.name} — {ch}章, {kw}关键词, {tb}表格, {parsed['text_length']}字")
            except Exception as e:
                print(f"      ❌ {pdf_path.name} — {e}")

    # 构建 向量库 + 专家知识库（替代原 RAG 全量重建）
    try:
        from app.vector_store import VectorIndex, index_book_chapters
        from app.expert_knowledge_base import ExpertKnowledgeBase

        vi = VectorIndex()
        vi.clear_all()
        kb = ExpertKnowledgeBase()
        kb.build(store)
        for canonical, entry in kb.concepts.items():
            vi.add_document(
                f"expert/{canonical}",
                f"概念：{entry['name']}（{'，'.join(entry['aliases'][:6])}）\n{entry['definition']}\n{entry['application']}",
                meta={"source": "expert", "kind": "expert", "chapter": f"概念：{entry['name']}"},
            )
        for src in store.list_sources():
            index_book_chapters(store, src["id"], vi)
        vi.save()
        print(f"   🧠 专家知识库 {len(kb.concepts)} 概念 + 向量库 {len(vi.ids)} 行")
    except Exception as e:
        print(f"   ⚠️ 索引构建失败: {e}")

    print(f"   ✅ 知识库现有 {len(store.list_sources())} 本书")


def main():
    print("=" * 55)
    print("  🔬 焊接工艺专家系统 v2.0")
    print("  AI大模型 + 《材料焊接原理》+ PDF知识库")
    print("=" * 55)
    print()

    # 释放端口
    kill_port(PORT)

    # 自动学习 uploads/ 中所有PDF
    auto_learn_uploads()
    print()

    # 检查依赖
    try:
        import fastapi, uvicorn, pydantic
    except ImportError:
        print("📦 正在安装依赖...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", str(PROJECT_ROOT / "requirements.txt")],
            cwd=str(PROJECT_ROOT),
        )
        print("✅ 依赖安装完成\n")

    # 检查 LLM 状态
    try:
        from app.llm_service import get_client
        llm = get_client()
        llm_status = f"✅ 已连接 ({llm.model})" if llm.available else "⚠️ 未配置 (本地模式)"
    except Exception:
        llm_status = "⚠️ 检测失败"

    print(f"   🤖 LLM: {llm_status}")
    print(f"   📂 项目: {PROJECT_ROOT}")
    print(f"   🌐 地址: http://localhost:{PORT}")
    print()
    print("   按 Ctrl+C 停止服务")
    print("=" * 55)

    # 自动打开浏览器
    try:
        time.sleep(0.8)
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass

    # 启动
    os.chdir(str(PROJECT_ROOT))
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
