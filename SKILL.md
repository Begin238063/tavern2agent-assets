---
name: tavern2agent-assets
description: 用户提供 SillyTavern 角色卡（PNG/JSON）并要求提取、资产化、转成平台无关角色资产时使用；产出角色资产骨架（persona/lore/dialogs/provenance），供任意聊天平台的渲染器消费。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# 角色卡 → 平台无关角色资产（tavern2agent-assets）

> **重要声明**：本 skill 只产出平台无关的角色资产，**不生成任何运行时工程**；如需 pi 运行时转换，请使用上游 tavern2agent。

本 skill 是上游 tavern2agent 的 fork：只保留「解包 + 审计 + 资产骨架生成」能力。它把 SillyTavern 角色卡（PNG/WEBP/JPEG/JSON）提取为可版本化、可复用、跨平台的角色资产库，供 AstrBot、SillyTavern、Open WebUI 等任意聊天平台的未来渲染器消费。资产库中的任何文件都不含平台专属概念（pi、extension.ts、pi session、subagent、prompt orchestrator、start.sh 等）。

## 核心原则

- 资产 = 平台无关的事实与风格素材；渲染 = 平台侧的事，本 skill 不做。
- 只提取与资产化，不生成运行时：没有 engine、没有工具注册、没有 prompt 编排、没有启动脚本。
- 保留卡片语义（人设、世界书、示例对话、来源），剥离 ST 运行时补丁（HTML 状态栏、JSON Patch 输出、COT 标签、宏语法）——只提取其背后的内容。
- 只读安全：所有脚本只读卡文件与项目文件；build_assets.py 只向 `--out` 指定的资产目录写入。
- 幂等：重复运行 build_assets.py 不产生重复内容；已存在文件默认跳过（`--force` 覆盖），打印 跳过/更新/新建。
- 中文：全部注释、文档、输出信息使用中文。

## 调用流程

### ① 收卡

用户提供角色卡文件（PNG / WEBP / JPEG / JSON）。确认输出目录；目录存在时先问：覆盖、增量、另建。

### ② 解包与归档

```bash
python3 scripts/extract_card.py <card.png|webp|jpg|json> cards/<角色名>/card.json
```

得到 card.json（原始结构化字段，含 spec + data；v1 已归一化为 v2，带 `_normalized_from_v1` 标记）。

**card.json 归档位置（P1-2）**：原始 JSON 一律归档到 `cards/<角色名>/card.json`，不散落工作目录。该目录只读源、随 git 版本化；后续 re-extract 可覆盖 card.json，但 sha256 由 provenance.md 记录，用于追溯每次资产化对应的卡版本。

### ③ 世界书审计

```bash
python3 scripts/list_entries.py cards/<角色名>/card.json                  # 全部条目概览
python3 scripts/list_entries.py cards/<角色名>/card.json --filter mvu     # 只看 [mvu]/[mvu_update] 标记条目
python3 scripts/list_entries.py cards/<角色名>/card.json --filter initvar # 只看 [initvar] 条目
python3 scripts/list_entries.py cards/<角色名>/card.json --stats          # 一键统计：类型×标记×角色条目数
python3 scripts/get_entry.py cards/<角色名>/card.json <index>             # 读单条全文
```

审计要点：

- 条目计数：共多少条世界书条目；大型卡直接用 `--stats` 拿类型×标记×角色条目数统计。
- **v3 卡注意**：条目常无 `keys` 字段（list_entries 显示的「key」实为 comment）；build_assets 已支持 keys 为空回退 comment 命名，产出文件名可读（如 `Bot.yaml`），不会退化成 `entry_NN.yaml`。**keys 只对 ST 运行时有意义**——它不属于资产路由（路由由 layer 决定），compile_card.py 编 ST 卡时做可达性检查（启用+非常驻+keys 空 = 死条目），validate 不检查 keys。
- 标记识别：[mvu] / [mvu_update] / [mvu_plot] / [initvar] 条目，这些标记在资产化时写入 lore 模块的 note。
- disabled 条目：不能默认丢弃（可能是 DLC、路线、草稿规则），逐条决定去向。
- 多角色 / 世界观卡：先按「多角色 / 世界观卡（方案 A / B / C）」章节判断类型，再决定怎么资产化。

### ④ 生成资产骨架

```bash
python3 scripts/build_assets.py --card cards/<角色名>/card.json --out characters/
python3 scripts/build_assets.py --card cards/<角色名>/card.json --out characters/ --name 自定义名   # 可选：覆盖角色名（缺省用卡内 data.name）
python3 scripts/build_assets.py --card cards/<角色名>/card.json --out characters/ --force          # 整体覆盖（会清掉已做的人工补全，慎用）
python3 scripts/build_assets.py --card cards/<角色名>/card.json --out characters/ --migrate        # 字段级合并（推荐）：机器字段按新卡刷新，人工字段原样保留
```

> v3 卡条目无 keys 时自动回退到 comment 命名；同名条目自动加 `_2/_3` 后缀去重，内容不丢。
> 世界卡请用 `--name <世界名>` 生成世界资产，再按「多角色 / 世界观卡」章节为角色建 persona。
> **上游卡更新时反复走 `--migrate`**：跳过会让老文件永远拿不到新字段，--force 会清掉人工补全；migrate 按 `_entry_id`（稳定 id）匹配旧文件，只重写 `_derived` 块（content/comment/激活语义/sha），块外人工字段（keys/note/layer/budget_tokens）原样保留，schema 新增字段以默认值补齐。
> **压缩 = 改 layer，一个动作**：路由由 layer 决定（identity/behavior→Persona；narrative→知识库检索），压缩就是把条目从 identity/behavior 改成 narrative（人工字段，migrate 天然保留）；**不再修改任何 _derived 字段**（constant/keys 是源卡透传信息，migrate 会按卡刷新，勿动）。

产出 `characters/<角色名>/`：

- `persona.yaml`——画像骨架（identity / core / style / lore_refs / dialogs_refs / relationship / provenance）
- `lore/<key>.yaml`——世界书条目拆分。**激活语义无损**：机器字段归入 `_derived` 块（content/comment/keys 透传之外还有 secondary_keys/selective/constant/enabled/insertion_order/case_sensitive/scan_depth/prevent_recursion/use_regex/inject_at/extensions_json/_entry_id/_card_sha），块外人工字段（keys/note/layer/budget_tokens）；[mvu]/[initvar] 标记写进 note 初值
- `dialogs/first_mes.yaml`——**开场白自动资产化**（role: char，机器保证 first_mes 去向，不再依赖人工记得补）
- `dialogs/alternate_NN.yaml`——备用开场白（alternate_greetings，role: char）
- `dialogs/example_NN.yaml`——mes_example 按 {{user}}/{{char}} 回合拆分
- `provenance.md`——来源文件名、spec 版本、角色名、提取时间、sha256、原始字段清单

字段规范见 `assets_schema/persona.schema.yaml` / `lore.schema.yaml` / `dialogs.schema.yaml`，完整示例见 `assets_schema/persona.example.yaml`。

**inject_at 语义映射**（不抄 ST 的 before_char/after_char/depth）：constant=true → `resident`；after_char → `near_input`；before_char 且 depth≤1 → `near_input`；before_char 且 depth≥2 → `context_head`。

**路由模型（layer 是唯一路由载体）**：identity/behavior → 编译进 Persona 的 system_prompt（静态、可缓存）；narrative → 进知识库靠内容检索。constant 与 keys 不参与资产路由——`constant` 只是源卡透传信息（仅 compile_card.py 编 ST 卡时用），`inject_at` 同理（仅 compile_card.py 反映射为 ST depth/position）。**AstrBot 侧不消费 inject_at**，路由由 layer 决定。

**AstrBot 侧注入（勿踩坑）**：

| 路由 | AstrBot 去向 | 约束 |
|---|---|---|
| identity / behavior | **编译进 Persona 的 system_prompt**，部署时经 `PersonaManager.create_persona / update_persona` 推送 | 静态、可缓存。**禁止插件每轮往 system_prompt 追加**——拼每轮变化的内容会破坏服务端提示词缓存，成本约 7-20 倍；且插件 `on_llm_request` 在核心 persona 注入之前执行，本就控制不了相对 persona 块的位置 |
| narrative | 知识库检索命中 → `extra_user_content_parts` | 调 `.mark_as_temp()`（≥ v4.24.0）：只参与本轮请求、不写入会话历史 |

**禁止项**：`contexts` 是**真实会话历史**，不可写入——往里写会被持久化，下一轮它既在历史里又被重新注入，就是 AstrBot 内置长期记忆踩过的重复注入那一类 bug。

**预算门禁的立场**：常驻烧进 persona 且命中缓存后 token 成本不高，但**注意力占用照旧**——常驻压缩的真正理由是注意力，不是钱；不要因为缓存便宜就放松 `--budget-tokens` 门禁。

### ⑤ 语义补全

- 填空 persona.yaml：`identity.codename / archetype`、`style.tone / habits`、`relationship.form / memory_policy`；`core.traits / capabilities / boundaries` 按需增删。
- **分层（每条 lore 必做，唯一需要的人工判断）**：逐条判「这条信息会不会改变她在任意一句话里的反应方式」——会 → `layer: identity`（人格本体）或 `layer: behavior`（行为规则），编译进 Persona；不会 → `layer: narrative`（叙事/设定），进知识库靠内容检索。**只改块外 layer 这一个字段，不修改任何 _derived 字段**（constant/keys 都是源卡透传信息，migrate 会按卡刷新，勿动）。
- disabled 条目在 `note` 里写下处置去向（保留/丢弃/待定 + 理由）——机器默认 note 不算数，validate 强制改写。
- **常驻压缩（大卡必做）**：就是上面分层的重排——把不需要每轮常驻的条目从 identity/behavior 改成 `layer: narrative`，一个人工字段，migrate 天然保留，压缩决策不会被刷掉。死代码（如 `<%_ if (false) { _%>` 开头的条目）`enabled: false` + 处置 note（enabled 是 ST 透传，卡更新后需复核）。
- 确认 lore 模块归属与命名；核对 `lore_refs` / `dialogs_refs` 与实际文件一一对应（含方案 C 的跨资产引用）。
- 检查 mes_example 拆分是否合理（回合边界、role 标记）。first_mes / alternate_greetings 已自动资产化为 `dialogs/first_mes.yaml` / `alternate_NN.yaml`，只需确认内容，无需人工补。
- **大规模世界书（数百条）分批补全**：按 系统规则 → 角色档案 → 世界 lore → 剧情章节 四类顺序，每轮只补一类、多轮提交（靠 git 记录进展），不要试图一次补完。

### ⑥ 机器校验 + 提交

```bash
python3 scripts/validate_assets.py characters/<角色名>/                                   # layer 路由门禁 + 完整性与可用性
python3 scripts/validate_assets.py characters/<角色名>/ --budget-tokens 16384             # 覆盖 validation.yaml 的预算（阈值归私有仓库，工具不内置默认）
python3 scripts/validate_assets.py characters/<角色名>/ --strict                          # 把警告升级为错误
python3 scripts/validate_assets.py characters/<角色名>/ --report-unlayered                # 只输出未分层条目工作清单（分批分层用）
git add cards/<角色名>/ characters/<角色名>/
git commit -m "资产化 v1，来源 hash: <card.json 的 sha256 前 12 位>"
```

校验项（纯标准库）：

- **persona**：字段齐全 / refs 与 sample 存在 / sha 一致 / 5 个占位字段非空（语义补全门禁）。
- **lore 路由门禁（layer 承重）**：
  - enabled 且 layer 缺失/空 → **报错**（未分层；不逐文件刷屏，汇总为[分组工作清单]输出，`--report-unlayered` 只看它）；
  - enabled 且 layer 不在 identity/behavior/narrative → **报错**（layer 是路由载体，取值非法不可路由）；
  - resident_tokens（persona 常驻 + enabled 且 layer ∈ {identity, behavior}）超预算 → **报错**（**constant 不参与计算**；理由是注意力占用，不是 token 成本——缓存便宜不要放松门禁；预算值来自资产目录 `validation.yaml`（私有仓库持有）或 `--budget-tokens`，未配置则跳过预算门禁）；
  - disabled=true 且 note 空**或仍是机器默认值** → **报错**（迫使人工写下处置去向：保留/丢弃/待定 + 理由；机器默认 note 不算数）；
  - enabled 且 layer: narrative 且 comment 空且 content < 40 字 → 警告（知识库文档无标题过短，检索命中概率低）；
  - keys 过泛（单字 / ≤2 字符 / 全匹配正则）→ 警告（keys 不再承重，仅提示）；inject_at 取值出词表 → 警告（仅编译层消费）；孤立条目（`_orphaned: true`）→ 警告（要求 note 写处置）；
  - enabled 且 selective=true 且 secondary_keys 空 → **信息**（ST 空排除=无操作；归一化由 compile_card.py 做，--strict 不升级）。

**估算口径（estimated_tokens）**：中文/全角 1 字符 ≈ 1 token、ASCII 每 4 字符 ≈ 1 token；拿到一次线上真实请求用量后按实测比例校准并更新本处。

validate_assets.py 未通过则回到 ⑤ 补全，不许带空占位提交。草案注明「资产化 v1，来源 hash: xxx」。本轮结束。

### ⑦ 命中率回放（keys 质量 → 数字）

```bash
python3 scripts/replay_hitrate.py --lore characters/<角色名>/lore --chats 聊天记录.txt               # 子串匹配
python3 scripts/replay_hitrate.py --lore characters/<角色名>/lore --chats 聊天记录.txt --regex         # keys 按正则匹配（use_regex=true 时）
python3 scripts/replay_hitrate.py --lore ... --chats ... --default-window 8                           # scan_depth=null 的回溯窗口（默认 8，按运行时全局默认校准）
```

**滑窗模拟**：逐轮判定——条目在某轮激活当且仅当某个 key 出现在最近 window 条消息里（window=原卡 scan_depth，null 用 --default-window）；常驻每轮激活、disabled 从不激活。不再用全量文本一次匹配（会系统性高估命中率）。输出三个数 + 分布：

1. **命中率 0 的启用条目**：keys 写错，或该概念在你们实际聊天里根本不出现（无效资产）——前者修，后者删或改常驻；
2. **命中率 > 50% 的触发条目**：实质上已是常驻却没进常驻预算——改 constant=true 并计入预算，或收紧 keys；
3. **每轮设定开销分布**：常驻恒定 + 均值 / **p50 / p90 / p99**（estimated_tokens）——预算爆不爆看尾部（p90/p99），不是均值。

聊天记录格式：每行一条消息；JSON 行取 content/message/text 字段。拿几千条真实历史重跑才有统计意义（10 条算出的期望值不可信）。拿到这三个数，「先补设定还是先调性格」不再需要猜。

### ⑧ 编译 ST 卡（**可选逃生口**，非必经流程）

> 本步骤是能力不是流程：compile_card.py 是 extract_card.py 的逆运算（拆卡工具能编回卡是正常职能），
> 需要 ST 侧对照/试玩时才用；**完整资产化流程到 ⑦ 结束**，不强制走 ST。
> 平台专属编译器（如未来的 compile_astrbot.py）属私有数据仓库，**不进本工具仓库**。

```bash
python3 scripts/compile_card.py --assets characters/<角色名>/ --out out/card.json
python3 scripts/compile_card.py --assets characters/<角色名>/ --out out/card.json --resolve-refs
    # 方案 C：解析 persona 的 lore_refs / dialogs_refs（含 ../ 跨资产），把引用到的世界条目并入编译结果
```

把资产编译回 ST 可导入的 chara_card_v3 卡（ST 支持 JSON 导入）。**归一化在这一层**：

- **selective 归一化**：selective=true 且 secondary_keys 为空（ST 空排除=无操作）→ 编译时按 false 输出；资产层保持原卡透传（_derived 机器字段），不改文件；
- **inject_at → ST position/depth** 反映射：优先从 extensions_json 恢复原卡值，缺失时按语义映射（near_input/resident→depth 0、context_head→depth 4）；
- keys/constant/content/insertion_order/enabled/use_regex/case_sensitive/scan_depth/prevent_recursion 全部透传；
- **ST 可达性检查**：enabled 且 constant=false 且 keys 为空 → 错误（该条目在 ST 中永不激活；`--allow-dead-entries` 降为警告）。

**何时用**：需要在 ST 侧对照原卡试玩/验证（如对话沙盒对比）时，编译一张卡导入即可，属可选动作；creator_notes 会注明「由资产编译生成，验证用卡」。

## 多角色 / 世界观卡（方案 A / B / C）

单角色卡走上面的标准流程即可；对「大型世界卡 / 合集卡」，先判断类型再选方案。

**判断信号**（逐项检查 `data`）：
- `extensions.group_only_greetings` 非空 → ST 群组卡，天然多角色。
- `character_book.entries[]` 含「角色档案 / `<Character_X>`」类条目（如 60+ 个角色定义）→ 世界书内嵌多角色。
- `alternate_greetings` 各条是否同一主角（如都是「你 / 同一主角」的不同线路）→ 区分「多线路」与「多角色」。
- `creator_notes` 是否声明为合集卡；`data.name` 是世界名还是角色名。

**方案选型**：
- **A（整体单角色化）**：卡本身有明确主角人格、角色档案不多。`chars/<主角色名>/`，世界书全进 lore。
- **B（拆多角色）**：`group_only_greetings` 非空、或各角色有独立开场/人格的合集卡。把大卡切成单角色子卡（按角色档案条目 + 对应 greetings 抽成子 JSON），逐个 build_assets。
- **C（世界观 + persona 引用，推荐用于世界卡）**：先 `build_assets.py --name <世界名>` 生成世界资产承载全部 lore/dialogs；再为每个角色建 `chars/<角色>/persona.yaml`，`lore_refs` / `dialogs_refs` 用 `../<世界名>/...` 跨资产引用——**只引用不复制**。

**方案 C 的跨资产引用约定**：refs 必须带引号（`- "../<世界>/lore/x.yaml"`）；行内不得写 `#` 注释（validate 的行级解析会把注释当路径）；解析后必须仍落在 `characters/` 资产树内（见 persona.schema.yaml 的 refs 契约）。跨资产引用由 `compile_card.py --resolve-refs` 在编译时解析并入卡。

## 信息源 → 资产去向

| 看什么 | 路径/信号 | 资产化去向 |
|---|---|---|
| 基础设定 | description / personality / scenario / system_prompt | persona.core.self + traits/capabilities/boundaries 草稿 |
| 开场 | first_mes / alternate_greetings / group_only_greetings | dialogs/first_mes.yaml + alternate_NN.yaml（**自动，机器保证**）；group_only_greetings 按 alternate_greetings 处理 |
| 世界书 | character_book.entries[] | lore/<key>.yaml（保留 constant/selective/disabled 与标记） |
| 初始状态 | [initvar] / YAML / 变量表 | lore 模块 note 保留标记；不生成任何运行时状态 |
| 规则更新 | [mvu_update] / 变量变化 | lore 模块 note 保留标记；语义进 relationship/memory_policy 草稿 |
| 示例对话 | mes_example | dialogs/example_NN.yaml（按 {{user}}/{{char}} 拆回合） |
| 作者说明 | creator_notes | provenance.md 备注；敏感秘密标注「资产内部，勿公开」 |
| TH scripts / regex scripts | 状态栏/输出补丁 | 剥离补丁语法；可提取的规则/字段进 lore 或 persona 备注 |

## 卡侧运行时清单（runtime.yaml）

卡自带的 **ST 运行时管道**不属于平台无关资产，不该落进 `characters/`——`ARCHITECTURE.md`
已经定过这条边界：「原卡的 `constant` / `keys` / `inject_at` 不参与路由，那是 ST 的运行时」。
典型条目：CG 插图（EJS 模板 + `<pic>` 标签 + `{{roll}}`）、行动选项、`[mvu_update]` 变量规则簇、
`[initvar]` / `[opening]` / `[勿开]` / `[勿关]`。这些内容留在资产层里永远是死文本，
还会作为知识库文档参与检索、把 ST 语法注入提示词。

处置方式是把它们的 `id` 写进**卡侧清单** `cards/<角色名>/runtime.yaml`：

```yaml
version: "1"

exclude_entry_ids:
  - 12    # 09_📌CG插图 —— EJS 模板 + {{roll:2}}×40 + <pic> 标签渲染
  - 476   # [opening]开场数据存储勿开
```

- `build_assets.py` 读到它在**生成**与**迁移**两条路径上都跳过这些条目。
- `--migrate` 时**不会**把这些条目已存在的文件误标 `_orphaned`——排除 ≠ 上游删条目。
- **fail-closed**：清单存在却读不出 `exclude_entry_ids` 时直接报错中止。`_mini_yaml`
  解析失败会返回空 dict，若静默当成空清单，排除就会悄悄失效。
- 格式限制：`_mini_yaml` 只认固定子集，**序列里不能夹独立注释行**（会被解析成空串），
  注释请写在条目同一行。
- 内容不会丢：`cards/<名>/card.json` 保留全部条目原文；某条要改判回资产，从清单里
  删掉它再重跑 `build_assets.py` 即可。
- 清单**只登记纯运行时管道**。内容条目里内嵌的 `{{getvar::…}}` / `<%_ … _%>` 属于
  「该剥离」而不是「该排除」——语义要留，得逐条改写成平台无关表述，不要图省事塞进这里。

## 世界书处理策略（资产化语境）

每条（含 disabled）给去向：

| 类型 | 信号 | 去向 |
|---|---|---|
| 稳定设定/规则/术语 | 常驻说明、规则 | lore/ 模块 |
| 地区/场景/NPC | 地点、角色模板 | lore/ 模块 |
| 初始状态 | [initvar] | lore/ 模块，note 保留标记 |
| 更新规则 | [mvu_update] | lore/ 模块，note 保留标记；语义进关系/记忆策略草稿 |
| 骰子/公式 | {{roll}}、DC、伤害 | 只保留语义说明进 lore；不生成任何结算代码 |
| 路线/分支 | route、结局 | lore/ 模块，note 标注；供渲染器按路线选择 |
| ST 补丁 | COT、JSON Patch、__结束__ | 剥离语法，不资产化 |

## 产出（资产骨架）

```txt
cards/<角色名>/                # 原始 card.json 归档（只读源，随 git 版本化）
└── card.json
characters/<角色名>/           # 资产骨架（--out 默认 characters/）
├── persona.yaml              # 画像骨架（人工/LLM 补全）
├── lore/                     # 世界书模块，每模块一个 yaml
│                             #   _derived 块 = 机器字段（激活语义/内容/comment/sha）
│                             #   块外 = 人工字段（keys/note/layer/budget_tokens）
├── dialogs/                  # first_mes.yaml / alternate_NN.yaml / example_NN.yaml
└── provenance.md             # 来源与提取信息
assets_schema/
├── persona.schema.yaml       # persona.yaml 字段规范
├── lore.schema.yaml          # lore/*.yaml 字段规范（含激活语义与 _derived 约定）
├── dialogs.schema.yaml       # dialogs/*.yaml 字段规范
└── persona.example.yaml      # 完整示例（Bot）
```

## 硬约束

- **平台无关**：产物中不得出现 pi、extension.ts、pi session、subagent、prompt orchestrator、start.sh 等平台专属概念；ST 的 depth/position 不直接透传，映射为平台无关注入语义 `inject_at`（resident/context_head/near_input）。
- **只读安全**：extract/list/get 脚本只读；build_assets.py 只向 `--out` 目录写入，不碰用户数据与项目其它文件。
- **纯标准库**：build_assets.py / validate_assets.py / replay_hitrate.py 只用 Python 标准库（json、hashlib、pathlib、re、datetime、argparse）+ 同目录 `_mini_yaml.py`（本工具族共享的极简 YAML 解析器）。
- **幂等**：重复运行 build_assets.py 不产生重复内容；已存在资产文件默认跳过（`--force` 覆盖、`--migrate` 字段级合并），打印 跳过/更新/新建/迁移。
- **激活语义无损**：keys/secondary_keys/selective/constant/insertion_order/case_sensitive/scan_depth/prevent_recursion/use_regex/inject_at/budget_tokens 全部结构化保留，不塞进不透明 JSON；**路由由 layer 决定**（未分层/layer 非法/常驻超预算/disabled 未处置由 validate 机器拦截；ST 死条目由 compile_card.py 检查）。
- **资产层忠实于卡、归一化在编译层**：selective=true 且 secondary 为空在资产层保持原卡透传（_derived 机器字段，migrate 会刷新），`compile_card.py` 编译时按 selective=false 处理；validate 只给信息提示，不要求改文件。
- **估算口径**：token 一律用 estimated_tokens（中文/全角 1 字符≈1 token、ASCII 4 字符≈1 token），用线上真实用量校准后更新。
- **中文**：注释、文档、输出信息一律中文。
- **保留语义、剥离补丁**：ST 宏、HTML 状态栏、JSON Patch 输出、COT 标签默认剥离，只保留背后的内容。
- **跨资产引用（方案 C）**：persona 允许用 `../<世界>/...` 引用同 `characters/` 树下的世界资产；refs 一律带引号、行内不写注释。

## 完工

- 残留扫描：grep 确认资产目录无 ST 宏/补丁残留。
- 审计清单：
  - [ ] description / personality / scenario / system_prompt 已进 persona.core.self
  - [ ] first_mes / alternate_greetings 已自动资产化（dialogs/first_mes.yaml / alternate_NN.yaml，**机器保证**，非人工承诺）
  - [ ] 所有世界书条目（含 disabled）有去向；disabled 的 note 已写处置（非机器默认值）
  - [ ] 所有 lore 条目已分层（layer 非空且在 identity/behavior/narrative 内；`--report-unlayered` 工作清单清零）
  - [ ] selective=true 条目已确认：非空 secondary 是真排除；空 secondary 由编译层归一化（不改文件）
  - [ ] 常驻（constant=true）+ persona 常驻部分在 `--budget-tokens` 预算内（estimated_tokens）
  - [ ] [initvar] / [mvu_update] 标记已保留到 lore 的 note
  - [ ] mes_example 拆分完成且 role 标记正确
  - [ ] creator_notes 秘密已标注「资产内部，勿公开」
  - [ ] ST 宏 / 补丁无残留
- persona.yaml 无「待填」空白（至少 style 与 relationship 已填）——**由 validate_assets.py 非空门禁机器保证**，不允许带空占位提交。
- lore_refs / dialogs_refs 与实际文件一一对应（validate_assets.py 校验）。
- `python3 scripts/validate_assets.py characters/<角色名>/` 必须通过。
- 上游卡更新走 `python3 scripts/build_assets.py --card <新卡> --out characters/ --migrate`，人工字段不丢。
- git commit 注明「资产化 v1，来源 hash: xxx」。
