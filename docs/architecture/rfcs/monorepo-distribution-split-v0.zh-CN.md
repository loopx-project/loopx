# RFC：Monorepo 内的发行物拆分（v0）

- **RFC 状态：** Draft
- **交付成熟度：** Proposal
- **作者 / 负责人：** LoopX maintainers
- **创建：** 2026-09-26
- **最近规范性修订：** 2026-09-26
- **实现基线：** `3e443ad7c`
- **相关契约：** [TypeScript 控制面迁移 v0](typescript-control-plane-migration-v0.zh-CN.md)、[Extensions 参考](../../reference/extensions.md)、[Capability 目录](../../../loopx/capabilities/README.md)、[总体路线图 v0](loopx-overall-roadmap-v0.zh-CN.md)（S2、S8、S12）、[import 边界测试](../../../tests/architecture/test_control_plane_import_boundaries.py)
- **跟踪 issue：** [#5072](https://github.com/loopx-project/loopx/issues/5072)
- **语言镜像：** [English](monorepo-distribution-split-v0.md)

## 文档结构与维护约定

第 1–10 节是持久的设计与验收契约；第 11 节是规范性交付计划；第 12 节列出未决决策，建议答案不等于批准；附录为非规范内容。本 RFC 同时提供英文版与 `.zh-CN.md` 语义镜像，两者不一致视为缺陷。

---

## 1. 决策摘要

1. **仓库保持单一 monorepo。** 一条 PR 流程、一套 CI、一个发布列车。拆成多个 Git 仓库是本 RFC 的非目标。
2. **安装物拆成多个发行物。** 单一 `loopx` wheel 逐步替换为 `loopx-core`、`loopx-workspace` 以及 `packages/` 下的按 capability 打包；`loopx` 保留为依赖全部子包的 meta-package。
3. **先做顶层目录重组，再做打包变更。** `loopx/chat_*` 迁入 `loopx/chat/`，`loopx/*_goal_mode/` 迁入 `loopx/hosts/`，旧路径保留至少一个 minor 版本的兼容 re-export。
4. **新增一条架构测试钉住顶层增长。** `loopx/*.py` 的文件数只能减少，与现有零例外 import 边界测试并列。
5. **仅内核安装不得在 import 阶段要求 Node.js。** TS effect runtime 对有副作用的命令仍是必需的；缺失时必须以现有的类型化 `node_unavailable` 诊断呈现，而不是安装或 import 失败。
6. **不变：** 内核权威、CLI 兼容基线、extension 生命周期规则、公开/私有边界、全部稳定协议。
7. **本 RFC 不批准：** 任何子包"毕业"到独立仓库、按发行物改许可证、任何 schema 缩减。

## 2. 问题与动机

只想要 CLI 或内核的开发者，安装的和运行桌面工作台、Lark 管家、全部内置 capability 的人是同一个 wheel。基线数据：

| 事实 | 基线值 |
| --- | --- |
| `loopx/*.py` 平铺模块 | 143 个文件，约 7.88 万行 |
| 顶层 `loopx/chat_*.py` | 37 个 |
| 顶层 `loopx/*_goal_mode/` 宿主包 | 9 个 |
| `loopx/control_plane` / `capabilities` / `extensions` | 约 13.1 万 / 11.5 万 / 4.4 万行，同一包内 |
| 声明的 Python 依赖 | `dependencies = []` |
| 每次安装的运行前提 | effect runtime 需要 Node.js ≥ 22.22.3 |
| 内核/CLI/顶层模块 import `loopx.capabilities` | 52 处 |
| capability 模块 import `loopx.control_plane` | 74 处 |
| `packages/` 下已独立打包的 extension | 9 个 |

由此产生的具体问题：

- **首次使用过重。** "装包、跑 `loopx dashboard`" 会拉进 chat server、展示层资产和全部 capability 代码，哪怕用户只想对一个 goal 跑 `loopx status`。wheel 单体化时，路线图 S1/S12 的首次使用路径无法变轻。
- **归属不可发现。** 想找"chat 在哪"的贡献者看到的是 37 个和 `quota.py`、`todos.py` 并排的兄弟文件。模块级 CODEOWNERS 和首轮评审人无法在平铺命名空间上清晰表达。
- **两套打包惯例并存。** finance、JEV、Obelisk、repo-health 已经用各自的 `pyproject.toml` + `extension.toml` 从 `packages/` 发布，而 33 个内置 capability 仍在内核 wheel 里。新 capability 作者没有规则可依。
- **顶层增长无上限。** import 边界测试保护 `control_plane` 的入边，但没有任何机制阻止下一批 40 个 `something_*.py` 落在顶层。

这些表面的负责人无法在本地解决：capability 作者不能决定内核的打包方式，内核也不能在没有仓库级规则的情况下决定哪些 capability 是可选的。

### 不变量

- I1. 任何跨包变更都是一个仓库、一个 PR、一次 CI。
- I2. 内核真相（Goal/Todo/claim/lease/quota/effect/receipt）每个事务只有一个负责人、一条实现路径；打包不得引入第二份拷贝。
- I3. 迁移后 `import loopx.<旧模块>` 至少在一个 minor 版本内继续可用，并有文档化的弃用说明。
- I4. 缺失的可选发行物退化为与 extension "off state" 相同的行为：核心命令可用，缺失的 capability 报告为不可用，绝不静默替换。
- I5. 安装仅内核发行物不需要 Node.js；在没有 Node.js 时运行有副作用的命令得到类型化 `node_unavailable` 诊断。
- I6. 任何移动都不附带改变公开/私有边界规则、schema 字段或许可证。

## 3. 范围与非目标

### 范围内

- `loopx/` 顶层模块的目录重组与兼容 shim。
- 钉住 `loopx/*.py` 数量的架构测试。
- 三层发行物的定义及"哪类代码归哪层"的规则。
- 内置 capability 经现有 `CapabilityRegistry` provider 边界迁入 `packages/`。
- `loopx-core` 以 optional extra 或随包捆绑方式交付 Node effect runtime。

### 非目标

- 拆成多个 Git 仓库（见第 6 节与附录 D）。
- 按发行物改许可证；除另有许可证 RFC 决定外，所有层保持 Apache-2.0。
- 任何内核语义、TS 迁移顺序或稳定协议的变更。
- 移除旧兼容 facade（`loopx.status`、`loopx.quota`）；其退役仍由迁移 RFC 管辖。

## 4. 现状契约

在 `3e443ad7c` 上审计的事实：

- `pyproject.toml` 以 `packages.find(include=["loopx*"])` 构建单一发行物 `loopx`，把 TS 源码与 JSON 作为 `loopx.control_plane*` 的 package data 打包，声明五个 console script。
- `loopx/control_plane/effect_runtime.py` 在首次有副作用调用时探测 Node，并已产出类型化启动诊断 `node_unavailable`；缺口在于安装文档与桌面路径把 Node 当作一切功能的硬前提。
- `tests/architecture/test_control_plane_import_boundaries.py` 零例外地拒绝控制面 import 展示层、CLI、capability 或 benchmark 适配层。它不约束顶层模块数量，也不约束 capability → 内核的 import。
- `packages/*/extension.toml` 与 `loopx.extensions.manifest` 定义 extension 生命周期（readiness、版本、默认关闭、卸载）。这是 capability 包必须采用的契约，本文不重新定义。
- `docs/reference/extensions.md` 已写明：capability 是产品契约，extension 是交付单元。本 RFC 把这条规则应用到内核 wheel 自身。

## 5. 提议的架构

### 归属与权威

| 发行物 | 包含 | 负责人边界 | 依赖 |
| --- | --- | --- | --- |
| `loopx-core` | `loopx/control_plane`、`loopx/cli_commands`、`loopx/semantics`、`loopx/hosts/` 下的最小宿主适配、`loopx check`、doctor、status | 内核 maintainers | Python ≥ 3.11；Node effect runtime 作为 `loopx-core[runtime]` extra 或捆绑产物（D1） |
| `loopx-workspace` | `loopx/chat/`、`loopx/web`、`apps/presentation`、dashboard launcher、桌面壳胶水 | 前台 maintainers | `loopx-core` |
| `loopx-capability-<name>`（位于 `packages/`） | 单个 capability 的契约、provider、CLI 子命令、文档 | CODEOWNERS 中的 capability owner | `loopx-core`；可选依赖其他 capability 包 |
| `loopx`（meta） | 无代码 | 发布负责人 | 以上全部，钉在同一发布列车 |

权威不移动：内核仍是 Goal/Todo/claim/lease/quota/effect 状态的唯一写者。capability 包只能通过 `CapabilityRegistry` 注册 provider、capability 契约和 CLI 子命令，绝不 import 内核私有模块。

### 状态模型与 schema

不改任何规范记录。唯一新增的持久产物是架构 fixture：

```text
tests/architecture/top_level_module_budget.json
{ "schema": "loopx_top_level_module_budget_v0",
  "baseline_commit": "<sha>",
  "max_top_level_modules": 143,
  "allowlist": ["__init__.py", "entrypoint.py", "cli.py"] }
```

`max_top_level_modules` 只能在同时移动文件的 PR 中调低。allowlist 列出因入口点原因必须留在顶层的模块。

### 命令或事件生命周期

每组迁移按固定顺序执行：

1. `git mv` 该组到目标包（`loopx/chat/`、`loopx/hosts/` 或 `packages/loopx-capability-<name>/src/`）。
2. 在旧路径留下 shim，re-export 公开名字并每进程发出一次 `DeprecationWarning`。
3. 按迁移文件数调低 `max_top_level_modules`。
4. 运行 import 边界测试、预算测试、对触及文档的 `loopx check`，以及 package smoke lane。

任一项失败的迁移 PR 不可合并；改变行为的迁移 PR 视为超出范围而拒绝。

### Provider 或 extension 契约

capability 包直接采用现有 `extension.toml` 契约，不做扩展。唯一新规则：**`loopx/capabilities/` 下在 `main` 上没有内核调用方的 capability，在接受新特性前必须先迁到 `packages/`。** 内核调用方清单即第 2 节的 52 处 import；每处要么改为 registry 查询，要么被记录为保留的核心 capability（D2）。

## 6. 备选方案与设计选择

| 备选 | 为何现在不选 |
| --- | --- |
| **拆成多个 Git 仓库** | 在 TS 事务 cutover 期间违反 I1：内核语义每天变化，下游仓库会随每次变更断裂。评审产能是稀缺资源时，它成倍放大 CI、发布与评审成本。仅在内核迁移完成且某个包有独立 maintainers 后重开（附录 D）。 |
| **保持单 wheel，只加 extras** | 能减轻安装重量，但平铺命名空间与两套打包惯例照旧；贡献者仍找不到边界。 |
| **一次性全部搬到 `packages/`** | 同时对大量调用方破坏 I3，并把行为风险混入结构性变更。按组迁移 + shim 更易评审、更易回滚。 |
| **无条件把 Node 捆进 `loopx-core`** | 解决 I5，但让内核 wheel 变成平台相关且体积大。作为 D1 选项与 optional-extra 方案并列保留。 |

## 7. 安全、隐私与兼容

- **默认关闭 / 功能关闭一致性：** 缺失 `loopx-workspace` 或 capability 包时，行为等同现有 extension off state（I4）。没有命令会静默回退到另一实现。
- **公开/私有边界：** 不变。`loopx check` 继续扫描每个包；移动文件不改变其扫描类别。
- **旧读者/写者：** import shim（I3）覆盖 Python 调用方。console script 保持名字；`loopx` meta-package 让 `pip install loopx` 在一个 minor 版本内行为完全一致。
- **混合版本：** meta-package 把所有层钉到同一发布列车。混合版本安装不受支持，由 `loopx doctor` 报告。
- **fail-closed：** 没有 runtime extra 时，有副作用的命令以 `node_unavailable` 失败，绝不在没有 TS 内核的情况下继续。

## 8. 迁移与回滚

| 步骤 | 门禁 | 回滚 |
| --- | --- | --- |
| 以当前数量加入预算测试 | 无；纯新增 | 删除 fixture |
| `chat_*` → `loopx/chat/` 带 shim | import 边界 + 预算 + smoke 全绿 | revert PR；shim 使 revert 对调用方无感 |
| `*_goal_mode` → `loopx/hosts/` 带 shim | 同上 | 同上 |
| 发布 `loopx-core` / `loopx-workspace` / meta `loopx` | package-smoke lane 在干净 runner 上分别安装 | 撤下预发布；`loopx` meta 保留旧布局一个版本 |
| capability 包迁移 | 逐个 capability，内核调用方清单已解决 | revert 单个包 |
| 移除 shim | 迁移后一个 minor 版本；弃用记入 update notes | 不适用；需调用方迁移 |

任何步骤都不需要数据迁移，因为没有规范记录改变。

## 9. 验证与验收

| 主张 | 测试或证据 | 要求结果 | 边界 / 排除 |
| --- | --- | --- | --- |
| 顶层模块数量不增长 | `tests/architecture/test_top_level_module_budget.py` | `len(loopx/*.py) > max_top_level_modules` 时失败 | 不评判模块质量 |
| 迁移后旧 import 仍可用 | 对每个迁移公开名字的 import 测试 | 通过并发出一次 `DeprecationWarning` | 仅一个 minor 版本 |
| 无 Node 的仅内核安装 | 无 Node 的 runner 上 package-smoke：`pip install loopx-core && loopx --help && loopx status --format json` | 退出码 0 | 不含有副作用命令 |
| 无 Node 的有副作用命令类型化失败 | 同一 runner：`loopx heartbeat-prompt ...` | `node_unavailable` 诊断，非零退出 | — |
| 缺失 capability 包 = off state | 仅安装 `loopx-core`；`loopx capability list` | capability 报告不可用，核心命令不受影响 | — |
| 无内核真相重复 | import 边界测试 + 审查 `packages/*/src` 是否 import 内核私有模块 | 零违规 | 与现状一致，仅静态 import |
| meta-package 一致性 | `pip install loopx` 后跑现有完整 smoke | 与拆分前 smoke 一致 | — |

以上确定性一致性行是每个里程碑的必需项；本 RFC 不做任何实机 qualification 或性能主张。

## 10. 运维契约

- `loopx doctor` 报告已安装的发行物集合与版本，并标记混合发布列车。
- `loopx capability list` 区分"未安装"与"已安装但未就绪"。
- 发布说明列出每个迁移模块及其 shim 到期版本。
- 不引入新的守护进程、存储或网络面。

## 11. 规范性交付计划

| 里程碑 | 交付行为 | 进入门禁 | 退出证据 | 回滚 |
| --- | --- | --- | --- | --- |
| M0 | 以当前数量加入预算 fixture 与测试；RFC 索引条目 | 本 RFC 以 Draft 合入 | 预算测试绿；数量钉住 | 删除测试 |
| M1 | `loopx/chat_*` → `loopx/chat/` 带 shim；预算调低 | M0 | 第 9 节第 1–2 行 | revert |
| M2 | `loopx/*_goal_mode` → `loopx/hosts/` 带 shim | M0 | 第 1–2 行 | revert |
| M3 | `loopx-core` + `loopx-workspace` + meta `loopx` 以预发布发布；Node 按 D1 以 extra 或捆绑交付 | M1、M2；D1 已决 | 第 3–4、7 行 | 撤下预发布 |
| M4 | 第一个无内核调用方的 capability 迁入 `packages/` | M3；D2 清单 | 第 5–6 行 | revert 单包 |
| M5 | 其余符合条件的 capability 迁移；M1/M2 的 shim 在一个 minor 版本后移除 | M4 | 完整 smoke 一致 | 不适用 |

## 12. 未决决策

1. **D1 — `loopx-core` 的 Node 交付方式。** 负责人：内核 maintainers。选项：(a) `loopx-core[runtime]` extra，文档说明 Node 为前提；(b) 以平台 wheel 捆绑钉住版本的 Node；(c) 两者都做，(a) 为默认。建议：(c)。所需证据：package-smoke lane 的 wheel 体积与平台矩阵。截止：M3 前。
2. **D2 — 哪些 capability 留在 core。** 负责人：内核 + capability maintainers。输入：52 处内核侧 import。建议：只保留 Turn driver、quota 或 heartbeat prompt 在运行时需要的 capability，其余全部成包。截止：M4 前。
3. **D3 — shim 存活期。** 负责人：发布负责人。选项：一个 minor 版本（建议）或两个。截止：M1。
4. **D4 — 索引位置。** 本 RFC 列在"控制面内核、状态与迁移"还是"运行时、Capability 与协作集成"。建议：内核章节，因为预算测试与 `loopx-core` 边界由内核负责。

---

## 附录 A：执行台账（非规范）

### 2026-09-26 — RFC 开启

- **基线：** `3e443ad7c`
- **交付：** 仅提案；第 2 节指标在该基线上测量。
- **证据：** `ls loopx/*.py`、`ls loopx/chat_*.py`、`ls -d loopx/*_goal_mode` 的文件计数；`rg` 扫描 `loopx/` 得到的 import 位置。
- **已知缺口：** 全部里程碑。
- **对规范性设计的影响：** 无。

## 附录 B：决策日志

| 日期 | 决策 | 负责人 / 批准 | 备选 | 变更的规范章节 |
| --- | --- | --- | --- | --- |
| — | — | — | — | — |

## 附录 C：证据登记

| 证据 id | 主张 | 基线 / 环境 | 产物或命令 | 结果 | 隐私 / 有效性边界 |
| --- | --- | --- | --- | --- | --- |
| E1 | 143 个顶层模块 | `3e443ad7c` | `ls loopx/*.py \| wc -l` | 143 | 统计文件数，非公开 API |
| E2 | 52 处内核侧 capability import | `3e443ad7c` | `rg -l "loopx\.capabilities\|from \.\.capabilities\|from \.capabilities" loopx/control_plane loopx/cli_commands loopx/*.py` | 52 | 仅静态 import |
| E3 | 74 处 capability → control_plane import | `3e443ad7c` | `rg -l control_plane loopx/capabilities` | 74 | 含 docstring；为上界 |

## 附录 D：被拒绝或被取代的备选

**多仓库拆分。** 本 RFC 拒绝的原因：在 TypeScript 控制面迁移处于事务 cutover 期间违反 I1，成倍放大 CI 与发布面，且需要目前不存在的按仓库 maintainers。可重开该决策的证据：迁移 RFC Stage 4 完成；某个 `packages/` 发行物拥有两位以上非内核 maintainers，且连续三个版本无跨包破坏性变更。

## 附录 E：事故与评审经验

暂无。
