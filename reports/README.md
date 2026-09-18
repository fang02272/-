# 验收报告说明

本目录保存最近两周计划对应的可复核产物。报告均由项目内脚本生成，数据来自当前 `saved_knowledge`，不会修改原始书籍文件。

## 报告

- `fixed_questions_evaluation.json`：固定 50 题的 Top-5 检索证据词命中、引用初筛、缺失输入覆盖率，以及首次/缓存/缓存失效后的耗时与调用统计。
- `knowledge_quality_audit.json`：抽样章节的数据质量审计，包含标题疑似 OCR 乱码、缺少页码提示等问题。
- `demo_scenarios.md`：三个可直接演示的查询场景和预期观察点。

## 重新生成

```powershell
.\.venv\Scripts\python.exe tools\evaluate_fixed_questions.py --output reports\fixed_questions_evaluation.json
.\.venv\Scripts\python.exe tools\audit_knowledge_quality.py --output reports\knowledge_quality_audit.json
```

固定题集报告是离线自动初筛：证据词命中不等于人工确认了原文语义，引用仍需对照原 PDF 和页码。当前报告中的检索未命中项会明确列出，不能把自动初筛结果当成生产验收结论。
