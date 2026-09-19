#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_conditions.py —— EJS 条件门控解析的回归测试（纯标准库 + unittest）。

守护的核心性质按重要性排序：
  1. **散文零丢失**：切分只消费控制标签，正文一个字符都不能少；
  2. 条件归属正确（if / else / else if / 嵌套合取）；
  3. **不猜、不删、不静默**：非标准表达式、JS 声明、取值插值都要被报告。

运行： python3 -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _conditions as C  # noqa: E402


def _pairs(content):
    segs, rep = C.split_conditions(content)
    return [(s.condition, s.text) for s in segs], rep


class TestSplit(unittest.TestCase):
    def test_无ejs时原样一段(self):
        pairs, rep = _pairs("就是普通正文")
        self.assertEqual(pairs, [(None, "就是普通正文")])
        self.assertTrue(rep.clean)
        self.assertEqual(rep.conditions, 0)

    def test_单个if与尾部无条件段(self):
        pairs, rep = _pairs("<%_ if (getvar('剧情权重') > 1) { _%>甲<%_ } _%>乙")
        self.assertEqual(pairs, [("剧情权重 > 1", "甲"), (None, "乙")])
        self.assertEqual(rep.conditions, 1)
        self.assertTrue(rep.clean)

    def test_else取反(self):
        pairs, rep = _pairs("<%_ if (getvar('c') > 1) { _%>甲<%_ } else { _%>乙<%_ } _%>")
        self.assertEqual(pairs, [("c > 1", "甲"), ("not(c > 1)", "乙")])
        self.assertEqual(rep.else_branches, 1)

    def test_else_if链(self):
        pairs, _ = _pairs(
            "<%_ if (getvar('c') > 1) { _%>甲"
            "<%_ } else if (getvar('c') > 2) { _%>乙<%_ } _%>")
        self.assertEqual(pairs[0], ("c > 1", "甲"))
        self.assertEqual(pairs[1], ("not(c > 1) and c > 2", "乙"))

    def test_嵌套合取(self):
        pairs, _ = _pairs(
            "<%_ if (getvar('a') > 1) { _%>"
            "外<%_ if (getvar('b') > 2) { _%>内<%_ } _%>"
            "<%_ } _%>")
        conds = [c for c, _ in pairs if c]
        self.assertEqual(conds, ["a > 1", "a > 1 and b > 2"])

    def test_散文零丢失(self):
        content = ("前言<%_ if (getvar('c') > 5) { _%>甲<%_ } _%>中间"
                   "<%_ if (getvar('c') > 9) { _%>乙<%_ } else { _%>丙<%_ } _%>结尾")
        pairs, _ = _pairs(content)
        self.assertEqual("".join(t for _, t in pairs), "前言甲中间乙丙结尾")

    def test_非标准表达式保留并报告(self):
        pairs, rep = _pairs("<%_ if (false) { _%>甲<%_ } _%>")
        self.assertEqual(pairs[0], ("false", "甲"))
        self.assertIn("false", rep.nonstandard)
        self.assertFalse(rep.clean)

    def test_复合表达式保留并报告(self):
        expr = "getvar('x') === true && getvar('y') > 1"
        pairs, rep = _pairs(f"<%_ if ({expr}) {{ _%>甲<%_ }} _%>")
        self.assertEqual(pairs[0][0], expr)
        self.assertIn(expr, rep.nonstandard)

    def test_js声明被消费但报告(self):
        pairs, rep = _pairs("<% const f = (key) => { return key } %>正文")
        self.assertEqual(pairs, [(None, "正文")])
        self.assertEqual(sum(rep.code_tags.values()), 1)

    def test_取值插值不删但要报告(self):
        pairs, rep = _pairs("甲<%= getLocalVar('用户名') %>乙")
        self.assertEqual(pairs, [(None, "甲<%= getLocalVar('用户名') %>乙")])
        self.assertEqual(sum(rep.unresolved.values()), 1)

    def test_未闭合的if不会崩(self):
        pairs, rep = _pairs("<%_ if (getvar('c') > 1) { _%>甲")
        self.assertEqual(pairs, [("c > 1", "甲")])
        self.assertEqual(rep.conditions, 1)

    def test_多余闭括号不会崩(self):
        pairs, _ = _pairs("甲<%_ } _%>乙")
        self.assertEqual("".join(t for _, t in pairs), "甲乙")


class TestRender(unittest.TestCase):
    def test_结构级往返_仅if(self):
        content = "前<%_ if (getvar('c') > 5) { _%>甲<%_ } _%>后"
        segs, _ = C.split_conditions(content)
        segs2, _ = C.split_conditions(C.render_conditions(segs))
        self.assertEqual([(s.condition, s.text) for s in segs2],
                         [(s.condition, s.text) for s in segs])

    def test_无条件段原样输出(self):
        self.assertEqual(C.render_conditions([C.Segment(None, "正文")]), "正文")


if __name__ == "__main__":
    unittest.main()
