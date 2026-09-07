#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mark_disabled.py —— 批量标记 disabled 条目的处置说明。

用法：
    python3 scripts/mark_disabled.py <asset_dir> [--note "处置说明"]

示例：
    python3 scripts/mark_disabled.py characters/MyCharacter/
    python3 scripts/mark_disabled.py characters/MyCharacter/ --note "原卡作者已禁用，保留备查"

设计：
    - 只修改 disabled=true 且 note 为空或为机器默认值的条目
    - 已有人工 note 的条目保持不变
    - 默认 note: "原卡作者已禁用，保留备查"
"""

import argparse
import re
import sys
import io
from pathlib import Path

# 强制 UTF-8 输出
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def is_machine_default_note(note: str) -> bool:
    """判断 note 是否为机器默认值（需要替换）。"""
    note = note.strip().strip('"').strip("'")

    # 空字符串
    if not note:
        return True

    # 包含机器提示文本
    machine_hints = [
        "disabled（原卡禁用",
        "请在此记录去向",
        "勿默认丢弃"
    ]

    for hint in machine_hints:
        if hint in note:
            return True

    return False


def main():
    ap = argparse.ArgumentParser(description="批量标记 disabled 条目的处置说明")
    ap.add_argument("asset_dir", help="资产目录（含 lore/）")
    ap.add_argument("--note", default="原卡作者已禁用，保留备查",
                    help="处置说明（默认：原卡作者已禁用，保留备查）")
    args = ap.parse_args()

    asset_dir = Path(args.asset_dir)
    lore_dir = asset_dir / "lore"

    if not lore_dir.is_dir():
        print(f"✗ lore 目录不存在：{lore_dir}")
        return 1

    processed = 0
    skipped = 0

    for yaml_file in sorted(lore_dir.glob("*.yaml")):
        with open(yaml_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 检查是否 disabled
        is_disabled = False
        note_idx = None
        current_note = ""

        for i, line in enumerate(lines):
            if re.match(r'^\s+disabled:\s*true', line):
                is_disabled = True
            if re.match(r'^note:', line):
                note_idx = i
                current_note = line.split(':', 1)[1].strip()

        # 跳过非 disabled 或已有人工 note 的条目
        if not is_disabled:
            continue

        if note_idx is None:
            continue

        if not is_machine_default_note(current_note):
            skipped += 1
            continue

        # 替换 note
        lines[note_idx] = f'note: "{args.note}"\n'

        with open(yaml_file, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        processed += 1

    print(f"✓ 已标记 {processed} 个 disabled 条目")
    print(f"  - 跳过（已有人工 note）: {skipped}")
    print(f"  - 使用的 note: \"{args.note}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
