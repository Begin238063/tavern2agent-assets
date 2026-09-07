#!/usr/bin/env python3
"""列出角色卡 JSON 中所有世界书条目的概览。

Usage:
    python3 list_entries.py card.json                  # 列出所有条目
    python3 list_entries.py card.json --filter mvu     # 只看 MVU 条目
    python3 list_entries.py card.json --filter mvu_update  # 只看 [mvu_update]
    python3 list_entries.py card.json --filter initvar     # 只看 [initvar]
    python3 list_entries.py card.json --search 战斗      # 搜索含关键词的条目
    python3 list_entries.py card.json --stats            # 一键统计（类型×标记×角色条目数）
"""

import json
import re
import sys


def list_entries(card: dict, filter_tag: str | None = None, search: str | None = None):
    data = card.get("data", card)
    entries = data.get("character_book", {}).get("entries", [])
    print(f"共 {len(entries)} 条世界书条目\n")

    shown = 0
    for i, entry in enumerate(entries):
        comment = entry.get("comment", "") or "(无注释)"
        keys = entry.get("keys", []) or []
        content = entry.get("content", "") or ""
        content_preview = content[:80].replace("\n", " ").replace("\r", "") if content else "(空)"

        # 提取标签
        comment_lower = comment.lower()

        # 过滤
        if filter_tag:
            if filter_tag in ("mvu", "mvu_plot", "mvu_update", "initvar"):
                if filter_tag == "mvu":
                    if "[mvu" not in comment_lower:
                        continue
                else:
                    if f"[{filter_tag}]" not in comment_lower:
                        continue

        if search:
            combined = comment + " ".join(keys) + content[:200]
            if search.lower() not in combined.lower():
                continue

        # 标记类型
        tags = []
        if "[mvu_plot]" in comment_lower:
            tags.append("📖plot")
        elif "[mvu_update]" in comment_lower:
            tags.append("📝update")
        if "[initvar]" in comment_lower:
            tags.append("🔰initvar")
        if "[mvu" in comment_lower:
            pass  # already tagged
        if entry.get("constant"):
            tags.append("🔒常驻")
        if entry.get("selective"):
            tags.append("⚡选择性")
        if not entry.get("enabled", True):
            tags.append("❌禁用")

        tag_str = " ".join(tags) + " " if tags else ""

        print(f"[{i:3d}] {tag_str}{comment}")
        if keys:
            print(f"      触发词: {', '.join(keys[:6])}")
        print(f"      内容: {content_preview}")
        print()
        shown += 1

    print(f"\n显示 {shown} / {len(entries)} 条")


def stats_entries(card: dict):
    """--stats：类型×标记×角色条目数一键统计（大型世界书审计用）。"""
    data = card.get("data", card)
    entries = data.get("character_book", {}).get("entries", [])
    N = len(entries)

    def _mark(e, tag):
        return tag in (e.get("comment") or "").lower()

    constant = [e for e in entries if e.get("constant")]
    selective = [e for e in entries if e.get("selective")]
    enabled = [e for e in entries if e.get("enabled", True)]
    disabled = [e for e in entries if not e.get("enabled", True)]
    cc = [e for e in constant if e.get("enabled", True)]
    cd = [e for e in constant if not e.get("enabled", True)]
    sc = [e for e in selective if e.get("enabled", True)]
    sd = [e for e in selective if not e.get("enabled", True)]

    mvu = [e for e in entries if "[mvu" in (e.get("comment") or "").lower()]
    mvu_update = [e for e in entries if _mark(e, "[mvu_update]")]
    mvu_plot = [e for e in entries if _mark(e, "[mvu_plot]")]
    initvar = [e for e in entries if _mark(e, "[initvar]")]

    # 角色定义类条目（角色档案 / <Character_X>）
    chardef = [e for e in entries
               if "<Character_" in ((e.get("content") or "") + (e.get("comment") or ""))
               or "角色档案" in ((e.get("content") or "") + (e.get("comment") or ""))]
    char_tags = set()
    for e in chardef:
        for m in re.finditer(r"<Character_([A-Za-z0-9_]+)>", e.get("content") or ""):
            char_tags.add(m.group(1))
    pro = [e for e in entries if "[Pro]" in (e.get("comment") or "")]
    lite = [e for e in entries if "[Lite]" in (e.get("comment") or "")]

    # ST 运行时模板补丁（MVU <%_ ... _%> 等）
    patched = [e for e in entries if "<%_" in (e.get("content") or "")
               or "{{setvar" in (e.get("content") or "")
               or "<%=" in (e.get("content") or "")]

    print(f"总条目: {N}")
    print(f"  constant(常驻): {len(constant)}  (启用 {len(cc)} / 禁用 {len(cd)})")
    print(f"  selective(选择性): {len(selective)}  (启用 {len(sc)} / 禁用 {len(sd)})")
    print(f"  禁用(enabled=false): {len(disabled)} / 启用: {len(enabled)}")
    print(f"标记: [mvu]任意 {len(mvu)} | [mvu_update] {len(mvu_update)} | [mvu_plot] {len(mvu_plot)} | [initvar] {len(initvar)}")
    print(f"角色定义/人设类条目: {len(chardef)}  (唯一 <Character_X> 标签 {len(char_tags)} 个; [Pro] {len(pro)} / [Lite] {len(lite)})")
    print(f"含 ST 模板补丁(<%_/{{{{var}}/<%==)的条目: {len(patched)} / {N}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    card_path = sys.argv[1]

    with open(card_path, "r", encoding="utf-8") as f:
        card = json.load(f)

    filter_tag = None
    search = None
    do_stats = False

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--filter" and i + 1 < len(args):
            filter_tag = args[i + 1]
            i += 2
        elif args[i] == "--search" and i + 1 < len(args):
            search = args[i + 1]
            i += 2
        elif args[i] == "--stats":
            do_stats = True
            i += 1
        else:
            print(f"未知参数: {args[i]}")
            sys.exit(1)

    if do_stats:
        stats_entries(card)
        return

    list_entries(card, filter_tag, search)


if __name__ == "__main__":
    main()
