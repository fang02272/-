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
    """启动时自动学习 uploads/ 中尚未入库的PDF"""
    from knowledge_store import get_store
    from pdf_parser import get_parser

    upload_dir = PROJECT_ROOT / "uploads"
    if not upload_dir.exists():
        return

    pdfs = list(upload_dir.glob("*.pdf"))
    if not pdfs:
        return

    store = get_store()
    parser = get_parser()
    known = {s["filename"] for s in store.list_sources()}
    new_pdfs = [p for p in pdfs if p.name not in known]

    if not new_pdfs:
        print(f"   📂 uploads/: {len(pdfs)} 个PDF，全部已学习")
        return

    print(f"   📂 发现 {len(new_pdfs)} 本新书，正在自动学习...")
    from rag_retriever import get_rag
    rag = get_rag()

    for pdf_path in new_pdfs:
        try:
            print(f"      ⏳ {pdf_path.name}...")
            parsed = parser.parse(str(pdf_path))
            store.learn_book(
                filename=pdf_path.name,
                full_text=parsed["full_text"],
                tables=parsed["tables"],
                page_count=parsed["page_count"],
                images=parsed["images"],
            )
            src = store.get_source(pdf_path.name.replace('.pdf', '')[:40])
            ch = src.get("chapter_count", 0) if src else 0
            kw = src.get("keyword_count", 0) if src else 0
            print(f"      ✅ {pdf_path.name} — {ch}章, {kw}关键词, {parsed['text_length']}字")
        except Exception as e:
            print(f"      ❌ {pdf_path.name} — {e}")

    # 重建RAG
    rag.remove_pdf_documents()
    for src in store.list_sources():
        chapters = store.get_chapters(src["id"])
        for ch in chapters:
            chunk = f"《{src['filename']}》 {ch['title']}\n{ch.get('summary','')}\n{ch.get('content','')[:1000]}"
            rag.add_pdf_document(f"{src['id']}_{ch['title'][:30]}", chunk)
    all_texts = store.get_all_full_texts()
    if all_texts:
        rag.add_pdf_document("all_uploads", all_texts)

    print(f"   ✅ 学习完成，知识库现有 {len(store.list_sources())} 本书")


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
        from llm_service import get_client
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
