#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_mini_yaml.py —— 共享解析器的行为锁（纯标准库 + unittest）。

为什么这个文件值得存在：`parse()` 解析失败时**静默返回空 dict**（见其 docstring）。
这意味着任何让解析退化的改动都不会以异常的形式暴露，而是让所有下游检查
「没看到字段 → 视为缺省 → 通过」。validate_assets.py 会假通过，compile_card.py
会编出空卡。这类失败没有堆栈、没有日志，只有结果悄悄不对。

所以这里锁两件事：
  1. **能解析的格式**：build_assets.py 实际产出的每种构造（块标量、列表、空列表、
     嵌套字典、行内注释、带引号的值、null/bool/int）都逐一断言；
  2. **静默失败的边界**：明确哪些输入返回 {} 或跳过，避免有人「顺手改一下正则」
     就把某类合法资产变成空 dict。

运行：
    python3 -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _mini_yaml  # noqa: E402


class TestScalars(unittest.TestCase):
    """标量解析：类型判定错了会让 enabled/disabled 一类布尔门禁全面失效。"""

    def test_types(self):
        d = _mini_yaml.parse(
            'a: "带引号"\n'
            "b: 裸字符串\n"
            "c: true\n"
            "d: false\n"
            "e: 42\n"
            "f: -7\n"
            "g: 1.5\n"
            "h: null\n"
            "i: ~\n"
        )
        self.assertEqual(d["a"], "带引号")
        self.assertEqual(d["b"], "裸字符串")
        self.assertIs(d["c"], True)
        self.assertIs(d["d"], False)
        self.assertEqual(d["e"], 42)
        self.assertEqual(d["f"], -7)
        self.assertEqual(d["g"], 1.5)
        self.assertIsNone(d["h"])
        self.assertIsNone(d["i"])

    def test_empty_value_is_empty_string(self):
        """`layer:` 无值 → ""。这是「未分层」的机器表示，门禁靠它判定。"""
        d = _mini_yaml.parse("layer:\nnote: x\n")
        self.assertEqual(d["layer"], "")
        self.assertEqual(d["note"], "x")

    def test_inline_comment_stripped(self):
        d = _mini_yaml.parse("k: v  # 这是注释\n")
        self.assertEqual(d["k"], "v")

    def test_hash_inside_quotes_kept(self):
        """带引号的值只在引号闭合后切注释——否则含 # 的正文会被截断。"""
        d = _mini_yaml.parse('k: "a # b"  # 真注释\n')
        self.assertEqual(d["k"], "a # b")

    def test_escaped_quote_in_value(self):
        d = _mini_yaml.parse('k: "她说\\"好\\""\n')
        self.assertEqual(d["k"], '她说"好"')


class TestLists(unittest.TestCase):
    def test_string_list(self):
        d = _mini_yaml.parse('keys:\n  - "悲鸣"\n  - "海蚀"\nnote: x\n')
        self.assertEqual(d["keys"], ["悲鸣", "海蚀"])
        self.assertEqual(d["note"], "x")

    def test_empty_list(self):
        """`[]` 必须是空列表而不是字符串 "[]"——否则 `or []` 兜不住，len() 变 2。"""
        d = _mini_yaml.parse("keys:\n  []\nnote: x\n")
        self.assertEqual(d["keys"], [])
        self.assertEqual(d["note"], "x")

    def test_list_then_next_toplevel_key(self):
        """列表后紧跟顶层键：列表不能把后面的键吞掉。"""
        d = _mini_yaml.parse('a:\n  - "x"\nb: 1\n')
        self.assertEqual(d["a"], ["x"])
        self.assertEqual(d["b"], 1)


class TestBlockScalar(unittest.TestCase):
    """块标量承载 lore 正文与 persona.core.self —— 内容错了预算和知识库全错。"""

    def test_multiline_content(self):
        d = _mini_yaml.parse("content: |-\n  第一行\n  第二行\nnote: x\n")
        self.assertEqual(d["content"], "第一行\n第二行")
        self.assertEqual(d["note"], "x")

    def test_blank_line_inside_block(self):
        d = _mini_yaml.parse("content: |-\n  上\n\n  下\nnote: x\n")
        self.assertEqual(d["content"], "上\n\n下")

    def test_deeper_indent_preserved(self):
        """正文自身的缩进（YAML 列表、代码）必须原样保留。"""
        d = _mini_yaml.parse("content: |-\n  trust: 0\n    nested: 1\nnote: x\n")
        self.assertEqual(d["content"], "trust: 0\n  nested: 1")

    def test_hash_line_in_block_not_comment(self):
        """块标量里的 `# 标题` 是 markdown 正文，不是注释。"""
        d = _mini_yaml.parse("content: |-\n  # 标题\n  正文\nnote: x\n")
        self.assertEqual(d["content"], "# 标题\n正文")

    def test_nested_block_scalar(self):
        """_derived 块内的块标量（真实资产的形状）。"""
        d = _mini_yaml.parse(
            "_derived:\n"
            "  comment: \"[initvar]\"\n"
            "  content: |-\n"
            "    甲\n"
            "    乙\n"
            "  enabled: true\n"
            "layer: narrative\n"
        )
        self.assertEqual(d["_derived"]["content"], "甲\n乙")
        self.assertEqual(d["_derived"]["comment"], "[initvar]")
        self.assertIs(d["_derived"]["enabled"], True)
        self.assertEqual(d["layer"], "narrative")


class TestNesting(unittest.TestCase):
    def test_derived_block_and_outer_fields(self):
        """块内机器字段 / 块外人工字段的划分是 --migrate 的基础，必须解析对。"""
        d = _mini_yaml.parse(
            "id: entry_00\n"
            "_derived:\n"
            "  constant: true\n"
            "  insertion_order: 100\n"
            "  scan_depth: null\n"
            "keys:\n"
            '  - "人工键"\n'
            "layer: behavior\n"
            "budget_tokens: null\n"
        )
        self.assertEqual(d["id"], "entry_00")
        self.assertIs(d["_derived"]["constant"], True)
        self.assertEqual(d["_derived"]["insertion_order"], 100)
        self.assertIsNone(d["_derived"]["scan_depth"])
        self.assertEqual(d["keys"], ["人工键"])
        self.assertEqual(d["layer"], "behavior")
        self.assertIsNone(d["budget_tokens"])

    def test_two_level_persona_shape(self):
        d = _mini_yaml.parse(
            "identity:\n"
            '  name: "测试员"\n'
            "  provenance:\n"
            '    hash_sha256: "abc"\n'
            "core:\n"
            "  traits:\n"
            '    - "随和"\n'
        )
        self.assertEqual(d["identity"]["name"], "测试员")
        self.assertEqual(d["core"]["traits"], ["随和"])


class TestGet(unittest.TestCase):
    """get() 的顶层优先 / _derived 回退 —— 兼容新旧两种文件格式。"""

    def test_toplevel_wins(self):
        d = {"enabled": False, "_derived": {"enabled": True}}
        self.assertIs(_mini_yaml.get(d, "enabled"), False)

    def test_falls_back_into_derived(self):
        d = {"_derived": {"enabled": True}}
        self.assertIs(_mini_yaml.get(d, "enabled"), True)

    def test_default_when_absent(self):
        self.assertEqual(_mini_yaml.get({}, "enabled", "缺省"), "缺省")

    def test_derived_not_a_dict(self):
        """_derived 被写坏成标量时不能抛，否则整条校验链崩在这里。"""
        self.assertEqual(_mini_yaml.get({"_derived": "坏了"}, "enabled", 7), 7)

    def test_falsy_toplevel_value_not_skipped(self):
        """顶层值为 False/""/0 时仍应命中顶层，不能回退到 _derived。"""
        self.assertIs(_mini_yaml.get({"enabled": False, "_derived": {"enabled": True}},
                                     "enabled"), False)
        self.assertEqual(_mini_yaml.get({"layer": "", "_derived": {"layer": "identity"}},
                                        "layer"), "")


class TestSilentFailureBoundary(unittest.TestCase):
    """静默失败的边界。

    parse() 吞异常返回 {} 是有意的（调用方自行处理），但**范围必须可预期**：
    下面这些输入是真实资产里可能出现的噪声，它们不该让整份文件退化成 {}。
    """

    def test_empty_text(self):
        self.assertEqual(_mini_yaml.parse(""), {})

    def test_crlf_normalised(self):
        """Windows 上写出的资产是 CRLF——不归一化会让每个值尾部挂个 \\r。"""
        d = _mini_yaml.parse('a: "x"\r\nb: |-\r\n  行一\r\n  行二\r\n')
        self.assertEqual(d["a"], "x")
        self.assertEqual(d["b"], "行一\n行二")

    def test_leading_comments_and_blank_lines(self):
        d = _mini_yaml.parse("# 头部注释\n\n# 又一行\na: 1\n")
        self.assertEqual(d["a"], 1)

    def test_document_marker_line_skipped(self):
        """`---` 不是 key: value，跳过它而不是整份失败。"""
        d = _mini_yaml.parse("---\na: 1\n")
        self.assertEqual(d["a"], 1)

    def test_garbage_line_does_not_nuke_file(self):
        """单行垃圾只该被跳过；其余键必须照常拿到。"""
        d = _mini_yaml.parse("a: 1\n这是一行没有冒号的垃圾\nb: 2\n")
        self.assertEqual(d.get("a"), 1)
        self.assertEqual(d.get("b"), 2)

    def test_key_with_space_is_skipped_not_fatal(self):
        """`^(\\S+):` 不匹配含空格的键；该行跳过，其余仍解析。"""
        d = _mini_yaml.parse("a: 1\nbad key: 2\nb: 3\n")
        self.assertEqual(d.get("a"), 1)
        self.assertEqual(d.get("b"), 3)
        self.assertNotIn("bad key", d)


if __name__ == "__main__":
    unittest.main()
