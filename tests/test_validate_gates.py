#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_validate_gates.py —— 预算口径与路由门禁的行为锁（纯标准库 + unittest）。

守护场景：门禁的失败模式是「偏松」而不是「报错」——漏算一个字段，预算就一直不超，
你以为压过了，实际每轮都在稀释注意力。这类偏差不会有任何异常。

重点锁 `_persona_standing_tokens()` 的**覆盖范围**：
    2026-09-06 修过一次漏算 relationship（form / memory_policy 会被下游编译进
    system_prompt，却没计入 resident_tokens）。这里逐字段断言「改一个字段就该让
    数字变大」，这样再漏一个就会红。

运行：
    python3 -m unittest discover -s tests -v
"""

import subprocess
import sys
import re
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import _mini_yaml  # noqa: E402
import validate_assets  # noqa: E402

est = _mini_yaml.estimated_tokens


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / args[0]), *args[1:]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


# persona 的语义字段全集：每一项都会被下游编译进 system_prompt，
# 因此每一项都必须计入 resident_tokens。
_PERSONA = {
    "identity": {"name": "测试员", "codename": "代号甲", "archetype": "用于测试的原型",
                 "version": "0.1.0", "provenance": {"hash_sha256": "abc"}},
    "core": {"self": "自我认知全文，占位。", "traits": ["随和", "认真"],
             "capabilities": ["能力一"], "boundaries": ["边界一"]},
    "style": {"tone": "平实", "habits": ["习惯一", "习惯二"], "sample": "dialogs/example_01.yaml"},
    "relationship": {"form": "同事关系，保持距离。",
                     "memory_policy": "记住偏好与重要事件，不数值化好感。"},
    "lore_refs": ["lore/a.yaml"],
    "dialogs_refs": ["dialogs/example_01.yaml"],
    "provenance": {"source_file": "card.json", "sha256": "abc"},
}


def _persona(**overrides) -> dict:
    """深拷一份 persona，允许按 "core.self" 形式覆盖单个字段。"""
    import copy
    p = copy.deepcopy(_PERSONA)
    for dotted, value in overrides.items():
        sec, key = dotted.split(".")
        p[sec][key] = value
    return p


class TestPersonaStandingTokens(unittest.TestCase):
    """常驻口径：每个语义字段都必须被计入。"""

    def setUp(self):
        self.base = validate_assets._persona_standing_tokens(_PERSONA)

    def test_counts_something(self):
        self.assertGreater(self.base, 0)

    def test_每个字符串字段都计入(self):
        """逐字段加长 → 总数必须变大。漏算任何一个，这里就红。"""
        for dotted in ("identity.name", "identity.codename", "identity.archetype",
                       "core.self", "style.tone",
                       "relationship.form", "relationship.memory_policy"):
            with self.subTest(field=dotted):
                sec, key = dotted.split(".")
                longer = _PERSONA[sec][key] + "追加一段足够长的中文文本用于抬高估算值。"
                got = validate_assets._persona_standing_tokens(_persona(**{dotted: longer}))
                self.assertGreater(got, self.base, f"{dotted} 未计入 resident_tokens")

    def test_每个列表字段都计入(self):
        for dotted in ("core.traits", "core.capabilities", "core.boundaries", "style.habits"):
            with self.subTest(field=dotted):
                sec, key = dotted.split(".")
                longer = [*_PERSONA[sec][key], "新增一条足够长的中文条目用于抬高估算值。"]
                got = validate_assets._persona_standing_tokens(_persona(**{dotted: longer}))
                self.assertGreater(got, self.base, f"{dotted} 未计入 resident_tokens")

    def test_relationship_精确计入(self):
        """回归锁（2026-09-06）：relationship 曾被整节漏算。"""
        without = validate_assets._persona_standing_tokens(
            _persona(**{"relationship.form": "", "relationship.memory_policy": ""}))
        expected = est(_PERSONA["relationship"]["form"]) + \
            est(_PERSONA["relationship"]["memory_policy"])
        self.assertEqual(self.base - without, expected)

    def test_refs_与_provenance_不计入(self):
        """指针与来源指纹不进提示词，不该占预算。"""
        p = _persona()
        p["lore_refs"] = [f"lore/{i}.yaml" for i in range(50)]
        p["dialogs_refs"] = [f"dialogs/example_{i}.yaml" for i in range(50)]
        p["provenance"]["sha256"] = "f" * 64
        p["identity"]["provenance"]["hash_sha256"] = "f" * 64
        self.assertEqual(validate_assets._persona_standing_tokens(p), self.base)

    def test_style_sample_不计入(self):
        """sample 是文件路径（指针），不是提示词内容。"""
        got = validate_assets._persona_standing_tokens(
            _persona(**{"style.sample": "dialogs/" + "长" * 200 + ".yaml"}))
        self.assertEqual(got, self.base)

    def test_缺字段不抛(self):
        """半成品资产（字段缺失/类型不对）不能让校验崩在估算这一步。"""
        for bad in ({}, {"core": None}, {"core": {"traits": "不是列表"}},
                    {"identity": {"name": 123}}, {"relationship": None}):
            with self.subTest(persona=bad):
                self.assertIsInstance(validate_assets._persona_standing_tokens(bad), int)


class TestBudgetGateEndToEnd(unittest.TestCase):
    """预算门禁走真实资产目录：未配置=跳过、超限=错误退出码 1。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.characters = Path(self._tmp.name) / "characters"
        self.characters.mkdir()
        r = _run("build_assets.py", "--card", str(FIXTURES / "migrate_a.json"),
                 "--out", str(self.characters))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.asset = self.characters / "测试员"
        # 分层：全部标 identity，让 lore 正文计入常驻
        for f in sorted((self.asset / "lore").glob("*.yaml")):
            t = f.read_text(encoding="utf-8")
            f.write_text(t.replace("\nlayer:\n", "\nlayer: identity\n")
                          .replace("\nlayer: \n", "\nlayer: identity\n"), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_未配置预算时跳过门禁(self):
        r = _run("validate_assets.py", str(self.asset))
        self.assertIn("未配置常驻预算", r.stdout)

    def test_预算过小时报错并退出码1(self):
        r = _run("validate_assets.py", str(self.asset), "--budget-tokens", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("常驻预算超限", r.stdout)

    def test_预算充裕时不报预算错(self):
        r = _run("validate_assets.py", str(self.asset), "--budget-tokens", "1000000")
        self.assertNotIn("常驻预算超限", r.stdout)

    def test_validation_yaml_提供阈值(self):
        """阈值归私有仓库：工具不内置默认值，读资产目录的 validation.yaml。"""
        (self.asset / "validation.yaml").write_text("budget_tokens: 1\n", encoding="utf-8")
        r = _run("validate_assets.py", str(self.asset))
        self.assertEqual(r.returncode, 1)
        self.assertIn("常驻预算超限", r.stdout)

    def test_cli_覆盖_validation_yaml(self):
        (self.asset / "validation.yaml").write_text("budget_tokens: 1\n", encoding="utf-8")
        r = _run("validate_assets.py", str(self.asset), "--budget-tokens", "1000000")
        self.assertNotIn("常驻预算超限", r.stdout)


class TestLayerGates(unittest.TestCase):
    """layer 是唯一路由载体：未分层与取值非法都必须被机器拦住。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.characters = Path(self._tmp.name) / "characters"
        self.characters.mkdir()
        r = _run("build_assets.py", "--card", str(FIXTURES / "migrate_a.json"),
                 "--out", str(self.characters))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.asset = self.characters / "测试员"
        self.lore = self.asset / "lore"

    def tearDown(self):
        self._tmp.cleanup()

    def _set_layer(self, value: str):
        """改写 layer 行（value 传空串 = 造未分层）。

        用正则按行改写而不是字符串替换：`_infer_layer` 上线后新建条目的 layer 已是
        `narrative`，旧写法只匹配空值形态 `layer:` / `layer: `，从此静默失配。
        """
        for f in sorted(self.lore.glob("*.yaml")):
            t = f.read_text(encoding="utf-8")
            f.write_text(re.sub(r"^layer: .*$", f"layer: {value}", t, count=1, flags=re.M),
                         encoding="utf-8")

    def test_未分层被拦住(self):
        self._set_layer("")  # 显式造未分层：新建条目的 layer 现在会被自动推断
        r = _run("validate_assets.py", str(self.asset))
        self.assertEqual(r.returncode, 1)
        self.assertIn("未分层", r.stdout)

    def test_report_unlayered_只输出清单(self):
        r = _run("validate_assets.py", str(self.asset), "--report-unlayered")
        self.assertIn("未分层条目工作清单", r.stdout)
        # 只出清单，不混入 persona 的语义补全报错
        self.assertNotIn("语义补全", r.stdout)

    def test_layer_取值非法报错(self):
        self._set_layer("narrativ")  # 打错字
        r = _run("validate_assets.py", str(self.asset))
        self.assertEqual(r.returncode, 1)
        self.assertIn("layer 取值非法", r.stdout)

    def test_分层齐全后未分层清零(self):
        self._set_layer("narrative")
        r = _run("validate_assets.py", str(self.asset))
        self.assertNotIn("未分层", r.stdout)

    def test_narrative_不计入常驻(self):
        """narrative 走检索、不常驻——极小预算也不该因它超限。"""
        self._set_layer("narrative")
        base = _run("validate_assets.py", str(self.asset), "--budget-tokens", "1000000")
        self.assertNotIn("常驻预算超限", base.stdout)
        # 同一份资产改成 identity 后，常驻应变大到超限
        self._set_layer("identity")
        r = _run("validate_assets.py", str(self.asset), "--budget-tokens", "1")
        self.assertIn("常驻预算超限", r.stdout)


if __name__ == "__main__":
    unittest.main()
