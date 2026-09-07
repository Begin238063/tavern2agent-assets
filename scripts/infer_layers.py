#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""infer_layers.py —— 批量推断并填充 lore/*.yaml 的空 layer 字段。

用法：
    python3 scripts/infer_layers.py characters/<角色名>/

设计：
    - 只填充空 layer（或值为空字符串的 layer），已有非空值保持不变
    - 推断规则与 build_assets.py 的 _infer_layer 函数一致
    - 幂等：可重复运行，不会覆盖已有的人工分层
"""

import re
import sys
import io
from pathlib import Path

# 强制 UTF-8 输出
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def _infer_layer(comment: str, filename: str, keys: list, constant: bool) -> str:
    """推断 layer 默认值。

    layer 是路由载体：
    - behavior: roleplay 逻辑、格式约束、变量更新规则
    - identity: 角色核心设定、世界观总纲、初始化变量
    - narrative: 剧情大纲、角色/地点/物品百科（默认）
    """
    comment_lower = comment.lower()
    filename_lower = filename.lower()

    # behavior: roleplay 指令、MVU 变量系统
    behavior_patterns = [
        "角色扮演", "roleplay", "注意事项", "系统指令",
        "mvu_update", "mvu_plot", "变量更新", "变量格式", "变量禁词",
        "变量输出", "变量防呆", "行动选项", "cg插图", "守岸人"
    ]
    for pattern in behavior_patterns:
        if pattern in comment_lower or pattern in filename_lower:
            return "behavior"

    # identity: 角色核心、世界观、初始化
    identity_patterns = [
        "世界观总纲", "主角自设", "用户名变量", "角色核心",
        "initvar", "opening", "勿关", "勿开", "默认变量",
        "i.r.i.s", "iris"
    ]
    # 特殊：角色同名条目（如"清宵.yaml"）通常是核心档案
    if constant and len(keys) == 1:
        only_key = keys[0] if keys else ""
        if only_key and filename_lower.startswith(only_key.lower()):
            return "identity"

    for pattern in identity_patterns:
        if pattern in comment_lower or pattern in filename_lower:
            return "identity"

    # 默认：narrative（剧情、百科）
    return "narrative"


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/infer_layers.py <asset_dir>")
        print("示例: python3 scripts/infer_layers.py characters/MyCharacter/")
        return 1

    asset_dir = Path(sys.argv[1])
    lore_dir = asset_dir / "lore"

    if not lore_dir.is_dir():
        print(f"✗ lore 目录不存在：{lore_dir}")
        return 1

    stats = {"behavior": 0, "identity": 0, "narrative": 0, "skipped": 0}

    for yaml_file in sorted(lore_dir.glob("*.yaml")):
        with open(yaml_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 查找 layer 行
        layer_idx = None
        current_layer = None
        for i, line in enumerate(lines):
            if re.match(r'^layer:\s*', line):
                layer_idx = i
                current_layer = line.split(':', 1)[1].strip()
                break

        # 跳过已有非空 layer
        if current_layer:
            stats["skipped"] += 1
            continue

        # 提取必要信息用于推断
        comment = ""
        keys = []
        constant = False

        for line in lines:
            if 'comment:' in line and '_derived:' not in lines[max(0, lines.index(line)-5):lines.index(line)]:
                match = re.search(r'comment:\s*"([^"]*)"', line)
                if match:
                    comment = match.group(1)
            if re.match(r'^  constant:\s*true', line):
                constant = True
            if line.strip().startswith('- "') and 'keys:' in '\n'.join(lines[:lines.index(line)]):
                key_match = re.search(r'- "([^"]*)"', line)
                if key_match:
                    keys.append(key_match.group(1))

        # 推断 layer
        inferred = _infer_layer(comment, yaml_file.stem, keys, constant)

        # 替换空 layer 行
        if layer_idx is not None:
            lines[layer_idx] = f'layer: {inferred}\n'

            with open(yaml_file, 'w', encoding='utf-8') as f:
                f.writelines(lines)

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
