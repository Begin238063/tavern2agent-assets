#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出与审计角色卡 JSON 中的世界书条目、顶层字段、扩展脚本及运行时排除候选。

Usage:
    python3 list_entries.py card.json                     # 列出所有世界书条目（含数组下标与 entry id）
    python3 list_entries.py card.json --filter mvu        # 只看含 [mvu*] 标记的条目
    python3 list_entries.py card.json --filter mvu_update # 只看 [mvu_update]
    python3 list_entries.py card.json --filter initvar    # 只看 [initvar]
    python3 list_entries.py card.json --search 战斗       # 搜索含关键词的条目
    python3 list_entries.py card.json --stats             # 一键统计（类型×标记×角色条目×扩展脚本）
    python3 list_entries.py card.json --audit             # 全量卡片审计（data 字段 + 世界书 + TH/正则脚本 + 方案建议）
    python3 list_entries.py card.json --suggest-runtime   # 扫描并输出卡侧 runtime.yaml 建议清单（只读）
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# 允许从任意工作目录直接执行 scripts/list_entries.py 时导入同目录 _conditions
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import _conditions  # noqa: E402


def _entry_id_of(entry: dict) -> int | None:
    """读取原卡条目的稳定整数 id（与 build_assets.py 的 runtime.yaml 口径对齐）。"""
    for key in ("id", "uid"):
        val = entry.get(key)
        if isinstance(val, int) and not isinstance(val, bool):
            return val
        try:
            if val is not None and str(val).strip() != "":
                return int(val)
        except (TypeError, ValueError):
            continue
    return None


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

        comment_lower = comment.lower()

        if filter_tag:
            if filter_tag in ("mvu", "mvu_plot", "mvu_update", "initvar"):
                if filter_tag == "mvu":
                    if "[mvu" not in comment_lower:
                        continue
                else:
                    if f"[{filter_tag}]" not in comment_lower:
                        continue

        if search:
            combined = comment + " ".join(str(k) for k in keys) + content[:200]
            if search.lower() not in combined.lower():
                continue

        tags = []
        if "[mvu_plot]" in comment_lower:
            tags.append("📖plot")
        elif "[mvu_update]" in comment_lower:
            tags.append("📝update")
        if "[initvar]" in comment_lower:
            tags.append("🔰initvar")
        if entry.get("constant"):
            tags.append("🔒常驻")
        if entry.get("selective"):
            tags.append("⚡选择性")
        if not entry.get("enabled", True):
            tags.append("❌禁用")

        tag_str = " ".join(tags) + " " if tags else ""
        eid = _entry_id_of(entry)
        id_label = f"[{i:3d} | id={eid}]" if eid is not None else f"[{i:3d}]"

        print(f"{id_label} {tag_str}{comment}")
        if keys:
            print(f"      触发词: {', '.join(str(k) for k in keys[:6])}")
        print(f"      内容: {content_preview}")
        print()
        shown += 1

    print(f"\n显示 {shown} / {len(entries)} 条")


# ─── 扩展脚本与卡片审计（吸收上游 script-analysis.md / SKILL.md）──────────

def _extract_th_scripts(ext: dict) -> list[dict]:
    """兼容 tavern_helper 的两种常见存档形态（dict.scripts 或键值元组列表）。"""
    th = ext.get("tavern_helper")
    if isinstance(th, dict):
        scripts = th.get("scripts")
        return [s for s in scripts if isinstance(s, dict)] if isinstance(scripts, list) else []
    if isinstance(th, list):
        for item in th:
            if isinstance(item, list) and len(item) == 2 and item[0] == "scripts" and isinstance(item[1], list):
                return [s for s in item[1] if isinstance(s, dict)]
    return []


def classify_th_script(script: dict) -> str:
    """按上游 script-analysis.md 信号对 tavern_helper 脚本分类。"""
    content = str(script.get("content") or "")
    if "registerMvuSchema" in content or "z.object" in content:
        return "zod_schema"
    if "MagVarUpdate" in content:
        return "mvu_engine"
    if re.search(r"^\s*import\s+.*https?://", content, re.M):
        return "external_import"
    if "<div" in content or "<style" in content or "document." in content or "$(" in content:
        return "ui_or_dom"
    return "game_or_util"


def classify_regex_script(rs: dict) -> str:
    """按上游 script-analysis.md 信号对 regex_scripts 分类。"""
    name = str(rs.get("scriptName") or "")
    find = str(rs.get("findRegex") or "")
    repl = str(rs.get("replaceString") or "")
    if "UpdateVariable" in find or "update(?:variable)" in find.lower():
        return "ST_CLEANUP"
    if not repl and ("StatusPlaceHolder" in find or "options|selection" in find or "隐藏" in name or "不发送" in name or "删除" in name):
        return "AI_HIDE"
    if "杀八股" in name or (not repl and len(find) > 30):
        return "PROSE_LINT"
    if len(repl) > 1500 or "<div" in repl or "<details" in repl:
        return "UI_PANEL"
    if len(repl) > 500:
        return "GAME_INJECT"
    return "OTHER"


# ─── runtime.yaml 候选扫描（吸收上游 mvu-mapping 反幻觉清单 + _conditions）──

_PURE_SETVAR_RE = re.compile(r"^(?:\s*\{\{(?:set|add|inc|dec)var::.*?\}\}\s*)+$", re.S)
_XML_TAG_LINE_RE = re.compile(r"^</?[A-Za-z_][A-Za-z0-9_.-]*>$")
_SIMPLE_IDENT_COND_RE = re.compile(r"^_?[A-Za-z0-9_$]{1,12}$")


def _is_pure_xml_wrapper(entry: dict) -> bool:
    """识别 ST 世界书排序拼接用的纯 XML 前标/后标（无触发键、超短、仅有分隔线/标题/单标签）。"""
    content = (entry.get("content") or "").strip()
    if not content or len(content) > 120:
        return False
    keys = [str(k).strip() for k in (entry.get("keys") or []) if str(k).strip()]
    if keys or not entry.get("constant"):
        return False
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not lines or len(lines) > 4:
        return False
    has_xml = False
    for ln in lines:
        if ln == "---" or ln.startswith("#"):
            continue
        if _XML_TAG_LINE_RE.match(ln):
            has_xml = True
            continue
        return False
    return has_xml


def _is_dead_or_trap_ejs(content: str) -> str | None:
    """用 _conditions.split_conditions 检测零无条件正文且全为死代码/反盗卡混淆陷阱的条目。

    注意：正常剧情门控条目（如整段包裹在 getvar('stat_data.剧情权重') > N 内）必须保留在
    资产层供编译期归一化，绝不能误判为死代码。
    """
    if "<%" not in content:
        return None
    segs, rep = _conditions.split_conditions(content)
    if not segs:
        return None
    if any(s.condition is None and s.text.strip() for s in segs):
        return None
    conds = [s.condition for s in segs if s.condition is not None and s.text.strip()]
    if not conds:
        return None
    if all(c in ("false", "0", "!true") for c in conds):
        return "EJS if (false) 死代码条目（条件外无有效正文）"
    has_trap_marker = ("__PPX_TRIP" in content or "piupiu_" in content)
    all_obfuscated_var = (
        bool(rep.code_tags)
        and all(_SIMPLE_IDENT_COND_RE.match(c) and "getvar" not in c.lower() for c in conds)
    )
    if has_trap_marker or all_obfuscated_var:
        return f"EJS 反盗卡/混淆死代码陷阱（门控 {conds[0]}，条件外无有效正文）"
    return None


def suggest_runtime_excludes(card: dict) -> list[dict]:
    """扫描 character_book.entries[]，返回建议写入 runtime.yaml 的纯运行时条目列表。

    每项结构：{"id": int, "index": int, "category": str, "comment": str, "reason": str}
    遵循上游 mvu-mapping 结论：只有 [initvar] / [mvu_update] 属于 MVU 引擎条目；
    [mvu_plot] 常用于正文扮演规则，仅在内容本身为前端组件或格式防呆时才列入候选。
    """
    data = card.get("data", card)
    entries = (data.get("character_book") or {}).get("entries") or []
    candidates: list[dict] = []

    for idx, entry in enumerate(entries):
        eid = _entry_id_of(entry)
        if eid is None:
            continue
        comment = (entry.get("comment") or "").strip()
        comment_lower = comment.lower()
        content = entry.get("content") or ""

        reason = None
        category = None

        # 1) MVU / 变量读写与初始化管道
        if "[mvu_update]" in comment_lower:
            category, reason = "mvu_pipeline", "[mvu_update] 变量更新引擎规则/格式管道"
        elif "[initvar]" in comment_lower:
            category, reason = "mvu_pipeline", "[initvar] 初始变量表"
        elif "[opening]" in comment_lower:
            category, reason = "mvu_pipeline", "[opening] 开场状态存储条目"
        elif "[勿开]" in comment or "勿开" in comment and ("备份" in comment or "变量" in comment or "数据" in comment):
            category, reason = "mvu_pipeline", "变量/开场数据备份条目（勿开）"
        elif _PURE_SETVAR_RE.match(content.strip()):
            category, reason = "mvu_pipeline", "纯 ST 变量赋值宏管道（setvar/addvar）"
        elif "<status_current_variable>" in content and "</status_current_variable>" in content and "getMessageVar" in content:
            category, reason = "mvu_pipeline", "ST 状态变量注入管道（<status_current_variable>）"
        elif ("变量格式" in comment or "格式防呆" in comment) and "<UpdateVariable>" in content and len(content) < 1500:
            category, reason = "mvu_pipeline", "<UpdateVariable> 输出格式防呆管道"

        # 2) ST 前端渲染组件（插图 / 选项面板）
        if reason is None:
            if "<pic>" in content and ("{{roll" in content or "插图" in comment or "cg" in comment_lower):
                category, reason = "st_ui_render", "ST 插图标签渲染管道（<pic> + 骰子/模板）"
            elif ("<selection>" in content or "<options>" in content) and ("行动选项" in comment or "选项" in comment):
                category, reason = "st_ui_render", "ST 前端行动选项模板（<selection>/<options>）"

        # 3) ST 世界书排序拼接用纯 XML 前后标牌
        if reason is None and _is_pure_xml_wrapper(entry):
            tag_preview = " ".join(content.strip().split())[:40]
            category, reason = "xml_wrapper", f"ST 排序拼接用纯 XML 结构标牌（{tag_preview}）"

        # 4) EJS 死代码 / 反盗卡陷阱
        if reason is None:
            trap_reason = _is_dead_or_trap_ejs(content)
            if trap_reason:
                category, reason = "dead_or_trap", trap_reason

        if reason is not None:
            candidates.append({
                "id": eid,
                "index": idx,
                "category": category,
                "comment": comment or f"entry_{idx}",
                "reason": reason,
            })

    return candidates


def format_runtime_yaml(candidates: list[dict]) -> str:
    """把 suggest_runtime_excludes 的结果格式化为可直接保存的 runtime.yaml 文本。"""
    group_titles = [
        ("mvu_pipeline", "ST MVU / 变量读写与初始化管道"),
        ("st_ui_render", "ST 前端交互/插图渲染模板"),
        ("xml_wrapper", "ST 世界书排序拼接用纯 XML 结构起止标签"),
        ("dead_or_trap", "EJS 死代码 / 反盗卡陷阱（条件外无有效设定正文）"),
    ]
    lines = [
        'version: "1"',
        "",
        "# 由 list_entries.py --suggest-runtime 自动扫描生成（请人工复核后保存至 cards/<角色名>/runtime.yaml）",
        "# 原文仍完整保留在 card.json；若某条需调回资产层，从此清单移除该 id 即可。",
        "exclude_entry_ids:",
    ]
    if not candidates:
        lines.append("  []")
        return "\n".join(lines) + "\n"

    first_group = True
    for cat_key, title in group_titles:
        items = [c for c in candidates if c["category"] == cat_key]
        if not items:
            continue
        if not first_group:
            lines.append("")
        first_group = False
        lines.append(f"  # --- {title}（{len(items)} 条） ---")
        for c in items:
            safe_comment = c["comment"].replace("\n", " ").strip()
            lines.append(f"  - {c['id']:<5d} # {safe_comment} —— {c['reason']}")
    return "\n".join(lines) + "\n"


def stats_entries(card: dict):
    """--stats：类型×标记×角色条目×扩展脚本一键统计（大型世界书审计用）。"""
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

    ext = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    th_scripts = _extract_th_scripts(ext)
    regex_scripts = ext.get("regex_scripts") if isinstance(ext.get("regex_scripts"), list) else []
    rt_candidates = suggest_runtime_excludes(card)

    print(f"总条目: {N}")
    print(f"  constant(常驻): {len(constant)}  (启用 {len(cc)} / 禁用 {len(cd)})")
    print(f"  selective(选择性): {len(selective)}  (启用 {len(sc)} / 禁用 {len(sd)})")
    print(f"  禁用(enabled=false): {len(disabled)} / 启用: {len(enabled)}")
    print(f"标记: [mvu]任意 {len(mvu)} | [mvu_update] {len(mvu_update)} | [mvu_plot] {len(mvu_plot)} | [initvar] {len(initvar)}")
    print(f"角色定义/人设类条目: {len(chardef)}  (唯一 <Character_X> 标签 {len(char_tags)} 个; [Pro] {len(pro)} / [Lite] {len(lite)})")
    print(f"含 ST 模板补丁(<%_/{{{{var}}/<%==)的条目: {len(patched)} / {N}")
    print(f"扩展脚本: tavern_helper.scripts {len(th_scripts)} 条 | regex_scripts {len(regex_scripts)} 条")
    print(f"runtime.yaml 候选排除条目: {len(rt_candidates)} 条（可用 --suggest-runtime 查看详情）")


def audit_card(card: dict):
    """--audit：一步完成 SKILL.md 第 2 步全量审计（data 顶层字段 + 世界书 + TH/正则脚本 + 方案选型建议）。"""
    data = card.get("data", card)
    spec = card.get("spec", "未知")
    spec_ver = card.get("spec_version", "")
    name = data.get("name", "(未命名)")
    creator = data.get("creator", "")
    version = data.get("character_version", "")

    print(f"== 卡片全量审计：{name}（{spec} {spec_ver}） ==")
    if creator or version:
        print(f"作者: {creator or '(空)'} | 版本: {version or '(空)'}")

    print("\n── 1. data 顶层文本字段 ──")
    text_fields = (
        "description", "personality", "scenario", "system_prompt",
        "post_history_instructions", "first_mes", "mes_example", "creator_notes",
    )
    for k in text_fields:
        val = data.get(k) or ""
        s = str(val)
        preview = s[:48].replace("\r", "").replace("\n", "\\n") if s else ""
        print(f"  {k:<26} len={len(s):<6d}" + (f" | {preview}" if preview else ""))

    alts = data.get("alternate_greetings") or []
    grp = data.get("group_only_greetings") or []
    ext = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    if not grp and isinstance(ext.get("group_only_greetings"), list):
        grp = ext.get("group_only_greetings") or []

    alt_html = sum(1 for g in alts if isinstance(g, str) and ("<!--AI_HIDE_" in g or "<div" in g))
    alt_init = sum(1 for g in alts if isinstance(g, str) and "<UpdateVariable>" in g)
    print(f"  {'alternate_greetings':<26} count={len(alts):<4d}"
          + (f" (含 HTML 面板/块 {alt_html} 条, 含 <UpdateVariable> {alt_init} 条)" if alts else ""))
    print(f"  {'group_only_greetings':<26} count={len(grp):<4d}")

    dp = ext.get("depth_prompt")
    if isinstance(dp, dict) and dp.get("prompt"):
        dp_text = str(dp.get("prompt") or "")
        print(f"  {'extensions.depth_prompt':<26} len={len(dp_text):<6d} depth={dp.get('depth')} role={dp.get('role')}")

    print("\n── 2. 世界书与运行时统计 ──")
    stats_entries(card)

    print("\n── 3. 扩展脚本审计（tavern_helper / regex_scripts） ──")
    th_scripts = _extract_th_scripts(ext)
    if th_scripts:
        print(f"  tavern_helper.scripts（共 {len(th_scripts)} 条）：")
        for i, s in enumerate(th_scripts):
            c = str(s.get("content") or "")
            kind = classify_th_script(s)
            en = s.get("enabled", True)
            print(f"    [{i}] {s.get('name', '?')!r} enabled={en} kind={kind} len={len(c)}")
    else:
        print("  tavern_helper.scripts：无")

    regex_scripts = ext.get("regex_scripts") if isinstance(ext.get("regex_scripts"), list) else []
    if regex_scripts:
        by_kind: dict[str, list[tuple[bool, str]]] = {}
        for rs in regex_scripts:
            if not isinstance(rs, dict):
                continue
            kind = classify_regex_script(rs)
            enabled = not bool(rs.get("disabled", False))
            by_kind.setdefault(kind, []).append((enabled, str(rs.get("scriptName") or "?")))
        print(f"  regex_scripts（共 {len(regex_scripts)} 条）：")
        for kind, items in sorted(by_kind.items()):
            en_cnt = sum(1 for en, _ in items if en)
            sample_names = ", ".join(n for _, n in items[:3])
            more = f" 等 {len(items)} 条" if len(items) > 3 else ""
            print(f"    - {kind:<12}: {len(items)} 条（启用 {en_cnt} / 禁用 {len(items) - en_cnt}）｜示例: {sample_names}{more}")
    else:
        print("  regex_scripts：无")

    # 4) 多角色 / 世界卡方案建议（对齐 SKILL.md 判据）
    entries = (data.get("character_book") or {}).get("entries") or []
    char_tags = set()
    for e in entries:
        for m in re.finditer(r"<Character_([A-Za-z0-9_]+)>", e.get("content") or ""):
            char_tags.add(m.group(1))
    pro_cnt = sum(1 for e in entries if "[Pro]" in (e.get("comment") or ""))

    print("\n── 4. 拆卡方案选型提示（SKILL.md） ──")
    if grp:
        print("  检测到非空 group_only_greetings → ST 群组多角色卡，建议评估【方案 B（拆多角色）】或【方案 C（世界底座 + 跨资产引用）】。")
    elif len(char_tags) >= 5 or pro_cnt >= 5:
        print(f"  检测到多角色世界书信号（<Character_X> {len(char_tags)} 个, [Pro] {pro_cnt} 条）→ 建议采用【方案 C（先生成世界观底座，角色按需用 ../ 跨资产引用）】。")
    else:
        print("  未见大型多角色合集信号 → 建议按【标准单角色流程 / 方案 A】处理。")


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
    do_audit = False
    do_suggest_runtime = False

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
        elif args[i] == "--audit":
            do_audit = True
            i += 1
        elif args[i] == "--suggest-runtime":
            do_suggest_runtime = True
            i += 1
        else:
            print(f"未知参数: {args[i]}")
            sys.exit(1)

    if do_suggest_runtime:
        candidates = suggest_runtime_excludes(card)
        print(format_runtime_yaml(candidates), end="")
        return

    if do_audit:
        audit_card(card)
        return

    if do_stats:
        stats_entries(card)
        return

    list_entries(card, filter_tag, search)


if __name__ == "__main__":
    main()
