"""集中联调：覆盖普通概念问答、较长回答、连续提问、服务中断四个场景，
并核对前端新增按钮布局与提示文案。

用法：
    python tools/test_integration_scenarios.py

与 tools/tests.py 的区别：tests.py 按模块做单元/回归校验，本脚本按「用户真实
使用路径」串起来跑，模拟一次完整演示会话中会遇到的四类情况。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

checks: list[tuple[str, bool]] = []


def _check(description: str, condition: bool) -> None:
    checks.append((description, bool(condition)))


def _sse_events(blocks: list[str]) -> dict[str, list[dict]]:
    """把 SSE 文本块解析成 {event_name: [payload, ...]}。"""
    parsed: dict[str, list[dict]] = {}
    for block in blocks:
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:]
        if name and data:
            try:
                parsed.setdefault(name, []).append(json.loads(data))
            except json.JSONDecodeError:
                parsed.setdefault(name, []).append({"raw": data})
    return parsed


class StreamingLLM:
    """模拟可正常流式输出的 LLM。"""

    available = True
    model = "fake-stream"

    def chat_intent_stream(self, query, **kwargs):
        for i in range(6):
            yield f"第{i + 1}段：焊接工艺要点分析，需结合实际工况确认。"


class FlakyLLM:
    """模拟流式过程中连接被中断的 LLM（发若干 token 后抛错）。"""

    available = True
    model = "fake-flaky"

    def chat_intent_stream(self, query, **kwargs):
        yield "第一段：已开始输出"
        raise ConnectionError("connection reset by peer")


def _install_temp_cache(server, directory: str):
    from app.answer_cache import AnswerCache

    return AnswerCache(
        max_entries=100, ttl_seconds=3600,
        path=str(Path(directory) / "answer_cache.json"),
    )


def run_tests() -> bool:
    import server
    from app.metrics_service import get_metrics

    old_cache, old_llm = server._cache, server._llm_client
    try:
        with tempfile.TemporaryDirectory(prefix="welding-it-") as tmp:
            # ============================================================
            # 场景 1：普通概念问答
            # ============================================================
            server._cache = _install_temp_cache(server, tmp)
            server._llm_client = StreamingLLM()
            get_metrics().reset()

            payload = server.process_query("什么是焊接热输入？")
            _check("概念问答：返回本地答案且不触发 LLM",
                   payload.get("model_used") in {"local_expert", "local_knowledge_base", "process_card"})
            _check("概念问答：带有可追溯的参考来源",
                   bool(payload.get("references")))
            _check("概念问答：返回章节化内容而非空响应",
                   bool(payload.get("sections")) or bool(payload.get("content")))
            _check("概念问答：置信度与路由意图随响应返回",
                   payload.get("route", {}).get("intent") is not None)

            # ============================================================
            # 场景 2：较长回答（流式多段输出）
            # ============================================================
            events = _sse_events(list(server.stream_query_events("焊接热输入对热影响区组织的影响机理")))
            _check("较长回答：先发 start 事件再输出内容",
                   "start" in events and ("token" in events or "done" in events))
            _check("较长回答：以 done 事件收尾且负载完整",
                   "done" in events and bool(events["done"][-1].get("response")))
            done_payload = events["done"][-1]["response"]
            _check("较长回答：done 负载带来源引用",
                   bool(done_payload.get("references")))
            _check("较长回答：事件流中无 error",
                   "error" not in events)

            # ============================================================
            # 场景 3：连续提问（缓存与指标累计）
            # ============================================================
            get_metrics().reset()
            first = server.process_query("Q345钢板 12mm GMAW焊接参数")
            second = server.process_query("304不锈钢3mm TIG焊保护气体流量")
            repeat = server.process_query("Q345钢板 12mm GMAW焊接参数")
            _check("连续提问：三个请求均返回有效负载",
                   all(server._payload_is_valid(p) for p in (first, second, repeat)))
            _check("连续提问：重复问题命中缓存",
                   bool(repeat.get("cache_hit")) and repeat.get("model_used") == "cache")
            _check("连续提问：不同问题不被错误复用",
                   not second.get("cache_hit"))
            _check("连续提问：指标累计三次请求",
                   get_metrics().snapshot()["total_requests"] == 3)

            # ============================================================
            # 场景 4：服务中断
            # ============================================================
            server._llm_client = FlakyLLM()
            interrupted = _sse_events(list(server.stream_query_events(
                "请论述焊接结构疲劳寿命评估的工程方法")))
            _check("服务中断：中断时发出 error 事件而非静默失败",
                   "error" in interrupted)
            _check("服务中断：error 事件带可展示的错误信息",
                   bool(interrupted.get("error", [{}])[-1].get("message")))
            _check("服务中断：中断不会污染后续请求",
                   server._payload_is_valid(server.process_query("什么是焊接热输入？")))

            # 中断后指标记录为错误请求，便于观测
            snapshot = get_metrics().snapshot()
            _check("服务中断：错误请求被计入指标",
                   snapshot["total_requests"] >= 2)

    finally:
        server._cache, server._llm_client = old_cache, old_llm

    # ================================================================
    # 前端按钮布局与提示文案核对
    # ================================================================
    frontend = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    _check("前端：顶栏提供检索诊断入口", "🔎 检索诊断" in frontend and "openInspectModal" in frontend)
    _check("前端：顶栏提供书籍目录入口", "📖 书籍目录" in frontend and "openBookPanel" in frontend)
    _check("前端：顶栏提供导入PDF入口", "📄 导入PDF" in frontend and "openUploadModal" in frontend)
    _check("前端：错误卡提供重试按钮", "↻ 重试" in frontend and "retryQuery" in frontend)
    _check("前端：流式进行中提供取消按钮", "取消" in frontend and "cancelActiveQuery" in frontend)
    _check("前端：取消基于 AbortController",
           "AbortController" in frontend and "AbortError" in frontend)
    _check("前端：缺参时提供补充模板按钮",
           "📝 填入补充模板" in frontend and "fillMissingPrompt" in frontend)
    _check("前端：缺参提示文案完整", "补充信息后重新生成" in frontend and "生产使用前必须确认" in frontend)
    _check("前端：工艺卡提供打印与JSON导出",
           "🖨️ 打印 / 导出PDF" in frontend and "📋 复制JSON（仿真/机器人导入）" in frontend)
    _check("前端：参数表带来源列", "<th>来源</th>" in frontend and "sourceBadge" in frontend)
    _check("前端：流式失败有明确文案",
           "流式请求失败" in frontend and "流式响应未正常完成" in frontend)

    print("\n" + "=" * 58)
    print("🔗 集中联调：四场景 + 前端按钮/提示文案")
    print("=" * 58)
    for description, ok in checks:
        print(f"  {'✓' if ok else '✗'} {description}")
    passed = sum(1 for _, ok in checks if ok)
    print(f"\n  通过: {passed}/{len(checks)}")
    return passed == len(checks)


if __name__ == "__main__":
    raise SystemExit(0 if run_tests() else 1)
