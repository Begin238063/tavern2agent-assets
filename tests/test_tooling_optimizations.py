#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_tooling_optimizations.py —— 拆卡审计与资产化工具链增强回归测试（纯标准库 + unittest）。

覆盖范围：
  1. extract_card.py：CCv3 .charx（ZIP）解包、magic bytes 兜底、自动创建多级输出目录；
  2. get_entry.py：--id 按条目稳定 id/uid 精确查询（对齐 runtime.yaml）；
  3. build_assets.py & infer_layers.py：
     - 卡片中性的 infer_layer（非常驻 NPC 不误判常驻、常驻同名主角归 identity、[mvu_plot] 仅常驻归 behavior）；
     - _entry_key_name 可读命名回退（哨兵键 / 正则元字符键 / 单字泛匹配键组回退 comment）；
     - migrate_lorebook 首条空 layer 不触发 UnboundLocalError，且迁移新增同名条目自动去重；
     - infer_layers.py 复用统一 infer_layer 填充空层并跳过已有层；
  4. list_entries.py：--audit 扩展脚本分类（tavern_helper / regex_scripts）与 --suggest-runtime 候选生成
     （识别 MVU/前端/XML 标牌/死代码陷阱，且绝不误伤正常剧情权重门控与正文 [mvu_plot]）；
  5. validate_assets.py：分层 token 统计、--summary 折叠输出、ST 运行时残留扫描（含 --strict 升级）。

所有测试数据均为合成夹具（公开 MIT 仓库可携带），不含任何真实卡片文本。
"""

import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _mini_yaml  # noqa: E402
import build_assets  # noqa: E402
import list_entries  # noqa: E402


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / args[0]), *args[1:]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _make_card(name="测试主角", entries=None, extensions=None, first_mes="你好。"):
    return {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {
            "name": name,
            "description": "用于自动化回归测试的合成角色描述。",
            "personality": "冷静、严谨",
            "scenario": "测试沙盒场景",
            "first_mes": first_mes,
            "mes_example": "",
            "creator_notes": "合成测试卡说明。",
            "alternate_greetings": [],
            "extensions": extensions or {},
            "character_book": {"entries": entries or []},
        },
    }


class TestExtractCardEnhancements(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_charx_extraction_and_nested_mkdir(self):
        charx_path = self.root / "synthetic.charx"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("card.json", json.dumps(_make_card(name="归档角色"), ensure_ascii=False))
            zf.writestr("assets/icon/main.png", b"fakepng")
        charx_path.write_bytes(buf.getvalue())

        nested_out = self.root / "cards" / "nested" / "sub" / "card.json"
        r = _run("extract_card.py", str(charx_path), str(nested_out))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(nested_out.is_file(), "应自动创建不存在的父级目录并写出 card.json")

        extracted = json.loads(nested_out.read_text(encoding="utf-8"))
        self.assertEqual(extracted["data"]["name"], "归档角色")
        self.assertEqual(extracted["_source_file"], "synthetic.charx")

    def test_charx_magic_bytes_fallback_and_nested_member(self):
        unknown_ext = self.root / "archive_without_charx_ext.bin"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("bundle/card.json", json.dumps({"char_name": "旧版V1角色", "char_greeting": "嗨"}, ensure_ascii=False))
        unknown_ext.write_bytes(buf.getvalue())

        out_json = self.root / "out.json"
        r = _run("extract_card.py", str(unknown_ext), str(out_json))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        extracted = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertTrue(extracted.get("_normalized_from_v1"))
        self.assertEqual(extracted["data"]["name"], "旧版V1角色")


class TestGetEntryById(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.card_path = Path(self._tmp.name) / "card.json"
        card = _make_card(entries=[
            {"id": 101, "comment": "首条设定", "content": "内容甲", "keys": ["甲"]},
            {"id": 205, "comment": "中段设定", "content": "内容乙", "keys": ["乙"]},
            {"id": 433, "comment": "末尾管道", "content": "内容丙", "keys": []},
        ])
        self.card_path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_lookup_by_id_list(self):
        r = _run("get_entry.py", str(self.card_path), "--id", "205,433")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("[1 | id=205] 中段设定", r.stdout)
        self.assertIn("[2 | id=433] 末尾管道", r.stdout)
        self.assertNotIn("首条设定", r.stdout)

    def test_lookup_missing_id_fails(self):
        r = _run("get_entry.py", str(self.card_path), "--id", "999")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("未找到指定 id", r.stdout)


class TestInferLayerAndKeyNaming(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_infer_layer_card_neutral_and_mvu_plot(self):
        # 1) 非常驻 NPC 档案绝不应误判为常驻 identity
        npc_entry = {"comment": "同伴甲角色档案", "constant": False, "keys": ["同伴甲"]}
        self.assertEqual(build_assets.infer_layer(npc_entry, "同伴甲", char_name="测试主角"), "narrative")

        # 2) 常驻且匹配角色名的核心档案归入 identity
        protag_entry = {"comment": "测试主角", "constant": True, "keys": ["测试主角", "主角别名"]}
        self.assertEqual(build_assets.infer_layer(protag_entry, "测试主角", char_name="测试主角"), "identity")

        # 3) [mvu_plot] 仅在 constant=True 时归 behavior，非常驻剧情规则归 narrative
        plot_trigger = {"comment": "[mvu_plot]支线触发说明", "constant": False, "keys": ["支线"]}
        plot_const = {"comment": "[mvu_plot]全局叙事推进", "constant": True, "keys": []}
        self.assertEqual(build_assets.infer_layer(plot_trigger, "支线触发说明"), "narrative")
        self.assertEqual(build_assets.infer_layer(plot_const, "全局叙事推进"), "behavior")

    def test_entry_key_name_readable_fallback(self):
        # 哨兵键回退 comment
        e_sentinel = {"keys": ["$$_NO_TRIGGER_$$"], "comment": "特殊事件档案"}
        self.assertEqual(build_assets._entry_key_name(e_sentinel, 0), "特殊事件档案")

        # 正则元字符键回退 comment
        e_regex = {"keys": [r"(?=.*[\s\S]*(学院))(?=.*(终端|机兵))"], "comment": "学院终端设定"}
        self.assertEqual(build_assets._entry_key_name(e_regex, 1), "学院终端设定")

        # 单字符多语种泛匹配键组回退 comment
        e_kana = {"keys": ["あ", "い", "う", "え", "お", "a"], "comment": "多语种兜底规则"}
        self.assertEqual(build_assets._entry_key_name(e_kana, 2), "多语种兜底规则")

        # 正常词条仍优先使用首个 key
        e_normal = {"keys": ["中央车站", "车站"], "comment": "地点_01_中央车站"}
        self.assertEqual(build_assets._entry_key_name(e_normal, 3), "中央车站")

    def test_migrate_empty_layer_on_first_entry_and_duplicate_new_names(self):
        card_a = self.root / "card_a.json"
        card_b = self.root / "card_b.json"
        out_dir = self.root / "characters"

        card_a.write_text(json.dumps(_make_card(entries=[
            {"id": 1, "comment": "角色扮演总则", "content": "初版规则", "constant": True, "keys": []},
        ]), ensure_ascii=False), encoding="utf-8")

        r1 = _run("build_assets.py", "--card", str(card_a), "--out", str(out_dir))
        self.assertEqual(r1.returncode, 0, r1.stderr + r1.stdout)

        # 将第 1 条 lore 的 layer 与 note 置空（复现修复前 migrate_lorebook 的 UnboundLocalError 触发条件）
        lore_dir = out_dir / "测试主角" / "lore"
        first_file = next(lore_dir.glob("*.yaml"))
        txt = first_file.read_text(encoding="utf-8")
        txt = re.sub(r"^layer: .*$", "layer: ", txt, count=1, flags=re.M)
        txt = re.sub(r"^note: .*$", 'note: ""', txt, count=1, flags=re.M)
        first_file.write_text(txt, encoding="utf-8")

        # 新卡更新 id=1，并新增两条同名条目 id=2, id=3
        card_b.write_text(json.dumps(_make_card(entries=[
            {"id": 1, "comment": "角色扮演总则", "content": "二版规则", "constant": True, "keys": []},
            {"id": 2, "comment": "同名补充设定", "content": "补充内容一", "constant": False, "keys": []},
            {"id": 3, "comment": "同名补充设定", "content": "补充内容二", "constant": False, "keys": []},
        ]), ensure_ascii=False), encoding="utf-8")

        r2 = _run("build_assets.py", "--card", str(card_b), "--out", str(out_dir), "--migrate")
        self.assertEqual(r2.returncode, 0, r2.stderr + r2.stdout)

        files = sorted(lore_dir.glob("*.yaml"))
        self.assertEqual(len(files), 3, "新增同名条目应自动加 _2 后缀去重，不得互相覆盖")
        d1 = _mini_yaml.parse(first_file.read_text(encoding="utf-8"))
        self.assertEqual(d1.get("layer"), "behavior")

    def test_infer_layers_cli_uses_shared_logic(self):
        card_path = self.root / "card.json"
        out_dir = self.root / "characters"
        card_path.write_text(json.dumps(_make_card(entries=[
            {"id": 1, "comment": "系统指令与文风", "content": "保持克制文风。", "constant": True, "keys": []},
            {"id": 2, "comment": "普通地点设定", "content": "一座安静的图书馆。", "constant": False, "keys": ["图书馆"]},
        ]), ensure_ascii=False), encoding="utf-8")

        _run("build_assets.py", "--card", str(card_path), "--out", str(out_dir))
        lore_dir = out_dir / "测试主角" / "lore"
        f_rule = lore_dir / "系统指令与文风.yaml"
        f_place = lore_dir / "图书馆.yaml"
        # 清空规则条目的 layer，地点条目改为人工指定的 identity
        t0 = re.sub(r"^layer: .*$", "layer:", f_rule.read_text(encoding="utf-8"), count=1, flags=re.M)
        f_rule.write_text(t0, encoding="utf-8")
        t1 = re.sub(r"^layer: .*$", "layer: identity", f_place.read_text(encoding="utf-8"), count=1, flags=re.M)
        f_place.write_text(t1, encoding="utf-8")

        r = _run("infer_layers.py", str(out_dir / "测试主角"))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("已有 layer（跳过）: 1", r.stdout)
        self.assertEqual(_mini_yaml.parse(f_rule.read_text(encoding="utf-8")).get("layer"), "behavior")
        self.assertEqual(_mini_yaml.parse(f_place.read_text(encoding="utf-8")).get("layer"), "identity")


class TestListEntriesAuditAndSuggestRuntime(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.card_path = Path(self._tmp.name) / "card.json"
        card = _make_card(
            entries=[
                # 应保留在资产层的正常条目（0 误报锁）
                {
                    "id": 10,
                    "comment": "主线第一章剧情",
                    "content": "<%_ if (getvar('stat_data.剧情权重') > 10) { _%>这里是正式剧情设定正文。<%_ } _%>",
                    "constant": False,
                    "keys": ["第一章"],
                    "enabled": True,
                },
                {
                    "id": 11,
                    "comment": "02_📌角色扮演核心逻辑[mvu_plot]",
                    "content": "请严格遵循角色性格进行自然对话，使用 getvar 读取状态但包含完整扮演准则。",
                    "constant": True,
                    "keys": [],
                    "enabled": True,
                },
                # 应被识别为 runtime.yaml 候选的纯运行时/陷阱条目
                {
                    "id": 20,
                    "comment": "[initvar]初始变量定义",
                    "content": "stat_data: { 好感度: 0 }",
                    "constant": True,
                    "keys": [],
                    "enabled": True,
                },
                {
                    "id": 21,
                    "comment": "[mvu_update]变量更新规则",
                    "content": "每次回复末尾输出 <UpdateVariable> 块。",
                    "constant": True,
                    "keys": [],
                    "enabled": True,
                },
                {
                    "id": 22,
                    "comment": "09_CG插图渲染",
                    "content": "插图模板：<pic>{{roll:2}}</pic>",
                    "constant": True,
                    "keys": [],
                    "enabled": True,
                },
                {
                    "id": 23,
                    "comment": "🏷️世界书前标",
                    "content": "---\n<world_info>",
                    "constant": True,
                    "keys": [],
                    "enabled": True,
                },
                {
                    "id": 24,
                    "comment": "废弃草稿",
                    "content": "<%_ if (false) { _%>这段永远不会激活<%_ } _%>",
                    "constant": False,
                    "keys": ["草稿"],
                    "enabled": True,
                },
            ],
            extensions={
                "tavern_helper": {
                    "scripts": [
                        {"name": "变量结构", "enabled": True, "content": "registerMvuSchema(z.object({ hp: z.number() }))"},
                        {"name": "MVU引擎", "enabled": True, "content": "window.MagVarUpdate = true;"},
                        {"name": "状态栏面板", "enabled": True, "content": "<div class='panel'>状态栏</div>"},
                    ]
                },
                "regex_scripts": [
                    {"scriptName": "清理变量块", "findRegex": "<UpdateVariable>[\\s\\S]*?</UpdateVariable>", "replaceString": ""},
                    {"scriptName": "隐藏状态栏不发送", "findRegex": "<StatusPlaceHolderImpl/>", "replaceString": ""},
                    {"scriptName": "[杀八股]禁用陈词滥调", "findRegex": "(?:眼神中闪过一丝不易察觉的|嘴角勾起一抹弧度|空气仿佛凝固了一般)", "replaceString": ""},
                    {"scriptName": "前端折叠面板", "findRegex": "<panel>", "replaceString": "<div class='box'><details>面板</details></div>"},
                ],
            },
        )
        self.card_path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_audit_surfaces_scripts_and_counts(self):
        r = _run("list_entries.py", str(self.card_path), "--audit")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        for token in ("zod_schema", "mvu_engine", "ui_or_dom", "ST_CLEANUP", "AI_HIDE", "PROSE_LINT", "UI_PANEL"):
            self.assertIn(token, r.stdout)

    def test_suggest_runtime_exact_match_and_valid_yaml(self):
        r = _run("list_entries.py", str(self.card_path), "--suggest-runtime")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        parsed = _mini_yaml.parse(r.stdout)
        self.assertEqual(parsed.get("exclude_entry_ids"), [20, 21, 22, 23, 24])


class TestValidateEnhancements(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.card_path = self.root / "card.json"
        self.out_dir = self.root / "characters"
        entries = [
            {
                "id": i,
                "comment": f"词条_{i}",
                "content": "这是一段超过四十个字符的正常知识库设定描述文本，用于通过过短无标题检查门禁并测试警告折叠能力。",
                "constant": False,
                "selective": True,
                "secondary_keys": [],
                "keys": ["泛"],
                "enabled": True,
            }
            for i in range(1, 6)
        ]
        card = _make_card(
            entries=entries,
            first_mes="<!--AI_HIDE_START--><UpdateVariable></UpdateVariable><!--AI_HIDE_END-->你好，我是测试主角。",
        )
        self.card_path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
        r = _run("build_assets.py", "--card", str(self.card_path), "--out", str(self.out_dir))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.asset = self.out_dir / "测试主角"

        # 补全 persona 5 个必填字段
        p_path = self.asset / "persona.yaml"
        p_text = p_path.read_text(encoding="utf-8")
        p_text = (
            p_text.replace('  codename: ""', '  codename: "测试代号"')
            .replace('  archetype: ""', '  archetype: "测试原型"')
            .replace('  tone: ""', '  tone: "平实冷静"')
            .replace('  form: ""', '  form: "搭档关系"')
            .replace('  memory_policy: ""', '  memory_policy: "记录关键事件"')
        )
        p_path.write_text(p_text, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_layered_token_breakdown_and_summary_folding(self):
        r = _run("validate_assets.py", str(self.asset), "--summary")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("分层 token 统计：常驻 resident=", r.stdout)
        self.assertIn("dialogs 含 ST 开场运行时块", r.stdout)
        self.assertIn("lore keys 可能过泛（共 5 条，展示前 3 条）", r.stdout)
        self.assertIn("lore selective 归一化提示（共 5 条，展示前 3 条）", r.stdout)

    def test_persona_st_residual_warning_and_strict_upgrade(self):
        p_path = self.asset / "persona.yaml"
        p_text = p_path.read_text(encoding="utf-8")
        p_path.write_text(
            p_text.replace('  tone: "平实冷静"', '  tone: "平实 {{getvar::mood}}"'),
            encoding="utf-8",
        )
        r_warn = _run("validate_assets.py", str(self.asset), "--summary")
        self.assertEqual(r_warn.returncode, 0, r_warn.stdout)
        self.assertIn("persona 含 ST 运行时语法残留：style.tone", r_warn.stdout)

        r_strict = _run("validate_assets.py", str(self.asset), "--strict", "--summary")
        self.assertEqual(r_strict.returncode, 1)
        self.assertIn("✗ persona 含 ST 运行时语法残留：style.tone", r_strict.stdout)


if __name__ == "__main__":
    unittest.main()
