# 焊接工艺专家系统 v2.7

这是一个面向焊接知识问答、工艺参数推荐和机器人焊接工艺卡片生成的本地专家系统。项目以 `saved_knowledge` 中已经学习的书籍为知识底座，优先在本地完成检索、路由和结构化回答；只有本地证据不足并且配置了 LLM 时，才调用 OpenAI 兼容接口补充答案。

当前集成基线分支是 `refactor_V2`，不是 `main`。

## 1. 核心能力

- 多书知识库：保存 PDF 全文、章节、关键词、表格和数据点。
- Jieba 分词：注册焊接专业词，使用搜索模式多粒度分词，替代机械的中文单双字切分。
- 本地检索：关键词匹配、跨书章节检索、特征向量余弦检索和可选语义向量增强。
- 专家知识库：把规范概念、别名、定义、应用场景和来源整理为可查询条目。
- 意图路由：区分概念、工艺参数、综合和通用问题。
- 工艺卡片：输出母材、板厚、坡口、电参数、热参数、机器人参数、质量检查及装备信息等机器可读 JSON。
- 本地优先：高置信答案或已生成工艺卡片时不调用 LLM。
- LLM 兜底：只注入命中的薄目录和少量相关原文，默认最多生成 2000 Token。
- 答案缓存：LRU + TTL + 相似问题命中，知识源变化后自动失效。
- SSE 流式回答：LLM 内容按增量展示；完成事件仍返回完整结构化结果。
- 可观测性：记录请求量、LLM 调用/失败、本地回答、缓存命中、P50/P95、分阶段耗时和 TTFT。
- PDF 导入：支持文本 PDF；扫描 PDF 可使用单独配置的 PaddleOCR GPU 环境。

## 2. 技术路线

### 2.1 技术栈

| 层 | 技术 |
|---|---|
| Web 后端 | Python、FastAPI、Uvicorn、Pydantic |
| 前端 | 原生 HTML/CSS/JavaScript、Fetch、SSE、Marked、Mermaid |
| 文本处理 | Jieba、焊接术语词典、繁简/缩写/同义词归一化 |
| 本地检索 | TF-IDF 兜底检索、NumPy 特征向量、余弦相似度、可选语义向量 |
| 知识存储 | JSON + TXT 文件；不依赖数据库即可运行 |
| PDF | PyMuPDF、pdfplumber、PyPDF2；Pytesseract 备用 |
| 扫描件 OCR | 可选 PaddleOCR + OpenCV + CUDA GPU 环境 |
| LLM | OpenAI 兼容的 `/chat/completions` 接口 |

### 2.2 一次查询的执行链

```text
用户问题
  ↓
答案缓存：规范化后做精确命中或相似命中
  ↓ 未命中
本地分析：关键词、类别、跨书章节
  ↓
专家概念库 + 向量检索
  ↓
意图路由：概念 / 参数 / 综合 / 通用
  ↓
参数问题生成机器可读工艺卡片
  ↓
本地置信度足够？ ── 是 → 本地回答并写入缓存
  │ 否
  ↓
构造薄 Prompt：命中目录 + Top-K 相关原文
  ↓
LLM 普通响应或 SSE 流式响应
  ↓
解析为统一 QueryResponse → 写入缓存 → 前端渲染
```

这里的“本地优先”很重要：缓存、专家库、路由、工艺卡片和向量检索都在 LLM 之前。LLM 是低置信问题的兜底，不是每次查询的必经步骤。

## 3. `saved_knowledge` 到底如何加载

目录大致如下：

```text
saved_knowledge/
├── registry.json
├── 材料焊接原理_完整版/
│   ├── full_text.txt
│   ├── chapters.json
│   ├── keywords.json
│   ├── data_points.json
│   └── tables.json
├── 焊接结构原理/
└── 实用焊接工艺手册_第二版/
```

`registry.json` 的 `sources` 只是一份“书籍登记表”，保存书籍 ID、文件名、章节数、关键词数等元数据。`return self.registry["sources"]` 返回的是这份目录，不会把全文一起返回。

真正需要内容时，`app/knowledge_store.py` 会根据书籍 ID 继续读取对应目录中的文件：

- `get_full_text(source_id)` 读取 `full_text.txt`；
- `get_chapters(source_id)` 读取 `chapters.json`；
- `get_keywords(source_id)` 读取 `keywords.json`；
- 其他方法按需读取表格和数据点。

专家库、向量索引和答案缓存属于可再生运行时产物，默认不提交到 Git：

```text
saved_knowledge/expert_kb.json
saved_knowledge/vector_index/
saved_knowledge/answer_cache.json
```

服务第一次查询时会检查这些产物。如果文件不存在，或 `registry.json` 反映的书籍内容已经变化，系统会从各书的 `chapters.json`、`full_text.txt` 等持久化资料自动重建专家库和向量索引。因此，保存书籍数据与构建搜索索引是两层不同的工作。

## 4. 项目结构

```text
PyCharmMiscProject/
├── app/
│   ├── answer_cache.py          # LRU/TTL/相似缓存及统计
│   ├── config.yaml              # LLM、路由、缓存、向量和装备配置
│   ├── expert_knowledge_base.py # 专家概念库构建与查询
│   ├── knowledge_store.py       # registry 和每本书文件的读写入口
│   ├── llm_service.py           # 普通/流式 OpenAI 兼容调用
│   ├── metrics_service.py       # 性能与调用指标
│   ├── pdf_parser.py            # PDF 文本、表格和 OCR 解析
│   ├── process_card.py          # 工艺卡片组装
│   ├── qa_router.py             # 意图、置信度和参数匹配
│   ├── rag_retriever.py         # Jieba + TF-IDF 检索器
│   ├── robot_welding.py         # 机器人焊接规则
│   ├── vector_store.py          # NumPy 向量索引
│   ├── welding_knowledge_base.py# 焊接术语、类别和参数基座
│   └── welding_qa_system.py     # 关键词抽取与本地结构化回答
├── saved_knowledge/             # 已学习书籍及可再生索引
├── static/index.html            # Web 聊天、上传和流式渲染
├── tools/
│   ├── benchmark.py             # 无 LLM、临时缓存性能基准
│   ├── build_expert_kb.py       # 手动重建专家库/向量库
│   ├── gpu_ingest.py            # GPU OCR 入库
│   ├── inspect_knowledge.py     # 知识库检查
│   ├── run_welding_qa.py        # 命令行问答
│   ├── test_runtime_features.py # 缓存/指标/SSE 回归测试
│   └── tests.py                 # 完整本地测试集
├── requirements.txt
├── server.py                    # FastAPI 入口和统一查询流程
└── start.py                     # 自动学习 uploads 后启动服务
```

## 5. 本地启动

推荐 Python 3.12。仓库没有 `environment.yml`，因此没有预定义的 Conda 环境；当前本地开发方式是项目内的 `.venv`。

### 5.1 首次创建环境

```powershell
cd G:\ZEQP_CODE\PyCharmMiscProject
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

如果 PowerShell 禁止执行激活脚本，可以只对当前窗口放开：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

不激活也可以，直接使用虚拟环境中的解释器：

```powershell
.\.venv\Scripts\python.exe start.py
```

### 5.2 配置 LLM（可选）

没有 LLM Key 时，本地知识库、检索和工艺卡片仍可运行。推荐通过环境变量配置密钥，避免提交到 Git：

```powershell
$env:WELDING_LLM_API_KEY="sk-你的密钥"
$env:WELDING_LLM_API_BASE="https://api.deepseek.com/v1"
$env:WELDING_LLM_MODEL="deepseek-chat"
```

也可编辑 `app/config.yaml`。仓库中的 `sk-REVOKED_REPLACE_ME` 会被识别为占位符，不会触发网络调用。

### 5.3 启动服务

```powershell
.\.venv\Scripts\python.exe start.py
```

浏览器访问：

- Web 页面：<http://localhost:8000>
- OpenAPI 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/health>

`start.py` 会扫描 `uploads` 中尚未学习的 PDF，完成入库后启动 FastAPI。也可以直接运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8000
```

直接运行 Uvicorn 时，缺失的专家库和向量索引会在第一次查询时从 `saved_knowledge` 自动重建。

## 6. 扫描 PDF 与 GPU 环境

基础 `.venv` 足以运行 Web、本地检索、文本 PDF 和测试。扫描版 PDF 的 PaddleOCR GPU 流程是可选能力，需要自行创建 `.venv-gpu` 并安装与本机 CUDA 匹配的 PaddlePaddle、PaddleOCR 和 OpenCV；该大体积环境不在 `requirements.txt` 中自动安装。

配置完成后可执行：

```powershell
.\.venv-gpu\Scripts\python.exe tools\gpu_ingest.py "某本手册.pdf"
```

不要把本机 CUDA 版本不匹配的 GPU 依赖直接加入普通 `.venv`。

## 7. API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/query` | 普通结构化问答 |
| `POST` | `/api/query/stream` | SSE 流式问答；`done` 事件携带完整响应 |
| `GET` | `/api/health` | 服务和 LLM 状态 |
| `GET` | `/api/categories` | 原书及已学习 PDF 目录 |
| `GET` | `/api/knowledge/inspect?q=` | 知识库和匹配检查，不调用 LLM |
| `GET` | `/api/config/status` | 当前 LLM 与上传文件状态 |
| `POST` | `/api/upload-pdf` | 上传单个 PDF |
| `POST` | `/api/upload-pdfs` | 批量上传 PDF |
| `GET` | `/api/upload-status/{job_id}` | GPU OCR 任务进度 |
| `GET` | `/api/uploaded-files` | 上传文件列表 |
| `DELETE` | `/api/uploaded-files/{filename}` | 删除一本上传资料并更新索引 |
| `GET` | `/api/metrics` | 请求、LLM、缓存和耗时指标 |
| `POST` | `/api/metrics/reset` | 重置内存指标和缓存统计，不删除答案 |
| `GET` | `/api/cache/stats` | 缓存容量、命中率、淘汰和过期统计 |
| `POST` | `/api/cache/invalidate` | 清空答案缓存 |

普通请求示例：

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/query `
  -ContentType application/json `
  -Body '{"query":"Q345钢板12mm MIG焊参数"}'
```

SSE 事件协议：

```text
event: start   data: {"query":"..."}
event: token   data: {"text":"增量文本"}
event: done    data: {"response":{完整 QueryResponse}}
event: error   data: {"message":"错误信息"}
```

## 8. 测试与性能基准

所有默认测试都不调用 LLM：

```powershell
# 完整测试：分词、知识完整性、匹配、跨书、端到端、归一化、链路、运行时
.\.venv\Scripts\python.exe tools\tests.py

# 只测试缓存统计、性能指标和 SSE
.\.venv\Scripts\python.exe tools\tests.py --runtime

# 语法编译检查
.\.venv\Scripts\python.exe -m compileall -q app server.py
```

离线性能基准会禁用 LLM，并使用临时缓存，不会消耗 Token，也不会覆盖正式答案缓存：

```powershell
# 30 道题，分别测首次本地回答和第二次缓存命中
.\.venv\Scripts\python.exe tools\benchmark.py

# 快速抽测 5 道题并保存结果
.\.venv\Scripts\python.exe tools\benchmark.py --questions 5 --save
```

生成的 `benchmark_result*.json` 已加入 `.gitignore`。

## 9. 开发时建议先看什么

建议按以下顺序阅读：

1. `README.md`：先建立全局数据流。
2. `server.py` 的 `process_query`、`_prepare_query` 和 `stream_query_events`：理解一次请求如何走完整链路。
3. `app/knowledge_store.py`：理解 `registry.json` 与每本书实际文件的关系。
4. `app/welding_qa_system.py` 和 `app/welding_knowledge_base.py`：理解关键词、归一化和类别匹配。
5. `app/expert_knowledge_base.py`、`app/vector_store.py`、`app/qa_router.py`：理解专家库、检索和路由。
6. `app/process_card.py` 与 `app/robot_welding.py`：理解确定性工艺卡片。
7. `app/llm_service.py`：理解何时调用 LLM、Prompt 如何压缩及 SSE 如何解析。
8. `tools/tests.py`：从断言反推系统的预期行为。

## 10. 当前边界

- 工艺卡片中的规则值仍需结合实际机器人、焊机、焊枪和现场 WPS/PQR 标定，不能直接替代生产审批。
- NumPy 特征向量适合本地轻量检索；若数据规模显著增长，可迁移到专用向量数据库和更完整的嵌入模型。
- 性能指标保存在单进程内存中，服务重启后清零；多进程部署时需接入 Prometheus 等集中式指标系统。
- 答案缓存也按单进程维护并持久化到本地文件，不适合多个实例同时写同一目录。
- 扫描 PDF 的识别质量取决于页面清晰度、表格复杂度和 OCR 环境，入库后应使用 `tools/inspect_knowledge.py` 检查章节与关键词质量。
