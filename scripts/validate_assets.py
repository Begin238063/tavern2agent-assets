#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_assets.py —— 机器校验 build_assets.py 产出的资产骨架。

用法：
    python3 scripts/validate_assets.py characters/<角色名>/ [--budget-tokens N] [--strict]
    python3 scripts/validate_assets.py characters/<角色名>/ --level archival  # 归档级验证
    python3 scripts/validate_assets.py characters/<角色名>/ --report-unlayered   # 只输出未分层工作清单

核心设计（layer 是唯一的路由载体）：
    路由目标完全由 layer 决定：identity/behavior → 编译进 Persona 的 system_prompt
    （静态、可缓存）；narrative → 进知识库靠内容检索。constant 不参与路由，
    只是源卡透传信息，仅 compile_card.py 编 ST 卡时消费；keys 同理（ST 运行时才有意义），
    可达性检查归 compile_card.py，本脚本不检查。

校验项：
  A. persona / provenance（同前）：字段齐全 / 语义补全门禁 / refs 存在 / sha 一致。
  B. lore 路由完整性：
     1. enabled 且 layer 缺失或为空 → 错误（新增，替代原 keys 门禁）；
     2. enabled 且 layer 不在 identity/behavior/narrative → 错误（layer 承重，由警告升级）；
     3. 常驻预算：resident_tokens = persona 常驻部分 estimated_tokens
        + Σ estimated_tokens(enabled 且 layer ∈ {identity, behavior})；
        constant 不参与计算；超 --budget-tokens → 错误；
     4. disabled 且 note 为空或仍是机器默认值 → 错误（不变，强制人工处置）；
     5. enabled 且 layer: narrative 且 comment 为空且 content < 40 字 → 警告
        （知识库文档无标题且过短，检索命中概率低）；
     6. keys 非空但过泛（单字 / ≤2 字符 / 全匹配正则）→ 警告（不变；keys 不再承重）；
     7. inject_at 取值出词表 → 警告（仅 compile_card 消费）；
     8. selective=true 且 secondary_keys 为空 → 信息（不变，编译层归一化）；
     9. _derived._orphaned=true（上游卡已删条目）→ 警告：请在 note 写处置去向。

未分层条目不逐文件刷屏报错：收集后以分组工作清单输出（按 comment 前缀
[mvu]/[initvar]/其他 分组、组内按 insertion_order 排序），该清单就是逐批分层的输入。

退出码：无错误返回 0；有错误返回 1（警告不阻止，--strict 把警告升级为错误；信息不参与）。
"""

import argparse
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
from build_assets import _note_of

# persona.yaml 必需字段：顶层键 → 其下必须出现的 2 空格缩进子键（None 表示只需顶层键）
REQUIRED = {
    "schema_version": None,
    "identity": ("name", "codename", "archetype", "version", "provenance"),
    "core": ("self", "traits", "capabilities", "boundaries"),
    "style": ("tone", "habits", "sample"),
    "lore_refs": None,
    "dialogs_refs": None,
    "relationship": ("form", "memory_policy"),
    "provenance": ("source_file", "spec", "spec_version", "character_name",
                   "extracted_at", "sha256", "raw_fields"),
}

# 语义补全门禁：(顶层, 子键) → 中文名；这些字段生成时为 "" 占位，必须填非空
NON_EMPTY = {
    ("identity", "codename"): "identity.codename（代号/别称）",
    ("identity", "archetype"): "identity.archetype（角色原型/定位）",
    ("style", "tone"): "style.tone（语气基调）",
    ("relationship", "form"): "relationship.form（关系形态）",
    ("relationship", "memory_policy"): "relationship.memory_policy（记忆/好感策略）",
}

# layer 是唯一路由载体（承重字段）
LAYERS = ("identity", "behavior", "narrative")
INJECT_ATS = ("resident", "context_head", "near_input")
# 全匹配正则（use_regex: true 时 keys 是正则）
_OVER_BROAD_REGEX = {".", ".*", "(.+)", "^.*$", ".*.*"}
# 未分层条目的 comment 前缀分组
_GROUP_RULES = (("mvu", re.compile(r"mvu", re.I)), ("initvar", re.compile(r"initvar", re.I)))

est = _mini_yaml.estimated_tokens  # token 估算（中文 1 字符≈1 token，ASCII 4 字符≈1 token）


def _scalar(line: str) -> str:
    """取 'key: "value"  # 注释' 的值（去行内注释、去引号与空白）。"""
    v = line.split(":", 1)[1]
    v = v.split(" #", 1)[0]  # 去掉行内注释
    return v.strip().strip('"').strip("'")


def _refs(lines: list, key: str) -> list:
    """提取 <key> 顶层节下的 '- xxx' 引用列表（相对资产目录）。"""
    start = next((i for i, ln in enumerate(lines) if re.match(rf"^{re.escape(key)}:", ln)), None)
    if start is None:
        return []
    refs = []
    for ln in lines[start + 1:]:
        if ln.startswith("  - "):
            ref = ln.strip()
            if ref.startswith("- "):
                ref = ref[2:]
            refs.append(ref.strip().strip('"').strip("'"))
        elif ln and not ln.startswith(("  ", "#")):
            break  # 离开该节
    return refs


def _persona_standing_tokens(persona: dict) -> int:
    """persona 常驻部分 token 估算：identity / core / style / relationship 的文本值。

    persona 的语义字段整体编译进 Persona system_prompt，因此全部算常驻；
    只有 refs（lore_refs / dialogs_refs，指针不是内容）与 provenance（来源指纹，
    不进提示词）不计入。

    relationship 曾被漏算（2026-09-06 修）：form / memory_policy 确实会被下游编译进
    system_prompt，漏算会让 resident_tokens 系统性低估、预算门禁偏松。下游私有仓库
    按「编译后 system_prompt 实际字符」估算，会比本函数略高（多出段落标题），
    那部分是口径差异、不是漏算。
    """
    total = 0
    for sec, keys in (("identity", ("name", "codename", "archetype")),
                      ("core", ("self",)),
                      ("style", ("tone",)),
                      ("relationship", ("form", "memory_policy"))):
        sub = persona.get(sec) or {}
        for k in keys:
            v = sub.get(k)
            if isinstance(v, str):
                total += est(v)
    for sec, keys in (("core", ("traits", "capabilities", "boundaries")),
                      ("style", ("habits",))):
        sub = persona.get(sec) or {}
        for k in keys:
            v = sub.get(k)
            if isinstance(v, list):
                total += sum(est(x) for x in v if isinstance(x, str))
    return total


def _group_of(comment: str) -> str:
    """按 comment 前缀分组：[mvu] / [initvar] / 其他。"""
    for name, rx in _GROUP_RULES:
        if rx.search(comment or ""):
            return name
    return "其他"


def _collect_unlayered(lore_dir: Path) -> dict:
    """收集未分层条目（enabled 且 layer 缺失/为空）：文件名、comment、estimated_tokens、原卡 constant。"""
    rows = []
    for f in sorted(lore_dir.glob("*.yaml")):
        d = _mini_yaml.parse(f.read_text(encoding="utf-8"))
        enabled = bool(_mini_yaml.get(d, "enabled", True))
        if not enabled:
            continue
        layer = (d.get("layer") or "").strip()
        if layer:
            continue
        der = d.get("_derived") or {}
        rows.append({
            "file": f.name,
            "comment": der.get("comment") or "",
            "tokens": est(der.get("content") or ""),
            "constant": bool(der.get("constant", False)),
            "insertion_order": int(der.get("insertion_order", 100) or 100),
            "id": der.get("_entry_id"),
        })
    # 分组：先按 comment 前缀，组内按 insertion_order（次按 id）
    groups = {}
    for r in rows:
        groups.setdefault(_group_of(r["comment"]), []).append(r)
    for g in groups.values():
        g.sort(key=lambda r: (r["insertion_order"], r["id"] if r["id"] is not None else 0))
    return groups


def _print_unlayered(groups: dict) -> int:
    """只输出未分层工作清单，退出 0。"""
    total = sum(len(v) for v in groups.values())
    print(f"== 未分层条目工作清单（共 {total} 条）：请逐条判定 layer == identity | behavior | narrative ==")
    print("   判据：这条信息会不会改变她在任意一句话里的反应方式？会 → identity/behavior（编译进 Persona）；不会 → narrative（进知识库检索）。")
    for name in ("mvu", "initvar", "其他"):
        rows = groups.get(name, [])
        if not rows:
            continue
        print(f"\n── [{name}]组（{len(rows)} 条）──")
        print(f"  {'文件':<44}{'tok':<7}{'constant':<10}comment")
        for r in rows:
            print(f"  {r['file'][:42]:<44}{r['tokens']:<7}{'真' if r['constant'] else '假':<10}{r['comment'][:38]}")
    return 0


def _asset_config(asset_dir: Path) -> dict:
    """读取资产目录下的 validation.yaml（私有仓库持有阈值/默认动作，工具不内置）。

    工具对任何卡都中性：预算等阈值属于具体资产/项目的决策，不进本仓库。
    """
    cfg_path = asset_dir / "validation.yaml"
    if not cfg_path.is_file():
        return {}
    return _mini_yaml.parse(cfg_path.read_text(encoding="utf-8"))


def _lore_gates(asset_dir: Path, errors: list, warnings: list, infos: list,
                budget_tokens, strict: bool, level: str = "platform") -> tuple:
    """逐条检查 lore/*.yaml 的路由门禁；返回 (检查数, resident_tokens)。

    resident_tokens = persona 常驻部分 estimated_tokens
                    + Σ estimated_tokens(enabled 且 layer ∈ {identity, behavior})
    constant 不参与这个计算。
    budget_tokens：None = 未配置，跳过预算门禁（阈值由资产目录 validation.yaml
    或 --budget-tokens 提供，工具不内置默认值）。
    """
    lore_dir = asset_dir / "lore"
    if not lore_dir.is_dir():
        return 0, 0
    checked = 0
    resident = 0
    persona = _mini_yaml.parse((asset_dir / "persona.yaml").read_text(encoding="utf-8")) \
        if (asset_dir / "persona.yaml").is_file() else {}
    resident += _persona_standing_tokens(persona)
    unlayered_groups = _collect_unlayered(lore_dir)

    for f in sorted(lore_dir.glob("*.yaml")):
        d = _mini_yaml.parse(f.read_text(encoding="utf-8"))
        checked += 1
        label = f"{f.parent.name}/{f.name}"
        keys = [str(k) for k in (d.get("keys") or [])]
        keys_nonempty = [k for k in keys if k.strip()]
        layer = (d.get("layer") or "").strip()
        enabled = bool(_mini_yaml.get(d, "enabled", True))
        disabled = bool(_mini_yaml.get(d, "disabled", False))
        selective = bool(_mini_yaml.get(d, "selective", False))
        secondary = [str(k) for k in (_mini_yaml.get(d, "secondary_keys") or [])]
        note = d.get("note") or ""
        inject_at = _mini_yaml.get(d, "inject_at", "")
        orphaned = bool(_mini_yaml.get(d, "_orphaned", False))
        comment = _mini_yaml.get(d, "comment", "") or ""
        content = _mini_yaml.get(d, "content", "") or ""

        # 1) layer 缺失/为空=未分层：仅 platform 级别报错
        # 2) layer 不在词表 → 错误（承重字段）
        if enabled and layer and layer not in LAYERS:
            errors.append(f"lore layer 取值非法：{label} —— {layer!r}（应为 identity/behavior/narrative）")
        # 3) 常驻预算：resident_tokens
        if enabled and layer in ("identity", "behavior"):
            resident += est(content)
        # 4) disabled 处置（仅 platform 级别要求）
        if level == "platform" and disabled:
            default_note = _note_of(_mini_yaml.get(d, "comment", "") or "",
                                    bool(_mini_yaml.get(d, "constant", False)),
                                    selective, enabled)
            if not note.strip() or note.strip() == default_note.strip():
                errors.append(
                    f"lore disabled 未处置：{label} —— disabled=true 且 note 为空或仍是机器默认值；"
                    f"请写下处置去向（保留/丢弃/待定 + 理由），机器默认值不算数"
                )
        # 5) narrative 过短无标题 → 警告（知识库检索命中概率低）
        if enabled and layer == "narrative" and not comment.strip() and len(content) < 40:
            warnings.append(
                f"lore narrative 过短无标题：{label} —— 知识库文档无 comment 且内容 < 40 字，检索命中概率低；"
                "请补标题性 comment 或合并到相近条目"
            )
        # 6) keys 过泛（不承重，仅提示）
        for k in keys_nonempty:
            stripped = k.strip()
            if stripped in _OVER_BROAD_REGEX or len(stripped) <= 2:
                warnings.append(
                    f"lore keys 可能过泛：{label} —— 键 {k!r}（单字/过短/全匹配正则）；"
                    "若为专有名词可忽略，否则收紧"
                )
        # 7) inject_at 词表（仅 compile_card 消费）
        if inject_at and inject_at not in INJECT_ATS:
            warnings.append(f"lore inject_at 取值未知：{label} —— {inject_at!r}（应为 resident/context_head/near_input）")
        # 8) selective 信息（编译层归一化）
        if enabled and selective and not secondary:
            infos.append(
                f"lore selective 归一化提示：{label} —— selective=true 且 secondary_keys 为空"
                "（ST 空排除=无操作）；资产层保持原卡透传，compile_card.py 将按 selective=false 归一化"
            )
        # 9) _orphaned 孤立条目（上游卡已删）
        if orphaned:
            (errors if strict else warnings).append(
                f"lore 孤立条目（上游卡已删除）：{label} —— 请在 note 写处置去向（保留/丢弃/待定 + 理由）；"
                "机器不动它，但强制人做决定"
            )

    # 未分层检查：仅 platform 级别报错
    if level == "platform" and unlayered_groups:
        n = sum(len(v) for v in unlayered_groups.values())
        errors.append(
            f"lore 路由未分层：{n} 条 enabled 条目缺 layer（见文件末尾工作清单；"
            "逐条判 identity/behavior/narrative 即可，比编造触发键便宜得多）"
        )
    # 预算检查：仅 platform 级别报错
    if level == "platform" and budget_tokens is not None and resident > budget_tokens:
        errors.append(
            f"常驻预算超限：resident_tokens（persona 常驻 + enabled 且 layer ∈ {{identity, behavior}} 层）"
            f"共约 {resident} estimated_tokens > 预算 {budget_tokens}"
            "（阈值来自资产目录 validation.yaml，可用 --budget-tokens 覆盖；常驻层占用注意力，是压缩的直接理由）"
        )
    elif budget_tokens is None:
        infos.append("未配置常驻预算（资产目录无 validation.yaml 且未传 --budget-tokens），跳过预算门禁")
    return checked, resident


def validate(asset_dir: Path, budget_tokens=None, strict: bool = False,
             level: str = "platform", report_unlayered: bool = False) -> int:
    errors = []
    warnings = []
    infos = []
    persona_path = asset_dir / "persona.yaml"
    prov_md = asset_dir / "provenance.md"

    if not persona_path.is_file():
        print(f"✗ 缺少 persona.yaml：{persona_path}")
        return 1
    if report_unlayered:
        lore_dir = asset_dir / "lore"
        if not lore_dir.is_dir():
            print("（无 lore 目录，没有未分层条目）")
            return 0
        return _print_unlayered(_collect_unlayered(lore_dir))
    if not prov_md.is_file():
        errors.append(f"缺少 provenance.md：{prov_md}")

    lines = persona_path.read_text(encoding="utf-8").splitlines()

    # A1) 必需字段
    for top, subs in REQUIRED.items():
        if not any(re.match(rf"^{re.escape(top)}:", ln) for ln in lines):
            errors.append(f"persona.yaml 缺少顶层字段：{top}")
            continue
        if subs:
            for sub in subs:
                if not any(re.match(rf"^  {re.escape(sub)}:", ln) for ln in lines):
                    errors.append(f"persona.yaml 缺少 {top}.{sub}")

    # A2) 语义补全门禁（仅 platform 级别检查）
    if level == "platform":
        for (top, sub), label in NON_EMPTY.items():
            line = next((ln for ln in lines if re.match(rf"^  {re.escape(sub)}:", ln)), None)
            if line is None:
                errors.append(f"缺少 {top}.{sub}")
            elif not _scalar(line):
                errors.append(f"资产未完成语义补全：{label}（当前为空占位，请人工/LLM 填写）")

    # A3) refs 文件存在性
    for key in ("lore_refs", "dialogs_refs"):
        for ref in _refs(lines, key):
            if not (asset_dir / ref).is_file():
                errors.append(f"{key} 指向的文件不存在：{ref}")
    sample_line = next((ln for ln in lines if re.match(r"^  sample:", ln)), None)
    if sample_line:
        sample = _scalar(sample_line)
        if sample and not (asset_dir / sample).is_file():
            errors.append(f"style.sample 指向的文件不存在：{sample}")

    # A4) sha 一致性
    hash_sha = next((_scalar(ln) for ln in lines if re.match(r"^    hash_sha256:", ln)), None)
    sha = next((_scalar(ln) for ln in lines if re.match(r"^  sha256:", ln)), None)
    if hash_sha and sha and hash_sha != sha:
        errors.append("identity.provenance.hash_sha256 与 provenance.sha256 不一致")
    if prov_md.is_file():
        md = prov_md.read_text(encoding="utf-8")
        if sha and sha not in md:
            errors.append("provenance.md 未记录 persona.provenance.sha256")

    # B) lore 路由门禁
    checked, resident = _lore_gates(asset_dir, errors, warnings, infos, budget_tokens, strict, level)

    for i in infos:
        print(f"ℹ {i}")
    for w in warnings:
        print(f"⚠ {w}")
    if errors:
        for e in errors:
            print(f"✗ {e}")
        # 未分层工作清单（如果有）：分组打印
        unlayered = _collect_unlayered(asset_dir / "lore")
        if unlayered:
            print()
            _print_unlayered(unlayered)
        return 1

    n_lore = len(_refs(lines, "lore_refs"))
    print(f"✓ 资产校验通过：{asset_dir}（persona 语义补全完成、{n_lore} 个 lore 引用、"
          f"lore/{checked} 个模块已检、resident_tokens 约 {resident} estimated_tokens、sha 一致"
          + ("、无警告" if not warnings else f"、{len(warnings)} 条警告")
          + (f"、{len(infos)} 条信息" if infos else "") + "）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="机器校验角色资产（persona + lore 路由门禁；layer 是唯一路由载体）")
    ap.add_argument("asset_dir", help="资产目录（含 persona.yaml 与 lore/）")
    ap.add_argument("--budget-tokens", type=int, default=None,
                    help="常驻预算上限（覆盖 validation.yaml；不传且无配置则跳过预算门禁）")
    ap.add_argument("--strict", action="store_true",
                    help="把警告升级为错误（keys 过泛 / 词表外取值 / narrative 过短 / 孤立条目）")
    ap.add_argument("--level", choices=["archival", "platform"], default="platform",
                    help="验证级别：archival=归档级（只检查机器字段），platform=平台级（检查所有字段，默认）")
    ap.add_argument("--report-unlayered", action="store_true",
                    help="只输出未分层条目工作清单并退出 0，不做其他检查")
    args = ap.parse_args()
    asset_dir = Path(args.asset_dir)
    if args.budget_tokens is None:
        # 阈值属私有仓库决策：优先资产目录 validation.yaml，工具不内置默认值
        args.budget_tokens = _asset_config(asset_dir).get("budget_tokens")
    return validate(asset_dir, budget_tokens=args.budget_tokens,
                    strict=args.strict, level=args.level, report_unlayered=args.report_unlayered)


if __name__ == "__main__":
    raise SystemExit(main())
