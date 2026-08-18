# 焊接工艺知识问答系统 v2.6

> AI大模型 + 专家知识库 + 本地向量库 + PDF持续学习 + 意图路由 + 工艺卡片  
> 目标：打造焊接领域专属知识问答引擎，减少通用大模型token消耗，提高工艺推荐精准度

---

## 知识库组成（4层）

| 层级 | 来源 | 规模 | 说明 |
|------|------|------|------|
| **第1层** | 《材料焊接原理》(王宗杰,2024) | 2篇9章 · 1122关键词 · 179表格 | 焊接理论核心，侧重冶金原理和材料焊接性 |
| **第2层** | 《实用焊接工艺手册》(王洪光,2014) | 13章 · 1899关键词 · 112表格 · 9种材料参数 | 工艺工具书，侧重现场参数、焊材选型、板厚-电流对照 |
| **第3层** | 《焊接结构原理》 | 13章 · 134关键词 · 1表格 | 焊接结构/热源/应力理论 |
| **第4层** | 材料-参数结构化对照表 | 9种材料 · 6种焊条 · 6种工艺 | 精准参数查表，零token消耗 |

## v2.6 新增能力

- **机器人焊接能力**（本周核心）：默认工艺 SMAW→**GMAW/MIG**，新增 `robot_welding.py` 规则库（焊丝选型 ER50-6/ER308L、保护气配比、TCP/导电嘴、枪姿态分解、船型焊 PA、层道电流电压序列、管道 6G 半圈分段）
- **专家库概念定义全量覆盖**：123 概念 **92% 手工定义（114 个）**，0 无定义 / 0 应用缺失 / 0 跑题摘要（手工定义表覆盖工艺方法/材料/缺陷/组织/冶金/检测 55+ 概念，每个含定义+应用要点+工艺类型）
- **概念解析质量**：干净文字优先（手工定义→深度分析→章节摘要，不再抓 OCR 原文段落）、滑动窗口主题提取（修复"氩弧焊被埋弧焊抢答"）、OCR 乱码/[Page]/页眉/图注过滤、来源相关度排序
- **健壮性修复**：空答案不缓存（避免命中 0 字空回复）、参考来源提取加固（剔除 markdown 标记/过滤残缺片段/截断到书名号闭合）
- **词库扩充**：关键词库 1173→**1317**，同义词组 106→**123**（电弧物理/冶金/凝固/相变/牌号/裂纹/热循环/HAZ/氢/冷裂/强韧性/微观/异种界面/表面改性/活性钎焊/中间层/扩散焊/成形/NDT/位置/接头）
- **jieba 智能分词**：注册焊接术语高频防误切 + 搜索模式多粒度切分（`焊接热影响区/冷裂纹/预热温度` 保持完整词）
- **六阶段关键词提取**：繁简/上下标归一化 → 数值参数 → 子串+ngram+去标点 → 长术语吞并 → 双向别名扩展 → 噪声过滤（`SCC`→应力腐蚀开裂、`鋁`→铝、`焊条电弧焊`→SMAW）
- **表格重建 + 并入章节**：扫描版定向 OCR 重建表格，内容并入章节正文（检索命中表格数值）
- **卡诺普机器人焊接真值库**：206 条实测参数（碳钢 98/镀锌板 54/不锈钢 54），工艺卡片优先输出真值（电流/电压/速度/焊枪角度/摆幅/频率），标注"卡诺普实测"
- **语义向量检索**：bge-small-zh 中文 embedding（512维），与特征向量融合（0.7特征+0.3语义），识别语义近义（`不锈钢腐蚀防护`→应力腐蚀开裂）
- **三层匹配融合**：关键词 → 向量 → 专家库概念，关键词未命中时用向量 top 补概念
- **OCR 乱码过滤**：摘要/概念定义过滤中文占比<0.55 的乱码句
- **git 历史清理**：.git 1.1GB→94MB（移除历史大文件），API key 历史抹除
- 外部化停用词表 `stop_words.json`

## v2.5 新增能力

- **工艺卡片**：参数问题输出**机器可读 JSON 工艺卡片**（母材/板厚/工艺/坡口/电参数/机器人参数/质量评估 + 装备信息），供**仿真、机器人路径规划与操控**使用，减少示教时间
- **意图路由**：概念问题 ↔ 工艺参数问题差异化回答（意图解析/工艺匹配为内部思考，不在前端展示）
- **专家知识库**：123 概念条目（解析/应用拓展/工艺类型/来源），基座 `expert_kb.json`
- **本地向量库**：numpy 特征向量 + 余弦检索，多本书增量压缩入库
- **本地匹配优先**：高置信（≥0.78）直接返回本地答案，跳过 LLM
- **结果缓存**：相同/相似问题 LRU+TTL 缓存，避免重复调用
- **OCR 增强**：PaddleOCR 优先 + cv2 预处理 + 纠错字典 + 修复重复页 bug
- **GPU 环境**：Python 3.12 + paddlepaddle-gpu(CUDA 11.8)，全系统单环境运行，扫描版自动 GPU 识别
- **前端 GPU 上传**：扫描版 PDF 上传自动 GPU 识别（进度轮询），服务自带 GPU 时进程内直算
- **命令行工艺卡片**：`tools/run_welding_qa.py` 与 Web 端共用引擎，参数问题直接输出工艺卡片

## 融合参考实现

| 来源 | 融合内容 |
|------|---------|
| `welding_knowledge_base.py`（参考词库） | TERM_ALIAS_MAP 123组、KEYWORD_CATEGORY_MAP 扩至 1317 词（电弧物理/冶金/凝固/相变/牌号/裂纹/热循环/HAZ/氢/冷裂/强韧性/微观/异种界面/表面改性/活性钎焊/中间层/扩散焊/成形/NDT/位置/接头）|
| `welding_qa_system.py`（搜索端） | 六阶段关键词提取（繁简归一化/数值参数/候选收集/吞并过滤/双向别名扩展/噪声过滤）|
| `knowledge_store.py`（导入端） | 表格关键词并入、表格并入章节、`_WELDING_CHARS` 动态特征字、`relearn_keywords` |
| git 分支 `feature/replace-ngram-with-jieba-tokenization` | **jieba 分词**（替代纯 n-gram）：注册焊接术语高频防误切 + `cut_for_search` 多粒度 |
| `stop_words.json`（参考） | 外部化停用词表（144 词）|
| 机器人焊接需求（卡诺普场景） | **`robot_welding.py`**：焊丝选型/送丝/TCP/枪姿态/船型焊/层道序列/管道6G（机器人工艺卡片）|
| 汇报 PPT | **`gen_report_ppt.py`**：真表格/柱状图/架构图生成汇报幻灯片 |

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
│   ├── expert_knowledge_base.py #   专家基座：概念→解析/应用/工艺类型/来源 + 手工定义表
│   ├── qa_router.py             #   意图路由：概念/参数判定 + 参数匹配 + 管道识别
│   ├── process_card.py          #   工艺卡片：结构化/机器可读 + 机器人参数 + 装备
│   ├── robot_welding.py         #   机器人焊接规则库：焊丝/送丝/TCP/枪姿态/船型焊/层道/管道
│   ├── answer_cache.py          #   结果缓存：LRU+TTL+相似度命中
│   ├── stop_words.json          #   外部停用词表（可调优）
│
├── server.py                    # 【Web服务入口】FastAPI后端（process_query 供 Web/CLI 共用）
├── start.py                     # 【启动入口】一键启动 + 自动学习uploads/PDF
├── requirements.txt             # 【依赖】Python包清单
├── README.md                    # 【本文档】
│
├── tools/                       # 【工具脚本】
│   ├── build_expert_kb.py       #   重建专家知识库 + 向量库（可重复执行）
│   ├── inspect_knowledge.py     #   知识库自检（不调LLM）
│   ├── relearn_book.py          #   重新学习指定手册
│   ├── reocr_handbook.py        #   GPU 重跑手册 OCR
│   ├── relearn_tables.py        #   表格重建（定向OCR候选页→结构化表格）
│   ├── gpu_ingest.py            #   GPU 新书入库（OCR→表格重建→学习→索引，一条命令）
│   ├── run_welding_qa.py        #   命令行问答（参数问题输出工艺卡片）
│   ├── gen_report_ppt.py        #   汇报PPT生成脚本（真表格/图表/架构图）
│   └── tests.py                 #   测试用例（含 jieba 分词测试）
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
① 结果缓存 (answer_cache.py)  ──命中──→  直接返回
    │ 未命中
    ▼
② 本地分析 (welding_qa_system.py)
   关键词/类别 + 专家库概念 + 向量检索
    │
    ▼
③ 意图路由 (qa_router.py)   [内部思考·不展示]
    ├─ 基本概念 → 概念解析 + 应用拓展 + 工艺类型 + 来源
    └─ 工艺参数 → 工艺匹配 (内部) → 工艺卡片 process_card.py
                     │
                     ▼
④ 本地匹配优先 (置信度≥0.78 或 卡片就绪)
    ├─ 工艺卡片：机器可读 JSON（母材/板厚/工艺/电参数/机器人参数/质量评估/装备）
    ├─ 概念解析：专家知识库条目 + PDF 来源
    └─ 直接返回，跳过 LLM
    │ 置信度不足
    ▼
⑤ LLM 兜底 (llm_service.py chat_intent)
   意图感知 + 瘦身上下文，max_tokens 2000
    │
    ▼
⑥ 结果缓存 (put) → 返回

扫描版PDF → PaddleOCR(GPU) 自动识别 → learn_book → 向量库/专家库增量更新
```

---

## 🏷️ 工艺卡片（面向仿真 / 机器人路径规划 / 操控）

参数问题（材料+板厚+工艺）会输出**机器可读的工艺卡片 JSON**，供智能引擎、仿真、机器人路径规划与操控直接使用，**减少示教时间**。

### 卡片结构示例（默认机器人工艺 GMAW/MIG）
```json
{
  "base_material": "低碳钢", "thickness_mm": 12, "process": "GMAW/MIG (熔化极氩弧焊)",
  "groove": "单V坡口 60°±5°（钝边1-2mm）", "joint_gap_mm": "2-3mm",
  "electrical": {"current_a": "90-140A", "voltage_v": "20-36V", "travel_speed_cm_min": "5-40 cm/min"},
  "thermal": {"preheat": "板厚<30mm: 不需预热", "interpass_temp": "≤250°C", "postheat": "一般不需要"},
  "robot": {
    "weld_mode": "MAG 富氩混合气",
    "wire": {"type": "ER50-6", "diameter": "1.2mm"},
    "gas": {"type": "80%Ar+20%CO₂", "flow": "15-20 L/min"},
    "tcp": {"contact_tip_to_work": "12-15mm", "stick_out": "15-18mm", "approach": "TCP指向坡口中心"},
    "gun_pose": {"work_angle": "75-85°", "travel_angle": "10-15°推枪", "axis_rotation": "0°"},
    "ship_position": {"position": "PA/1G 船型焊", "angle": "坡口旋转至45°朝上"},
    "pass_sequence": [
      {"pass": "打底", "current_a": "120-140A", "voltage_v": "18-20V", "speed_cm_min": "25-35", "wire_feed": "6-8 m/min", "bead_width": "4-5mm"},
      {"pass": "填充", "current_a": "160-190A", "voltage_v": "21-24V", "speed_cm_min": "28-35", "wire_feed": "7-9 m/min", "bead_width": "6-7mm"},
      {"pass": "盖面", "current_a": "180-210A", "voltage_v": "23-25V", "speed_cm_min": "25-30", "wire_feed": "8-10 m/min", "bead_width": "7-9mm"}
    ],
    "pipe": {"pipe_mode": "管道固定焊", "strategy": "6点→12点半圈分段", "6G_note": "每半圈含斜仰/斜立/斜平"}
  },
  "quality": {"checks": [{"defect": "气孔", "cause": "保护气不足", "prevention": "控制气体流量15-20L/min"}], "inspection": "..."},
  "equipment": {"robot_model": "MR2010_1", "torch_model": "APW50N", "welder_model": "NBC-500RP", "work_area_m2": 6},
  "application": "..."
}
```

### 关键特性
- **机器人焊接字段**（`robot` 块）：焊丝选型（按母材 ER50-6/ER308L）、保护气配比、送丝速度、TCP/导电嘴、枪姿态分解（工作角/行走角/绕枪轴）、船型焊位置、层道电流电压序列、管道 6G 半圈分段
- **默认工艺 GMAW/MIG**：面向机器人焊丝工艺（非手工焊条），管道/圆弧场景自动识别
- **确定性输出**：电参数/热参数取自基座三表（工艺/材料/焊条参数表）+ `robot_welding.py` 规则库，不依赖 LLM 随机性 —— 适合机器人控制
- **装备信息**：机器人型号、焊枪、焊机、作业幅宽在 `app/config.yaml` 的 `equipment` 段配置
- **推理隐藏**：意图解析、工艺匹配等思考过程不展示，前端只显示工艺卡片
- **JSON 导出 / 打印PDF**：Web 端卡片带「复制JSON」「打印/导出PDF」按钮；命令行 `tools/run_welding_qa.py` 直接打印卡片

### 机器人参数（可后续按实机标定）
| 工艺 | 焊枪倾角 | 干伸长 |
|---|---|---|
| SMAW 焊条电弧焊 | 70-80°（后倾5-10°） | 焊条干伸长 15-20mm |
| GTAW/TIG 钨极氩弧焊 | 75-85° | 钨极伸出 3-5mm |
| GMAW/MIG 熔化极氩弧焊 | 80-90° | 导电嘴到工件 10-15mm |
| FCAW 药芯焊丝 | 80-90° | 导电嘴到工件 15-25mm |
| SAW 埋弧自动焊 | 75-85° | 焊丝伸出 25-40mm |

---

## 快速开始

### 1. 环境（推荐 GPU 环境，扫描 OCR 用 GPU）
```bash
# 本机已内置 .venv-gpu（Python 3.12 + paddlepaddle-gpu CUDA 11.8 + 全部应用依赖）
.venv-gpu\Scripts\python.exe start.py
```
> 若在 PyCharm：Project Interpreter 设为 `E:\PyCharmMisProject\.venv-gpu\Scripts\python.exe`

### 2. 配置 LLM（可选）
编辑 `app/config.yaml`，填入 API Key：
```yaml
llm:
  api_base: "https://api.deepseek.com/v1"
  api_key: "sk-your-key"
  model: "deepseek-chat"
```
不配置也可运行（本地知识库 + 工艺卡片模式，不消耗token）。

### 3. 导入工艺手册
将焊接工艺 PDF 放入 `uploads/` 文件夹，或在网页端直接上传。

### 4. 启动
```bash
.venv-gpu\Scripts\python.exe start.py
# → http://localhost:8000
```
- 文字版 PDF：直接提取学习
- **扫描版 PDF**：自动 GPU 识别（前端上传显示进度，约1.5秒/页）
- 启动时自动学习 `uploads/` 中所有新 PDF

### 5. 命令行工艺卡片（与 Web 共用引擎）
```bash
.venv-gpu\Scripts\python.exe tools/run_welding_qa.py "Q345钢板12mm预热温度"
.venv-gpu\Scripts\python.exe tools/run_welding_qa.py "304不锈钢 3mm TIG焊"
```

---

## 知识库自检（不耗token）

```bash
# 查看所有已学习书籍
.venv-gpu\Scripts\python.exe tools/inspect_knowledge.py

# 测试关键词匹配效果
.venv-gpu\Scripts\python.exe tools/inspect_knowledge.py "弧焊机器人焊接参数"

# 查看完整目录
.venv-gpu\Scripts\python.exe tools/inspect_knowledge.py --chapters

# 浏览器在线查看
http://localhost:8000/api/knowledge/inspect?q=焊接参数
```

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/query` | 问答（概念/工艺卡片/LLM兜底，返回 `process_card`） |
| `POST` | `/api/upload-pdf` | 上传单个PDF（扫描版自动GPU识别，返回 `job_id`） |
| `POST` | `/api/upload-pdfs` | 批量上传多个PDF |
| `GET` | `/api/upload-status/{job_id}` | GPU 识别任务进度（含页进度） |
| `GET` | `/api/categories` | 知识目录（原书+已学习PDF） |
| `GET` | `/api/knowledge/inspect?q=` | 知识库自检（不调LLM） |
| `GET` | `/api/config/status` | LLM配置状态 |
| `GET` | `/api/health` | 健康检查 |

---

## 如何添加新工艺手册

### 方式一：前端上传（推荐，最简）
```
1. 网页上传 PDF（扫描版自动 GPU 识别，显示页进度）
2. 完成后自动入库 + 索引更新，直接提问即可检索
```
- 文字版：秒级完成
- 扫描版：GPU 识别（约1.5秒/页），超 50MB 的大书走方式二

### 方式二：命令行 GPU 入库（大扫描书）
```bash
# 1. 把 PDF 放入 uploads/ 目录
# 2. GPU 一条命令：OCR → 学习 → 索引
.venv-gpu\Scripts\python.exe tools/gpu_ingest.py 新书.pdf
```

### 验证
```bash
.venv-gpu\Scripts\python.exe tools/inspect_knowledge.py
.venv-gpu\Scripts\python.exe tools/run_welding_qa.py "新书的工艺参数问题"
```

---

## 演进路线图

### 当前 (v2.6) — 完成
- [x] **机器人焊接能力**：默认工艺 GMAW/MIG、机器人规则库（焊丝/送丝/TCP/枪姿态/船型焊/层道/管道6G）
- [x] **专家库概念定义全量覆盖**：123 概念 92% 手工定义，0 无定义 / 0 应用缺失 / 0 跑题摘要
- [x] **概念解析质量**：干净文字优先（不抓 OCR 原文）、滑动窗口主题提取、乱码/[Page]/页眉/图注过滤、来源排序
- [x] **健壮性修复**：空答案不缓存、参考来源截断加固
- [x] 词库扩充（1317 关键词 / 123 同义词组）+ 六阶段关键词提取
- [x] jieba 智能分词（焊接术语高频注册 + 搜索模式多粒度）
- [x] 表格重建 + 并入章节正文（扫描版定向 OCR 恢复结构化数据）
- [x] 专家知识库（123 概念）+ 本地向量库（numpy 余弦 + jieba）
- [x] 意图路由：概念 ↔ 工艺参数差异化回答
- [x] **工艺卡片**：机器可读 JSON + 机器人字段 + 装备信息（仿真/机器人用）
- [x] 本地匹配优先 + 结果缓存（LRU+TTL+相似度）
- [x] GPU OCR（PaddleOCR + cv2 预处理 + 纠错字典）+ 前端 GPU 上传
- [x] 命令行工艺卡片（run_welding_qa.py 与 Web 共用引擎）
- [x] 多书增量入库（gpu_ingest.py 一条命令）+ 表格自动重建

### 下一步 (v2.7)
- [ ] **工艺卡片标定**：按实际机器人（MR2010_1）标定焊枪角/干伸长/摆动
- [ ] **焊道级路径**：由板厚/坡口自动生成机器人焊道序列（打底/填充/盖面）
- [ ] **工艺卡片引用表格数值**：从手册参数表直接取值填充卡片
- [ ] **工况输入**：支持工件类型/坡口角度/焊接位置 细化卡片
- [ ] **工艺卡片回归测试**：标准参数问答对 + 准确率评估

### 远期 (v3.0) — 专属焊接大模型
- [ ] **领域微调**：用积累的问答对微调开源模型（Qwen/Llama）
- [ ] **知识蒸馏**：将多本书籍知识压缩为向量数据库
- [ ] **工艺推荐引擎**：根据材料-板厚-工况自动推荐最优工艺参数
- [ ] **测试用例体系**：标准问答对 + 准确率评估 + 回归测试

---

## 提高扫描解析率的方案

| 方案 | 效果 | 实施难度 |
|------|------|---------|
| PaddleOCR (GPU, CUDA 11.8) | 中文识别率~95%，1.5秒/页 | ★☆☆ 已实现 |
| cv2 预处理（去噪/CLAHE/纠偏/放大） | 大幅提升弱对比扫描件 | ★☆☆ 已实现 |
| OCR 纠错字典（氩弧焊/钨极/CO₂ 等46条） | 修正识别错误 | ★☆☆ 已实现 |
| Tesseract OCR + 中文包 | 备用引擎 | ★☆☆ 已实现 |
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
| numpy | 本地向量库 |
| paddlepaddle-gpu (CUDA 11.8) | GPU 推理（`.venv-gpu` 环境） |
| paddleocr | 扫描件中文 OCR |
| opencv-python + Pillow | OCR 预处理 / 图片 |
| pytesseract | 备用 OCR（可选） |

> 推荐使用项目内置的 `.venv-gpu` 环境（已全部装好，含 GPU paddle）。

---

## 常见问题

**Q: 端口冲突 (10013/10048)？**  
A: `start.py` 会自动释放端口。不要手动运行 `server.py`。

**Q: 上传PDF后显示0章0关键词？**  
A: PDF是扫描版（图片）时，用 GPU 环境（`.venv-gpu`）上传会自动识别；文字版PDF直接提取。

**Q: 扫描版PDF上传很慢？**  
A: 确认服务跑在 `.venv-gpu`（GPU 1.5秒/页）。若在 CPU 环境，扫描版走 GPU 子进程；超大书（>50MB）用 `tools/gpu_ingest.py`。

**Q: 如何不花token检查知识库？**  
A: `python tools/inspect_knowledge.py` 或浏览器访问 `/api/knowledge/inspect`

**Q: 工艺卡片里的机器人参数是哪里来的？**  
A: 规则经验值（`app/process_card.py`），可按实际机器人标定；装备型号在 `app/config.yaml` 的 `equipment` 段。

**Q: 如何批量导入多本书？**  
A: 前端批量上传，或把所有PDF放入 `uploads/` → 运行 `.venv-gpu\Scripts\python.exe tools/gpu_ingest.py 每本.pdf`。

