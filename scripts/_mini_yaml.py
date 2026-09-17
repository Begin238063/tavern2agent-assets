#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_mini_yaml.py —— 本工具族共享的极简 YAML 子集解析器（纯标准库）。

只解析 build_assets.py 产出的固定格式资产文件：
  - 顶层键值对（缩进 0）；
  - 一层嵌套字典（如 _derived，缩进 2）；
  - 字符串序列（缩进 2 / 4 的 `- item`，或空列表 `[]`）；
  - 块标量（`key: |-`，正文比 key 多缩进 2）；
  - 行内注释（值后 ` # ...`，带引号的值只在引号闭合后切分）。

不做通用 YAML；解析失败时返回空 dict，调用方自行处理。
"""

import re


def _split_inline(s: str):
    """把 `value  # 注释` 拆成 (value, 注释)。带引号的值只在引号闭合后切注释。"""
    s = s.strip()
    if s.startswith('"'):
        i = 1
        while i < len(s):
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == '"':
                return s[: i + 1], s[i + 1 :]
            i += 1
        return s, ""
    if s.startswith("'"):
        i = 1
        while i < len(s):
            if s[i] == "'":
                return s[: i + 1], s[i + 1 :]
            i += 1
        return s, ""
    idx = s.find(" #")
    if idx >= 0:
        return s[:idx], s[idx:]
    return s, ""


def _scalar_of(v: str):
    """把 YAML 标量字符串解析为 Python 值（引号 / 布尔 / 整数 / null）。"""
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1]
    if v in ("null", "~"):
        return None
    if v == "true":
        return True
    if v == "false":
        return False
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def _parse_level(lines, start, indent):
    """解析从 start 开始、缩进恰为 indent 的键值序列，返回 (dict, next_index)。"""
    d = {}
    i = start
    n = len(lines)
    while i < n:
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        cur = len(raw) - len(raw.lstrip(" "))
        if cur < indent:
            break
        if cur > indent:
            i += 1
            continue
        m = re.match(r"^(\S+):(.*)$", raw.strip())
        if not m:
            i += 1
            continue
        key, rest = m.group(1), m.group(2).strip()
        # 前瞻必须跳过空行与注释行：`key:` 后面紧跟一个空行或注释行时若不跳过，
        # 会落到下面「空值」分支，静默吞掉 keys / layer 这类人工字段 —— 而空行在
        # YAML 里是完全正常的写法（手工编辑资产极易触发）。
        k = i + 1
        while k < n and (not lines[k].strip() or lines[k].lstrip().startswith("#")):
            k += 1
        nxt = lines[k] if k < n else ""
        nxt_s = nxt.strip()
        nxt_i = len(nxt) - len(nxt.lstrip(" ")) if nxt else 0
        if rest == "":
            if nxt_s == "[]":
                d[key] = []
                i = k + 1
                continue
            if nxt_s.startswith("-"):
                lst = []
                j = k
                while j < n:
                    l2 = lines[j]
                    if not l2.strip() or l2.lstrip().startswith("#"):
                        j += 1
                        continue
                    li = len(l2) - len(l2.lstrip(" "))
                    if li <= indent:
                        break
                    mm = re.match(r"^\s*-\s?(.*)$", l2)
                    if mm:
                        lst.append(_scalar_of(_split_inline(mm.group(1))[0]))
                    j += 1
                d[key] = lst
                i = j
                continue
            if nxt_s and nxt_i > indent and re.match(r"^\S+:", nxt_s):
                sub, j = _parse_level(lines, k, nxt_i)
                d[key] = sub
                i = j
                continue
            d[key] = ""
            i += 1
            continue
        if rest == "|-":
            body_indent = indent + 2
            body = []
            j = i + 1
            while j < n:
                l2 = lines[j]
                if l2.strip() == "":
                    body.append("")
                    j += 1
                    continue
                li = len(l2) - len(l2.lstrip(" "))
                if li <= indent:
                    break
                body.append(l2[body_indent:] if len(l2) >= body_indent else "")
                j += 1
            d[key] = "\n".join(body).rstrip("\n")
            i = j
            continue
        d[key] = _scalar_of(_split_inline(rest)[0])
        i += 1
    return d, i


def parse(text: str) -> dict:
    """把本工具产出的资产 YAML 文本解析为 dict。解析失败返回空 dict。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    try:
        d, _ = _parse_level(lines, 0, 0)
        return d
    except Exception:
        return {}


def get(d: dict, key: str, default=None):
    """取值：优先顶层，其次 _derived 块（兼容新旧两种文件格式）。"""
    if key in d:
        return d[key]
    sub = d.get("_derived")
    if isinstance(sub, dict) and key in sub:
        return sub[key]
    return default


# ─── 共享估算工具 ───────────────────────────────────────────────

def estimated_tokens(text) -> int:
    """token 估算（纯标准库，无真 tokenizer）：中文/全角字符 1 字符 ≈ 1 token，
    ASCII 等其它字符每 4 字符 ≈ 1 token。口径统一给 validate / replay 使用。

    校准说明：拿到一次线上真实请求用量后，把实测比例记入 SKILL.md 的「估算口径」；
    在数量级大（数万）时结论不受影响，压到千级时必须按校准比例复核。
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        o = ord(ch)
        if (0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
                or 0xFE30 <= o <= 0xFE4F or 0xFF00 <= o <= 0xFFEF):
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4
