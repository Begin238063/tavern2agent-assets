#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_runtime_exclude.py —— 卡侧 runtime.yaml 排除清单的回归测试（纯标准库 + unittest）。

守护的场景：卡自带的 ST 运行时管道（CG 插图 / MVU 变量规则 / initvar 等）不该落进
平台无关资产层。这里锁住三件事：
  1. 清单里的 id 在「生成」与「迁移」两条路径上都被跳过；
  2. 清单读不出来时 **fail-closed**（报错中止，绝不静默当成空清单继续跑）；
  3. 迁移时被排除的旧文件不会被误标 _orphaned（排除 ≠ 上游删条目）。

fixture 全部在测试内自建（公开仓库可携带），无任何真实卡文本。
运行： python3 -m unittest discover -s tests -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _mini_yaml  # noqa: E402


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / args[0]), *args[1:]],
                          capture_output=True, text=True)


def _card(entries):
    return {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {
            "name": "测试员",
            "description": "极简占位角色。",
            "personality": "随和",
            "first_mes": "你好。",
            "character_book": {"entries": entries},
        },
    }


def _entry(eid, comment, content):
    return {"id": eid, "comment": comment, "content": content,
            "enabled": True, "constant": False, "selective": False, "keys": []}


class TestRuntimeExclude(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.card_dir = self.root / "cards"
        self.card_dir.mkdir()
        self.card_path = self.card_dir / "card.json"
        self.card_path.write_text(json.dumps(_card([
            _entry(1, "普通设定", "这是一条会进资产层的正常内容。"),
            _entry(2, "09_📌CG插图", "<%_ const cg = getGlobalVar('cg_config') _%> 通用/{{roll:2}}"),
            _entry(3, "临时条目", "这条稍后会从卡里删掉，用来验证孤儿标记仍生效。"),
        ]), ensure_ascii=False), encoding="utf-8")
        self.out = self.root / "characters"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_manifest(self, text):
        (self.card_dir / "runtime.yaml").write_text(text, encoding="utf-8")

    def _lore_eids(self):
        d = self.out / "测试员" / "lore"
        if not d.is_dir():
            return set()
        out = set()
        for f in d.glob("*.yaml"):
            eid = _mini_yaml.get(_mini_yaml.parse(f.read_text(encoding="utf-8")), "_entry_id")
            if eid is not None:
                out.add(int(eid))
        return out

    def test_排除的条目不生成_其余照常生成(self):
        self._write_manifest("version: \"1\"\n\nexclude_entry_ids:\n  - 2    # 行内注释应被正确剥离\n")
        r = _run("build_assets.py", "--card", str(self.card_path), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("[排除]", r.stdout)
        self.assertEqual(self._lore_eids(), {1, 3})

    def test_没有清单时全部生成_行为不变(self):
        r = _run("build_assets.py", "--card", str(self.card_path), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self._lore_eids(), {1, 2, 3})

    def test_清单读不出时fail_closed_不生成任何东西(self):
        # 缺 exclude_entry_ids：_mini_yaml 会解析成 {} / 空串，必须报错而不是当空清单
        # 注：`key:` 后紧跟注释行曾因 _mini_yaml 前瞻 bug 被解析成空串，那个 bug
        # 已修（见 test_mini_yaml.TestLookaheadAfterKey），所以它不再是坏清单。
        for bad in ("version: \"1\"\n", "exclude_entry_ids: \"2,3\"\n"):
            with self.subTest(manifest=bad):
                self._write_manifest(bad)
                r = _run("build_assets.py", "--card", str(self.card_path), "--out", str(self.out))
                self.assertNotEqual(r.returncode, 0, "应 fail-closed 报错")
                self.assertIn("exclude_entry_ids", r.stdout + r.stderr)
                self.assertFalse(self.out.exists(), "报错时不应产出任何资产")

    def test_key后紧跟注释行仍能读出清单(self):
        self._write_manifest("exclude_entry_ids:\n  # 注释行紧跟 key\n  - 2\n")
        r = _run("build_assets.py", "--card", str(self.card_path), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self._lore_eids(), {1, 3})

    def test_迁移时被排除的旧文件不被标为孤儿(self):
        r = _run("build_assets.py", "--card", str(self.card_path), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self._write_manifest("exclude_entry_ids:\n  - 2\n")

        def _file_of(eid):
            for f in (self.out / "测试员" / "lore").glob("*.yaml"):
                if _mini_yaml.get(_mini_yaml.parse(f.read_text(encoding="utf-8")), "_entry_id") == eid:
                    return f
            raise AssertionError(f"找不到 _entry_id={eid}")

        excluded_file, orphan_file = _file_of(2), _file_of(3)

        # 卡里删掉 id=3（真孤儿）后迁移
        self.card_path.write_text(json.dumps(_card([
            _entry(1, "普通设定", "这是一条会进资产层的正常内容。"),
            _entry(2, "09_📌CG插图", "<%_ const cg = getGlobalVar('cg_config') _%> 通用/{{roll:2}}"),
        ]), ensure_ascii=False), encoding="utf-8")
        r = _run("build_assets.py", "--card", str(self.card_path), "--out", str(self.out), "--migrate")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

        self.assertIn("[排除]", r.stdout)
        self.assertNotIn("_orphaned", excluded_file.read_text(encoding="utf-8"),
                         "被排除不是上游删条目，不该标 _orphaned")
        orphan_file = _file_of(3)
        self.assertIn("_orphaned: true", orphan_file.read_text(encoding="utf-8"),
                      "真孤儿（卡里删掉且不在清单里）仍应被标记")


if __name__ == "__main__":
    unittest.main()
