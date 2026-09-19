#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_conditions.py —— EJS 条件门控的解析（纯标准库，本工具族共享）。

立场（与 macros.py 一致）：**资产层忠实于卡** —— EJS 原样留在 lore 的 `content` 里，
不拆资产文件、不改 schema。本模块只把「条件门控块」解析成结构，供**编译层**按平台归一化：

  · ST 自己会渲染 EJS，`compile_card.py` 原样带走即无损 —— 不需要本模块；
  · AstrBot 没有渲染器，必须把门控变成结构化元数据，否则原始代码会进提示词
    （实测：75/461 篇知识库文档 + system_prompt 里 25 处原始 EJS）。

实测来源（WuWa Solaris-3 MVU Edition）：466 处 `if (getvar('stat_data.剧情权重') > N)`，
另有 `} else {`、`} else if (...)`、复合表达式、`if (false)` 死代码、JS 辅助函数声明。

不猜、不删、不静默：认不出的标签原样留在正文里并记入 report；
JS 声明这类纯代码会被消费掉，但同样记入 report；非标准条件表达式原样保留并报告。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 任意 EJS 标签：<% ... %> / <%= ... %> / <%_ ... _%> / <%- ... %>
TAG = re.compile(r"<%(.*?)%>", re.S)
# 取值插值（编译层按宏词表归一化，见私有仓 compiling/macros.py）
OUTPUT_TAG = re.compile(r"<%=.*?%>|<%-.*?%>", re.S)

# 规范形态：getvar('X') > N —— 可归一成平台无关的 `X > N`
_COMPARE = r"(?P<op>>=|<=|==|!=|>|<)\s*(?P<num>-?\d+)"
# 变量名不含引号：否则 `.+?` 会跨引号，把复合表达式误判成标准形态
_GETVAR = r"getvar\(\s*(?P<q>['\"])(?P<var>[^'\"]+)(?P=q)\s*\)"
_NORM_COND = re.compile(rf"^\s*{_GETVAR}\s*{_COMPARE}\s*$", re.S)

# 通用形态：任何 `if (<expr>) {` / `} else if (<expr>) {`
_RE_IF = re.compile(r"^\s*if\s*\((?P<expr>.*)\)\s*\{\s*$", re.S)
_RE_ELSE_IF = re.compile(r"^\s*\}\s*else\s+if\s*\((?P<expr>.*)\)\s*\{\s*$", re.S)
_RE_ELSE = re.compile(r"^\s*\}\s*else\s*\{\s*$", re.S)
_RE_CLOSE = re.compile(r"^\s*\}\s*$", re.S)
# JS 声明/循环：是代码，不是内容
_RE_DECL = re.compile(r"^\s*(const|let|var|function|for|while)\b", re.S)


@dataclass
class ConditionReport:
    """解析结果里值得人看一眼的东西。按 macros.py 的立场：报告，不静默。"""

    conditions: int = 0
    else_branches: int = 0
    nonstandard: dict = field(default_factory=dict)  # 非标准条件表达式 -> 次数
    code_tags: dict = field(default_factory=dict)    # 被消费的 JS 声明 -> 次数
    unresolved: dict = field(default_factory=dict)   # 认不出的标签 -> 次数

    @property
    def has_unresolved(self) -> bool:
        return bool(self.unresolved)

    @property
    def clean(self) -> bool:
        """没有任何需要人判断的东西（插值不算——那是编译层的常规归一化）。"""
        return not (self.unresolved or self.nonstandard)

    def _bump(self, bucket: dict, raw: str) -> None:
        key = " ".join(raw.split())[:90]
        bucket[key] = bucket.get(key, 0) + 1

    def note_nonstandard(self, expr: str) -> None:
        self._bump(self.nonstandard, expr)

    def note_code(self, raw: str) -> None:
        self._bump(self.code_tags, raw)

    def note_unresolved(self, raw: str) -> None:
        self._bump(self.unresolved, raw)

    def merge(self, other: "ConditionReport") -> None:
        self.conditions += other.conditions
        self.else_branches += other.else_branches
        for bucket in ("nonstandard", "code_tags", "unresolved"):
            mine, theirs = getattr(self, bucket), getattr(other, bucket)
            for k, v in theirs.items():
                mine[k] = mine.get(k, 0) + v


@dataclass
class Segment:
    """一段正文，外加它的门控条件（None = 无条件，永远成立）。"""

    condition: str | None
    text: str


def _normalize_condition(expr: str, report: ConditionReport) -> str:
    """把 if 的表达式归一成条件字符串。

    规范形态 `getvar('X') > N` → `X > N`（平台无关）；
    其余（复合表达式、`false` 常量等）**原样保留表达式**并记入 report —— 不猜、不静默。
    """
    expr = " ".join(expr.split())
    m = _NORM_COND.match(expr)
    if m:
        return f"{m.group('var').strip()} {m.group('op')} {m.group('num')}"
    report.note_nonstandard(expr)
    return expr


def _balance(stack: list) -> str | None:
    """把当前打开的条件栈合成一个合取表达式（嵌套时用 and 连接）。"""
    if not stack:
        return None
    return " and ".join(stack)


def _classify(raw: str):
    """判定一个 EJS 标签的角色，返回 (kind, info)。

    kind ∈ {if, else_if, else, close, output, code, unknown}
    """
    s = raw.strip()
    if s.startswith("=") or s.startswith("-"):  # <%= %> / <%- %> 取值插值
        return "output", s[1:].strip()
    s = s.strip("_-").strip()  # 去掉 <%_ … _%> 的空白控制标记
    m = _RE_IF.match(s)
    if m:
        return "if", m.group("expr")
    m = _RE_ELSE_IF.match(s)
    if m:
        return "else_if", m.group("expr")
    if _RE_ELSE.match(s):
        return "else", None
    if _RE_CLOSE.match(s):
        return "close", None
    if _RE_DECL.match(s):  # JS 声明/辅助函数：代码，不是内容
        return "code", s
    return "unknown", s


def split_conditions(content: str, report: ConditionReport | None = None):
    """把 content 切成 [(条件, 正文)]。

    认可的控制标签被「消费」成结构；认不出的标签**原样留在正文里**并记入 report。
    因此 `"".join(seg.text)` == 「原文减去已被消费的控制标签」——没有正文凭空消失。
    """
    rep = report if report is not None else ConditionReport()
    segs: list[Segment] = []
    stack: list = []
    buf: list[str] = []
    pos = 0

    def flush() -> None:
        text = "".join(buf)
        buf.clear()
        if text:  # 只丢真空段；纯空白段要留，否则「散文零丢失」会被悄悄破坏
            segs.append(Segment(_balance(stack), text))

    for m in TAG.finditer(content):
        buf.append(content[pos:m.start()])
        pos = m.end()
        kind, info = _classify(m.group(1))
        if kind == "if":
            flush()
            stack.append(_normalize_condition(info, rep))
            rep.conditions += 1
        elif kind == "else_if":
            flush()
            prev = stack.pop() if stack else ""
            cur = _normalize_condition(info, rep)
            stack.append(f"not({prev}) and {cur}" if prev else cur)
            rep.conditions += 1
        elif kind == "else":
            flush()
            prev = stack.pop() if stack else ""
            stack.append(f"not({prev})" if prev else "")
            rep.else_branches += 1
        elif kind == "close":
            flush()
            if stack:
                stack.pop()
        elif kind == "code":  # JS 声明：纯代码，消费掉但要报告
            rep.note_code(m.group(0))
        else:  # output / unknown：原样保留并报告
            buf.append(m.group(0))
            rep.note_unresolved(m.group(0))
    buf.append(content[pos:])
    flush()

    # 相邻同条件的段合并（减少碎片）
    merged: list[Segment] = []
    for seg in segs:
        if merged and merged[-1].condition == seg.condition:
            merged[-1].text += seg.text
        else:
            merged.append(Segment(seg.condition, seg.text))
    return merged, rep


def render_conditions(segs: list) -> str:
    """split_conditions 的结构级逆运算（供测试使用，不做 getvar 包装还原）。

    连续的条件段还原成一条 `if / else if` 链，无条件段原样输出。
    用它做的往返断言是**结构级**的（条件 + 去标签后的正文一致），不是字节级的。
    """
    out: list[str] = []
    i = 0
    while i < len(segs):
        seg = segs[i]
        if seg.condition is None:
            out.append(seg.text)
            i += 1
            continue
        chain = [seg]
        j = i + 1
        while j < len(segs) and segs[j].condition is not None:
            chain.append(segs[j])
            j += 1
        for k, s in enumerate(chain):
            kw = "if" if k == 0 else "else if"
            out.append(f"<%_ {kw} ({s.condition}) {{ _%>")
            out.append(s.text)
        out.append("<%_ } _%>")
        i = j
    return "".join(out)
