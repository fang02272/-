"""
LLM 服务层 — OpenAI 兼容接口
支持 DeepSeek / Kimi / Qwen / Ollama / 任意 OpenAI 兼容 API
"""

import json
import logging
import os
import sys
from typing import Optional, Generator

import yaml

logger = logging.getLogger("llm_service")


# ============================================================
# 焊接工艺专家 System Prompt（精简版）
# ============================================================
WELDING_EXPERT_PROMPT = """你是专业焊接工艺知识问答系统。

## 知识来源
1. **《材料焊接原理》**（王宗杰，2024）— 焊接理论教材，2篇9章
2. **用户上传工艺资料** — 已全文学习，与原书同等对待

## 回答规则
- 同时检索所有知识来源，融合成完整回答
- 上传资料的具体参数和表格数据**优先使用**
- 原书理论解释"为什么"，上传资料说明"怎么做"
- 禁止输出 `~~删除线~~` 格式

## 固定回答结构（必须按此4段输出）
### 🔍 专家分析
从焊接原理和工艺实践两维度分析。引用格式：「据《材料焊接原理》...」「根据《XXX手册》...」

### 📖 全面科普
核心概念 + Markdown参数表格（直接引用上传资料数据）

### ⚙️ 工艺方案
具体参数范围 + 设备选型 + 质量检验要点

### 📚 知识来源
精确引用书名+章节，每条可追溯"""


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
        self.max_tokens = llm_cfg.get("max_tokens", 2000)   # 精简：4096 → 2000
        self.temperature = llm_cfg.get("temperature", 0.3)
        self.timeout = llm_cfg.get("timeout_seconds", 120)
        self.available = bool(self.api_key and self.api_key not in ("sk-your-api-key-here", ""))

    def chat(self, messages: list, stream: bool = False) -> Optional[str]:
        """
        调用 LLM，返回生成的文本
        失败时返回 None
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
            "max_tokens": self.max_tokens,
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

    def chat_stream(self, messages: list) -> Generator[str, None, None]:
        """
        流式调用 LLM，逐块 yield 文本片段（token by token）。
        调用方负责捕获异常。
        """
        if not self.available:
            return

        url = f"{self.api_base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }

        try:
            import urllib.request
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            yield token
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except Exception as e:
            logger.error(f"LLM stream failed: {e}")

    def _build_messages(
        self,
        user_message: str,
        context: str = "",
        uploaded_files: list = None,
        cross_source_matches: list = None,
    ) -> list:
        """
        构建发给 LLM 的 messages 列表（精简版）：
        - System Prompt 固定部分（已精简）
        - 知识源列表（仅文件名，不含完整目录）
        - 最多 5 条跨书章节匹配
        - RAG 上下文（截断至 2000 字）
        """
        system_content = WELDING_EXPERT_PROMPT

        # 注入知识源文件名（精简：不传完整目录）
        if uploaded_files:
            names = "\n".join(f"- 《{f}》" for f in uploaded_files)
            system_content += f"\n\n## 已学习资料\n{names}\n引用时使用真实书名。"

        # 最相关章节（最多5条，摘要截断至100字）
        if cross_source_matches:
            system_content += "\n\n## 最匹配章节（必须优先引用）\n"
            for m in cross_source_matches[:5]:
                summary = m.get('summary', '')[:100]
                kws = ', '.join(m.get('matched_keywords', [])[:6])
                system_content += (
                    f"- 《{m['source']}》「{m['chapter']}」"
                    f"[相关度:{m['score']}] 关键词:{kws} 摘要:{summary}\n"
                )

        # RAG 检索内容（截断至 2000 字，原来是 3000）
        if context:
            system_content += f"\n\n## 检索到的相关内容\n{context[:2000]}"

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_message},
        ]

    def chat_sync(self, user_message: str, context: str = "", uploaded_files: list = None,
                  knowledge_catalog: str = "", cross_source_matches: list = None) -> Optional[str]:
        """
        便捷方法：发送单条消息给焊接专家（非流式）
        knowledge_catalog 参数保留但不再注入（精简 Prompt）
        """
        messages = self._build_messages(
            user_message,
            context=context,
            uploaded_files=uploaded_files,
            cross_source_matches=cross_source_matches,
        )
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

    # 提取参考来源行
    references = []
    ref_pattern = re.compile(r'(?:参考|参见)[\s：:]*(.+?)(?:\n|$)', re.MULTILINE)
    for m in ref_pattern.finditer(raw_text):
        ref = m.group(1).strip()
        if any(kw in ref for kw in ["材料焊接原理", "GB", "AWS", "ISO", "第", "章", "节"]):
            references.append(f"参考{ref}" if not ref.startswith("参考") else ref)

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
