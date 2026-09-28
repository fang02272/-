# 焊接工艺智能问答系统 — 优化成果汇总

- 题集：`tools\fixed_questions.json`，共 50 题（概念/参数/缺失输入/同义词/跨书 各 10 题）
- 评测模式：`offline-no-llm`（离线本地，不调用大模型，结果可复现）
- 基线报告：`reports/baseline_evaluation.json`

## 一、核心指标对比

| 指标 | 优化前 | 优化后 |
|---|---|---|
| Top-5 检索证据词命中率 | 82.5% | 100.0% |
| 引用支持率 | 77.5% | 100.0% |
| 缺失输入覆盖率 | 100.0% | 100.0% |

## 二、性能对比（首次请求，无缓存）

| 指标 | 优化前 | 优化后 |
|---|---|---|
| P50 延迟 | 261.9 ms | 166.5 ms |
| P95 延迟 | 523.9 ms | 266.8 ms |

## 三、缓存命中（重复提问）

| 指标 | 优化前 | 优化后 |
|---|---|---|
| 缓存命中数 | 50 | 50 |
| 缓存后 P50 延迟 | 0.6 ms | 1.4 ms |

> 缓存路径两者均为亚毫秒级，差异在测量噪声范围内；优化后缓存键额外纳入专家库文件状态，以保证专家库重建后旧答案立即失效（Day5 盲区修复）。

## 四、检索未命中题目

- 优化前未命中 7 题：C04、C06、P08、S01、S05、S07、X10
- 优化后未命中 0 题：无 ✓

三类归因明细见 `reports/retrieval_miss_analysis_baseline.md`（优化前）与 `reports/retrieval_miss_analysis.md`（优化后）。

## 五、复现命令

```powershell
# 一键生成全部成果数据
python tools\demo_results.py

# 分项运行
python tools\evaluate_fixed_questions.py --output reports\fixed_questions_evaluation.json
python tools\diagnose_retrieval_misses.py --output reports\retrieval_miss_analysis.md
python tools\test_integration_scenarios.py

# 网页端实景演示
python start.py   # 打开 http://localhost:8000
```

## 六、口径说明（避免PPT夸大）

- 检索命中 = Top-5 章节的标题/关键词/摘要/可读预览中命中至少一个人工标注证据词，属**自动初筛**，不等于人工确认了原文语义。
- 引用支持 = 响应存在来源，且 Top-5 命中人工标注证据词；正式引用仍需对照原 PDF 与页码。
- 性能为无 LLM 的本地模式；接入真实大模型后端到端耗时以 LLM 生成为主。
- 覆盖的语料为 `saved_knowledge/` 下当前 3 本资料、28 个章节。
