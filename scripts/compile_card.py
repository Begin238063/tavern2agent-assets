#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compile_card.py —— 把平台无关的角色资产编译回 ST 可导入的 chara_card_v3 卡。

用途：
    1. 等价性验证：把资产编译成一张卡导进 SillyTavern 试玩（机制原生），
       压缩后的资产效果不应低于原卡；验证通过后，同一份资产再交给 AstrBot 注入插件。
    2. selective 归一化在这一层：selective=true 且 secondary_keys 为空（ST 空排除=无操作）
       → 编译时按 selective=false 输出；资产层保持原卡透传，_derived 机器字段不被改动。
    3. inject_at 语义 → ST position/depth 反映射（优先从 extensions_json 恢复原卡值）。
    4. ST 可达性检查在这一层：enabled 且 constant=false 且 keys 为空 → 错误——
       该条目在 ST 中永不激活（死条目）。keys 是 CCv2/v3 character_book.entries 的
       激活字段，只在 ST 运行时有意义，因此可达性检查属于 ST 编译器，不属于资产校验
       （资产层只承载内容与 layer 路由；validate 不再检查 keys）。

用法：
    python3 scripts/compile_card.py --assets characters/<角色名>/ --out out/card.json
    python3 scripts/compile_card.py --assets characters/<角色名>/ --out out/card.json --resolve-refs
        # 解析 persona 的 lore_refs / dialogs_refs（含 ../ 跨资产），把引用到的条目并入编译结果
        # （方案 C：persona + 世界资产的完整可玩卡）
    python3 scripts/compile_card.py --assets ... --out ... --allow-dead-entries
        # 把死条目错误降为警告并继续输出

设计约束（与 build_assets.py 一致）：
    - 纯标准库；只读资产目录，只写 --out 指定的输出文件；
    - 平台无关产物只向 ST 方向映射（编译输出允许含 ST 概念，这是 ST 目标格式）。
"""

import argparse
import json
import re
from pathlib import Path

import _mini_yaml

_ENTRY_KEYS = ("id", "keys", "secondary_keys", "comment", "content", "constant",
               "selective", "insertion_order", "enabled", "position", "use_regex",
               "extensions")


def _scalar(value, default=""):
    return default if value is None else value


def _inject_at_to_st(derived: dict, extensions: dict) -> tuple:
    """inject_at → (position, depth)。优先恢复原卡 extensions 里的 depth/position；否则按语义映射。"""
    pos = extensions.get("position")
    depth = extensions.get("depth")
    if isinstance(pos, int):
        position = "after_char" if pos == 1 else "before_char"
    elif pos in ("before_char", "after_char"):
        position = pos
    else:
        position = "before_char"
    if not isinstance(depth, int):
        inject_at = derived.get("inject_at") or "near_input"
        depth = 0 if inject_at in ("near_input", "resident") else 4
    return position, depth


def _lore_to_entry(derived: dict, manual: dict, entry_id) -> dict:
    """一条 lore/*.yaml → ST entry（selective 归一化在此层）。"""
    ext = {}
    if derived.get("extensions_json"):
        try:
            ext = json.loads(derived["extensions_json"])
            if not isinstance(ext, dict):
                ext = {}
        except Exception:
            ext = {}

    secondary = [str(k) for k in (derived.get("secondary_keys") or [])]
    selective = bool(derived.get("selective", False))
    if selective and not secondary:
        selective = False  # 归一化：ST 空排除=无操作

    position, depth = _inject_at_to_st(derived, ext)
    ext["position"] = 0 if position == "before_char" else 1
    ext["depth"] = depth
    ext["case_sensitive"] = bool(derived.get("case_sensitive", False))
    ext["scan_depth"] = derived.get("scan_depth")
    ext["prevent_recursion"] = bool(derived.get("prevent_recursion", False))

    entry = {
        "id": entry_id if entry_id is not None else 0,
        "keys": [str(k) for k in (manual.get("keys") or [])],
        "secondary_keys": secondary,
        "comment": derived.get("comment") or "",
        "content": derived.get("content") or "",
        "constant": bool(derived.get("constant", False)),
        "selective": selective,
        "insertion_order": int(derived.get("insertion_order", 100) or 100),
        "enabled": bool(derived.get("enabled", True)),
        "position": position,
        "use_regex": bool(derived.get("use_regex", False)),
        "extensions": ext,
    }
    bt = manual.get("budget_tokens")
    if bt is not None:
        entry["token_budget"] = int(bt)
    return entry


def _load_lore(lore_dir: Path) -> list:
    """读取 lore/*.yaml → [{derived, manual, id, source, entry_id}]。"""
    out = []
    for f in sorted(lore_dir.glob("*.yaml")):
        d = _mini_yaml.parse(f.read_text(encoding="utf-8"))
        der = d.get("_derived") or {}
        manual = {
            "keys": d.get("keys") or [],
            "budget_tokens": d.get("budget_tokens"),
        }
        out.append({
            "derived": der,
            "manual": manual,
            "id": d.get("id"),
            "source": d.get("source"),
            "entry_id": der.get("_entry_id"),
            "_path": f,
        })
    # 按原卡顺序（id: entry_NN）排序
    def _num(item):
        m = re.search(r"(\d+)$", str(item["id"] or ""))
        return int(m.group(1)) if m else 10 ** 9
    out.sort(key=_num)
    return out


def _resolve_ref_entries(asset_dir: Path, refs: list, seen: set, entries_by_src: dict,
                         collected: list):
    """递归解析 lore_refs（含 ../ 跨资产），按 source/_entry_id 去重后并入 collected。"""
    for ref in refs:
        p = (asset_dir / ref).resolve()
        if p.suffix != ".yaml":
            continue
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        d = _mini_yaml.parse(p.read_text(encoding="utf-8"))
        der = d.get("_derived") or {}
        src = d.get("source") or ""
        eid = der.get("_entry_id")
        if src in entries_by_src or (eid is not None and eid in {x.get("entry_id") for x in collected}):
            continue
        collected.append({
            "derived": der,
            "manual": {"keys": d.get("keys") or [], "budget_tokens": d.get("budget_tokens")},
            "id": d.get("id"),
            "source": src,
            "entry_id": eid,
            "_path": p,
        })


def _read_yaml(path: Path) -> dict:
    return _mini_yaml.parse(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _load_dialogs(asset_dir: Path, dialog_refs: list) -> dict:
    """读取 dialogs 引用 → {role, content} 列表（跨资产 refs 一并解析）。"""
    out = []
    for ref in dialog_refs:
        p = (asset_dir / ref)
        if not p.is_file():
            continue
        d = _read_yaml(p)
        out.append({"role": d.get("role") or "char",
                    "content": d.get("content") or "",
                    "_file": ref})
    return out


def compile_assets(asset_dir: Path, out_path: Path, resolve_refs: bool,
                   name: str = None, allow_dead: bool = False) -> int:
    persona = _read_yaml(asset_dir / "persona.yaml")
    if not persona:
        print(f"✗ 资产目录缺少 persona.yaml：{asset_dir}")
        return 1

    identity = persona.get("identity") or {}
    core = persona.get("core") or {}
    style = persona.get("style") or {}
    provenance = persona.get("provenance") or {}

    char_name = name or identity.get("name") or "未命名角色"
    lore_refs = persona.get("lore_refs") or []
    dialog_refs = persona.get("dialogs_refs") or []
    if style.get("sample") and style["sample"] not in dialog_refs:
        pass  # sample 只用于风格示例，不编译进卡

    # 1) 世界书条目：本资产 lore + （可选）跨资产 refs
    lore_dir = asset_dir / "lore"
    entries_meta = _load_lore(lore_dir) if lore_dir.is_dir() else []
    seen_paths = {str(m["_path"].resolve()) for m in entries_meta}
    if resolve_refs:
        entries_by_src = {m["source"]: m for m in entries_meta}
        _resolve_ref_entries(asset_dir, lore_refs, seen_paths, entries_by_src, entries_meta)

    # 2) 对话：first_mes / alternates / examples（含跨资产 refs）
    dialogs = {}
    for f in (asset_dir / "dialogs").glob("*.yaml"):
        d = _read_yaml(f)
        dialogs[f.stem] = d
    if resolve_refs:
        for ref in dialog_refs:
            stem = Path(ref).stem
            if stem not in dialogs:
                p = asset_dir / ref
                if p.is_file():
                    dialogs[stem] = _read_yaml(p)

    first_mes = (dialogs.get("first_mes") or {}).get("content") or ""
    alternates = []
    for key in sorted(dialogs.keys()):
        m = re.match(r"^alternate_(\d+)$", key)
        if m:
            alternates.append((int(m.group(1)), (dialogs[key].get("content") or "")))
    alternates = [c for _, c in sorted(alternates)]

    mes_example = ""
    example_lines = []
    for key in sorted(dialogs.keys()):
        m = re.match(r"^example_(\d+)$", key)
        if m:
            d = dialogs[key]
            role = d.get("role") or "char"
            marker = "{{user}}" if role == "user" else "{{char}}"
            example_lines.append(f"{marker}: {d.get('content') or ''}")
    if example_lines:
        mes_example = "\n".join(example_lines)

    # 3) entries 编译（selective 归一化在此）
    entries = []
    used_ids = set()
    dead = []
    for meta in entries_meta:
        eid = meta["entry_id"]
        if eid is not None and eid in used_ids:
            continue
        if eid is not None:
            used_ids.add(eid)
        entry = _lore_to_entry(meta["derived"], meta["manual"], eid)
        entries.append(entry)
        # 4) ST 可达性检查（keys 只在 ST 运行时有意义 → 归 ST 编译器）
        if entry["enabled"] and not entry["constant"] and \
                not [k for k in (entry.get("keys") or []) if str(k).strip()]:
            dead.append((meta["_path"].name, entry["id"]))

    if dead:
        msg = "编译错误：以下条目启用且非常驻但 keys 为空（在 ST 中永不激活）：\n" + \
            "\n".join(f"   · {name}（id={eid}）" for name, eid in dead)
        if allow_dead:
            print(f"⚠ {msg}\n  （--allow-dead-entries：降为警告，继续输出）")
        else:
            print(f"✗ {msg}\n  （确认为死条目可加 --allow-dead-entries 继续；keys 只在 ST 运行时才有意义）")
            return 1

    self_text = core.get("self") or ""
    personality = "；".join(str(t) for t in (core.get("traits") or []) if str(t).strip())

    card = {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {
            "name": str(char_name),
            "description": self_text,
            "personality": personality,
            "scenario": "",
            "first_mes": first_mes,
            "mes_example": mes_example,
            "creator_notes": (
                f"由 tavern2agent-assets 资产编译生成（compile_card.py）；"
                f"来源资产：{asset_dir}；原卡 sha256：{provenance.get('sha256') or '未知'}；"
                "验证用卡，勿作为最终卡分发。"
            ),
            "system_prompt": "",
            "post_history_instructions": "",
            "alternate_greetings": alternates,
            "tags": [],
            "creator": "",
            "character_version": str(identity.get("version") or ""),
            "extensions": {},
            "character_book": {
                "name": f"{char_name} 世界书",
                "entries": entries,
                "extensions": {},
            },
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 已编译：{out_path}")
    print(f"  角色：{char_name}｜世界书条目：{len(entries)} 条｜"
          f"first_mes：{'有' if first_mes else '无'}｜alternate：{len(alternates)}｜example：{len(example_lines)}")
    n_sel = sum(1 for e in entries if e["selective"])
    print(f"  selective=true 保留：{n_sel} 条（空 secondary 已在编译层归一化为 false）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="把平台无关角色资产编译回 ST 可导入的 chara_card_v3 卡")
    ap.add_argument("--assets", required=True, help="资产目录（含 persona.yaml 与 lore/、dialogs/）")
    ap.add_argument("--out", required=True, help="输出 card.json 路径")
    ap.add_argument("--name", default=None, help="角色名（缺省用 persona.identity.name）")
    ap.add_argument("--resolve-refs", action="store_true",
                    help="解析 persona 的 lore_refs / dialogs_refs（含 ../ 跨资产），并入编译结果")
    ap.add_argument("--allow-dead-entries", action="store_true",
                    help="死条目（启用+非常驻+keys 空）从错误降为警告并继续输出")
    args = ap.parse_args()
    return compile_assets(Path(args.assets), Path(args.out),
                          resolve_refs=args.resolve_refs, name=args.name,
                          allow_dead=args.allow_dead_entries)


if __name__ == "__main__":
    raise SystemExit(main())
