# -*- coding: utf-8 -*-
"""重新学习《实用焊接工艺手册》第二版全书
- 读取 OCR 全文 -> learn_book 入库（自动替换旧人工摘要数据，保留人工标注）
- 验证: 章节拆分 / 关键词(含E4303等型号) / 焊接参数提取
"""
import sys
import io
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from app.knowledge_store import KnowledgeStore

OCR_TXT = PROJECT_ROOT / "uploads/实用焊接工艺手册_第二版_full_ocr.txt"
RESULT = PROJECT_ROOT / "relearn_result.txt"
out = io.StringIO()

full_text = Path(OCR_TXT).read_text(encoding="utf-8")
out.write(f"OCR全文: {len(full_text)} 字符, {full_text.count('[Page ')} 页\n")

ks = KnowledgeStore()
source_id = ks.learn_book(
    filename="实用焊接工艺手册_第二版.pdf",
    full_text=full_text,
    tables=[],
    page_count=full_text.count("[Page "),
    images=[],
)
out.write(f"学习完成 source_id: {source_id}\n\n")

# 验证 1: 章节拆分
src_dir = ks.store_dir / source_id
chapters = json.loads((src_dir / "chapters.json").read_text(encoding="utf-8"))
out.write(f"章节数: {len(chapters)}\n")
for c in chapters:
    out.write(f"  - {c['title']} | 长度 {c['content_length']} | 关键词 {len(c.get('keywords', []))} 个\n")

# 验证 2: 关键词覆盖（关键焊材型号/章节术语）
keywords = json.loads((src_dir / "keywords.json").read_text(encoding="utf-8"))
out.write(f"\n全书关键词总数: {len(keywords)}\n")
checks = ["E4303", "E5015", "J422", "Q345", "埋弧焊", "氩弧焊", "CO2", "预热",
          "焊条直径", "焊接电流", "铸铁", "不锈钢", "铝合金", "钎焊", "堆焊"]
hit = [k for k in checks if k in keywords]
miss = [k for k in checks if k not in keywords]
out.write(f"命中 {len(hit)}/{len(checks)}: {hit}\n")
if miss:
    out.write(f"未命中: {miss}\n")

# 验证 3: 焊接参数提取
data_points = json.loads((src_dir / "data_points.json").read_text(encoding="utf-8"))
if isinstance(data_points, dict):
    out.write(f"\n焊接参数条目: {len(data_points)}\n")
    for k in list(data_points.keys())[:15]:
        v = data_points[k]
        if isinstance(v, list) and v:
            out.write(f"  {k}: {str(v[0])[:80]}\n")
        else:
            out.write(f"  {k}: {str(v)[:80]}\n")
elif isinstance(data_points, list):
    out.write(f"\n焊接参数条目: {len(data_points)}\n")
    for d in data_points[:15]:
        out.write(f"  {str(d)[:100]}\n")

# 验证 4: 保留的人工标注文件
for name in ["structure.json", "electrode_params.json", "process_params.json"]:
    p = src_dir / name
    out.write(f"\n{name}: {'保留 ✓' if p.exists() else '不存在'}")
    if p.exists():
        out.write(f" ({p.stat().st_size} 字节)")

with open(RESULT, "w", encoding="utf-8") as f:
    f.write(out.getvalue())
print("DONE")
