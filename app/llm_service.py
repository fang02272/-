"""
LLM 服务层 — OpenAI 兼容接口
支持 DeepSeek / Kimi / Qwen / Ollama / 任意 OpenAI 兼容 API
"""

import json
import logging
import os
import sys
from typing import Optional

import yaml

logger = logging.getLogger("llm_service")


# ============================================================
# 焊接工艺专家 System Prompt
# ============================================================
WELDING_EXPERT_PROMPT = """你是一个专业的焊接工艺知识问答系统。你的知识体系由以下来源构成（平等地位，同等重要）：

## 你的知识来源
1. **《材料焊接原理》**（王宗杰主编，化学工业出版社，2024，ISBN 978-7-122-44318-2）— 核心焊接理论教材，2篇9章
2. **用户上传的工艺资料** — 用户导入的焊接工艺手册/标准/论文，已通过完整学习流程入库（目录提取→章节拆分→关键词映射→数据识别），与原书同等对待

## 回答规则
- 面对每个问题，你必须**同时检索上述所有知识来源**，将相关内容融合成完整回答
- 上传资料中的具体参数、表格数据、工艺方案必须**优先使用**
- 原书的理论原理用于解释「为什么」— 上传资料的参数用于说明「怎么做」
- 绝对禁止输出 `~~删除线~~` 格式的文字（strikethrough markdown），不要使用波浪线包裹文字

## 回答结构
### 🔍 专家分析
从焊接原理（原书理论）和工艺实践（上传资料）两个维度综合分析。引用格式：「据《材料焊接原理》...」「根据《XXX手册》...」

### 📖 全面科普
- 核心概念定义与原理
- **用Markdown表格**呈现参数对比/选型建议（直接引用上传资料中的表格数据）
- 工艺流程可用 ```mermaid 代码块描述

### ⚙️ 工艺方案
- 具体可操作的参数范围（优先使用上传资料的数值）
- 设备选型建议
- 质量检验要点

### 📚 知识来源
- 参考《材料焊接原理》第X章第X节「具体节名」
- 参考《上传资料名称》「具体章节名/表格名」
- 每条引用都要精确可追溯，说明从哪本书的哪个章节获取"""


def _clean_output(text: str) -> str:
    """清理LLM输出中的不良格式"""
    import re
    # 删除 ~~strikethrough~~ 格式
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # 删除单独成对的 ~~ 标记
    text = re.sub(r'~~', '', text)
    # 删除多余空行
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    return text.strip()


# ============================================================
# Config Loader
# ============================================================
def load_config(config_path: str = None) -> dict:
    """加载 YAML 配置文件"""
    if config_path is None:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning(f"Config file not found: {config_path}, using defaults")
        return {}
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return {}


# ============================================================
# LLM Client
# ============================================================
class LLMClient:
    """OpenAI 兼容接口 LLM 客户端"""

    def __init__(self, config: dict = None):
        if config is None:
            config = load_config()
        llm_cfg = config.get("llm", {})
        self.api_base = llm_cfg.get("api_base", "").rstrip("/")
        self.api_key = llm_cfg.get("api_key", "")
        self.model = llm_cfg.get("model", "deepseek-chat")
        self.max_tokens = llm_cfg.get("max_tokens", 4096)
        self.temperature = llm_cfg.get("temperature", 0.3)
        self.timeout = llm_cfg.get("timeout_seconds", 120)
        self.available = bool(self.api_key and self.api_key not in ("sk-your-api-key-here", ""))

    def chat(self, messages: list, stream: bool = False, max_tokens: int = None) -> Optional[str]:
        """
        调用 LLM，返回生成的文本
        失败时返回 None
        max_tokens: 覆盖默认值（压缩 prompt 时用较小值加快响应）
        """
        if not self.available:
            return None

        url = f"{self.api_base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
        }

        try:
            import urllib.request
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if "choices" in body and len(body["choices"]) > 0:
                    raw = body["choices"][0]["message"]["content"]
                    return _clean_output(raw)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
        return None

    def chat_intent(self, user_message: str, intent: str = "other",
                    rag_context: str = "", thin_catalog: str = "",
                    concept: dict = None, local_payload: dict = None,
                    max_tokens: int = 2000) -> Optional[str]:
        """
        意图感知的压缩版问答（v2.5）：
        - 按意图（concept/parameter/mixed/other）定制 system prompt
        - 只注入 命中书/章的薄目录 + top_k 相关段落，不再全量知识目录
        - max_tokens 2000（原 4096），显著降低响应延迟
        """
        system_content = WELDING_EXPERT_PROMPT

        # ---- 意图化指令 ----
        if intent in ("concept", "mixed"):
            system_content += (
                "\n\n## 当前问题类型：基本概念\n"
                "请重点输出以下结构（Markdown）：\n"
                "### 📖 概念解析\n核心定义与原理\n"
                "### 🔧 应用及拓展\n应用场景、选型要点、常见缺陷预防\n"
                "### ⚙️ 支持的大体工艺类型\n列出涉及的主要工艺\n"
                "### 📚 参考基座知识库与PDF来源\n逐条标注《书名》章节\n"
            )
        if intent in ("parameter", "mixed"):
            system_content += (
                "\n\n## 当前问题类型：工艺参数\n"
                "请重点输出以下结构（Markdown）：\n"
                "### ⚙️ 选型参数建议\n用 Markdown 表格给出参数名/建议值（优先使用下面提供的基座参数）\n"
                "### 🔧 应用拓展\n适用场景、工艺要点、缺陷预防\n"
                "### 📚 应用来源\n逐条标注《书名》章节\n"
            )

        # ---- 概念条目（权威基座知识） ----
        if concept:
            system_content += (
                "\n\n## 🧠 基座专家知识（概念条目，必须优先引用其内容与来源）\n"
                f"概念：{concept.get('name','')}（别名：{', '.join(concept.get('aliases', [])[:8])}）\n"
                f"概念解析：{concept.get('definition','')[:800]}\n"
                f"应用及拓展：{concept.get('application','')[:600]}\n"
                f"支持工艺类型：{'、'.join(concept.get('process_types', []))}\n"
            )
            srcs = concept.get("sources", [])
            if srcs:
                system_content += "来源：\n" + "\n".join(
                    f"- 《{s.get('book','')}》「{s.get('chapter','')}」{s.get('page_hint','')}"
                    for s in srcs[:6]
                ) + "\n"

        # ---- 本地已组装的参数建议（供 LLM 综合/润色） ----
        if local_payload:
            param_md = local_payload.get("param_md", "")
            if param_md:
                system_content += f"\n## 📊 本地基座参数匹配结果（可直接引用）\n{param_md[:1500]}\n"

        # ---- 薄目录 + 检索上下文（不再全量） ----
        if thin_catalog:
            system_content += f"\n## 📚 已学习资料目录（仅命中相关）\n{thin_catalog[:1500]}\n"
        if rag_context:
            system_content += f"\n## 📖 检索到的相关原文\n{rag_context[:1500]}\n"

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_message},
        ]
        return self.chat(messages, max_tokens=max_tokens)

    def chat_sync(self, user_message: str, context: str = "", uploaded_files: list = None,
                  knowledge_catalog: str = "", cross_source_matches: list = None) -> Optional[str]:
        """
        便捷方法：发送单条消息给焊接专家
        """
        system_content = WELDING_EXPERT_PROMPT

        # --- 注入所有已学习资料的知识目录 ---
        if uploaded_files or knowledge_catalog:
            system_content += "\n\n## 📚 你的完整知识库（以下所有来源平等对待，排名不分先后）\n\n"
            system_content += "### 来源0：《材料焊接原理》— 焊接理论教材，2篇9章\n"

            if uploaded_files:
                for i, fname in enumerate(uploaded_files, 1):
                    system_content += f"### 来源{i}：《{fname}》— 用户上传的工艺资料（已全文学习）\n"

            if knowledge_catalog:
                system_content += "\n" + knowledge_catalog + "\n"

            system_content += "\n**核心规则：你必须使用上述每本书的真实书名来引用它们。**\n"

            if cross_source_matches:
                system_content += "\n### 🔗 与当前问题最匹配的章节（按相关度排序，必须优先引用）\n"
                for m in cross_source_matches[:8]:
                    system_content += (
                        f"- 📄《{m['source']}》「{m['chapter']}」[相关度:{m['score']}]\n"
                        f"  匹配关键词: {', '.join(m.get('matched_keywords', [])[:8])}\n"
                        f"  章节摘要: {m.get('summary', '')[:150]}\n"
                    )
                system_content += "\n⚠️ 上述匹配章节中如包含表格数据、工艺参数，必须**直接引用**到回答中，使用书籍的真实名称。\n"

        # --- RAG检索上下文 ---
        if context:
            system_content += f"\n## 📖 从知识库全文检索到的补充内容\n{context[:3000]}\n"

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_message},
        ]
        return self.chat(messages)


# ============================================================
# Response Parser — 从 LLM 输出中提取结构化信息
# ============================================================
def parse_llm_response(raw_text: str, query: str, keywords: list, categories: list) -> dict:
    """
    将 LLM 原始输出解析为前端可渲染的结构化 JSON
    提取：专家分析段、科普段、表格、Mermaid 图、参考来源
    """
    import re

    # 提取 Mermaid 代码块
    mermaid_blocks = []
    mermaid_pattern = re.compile(r'```mermaid\s*\n(.*?)```', re.DOTALL)
    for m in mermaid_pattern.finditer(raw_text):
        mermaid_blocks.append(m.group(1).strip())

    # 提取 Markdown 表格
    tables = []
    table_pattern = re.compile(r'(\|.+\|\n\|[-| :]+\|\n(?:\|.+\|\n?)+)', re.MULTILINE)
    for m in table_pattern.finditer(raw_text):
        tables.append(m.group(1).strip())

    # 提取参考来源行（加固：剔除 markdown 标记、过滤残缺片段）
    references = []
    seen = set()
    ref_pattern = re.compile(r'(?:参考|参见)[\s：:]*(.+?)(?:\n|$)', re.MULTILINE)
    for m in ref_pattern.finditer(raw_text):
        ref = m.group(1).strip()
        # 剔除 markdown 标记（**粗体**、`代码`、行首 #、-、尾部破折号残渣）
        ref = re.sub(r'\*+', '', ref)
        ref = ref.strip().lstrip('#').strip().lstrip('-').strip()
        # 截断到「」或书名号结束：LLM 常见 `参考《X》「Y」 — 说明`，只保留到「Y」或《X》
        # 找到「」闭合 或 《》闭合位置
        m_cn = re.search(r'「([^」]{1,30})」', ref)
        m_book = re.search(r'《([^》]{1,40})》', ref)
        if m_cn:
            ref = ref[:m_cn.end()]
        elif m_book:
            ref = ref[:m_book.end()]
        # 过滤残缺片段：必须含《》书名号 或 已知标准/书名，且长度足够
        if not (len(ref) >= 6 and ('《' in ref or any(k in ref for k in ("GB ", "AWS", "ISO", "第", "章", "节")))):
            continue
        if not ref:
            continue
        ref_full = f"参考{ref}" if not ref.startswith("参考") else ref
        key = ref_full.split('「')[0][:30]
        if key not in seen:
            seen.add(key)
            references.append(ref_full)

    # 清理 Mermaid 块（避免 markdown 渲染时出错）
    clean_text = mermaid_pattern.sub(lambda m: f'<div class="mermaid-placeholder" data-mermaid="{m.group(1).strip().replace(chr(34), "&quot;").replace(chr(10), "\\n")}"></div>', raw_text)

    return {
        "query": query,
        "keywords": keywords,
        "matched_categories": categories,
        "model_used": "llm",
        "raw_markdown": raw_text,
        "clean_markdown": clean_text,
        "mermaid_blocks": mermaid_blocks,
        "tables": tables,
        "references": references,
    }


# ============================================================
# Singleton
# ============================================================
_client: Optional[LLMClient] = None


def get_client(config_path: str = None) -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient(load_config(config_path))
    return _client
