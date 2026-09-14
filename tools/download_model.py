# -*- coding: utf-8 -*-
"""下载语义向量模型（bge-small-zh）到 models/

语义向量检索依赖此模型（约 184MB）。新环境部署时运行一次：
  python tools/download_model.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TARGET_DIR = PROJECT_ROOT / "models"
TARGET = TARGET_DIR / "models" / "AI-ModelScope--bge-small-zh-v1.5"


def main():
    # 已存在则跳过
    if (TARGET / "snapshots" / "master" / "model.safetensors").exists():
        print(f"✅ 模型已存在: {TARGET}")
        return
    print("⏳ 通过 modelscope 下载 bge-small-zh-v1.5（约184MB）...")
    from modelscope import snapshot_download
    model_dir = snapshot_download(
        "AI-ModelScope/bge-small-zh-v1.5",
        cache_dir=str(TARGET_DIR),
    )
    print(f"✅ 模型已下载: {model_dir}")
    print("   向量检索将自动使用语义向量。")


if __name__ == "__main__":
    main()
