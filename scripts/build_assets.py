#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_assets.py —— 从 card.json 生成平台无关的角色资产骨架。

输入：
    scripts/extract_card.py 产出的 card.json（原始结构化字段，含 spec + data）。

输出（--out 目录下 characters/<角色名>/）：
    persona.yaml            角色画像骨架（人工/LLM 补全）
    lore/<key>.yaml         世界书条目拆分（每条一个模块；机器字段在 _derived 块内）
    dialogs/example_NN.yaml mes_example 回合拆分（每回合一条示例）
    provenance.md           来源与提取信息

设计约束：
    - 平台无关：产物不含任何聊天平台/运行时专属概念，不生成任何运行时工程。
      ST 的 depth/position 不直接透传，而是映射为平台无关注入语义 inject_at
      （resident | context_head | near_input），由各平台渲染器自行映射回自己的注入点。
    - 只读安全：只读入 --card 指定的卡文件；只向 --out 指定的资产目录写入，
      绝不触碰用户数据或项目其它文件。
    - 纯标准库：仅使用 json / hashlib / pathlib / re / datetime / argparse 与
      同目录的 _mini_yaml.py（本工具族共享的极简解析器）。
    - 幂等：已存在的资产文件默认跳过（--force 覆盖），并打印 跳过/更新/新建 状态。
    - 激活语义无损：keys / secondary_keys / selective / constant / insertion_order /
      case_sensitive / scan_depth / prevent_recursion / budget_tokens / use_regex /
      inject_at 全部结构化保留；机器字段归入 _derived 块，人工字段在块外。
    - 中文：全部注释、文档、输出信息使用中文。

用法：
    python3 scripts/build_assets.py --card card.json --out characters/
    python3 scripts/build_assets.py --card card.json --out characters/ --name 自定义名
    python3 scripts/build_assets.py --card card.json --out characters/ --force
    python3 scripts/build_assets.py --card card.json --out characters/ --migrate
        # 字段级合并（推荐）：机器字段按新卡刷新，人工字段（keys/note/layer/budget_tokens）
        # 原样保留；schema 新增字段以空值/默认值补齐。上游卡更新时反复走这条路。
"""

import argparse
import datetime
import hashlib
import json
import re
import sys
import io
from pathlib import Path

# 强制 UTF-8 输出（Windows 默认 GBK 会导致中文乱码）
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import _mini_yaml

# ─── 通用工具 ──────────────────────────────────────────────────

def _sha256_of(path: Path) -> str:
    """计算 card.json 的 SHA-256 摘要，用于追溯与去重。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_iso() -> str:
    """当前时间（本地时区，ISO 格式），用于 provenance。"""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _sanitize_filename(name: str, fallback: str = "entry") -> str:
    """清理文件名：去掉路径分隔符/非法字符/空白，限制长度；清理后为空则回退 fallback。"""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    if not cleaned:
        return fallback
    return cleaned[:60]


def _yaml_quote(value: object) -> str:
    """给 YAML 标量加双引号（统一引号风格，与 schema 示例一致），并转义内部引号/反斜杠。"""
    if value is None:
        return '""'
    s = str(value)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _yaml_block(text: str, indent: int = 2) -> str:
    """把多行文本写成 YAML 块标量（|- 去掉末尾换行）。内容行缩进 = indent。"""
    lines = text.rstrip("\n").split("\n")
    pad = " " * indent
    return "|-\n" + "\n".join(pad + ln for ln in lines) + "\n"


def _yaml_str_list(items: list, indent: int = 2) -> str:
    """把字符串列表写成 YAML 序列；空列表输出带缩进的 []（否则裸 [] 会落在第 0 列破坏结构）。"""
    pad = " " * indent
    if not items:
        return pad + "[]"
    return "\n".join(f"{pad}- {_yaml_quote(i)}" for i in items)


def _source_of(card: dict, card_path: Path) -> str:
    """追溯原卡文件名：优先 card.json 里的 _source_file（extract_card.py 写入），否则用归档文件名。"""
    return str(card.get("_source_file") or card_path.name)


def _write_file(path: Path, content: str, force: bool) -> str:
    """写入文件并返回状态：新建 / 跳过 / 更新。已存在且非 force 时跳过（幂等）。"""
    if path.exists():
        if not force:
            return "跳过"
        path.write_text(content, encoding="utf-8")
        return "更新"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "新建"


def _display(path: Path) -> str:
    """打印用路径：尽量相对当前目录，否则显示绝对路径。"""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


# ─── 世界书拆分 ────────────────────────────────────────────────

def _markers_of(comment: str) -> list:
    """从世界书条目的 comment 中提取 [mvu]/[initvar]/[mvu_update] 等标记。"""
    if not comment:
        return []
    return re.findall(r"\[[^\[\]]+\]", comment)


def _note_of(comment: str, constant: bool, selective: bool, enabled: bool) -> str:
    """生成 note 初值：原卡标记 + 属性提示。此后 note 视为人工所有（migrate 保留）。"""
    tags = _markers_of(comment)
    parts = []
    if tags:
        parts.append("原卡标记: " + "、".join(tags))
    else:
        parts.append("无特殊标记")
    if constant:
        parts.append("常驻条目")
    if selective:
        parts.append("选择性条目")
    # disabled 条目的 note 留空，由人工填写处置说明
    if not enabled:
        return ""  # 空字符串，验证脚本会要求补全
    return "；".join(parts)


def _inject_at_of(entry: dict) -> str:
    """把原卡 position/depth 映射为平台无关注入位置语义（渲染器各自映射回自己的注入点）：

        constant: true            → resident（常驻，始终注入）
        after_char（v2 数值 1）    → near_input（贴近最近消息）
        before_char（v2 数值 0）：
            depth <= 1 或缺省     → near_input
            depth >= 2            → context_head（靠近上下文头部/系统区）
    """
    if entry.get("constant"):
        return "resident"
    position = entry.get("position")
    depth = (entry.get("extensions") or {}).get("depth")
    if isinstance(depth, (int, float)):
        depth = int(depth)
    else:
        depth = None
    if position == "after_char" or position == 1:
        return "near_input"
    # before_char（字符串）或 0（数值）
    if depth is None or depth <= 1:
        return "near_input"
    return "context_head"


# 已提升为一级字段的原卡字段：不再塞进 extensions_json
_HANDLED = {
    "id", "keys", "content", "comment", "constant", "selective", "enabled",
    "secondary_keys", "insertion_order", "position", "use_regex", "extensions",
}


def _machine_fields(entry: dict, idx: int, sha256: str) -> dict:
    """从原卡条目构建机器字段（_derived 块内容，--migrate 时按新卡整体重写）。

    透传：comment / secondary_keys / selective / constant / enabled / insertion_order /
          use_regex；case_sensitive / scan_depth / prevent_recursion 从原卡 extensions 提升；
          inject_at 由 position/depth 语义映射；其余未处理字段原样序列化进 extensions_json。
    """
    ext = entry.get("extensions") or {}

    def _e(key, default):
        v = ext.get(key)
        return default if v is None else v

    extra = {k: v for k, v in entry.items() if k not in _HANDLED}
    return {
        "comment": entry.get("comment") or "",
        "use_regex": bool(entry.get("use_regex", False)),
        "secondary_keys": [str(k) for k in (entry.get("secondary_keys") or [])],
        "selective": bool(entry.get("selective", False)),
        "constant": bool(entry.get("constant", False)),
        "enabled": bool(entry.get("enabled", True)),
        "insertion_order": int(entry.get("insertion_order", 100) or 100),
        "case_sensitive": bool(_e("case_sensitive", False)),
        "scan_depth": _e("scan_depth", None),
        "prevent_recursion": bool(_e("prevent_recursion", False)),
        "inject_at": _inject_at_of(entry),
        "extensions_json": json.dumps(extra, ensure_ascii=False) if extra else "",
        "_entry_id": entry.get("id"),
        "_card_sha": sha256,
        "content": entry.get("content") or "",
    }


def _render_lore(key_name: str, idx: int, machine: dict, manual: dict) -> str:
    """渲染 lore/<key>.yaml：机器字段在 _derived 块内，人工字段（keys/note/layer/budget_tokens）在块外。"""
    lines = [
        f"# 世界书条目：{key_name} —— 来自 card.json 的 character_book.entries[{idx}]",
        "# 本文件是平台无关的原始内容保留，不是任何运行时的注入规则。",
        "# 机器字段（_derived 块）由 build_assets.py 维护；--migrate 只重写块内；",
        "# 块外字段（keys / note / layer / budget_tokens）视为人工所有，--migrate 原样保留。",
        f"id: entry_{idx:02d}",
        f"source: character_book.entries[{idx}]",
        "_derived:",
        f"  comment: {_yaml_quote(machine['comment'])}",
        f"  use_regex: {'true' if machine['use_regex'] else 'false'}",
        "  secondary_keys:",
        _yaml_str_list(machine["secondary_keys"], indent=4),
        f"  selective: {'true' if machine['selective'] else 'false'}",
        f"  constant: {'true' if machine['constant'] else 'false'}",
        f"  enabled: {'true' if machine['enabled'] else 'false'}",
        f"  disabled: {'true' if not machine['enabled'] else 'false'}",
        f"  insertion_order: {machine['insertion_order']}",
        f"  case_sensitive: {'true' if machine['case_sensitive'] else 'false'}",
        "  scan_depth: " + ("null" if machine["scan_depth"] is None else str(machine["scan_depth"])),
        f"  prevent_recursion: {'true' if machine['prevent_recursion'] else 'false'}",
        f"  inject_at: {machine['inject_at']}",
    ]
    if machine.get("extensions_json"):
        lines.append(f"  extensions_json: {_yaml_quote(machine['extensions_json'])}")
    if machine.get("_entry_id") is not None:
        lines.append(f"  _entry_id: {machine['_entry_id']}")
    lines.append(f"  _card_sha: {_yaml_quote(machine['_card_sha'])}")
    if machine["content"]:
        lines.append("  content: " + _yaml_block(machine["content"], indent=4))
    else:
        lines.append('  content: ""')
    # 人工字段区
    lines.append("keys:")
    lines.append(_yaml_str_list(manual.get("keys") or [], indent=2))
    lines.append(f"note: {_yaml_quote(manual.get('note') or '')}")
    lines.append(f"layer: {manual.get('layer') or ''}")
    bt = manual.get("budget_tokens")
    lines.append("budget_tokens: " + ("null" if bt is None else str(bt)))
    lines.append("")
    return "\n".join(lines)


def _infer_layer(entry: dict, filename: str) -> str:
    """推断 layer 默认值（首次生成时）。

    layer 是路由载体：
    - behavior: roleplay 逻辑、格式约束、变量更新规则
    - identity: 角色核心设定、世界观总纲、初始化变量
    - narrative: 剧情大纲、角色/地点/物品百科（默认）
    """
    comment = (entry.get("comment") or "").lower()
    filename_lower = filename.lower()

    # behavior: roleplay 指令、MVU 变量系统
    behavior_patterns = [
        "角色扮演", "roleplay", "注意事项", "系统指令",
        "mvu_update", "mvu_plot", "变量更新", "变量格式", "变量禁词",
        "变量输出", "变量防呆", "行动选项", "cg插图", "守岸人"
    ]
    for pattern in behavior_patterns:
        if pattern in comment or pattern in filename_lower:
            return "behavior"

    # identity: 角色核心、世界观、初始化
    identity_patterns = [
        "世界观总纲", "主角自设", "用户名变量", "角色核心",
        "initvar", "opening", "勿关", "勿开", "默认变量",
        "i.r.i.s", "iris"
    ]
    # 特殊：角色同名条目（如"清宵.yaml"）通常是核心档案
    if entry.get("constant") and len(entry.get("keys") or []) == 1:
        only_key = (entry.get("keys") or [""])[0]
        if only_key and filename.startswith(only_key.lower()):
            return "identity"

    for pattern in identity_patterns:
        if pattern in comment or pattern in filename_lower:
            return "identity"

    # 默认：narrative（剧情、百科）
    return "narrative"


def _manual_defaults(entry: dict, filename: str = "") -> dict:
    """首次生成时的人工字段初值（此后视为人工所有，--migrate 原样保留）。"""
    constant = bool(entry.get("constant", False))
    selective = bool(entry.get("selective", False))
    enabled = bool(entry.get("enabled", True))
    return {
        "keys": [str(k) for k in (entry.get("keys") or [])],
        "note": _note_of(entry.get("comment") or "", constant, selective, enabled),
        "layer": _infer_layer(entry, filename),  # 首次生成时自动推断
        "budget_tokens": entry.get("token_budget") or None,
    }


def _entry_key_name(entry: dict, idx: int) -> str:
    """取 keys 里第一个非空 key 作为文件名；keys 为空时回退到 comment（v3 卡常无 keys 字段）；
    两者皆空才用序号命名。"""
    keys = entry.get("keys") or []
    raw_key = (keys[0] if keys else "") or (entry.get("comment") or "")
    return _sanitize_filename(raw_key, f"entry_{idx:02d}")


def split_lorebook(card: dict, lore_dir: Path, force: bool, sha256: str) -> list:
    """把 character_book.entries[] 拆为 lore/<key>.yaml，返回生成的文件路径列表。

    激活语义全部结构化保留（见 _machine_fields / _render_lore）；keys 为空时用 comment 命名。
    """
    data = card.get("data", card)
    entries = (data.get("character_book") or {}).get("entries") or []
    if not entries:
        print("⚠ 卡内无 character_book（世界书为空），跳过 lore 拆分。")
        return []

    files = []
    used_names = set()  # 已用文件名，防同名 key 碰撞导致静默覆盖/丢失
    for idx, entry in enumerate(entries):
        key_name = _entry_key_name(entry, idx)
        base_name = key_name
        n = 2
        while key_name in used_names:
            key_name = f"{base_name}_{n}"
            n += 1
        used_names.add(key_name)

        machine = _machine_fields(entry, idx, sha256)
        manual = _manual_defaults(entry, key_name)
        content = _render_lore(key_name, idx, machine, manual)

        path = lore_dir / f"{key_name}.yaml"
        status = _write_file(path, content, force)
        print(f"  [{status}] {_display(path)}")
        files.append(f"lore/{path.name}")

    return files


def migrate_lorebook(card: dict, lore_dir: Path, sha256: str) -> list:
    """字段级合并：机器字段按新卡刷新，人工字段（keys/note/layer/budget_tokens）原样保留。

    - 优先按原卡稳定 id（_entry_id）匹配旧文件，其次按 source（character_book.entries[N]）
      索引匹配——卡片中间删/插条目后索引会漂移，稳定 id 才不会把人工字段错配给别的条目；
    - migrate 不重命名文件（lore_refs 引用不变）；
    - 旧格式（无 _derived 块）无 _entry_id，退化为索引匹配；
    - 卡片中已移除的条目：保留文件并提示，由人工决定去留。
    """
    data = card.get("data", card)
    entries = (data.get("character_book") or {}).get("entries") or []
    if not entries:
        print("⚠ 卡内无 character_book，无可迁移。")
        return []

    existing = {}  # path -> (source, entry_id 字符串或 None)
    for f in sorted(lore_dir.glob("*.yaml")):
        try:
            parsed = _mini_yaml.parse(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        src = parsed.get("source") or ""
        eid = _mini_yaml.get(parsed, "_entry_id")
        if src or eid is not None:
            existing[f] = (src, str(eid) if eid is not None else None)

    files = []
    matched = set()
    for idx, entry in enumerate(entries):
        src = f"character_book.entries[{idx}]"
        machine = _machine_fields(entry, idx, sha256)
        eid = str(machine["_entry_id"]) if machine["_entry_id"] is not None else None
        # 1) 稳定 id 匹配（文件带 _entry_id 时只走这条，杜绝索引漂移错配）；
        # 2) source 索引兜底：仅限旧格式（无 _entry_id 的文件），避免稳定 id 存在时把
        #    新条目错配到被删条目遗留的文件上。
        path = next((f for f, (s, e) in existing.items()
                     if f not in matched and eid is not None and e == eid), None)
        if path is None:
            path = next((f for f, (s, e) in existing.items()
                         if f not in matched and e is None and s == src), None)
        if path is not None:
            matched.add(path)
            old = _mini_yaml.parse(path.read_text(encoding="utf-8"))
            manual = {
                "keys": old.get("keys") if old.get("keys") is not None
                        else [str(k) for k in (entry.get("keys") or [])],
                "note": old.get("note") if old.get("note") is not None
                        else _manual_defaults(entry, key_name)["note"],
                "layer": old.get("layer") or _infer_layer(entry, key_name),  # 保留人工 layer；空则推断
                "budget_tokens": old.get("budget_tokens")
                        if old.get("budget_tokens") is not None
                        else (entry.get("token_budget") or None),
            }
            key_name = path.stem  # migrate 不重命名：lore_refs 引用不变
            status = "迁移"
        else:
            key_name = _entry_key_name(entry, idx)
            manual = _manual_defaults(entry, key_name)
            path = lore_dir / f"{key_name}.yaml"
            status = "新建"
        content = _render_lore(key_name, idx, machine, manual)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"  [{status}] {_display(path)}")
        files.append(f"lore/{path.name}")

    for f, (s, e) in sorted(existing.items()):
        if f not in matched:
            # 孤立标记（E4 决策）：机器不替人做去留决定，但强制人做——
            # 写入 _orphaned: true，validate 会警告并要求 note 写处置去向。
            text = f.read_text(encoding="utf-8")
            if "_orphaned:" not in text:
                # 插到 source: 行之后（顶层），保持 _mini_yaml 可解析
                if re.search(r"^source: .*$", text, re.M):
                    text = re.sub(r"^source: .*$", lambda m: m.group(0) + "\n_orphaned: true", text, count=1, flags=re.M)
                else:
                    text = "_orphaned: true\n" + text
                f.write_text(text, encoding="utf-8")
            print(f"  [孤立] {_display(f)}（{s or ('_entry_id=' + e)}，上游卡已删除；"
                  f"已标 _orphaned: true，请在 note 写处置去向）")

    return files


# ─── 对话拆分 ──────────────────────────────────────────────────

# 支持 {{user}}: / {{char}}: 与部分卡的 {{user}}:: / {{char}}::（双冒号）变体
_USER_RE = re.compile(r"^\{\{user\}\}\s*[:：]{1,2}\s*(.*)$", re.I)
_CHAR_RE = re.compile(r"^\{\{char\}\}\s*[:：]{1,2}\s*(.*)$", re.I)


def _parse_mes_example(mes_example: str) -> list:
    """把 mes_example 按 {{user}}/{{char}} 标记拆成回合列表 [{role, content}, ...]。

    <START>/<END> 等区块标记只作分隔；未识别标记的行追加到当前回合作为续行。
    """
    turns = []
    current = None
    for raw_line in mes_example.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper in ("<START>", "<END>", "<START", "START>", "<START/>"):
            current = None  # 区块标记：结束当前回合，不产生回合
            continue
        m = _USER_RE.match(stripped)
        if m:
            current = {"role": "user", "content": [m.group(1).strip()]}
            turns.append(current)
            continue
        m = _CHAR_RE.match(stripped)
        if m:
            current = {"role": "char", "content": [m.group(1).strip()]}
            turns.append(current)
            continue
        # 续行：接到当前回合
        if current is not None:
            current["content"].append(stripped)

    result = []
    for t in turns:
        text = "\n".join(x for x in t["content"] if x)
        if text:
            result.append({"role": t["role"], "content": text})
    return result


def split_dialogs(card: dict, dialogs_dir: Path, force: bool) -> list:
    """把 mes_example 按回合拆为 dialogs/example_NN.yaml，返回生成的文件路径列表。"""
    data = card.get("data", card)
    mes_example = data.get("mes_example") or ""
    if not mes_example.strip():
        print("⚠ 卡内无 mes_example，跳过示例对话拆分（first_mes 开场白仍会自动资产化）。")
        return []

    turns = _parse_mes_example(mes_example)
    if not turns:
        print("⚠ mes_example 中未识别到 {{user}}/{{char}} 回合，跳过 dialogs 拆分。")
        return []

    files = []
    for i, turn in enumerate(turns, start=1):
        role = turn["role"]
        role_cn = "用户侧发言" if role == "user" else "角色侧发言"
        marker = "{{user}}" if role == "user" else "{{char}}"
        lines = [
            f"# 对话示例 example_{i:02d} —— 来自 mes_example 的第 {i} 回合",
            f"# 角色侧发言的补充参考：原卡 first_mes 与 alternate_greetings。",
            f"role: {role}",
            f"note: {_yaml_quote(f'{role_cn}（标记 {marker}），mes_example 回合 {i}')}",
            "content: " + _yaml_block(turn["content"]),
            "",
        ]
        path = dialogs_dir / f"example_{i:02d}.yaml"
        status = _write_file(path, "\n".join(lines), force)
        print(f"  [{status}] {_display(path)}")
        files.append(f"dialogs/{path.name}")

    return files


def split_greetings(card: dict, dialogs_dir: Path, force: bool) -> list:
    """把 first_mes 与 alternate_greetings 资产化为 dialogs/first_mes.yaml、dialogs/alternate_NN.yaml。

    开场白是角色最重要的开场设定，必须机器保证去向（不再依赖人工/LLM 记得补）。
    返回生成的文件路径列表（相对资产目录）。
    """
    data = card.get("data", card)
    files = []

    first_mes = data.get("first_mes") or ""
    if first_mes.strip():
        lines = [
            "# 对话示例 first_mes —— 角色开场白（来自卡内 data.first_mes，机器保证去向）",
            "role: char",
            "note: " + _yaml_quote("角色开场白（first_mes），角色侧发言"),
            "content: " + _yaml_block(first_mes.strip()),
            "",
        ]
        path = dialogs_dir / "first_mes.yaml"
        status = _write_file(path, "\n".join(lines), force)
        print(f"  [{status}] {_display(path)}")
        files.append("dialogs/first_mes.yaml")
    else:
        print("⚠ 卡内无 first_mes，未生成开场白示例（多数角色卡不应出现；请人工确认）")

    alternates = data.get("alternate_greetings") or []
    if not isinstance(alternates, list):
        alternates = []
    for i, alt in enumerate(alternates, start=1):
        if not (isinstance(alt, str) and alt.strip()):
            continue
        lines = [
            f"# 对话示例 alternate_{i:02d} —— 备用开场白（来自 data.alternate_greetings[{i - 1}]）",
            "role: char",
            "note: " + _yaml_quote(f"备用开场白（alternate_greetings[{i - 1}]），角色侧发言"),
            "content: " + _yaml_block(alt.strip()),
            "",
        ]
        path = dialogs_dir / f"alternate_{i:02d}.yaml"
        status = _write_file(path, "\n".join(lines), force)
        print(f"  [{status}] {_display(path)}")
        files.append(f"dialogs/alternate_{i:02d}.yaml")

    if not first_mes.strip() and not alternates:
        print("⚠ 卡内无 first_mes 且无 alternate_greetings，跳过开场白资产化。")
    return files


# ─── persona.yaml 生成 ─────────────────────────────────────────

def _extract_traits(personality: str) -> list:
    """把 personality 原文按常见中文分隔符切分为条目，作为 traits 的初步草稿。

    启发式初值仅供人工审阅；capabilities / boundaries 默认空数组由人工填写。
    """
    if not personality:
        return []
    parts = re.split(r"[；;。\n、，,]+", personality)
    return [p.strip() for p in parts if p.strip()]


def gen_persona_sketch(card: dict, card_path: Path, name: str, sha256: str,
                       lore_files: list, dialog_files: list, traits: list,
                       manual: dict = None) -> str:
    """生成 persona.yaml 骨架：identity / core / style / lore_refs / dialogs_refs / relationship / provenance。

    manual 不为空时（--migrate），人工字段（codename/archetype/version/self/traits/
    capabilities/boundaries/tone/habits/form/memory_policy）原样保留，机器字段按新卡刷新。
    """
    data = card.get("data", card)
    desc = (data.get("description") or "").strip()
    pers = (data.get("personality") or "").strip()

    manual = manual or {}

    def _m(key, default):
        v = manual.get(key)
        return default if v is None else v

    self_text = _m("self", None)
    if self_text is None:
        self_text = "<!-- 来自角色卡原始字段，可人工改写 -->\n\n" + "\n\n".join(
            t for t in (desc, pers) if t
        )
    spec = card.get("spec", "未知")
    spec_version = str(card.get("spec_version", ""))
    raw_fields = sorted(k for k in data.keys() if isinstance(k, str))
    # 风格示例优先取角色自己的开场白（first_mes），其次第一条对话
    sample = "dialogs/first_mes.yaml" if "dialogs/first_mes.yaml" in dialog_files \
        else (dialog_files[0] if dialog_files else "")

    codename = _m("codename", "")
    archetype = _m("archetype", "")
    version = _m("version", "0.1.0")
    traits = _m("traits", traits)
    capabilities = _m("capabilities", [])
    boundaries = _m("boundaries", [])
    tone = _m("tone", "")
    habits = _m("habits", [])
    form = _m("form", "")
    memory_policy = _m("memory_policy", "")

    suffix = "（待填）" if not codename else ""
    lines = [
        "# ============================================================",
        "# 角色资产：persona.yaml",
        "# 由 build_assets.py 自动生成 —— 仅供人工/LLM 补全与平台无关渲染器消费。",
        "# 字段规范见 assets_schema/persona.schema.yaml，完整示例见 assets_schema/persona.example.yaml。",
        "# 补全原则：只改写标有「待填」或「可人工改写」的字段；auto 字段（provenance 等）勿手改。",
        "# ============================================================",
        "",
        f'schema_version: "1.0"',
        "",
        "identity:",
        f"  name: {_yaml_quote(name)}",
        f"  codename: {_yaml_quote(codename)}" + ("                # 代号/别称（待填）" if suffix else ""),
        f"  archetype: {_yaml_quote(archetype)}" + ("          # 角色原型/定位，如：电子幽灵助手（待填）" if not archetype else ""),
        f'  version: "{version}"            # 资产版本，人工修订后递增',
        "  provenance:",
        f"    hash_sha256: {_yaml_quote(sha256)}  # 来源 card.json 的 SHA-256",
        "",
        "core:",
        "  # core.self：description + personality 原文合并（可人工改写）",
        "  self: " + _yaml_block(self_text, indent=4) if self_text else '  self: ""',
        "  # core.traits：由 personality 启发式切分的初步草稿，请人工审阅增删",
        "  traits:",
        _yaml_str_list(traits, indent=4),
        "  # core.capabilities：能力清单（待填，可空数组）",
        "  capabilities:",
        _yaml_str_list(capabilities, indent=4),
        "  # core.boundaries：行为边界/禁区（待填，可空数组）",
        "  boundaries:",
        _yaml_str_list(boundaries, indent=4),
        "",
        "style:",
        f"  tone: {_yaml_quote(tone)}" + ("                    # 语气基调（待填），如：冷清、数据流隐喻" if not tone else ""),
        "  habits:",
        _yaml_str_list(habits, indent=4),
        f"  sample: {_yaml_quote(sample)}  # 风格示例，指向拆出的第一段对话",
        "",
        "lore_refs:",
        _yaml_str_list(sorted(lore_files), indent=2) if lore_files else "  []   # 无世界书",
        "",
        "dialogs_refs:",
        # 保持生成顺序：first_mes → alternate_NN → example_NN（开场白在前，不排序）
        _yaml_str_list(dialog_files, indent=2) if dialog_files else "  []   # 无对话示例",
        "",
        "relationship:",
        f"  form: {_yaml_quote(form)}" + ("                    # 与用户/外界的关系形态（待填）" if not form else ""),
        f"  memory_policy: {_yaml_quote(memory_policy)}" + ("       # 记忆/好感策略，平台无关表述（待填）" if not memory_policy else ""),
        "",
        "provenance:",
        f"  source_file: {_yaml_quote(_source_of(card, card_path))}",
        f"  spec: {_yaml_quote(spec)}",
        f"  spec_version: {_yaml_quote(spec_version)}",
        f"  character_name: {_yaml_quote(name)}",
        f"  extracted_at: {_yaml_quote(_now_iso())}",
        f"  sha256: {_yaml_quote(sha256)}",
        "  raw_fields:",
        _yaml_str_list(raw_fields, indent=4),
        "",
    ]
    return "\n".join(lines)


# ─── provenance.md 生成 ────────────────────────────────────────

def gen_provenance_md(card: dict, card_path: Path, name: str, sha256: str) -> str:
    """生成 provenance.md：来源文件名、spec 版本、角色名、提取时间、sha256、原始字段清单。"""
    data = card.get("data", card)
    spec = card.get("spec", "未知")
    spec_version = card.get("spec_version", "")
    raw_fields = sorted(k for k in data.keys() if isinstance(k, str))

    lines = [
        "# 角色资产来源信息（provenance）",
        "",
        "> 本文件由 build_assets.py 自动生成，记录资产骨架的来源与提取信息，便于追溯与去重。",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| 来源文件 | {_source_of(card, card_path)} |",
        f"| 卡片规范 | {spec}（spec_version {spec_version}） |",
        f"| 角色名 | {name} |",
        f"| 提取时间 | {_now_iso()} |",
        f"| SHA-256 | `{sha256}` |",
        "",
        f"## 原始字段清单",
        "",
        f"data 层原始字段（共 {len(raw_fields)} 个）：",
        "",
        *[f"- {f}" for f in raw_fields],
        "",
        "## 备注",
        "",
        "- creator_notes 中的敏感内容（秘密/幕后真相）默认不写入公共资产；如需保留，请单独标注「资产内部，勿公开」。",
        "- 资产骨架的语义补全（style / relationship / traits 等）由人工或 LLM 完成，不改变本文件记录。",
        "",
    ]
    return "\n".join(lines)


# ─── 入口 ──────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="从 card.json 生成平台无关的角色资产骨架（幂等；已存在文件默认跳过）"
    )
    ap.add_argument("--card", required=True, help="extract_card.py 产出的 card.json 路径")
    ap.add_argument("--out", default="characters", help="输出根目录（默认 characters/）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的资产文件（人工补全会被清掉，慎用）")
    ap.add_argument("--migrate", action="store_true",
                    help="字段级合并：机器字段按新卡刷新，人工字段原样保留（推荐用于卡更新）")
    ap.add_argument("--name", default=None, help="角色名（缺省使用卡内 data.name）")
    args = ap.parse_args()

    card_path = Path(args.card)
    if not card_path.is_file():
        print(f"错误：找不到 card.json：{card_path}")
        return 1

    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"错误：card.json 解析失败：{exc}")
        return 1

    data = card.get("data", card)
    if not isinstance(data, dict):
        print("错误：card.json 的 data 不是对象，无法生成资产。")
        return 1

    sha256 = _sha256_of(card_path)
    name = (args.name or data.get("name") or "未命名角色").strip()
    if not name:
        name = "未命名角色"
    out_dir = Path(args.out) / _sanitize_filename(name, "未命名角色")
    out_dir.mkdir(parents=True, exist_ok=True)

    mode = "迁移" if args.migrate else ("覆盖" if args.force else "增量")
    print(f"== 资产化（{mode}）：{name}（来源 {card_path.name}，sha256={sha256[:12]}…） ==")

    # 1) 世界书拆分 / 迁移
    if args.migrate:
        lore_files = migrate_lorebook(card, out_dir / "lore", sha256)
    else:
        lore_files = split_lorebook(card, out_dir / "lore", force=args.force, sha256=sha256)
    # 2) 开场白 + 对话拆分（开场白在前：first_mes / alternate_NN / example_NN）
    #    dialogs 纯机器产物：migrate 时也按新卡刷新
    dlg_force = args.force or args.migrate
    greeting_files = split_greetings(card, out_dir / "dialogs", force=dlg_force)
    example_files = split_dialogs(card, out_dir / "dialogs", force=dlg_force)
    dialog_refs = greeting_files + example_files
    # 3) persona.yaml
    traits = _extract_traits(data.get("personality"))
    if args.migrate and (out_dir / "persona.yaml").is_file():
        old_p = _mini_yaml.parse((out_dir / "persona.yaml").read_text(encoding="utf-8"))

        def _g(section, key):
            sec = old_p.get(section)
            return sec.get(key) if isinstance(sec, dict) else None

        manual_p = {
            "codename": _g("identity", "codename"),
            "archetype": _g("identity", "archetype"),
            "version": _g("identity", "version"),
            "self": _g("core", "self"),
            "traits": _g("core", "traits"),
            "capabilities": _g("core", "capabilities"),
            "boundaries": _g("core", "boundaries"),
            "tone": _g("style", "tone"),
            "habits": _g("style", "habits"),
            "form": _g("relationship", "form"),
            "memory_policy": _g("relationship", "memory_policy"),
        }
        persona = gen_persona_sketch(card, card_path, name, sha256, lore_files,
                                     dialog_refs, traits, manual=manual_p)
        st_persona = "迁移"
        (out_dir / "persona.yaml").write_text(persona, encoding="utf-8")
    else:
        persona = gen_persona_sketch(card, card_path, name, sha256, lore_files,
                                     dialog_refs, traits)
        st_persona = _write_file(out_dir / "persona.yaml", persona, args.force)
    print(f"  [{st_persona}] {_display(out_dir / 'persona.yaml')}")
    # 4) provenance.md（纯机器产物，migrate 时刷新）
    prov = gen_provenance_md(card, card_path, name, sha256)
    st_prov = _write_file(out_dir / "provenance.md", prov, dlg_force)
    print(f"  [{st_prov}] {_display(out_dir / 'provenance.md')}")

    total = 1 + len(lore_files) + len(dialog_refs) + 1
    print(
        f"生成 {total} 个文件：persona.yaml、lore/{len(lore_files)} 个模块、"
        f"dialogs/{len(dialog_refs)} 条示例（含开场白 {len(greeting_files)} 条）、provenance.md"
    )
    print(f"资产目录：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
