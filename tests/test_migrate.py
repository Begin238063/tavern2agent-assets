#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_migrate.py —— --migrate 路径的回归测试（纯标准库 + unittest）。

守护场景：上游卡持续更新，migrate 出问题的时刻正是你在合并新版本、注意力在别处的时候。
fixture 内容全部自编（公开 MIT 仓库可携带），角色「测试员」，无任何真实卡文本。

运行：
    python3 -m unittest discover -s tests -v
"""

import hashlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import _mini_yaml  # noqa: E402  # 工具族共享解析器


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / args[0]), *args[1:]],
                          capture_output=True, text=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _lore_by_eid(lore_dir: Path, eid) -> Path:
    for f in sorted(lore_dir.glob("*.yaml")):
        d = _mini_yaml.parse(f.read_text(encoding="utf-8"))
        if d.get("_derived", {}).get("_entry_id") == eid:
            return f
    raise AssertionError(f"找不到 _entry_id={eid} 的 lore 文件")


class TestMigrate(unittest.TestCase):
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

    def _human_fill(self):
        """人工填充 E1..E4：keys / note / layer / budget_tokens 各写入人工值。"""
        for f in sorted(self.lore.glob("*.yaml")):
            text = f.read_text(encoding="utf-8")
            eid = _mini_yaml.parse(text).get("_derived", {}).get("_entry_id")
            if eid is None:
                continue
            eid = int(eid)
            text = re.sub(r"^keys:\n(?:  - .*\n|  \[\]\n)*",
                          'keys:\n  - "人工键"\n', text, count=1, flags=re.M)
            text = re.sub(r"^note: .*$", f'note: "人工{eid}"', text, count=1, flags=re.M)
            text = re.sub(r"^layer: .*$", "layer: behavior", text, count=1, flags=re.M)
            text = re.sub(r"budget_tokens: null", "budget_tokens: 300", text, count=1)
            f.write_text(text, encoding="utf-8")

    def _migrate(self):
        r = _run("build_assets.py", "--card", str(FIXTURES / "migrate_b.json"),
                 "--out", str(self.characters), "--migrate")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r

    def test_migrate_merges_and_preserves(self):
        self._human_fill()
        sha_b = _sha256(FIXTURES / "migrate_b.json")

        # E2 的文件名（先记录，断言 comment 改名后路径不变）
        e2_before = _lore_by_eid(self.lore, 2).name

        self._migrate()

        # E1：content 按新卡刷新；人工字段原值；sha 更新
        e1 = _lore_by_eid(self.lore, 1)
        d1 = _mini_yaml.parse(e1.read_text(encoding="utf-8"))
        self.assertIn("新版本内容", (d1.get("_derived") or {}).get("content", ""))
        self.assertEqual(d1.get("keys"), ["人工键"])
        self.assertEqual(d1.get("note"), "人工1")
        self.assertEqual(d1.get("layer"), "behavior")
        self.assertEqual(d1.get("budget_tokens"), 300)
        self.assertEqual((d1.get("_derived") or {}).get("_card_sha"), sha_b)

        # E2：路径不变；comment 更新；人工字段原值
        e2 = _lore_by_eid(self.lore, 2)
        self.assertEqual(e2.name, e2_before)
        d2 = _mini_yaml.parse(e2.read_text(encoding="utf-8"))
        self.assertEqual((d2.get("_derived") or {}).get("comment"), "条目二新名")
        self.assertEqual(d2.get("keys"), ["人工键"])
        self.assertEqual(d2.get("note"), "人工2")
        self.assertEqual(d2.get("layer"), "behavior")

        # E3：constant 按新卡刷新为 false；layer 原值（路由决策不被机器字段刷新影响）
        e3 = _lore_by_eid(self.lore, 3)
        d3 = _mini_yaml.parse(e3.read_text(encoding="utf-8"))
        self.assertFalse(bool((d3.get("_derived") or {}).get("constant", True)))
        self.assertEqual(d3.get("layer"), "behavior")
        self.assertEqual(d3.get("note"), "人工3")

        # E4（b 中删除）：孤立标记
        e4 = _lore_by_eid(self.lore, 4)
        d4 = _mini_yaml.parse(e4.read_text(encoding="utf-8"))
        self.assertTrue(bool(_mini_yaml.get(d4, "_orphaned", False)))
        self.assertEqual(d4.get("note"), "人工4")

        # E5（b 中新增）：新文件；_derived 齐全；人工字段为空/默认待补
        e5 = _lore_by_eid(self.lore, 5)
        d5 = _mini_yaml.parse(e5.read_text(encoding="utf-8"))
        self.assertEqual(d5.get("keys"), ["条目五"])
        # 新建条目的 layer 由 _infer_layer 自动推断（见 CHANGELOG「layer 字段不再留空」）；
        # 此处期望曾停留在「留空待人工填」，与 auto-infer 上线后的代码不符。
        self.assertEqual(d5.get("layer"), "narrative")
        self.assertIsNone(d5.get("budget_tokens"))
        for k in ("comment", "secondary_keys", "selective", "constant", "enabled",
                  "insertion_order", "case_sensitive", "scan_depth", "prevent_recursion",
                  "inject_at", "_card_sha", "content"):
            self.assertIn(k, d5.get("_derived") or {}, f"E5 缺 _derived.{k}")

        # 全部文件可解析、无 KeyError
        for f in sorted(self.lore.glob("*.yaml")):
            d = _mini_yaml.parse(f.read_text(encoding="utf-8"))  # 不崩即可
            self.assertIsInstance(d, dict)

    def test_migrate_idempotent(self):
        self._human_fill()
        self._migrate()
        before = {f.name: f.read_bytes() for f in sorted(self.lore.glob("*.yaml"))}
        r = self._migrate()  # 第二次
        after = {f.name: f.read_bytes() for f in sorted(self.lore.glob("*.yaml"))}
        self.assertEqual(before, after, "第二次 migrate 应字节级不变（幂等）")
        self.assertNotIn("[新建]", r.stdout)
        self.assertNotIn("[卡片已移除]", r.stdout)


class TestMinimalV2(unittest.TestCase):
    """通用性守护：无世界书、无 alternate_greetings 的 CCv2 卡走全链路不崩。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.characters = Path(self._tmp.name) / "characters"
        self.characters.mkdir()
        self.asset = self.characters / "测试员"

    def tearDown(self):
        self._tmp.cleanup()

    def test_pipeline_no_worldbook(self):
        r = _run("build_assets.py", "--card", str(FIXTURES / "minimal_v2.json"),
                 "--out", str(self.characters))
        self.assertEqual(r.returncode, 0, r.stderr)
        # 无世界书：不产生 lore/ 目录导致的报错，persona 照常生成
        self.assertTrue((self.asset / "persona.yaml").is_file())
        if (self.asset / "lore").is_dir():
            self.assertEqual(list((self.asset / "lore").glob("*.yaml")), [])
        # validate：不该有「缺失文件/解析崩溃」类错误（语义补全占位允许报错）
        r = _run("validate_assets.py", str(self.asset))
        self.assertIn(r.stderr, "")
        # compile：无世界书 → 0 条 entries
        out = Path(self._tmp.name) / "card.json"
        r = _run("compile_card.py", "--assets", str(self.asset), "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        import json
        card = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(card["data"]["character_book"]["entries"]), 0)
        self.assertEqual(card["data"]["name"], "测试员")


if __name__ == "__main__":
    unittest.main()
