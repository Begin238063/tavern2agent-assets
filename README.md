# tavern2agent-assets

> **本仓库是 [tavern2agent](https://github.com/Xerxes-2/tavern2agent)（Xerxes-2）的 fork**，只保留「解包 + 审计 + 资产骨架生成」能力，把 SillyTavern 角色卡（PNG/WEBP/JPEG/CHARX/JSON）提取为**平台无关的角色资产**，供任意聊天平台（AstrBot、SillyTavern、Open WebUI 等）的渲染器消费。
> **本 fork 不生成任何运行时工程**（无 pi 工程 / extension.ts / start.sh）；如需 pi 运行时转换，请使用上游 tavern2agent。

## 与上游的关系

- 保留并增强：`scripts/extract_card.py`（增加 `_source_file`、CCv3 `.charx` 解包与自动建目录）、`list_entries.py`（增加 `--stats`、`--audit` 全量字段与扩展脚本分类审计、`--suggest-runtime` 运行时排除清单生成）、`get_entry.py`（增加 `--id` 按稳定条目 ID 查询）；解包/审计/字段审计方法论改写为「资产化」语境。
- 新增：`scripts/build_assets.py`（资产骨架生成器，支持 `--migrate` 字段级合并与 `runtime.yaml` 排除）、`scripts/validate_assets.py`（资产校验器，persona 完整性 + lore 路由门禁 + 分层 token 统计 + ST 残留扫描）、`scripts/replay_hitrate.py`（keys 命中率回放）、`scripts/_conditions.py`（EJS 条件门控解析）、`assets_schema/`（字段规范与示例）。
- 删除：上游的 pi 运行时相关全部内容（pi 专属 references/、start.sh、decision gate、event packs 等）。

### 同步策略：不做同步（与上游无共同祖先）

本仓**不是 clone、也不是 GitHub 的 fork 网络关系**，而是复制文件新建的仓（初始 commit 为 `Local working copy`）。两边历史完全无关，`git merge-base HEAD upstream/main` 为空。

- **`git merge upstream/main` 不成立**，本仓不做任何形式的同步。合并会把上游已删的 pi 运行时内容整批带回来，与本 fork 的定位相反；强行加 `--allow-unrelated-histories` 等于重开一个仓，不是同步。
- **比对仍然可用**（`git diff` 比的是树而不是历史）：`git diff upstream/main -- <path>` 是本仓与上游之间唯一可靠的手段。
- **只在遇到解包/解析异常时**看上游有没有可搬运的修复，不做定期同步：

  ```bash
  git fetch upstream && git diff upstream/main -- scripts/extract_card.py scripts/get_entry.py
  ```

  `get_entry.py` 与上游逐字节相同，修复可直接搬；`extract_card.py` 只多 `_source_file`，差异极小；`list_entries.py` 因本仓新增 `--stats` 已有分叉，需挑着看。
- **上游是否还有本仓缺的东西**（2026-11-14 复核）：上游整仓自 `2026-07-11`（`601b1c3`）未再提交，`scripts/` 自 fork 以来无新增改动；上游文档层沉淀的三项方法论（`mvu-mapping.md` 对 `[initvar]`/`[mvu_update]` 与 `[mvu_plot]` 的反幻觉区分、`script-analysis.md` 对 `tavern_helper.scripts` / `regex_scripts` 的分类审计、`validation.md` 的 ST 补丁残留扫描）均已内化进本仓 `list_entries.py`、`build_assets.py` 与 `validate_assets.py`。
- **无法向上游提 PR**：无共同祖先 ⇒ GitHub 形成不了可合并的 PR。若要把改动回馈上游，需基于上游历史另建分支打 patch。

## 下游项目

本工具仓被以下项目依赖：
- **character-studio**（私有仓库）：通过 `toolchain.py` 子进程调用本仓脚本

### 兼容性约定

修改以下脚本的参数或输出格式时，需通知下游项目更新调用代码：
- `extract_card.py`（已向后兼容支持 `.charx` 输入与自动创建父目录，2026-11-14）
- `build_assets.py`
- `validate_assets.py`（已新增可选 `--level` 参数，2026-09-07；新增可选 `--summary` 参数，2026-11-14，默认输出兼容）
- `compile_card.py`
- `replay_hitrate.py`

### 变更后检查清单
- [ ] 更新本仓 `CHANGELOG.md`
- [ ] 如有参数变更，通知下游项目更新 `toolchain.py`
- [ ] 如有输出格式变更，通知下游项目更新解析逻辑

## 用法

### 基础流程

```bash
python3 scripts/extract_card.py card.png cards/<角色名>/card.json   # ① 解包并归档（支持 PNG/WEBP/JPEG/CHARX/JSON）
python3 scripts/list_entries.py cards/<角色名>/card.json --audit    # ② 全量审计（data 字段 + 世界书 + TH/正则脚本 + 方案建议）
python3 scripts/list_entries.py cards/<角色名>/card.json --suggest-runtime  # ② 扫描 ST 纯运行时管道，输出 runtime.yaml 建议清单
python3 scripts/get_entry.py cards/<角色名>/card.json --id 433      # ② 按条目稳定 id（或数组下标）读单条全文
python3 scripts/build_assets.py --card cards/<角色名>/card.json --out characters/  # ③ 生成资产骨架（幂等，自动读取同目录 runtime.yaml）
python3 scripts/build_assets.py --card <新卡> --out characters/ --migrate          # ③ 卡更新：字段级合并（机器字段刷新、人工字段保留）
python3 scripts/validate_assets.py characters/<角色名>/ --summary   # ④ 完整性与可用性门禁（--summary 折叠大型卡警告，展示分层 token 统计）
python3 scripts/replay_hitrate.py --lore characters/<角色名>/lore --chats 聊天记录.txt  # ⑤ keys 命中率回放（scan_depth 滑窗 + p50/p90/p99）
python3 scripts/compile_card.py --assets characters/<角色名>/ --out out/card.json --resolve-refs  # ⑥（可选逃生口）编译回 ST 卡
```

### 补全工作流（归档 → 平台部署）

从 `build_assets.py` 生成的骨架到可部署资产，需要补全人工字段：

```bash
# 场景 A：归档级验证（只检查机器字段，适合初次拆卡）
python3 scripts/validate_assets.py characters/<角色名>/ --level archival

# 补全流程
# 1. 补全 persona.yaml 的 5 个空字段
#    - identity.codename: 角色代号/别称
#    - identity.archetype: 角色原型/定位
#    - style.tone: 语气基调
#    - relationship.form: 关系形态
#    - relationship.memory_policy: 记忆/好感策略

# 2. 推断并填充空 layer 字段（自动）
python3 scripts/infer_layers.py characters/<角色名>/

# 3. 标记 disabled 条目的处置说明（自动）
python3 scripts/mark_disabled.py characters/<角色名>/

# 4. 平台级验证（检查所有字段，部署前必须通过）
python3 scripts/validate_assets.py characters/<角色名>/ --level platform
```

**验证级别说明：**
- `--level archival`（归档级）：只检查机器字段（_derived 块），适合初次拆卡、快速归档
- `--level platform`（平台级，默认）：检查所有字段，包括人工补全的 persona/layer/note，部署前必须通过

## 目录结构

```txt
SKILL.md                       # agent 入口流程（资产化）
scripts/
├── extract_card.py            # 解包（支持 PNG/WEBP/JPEG/CHARX/JSON，自动建父目录）
├── list_entries.py            # 卡片与世界书审计（支持 --audit / --suggest-runtime / --stats）
├── get_entry.py               # 条目查看（支持按下标或 --id 查询）
├── build_assets.py            # 资产骨架生成器（纯标准库、幂等；--migrate 字段级合并；支持 runtime.yaml）
├── infer_layers.py            # 批量推断空 layer（复用 build_assets.infer_layer）
├── mark_disabled.py           # 批量标记 disabled 条目默认处置说明
├── validate_assets.py         # 资产校验器（persona 完整性 + lore 路由门禁 + 分层 token 统计 + --summary）
├── replay_hitrate.py          # keys 命中率回放（真实聊天历史，滑窗 + p50/p90/p99）
├── compile_card.py            # 资产 → ST 卡编译器（selective 归一化 + 跨资产 refs 解析 + 死条目检查）
├── _conditions.py             # EJS 条件门控解析器（纯标准库）
└── _mini_yaml.py              # 工具族共享的极简 YAML 解析器（纯标准库）
assets_schema/
├── persona.schema.yaml        # persona.yaml 字段规范
├── lore.schema.yaml           # lore/*.yaml 字段规范（激活语义 + _derived 块约定）
├── dialogs.schema.yaml        # dialogs/*.yaml 字段规范
└── persona.example.yaml       # 完整示例（Bot）
cards/<角色名>/                # 原始 card.json 归档（只读源，随 git 版本化）
├── card.json
└── runtime.yaml               # 可选：卡侧纯 ST 运行时管道排除清单
characters/<角色名>/           # 产物（--out 默认 characters/）
├── persona.yaml
├── validation.yaml            # 私有仓库持有：阈值/默认动作（budget_tokens / default_window），工具不内置
├── lore/*.yaml                # _derived 块=机器字段（激活语义/内容/comment/sha）；块外 keys/note/layer/budget_tokens=人工字段
├── dialogs/                   # first_mes.yaml / alternate_NN.yaml / example_NN.yaml
└── provenance.md
```

## 硬约束

- 平台无关：产物不含任何聊天平台/运行时专属概念；ST 的 depth/position 映射为平台无关注入语义 `inject_at`（resident/context_head/near_input）。AstrBot 映射：resident 编译进 Persona system_prompt（静态可缓存，禁止插件每轮追加）；context_head/near_input 走 `extra_user_content_parts` + `.mark_as_temp()`（只参与本轮、不写会话历史）；禁止写入 contexts（真实会话历史，会重复注入）。
- 只读安全：脚本只读卡文件与项目文件；build_assets.py 只向 `--out` 目录写入。
- 纯标准库：build_assets.py / validate_assets.py / replay_hitrate.py 仅用 Python 标准库 + 共享 `_mini_yaml.py`。
- 幂等：重复运行不产生重复内容；已存在文件默认跳过（`--force` 覆盖、`--migrate` 字段级合并），打印 跳过/更新/新建/迁移。
- 激活语义无损：keys / secondary_keys / selective / constant / insertion_order / case_sensitive / scan_depth / prevent_recursion / use_regex / inject_at / budget_tokens 全部结构化保留；**路由由 layer 决定**（未分层 / layer 非法 / 常驻超预算 / disabled 未处置由 validate 机器拦截；ST 死条目由 compile_card.py 检查）。
- 资产层忠实于卡、归一化在编译层：selective=true 且 secondary 为空保持原卡透传（_derived 机器字段），`compile_card.py` 编译时按 selective=false 处理，validate 只给信息。
- 估算口径：token 一律用 estimated_tokens（中文/全角 1 字符≈1 token、ASCII 4 字符≈1 token），用线上真实用量校准后更新。
- 阈值/默认动作归私有仓库：工具对任何卡中性，不内置预算/回放窗等默认值（读资产目录 `validation.yaml`，或 CLI 显式传参）；平台专属编译器（如 compile_astrbot.py）也归私有仓库，不进本工具仓库。
- 机器保证：first_mes / alternate_greetings 自动资产化；语义补全（style/relationship 占位字段）由 validate_assets.py 非空门禁强制，人工承诺不再算数。
- 中文：全部注释、文档、输出信息使用中文。

## 许可证

MIT License，版权归原作者 **Xerxes-2**（[上游 LICENSE](LICENSE) 原样保留）。
