#!/usr/bin/env python3
"""获取角色卡 JSON 中特定世界书条目的完整内容。

Usage:
    python3 get_entry.py card.json 0           # 查看第 0 条（按数组下标）
    python3 get_entry.py card.json 0,3,5       # 查看第 0, 3, 5 条
    python3 get_entry.py card.json 0-5         # 查看第 0 到 5 条
    python3 get_entry.py card.json 0,3-5,8     # 混合
    python3 get_entry.py card.json --id 433    # 按条目 id/uid 查找（支持逗号/区间，与 runtime.yaml 对齐）
"""

import io
import json
import sys

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def _entry_id_of(entry: dict) -> int | None:
    for k in ("id", "uid"):
        v = entry.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    return None


def parse_raw_ints(raw: str) -> list[int]:
    nums = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            nums.extend(range(int(start), int(end) + 1))
        else:
            nums.append(int(part))
    return nums


def parse_indices(raw: str, total: int) -> list[int]:
    return [i for i in parse_raw_ints(raw) if 0 <= i < total]


def show_entry(entry: dict):
    for key, value in entry.items():
        if key == "content":
            content = str(value) if value else "(空)"
            print(f"  {key} ({len(content)} 字符):")
            print(f"  {'─' * 40}")
            print(content)
            print(f"  {'─' * 40}")
        elif key == "extensions":
            if value:
                print(f"  {key}: {json.dumps(value, ensure_ascii=False, indent=4)}")
            else:
                print(f"  {key}: (空)")
        else:
            print(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
    print()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    card_path = sys.argv[1]
    by_id = False
    if sys.argv[2] == "--id":
        if len(sys.argv) < 4:
            print("用法: python3 get_entry.py card.json --id <id_spec>")
            sys.exit(1)
        by_id = True
        index_spec = sys.argv[3]
    else:
        index_spec = sys.argv[2]

    with open(card_path, "r", encoding="utf-8") as f:
        card = json.load(f)

    data = card.get("data", card)
    entries = data.get("character_book", {}).get("entries", [])
    total = len(entries)

    if by_id:
        want_ids = set(parse_raw_ints(index_spec))
        indices = [i for i, e in enumerate(entries) if _entry_id_of(e) in want_ids]
        if not indices:
            print(f"未找到指定 id ({index_spec})，共 {total} 条")
            sys.exit(1)
    else:
        indices = parse_indices(index_spec, total)
        if not indices:
            print(f"无效索引，共 {total} 条 (0-{total - 1})")
            sys.exit(1)

    print(f"共 {total} 条，显示 {len(indices)} 条\n")
    for i in indices:
        eid = _entry_id_of(entries[i])
        id_tag = f" | id={eid}" if eid is not None else ""
        print(f"=== [{i}{id_tag}] {entries[i].get('comment', '(无注释)')} ===")
        show_entry(entries[i])


if __name__ == "__main__":
    main()
