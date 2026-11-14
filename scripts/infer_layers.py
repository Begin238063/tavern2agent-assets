#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""infer_layers.py —— 批量推断并填充 lore/*.yaml 的空 layer 字段。

用法：
    python3 scripts/infer_layers.py characters/<角色名>/

设计：
    - 只填充空 layer（或值为空字符串的 layer），已有非空值保持不变
    - 复用 build_assets.infer_layer 与 _mini_yaml（单一权威实现，不再维护双份词表）
    - 幂等：可重复运行，不会覆盖已有的人工分层
"""

import argparse
import io
import re
import sys
from pathlib import Path

# 强制 UTF-8 输出
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import _mini_yaml
from build_assets import infer_layer


def main():
    ap = argparse.ArgumentParser(description="批量推断并填充 lore/*.yaml 的空 layer 字段")
    ap.add_argument("asset_dir", help="资产目录（含 lore/）")
    args = ap.parse_args()

    asset_dir = Path(args.asset_dir)
    lore_dir = asset_dir / "lore"

    if not lore_dir.is_dir():
        print(f"✗ lore 目录不存在：{lore_dir}")
        return 1

    char_name = ""
    persona_path = asset_dir / "persona.yaml"
    if persona_path.is_file():
        persona = _mini_yaml.parse(persona_path.read_text(encoding="utf-8"))
        ident = persona.get("identity") if isinstance(persona, dict) else None
        if isinstance(ident, dict):
            char_name = str(ident.get("name") or "")

    stats = {"behavior": 0, "identity": 0, "narrative": 0, "skipped": 0}

    for yaml_file in sorted(lore_dir.glob("*.yaml")):
        text = yaml_file.read_text(encoding="utf-8")
        parsed = _mini_yaml.parse(text)
        current_layer = str(parsed.get("layer") or "").strip()
        if current_layer:
            stats["skipped"] += 1
            continue

        entry_like = {
            "comment": _mini_yaml.get(parsed, "comment", "") or "",
            "constant": bool(_mini_yaml.get(parsed, "constant", False)),
            "keys": parsed.get("keys") or [],
        }
        inferred = infer_layer(entry_like, yaml_file.stem, char_name=char_name)

        if re.search(r"^layer:[ \t]*$", text, flags=re.M):
            new_text = re.sub(r"^layer:[ \t]*$", f"layer: {inferred}", text, count=1, flags=re.M)
        elif re.search(r'^layer:[ \t]*""[ \t]*$', text, flags=re.M):
            new_text = re.sub(r'^layer:[ \t]*""[ \t]*$', f"layer: {inferred}", text, count=1, flags=re.M)
        else:
            new_text = text.rstrip("\n") + f"\nlayer: {inferred}\n"

        yaml_file.write_text(new_text, encoding="utf-8")
        stats[inferred] += 1

    total = stats["behavior"] + stats["identity"] + stats["narrative"]
    print(f"✓ 已推断 {total} 个空 layer 字段：")
    print(f"  - behavior: {stats['behavior']}")
    print(f"  - identity: {stats['identity']}")
    print(f"  - narrative: {stats['narrative']}")
    print(f"  - 已有 layer（跳过）: {stats['skipped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
