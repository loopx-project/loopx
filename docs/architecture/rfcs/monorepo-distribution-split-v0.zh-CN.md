# RFC：Monorepo 发行拆分与小内核运行时装配（v0）

- **RFC 状态：** 已接受（仅提案；第 12 节 D1–D7 未决，合并不等于批准）
- **交付成熟度：** Proposal
- **作者 / 负责人：** LoopX maintainers
- **创建：** 2026-09-26
- **最近规范性修订：** 2026-10-29
- **实现基线：** 发行盘点 `2f3d13ae9`；运行时装配审计 `9cae3b28939f72fa4aadab5cfe49e19bcbdab372`
- **相关契约：** [TypeScript 控制面迁移 v0](typescript-control-plane-migration-v0.zh-CN.md)、[Extensions 参考](../../reference/extensions.md)、[Capability 目录](../../../loopx/capabilities/README.md)、[总体路线图 v0](loopx-overall-roadmap-v0.zh-CN.md)（S2、S8、S12）、[import 边界测试](../../../tests/architecture/test_control_plane_import_boundaries.py)
- **跟踪 issue：** [#5072](https://github.com/loopx-project/loopx/issues/5072)
- **替代 / 关闭：** 无
- **语言镜像：** [English](monorepo-distribution-split-v0.md)

## 文档结构与维护约定

第 1–10 节是持久的设计与验收契约；第 11 节是规范性交付计划；第 12 节列出未决决策，建议答案不等于批准；附录为非规范内容。本 RFC 同时提供英文版与 `.zh-CN.md` 语义镜像，两者不一致视为缺陷。

本文是目录归属、可安装发行物与运行时装配的统一设计，保留原文件名与跟踪 issue。
合并代表接受设计依据，不代表 runtime 已交付、性能已验证、宿主已合格或默认值已改变。
以下历史测量保留原有基线。

---

## 1. 决策摘要

1. **仓库保持单一 monorepo。** 一条 PR 流程、一套 CI、一个发布列车。拆成多个 Git 仓库是本 RFC 的非目标。
2. **安装物拆成多个发行物。** 单一 `loopx` wheel 逐步替换为 `loopx-core`、`loopx-workspace` 以及 `packages/` 下的按 capability 打包；`loopx` 保留为依赖全部子包的 meta-package。
3. **先做顶层目录重组，再做打包变更。** `loopx/chat_*` 迁入 `loopx/chat/`，`loopx/*_goal_mode/` 迁入 `loopx/hosts/`，已建立的公开 import 保留至少一个 minor 版本的兼容 re-export；内部调用方一起迁移并删除旧内部入口。
4. **新增一条架构测试钉住顶层增长。** `loopx/*.py` 的文件数只能减少，与现有零例外 import 边界测试并列。
5. **仅内核安装不得在 import 阶段要求 Node.js。** TS effect runtime 对有副作用的命令仍是必需的；缺失时必须以现有的类型化 `node_unavailable` 诊断呈现，而不是安装或 import 失败。
6. **不变：** 内核权威、CLI 兼容基线、extension 生命周期规则、公开/私有边界、全部稳定协议。
7. **本 RFC 不批准：** 任何子包"毕业"到独立仓库、按发行物改许可证、任何 schema 缩减。

各发行层把**同一份治理实现**装配为 minimal 与 standard 预设。
Minimal 必须执行并恢复真实的受治理任务；standard 围绕同一内核增加产品装配。
研究配置冻结其中一种装配与声明过的实验改动，不产生第三套治理实现。

“小”分别从安装依赖、加载代码、激活服务、模型可见上下文衡量。
发行表定义可安装内容，不代表每条命令都必须加载。安装 standard 不授予服务激活或权限。
现有默认值与机器强制工作义务持续生效，直到合格且明确披露的迁移改变它们。

## 2. 问题与动机

只想要 CLI 或内核的开发者，安装的和运行桌面工作台、Lark 管家、全部内置 capability 的人是同一个 wheel。`2f3d13ae9` 上的历史发行盘点：

| 事实 | 基线值 |
| --- | --- |
| `loopx/*.py` 平铺模块 | 148 个文件，约 7.98 万行 |
| 顶层 `loopx/chat_*.py` | 38 个 |
| 顶层 `loopx/*_goal_mode/` 宿主包 | 9 个 |
| `loopx/control_plane` / `capabilities` / `extensions` | 约 13.0 万 / 11.6 万 / 4.4 万行，同一包内 |
| 声明的 Python 依赖 | `dependencies = []` |
| 每次安装的运行前提 | effect runtime 需要 Node.js ≥ 22.22.3 |
| 内核/CLI/顶层模块 import `loopx.capabilities` | 52 处 |
| capability 模块 import `loopx.control_plane` | 80 处 |
| `packages/` 下已独立打包的 extension | 9 个 |

由此产生的具体问题：

- **首次使用过重。** "装包、跑 `loopx dashboard`" 会拉进 chat server、展示层资产和全部 capability 代码，哪怕用户只想对一个 goal 跑 `loopx status`。wheel 单体化时，路线图 S1/S12 的首次使用路径无法变轻。
- **归属不可发现。** 想找"chat 在哪"的贡献者看到的是 38 个和 `quota.py`、`todos.py` 并排的兄弟文件。模块级 CODEOWNERS 和首轮评审人无法在平铺命名空间上清晰表达。
- **两套打包惯例并存。** finance、JEV、Obelisk、repo-health 已经用各自的 `pyproject.toml` + `extension.toml` 从 `packages/` 发布，而 33 个内置 capability 仍在内核 wheel 里。新 capability 作者没有规则可依。
- **顶层增长无上限。** import 边界测试保护 `control_plane` 的入边，但没有任何机制阻止下一批 40 个 `something_*.py` 落在顶层。

这些表面的负责人无法在本地解决：capability 作者不能决定内核的打包方式，内核也不能在没有仓库级规则的情况下决定哪些 capability 是可选的。

目录与包拆分还需要执行契约。简短 CLI 读取不应初始化研究栈或 IM 服务，嵌入式 managed
任务不应要求交互 App。仅缩小安装代码不能防止提前导入、空闲轮询或每次模型调用的过量上下文。
因此第 9 节同时验证结构迁移和有用的任务旅程。

设计必须支持以下可观察旅程：

| 旅程 | 必须得到的结果 | 对装配的要求 |
| --- | --- | --- |
| 有界 managed 任务 | 无交互 App 也能启动、执行、结算并回读结果 | Minimal 包含一条合格宿主路径与持久 authority |
| 长程研究 | 先产出有用的首个结果，持续工作后有证据支持的修订或新成果 | 规划、研究、记忆策略按需；进展不能只是时间变长或重复总结 |
| 两三个 peer 协作 | 证据返回协调者，记录采纳，中断后恢复所有权 | 复用工作/租约 authority；peer 生命周期与结果采纳分离 |
| App 或 IM 请求 | 暂停、纠正、继续，并在原目的地收到已验收结果 | 前端与交付 provider 围绕同一工作身份装配 |
| 硬件或外部操作 | 完成多步任务，核对不确定副作用，并在对应边界验证停止 | 显式激活的 operation provider 负责回读、联锁与安全重试 |
| 可复现 benchmark | 用原生对照与完整成本比较合格产品装配 | 冻结实验输入；原生 runner、verifier 与评分保留各自所有权 |

这些是共享产品要求，不表示一次性交付所有旅程或组合。
内容、办公与工程能力使用同样的边界，无须成为强制启动依赖。

### 不变量

- I1. 任何跨包变更都是一个仓库、一个 PR、一次 CI。
- I2. 内核真相（Goal/Todo/claim/lease/quota/effect/receipt）每个事务只有一个负责人、一条实现路径；打包不得引入第二份拷贝。
- I3. 已建立的公开 `import loopx.<旧模块>` 合同在迁移后至少一个 minor 版本内继续可用，并有弃用说明。纯内部移动同步更新全部活跃 caller 并删除旧入口；只有具名未迁 caller 与明确退出条件才保留内部 bridge。
- I4. 缺失的可选发行物退化为与 extension "off state" 相同的行为：核心命令可用，缺失的 capability 报告为不可用，绝不静默替换。
- I5. 安装仅内核发行物不需要 Node.js；在没有 Node.js 时运行有副作用的命令得到类型化 `node_unavailable` 诊断。
- I6. 任何移动都不附带改变公开/私有边界规则、schema 字段或许可证。
- I7. 未选择的可选组件不能初始化、注册 timer/hook、联网或注入模型指令。
  缺少执行前提时须在 dispatch 前失败，不能降级到不受治理的执行。
- I8. 必需上下文保持完整、新鲜；缩短 prompt 不能删除要求、required reads 或停止条件。
  重放先核对持久身份与外部证据，再决定重试。
- I9. 性能与成果声明注明所测装配、基线、环境和范围。文件数减少、help 加速不能证明任务效果改善。

## 3. 范围与非目标

### 范围内

- `loopx/` 顶层模块的目录重组与兼容 shim。
- 钉住 `loopx/*.py` 数量的架构测试。
- 三层发行物的定义及"哪类代码归哪层"的规则。
- 内置 capability 经现有 `CapabilityRegistry` provider 边界迁入 `packages/`。
- `loopx-core` 以 optional extra 或随包捆绑方式交付 Node effect runtime。
- 使用同一类型化 owner 的 minimal/standard 装配、按需加载、激活、上下文边界与匹配的产品/benchmark 资格验证。

### 非目标

- 拆成多个 Git 仓库（见第 6 节与附录 D）。
- 按发行物改许可证；除另有许可证 RFC 决定外，所有层保持 Apache-2.0。
- 任何内核语义、TS 迁移顺序或稳定协议的变更。
- 移除旧兼容 facade（`loopx.status`、`loopx.quota`）；其退役仍由迁移 RFC 管辖。
- 新模型 loop、scheduler、插件体系、记忆数据库、benchmark 原生 runner、存储迁移、设备框架或强制 daemon。
  本提案不授权 benchmark 运行、设备操作或宿主晋级。

## 4. 现状契约

在 `2f3d13ae9` 上审计的事实：

- `pyproject.toml` 以 `packages.find(include=["loopx*"])` 构建单一发行物 `loopx`，把 TS 源码与 JSON 作为 `loopx.control_plane*` 的 package data 打包，声明五个 console script。
- `loopx/control_plane/effect_runtime.py` 在首次有副作用调用时探测 Node，并已产出类型化启动诊断 `node_unavailable`；缺口在于安装文档与桌面路径把 Node 当作一切功能的硬前提。
- `tests/architecture/test_control_plane_import_boundaries.py` 零例外地拒绝控制面 import 展示层、CLI、capability 或 benchmark 适配层。它不约束顶层模块数量，也不约束 capability → 内核的 import。
- `packages/*/extension.toml` 与 `loopx.extensions.manifest` 定义 extension 生命周期（readiness、版本、默认关闭、卸载）。这是 capability 包必须采用的契约，本文不重新定义。
- `docs/reference/extensions.md` 已写明：capability 是产品契约，extension 是交付单元。本 RFC 把这条规则应用到内核 wheel 自身。

`9cae3b28939f72fa4aadab5cfe49e19bcbdab372` 上的运行时装配审计：

- [entrypoint.py](../../../loopx/entrypoint.py) 有轻量 version 路径与部分原生 scheduler 后续调用。
  [cli_runtime.py](../../../loopx/cli_runtime.py) 按所选命令分发，保留完整 parser fallback。
  `performance-diagnosis` 无须 registry，但尚未进入 selected-command 集合；
  这是有界加载接缝，不代表所有启动都已延迟加载。
- [Controller/Executor 统一 RFC](controller-executor-unified-contract-v0.zh-CN.md) 负责受治理工作契约；
  [Session 模式](agent-session-execution-modes-v0.zh-CN.md) 负责 managed/attached 准入；
  [DSH/Pi 选择](harness-selection-dsh-pi-v0.zh-CN.md) 负责宿主资格。装配复用这些契约。
- [Benchmark toolkit](../../../loopx/capabilities/benchmark_toolkit/README.md) 负责通用权限、并发与完整性。
  [Benchmark 政策](../../../benchmark/README.md) 把原生执行/评估留在 benchmark，不恢复第二个 launcher。
- [三层协议](../../reference/protocols/multi-agent-three-layer-minimality-v0.md) 的 “kernel” 指更宽的复用层。
  本文治理内核指 authority 边界，启动依赖闭包指所选入口必需代码。
  可复用 runner 或展示设施无须进入每个启动闭包。

## 5. 提议的架构

### 归属与权威

| 发行物 | 包含 | 负责人边界 | 依赖 |
| --- | --- | --- | --- |
| `loopx-core` | `loopx/control_plane`、`loopx/cli_commands`、`loopx/semantics`、`loopx/hosts/` 下的最小宿主适配、`loopx check`、doctor、status | 内核 maintainers | Python ≥ 3.11；Node effect runtime 作为 `loopx-core[runtime]` extra 或捆绑产物（D1） |
| `loopx-workspace` | `loopx/chat/`、`loopx/web`、`apps/presentation`、dashboard launcher、桌面壳胶水 | 前台 maintainers | `loopx-core` |
| `loopx-capability-<name>`（位于 `packages/`） | 单个 capability 的契约、provider、CLI 子命令、文档 | CODEOWNERS 中的 capability owner | `loopx-core`；可选依赖其他 capability 包 |
| `loopx`（meta） | 无代码 | 发布负责人 | 以上全部，钉在同一发布列车 |

这是安装布局。`loopx-core` 中的 CLI handler 和宿主设施不因此进入每次启动的依赖闭包。
可选 provider 发行物继续遵守 `packages/<package-id>/` 惯例；capability 包这一行不要求每个 provider
都创建一个 capability。

权威不移动：内核仍是 Goal/Todo/claim/lease/quota/effect 状态的唯一写者。capability 包只能通过 `CapabilityRegistry` 注册 provider、capability 契约和 CLI 子命令，绝不 import 内核私有模块。

### 治理与运行时所有权

| 边界 | 现有 owner 与职责 | 不承担的职责 |
| --- | --- | --- |
| Bootstrap | Entrypoint/CLI 路由解析命令与已安装元数据 | Goal 决策、发现阶段激活 provider |
| 治理内核 | 类型化 Goal、work、quota、runtime、coordination owner：要求、准入、租约、预算、停止、effect、receipt、结算与恢复 | Model/tool loop、任务策略、UI、传输或评分 |
| 宿主 | 已准入的 managed/attached provider：模型/工具、Session 生命周期、观测 | 第二份工作账本或独立权限 authority |
| Capability 策略 | 最近的结果型 owner：规划、上下文选择、研究、召回、评审 | 覆盖强制迁移、租约、停止或预算 |
| Extension 与产品界面 | 现有生命周期/registry；前端和交付 provider 消费公开安全投影、提交命令 | 新插件机制，或从展示/私有源文字推导 authority |

放置不新增 capability 或 provider id：治理留在 `control_plane`，通用生命周期在 `extensions`，
任务行为在最近的 capability，可选发行物在 `packages/`。新的通用领域规则留在既有类型化
TypeScript owner；Python 可以适配，不能重建状态规则。
新 capability 需要独立有用、provider-neutral 的调用者契约。
真实宿主/设备集成负责自己的 provider 专属执行与恢复，不预建通用框架。

Planner 可以建议工作或 stride，所属迁移规则决定准入与义务。
预设不能把机器强制的 `must_attempt_work` 契约变成文字 guidance。
内核是内聚领域的集合，不是一个大文件。相邻重构应让下一次修改更局部，并保持已刻画的行为。

### 发行层之上的预设

| 装配 | 必需内容 | 显式可选内容 |
| --- | --- | --- |
| Minimal | Bootstrap、同一治理实现、一个受支持 authority provider、一条合格执行路径、紧凑状态/结果回读与恢复 | 任务能力、持久召回、observer、connector、workspace UI |
| Standard | 同一内核与兼容的普通产品装配 | 仅加载/激活所选工作流需要的 capability、宿主、UI/IM 服务与 connector |
| 研究配置 | 固定版本的 minimal 或 standard 装配及声明过的实验 overlay | 仅实验设计中列出的 instrumentation 或 ablation |

`loopx` meta-package 保留安装兼容；已安装 standard 内容不等于激活列表。
Minimal 是可执行装配，不是在 standard 完成全部初始化后加开关。
不带宿主的只读 kernel library 不构成可执行预设资格。
精确内容与首个受支持宿主/authority 组合由 D2/D5 决定。

装配、managed/attached 模式、宿主 provider、一个/两个模型 Session、存储 provider、benchmark arm
保持独立。Minimal 不意味着 attached，standard 不要求双模型 Session，research 不等于不受治理。
只发布已测组合，保留现有模式默认值；改变装配不能静默改变 binding 的执行 owner。

### 受支持的宿主接入决策

独立安装的 `generic-cli` 宿主 transport 使用已有 Turn wire protocol，不注册
新的 capability。其受支持的 Python 接缝为 `loopx.extensions.turn_host`：只转出
签名请求提取、候选构建、Goal 实例校验与结果校验的现有内核 owner，并提供复用
已有 TypeScript 进程监督器的原始 stdio relay。公开 `loopx.file_lock` API
继续负责 provider 私有状态的锁与原子写入。两种 import 都不授予工作权限。

MemT 只使用这两个公开模块，不能直接 import executor 或其他内核私有模块。
这是明确的接入接缝，不是任意包到内核 import 的许可；import ratchet 钉住这两条
边。Ark/JEV 的既有私有边仍是迁移积压，不新增豁免。facade 只转发现有行为，
不能复制候选、验收、进程生命周期或文件锁决策。

独立安装的 Codex provider-routing host 通过
`loopx.extensions.chat_service_guard` 接入其已有、已获授权的托管 Chat consumer
重启。facade 只转出现有 runtime activation owner 的
`managed_chat_activation_guard` 与 `RuntimeActivationDeferred`。已接受工作观察、
准确服务身份和 ingress lock 仍由该 owner 负责；包不读取私有 Goal 状态，
也不复制决策。Core 缺失或不支持时延期重启；没有托管服务的路径仍不依赖 Core。
import ratchet 只记录这一条受支持公开边；它不授予停止服务或增加其他内核 import 的权限。

该栅栏的拒绝范围与那份观察一样窄：它持有入口准入锁，并在仍有非终态 Turn 时
拒绝继续。待处理的入口收据是在 provider 投递之前预留的外部输入，因此还不是
Turn，而该栅栏也不读取入口收据账本。于是只有一条待处理的 `loopx_queue` 或
`loopx_inbox` 收据会被放行，而 queued Turn 会以 `chat_work_pending` 被拒绝。

stdio relay 在父进程 EOF、SIGTERM 或 SIGINT 时取消所持进程组，并等待已有
监督器完成回收再退出。POSIX 进程组行为与 Windows 尽力而为的进程树回收仍沿用
原资格边界。这是进程监督，不是沙箱；操作者选择的工具只保有原先获准的效果。

### 职责与安装验收

目录重组与发行物拆分分别验收。每次移动说明规则的唯一 owner、变更 caller、
import 方向、剩余兼容接缝和从失败定位 owner 的路径。每个发行物通过支持的命令
验证干净最小安装、缺少可选包、升级与回滚。跨运行时成本复用 TS 迁移兑现表，
不增加平行评分表。

文件数棘轮仍是架构守卫，不是优化目标。移动文件后仍保留 shim 不会降低文件数；
把无关职责塞进一个文件来满足预算不构成成功重构。代表性修改局部性与安装成本
分别测量。

### 状态模型与 schema

不改任何规范记录。唯一新增的持久产物是架构 fixture：

```text
tests/architecture/top_level_module_budget.json
{ "schema": "loopx_top_level_module_budget_v0",
  "baseline_commit": "<sha>",
  "max_top_level_modules": 148,
  "allowlist": ["__init__.py", "entrypoint.py", "cli.py"] }
```

`max_top_level_modules` 只能在同时移动文件的 PR 中调低。allowlist 列出因入口点原因必须留在顶层的模块。

### 命令或事件生命周期

每组迁移按固定顺序执行：

1. `git mv` 该组到目标包（`loopx/chat/`、`loopx/hosts/` 或 `packages/loopx-capability-<name>/src/`）。
2. 盘点公开 import 与活跃内部 caller。已建立的公开名字在旧路径 re-export，每进程发出一次 `DeprecationWarning`；纯内部 caller 同步迁移并删除旧入口，必要迁移 bridge 写明移除条件。
3. 重新测量包含保留 shim 的顶层文件数，只按实际净减少调低 `max_top_level_modules`。
4. 运行 import 边界测试、预算测试、对触及文档的 `loopx check`，以及 package smoke lane。

任一项失败的迁移 PR 不可合并；改变行为的迁移 PR 视为超出范围而拒绝。

### Provider 或 extension 契约

capability 包直接采用现有 `extension.toml` 契约，不做扩展。唯一新规则：**`loopx/capabilities/` 下在 `main` 上没有内核调用方的 capability，在接受新特性前必须先迁到 `packages/`。** 内核调用方清单即第 2 节的 52 处 import；每处要么改为 registry 查询，要么被记录为保留的核心 capability（D2）。

### 按需加载与上下文

1. **解析：** 读取 build/install 或显式 extension 注册时生成的本地版本化元数据。
   顶层 help、命令列表与未知命令拒绝不能扫描仓库、导入所有 provider 或联网。
   重复注册、过期或不兼容元数据必须可见；优先级显式确定，不能依赖导入顺序。
2. **加载：** 导入所选命令及其依赖闭包。详细命令 help 可以加载自身 parser，但不能激活执行服务。
   可选导入留在所属路由之后，fallback 路径也须遵守。
3. **激活：** 校验 installed/enabled/ready 状态及作用域权限，再经现有生命周期 owner 启动所需服务。
   可选代码缺失时说明如何安装/启用；必需 authority 或 runtime 缺失时 fail closed。
   Kernel 导入/help 不要求 Node；有副作用命令保留本文的类型化 `node_unavailable` 边界。
4. **提供上下文：** 为所选工作和 executor 渲染现有受治理工作契约，复用统一 Controller/Executor
   设计的首次完整、后续差量与恢复语义。Required reads 和来源完整性仍有约束力。

相关来源/要求版本变化、Session generation 变化、compaction 或 ACK 丢失时，必须使上下文复用失效。
不能因为 packet 更小，就认为 executor 已掌握前文。连续性未知时走所属契约的恢复路径。
Capability 指令仅在适用时提供；authority 不能从 prompt 文字推导。

可选 observer 消费已提交事件或有界投影。除非现有契约明确要求它的 receipt，
其延迟不能进入准入/结算事务。只批处理尽力而为的 telemetry；不能为满足时间目标而延迟
authority receipt 持久化、弱化 fsync/事务保证，或缓存过期权限/租约决策。

### 受治理生命周期与外部副作用

通过统一工作契约使用既有 Goal/Agent/work/Turn 身份、revision、lease、effect id 与 receipt。
解析装配，校验要求和 authority，准入工作，经绑定宿主执行，验证并持久化证据，结算一次，
再经所属 channel 交付/回读。各步骤使用既有合法迁移与重放规则；这段描述不新增竞争的 status enum
或事务协调者。

Executor 崩溃、effect ACK 丢失、结果交付 ACK 丢失是不同恢复情形。
Operation provider 必须说明某个 effect 能否核对、能否安全重试。
外部状态未知时，后续依赖操作须等待回读或人工干预；“未观测到”不等于“未执行”。
设备幂等、物理联锁与补偿归 provider 负责，不能虚构为内核保证。

模型取消、进程终止、设备确认停止是不同事实。模型停止不能在缺少设备证据时生成
“设备已停止”声明。需要持续监督的 provider，在 effect 尚未解决时不能卸载。
结果验收、传输交付、用户回读也必须保留现有的不同证据。

### 产品与 benchmark 执行

遵守 [长程研究计划](long-horizon-harness-benchmark-research-program-v0.zh-CN.md)：
native/plain 对照、被动观测、受治理执行与 ablation 具有明确身份。
原生对照不接受 LoopX 指令或 hook。被动观测须验证无干扰。
受治理实验臂使用与产品任务相同的合格装配与迁移；改变治理的 ablation 必须作为具名实验变体，
不能成为产品默认行为。

冻结产品/宿主版本、解析后的装配、模型与采样设置、skills/tools、初始状态、预算、
checkpoint/恢复策略、数据集与 verifier 版本。按 run/arm 隔离记忆，禁止跨实验臂污染。
除了任务结果，还要报告 controller、executor、compaction、retry、recovery 与人工干预成本。
原生 runner/verifier 保留任务语义与评分。性能工作不能跳过 toolkit 的权限/完整性检查，
也不能把原始任务、trajectory 或 verifier 日志作为公开产品证据发布。

长程产品资格还要求证明：后续工作改善了已经有用的初始结果，或改变了决策。
故意削弱首个结果、增加文字或运行更久都不算进展。

## 6. 备选方案与设计选择

| 备选 | 为何现在不选 |
| --- | --- |
| **拆成多个 Git 仓库** | 在 TS 事务 cutover 期间违反 I1：内核语义每天变化，下游仓库会随每次变更断裂。评审产能是稀缺资源时，它成倍放大 CI、发布与评审成本。仅在内核迁移完成且某个包有独立 maintainers 后重开（附录 D）。 |
| **保持单 wheel，只加 extras** | 能减轻安装重量，但平铺命名空间与两套打包惯例照旧；贡献者仍找不到边界。 |
| **一次性全部搬到 `packages/`** | 同时对大量调用方破坏 I3，并把行为风险混入结构性变更。按组迁移并遵守 I3 兼容更易评审、更易回滚。 |
| **无条件把 Node 捆进 `loopx-core`** | 解决 I5，但让内核 wheel 变成平台相关且体积大。作为 D1 选项与 optional-extra 方案并列保留。 |
| **Minimal 与 standard 各自一套引擎** | 重复状态迁移，破坏产品/benchmark 一致性。 |
| **全部治理规则插件化** | 预算、停止、租约、结算可能随预设消失。 |
| **以上游 agent runtime 替代 LoopX 内核** | 模型执行可以是宿主 provider，不能替代 LoopX 工作 authority。 |
| **每种装配都强制依赖一个 daemon 或远程服务** | 增加有界本地使用的生命周期与部署成本；可选 daemon 仍遵循其既有契约。 |

Pi 展示了小型 agent core、工具/事件执行，以及与 coding 产品分离的显式上下文转换。
DSH 展示了同一 runtime 的多预设装配与自包含的最小 SDK bundle。
在固定版本中，DSH web minimal 与 standard 使用相同 agent-preset 包、不同 plugin 列表；
minimal 包含持久 shell、图像读取与 compaction。SDK minimal 还提供工作目录状态、持久化，
并按配置提供可选 MCP 资源。这些是装配范例，不能据此声称 DSH 恰好有两套引擎，
或把 LoopX headless adapter 与 DSH plugin 分别当成这两种预设。
借鉴显式边界，不引入 Cordis、上游权限默认值或新的 LoopX 模型 loop。固定来源见附录 C。

## 7. 安全、隐私与兼容

- **默认关闭 / 功能关闭一致性：** 缺失 `loopx-workspace` 或 capability 包时，行为等同现有 extension off state（I4）。没有命令会静默回退到另一实现。
- **公开/私有边界：** 不变。`loopx check` 继续扫描每个包；移动文件不改变其扫描类别。
- **旧读者/写者：** import shim（I3）覆盖已建立的公开 Python import。console script 保持名字；`loopx` meta-package 让 `pip install loopx` 在一个 minor 版本内行为完全一致。
- **混合版本：** meta-package 把所有层钉到同一发布列车。混合版本安装不受支持，由 `loopx doctor` 报告。
- **fail-closed：** 没有 runtime extra 时，有副作用的命令以 `node_unavailable` 失败，绝不在没有 TS 内核的情况下继续。

宿主可用、安装和 capability 声明都是观测，不是授权。Provider 调用保持 Goal/Agent/workspace/delivery
作用域。预设不能把必需上下文或停止/预算规则变为可选。未迁 CLI 路由保留 fallback，直到
parsing、global options、输出、错误与 exit code 合格；不兼容版本在 dispatch 前拒绝。
只有全部受支持命令完成迁移、help 与诊断一致性验证通过后，才能退役完整 parser。
不能让两个 writer 同时写同一 binding 来制造混合版本 rollout 可用的假象。

## 8. 迁移与回滚

| 步骤 | 门禁 | 回滚 |
| --- | --- | --- |
| 以当前数量加入预算测试 | 无；纯新增 | 删除 fixture |
| `chat_*` → `loopx/chat/` 遵守 I3 兼容 | import 边界 + 预算 + smoke 全绿 | revert PR 及 caller 更新；保留必要公开 shim |
| `*_goal_mode` → `loopx/hosts/` 遵守 I3 兼容 | 同上 | 同上 |
| 发布 `loopx-core` / `loopx-workspace` / meta `loopx` | package-smoke lane 在干净 runner 上分别安装 | 撤下预发布；`loopx` meta 保留旧布局一个版本 |
| capability 包迁移 | 逐个 capability，内核调用方清单已解决 | revert 单个包 |
| 移除 shim | 迁移后一个 minor 版本；弃用记入 update notes | 不适用；需调用方迁移 |

切换活跃 binding 的装配前，检查兼容、让执行 owner 静止并核对未完成 effect。
继续前验证新 owner 与解析后的装配；不能跨活跃 lease 热切换。回滚同样先核对并保留持久状态。
后续不可逆数据变化须另设迁移/导出门槛。

这些结构/装配变化的回滚不需要数据迁移。默认值变化须有明确 owner 决策与 release notes；
本提案不改变现有默认值。

## 9. 验证与验收

| 主张 | 测试或证据 | 要求结果 | 边界 / 排除 |
| --- | --- | --- | --- |
| 顶层模块数量不增长 | `tests/architecture/test_top_level_module_budget.py` | `len(loopx/*.py) > max_top_level_modules` 时失败 | 不评判模块质量 |
| 迁移后旧 import 仍可用 | 对每个迁移公开名字的 import 测试 | 通过并发出一次 `DeprecationWarning` | 仅一个 minor 版本 |
| 无 Node 的仅内核安装 | 无 Node 的 runner 上 package-smoke：`pip install loopx-core && loopx --help && loopx status --format json` | 退出码 0 | 不含有副作用命令 |
| 无 Node 的有副作用命令类型化失败 | 同一 runner：`loopx heartbeat-prompt ...` | `node_unavailable` 诊断，非零退出 | — |
| 缺失 capability 包 = off state | 仅安装 `loopx-core`；`loopx capability list` | capability 报告不可用，核心命令不受影响 | — |
| 无内核真相重复 | import 边界测试 + 审查 `packages/*/src` 是否 import 内核私有模块；过渡守卫：`tests/architecture/test_package_kernel_import_ratchet.py` 钉住每一个 `loopx.*` import，未经评审的新边失败，退役的边必须在同一 PR 降低 pin | 零内核私有违规 | 仅静态 import。Ark、Finance、JEV 的 13 条既有边仍作为迁移积压跟踪，与 MemT 的 2 条受支持公开接入边区分。守卫变绿不消除积压，也不满足本行；受支持的公开边不要求消失 |
| meta-package 一致性 | `pip install loopx` 后跑现有完整 smoke | 与拆分前 smoke 一致 | — |

以上七条结构性验收继续约束对应迁移/打包里程碑。运行时与旅程资格增加以下条目。

以下是拟定验收条件，不是已通过声明。确定性 fixture 根据语义独立设计，不能从当前实现输出反推期望。
Live、性能和晋级证据分别记录。

| Id / 声明 | 测试或证据 | 必须得到的结果 | 边界 |
| --- | --- | --- | --- |
| V1 / 按需加载 | 两种预设下对 import、help、列表、未知命令、所选路由做导入/进程/网络追踪 | 只加载声明过的依赖闭包；无可选激活 | 不证明执行性能 |
| V2 / authority 一致性 | 对要求、过期 claim、停止、预算、重放、结算做刻画与负例测试 | 两种预设具有相同合法/非法迁移，只结算一次 | Minimal 不得绕过 authority |
| V3 / CLI 兼容 | 受支持命令的差分语料，含 fallback、Node/provider 缺失 | Parsing、输出契约、exit code 稳定，失败可操作 | 明确列出未迁移路由 |
| V4 / managed minimal | 一个真实 managed 任务、注入中断、重启、证据验证与结果回读 | 身份相同，effect 已核对；不重复派发已确认工作或结算；恢复成本有记录，已验收结果可获取 | 初期一个合格宿主即可；纯 mock 执行不够 |
| V5 / 上下文连续性 | 首次上下文、无变化续行、来源修订、compaction、ACK 丢失 | 必需上下文完整或明确恢复；拒绝过期上下文 | 单独测 token 减少 |
| V6 / standard 旅程 | 原始 App/IM 请求、纠正/暂停、继续、完成与返回 | 一个执行 owner；已验收结果和成本可见；目的地不静默改变 | 每个前端/provider 分别合格 |
| V7 / 研究与 peers | 有用首个结果后的证据性改善；owner 恢复后的 peer 结果返回与采纳 | 新成果或有理由的决策修订，并有可归属证据 | 更多时间、peer 或文字本身不能通过 |
| V8 / benchmark 保真 | 固定的匹配实验、native/passive/governed 边界、记忆隔离、完整成本账本 | 原生语义完整；C0–C4 证据遵守研究/诊断 owner | 本 RFC 不声称分数或因果收益 |
| V9 / 物理副作用 | 模拟器/故障注入，再做显式授权的 provider 专属 live 验证 | 核对丢失 ACK；在保证范围内防重复动作；独立确认设备停止 | 不承诺通用 exactly-once 或紧急停止 |
| V10 / 开销 | 指定环境中重复、成对测量冷进程与热路径 | 报告 median/p95、RSS、模块数、文件 I/O、Node 启动/IPC、字节与模型 token；持久性无退化 | R0 确定数字预算；CLI 基线无须付费任务运行 |

本地开发候选目标为 p95：import 低于 50 ms、顶层 help 低于 100 ms、所选命令 help 低于 200 ms。
它们是 D6 的建议，不是已验证结果或跨机器 CI 阈值。
采用前要定义解释器启动、缓存条件、样本数、机器/runtime 版本。
真实 status/admission/settlement 延迟须在有代表性的小/大状态上单独测量，包含 SQLite 竞争和恢复；
不能把 CLI-help 目标套在这些操作上，也不能减掉其必需 I/O。

## 10. 运维契约

- `loopx doctor` 报告已安装的发行物集合与版本，并标记混合发布列车。
- `loopx capability list` 区分"未安装"与"已安装但未就绪"。
- 发布说明列出公开 import 的移动及保留 shim 的到期版本。
- 不引入新的守护进程、存储或网络面。

回读还说明解析后的装配/版本、所选宿主和 Session 模式、必需/可选 provider 与加载原因。
复用现有诊断/投影，不新增状态库。区分前提缺失、authority 过期和 effect 不确定。
启动与 Turn 开销分别记录；observer 故障不改写已提交结果，必需证据/交付 receipt 仍由所属 owner
治理。容量、背压、恢复继续遵守既有 work/host 契约。

## 11. 规范性交付计划

| 里程碑 | 交付行为 | 进入门禁 | 退出证据 | 回滚 |
| --- | --- | --- | --- | --- |
| M0 | 以当前数量加入预算 fixture 与测试；RFC 索引条目 | 本 RFC 被接受；重测 fixture 基线 | 预算测试绿；数量钉住 | 删除测试 |
| R0 | 已刻画的 `performance-diagnosis` selected route 与实测加载接缝 | 现有路由/owner/依赖图；D6 测量决策；不依赖包发布 | 本路由 V1/V3、V10 基线；行为不变 | 撤回路由提取 |
| M1 | `loopx/chat_*` → `loopx/chat/` 遵守 I3 兼容；预算按净减少调低 | M0 | 第 9 节第 1–2 行 | revert |
| M2 | `loopx/*_goal_mode` → `loopx/hosts/` 遵守 I3 兼容 | M0 | 第 1–2 行 | revert |
| M3 | `loopx-core` + `loopx-workspace` + meta `loopx` 以预发布发布；Node 按 D1 以 extra 或捆绑交付 | M1、M2；D1 已决；所选 D2/D5 装配已映射 | 第 3–4、7 行；在干净拆包安装上重验已合格的 R1/R2 旅程 | 撤下预发布 |
| R1 | Opt-in 可执行 minimal，含一个真实 managed Turn、重启与结果回读 | R0；所选 D2 内容和 D5 宿主/authority；可先于 M3 验证源码装配 | 此受支持组合的 V1–V5/V10；源码证据本身不代表拆包安装合格 | 核对后返回旧合格装配 |
| R2 | Standard 使用同一内核，只激活所选产品工作流 | R1；现有前端/Session 准入；可先于 M3 验证源码装配 | V2/V5/V6/V10、关闭一致性；声称拆包资格前，在 M3 重验冷安装旅程 | 关闭 opt-in 装配，保留状态 |
| M4 | 第一个无内核调用方的 capability 迁入 `packages/` | M3；D2 清单 | 第 5–6 行 | revert 单包 |
| M5 | 其余符合条件的 capability 迁移；M1/M2 的公开 shim 在文档化兼容窗口结束后移除 | M4 | 完整 smoke 一致 | 不适用 |
| R3 | 匹配的产品路径 benchmark 及长程/peer 旅程 | 所选界面的 R1 或 R2；冻结设计与单独运行授权 | V7/V8、完整成本、隐私与既有 C0–C4 资格 | 停用实验 overlay，保留有界证据 |
| R4 | 一个真实外部操作 provider，含核对与停止回读 | D7；具名操作 owner 和显式操作授权 | V9 加继承的治理门槛 | 停用前先静止/核对 |

M0–M5 保留原发行里程碑；R0–R4 是同一计划中的运行时和旅程资格切片。
R0 可以先于目录移动交付，但不代表内核合格。R3/R4 可选，不阻塞已合格的 CLI 或 managed 任务。
真实调用者出现前不增加闲置 provider、字段或框架。每个 PR 记录应用/推迟的相邻重构。

## 12. 未决决策

1. **D1 — `loopx-core` 的 Node 交付方式。** 负责人：内核 maintainers。选项：(a) `loopx-core[runtime]` extra，文档说明 Node 为前提；(b) 以平台 wheel 捆绑钉住版本的 Node；(c) 两者都做，(a) 为默认。建议：(c)。所需证据：package-smoke lane 的 wheel 体积与平台矩阵。截止：M3 前。
2. **D2 — 哪些 capability 留在 core。** 负责人：内核 + capability maintainers。输入：52 处内核侧 import。建议：只保留 Turn driver、quota 或 heartbeat prompt 在运行时需要的 capability，其余全部成包。偶然的 eager import 应经 registry 解耦，不能把其存在直接当作 core 必需性的证据。记录 minimal/standard 的精确安装/加载/激活闭包。截止：所选装配在 R1/M3 前，全量盘点在 M4 前。
3. **D3 — shim 存活期。** 负责人：发布负责人。选项：一个 minor 版本（建议）或两个。截止：M1。
4. **D4 — 索引位置。** 本 RFC 列在"控制面内核、状态与迁移"还是"运行时、Capability 与协作集成"。建议：内核章节，因为预算测试与 `loopx-core` 边界由内核负责。

5. **D5 — 首个可执行装配。** 负责人：runtime/host 与 authority maintainers。
   在现有发行方案内选择 minimal 的精确宿主/存储组合与 standard 映射。
   建议 minimal 先 opt-in，选择最小且已合格的 managed 路径，保留当前默认值。
   DSH/Pi 仍按选择 RFC 验证。证据：R1 退出前有 V4 恢复与 Session 连续性，M3 前有包回读。
6. **D6 — 性能预算。** 负责人：性能 maintainers。选择机器相关 p95 预算或相对回归界限；
   建议第 9 节候选预算加明确进程/缓存/样本条件。证据：可复现 R0 基线；真实 Turn 预算独立推导。
7. **D7 — 外部操作调用者。** 负责人：operation-provider maintainer。
   选择具体调用者或推迟物理资格。建议真实调用者出现前不做设备框架。
   R4 前须确定范围、幂等/回读/停止契约和操作授权。

新增建议不关闭 D1–D4，也不批准新默认值。批准须有评审决策，不能来自实现进展或沉默。

---

## 附录 A：执行台账（非规范）

### 2026-09-26 — RFC 开启

- **基线：** `3e443ad7c`
- **交付：** 仅提案；第 2 节指标在该基线上测量。
- **证据：** `ls loopx/*.py`、`ls loopx/chat_*.py`、`ls -d loopx/*_goal_mode` 的文件计数；`rg` 扫描 `loopx/` 得到的 import 位置。
- **已知缺口：** 全部里程碑。
- **对规范性设计的影响：** 无。

### 2026-09-27 — 合并前按新基线重测

- **基线：** `2f3d13ae9`（rebase 后的 PR 基线；从 RFC 撰写到本次合并之间 `main` 前进了 174 个 commit）。
- **交付：** 设计未变。第 2 节指标、第 5 节 fixture 示例、第 4 节审计版本与附录 C 各行按新基线重测，使 M0 钉住的是代码树真实拥有的数量。
- **证据：** 与附录 C E1–E3 相同命令；测得漂移为 `loopx/*.py` 143 → 148、`loopx/chat_*.py` 37 → 38、capability → `control_plane` 的 import 74 → 80、`loopx/*.py` 行数 7.88 万 → 7.98 万。内核侧 capability import 数（E2，52 处）与 `packages/` 数量（9）未变。
- **已知缺口：** 全部里程碑；D1–D4 仍为未决。
- **对规范性设计的影响：** 第 2、5 节的预算数字跟随新基线；"随时间只降不升"的决策不变。

### 2026-10-14 — 包 → 内核 import 钉住

- **基线：** `70bc98198`。
- **交付：** `tests/architecture/test_package_kernel_import_ratchet.py` 与
  `package_kernel_import_pins.json` 钉住三个 co-located 包今天 import 的 13 个
  `loopx.*` 模块。新增一条边即失败；移除一条边必须在同一 PR 降低 pin。
- **证据：** 对 `packages/*/src/**/*.py` 做 AST 扫描，包含函数体内 import。
  `loopx-ark-turn` import 了 `file_lock` 与 `control_plane.turn_driver.host_candidate`
  却未声明 `loopx` 依赖；`loopx-jev` import 了 7 个 `control_plane`/`capabilities`
  模块；`loopx-finance-value-discovery` import 了 2 个 `extensions` 模块。
- **已知缺口：** 第 9 节该行的必需结果仍是零违规；被钉住的 13 条边是 M4/M5 的
  迁移积压，不是被认可的接口，守卫变绿不满足该行，也不满足引用它的 M4/M5
  里程碑。D1–D4 仍未决。
- **对规范设计的影响：** 无；必需结果不变，证据列在人工审查之外增加一项可执行的
  过渡性不增长检查。

### 2026-10-15 — Checkpoint Host 组合与 presentation SDK 边界

- **基线：** `77b404b92`。
- **交付：** native checkpoint 检查、重建及研究上下文组合从 `control_plane`
  移到第 5 节已有的 `hosts` owner。CLI、benchmark 与测试内部调用方一起迁移；
  两个旧模块均未随 v1.4.0 发布，因此不保留公开兼容 shim。类型化状态与 effect
  权威仍由现有控制面持有。
- **边界修复：** Finance 经已有 `extensions.presentation` SDK import 使用共享
  本地路径分类器，不新增包到内核的依赖边，也不复制隐私 matcher。既有 13 条
  历史 import pin 保持不变。
- **证据：** 架构/import ratchet、Finance 最小公开 SDK fixture、真实 native
  checkpoint 捕获/分叉/采用及一致性反例。捕获在两次逻辑读取与物理封存之间复用
  一个检查进程，保留字节 witness，不重新打开历史库写入进程。
- **剩余：** 不关闭更广的 M1–M5 发行物与零私有 import 验收；D1–D4 仍未决。
  不改变打包方案或权威决策。

### 2026-10-30 — Codex provider-routing 激活栅栏的边界

- **基线：** `10c84d1c0c`。
- **交付：** 第 5 节中 Codex provider-routing host 的接缝现在写明该栅栏实际
  拒绝什么：它持有入口准入锁，并在仍有非终态 Turn 时拒绝继续。待处理的入口
  收据是在 provider 投递之前预留的外部输入，因此还不是 Turn，而该栅栏也不
  读取入口收据账本。
- **边界修复：** 只有一条待处理的 `loopx_queue` 或 `loopx_inbox` 收据会被
  放行，而 queued Turn 会以 `chat_work_pending` 被拒绝。该包的受支持依赖边仍是
  唯一被 pin 的 `loopx.extensions.chat_service_guard` import；本条目不改变任何
  import、权限、默认值或持久格式。
- **证据：** `tests/test_chat_runtime_activation.py::
  test_a_pending_ingress_receipt_alone_does_not_defer_the_restart` 与
  `test_a_queued_turn_still_defers_the_restart_with_a_pending_ingress_receipt`
  通过真实临时 Session 与 ingress store、针对真实栅栏，读出该边界的两侧。
- **剩余：** 不关闭更广的 M1–M5 发行物与零私有 import 验收；D1–D4 仍未决。
  待处理的入口收据仍不被该栅栏覆盖；要覆盖它是一次带自有验收的运行时变更，
  而不是文档变更。

## 附录 B：决策日志

| 日期 | 决策 | 负责人 / 批准 | 备选 | 变更的规范章节 |
| --- | --- | --- | --- | --- |
| — | — | — | — | — |

## 附录 C：证据登记

| 证据 id | 主张 | 基线 / 环境 | 产物或命令 | 结果 | 隐私 / 有效性边界 |
| --- | --- | --- | --- | --- | --- |
| E1 | 148 个顶层模块 | `2f3d13ae9` | `ls loopx/*.py \| wc -l` | 148 | 统计文件数，非公开 API |
| E2 | 52 处内核侧 capability import | `2f3d13ae9` | `rg -l "loopx\.capabilities\|from \.\.capabilities\|from \.capabilities" loopx/control_plane loopx/cli_commands loopx/*.py` | 52 | 仅静态 import |
| E3 | 80 处 capability → control_plane import | `2f3d13ae9` | `rg -l control_plane loopx/capabilities` | 80 | 含 docstring；为上界 |
| E4 | 所选 CLI 加载接缝 | `9cae3b28939f72fa4aadab5cfe49e19bcbdab372` | 第 4 节链接的入口/CLI | 源码审计 | 无新性能测量 |
| E5 | DSH 装配范例 | 固定上游 commit | [`minimal.patch.yml`](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/packages/bundle/web-app/presets/minimal.patch.yml)、[`standard.patch.yml`](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/packages/bundle/web-app/presets/standard.patch.yml)、[`sdk-minimal`](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/packages/bundle/sdk-minimal/README.md) | 源码审计 | 共享预设装配与自包含 SDK bundle；不代表 LoopX 宿主资格或权限建议 |
| E6 | Pi core/上下文范例 | 固定上游 commit | [`agent` core](https://github.com/badlogic/pi-mono/blob/f14813e96510a7cf7562a120e809c6a134af073d/packages/agent/README.md) | 源码审计 | Agent/tool/event core，以及 `transformContext`、`convertToLlm`、`prepareRequest`、`finishTurn` 接缝；无 LoopX authority 或性能声明 |

## 附录 D：被拒绝或被取代的备选

**多仓库拆分。** 本 RFC 拒绝的原因：在 TypeScript 控制面迁移处于事务 cutover 期间违反 I1，成倍放大 CI 与发布面，且需要目前不存在的按仓库 maintainers。可重开该决策的证据：迁移 RFC Stage 4 完成；某个 `packages/` 发行物拥有两位以上非内核 maintainers，且连续三个版本无跨包破坏性变更。

## 附录 E：事故与评审经验

暂无。
