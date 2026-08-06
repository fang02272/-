# 焊接工艺知识问答系统 v2.5

> AI大模型 + 专家知识库 + 本地向量库 + PDF持续学习 + 意图路由  
> 目标：打造焊接领域专属知识问答引擎，减少通用大模型token消耗，提高工艺推荐精准度

---

## 知识库组成（4层）

| 层级 | 来源 | 规模 | 说明 |
|------|------|------|------|
| **第1层** | 《材料焊接原理》(王宗杰,2024) | 2篇9章 · 150+关键词 | 焊接理论核心，侧重冶金原理和材料焊接性 |
| **第2层** | 《实用焊接工艺手册》(王洪光,2014) | 13章 · 1679关键词 · 9种材料参数 | 工艺工具书，侧重现场参数、焊材选型、板厚-电流对照 |
| **第3层** | 用户上传PDF | 动态增长 | 自动学习（目录/章节/关键词/数据提取） |
| **第4层** | 材料-参数结构化对照表 | 9种材料 · 6种焊条 · 6种工艺 | 精准参数查表，零token消耗 |

## v2.5 新增能力

- **意图路由**：概念问题 ↔ 工艺参数问题差异化回答（意图解析/工艺匹配为内部思考，不在前端展示）
- **专家知识库**：106 概念条目（解析/应用拓展/工艺类型/来源），基座 `expert_kb.json`
- **本地向量库**：numpy 特征向量 + 余弦检索，多本书增量压缩入库
- **本地匹配优先**：高置信（≥0.78）直接返回本地答案，跳过 LLM
- **结果缓存**：相同/相似问题 LRU+TTL 缓存，避免重复调用
- **OCR 增强**：PaddleOCR 优先 + cv2 预处理 + 纠错字典 + 修复重复页 bug

---

## 项目文件结构与功能

```
PyCharmMiscProject/
│
├── app/                         # 【核心应用包】
│   ├── server.py                #  （见根目录入口）
│   ├── config.yaml              #   LLM API密钥/模型/路由阈值/缓存配置
│   ├── llm_service.py           #   LLM层：OpenAI兼容接口 + 意图感知压缩 prompt
│   ├── welding_qa_system.py     #   匹配引擎：关键词提取→类别映射→结构化输出
│   ├── welding_knowledge_base.py#   基座知识：1173关键词映射 + 6工艺 + 9材料参数
│   ├── knowledge_store.py       #   知识存储：PDF学习 + 目录/章节/关键词持久化
│   ├── pdf_parser.py            #   PDF解析：文本/表格/图片 + PaddleOCR扫描件
│   ├── rag_retriever.py         #   RAG检索（兜底）：TF-IDF + 余弦相似度
│   ├── vector_store.py          #   本地向量库：numpy特征向量 + 增量索引
│   ├── expert_knowledge_base.py #   专家基座：概念→解析/应用/工艺类型/来源
│   ├── qa_router.py             #   意图路由：概念/参数判定 + 参数匹配
│   └── answer_cache.py          #   结果缓存：LRU+TTL+相似度命中
│
├── server.py                    # 【Web服务入口】FastAPI后端（所有API接口）
├── start.py                     # 【启动入口】一键启动 + 自动学习uploads/PDF
├── requirements.txt             # 【依赖】Python包清单
├── README.md                    # 【本文档】
│
├── tools/                       # 【工具脚本】
│   ├── build_expert_kb.py       #   重建专家知识库 + 向量库（可重复执行）
│   ├── inspect_knowledge.py     #   知识库自检（不调LLM）
│   ├── relearn_book.py          #   重新学习指定手册
│   ├── run_welding_qa.py        #   命令行问答
│   └── tests.py                 #   测试用例
│
├── static/
│   └── index.html               # 【前端UI】聊天界面 + Markdown/Mermaid渲染 + PDF上传
│
├── uploads/                     # 【上传目录】放入PDF → 启动自动学习
├── saved_knowledge/             # 【知识存储】
│   ├── registry.json            #   知识源注册表
│   ├── expert_kb.json           #   专家知识库（概念条目）
│   ├── vector_index/            #   本地向量库（npy + meta）
│   ├── answer_cache.json        #   问答结果缓存
│   └── {book_id}/               #   每本书：full_text/chapters/keywords/data_points
│
├── data/                        # 【原始资料】welding_book_full.txt 等
└── __pycache__/                 #   Python字节码缓存
```

---

## 核心数据流

```
用户提问
    │
    ▼
┌─────────────── 匹配引擎 ───────────────┐
│ welding_qa_system.py                    │
│  ├─ extract_keywords()  关键词提取      │
│  │   ├─ 原书 150+ 映射                  │
│  │   └─ 所有上传PDF关键词 (动态注入)     │
│  ├─ match_categories()  类别映射        │
│  └─ generate_structured() 结构化输出    │
└───────────────┬─────────────────────────┘
                │
    ┌───────────▼───────────┐
    │     RAG 检索           │
    │  rag_retriever.py      │
    │  TF-IDF + 余弦相似度    │
    │  检索范围: 原书+所有PDF │
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │   LLM 生成 (可选)      │
    │  llm_service.py        │
    │  System Prompt:        │
    │  · 平等对待所有知识源  │
    │  · 优先用上传资料数据  │
    │  · 精确引用书名+章节   │
    └───────────┬───────────┘
                │
                ▼
        结构化回答 (5段式)
    科普 → 交叉分析 → 推荐 → 来源 → 迁移
```

---

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置 LLM（可选）
编辑 `config.yaml`，填入 API Key：
```yaml
llm:
  api_base: "https://api.deepseek.com/v1"
  api_key: "sk-your-key"
  model: "deepseek-chat"
```
不配置也可运行（本地知识库模式，不消耗token）。

### 3. 导入工艺手册
将焊接工艺 PDF 放入 `uploads/` 文件夹。

### 4. 启动
```bash
python start.py
# → http://localhost:8000
```
启动时自动学习 `uploads/` 中所有 PDF（文本版直接提取，扫描版需Tesseract OCR）。

---

## 知识库自检（不耗token）

```bash
# 查看所有已学习书籍
python inspect_knowledge.py

# 测试关键词匹配效果
python inspect_knowledge.py "弧焊机器人焊接参数"

# 查看完整目录
python inspect_knowledge.py --chapters

# 浏览器在线查看
http://localhost:8000/api/knowledge/inspect?q=焊接参数
```

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/query` | 问答（LLM生成或本地回退） |
| `POST` | `/api/upload-pdf` | 上传单个PDF学习 |
| `POST` | `/api/upload-pdfs` | 批量上传多个PDF |
| `GET` | `/api/categories` | 知识目录（原书+已学习PDF） |
| `GET` | `/api/knowledge/inspect?q=` | 知识库自检（不调LLM） |
| `GET` | `/api/config/status` | LLM配置状态 |
| `GET` | `/api/health` | 健康检查 |

---

## 如何添加新工艺手册

```
1. PDF 放入 uploads/ 文件夹
2. 重启 python start.py
3. 启动日志显示学习结果
4. python inspect_knowledge.py 验证
5. 前端 📖书籍目录 查看新书目录树
6. 提问时自动匹配新书内容
```

**扫描版PDF**: 需先安装 Tesseract OCR  
https://github.com/UB-Mannheim/tesseract/wiki  
安装时勾选 Chinese Simplified 语言包

---

## 演进路线图

### 当前 (v2.0) — 完成
- [x] LLM + RAG 双引擎
- [x] 《材料焊接原理》9章完整结构化
- [x] PDF自动学习（目录/章节/关键词/数据）
- [x] 多书知识融合匹配
- [x] 扫描PDF OCR识别
- [x] 知识库自检工具

### 下一步 (v2.5) — 提高精准度，减少token
- [ ] **本地匹配优先**：关键词匹配命中度高时直接返回本地内容，跳过LLM
- [ ] **结果缓存**：相同/相似问题缓存答案，避免重复调用
- [ ] **参数化回答模板**：高频问题（如"XX板厚焊XX材料用什么参数"）走模板+知识库填充，零token
- [ ] **分层检索**：先用本地知识库回答，LLM仅做润色和补充

### 远期 (v3.0) — 专属焊接大模型
- [ ] **领域微调**：用积累的问答对微调开源模型（Qwen/Llama）
- [ ] **知识蒸馏**：将多本书籍知识压缩为向量数据库
- [ ] **工艺推荐引擎**：根据材料-板厚-工况自动推荐最优工艺参数
- [ ] **测试用例体系**：标准问答对 + 准确率评估 + 回归测试

---

## 提高扫描解析率的方案

| 方案 | 效果 | 实施难度 |
|------|------|---------|
| Tesseract OCR + 中文包 | 扫描件基础识别 | ★☆☆ 已实现 |
| 提高OCR DPI (300→600) | 识别率提升30% | ★☆☆ 改参数 |
| PaddleOCR (百度OCR) | 中文识别率更高 | ★★☆ pip安装 |
| 扫描件预处理 (去噪/纠偏) | 大幅提升 | ★★☆ OpenCV |
| 表格专用OCR (TableBank) | 表格数据提取 | ★★★ 需训练 |

## 提高匹配推荐率的方案

| 方案 | 效果 | 实施难度 |
|------|------|---------|
| 章节TF-IDF权重调优 | 相关度排序更准 | ★☆☆ 改参数 |
| BM25替代TF-IDF | 检索效果提升 | ★★☆ 换算法 |
| 同义词扩展 (焊接→熔焊→电弧焊) | 召回率提升 | ★★☆ 词库扩展 |
| 材料-参数映射表 | 精准参数推荐 | ★★☆ 结构化建表 |
| 用户反馈学习 (点赞/踩) | 持续优化 | ★★★ 需积累数据 |

---

## 测试用例方案

### 1. 关键词匹配测试
```python
# test_matching.py
test_cases = [
    # (查询, 期望匹配到的书)
    ("弧焊机器人焊接参数", ["材料焊接原理", "弧焊工艺手册"]),
    ("Q345钢板预热温度", ["材料焊接原理", "焊接结构原理"]),
    ("不锈钢TIG焊工艺", ["材料焊接原理"]),
]
for query, expected_books in test_cases:
    kws = qa.extract_keywords(query)
    cats = qa.match_categories(kws)
    matched_books = [c for c in cats if c.startswith("📄_")]
    assert all(any(b in c for c in matched_books) for b in expected_books)
```

### 2. 知识库完整性测试
```python
# test_knowledge.py
for src in store.list_sources():
    assert src['chapter_count'] >= 1, f"{src['filename']} 未提取到章节"
    assert src['keyword_count'] >= 10, f"{src['filename']} 关键词不足"
    assert src['text_length'] >= 1000, f"{src['filename']} 文本量异常"
```

### 3. 端到端测试
```python
# test_e2e.py — 不调LLM，验证完整链路
result = qa.generate_structured("焊缝结晶裂纹")
assert len(result['keywords']) > 0
assert len(result['sections']['science']['content']) > 100
```

---

## 依赖清单

| 包 | 用途 |
|------|------|
| fastapi + uvicorn | Web服务框架 |
| pydantic | 数据验证 |
| PyYAML | 配置解析 |
| PyMuPDF (fitz) | PDF文本/图片提取 |
| pdfplumber | PDF表格提取 |
| PyPDF2 | PDF备用提取 |
| python-multipart | 文件上传 |
| pytesseract + Pillow | 扫描PDF OCR（可选） |

---

## 常见问题

**Q: 端口冲突 (10013/10048)？**  
A: `python start.py` 会自动释放端口。不要手动运行 `python server.py`。

**Q: 上传PDF后显示0章0关键词？**  
A: PDF是扫描版（图片），需安装Tesseract OCR。文字版PDF不存在此问题。

**Q: 如何不花token检查知识库？**  
A: `python inspect_knowledge.py` 或浏览器访问 `/api/knowledge/inspect`

**Q: 如何批量导入多本书？**  
A: 把所有PDF放入 `uploads/` → 运行 `python start.py` → 自动逐一学习。
# PyCharmMisProject
