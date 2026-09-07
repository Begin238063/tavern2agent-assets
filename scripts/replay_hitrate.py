#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""replay_hitrate.py —— 把每条 lore 的 keys 在真实聊天历史上回放，统计命中率与每轮注入开销。

用法：
    python3 scripts/replay_hitrate.py --lore characters/<角色名>/lore --chats 聊天记录.txt
    python3 scripts/replay_hitrate.py --lore ... --chats ... --regex          # keys 按正则匹配
    python3 scripts/replay_hitrate.py --lore ... --chats ... --default-window 8   # 覆盖 validation.yaml；两者皆无则报错（工具不内置阈值）

模拟方式（关键）：
    · 按消息逐轮滑窗：条目在某一轮激活当且仅当它的某个 key 出现在「最近 window 条消息」里；
      window = 原卡 scan_depth（条数），scan_depth 为 null 时用 --default-window（运行时全局默认未知时的假设，需人工校准）；
    · 常驻（constant=true 且 enabled）每轮都激活；disabled 永不激活；
    · 每轮开销 = Σ(激活条目 estimated_tokens)，输出 p50/p90/p99 分布——预算爆不爆看尾部，不是均值；
    · 不再用「全量文本一次匹配」，避免命中率系统性高估。
token 估算口径（与 validate 一致）：中文 1 字符≈1 token，ASCII 4 字符≈1 token（estimated_tokens）。

聊天记录格式：每行一条消息；若某行是 JSON 对象且含 content/message/text/msg/消息 字段（字符串），取该字段，否则整行。

三个关键数：
    ① 命中率 0 的条目：keys 写错，或该概念在真实聊天里根本不出现（无效资产）；
    ② 命中率 > 50% 的触发条目：实质上已常驻却不在常驻预算里；
    ③ 每轮设定开销分布（均值 + p50/p90/p99）：这就是每轮真实的设定成本曲线。
"""

import argparse
import json
import re
from pathlib import Path

import _mini_yaml

_TEXT_KEYS = ("content", "message", "text", "msg", "消息", "内容")


def _asset_config(asset_dir: Path) -> dict:
    """读取资产目录下的 validation.yaml（私有仓库持有阈值/默认动作，工具不内置）。"""
    cfg_path = asset_dir / "validation.yaml"
    if not cfg_path.is_file():
        return {}
    return _mini_yaml.parse(cfg_path.read_text(encoding="utf-8"))


def _messages(path: Path) -> list:
    """读聊天记录：每行一条消息；JSON 行取消息字段，否则整行。"""
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                for k in _TEXT_KEYS:
                    if isinstance(obj.get(k), str) and obj[k].strip():
                        line = obj[k]
                        break
            except Exception:
                pass
        out.append(line)
    return out


def _match(message: str, key: str, use_regex: bool) -> bool:
    if use_regex:
        try:
            return re.search(key, message, re.I) is not None
        except re.error:
            return key.lower() in message.lower()
    return key.lower() in message.lower()


def _percentile(sorted_vals: list, p: float):
    """有序列表的百分位数（线性插值，p 为 0-100）。"""
    if not sorted_vals:
        return 0
    pos = (len(sorted_vals) - 1) * p / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def main() -> int:
    ap = argparse.ArgumentParser(description="把 lore keys 在真实聊天历史上滑窗回放，统计命中率与开销分布")
    ap.add_argument("--lore", required=True, help="lore 目录（每条一个 yaml）")
    ap.add_argument("--chats", required=True, help="聊天记录文本（每行一条消息）")
    ap.add_argument("--regex", action="store_true", help="keys 按正则匹配（原卡 use_regex=true 时用）")
    ap.add_argument("--default-window", type=int, default=None,
                    help="scan_depth 为 null 时的回溯消息数（覆盖资产目录 validation.yaml 的 default_window；两者皆无则报错——这是运行时假设，工具不内置）")
    ap.add_argument("--budget-tokens", type=int, default=None,
                    help="常驻预算（仅用于尾部提示；优先 validation.yaml，缺省不比较）")
    args = ap.parse_args()

    lore_dir = Path(args.lore)
    cfg = _asset_config(lore_dir.parent)  # 阈值/默认动作归私有仓库，工具不内置
    if args.default_window is None:
        args.default_window = cfg.get("default_window")
    if args.budget_tokens is None:
        args.budget_tokens = cfg.get("budget_tokens")
    if not isinstance(args.default_window, int) or args.default_window < 1:
        print("✗ 回放窗口未配置：请传 --default-window，或在该资产目录放 validation.yaml（default_window: N）"
              "；这是运行时假设，不应由工具内置")
        return 1

    chats = _messages(Path(args.chats))
    if not chats:
        print("✗ 聊天记录为空或无法读取")
        return 1

    est = _mini_yaml.estimated_tokens
    rows = []  # (name, keys, tokens, layer, enabled, window)
    unlayered = 0
    for f in sorted(lore_dir.glob("*.yaml")):
        d = _mini_yaml.parse(f.read_text(encoding="utf-8"))
        keys = [str(k) for k in (d.get("keys") or []) if str(k).strip()]
        tokens = est(_mini_yaml.get(d, "content", "") or "")
        layer = (d.get("layer") or "").strip()
        enabled = bool(_mini_yaml.get(d, "enabled", True))
        if enabled and not layer:
            unlayered += 1
        sd = _mini_yaml.get(d, "scan_depth", None)
        window = sd if isinstance(sd, int) and sd > 0 else args.default_window
        rows.append((f.name, keys, tokens, layer, enabled, window))

    n = len(chats)
    active_count = [0] * len(rows)
    last_seen = [-10 ** 9] * len(rows)
    costs = [0] * n

    for i, msg in enumerate(chats):
        turn_cost = 0
        for e, (name, keys, tokens, layer, enabled, window) in enumerate(rows):
            if not enabled or not layer:
                continue
            if layer in ("identity", "behavior"):
                active = True           # 编译进 Persona：每轮常驻
            else:
                if any(_match(msg, k, args.regex) for k in keys):
                    last_seen[e] = i
                active = last_seen[e] >= i - window + 1   # narrative：知识库滑窗检索
            if active:
                active_count[e] += 1
                turn_cost += tokens
        costs[i] = turn_cost

    print(f"== 命中率回放：{len(rows)} 条 lore × {n} 条消息（滑窗；scan_depth=null 按 {args.default_window} 条）=="
          + (f"；未分层跳过 {unlayered} 条" if unlayered else ""))
    print(f"{'条目':<46}{'keys':<14}{'命中':<7}{'tok':<7}{'率':<8}{'类型'}")
    for (name, keys, tokens, layer, enabled, window), cnt in zip(rows, active_count):
        tag = (layer if layer in ("identity", "behavior", "narrative") else "未分层") \
            if enabled else "禁用"
        print(f"{name[:44]:<46}{'、'.join(keys)[:12]:<14}{cnt:<7}{tokens:<7}{cnt / n * 100:>6.1f}%{tag}")

    sorted_costs = sorted(costs)
    mean = sum(costs) / n
    p50 = _percentile(sorted_costs, 50)
    p90 = _percentile(sorted_costs, 90)
    p99 = _percentile(sorted_costs, 99)
    standing = sum(r[2] for r in rows if r[4] and r[3] in ("identity", "behavior"))
    zero = [r for r, c in zip(rows, active_count) if c == 0 and r[4] and r[3] == "narrative"]
    hot = [r for r, c in zip(rows, active_count) if r[4] and r[3] == "narrative" and c / n > 0.5]

    print("\n—— 三个关键数 ——")
    print(f"① 命中率 0 的 narrative 条目：{len(zero)} 条"
          + ("" if not zero else "（" + "、".join(r[0][:12] for r in zero[:5]) + "…）"))
    for r in zero[:20]:
        print(f"   · {r[0]}: keys 写错或该概念在真实聊天中不出现（无效资产）")
    print(f"② 命中率 > 50% 的 narrative 条目：{len(hot)} 条（实质上已常驻，建议改 layer: identity/behavior 并计入预算，或收紧 keys）")
    for r in hot[:20]:
        rate = active_count[rows.index(r)] / n
        print(f"   · {r[0]}: 命中 {rate * 100:.0f}%（estimated_tokens {r[2]}）")
    print(f"③ 每轮设定开销分布（estimated_tokens）：Persona 常驻恒定 {standing}；均值 {mean:.0f}"
          f" / p50 {p50:.0f} / p90 {p90:.0f} / p99 {p99:.0f}"
          + (f"（p90 > 预算 {args.budget_tokens}，尾部会爆）" if args.budget_tokens and p90 > args.budget_tokens else ""))
    print("   （预算是否超看尾部 p90/p99；均值只代表一般轮次）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
