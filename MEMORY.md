# 项目记忆索引

本文件记录 tavern2agent-assets 项目的重要决策、优化历史和技术债务。

## 优化历史

### 2026-11 上游借鉴与拆卡工具链优化

- 比对上游 `Xerxes-2/tavern2agent`（`601b1c3`）：吸收 `mvu-mapping`（`[initvar]`/`[mvu_update]` 与 `[mvu_plot]` 反幻觉区分）、`script-analysis`（`tavern_helper.scripts` 与 `regex_scripts` 分类审计）及 `validation`（ST 运行时残留扫描）
- 新增 `list_entries.py --audit`、`list_entries.py --suggest-runtime`、`get_entry.py --id`、`validate_assets.py --summary` 与分层 token 统计、`extract_card.py` `.charx` 支持
- 修复 `build_assets.py::migrate_lorebook` 首条空 layer 的 `UnboundLocalError` 隐患与 `infer_layers.py` 双份词表失步问题，去硬编码化 `infer_layer`

### 2025-01 验证工作流优化

- [验证工作流自动化优化](memory/optimization-validation-workflow-2025-01.md) — 解决补全流程效率问题

## 架构决策

### 分层验证设计（2025-01）

**Why:** 归档（场景A）只需机器字段完整，平台部署（场景B/C）才需要人工补全。混在一起导致无法快速归档。

**How to apply:**
```bash
# 归档级：只检查机器字段
python scripts/validate_assets.py assets/ --level archival

# 平台级：检查所有字段（默认）
python scripts/validate_assets.py assets/ --level platform
```

### layer 自动推断设计（2025-01）

**Why:** 手动分配 layer 效率低，且有规律可循（roleplay指令→behavior，角色核心→identity，其他→narrative）。

**How to apply:**
- 首次生成时自动推断（`build_assets.py`）
- 迁移时保留已有值，空值才推断
- 批量工具：`python scripts/infer_layers.py assets/`

## 技术债务

### 已解决

- ✅ disabled 条目默认 note 过长导致验证误报（2025-01 修复）
- ✅ layer 字段生成为空值但被识别为缺失（2025-01 修复）
- ✅ Windows 中文输出乱码（全套脚本统一内置 UTF-8 输出，2026-11 补齐）
- ✅ 预算统计缺少分层视图（`validate_assets.py` 已输出 `persona / identity / behavior / narrative / disabled` 分层统计，2026-11 解决）
- ✅ `compile_card.py` 的 keys 可达性检查（已在编译层实现启用+非常驻+空 keys 死条目拦截与 `--allow-dead-entries`）

### 待解决

- 🔲 replay_hitrate.py 的输出格式需要优化（p50/p90/p99 可视化）

## 工具链速查

### 拆卡补全流程（5分钟）

```bash
# 1. 解包 + 生成骨架（自动推断 layer）
python scripts/extract_card.py card.png cards/角色名/card.json
python scripts/build_assets.py --card cards/角色名/card.json --out characters/

# 2. 归档级验证（立即通过）
python scripts/validate_assets.py characters/角色名/ --level archival

# 3. 人工补全 persona.yaml 5个字段（5分钟）
#    codename / archetype / tone / relationship.form / memory_policy

# 4. 批量标记 disabled（1秒）
python scripts/mark_disabled.py characters/角色名/

# 5. 平台级验证（部署前）
python scripts/validate_assets.py characters/角色名/ --level platform
```

### 卡更新（字段级合并）

```bash
# migrate 模式：机器字段刷新，人工字段保留
python scripts/build_assets.py --card 新卡.json --out characters/ --migrate
```

## 注意事项

### Windows 用户

- 所有 Python 脚本已内置 UTF-8 强制输出，无需 `PYTHONIOENCODING=utf-8`
- Git 换行符警告正常（LF→CRLF），不影响功能

### 验证级别选择

- **archival**: 拆卡后立即归档，无需等待补全
- **platform**: 部署到聊天平台前，确保所有字段完整

### layer 推断规则

1. **behavior**: roleplay逻辑、MVU变量系统、格式约束
2. **identity**: 角色核心、世界观总纲、初始化变量
3. **narrative**: 剧情大纲、百科（默认）

推断逻辑见 `build_assets.py::infer_layer()`（与 `infer_layers.py` 共享单一实现，卡片中性）
