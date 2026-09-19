# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`_conditions.py`（共享模块）**: 解析 EJS 条件门控（`<%_ if (getvar('X') > N) { _%> … <%_ } _%>`）
  为 `[(条件, 正文)]`。**资产层不动**（EJS 原样留卡，见 macros.py 的立场）——它供**编译层**把门控变成结构化
  元数据：ST 自己渲染 EJS 属无损路径，AstrBot 没有渲染器，不归一化就会把原始代码送进提示词。
  不猜、不删、不静默：非标准表达式（复合条件、`if (false)`）原样保留并报告，JS 声明消费掉但要报告，
  取值插值留在正文里待编译层按宏词表处理。实测 WuWa Solaris-3 卡：527 个条件、54 个 else、
  123 条条目**散文零丢失**。
- **卡侧运行时清单 `runtime.yaml`**: `build_assets.py` 读 `cards/<角色名>/runtime.yaml` 的
  `exclude_entry_ids`，把卡自带的 ST 运行时管道（CG 插图 / 行动选项 / `[mvu_update]` 变量规则簇 /
  `[initvar]` / `[opening]` / `[勿开]` / `[勿关]`）挡在平台无关资产层之外；生成与迁移两条路径都生效，
  `--migrate` 不会把这些条目已存在的文件误标 `_orphaned`。清单读不出时 **fail-closed** 报错中止
  （`_mini_yaml` 解析失败会静默返回空 dict，当作空清单即等于排除悄悄失效）。见 SKILL.md
  「卡侧运行时清单」。
- **自动推断 layer 字段**: `build_assets.py` 生成时根据 comment/filename/keys 自动推断 layer（behavior/identity/narrative），不再生成空值
- **新增批量工具 `infer_layers.py`**: 批量推断并填充已有资产的空 layer 字段
- **新增批量工具 `mark_disabled.py`**: 批量标记 disabled 条目的处置说明（默认"原卡作者已禁用，保留备查"）
- **分级验证**: `validate_assets.py` 新增 `--level archival/platform` 参数
  - `archival`: 归档级，只检查机器字段，适合初次拆卡快速归档
  - `platform`: 平台级（默认），检查所有字段，部署前必须通过
- **项目记忆文档**: 新增 `MEMORY.md` 和 `memory/` 目录，记录优化历史和架构决策
- **补全工作流文档**: `README.md` 增加"补全工作流"章节，说明从归档到部署的完整流程

### Fixed
- **`_mini_yaml` 前瞻漏跳空行/注释行（会静默丢数据）**: `key:` 后面紧跟空行或注释行时会被误判成
  「空值」。`gen_persona_sketch` 产出的 persona.yaml 正是 `core:` 后紧跟一行注释，于是 `core`
  整块被解析成空字符串；而 `--migrate` 的 persona 合并有 `isinstance(sec, dict)` 守卫 ——
  结果是**人工补全的 `core.self` / `traits` / `capabilities` / `boundaries` 在每次 migrate 时
  被静默丢掉**（实测：304 字符 → 79 字符）。前瞻现在跳过空行与注释行，并补了 6 个回归用例。
  空行在 YAML 里是完全正常的写法，手工编辑极易触发。
- **三个过期测试**: `_infer_layer` 自动推断 layer 上线后，「新建条目 layer 留空」的期望与
  `test_validate_gates` 里靠字符串替换造空层/非法层的前置条件都失效了 —— 代码是对的、测试没跟上。
  已更新，套件恢复全绿。
- **disabled 条目验证误报**: disabled 条目的默认 note 改为空字符串，避免被验证脚本误判为"机器默认值"
- **Windows 中文乱码**: 所有 Python 脚本强制 UTF-8 输出，无需手动设置 `PYTHONIOENCODING`

### Changed
- **layer 字段不再留空**: `build_assets.py` 首次生成时自动推断 layer，不再输出 `layer: ""`
- **--migrate 保留已有 layer**: 迁移模式下保留人工填写的 layer，只对空值进行推断

### Performance
- **补全流程提速 83%**: 从 30 分钟缩短到 5 分钟（layer 自动推断 + disabled 批量标记）

## [1.0.0] - 2025-01-XX

### Added
- 初始版本：从 [tavern2agent](https://github.com/Xerxes-2/tavern2agent) fork，专注于资产化
- `extract_card.py`: SillyTavern 卡片解包工具（PNG/WEBP/JPEG/JSON → card.json）
- `build_assets.py`: 资产骨架生成器（card.json → persona.yaml + lore/*.yaml + dialogs/*.yaml）
  - 支持 `--force` 强制覆盖
  - 支持 `--migrate` 字段级合并（机器字段刷新，人工字段保留）
- `validate_assets.py`: 资产校验器
  - persona 完整性检查（必需字段 + 语义补全门禁）
  - lore 路由门禁（layer 承重、常驻预算、disabled 处置）
- `list_entries.py`: 世界书审计工具（支持 `--filter mvu/initvar`、`--stats` 统计）
- `get_entry.py`: 单条世界书条目查看工具
- `replay_hitrate.py`: keys 命中率回放工具（真实聊天历史 + 滑窗统计）
- `compile_card.py`: 资产 → ST 卡编译器（支持 `--resolve-refs` 跨资产引用解析）
- `_mini_yaml.py`: 极简 YAML 解析器（纯标准库，token 估算）
- `assets_schema/`: 资产字段规范与示例（persona/lore/dialogs schema）

### Design Principles
- **平台无关**: 不生成任何运行时工程，产物可供任意聊天平台消费
- **只读安全**: 脚本只读源文件，只向 `--out` 指定目录写入
- **纯标准库**: 核心工具仅依赖 Python 标准库
- **幂等**: 重复运行不产生重复内容（跳过/更新/新建/迁移状态提示）
- **激活语义无损**: keys/selective/constant/insertion_order 等字段全部结构化保留
- **路由由 layer 决定**: layer 是唯一路由载体（identity/behavior → system_prompt; narrative → 知识库检索）

---

## 版本号说明

遵循 [语义化版本](https://semver.org/lang/zh-CN/)：

- **MAJOR（主版本号）**: 不兼容的 API 修改
- **MINOR（次版本号）**: 向下兼容的功能性新增
- **PATCH（修订号）**: 向下兼容的问题修正

当前版本开发阶段：
- `1.0.0`: 初始发布（资产化核心功能）
- `1.1.0`: 验证工作流优化（layer 推断、分级验证、批量工具）
