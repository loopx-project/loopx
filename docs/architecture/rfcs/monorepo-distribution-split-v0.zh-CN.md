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
- [三层协议](https://github.com/loopx-project/loopx/blob/910ff3bca5c8ff5ee52bd691ae924cc37dbaf05f/docs/reference/protocols/multi-agent-three-layer-minimality-v0.md) 的 “kernel” 指更宽的复用层。
  本文治理内核指 authority 边界，启动依赖闭包指所选入口必需代码。
  可复用 runner 或展示设施无须进入每个启动闭包。

> 旧 tmux multi-agent 与 auto-research 演示已退役；上述演示合同链接固定到退役前版本，
> 仅用于解释历史设计，不是当前执行入口或共享运行时的归属。

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

### 2026-11-03 — 所选宿主模式发现路径的加载

- **基线：** `6a6a8590e`。此前 `performance-diagnosis` 与 `goal-acceptance`
  的 selected 路由已在该基线上；第 4 节审计仍为历史记录，不是当前实现清单。
- **变化：** `host-mode-plan` 经 `cli_runtime` 使用原有 registrar 与 handler，
  合法请求不再加载完整 CLI 或 Chat 依赖图。非法语法保留完整 parser 的诊断路径。
  宿主选择器、预览命令、readiness 与 authority 规则均不移动、不改变。
- **M1 集成：** [#7878](https://github.com/loopx-project/loopx/pull/7878) 已在
  `ffe84a693` 合入：17 个仅内部使用的 Chat owner 位于 `loopx/chat/`，顶层模块
  数量和预算均为 128（该移动组之前为 145）。这是结构变化，不代表性能或完整 M1 合格。
- **证据：** `tests/test_cli_entrypoint.py` 覆盖仅加载 owner 的 help、JSON 与
  Markdown 一致性、宿主不可用预览、非法请求及零进程、网络或文件写入激活。
  独立安装的 wheel 在没有 Node 或实时 registry 时验证 help、ready／unavailable
  预览及拒绝。每个版本／负载做 20 次本地成对新进程测量，丢弃一次预热、交替
  顺序、关闭遥测并保留热文件缓存。在 macOS arm64／Python 3.13.5 上，help／
  preview 的 LoopX 加载闭包从 1,149 降到 57 个模块，输出字节保持一致。时间／RSS
  留在 PR 的限定证据中，不作为可移植阈值或任务成果收益声明。
- **剩余：** 将 R0 类型的加载验证扩展到发现路径；不关闭 R0 的完整 V10 清点、
  M1 公开 import 迁移、发行物拆分或 R1 managed minimal。D1–D7 仍未决，
  不新增预设或改变默认值。

### 2026-11-03 — M1 公开 status 兼容性刻画

- **边界：** `ChatStatusRequestMixin` 与 `goal_read_runtime_root` 仍由
  `loopx.chat_status_api` 所有，Chat HTTP handler 与 Todo detail 是活跃调用方。
  本片交付刻画前置条件，不是 owner 迁移。
- **兼容证据：** `tests/test_chat_status_public_compatibility.py` 保护三个新进程
  import 顺序下的历史 import、历史 pickle 查找、reload 后已导入引用，以及经真实
  HTTP handler 生效的 registry、collector 和宿主观察 patch 点。发现路径仅容忍
  registry 缺失，不吞掉不可读或非法元数据。复制导出的 facade mutation 被源绑定
  Todo 调用方的 patch oracle 拒绝：仅 re-export 函数不能保留旧模块的 globals。
- **下一完整评审片：** 将完整 status owner 迁入 `loopx/chat/`，保留模块身份兼容的
  公开 bridge，并文档化 I3 弃用。同步重定向
  `tests/architecture/test_source_session_registry_denial.py` 中精确 loader 路径与
  `_discovery_registry` 函数例外，保留仅元数据发现及可执行 source-session 拒绝。
  本片不改变该 owner 或 oracle；不能为通过部分迁移而拆散所有权或放松拒绝 oracle。
- **剩余：** 刻画不交付 status 迁移及其安装 wheel 集成。I3 至少保留一个 minor
  版本，D3 精确退役时间仍由发布负责人决定。M1 未关闭，不声明模块数量、加载性能、
  拆包或 R1 managed Turn 收益。

### 2026-11-04 — M1 公开 status owner 迁移

- **变化：** 完整 `ChatStatusRequestMixin` 与 `goal_read_runtime_root` 实现迁入
  `loopx.chat.status_api`，Chat HTTP 与 Todo detail 直接导入该 owner。刻画前置
  [#8001](https://github.com/loopx-project/loopx/pull/8001) 已合入。
- **直接迁移：** 历史顶层 `loopx.chat_status_api` 模块直接删除，不保留 alias 或
  warning shim。仓库调用方与公开面审计均未发现外部 importer；所有活跃调用方和
  测试改用 `loopx.chat.status_api`。该兼容豁免仅适用于这个内部模块迁移，不构成
  删除受支持公开 import 的通用先例。
- **权限边界：** 同步重定向
  `tests/architecture/test_source_session_registry_denial.py` 的两个精确 owner
  路径项，全部拒绝断言及 `_discovery_registry` 例外不变。真实 HTTP 覆盖允许仅生命周期
  registry 的发现，但在 collector 执行前拒绝完整 status。移动 presentation owner
  不授予新的 runtime、来源、provider 或执行权威。
- **证据：** owner 测试要求 canonical import，并断言顶层 shim 不存在。既有 HTTP、
  File／SQLite 源路由、源丢失／恢复和 delivery review 测试保护原行为。解析为绝对
  import 后的 AST 对比覆盖完整 owner；拒绝 oracle 仅有两个 owner 路径差异。
- **剩余：** 这是 M1 的一个公开 owner 片，不关闭更广的公开 Chat 迁移、D3、发行物
  拆分或 R1 managed minimal。删除内部顶层模块属于表示层整理，不代表加载性能或
  长程成果收益。源码与隔离 wheel 验证不等于升级既有已安装宿主。

### 2026-11-04 — Capability 发现路径的所选加载

- **基线：** `79b781865`。`capability list` / `show` 及命令帮助复用既有
  capability registrar 与 catalog handler，不再加载完整 CLI 或 Chat。
  `inspect`、`bind`、`invoke` 的执行、显式完整 CLI 的 handler patch 点、registry
  路由及非法请求的规范诊断仍保留原路径。本地发现集合复用 CLI 词汇，不新增
  capability、provider、协议或权限 owner。
- **证据：** `tests/test_cli_entrypoint.py` 覆盖输出／帮助一致性、已声明但未安装
  及真实已安装且禁用的 provider、隐藏 helper、缺失／非法 manifest，以及不启动
  进程、不联网、不写文件。五个加载 oracle 在基线失败、候选通过；全部 23 个聚焦
  用例也在 checkout 外独立安装的 wheel 上、无 Node 条件下通过，不升级既有宿主。
  另有八组不可变基线／候选拒绝对照保留输出和退出码。
- **加载闭包测量：** macOS arm64/Python 3.13.5 上，三个新进程 help/list/show
  负载各自每版本采样 20 次，丢弃一次 warm-up 并交替顺序。两份源码快照均为暖文件
  缓存、无 bytecode，Node 与遥测关闭。真实 `main()` 路径加载的 LoopX 模块由
  1,149 降到 362，read-open 观察由 2,494 降到 832。输出字节完全一致；观察到的
  文件写入、进程启动、网络连接和线程启动均为零。耗时与 RSS 包含相同插桩及解释器
  启动，是 PR 中的限定证据，不是跨机器 D6 阈值，也不声明已安装宿主或任务收益。
- **验证保留项：** 更广的源码选择为 320 个通过，
  `test_settlement_plan_import_does_not_load_native_capture_io` 一个失败；不可变
  基线有完全相同的失败。配置的 mypy 在两个版本上同样报告 126 个文件中的 789 个
  错误。两项失败均不豁免、不改写为通过；聚焦一致性不证明所有必需检查通过。
- **剩余：** 本片独立于公开 status 提案 #8011，扩展 R0 类发现路径的资格证据，
  不关闭完整 R0/V10、M1、拆包安装、R1 managed minimal 或 D1–D7。不改变 preset、
  前端操作、权限或持久化状态；受保护执行仍由既有 owner 所有。

### 2026-11-07 — 调用方进程传输按需加载提案

- **基线与边界：**`7f521ff4505fa916a98a7401032922189eef414a` 的纯
  Turn-decision import 会加载 `extensions.process_runtime`。Doctor Git、
  contract 扫描、调用方 validation 和 Host bridge transport 改为使用时解析既有
  reexport。保留原函数对象、签名、direct/from/star import、名称发现、调用方补丁点、
  预加载 owner 的绑定和 reload 行为。复用现有 lazy-export 模式；导出名集合是本地
  兼容元数据，不是共享状态词汇。Python 保留平台传输，TypeScript 保留决策、deadline、
  authority 与 receipt；不新增 provider、capability、default 或进程算法。
- **可执行证据：**四个冷加载调用方 oracle 在不可变基线上失败。提案通过聚焦兼容
  检查和既有架构 oracle，保留 Turn-preview 的未选模块隔离门。真实隔离的 Git、
  validation 和 bridge 测试覆盖 timeout、cancellation、leader 退出后的后代、stdin
  策略、framing 和未确认 cleanup。重新构建的单体 wheel 在源码目录外重复兼容与真实
  进程检查；完整公开 discovery/star 名称集合及 transport 签名与基线一致。这是
  source/wheel 提案证据，不代表现有 host 升级或实际采用。
- **实测范围：**纯 Turn-decision import 的 LoopX 模块数从 338 降至 337。
  纯 import 和 help 没有进程启动或连接，支持的 help、unknown-command 输出与退出码
  保持一致。共享合成 SQLite fixture 覆盖接受与拒绝的已注册 Turn plan：两个版本都
  启动两个 Node 进程和十三个 Git probe，连接观察分别为 62/61 次。完整返回 envelope
  仅 fresh observation 的 source-decision digest 不同。已注册 plan 仍会选择进程
  transport，完整 preview 依赖闭包尚未通过冷加载资格。减少一个 import 不能证明
  latency、RSS、完整成本、managed-Turn 或长程收益。
- **剩余与回滚：**I3 至少保留一个 minor release。D2、D5、D6、I3、D3，以及完整
  R0/V10/M1/R1/context/model/install 验收仍开放。Canary 六个 direct check 通过，
  18 个 selected check 中 17 个通过；`first-connect-contract-smoke.py` 在基线与
  提案都报 `KeyError: should_run`，其 inline-field 假设需要独立修复。该失败与既有
  全库 lint/browser 失败仍开放。完成的作者提案可披露 hold 后进入正常 review；受保护 runtime
  合入仍需 maintainer 授权。回退本调用方加载改动即可恢复 eager binding，无需状态
  迁移或无关语言重写。

### 2026-11-06 — History 读取路径的所选加载

- **基线与边界：** `22b0f6c25`。普通 `history` 读取与帮助通过共享 CLI grammar
  和 guard 复用既有 registrar/handler。维护动作、parser 拒绝及已预加载完整 CLI
  的进程保留原路径，包括 handler/renderer patch 点。历史算法、证据窗口、公开
  import 与 TS owner 均不变。这是单体发行物中可回滚的 R0 读取片；公开迁移的
  I3 仍至少保留一个 minor 版本。
- **真实路径证据：** 基线／候选源码与实际安装 wheel 的四个环境，各有 27 个
  console 用例保留 stdout/stderr/退出码、UTC 排序、Agent 过滤、limit、空／未知
  输入与当前／过期证据读取。另有四个安装／源码 facade 探针保留被 patch 的
  handler 与 renderer。预加载 handler 的 oracle 在修复前失败；最终 history／
  parser 选择通过 26 个测试。既有历史、artifact 与证据 oracle 保留。必需原生
  服务使用可丢弃的数据／进程命名空间，并通过认证的退出回读确认清理。
- **匹配测量：** Apple M4 Pro、48 GiB、macOS 27 arm64、Python 3.13.5、
  Node 26.0.0；Python 依赖及源码配对中的 Node 依赖版本一致。四个负载各有 30
  组交错基线／候选配对，bytecode 预编译，Python 每次启动新进程，原生服务暖复用。
  最终采样 229 秒，三轮合计 698 秒，低于冻结的 20 分钟采样上限；安装、验证及
  tracing 单独记录。此前环境／兼容性修正前的观察保留原有效性范围。

| 负载 | elapsed 中位数 / p95 ms，基线 → 候选 | CPU 中位数 ms | CLI 峰值 RSS 中位数 MiB |
| --- | --- | --- | --- |
| 源码帮助 | 1552 / 1862 → 204 / 349 | 670 → 100 | 91.4 → 31.3 |
| 源码 Agent 读取 | 1603 / 1833 → 417 / 482 | 665 → 150 | 91.9 → 34.4 |
| Wheel 帮助 | 1621 / 2053 → 220 / 375 | 695 → 100 | 91.8 → 31.0 |
| Wheel Agent 读取 | 1648 / 1825 → 414 / 462 | 725 → 150 | 92.2 → 34.6 |

- **加载与效果：** 源码／wheel 帮助由 1157 降至 155 个 LoopX 模块，注册 Goal
  读取由 1158 降至 208，证据读取由 1159 降至 210。Python audit 的 read-open
  观察中，源码帮助为 1290 → 246，冷原生读取为 1311 → 723；wheel 分别为
  1291 → 247、1710 → 723。帮助观察到的写入／进程／连接均为零。冷注册读取
  保留六次 write-open、两个进程启动、一个连接，客户端 IPC 发送／接收为
  252/355 bytes；证据读取保留一次 write-open、两个连接及 1377/804 IPC bytes。
  这些必需效果不属于可选激活。已有服务可在 PATH 无 Node 时继续工作，冷缺失
  保留原拒绝。输出字节一致；耗时包含解释器与进程调用，子孙进程 CPU/RSS/I/O、
  完整模型与人工注意力成本仍未知。
- **保留项与后续：** 实测帮助 p95 仍高于提出的 200 ms 目标，不采纳 D6。
  source-session loader 清单及 Turn preview import 检查在未改动基线上同样失败，
  保留为资格验证限制。本片不关闭完整 R0/V10、M1、拆包安装、R1／context／宿主
  资格或开放设计决策。回滚只需撤回加载片，不涉及状态迁移。

### 2026-11-06 — Version 按需加载及匹配成本

独立候选 `8a79d8602e699e51452ca1f43dc9b9527c81aa1b` 对比基线
`5a58635028fbef1316dc66531daf5fdb655e9b13`，由 `cli_runtime` 选择既有
version registrar 与 handler，复用规范全局语法、format 优先级、usage hook
及 controller guard。解析拒绝保留完整 CLI fallback 与诊断；已显式加载完整
CLI 或绑定 package 导出的调用保留其 registrar/handler patch 点；普通查询不会
触发 lazy exports。公开 import、身份及类型不变。
不新增 capability、provider、package、权限、preset 或 TypeScript 决策 owner。
独立 DSH/helper 及负面成本提案仍在 [#8185](https://github.com/loopx-project/loopx/pull/8185)；
其 E7/E8 与本次 version 对照分开，也不构成依赖。

初始 `55e3ef45a` 对照的 120 个普通查询观察继续保留：source 中位数
1.546 → 0.059 秒、wheel 1.554 → 0.072 秒。随后独立用例发现它忽略已替换的
package-facade handler，返回 0 而非 37；此前通过的 premerge 未覆盖该分支。
原输入、数据和失败保留，不将其视为 facade 兼容资格。提案补回 package
registrar 与 handler 的采纳后，另行冻结下列精确 head 对照。

- **真实调用：** 两个不可变版本的源码及新安装单体 wheel 均经实际 `loopx`
  console 执行。每种发行形态的 27 个用例，stdout、stderr、退出状态完全一致，
  覆盖全局／局部／重复 format、显式路径、help、未知／缩写参数、缺失／空值及
  fallback。独立未知命令／参数 oracle 以 exit 2 拒绝且无结果。四个组合在缺少
  Node 时均可查询 version；outer-controller 读行为及完整 CLI patch 点兼容。
- **加载清点：** 独立 parent 插桩记录，两种发行形态均为 1,157 → 10 个 LoopX
  模块，候选是基线的子集。Source read-open 1,286 → 28，wheel 1,289 → 29。
  每个 version 捕获的 write-open、子进程、观测到的本地 IPC 连接及覆盖 socket
  字节均为零。Stdout 保持 81 字节，stderr 为空。
- **冻结对照：** 四个环境均核验来源，使用 CPython 3.13.5 及相同 57 个依赖
  版本。标准库、依赖及全部 1,421 个 LoopX 源文件，在独占 cache prefix 具有
  有效时间戳字节码；测量／profile 后的源码及 header 清点与预检一致，无缺失／
  无效缓存。每次采样是新 Python 进程，禁止字节码写入；磁盘缓存不受控，不比较
  broker／warm broker。顺序为源码先于 wheel，偶数对 AB、奇数对 BA。两组各
  完成 30 对，共 120 个有效观察，采样 62.246 秒。计入首轮后合计采样
  166.191 秒，低于不变的 1,200 秒上限；全部观察保留。

| 发行形态 | 基线秒数，中位数 / p95 | 候选秒数，中位数 / p95 | CPU 秒数，中位数（基线 → 候选） | RSS MiB，中位数（基线 → 候选） | 配对候选减基线的中位数，秒 |
| --- | --- | --- | --- | --- | --- |
| Source | 0.980 / 1.056 | 0.055 / 0.075 | 0.520 → 0.037 | 90.17 → 16.81 | -0.9242 |
| 单体 wheel | 0.948 / 1.147 | 0.056 / 0.077 | 0.511 → 0.038 | 89.89 → 16.60 | -0.8993 |

p95 使用 nearest rank；配对差中位数与两 arm 中位数之差不同。本次 macOS
arm64 条件下，source 与 wheel 中位延迟均约下降 94%，CPU 与 target RSS
也下降。一分钟宿主负载为 8.25–9.06。本片证明冻结条件下的明显版本查询收益，
不采纳可移植 D6 预算，不声明真实 managed Turn 或长程节省。用量覆盖 target
及等待子进程，清点中未启动子进程；native 细节及完整编写／模型／维护成本未知。

从本次唤醒时间戳到最终冻结约 2,404 秒，包含有重叠的入口恢复、首轮实验、修正、
依赖／构建／安装、测试、缓存及一致性验证；重叠阶段不可求和。缓存准备单独记录。
修正后的 console/facade 一致性为 82.759 秒、插桩 2.539 秒、四个 parent
profile 4.155 秒；首轮 136.239 / 3.830 / 7.089 秒成本继续保留。
不同轮的耗时水平不作为代码对照。既有
`performance-diagnosis` recipe 在基线采样栈中显示 export loading 及完整 parser
构建；栈权重与重复延迟分开。已验证单体打包，不代表拆包安装或已安装宿主升级。

七项 selected-loading 用例在基线失败、候选通过。Package registrar/handler
用例在基线通过、初始候选失败、修正后通过；规范诊断及完整 CLI patch 点在基线／
候选均通过。完整入口 161 项及聚焦 lint 通过。Owner-scoped mypy 使用
`--follow-imports=skip`，基线与候选在行号归一后均有 33 项相同错误，不是通过或
完整配置的类型检查。采纳仍需最终 head 验证、独立评审及 merge readiness。
I3 至少一个 minor release 的窗口继续有效，不作 D3／到期决策。R0 更广清点、
R1/minimal、D2/D5/D6、hard-lease／context／model 及拆包安装验收仍开放。
有界重构复用既有 parser/handler；更大 runtime/package 改动保留在其已接受 owner。

### 2026-11-06 — 发现路径集成与下一条可执行边界

- **已集成：** [#8012](https://github.com/loopx-project/loopx/pull/8012)
  于 11 月 4 日将提案 head `58442be4d` 合并为 `83f569178`。在独立准备的
  `main` 版本 `62e234c93` 上，全部 23 个发现与受保护分发用例通过。复验命令：
  `uv run --extra test python -m pytest -q tests/test_cli_entrypoint.py -k 'capability_discovery or capability_non_discovery'`。
  这验证当前源码集成；此前安装 wheel 与成对性能证据仍绑定原版本和环境。本次
  复验不证明已安装宿主或新 wheel 的资格。
- **已解决的前置问题：** [#8054](https://github.com/loopx-project/loopx/pull/8054)
  （`886f2cdff`）将 capture I/O 移出 settlement plan 的冷 import。
  `uv run --extra test python -m pytest -q tests/architecture/test_control_plane_import_boundaries.py::test_settlement_plan_import_does_not_load_native_capture_io`
  在 `62e234c93` 上通过。11 月 4 日的失败仍是历史证据；后续修复移除了这一
  特定 import 阻塞。本版本没有重跑更广的套件或配置的 mypy，不能将此前结果改写
  为当前通过资格。
- **下一源码切片：** 刻画既有 `generic-cli` managed Turn 路径，使用 SQLite
  authority 并关闭可选任务 hook，作为所选 D2/D5 装配的候选。复用 Turn、host
  selector 与类型化 authority owner。`tests/test_loopx_turn_driver.py` 中既有
  CLI 中断与 successor 重放测试可定位集成接缝，但不证明 minimal preset。
  下一片必须明确实际依赖闭包，并通过隔离、能产生任务产物的宿主进程验证中断、
  重启和独立验收后的结果回读，保留原身份且不重复已确认效果。确定性的本地宿主
  可以验证其合成 transport 边界，不证明模型宿主或完整 V4 旅程。
- **剩余：** 完整 R0/V10、M1/I3/D3、独立 status 提案 #8011、拆包安装与 R1
  均未关闭。D2/D5 仍由 owner 决策；本候选不改变 preset、默认值、执行权限或
  发布承诺。继续移动目录不构成下一条执行证据。

### 2026-11-06 — generic-cli/SQLite 装配候选与恢复证据

这是 D2/D5 候选及源码刻画，不是已批准的 preset。复用既有 Turn、宿主选择、
authority 与 settlement owner。

| 维度 | 候选／观察到的边界 |
| --- | --- |
| 安装 | 当前单体 `loopx` 源码 checkout；macOS arm64 上 Python 3.13.5、Node 26.0.0。test extras 只准备实验，不是最小安装；不证明拆分发行物或已安装宿主资格 |
| 宿主 | 既有 `generic-cli`、`isolated-headless`，经 `--host-adapter-command-json` 使用 JSON stdio；一个确定性的本地任务产物进程和独立、受信 argv 验收器。该夹具无须模型 session 或 workspace/IM 服务 |
| Authority | 经既有 TypeScript effect runtime 及本地 RPC 传输使用规范 SQLite，并复用 Turn journal、验收与结算。测试脚手架以 `soft_claim` shadow 初始化；不能仅靠这些证据证明正常 hard-lease 的 V2 一致性 |
| 必需的逻辑内核 | Bootstrap／所选 CLI、TurnEnvelope 与宿主准入、当前 authority／stop／budget／claim 规则、journal／恢复、进程监督、独立验收和结果／结算回读。owner 与 standard 共享 |
| 可选激活 | 夹具不启用任务 memory 或其他可选 hook，不运行模型任务或外部操作。加载 settings／policy helper 不证明激活，也不授权删除它们 |
| 身份与恢复 | 使用 `--resume-turn-key` 恢复原 journal；该路径有意拒绝新的 `--turn-instance-id`。复用缓存中已确认的宿主工作；未确认工作及其他中断点继续由各自契约治理 |

- **真实任务与恢复：** 真实宿主进程写出零至七的平方数，独立进程检查预先给定的
  字面数组。正常路径和 typed host result 持久化后对所持 CLI 注入 OS `SIGKILL`
  的路径，在各自隔离 SQLite 夹具中最终都只有一次宿主调用、一次 writeback 和
  一次扣额。中断路径在重启前没有扣额；重启验收缓存结果，不再次调用宿主。随后
  committed 重放报告没有重复 host／writeback／扣额／scheduler 效果。两条路径的
  产物字节一致；没有对活跃项目状态注入故障。
- **实际加载：** 产品版本 `62e234c93` 上，正常 Python 路径加载 538 个 LoopX
  模块，涉及 21 个 capability 命名空间，不加载 `loopx.cli` 或 `loopx.chat` 树。
  第二轮使用隔离的既有 runtime broker 和透明 Node module-load observer；其
  bootstrap、执行与恢复加载 `control_plane` 下 231 个 TypeScript 文件和八个
  JSON 文件。这是观察到的闭包，不是已批准的 core 成员。已核验私有 broker 的
  身份、停止 ACK 和进程退出；没有停止共享服务。
- **成本与限制：** 父进程插桩区分必需本地 RPC 与外部网络连接，后者观察为零。
  正常调用观察到 34 个 Git 子进程、一个 Node 版本探测、一个 Node 宿主监督器和
  一个 Python 验收器；监督器另行启动任务宿主。child／broker CPU 与 RSS、完整
  wire／context 字节及模型宿主成本尚未测量。这些小规模插桩演练不是重复的成对
  性能采样，不建立 D6 预算或长程收益证据。
- **独立回归证据：** `tests/test_loopx_turn_driver.py` 中
  `test_managed_start_interruption_preserves_reservation_and_attempt` 的六个
  SQLite 用例，以及 `test_turn_run_once_cli_rejects_unproven_host_claim_before_writeback`
  和 `test_turn_run_once_cli_replays_declared_successor_after_interruption` 均通过。
  探针设置拒绝与产品失败分别保留：过宽网络 observer、错误的 host-request schema
  及冲突的恢复 flags 已修正，不改生产代码、不放松权限，失败观察仍保留。
- **下一 owner 变更：** `loopx/cli_commands/turn_run_once.py` 在选宿主前导入
  DSH host builder，但调用点只在 `args.host == "dsh"` 分支。generic 路径因此
  加载未选择的 `turn_dsh_host` 和 `dsh_goal_mode.turn_host_adapter`。先刻画既有
  export／patch 点，再在其 owner 接缝延迟加载，并复验 generic／DSH／Codex 行为
  与默认关闭一致性。命名空间数量本身不支持删除共享 policy helper。
- **剩余：** opt-in minimal 选择、完整 D2/D5 采纳、hard-lease 与上下文 V2/V5
  一致性、尚未确认的宿主工作期间中断、模型宿主 V4、完整 V10 与拆包安装资格仍
  未关闭。不引入新 engine、provider、默认值、权限字段或 runtime self-merge。

### 2026-11-07 — Heartbeat pre-quota 按需加载

- **基线与 owner：**`79665b97a`。heartbeat builder 已在 external evidence polling
  可用时生成 `heartbeat-prequota`。选择性 parser 复用原 heartbeat registrar，dispatch
  放在现有 heartbeat transport 模块；完整 facade 显式传入原 runner、renderer 和全局
  registry resolver patch points。显式／隐式路由、别名、格式、timeout、异常、降级返回和
  controller 语义均已刻画；reconciliation／hook 顺序及 typed owners 保持原路径。
- **源码与包证据：**新增 50 个 transport／加载／真实后端用例；四个独立核对的源码／
  wheel console 各运行 22 个场景；16 个配置／降级 File／SQLite console 场景覆盖重放、
  opaque 原工作保留和零 quota 扣费。删除 `continue_to_quota` 的 mutation 经同一公开
  console oracle 在两种真实 store 上被拒绝。原 acknowledgement smoke 同时覆盖两个
  CLI facade。完整源码选择初轮得到 378 pass、两个路由测试失败，精确基线同样失败。
  fixture 改为读取原 digest-bound progressive capture，不重新准入；受影响文件随后
  17 个用例全部通过。失败未豁免。聚焦 lint、配置内 mypy 和代码 canary 通过。
- **配对本地成本：**macOS 27.0 arm64、Python 3.13.5、Node 26.0.0；相同依赖和合成
  store，预编译 bytecode、全新 Python 进程、热文件缓存及复用的隔离 Node 服务。
  每个 workload／distribution 30 个交错 base／head 配对，共 360 个样本，采样 339 秒；
  study 准备／验证另计 260 秒。测量使用共享开发机，并非空闲机器实验。下表是含解释器
  启动的墙钟 median／p95（秒）和进程 RSS 中位数（MiB）。

| Workload / distribution | 基线 median / p95 | 候选 median / p95 | 基线 → 候选 RSS |
| --- | --- | --- | --- |
| `source/prequota-help` | 1.433 / 1.889 | 0.172 / 0.226 | 91.5 → 24.7 |
| `source/prequota-file-off` | 1.494 / 1.927 | 0.545 / 0.667 | 92.0 → 47.0 |
| `source/prequota-sqlite-off` | 1.547 / 1.977 | 0.507 / 0.640 | 91.9 → 46.3 |
| `wheel/prequota-help` | 1.555 / 1.967 | 0.170 / 0.210 | 91.7 → 24.5 |
| `wheel/prequota-file-off` | 1.545 / 2.155 | 0.536 / 0.650 | 91.7 → 46.7 |
| `wheel/prequota-sqlite-off` | 1.397 / 1.866 | 0.531 / 0.667 | 92.0 → 46.9 |

- **加载边界与完整成本限制：**help 的 LoopX 模块为 29，基线 1,158；默认关闭的执行
  为 277，基线 1,160。Python open 观察由 1,292–1,312 降至 81–395；打开文件尺寸之和
  是 footprint，不是实际读取字节。候选 help 的 user／system 进程 CPU 中位数为
  0.04／0.02 秒，执行为 0.13–0.14／0.065–0.08 秒；基线为
  0.40–0.42／0.16–0.185 秒。help 输出仍为 737 字节，JSON 执行仍为 827 字节。
  独立 SQLite loopback profile 在两版均观察到发送 1,362、接收 668 字节；冷服务启动
  Node version／server 两个进程，复用服务启动数为零。单次带 instrumentation 的冷／
  热观察不能拆出 Node readiness 或 service CPU，相关成本未通过资格。初轮 IPC probe
  误以为使用 Unix socket 而失败，改为 loopback instrumentation 后通过，未修改产品。
  help p95 为 0.226／0.210 秒，超过提议的 0.200 秒目标；未晋升或重标该 D6 提议。
- **剩余：**这是一条 R0／V1／V3／V10 caller 的作者提案，I3 至少保留一个 minor release。
  完整 R0／V10、M1／#8011、R1、context／model、split-install、installed-host 恢复、
  采纳和完整任务成本仍开放。Python 仅适配 transport，没有新增 decision owner、preset、
  provider 或权限规则。最终 exact-head review／CI 仍是独立交付门，与这些本地观察分开。
### 2026-11-07 — Registry 检查的选择性加载

- **基线与边界：** `ac6296225`。`registry` 和 `registry-boundary` 使用现有
  selected CLI 路由。共享注册器与传输适配器留在 `cli_runtime`；显式完整 CLI
  通过 `support_control` 复用它们，保留原有检查函数与 renderer patch point。
  Registry 解析、分类及 Git 判定仍归现有 owner。Python 在此只适配 CLI，不新增
  决策 owner、provider 或 dispatcher 框架。既有导入与 I3 至少一个 minor release
  的兼容窗口保持不变。
- **兼容证据：** 源码 CLI 与 support ownership 的 197 个用例通过，包含新增的
  26 个检查结果、拒绝与 patch-point 用例及两个 help 用例。独立的完整 CLI 加载
  排除断言经同一公开入口，在不可变基线上失败、候选版本上通过。20 个成功与
  拒绝请求在源码及独立安装的单体 wheel 控制台上保持输出、状态与异常语义一致。
  Help 与读取路径在缺少 Node 时仍可用，并排除未选择的 Chat、Dashboard 和
  benchmark 执行模块。两版本在一次性临时目录中均通过真实 Git registry-boundary
  smoke；先前放在 `.local` 下的运行失败，因为该祖先路径会按既有规则选择私有
  registry 分类。这次失败作为 fixture 放置证据保留。
- **成本资格：** 源码与 wheel 各四条 help/读取路径，每条运行 30 对交替 base/head
  样本，共 480 个样本，采样耗时 416.7 秒；研究准备、兼容验证及 profiling 另耗时
  278.1 秒，不含此前环境、wheel 准备及测试。macOS arm64/Python 3.13.5、相同依赖、
  预编译字节码和暖文件缓存下，新进程控制台 help 中位数从 1.536–1.609 秒降至
  0.100–0.103 秒；registry 读取从 1.584–1.631 秒降至 0.301–0.310 秒，边界读取从
  1.542–1.612 秒降至 0.222–0.225 秒。RSS 中位数从约 91–92 MiB 降至 16–30 MiB。
  两条 help 的 LoopX 模块闭包均从 1,158 降至 9；registry/边界读取降至 148/44。
  PR 记录逐路径 median/p95、CPU、独立 audit 的文件打开次数及未改变的输出字节。
  未观察到 Node 启动/IPC 或 Python audit socket 事件；边界读取保留三次 Git 查询。
  暖缓存块 I/O 为零，准确读取字节数及 audit 之外的原生活动仍未知。这些结果只限定
  本地 CLI 成本改善，不建立可移植 D6 限值、完整准备/model 成本或采纳结论。
- **验证与剩余工作：** 改动文件 lint 通过。CI lint 范围仍失败于基线已有的 F841，
  其修复已另行提案；这里不豁免该结果。源码与 wheel 证据不升级已安装 host。
  M1/#8011、完整 R0/V10、R1 的真实受管 Turn、context/model 组合、拆包安装、采纳
  与完整成本仍开放。D2/D5/D6/D3、默认行为及权限判定均不改变。相关有界重构
  共享既有 grammar 与检查适配器；更大的 support/control 提取不纳入本切片。
### 2026-11-06 — 所选 Turn 宿主的加载接缝

承接 [#8180](https://github.com/loopx-project/loopx/pull/8180) 的
generic-cli/SQLite 候选，源码提案 `9b80cf7d6` 在 `turn_run_once` 延迟加载 DSH
builder，直到调用方选择 DSH 或显式请求既有 export。命名 import、原 builder
身份、静态类型、`dir()`、star import 与 CLI builder patch 点均保留。复用既有
宿主／authority owner；这不选择 minimal preset，也不代表 D2/D5 已采纳。

- **观察到的变化：** 真实 generic-cli/SQLite 任务路径加载 533 个 LoopX 模块，
  基线 `62e234c93` 为 538。移出的五个模块是 `turn_dsh_host`、
  `turn_driver.host_candidate`、`dsh_goal_mode` 及其 `host_failure_map`／
  `turn_host_adapter`，没有新增模块。冷 import oracle 在基线失败、提案通过；
  既有 DSH CLI 夹具还证明公开 builder patch 点被使用。
- **真实路径一致性：** 同一个私有 harness 在两个版本的隔离 SQLite 夹具上运行
  正常完成、typed host result 持久化后中断、原 journal 恢复及 committed 重放。
  每个夹具最终仅一次宿主调用／attempt、一次 writeback 和一次扣额，重放不重复
  效果。独立 argv 验收器接受字节相同的产物。每个版本使用隔离 native broker，
  已核验停止与 OS 退出。这仍是确定性 transport 证据，不是模型宿主 V4 或正常
  hard-lease V2 的资格。
- **观察成本：** 每个版本一次正常插桩调用记录耗时 2.962 → 3.169 秒，父进程
  CPU 0.737 → 0.775 秒、峰值 RSS 65.6 → 64.6 MiB；输出均为 3,474 字节。
  已 wait 子进程的 CPU 为 0.567 → 0.599 秒。缓存、执行顺序与竞争工作未受控，
  这些观察不证明延迟改善，也不建立回归阈值。broker RSS 观察及 wait 子进程的
  usage 不覆盖完整进程树峰值、完整 wire／context 字节或模型成本。加载变化较小，
  长程收益和 D6 资格仍未证明。
- **`bc59a9ed4` 的历史验证限制：** 更广的源码选择为 596 个通过、一个 preview 冷 import 测试失败；
  基线独立出现同一失败，原因是 `host_bridge_transport` 提前导入
  `extensions.process_runtime`。owner 范围的 mypy 使用 `--follow-imports=skip`，
  两个版本在线号归一化后均报告相同 29 个诊断，不代表完整配置的 mypy 套件。
  两项失败均不豁免、不改写为通过。下述 process helper 接缝在同一作者提案中
  修复 preview 失败，并保留类型检查限制。
- **剩余：** 采纳仍受独立评审、必需验证和精确 head 的 merge readiness 治理。
  opt-in minimal 选择、D2/D5 决策、完整 R0/V10/R1、hard-lease／上下文／模型
  一致性和拆包安装仍未关闭；独立 status 提案 #8011 保留原限制。不改变权限、
  provider、默认值、已安装宿主或发布行为。

### 2026-11-06 — Preview process helper 保留在执行路径

后续 runtime 提案 `a44e27d96` 修复未改动的 preview import oracle。先延迟
bridge helper 暴露了 `doctor_git` 的第二条 eager import；两个 adapter 现均在
执行／清理或显式 import 时解析既有 `process_runtime` owner。命名／star import、
原函数身份、静态 alias、`dir()` 和公开 patch 点均保留。TypeScript Host lifetime
及进程／权限判定不迁移。

- **可观察结果：** 导入 preview command 加载 337 个 LoopX 模块，`bc59a9ed4`
  为 338，仅移出 `extensions.process_runtime`。真实 Git 成功读取同样不加载清理
  helper。实际 generic-cli 执行仍需要这些 helper，两个版本均加载 533 个模块。
  本片修复加载边界，不证明提速或长程收益。
- **真实路径：** 同一个隔离 SQLite harness 在两个不可变 runtime 版本通过正常
  完成、typed result 后中断、原 journal 恢复及 committed 重放。产物字节相同，
  每个夹具仅一次宿主调用／attempt、writeback 和扣额，重放无重复效果。两个自有
  broker 均停止并核验 OS 退出。真实 bridge send／cancel、背压、deadline 和清理
  未确认分支保留行为；真实 Git 超时／终端取消退役查询自有 helper。清理错误通过
  既有 patch 点保留 unknown 诊断。
- **限制：** macOS 跳过七个 Windows Job Object 用例。一轮组合验证因 Git 夹具
  hook 未在既有预算内启动而失败；随后隔离 Git 选择通过，未修改预算或 oracle。
  owner 范围的 mypy 检查三个受影响加载 adapter，基线与提案仍有相同 29 个诊断。
  完整配置 mypy、打包／拆包安装、minimal 选择、D2/D5、完整 R0/R1/V10、
  hard-lease／上下文／模型一致性及完整成本收益仍未取得资格。采纳仍受独立精确
  head 评审与必需验证治理；前述历史失败仍是历史失败。

### 2026-11-06 — 成对成本实验仍未取得资格

`a9485073f` 对比 `86d9c542d` 的有界实验产生了有用的负面证据，未证明可归因的
提速。实验在冻结的 20 分钟采样墙钟上限结束，共有 499 个通过语义断言的观察：
八个源码组各完成 30 对；wheel 的 JSON version 完成九对，另有一个未配对的候选
观察，其余 wheel 组未采样。原标准和全部观察均保留；上限触发及未完成组不改写为
资格通过。随后集成的目标 `cd677182c` 不在这组不可变版本的测量中。

环境为 macOS arm64、CPython 3.13.5、Node 26.0.0，真实 backend 是 Node owner
的 SQLite 3.53.1。源码和隔离的单体 `loopx` 2.2.0 wheel 环境具有相同的 57 个
依赖版本，并核验实际 import 来源。公开入口依次运行 JSON version、所选
`performance-diagnosis` help、status、quota admission 和确定性 generic-cli
任务，复用既有 `_write_live_fixture(provider="sqlite")` owner。顺序冻结为源码
先于 wheel、上述命令顺序、冷路径先于暖路径，每对交替基线／候选顺序。每个任务
使用新的合成状态。冷路径为新 Python 进程加已退役的自有 broker；暖路径为新
Python 进程加保留的 broker。文件系统缓存未受控、禁止写入字节码，不声明冷磁盘。

**缓存限制：** 既有字节码未归一化。后续盘点发现源码组 LoopX `.pyc` 数为
7 对 1,241，依赖 `.pyc` 数为 2 对 344。两个版本的 JSON version 均加载
1,157 个 LoopX 模块，父进程 read-open census 为 2,402 对 1,253。因此不能把
源码观察到的下降归因于五个模块的加载变化；下表仅描述这些缓存条件。

| 源码操作 | Broker 条件 | 基线秒数，中位数 / p95 | 候选秒数，中位数 / p95 |
| --- | --- | --- | --- |
| JSON version | 无 | 3.092 / 3.394 | 1.707 / 2.110 |
| 所选 help | 无 | 0.093 / 0.174 | 0.101 / 0.165 |
| Status | 冷 | 2.104 / 2.437 | 1.832 / 2.074 |
| Status | 暖 | 1.304 / 1.422 | 0.985 / 1.115 |
| Quota admission | 冷 | 2.481 / 2.720 | 2.029 / 2.331 |
| Quota admission | 暖 | 1.785 / 1.881 | 1.313 / 1.460 |
| Generic-cli 任务 | 冷 | 3.910 / 4.158 | 3.446 / 3.803 |
| Generic-cli 任务 | 暖 | 2.837 / 3.025 | 2.424 / 2.635 |

- **覆盖的成本：** 冷任务 target 和已 wait 后代的 CPU 中位数／p95 为
  1.769 / 1.832 对 1.283 / 1.388 秒，其最大 RSS 为 79.2 / 87.4 对
  75.5 / 78.7 MiB。独立 broker CPU 为 0.720 / 0.780 对 0.735 / 0.800 秒，
  结束时 RSS 为 126.6 / 129.7 对 128.3 / 133.5 MiB；不代表并发进程树峰值或
  完整 CPU 账本。五个操作的输出分别保持 81、325、21,829、23,795、3,557 字节。
  宿主一分钟 load 范围为 5.00–14.42；这是具名环境的观察。
- **独立 instrumentation：** 源码和 wheel 的任务加载仍为 538 → 533。
  冷任务父进程 read/write-open 为源码 2,179/284 → 1,643/284、wheel
  2,184/284 → 2,173/284。四个 capture 各有 238 次本地 IPC 连接；覆盖的 socket
  send/receive 字节为源码 895,335/391,794 与 895,339/391,794，两个 wheel 均为
  894,790/391,543，仅覆盖已观察的 Python socket，不覆盖全部 context、transport
  或后代 I/O。独立源码冷任务 audit 的父进程／已 wait 子进程 CPU 为
  1.234/0.560 → 0.802/0.582 秒，最大 RSS 为 88.7/75.3 → 63.4/75.4 MiB；
  这些 instrumentation 样本不作为计时证据。既有 performance-diagnosis recipe 分别检查 Pyinstrument 和实际
  Node worker CPU profile。父进程等待、workspace capture 和读取，以及 worker
  启动／退役仍是调查线索；profile elapsed／idle 权重不进入计时表。完整 JSON
  version 走 full parser/export graph，`--version` 仅加载两个 LoopX 模块。
- **语义回读：** 所有已采样任务通过独立断言，产物为相同的八个整数平方。源码和
  wheel 在两个版本均通过正常 committed 重放、typed result 落盘后中断、原
  journal 恢复及重放。每个被接受的夹具仅一次宿主 attempt／调用、writeback 和
  扣额，重放不重复效果。移除最后一个平方值使四个独立 validator 均拒绝，只有
  一次 dispatch、零 writeback／扣额。自有 broker 结束后核验 OS PID 退出。
  一个 seed shutdown 起初选到不同 interpreter alias 的 fingerprint；保留该失败，
  随后通过核验过的自有 locator 恢复，未触及宿主状态。
- **完整成本限制及下一步：** setup／编排为 888 秒，包含 194 秒夹具准备和
  98 秒成功 preflight；失败的 bundle build、调用及 preflight 尝试仍为失败。
  20 分钟墙钟上限内，采样 target elapsed 总计 1,001 秒。独立语义验证耗时
  39 秒；产物拒绝及诊断／清理恢复 184 秒；instrumented census 为 108 秒，
  任务／version profiling 为 58/20 秒。后续撰写／工具／模型成本、完整
  native／thread 工作及进程树峰值仍未完整覆盖。两个 wheel 均有
  1,997 个文件、压缩体积约 8.9 MiB，不代表拆包安装资格。下一轮成本实验必须
  对齐字节码条件，并在冻结前根据实际准备／调用成本选定可完成的代表性
  source/wheel corpus；进一步加载修改等待该对照。R0/V10、R1/minimal、D2/D5/D6、
  I3/D3、模型／上下文／hard-lease 和安装缺口保留既有 owner／gate，本 checkpoint
  不关闭其中任何一项。

### 2026-11-06 — 字节码匹配的代表性成本 checkpoint

新冻结对照比较 `546c08c0d3f22358ef09b13e69f7edaa806121b8` 与
`0df5ad7921faab3b9ab8ec809f2fd71060692cce`。所选 source/wheel 六组各完成
30 对交错样本：360 个有效观察，采样耗时 948.573 秒，保持原定 1,200 秒上限。
E7 仍是不完整且缓存混杂的实验；本轮数据单独保留。测量候选包含截至
`546c08c0d` 的目标 main；后续目标分支集成不在本次不可变成本对照内。

环境仍为 macOS arm64、CPython 3.13.5、Node 26.0.0 及真实 Node-owned
SQLite 3.53.1。四个源码／单体 wheel 环境的来源已核验，57 个依赖版本相同。
冻结前，各 arm 在独占 `PYTHONPYCACHEPREFIX` 编译标准库、依赖及全部
1,421 个 LoopX Python 源文件；时间戳 header 清点没有缺失／无效的自身或依赖缓存。
同一发行形态的相对源码集合一致。采样、恢复和 profile 后，缓存清点与冻结 harness
哈希仍相同；目标进程禁止字节码写入。顺序固定为源码先于 wheel、help 先于 task、
cold 先于 warm；偶数对先 baseline，奇数对先 candidate。每次调用都是新 Python
进程；cold 退休独占 broker，warm 保留 broker。磁盘缓存不受控，不代表冷磁盘。
一分钟宿主负载范围为 8.35–88.73；保留全部尾部观察，不将源码 cold p95 差异
归因于代码。

| 所选操作 | Broker | 基线秒数，中位数 / p95 | 候选秒数，中位数 / p95 | 每对候选减基线的中位数，秒 |
| --- | --- | --- | --- | --- |
| Source help | 无 | 0.136 / 0.193 | 0.138 / 0.168 | +0.0010 |
| Source SQLite task | cold | 3.579 / 5.997 | 3.617 / 9.234 | -0.0195 |
| Source SQLite task | warm | 2.673 / 3.019 | 2.657 / 2.979 | -0.0602 |
| Wheel help | 无 | 0.087 / 0.107 | 0.083 / 0.116 | -0.0033 |
| Wheel SQLite task | cold | 3.722 / 4.266 | 3.663 / 4.430 | -0.0847 |
| Wheel SQLite task | warm | 2.707 / 3.111 | 2.797 / 3.156 | -0.0457 |

p95 使用 nearest rank。配对差的中位数与两 arm 中位数之差是不同统计量。
本轮未证明明显延迟或 CPU 收益：arm 中位数和尾部变化方向不一致，task CPU
相近。数据证明代表性对照完整执行及语义一致，不证明速度阈值、完整 V10 或
长程收益。

| Task 路径 | Target 与等待子进程 CPU 秒数，中位数（基线 → 候选） | Target/等待子进程峰值 RSS MiB，中位数（基线 → 候选） | 独占 broker CPU 秒数，中位数（基线 → 候选） | Broker 结束 RSS MiB，中位数 / p95（基线 → 候选） |
| --- | --- | --- | --- | --- |
| Source cold | 1.311 → 1.325 | 75.75 → 75.58 | 0.770 → 0.775 | 127.9 / 134.8 → 128.6 / 133.5 |
| Source warm | 1.235 → 1.226 | 76.15 → 76.16 | 0.180 → 0.185 | 146.5 / 148.4 → 129.8 / 148.7 |
| Wheel cold | 1.353 → 1.369 | 75.94 → 76.56 | 0.805 → 0.810 | 129.5 / 134.4 → 130.1 / 134.1 |
| Wheel warm | 1.273 → 1.260 | 76.05 → 76.69 | 0.190 → 0.180 | 100.4 / 136.3 → 101.4 / 140.2 |

上述 RSS 不是并发进程树峰值；warm broker 结束 RSS 还反映其保留生命周期。
Help 的 target/等待子进程 RSS 中位数为 17.56–17.85 MiB，CPU 中位数为
0.046–0.056 秒。未插桩 stdout 在两 arm 均为 help 325 字节、source task
3,557 字节、wheel task 3,550 字节，stderr 为空。Target/等待子进程块 I/O
计数不覆盖全部 broker/native I/O。

独立插桩确认两种发行形态的 task 加载均为 538 → 533，仅移除前述五个 DSH
模块；help 保持 12 个模块。Parent read-open 为 source cold 1,680 → 1,675、
source warm 1,269 → 1,264、wheel cold 1,683 → 1,677、wheel warm
1,272 → 1,267。Task write-open 保持 cold 284、warm 279，观测到的 parent
本地 socket 连接均为 238。覆盖的 task 发送／接收字节范围为
893,698–894,864 / 392,608–392,859；source 候选少发送五字节，wheel 不变。
Help 在 source 打开 31 个文件、wheel 32 个，无写入或 socket 流量。
插桩 task parent CPU 为 0.635–0.774 秒、峰值 RSS 62.3–65.5 MiB；等待子进程
CPU 0.528–0.573 秒、峰值 RSS 76.1–78.6 MiB。单次捕获只作清点，与重复计时分开。

四个版本组合的同源隔离真实 SQLite 夹具均通过普通 committed replay、typed-result
持久化后中断、原 journal 恢复及 committed replay。独立平方整数产物／转移 oracle
确认产物字节相同，host invocation/attempt、writeback、扣额各一次。Resume
不重复 dispatch，replay 不重复 effect。移除最后一个平方值时，四组均以 exit 1 /
failed 拒绝，dispatch 一次而 writeback／扣额为零。所有独占 broker 均取得
shutdown 与 OS 退出读回，实验 broker 已全部退出。

准备／编排耗时 1,018.786 秒，包含有重叠的构建、安装、夹具及缓存工作，各阶段
不能求和。独立拒绝阶段为 16.056 秒、插桩 39.116 秒、八个 parent／实际 Node
profile 捕获 54.880 秒。恢复／重放 target 耗时单独记录，其完整编排成本尚未覆盖。
既有 `performance-diagnosis` recipe 在 Python 观察中显示 workspace capture、
journal checkpoint、Git probe 及 quota preparation；Node profile 包含启动、
请求 dispatch、idle 与 retirement。采样栈权重不是 benchmark 延迟或根因证明；
inclusive 权重、warmup 与 retirement 不能相加作为 task 节省。完整 context、
进程树／native／thread／模型成本仍未知。单体 wheel 仍约 8.9 MiB，不证明拆包安装。

**证据驱动的下一步：** 保留已验证的兼容／加载接缝，停止为未证明的提速扩展这些
helper。优先有界刻画既有完整 parser/export 闭包或实测 workspace/journal/bridge
owner；采纳性能变更前必须另做匹配的真实路径对照。本 checkpoint 不新增 owner、
预算、preset 或默认行为。R0 完整清点、R1/minimal、D2/D5/D6、I3/D3、
hard-lease／context／model 和拆包安装验收仍开放；采纳仍受独立 exact-head
评审及必需验证约束。
### 2026-11-07 — 完整 governed CLI 组合成本与归档后终结边界

对照 main `8e86f173f08bd55eb833d8cdf42360f93bf989fe` 和私有实验组合：
#8240 `4df990c9e66465171f980176fba4e3602d41f826` 加上
#8243 `668bed2f03a274f273d39c187121a1a4ede67165`。候选只应用两提案的七个
生产 Python 路径差分。分别构建、独立安装的单体 wheel 各有 1,998 个文件、
约 8.91 MiB 压缩大小，1,836 个 runtime resource 与对应源码逐字节核对。
全部环境在 macOS arm64 上使用 CPython 3.13.5、Node 26.0.0、真实 Node 所有的
SQLite 3.53.1 和相同的 57 个依赖版本。这里只是作者实验组合，没有升级已安装
host，也没有修改交付 preset、authority provider 或公共 runtime。

采样前冻结代码/resource/wheel hash、工作负载、缓存、instrumentation、串行对照
顺序、采样和解释规则。每次 CLI 都是新 Python、独立空 bytecode cache prefix，
并禁止写入 bytecode。每个 episode 在 prequota 启动独立冷 Node，之后仅在同一轮
复用；compile cache 和临时根隔离。磁盘缓存及其他 host 负载不受控；每对交替
base/candidate 顺序。1,500 秒上限包含 fixture 准备、执行、退出与采样编排。
pilot、负例、打包和独立 instrumentation census 分别记账。

流程覆盖普通 hard-lease claim/admission、独立 Git worktree、完整连续 native
initial read、独立验证的 JSON 求和产物 `45`、Todo completion、fresh evidence-linked
Vision/writeback、一次 spend、terminal result/readback，以及 completion/writeback/
spend/terminal 的原参数重放。明确关闭 global sync 和 external sinks；本实验不
覆盖 standard fanout。小状态有一个活动 Todo；大状态增加 100 个带较长文本的
已完成背景 Todo。大状态失败保留为失败，不算成功成本样本。

全部 60 个计划观测在 **1,164.996 秒**内完成，未改变上限：52 个小状态 episode
完成，八个大状态 episode 保留相同终结失败。代码、脚本与冻结 inventory 未变。
小状态四组各有 8/6/6/6 对串行交错样本；大状态各一对，仅验证观察到的兼容性失败。

| 小状态路径 | 对数 | Baseline 秒，median / p95 | Candidate 秒，median / p95 | 配对差的 median，秒 | 探索性 95% percentile 区间 |
| --- | --- | --- | --- | --- | --- |
| Source / Sqlite | 8 | 16.942 / 17.173 | 14.986 / 15.399 | -1.806 | -2.156 to -1.669 |
| Source / File | 6 | 16.751 / 17.036 | 14.725 / 14.917 | -2.086 | -2.159 to -1.875 |
| Wheel / Sqlite | 6 | 17.066 / 22.878 | 15.169 / 19.378 | -1.932 | -5.246 to 2.348 |
| Wheel / File | 6 | 16.689 / 17.454 | 14.801 / 15.195 | -1.917 | -2.491 to -1.553 |

p95 使用 nearest rank；在这个样本量下就是观测最大值，不能作为尾延时上界。
区间来自固定 seed 的 10,000 次配对重采样。两臂 median 的差与配对差的 median
是不同统计量。两臂 median 改善约 11–12%；wheel/SQLite 区间跨过零，保留了两个
方向的尾部波动。这是小状态路径的局部证据，不是通用速度门槛或长程收益。

| 小状态路径 | Target 加已等待 descendants CPU 秒，median（base → candidate） | 每次调用 target/waited RSS 最大值 MiB，median（base → candidate） | 目标 broker CPU 秒，median（base → candidate） | Broker 结束时 RSS MiB，median（base → candidate） |
| --- | --- | --- | --- | --- |
| Source / Sqlite | 10.536 → 9.190 | 126.60 → 90.02 | 0.767 → 0.775 | 126.59 → 125.28 |
| Source / File | 10.507 → 9.101 | 127.77 → 87.15 | 0.716 → 0.702 | 126.77 → 126.70 |
| Wheel / Sqlite | 10.782 → 9.289 | 134.83 → 91.51 | 0.799 → 0.797 | 125.77 → 126.91 |
| Wheel / File | 10.606 → 9.348 | 132.01 → 88.21 | 0.729 → 0.723 | 127.97 → 127.77 |

RSS 是各次 CLI 调用峰值的最大值，不是同时存活进程树的总峰值。Broker counter
不含退出阶段，使用校准后的 Darwin timebase。默认 SQLite 的 target/waited CPU
下降约 13–14%；broker CPU/RSS 没有一致改善。Source/SQLite prequota median
从 3.425 降到 1.468 秒；guard 为 1.845 → 1.863 秒，成为候选最重阶段。
Wheel/SQLite prequota 为 3.463 → 1.581 秒，guard 为 1.844 → 2.033 秒。
降耗主要集中在新 Python 的 prequota loading，不能归因为 authority 执行加速。

各组 setup median 为 2.15–2.29 秒，retirement 为 0.36–0.40 秒。包含 setup、
退出和 driver 开销后，source/SQLite median 为 20.238 → 18.146 秒，wheel/SQLite
为 20.393 → 19.793 秒。两个 wheel 独立构建/安装用时 43.881 秒。准备、验证、恢复
与 census 有重叠，不能相加。准备阶段作者成本、完整 native/thread/进程树 I/O、
model token、model/人工成本及长程结果仍未完整测量。内核观察到的 broker 磁盘写入
保持约每轮 SQLite 5.85 MiB、File 220 KiB；这不是全部文件系统流量。

60 个观测的产物字节、原绑定、完整结算义务、一次 writeback 和一次 debit 一致。
小状态必读 native initial body 为 23,498–24,442 bytes；`bytes / 4` 仅是粗 token
proxy，不是 tokenizer 结果或 model retention 证明。候选多出的 source 85 / wheel
165 bytes 来自路径/reference 展示；规范化后的结算义务逐条一致，没有删减。

101 个 Todo 时，普通 completion 会把原展示位置靠前的选中 Todo 和 90 个背景
Todo 自动归档。Validation、writeback、一次 spend 已提交，后续原 Turn 的
`todo complete --no-follow-up` 却因 `todo_archived` 拒绝。四组 source/wheel ×
File/SQLite 对照在两臂均复现。失败轮没有计入验收成功，也不用于宣称完整大状态
加速。既有 `todo_terminal_lifecycle.ts`、`todo_archive_selection.ts` owner 需要协调
自动归档和原 receipt 的终结。相关 #5677 已负责 freshness/terminal recovery；其
公开的 0/1/4 history 矩阵没有验证本次自动归档分支。扩展既有 owner 时，保留 fresh
archived-Todo、变更 actor/lease/evidence 和重复 effect 的拒绝。

独立 census 在八个 source/wheel × File/SQLite 变体中保留 invalid Vision/workspace
拒绝。每个变体另有四个失败检查：missing Node admission、missing provider、capture
digest 不匹配，以及 released-claim completion；没有提交 writeback/debit。
Prequota 的 soft failure 继续进入 quota，不授予 admission。负例检查用时 69.559 秒，
instrumented census 用时 90.358 秒，两阶段有重叠。采样/census 的所有 broker 都已
观察到 shutdown 与 OS exit；pilot 初次 wrong-source retirement/pending wait 的失败
保留在历史中，随后在其独立 namespace 完成退出回读。

剔除 census 额外两个拒绝后，各变体完整 episode 的 RPC 均为 271 → 270，spend
单独为 83 → 82。实际 receive 流量为 999,060–1,004,527 → 966,984–972,825 bytes；
send 为 1,025,077–1,037,023 → 1,026,956–1,040,098 bytes，增加来自候选更长的路径。
Parent read-open 为 13,532–13,561 → 11,700–11,728，write-open 为 285 → 284。
带 instrumentation 的 prequota module graph 为 source 1,404 → 480、wheel
1,403 → 479，包含 instrumentation 自身 import，不是未插桩的 loading 预算。
Census 的冷 Node/readiness 为 0.217–0.249 秒，version probe 为 0.026–0.032 秒。
单次插桩用时不能证明重复延时收益；socket/open 计数也未覆盖全部 native/broker I/O。

**基于证据的下一步：**把归档分支交给既有 terminal/freshness owner 和 #5677
集成验证，再验证完整大状态生命周期。保留原 operation/read/receipt identity 与
所有拒绝边界；不为让这个工作负载通过而修改归档策略。随后 characterize 剩余
`quota should-run` parser/loading 与共享 projection caller，再选择性能改动。
小状态数据支持继续对 #8240/#8243 的精确 head 做 review，不授予 merge 或已安装
host 采纳资格。I3 至少保留一个 minor release；M1/#8011、完整 R0/V10/R1、
D2/D5/D6/D3、context/model/split-install、真实 adoption 和完整成本验收仍开放。
没有新增 budget、preset、provider 或 authority。

Source 重放时，拉取两个精确 PR head，仅将生产 `loopx/` 差分应用到隔离的
baseline archive；记录的 merge base 分别为
`79665b97a0a453380da02e15b1b62eee895147df`、
`97cc1e0c40e766c73193b8077526a7f565dc561b`。使用 `uv sync --extra test` 准备
console，并用 `uv build --wheel` 分别构建单体 wheel；构建前通过既有 owner 验证
Chat bundle。复用 `tests/control_plane/test_quota_settlement_cli.py` 的 source fixture：
在 `_initialize_fixture_authority` 前完整编写 synthetic Goal/背景 Todo，选择 File
或 SQLite 与 `hard_lease`，通过普通 claim 准备执行。Todo 和 Goal boundary 同时
声明产物 write scope，复用 Git fixture helper 并创建独立 worktree。

以下重放序列的每条命令都必须指定该 fixture 的 registry/runtime/worktree；guard
提供完整 read/recovery 命令。保持同一 Goal、Agent、Todo、Turn 和 claim operation：

```text
loopx heartbeat-prompt --thin --codex-app --goal-id <fixture-goal> --agent-id <fixture-agent>
loopx heartbeat-prequota -g <fixture-goal> -a <fixture-agent>
loopx todo claim --goal-id <fixture-goal> --todo-id <fixture-todo> --agent-id <fixture-agent> --claimed-by <fixture-agent> --claim-operation-id <fixture-claim>
loopx quota should-run --goal-id <fixture-goal> --agent-id <fixture-agent> --todo-id <fixture-todo> --turn-instance-id <fixture-turn> --codex-app --context-delivery on
# Execute first_read_command and every emitted contiguous next_command.
# Produce {"sum_0_through_9":45}; verify it independently before completion.
loopx todo complete --goal-id <fixture-goal> --agent-id <fixture-agent> --todo-id <fixture-todo> --turn-instance-id <fixture-turn> --claim-operation-id <fixture-claim> --evidence artifact:verified-sum
loopx refresh-state --goal-id <fixture-goal> --agent-id <fixture-agent> --todo-id <fixture-todo> --turn-instance-id <fixture-turn> --classification synthetic_cli_cost_artifact --delivery-boundary semantic_closeout --delivery-batch-scale single_surface --delivery-outcome outcome_progress --agent-vision-json <fixture-vision> --delivery-workspace-path <fixture-worktree> --no-global-sync --suppress-external-sinks
loopx quota spend-slot --goal-id <fixture-goal> --agent-id <fixture-agent> --todo-id <fixture-todo> --turn-instance-id <fixture-turn> --slots 1 --source heartbeat --execute
loopx todo complete --goal-id <fixture-goal> --agent-id <fixture-agent> --todo-id <fixture-todo> --turn-instance-id <fixture-turn> --claim-operation-id <fixture-claim> --evidence artifact:verified-sum --no-follow-up
loopx todo list --goal-id <fixture-goal> --todo-id <fixture-todo> --include-archived
```

Admission/writeback/spend 声明该 fixture 已观察到的 shell、filesystem-write、network
和 evidence-poll capability；生成的 reader 命令使用实际安装的 console。用不变参数
重放 completion、writeback、spend、terminal。Missing Node 使用隔离 PATH，missing
provider 使用空的 disposable runtime，stale source 使用不匹配的 capture digest，
stale claim 使用普通 release 后的 lease。它们必须在 writeback/debit 前拒绝；invalid
Vision/workspace 同样保留拒绝。临时测量 harness 和 raw capture 保持私有；这个 recipe
描述重复的 workload/oracle，不是分发的 benchmark runner 或公共复现 receipt。

### 2026-11-07 — 原 Turn 终结在 Todo 自动归档后仍可完成

#8257 的完整 CLI 比较在 101 个合成 Todo 下暴露了生命周期故障。基于
`4a33eb4b5` 的新源码 console 复现在 File 和默认 SQLite 上均保留同一
`todo_archived`：普通完成、持久写回和一次额度扣账已提交。冻结比较保持原样，
这些失败 episode 不计入验收通过的延迟样本。

修复提案仍由 `todo_terminal_lifecycle.ts` 拥有。在仅限 active 的拒绝之前，
复用已有同 Turn fence 与精确普通 completion receipt 校验，验证已归档 Todo
的终结资格。一个文件内 helper 统一 active 和已归档路径的父 receipt 检查。
原 claim/lease、任务 digest、validation 声明与 receipt、provider CAS 校验
继续生效；归档选择与保留阈值保持原样。允许的只是原 completion 的终结阶段，
不会重新开放已归档工作。

真实 File/SQLite console fixture 现在可以完成大状态整轮，并回放 completion、
写回、扣账和 terminal closeout：只有一次扣账，独立验证的 JSON 结果保持原样。
持久 CLI 回归使用真实 validator 和普通 hard-lease claim，拒绝过早结算、
无关归档工作、其他 actor、claim 或 Turn，以及变更后的 completion intent。
终结保留原已通过的 validation receipt、首次完成时间和归档状态，不重复执行
validator。既有 provider conformance 继续检查 source/digest、validation、
lease 和 replay；隔离真实 PostgreSQL suite 单独验证其共享 owner。

作者提案还集成了 main `abf144202`（#8161）：显式 Goal 范围的检查不约束局部
Todo 完成。归档回归在局部 validator 之外加入未通过的整体 Goal 承诺检查：
普通完成和终结仍保留原 receipt 与一次扣账，不重复执行 Goal checker，整体
检查继续失败。这是作者阶段的集成覆盖，不能替代独立审阅或审阅修复后的
wheel 资格验证。

#8264 `12627eff8` 的独立审阅发现：只改 `--evidence` 就能替换已归档 Todo
接受的产物，却仍返回旧 validation receipt。File/SQLite × 有无整体 Goal 检查
的四个 fixture 均在该 head 复现失败。修订集成 target main `10f66ec5e`，在归档
终结前拒绝不同的显式 evidence；新归档 closeout receipt 同时保存已接受证据，
让变更后的重试不能通过 receipt recovery 绕过检查。带有效 completion note 时，
省略或空 evidence 保留原值；CLI 原有的完成说明要求继续适用。
历史 fingerprint 和旧 receipt 继续兼容，active completion 行为保持原样。
回归在 terminal replay 前后检查 canonical evidence、原 validation 和完成时间，
以及只执行一次的 validation/writeback/debit。此前被审阅的 head 仍未获验收。
修订 `7ec8d19b9` 已获得独立
[APPROVE 审阅](https://github.com/loopx-project/loopx/pull/8264#pullrequestreview-5811807012)，
#8264 已以 `172d80d63` 合入。

基于 target main `32167518b` 的验证现在覆盖源码 console 和新构建、隔离安装的
单体 wheel。构建 wheel 前，已通过既有 owner 构建并验证 Chat bundle。
File/SQLite × source/wheel 四组 episode 均包含 101 个合成 Todo，使用真实
console、普通 hard-lease claim，以及独立验证的 JSON 产物。在一次扣账提交后，
fixture 只停止自己的 Effect 进程；新的 console 进程通过重启后的 runtime
恢复原 terminal 结果。Canonical 回读保留已接受证据、首次完成时间和归档状态。
Completion、写回、扣账和 terminal 重试仍保留四步已提交 receipt 和一次扣账；
terminal commit 前和 replay 时都拒绝替换证据。最后一次原 Turn guard 不再声明
可执行工作或执行义务。Wheel 的 console/import provenance 与源码 fixture
准备环境分开核对。

| 分发 / provider | 完整 episode | 初始 guard | 结果 |
| --- | ---: | ---: | --- |
| Source / File | 22.682 s | 2.148 s | 恢复原结果；一次扣账 |
| Source / SQLite | 22.220 s | 2.219 s | 恢复原结果；一次扣账 |
| Wheel / File | 20.929 s | 2.020 s | 恢复原结果；一次扣账 |
| Wheel / SQLite | 24.205 s | 2.787 s | 恢复原结果；一次扣账 |

这些是 macOS、Python 3.13.5、Node 26.0.0 下的单次正确性 episode，包含负例、
回放检查和受控进程停止。它们没有重跑冻结的配对成本研究，也不证明延迟收益。
原八组大状态失败与此前的不确定性继续保留为历史证据。三个隔离 parser 输入
还确认 source/wheel 接受 writer 输出的 archive pointer，同时继续拒绝意外正文
和错配 region marker。旧 reader 的 pointer 失败继续保留；这个比较不更换
grammar owner，也不升级 installed host。

本轮验证修复后的大状态 source/wheel 终结与受控 Effect 重启边界。剩余 guard
加载／共享 projection 成本 profile 和完整比较继续由既有工作承接。
突发中断与模型宿主 V4 recovery、split installation、完整 R0/V10/R1、
M1/#8011、D2/D5/D6/D3、context/model、实际 adoption 与完整成本收益仍开放。
I3 继续保留至少一个 minor release。

### 2026-11-07 — Guard 加载与已结算回放诊断

`6c10e61ad` 的源码 console 诊断复用上述真实 101-Todo hard-lease episode，
隔离 File/SQLite authority 与自有 Effect 进程。新增五个 episode 均在受控重启
后保留已验收证据、首次完成时间、四份已提交 receipt 和一次 debit；证据替换与
已结算 Turn 的再次执行仍被拒绝。只对 guard 和不可变上下文读取做剖析。这些
包含 profiler 开销的单次诊断观测，不是配对性能运行。

| 字节码条件 / provider | 插桩 guard | import 累计 | 跟踪的 runtime 请求 | 请求耗时之和 | 已加载 LoopX 模块 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 禁用字节码 / SQLite | 3.012 秒 | 2.281 秒 | 89 | 0.370 秒 | 503 |
| 禁用字节码 / File | 1.972 秒 | 1.255 秒 | 89 | 0.344 秒 | 503 |
| 正常继承字节码 / SQLite | 1.584 秒 | 0.845 秒 | 89 | 0.394 秒 | 503 |
| 正常继承字节码 / File | 1.465 秒 | 0.772 秒 | 89 | 0.342 秒 | 503 |

禁用字节码时，SQLite / File 的源码编译分别占 0.634 / 0.555 秒；两次正常
字节码观测均未调用源码编译。import、loader 读取与编译时间相互嵌套，不能
相加。字节码条件解释了部分启动成本，加载图仍相同；这不能修正或覆盖冻结
比较的失败。第五个正常字节码 SQLite episode 中，guard 加载 503 个模块并
跟踪到 89 次请求，不可变上下文 reader 加载 68 个模块并跟踪到一次请求：插桩
耗时分别为 2.140 / 0.246 秒，请求耗时之和分别为 0.389 / 0.067 秒。这是在
刻画不同的必要操作，不代表两条路径可以互换。

runtime 请求耗时包含 Python transport、共享 runtime 与 provider 工作。它只
是这些已跟踪请求中 provider 贡献的上界，不是单独的数据库计时或完整 service
CPU。四次 guard 观测的 source snapshot 约为 0.048–0.050 秒。因此下一步
成本调查优先考察 decision/status 加载图，而不是新 provider 或 fingerprint
优化。仅移动 `quota_context` import 无法省去后续 status collector 仍需要的
工作；quota 已省略 presentation projections，不可变上下文 reader 也已有
选择性加载路径。

真实 File/SQLite Monitor episode 还验证了独立 sibling 到期时，已结算与
关闭后回放仍保留原身份。每个原 Turn 保留一次 poll、零 debit，不调度 sibling。
通过公开 `configure-goal --quota-compute 0` 入口，暂停仍优先并返回
`quota_skip`。初始 probe 修改的是旧 `status` 字段，未修改 typed activation
或 quota owner；暂停预期失败属于夹具错误，与修正后的检查分开保留。

| Provider / 回放阶段 | 插桩回放 | import 累计 | 跟踪的 runtime 请求 | 请求耗时之和 | 已加载 LoopX 模块 |
| --- | ---: | ---: | ---: | ---: | ---: |
| SQLite / 已结算 | 1.135 秒 | 0.666 秒 | 68 | 0.150 秒 | 486 |
| SQLite / 已关闭 | 1.149 秒 | 0.726 秒 | 62 | 0.157 秒 | 486 |
| File / 已结算 | 1.113 秒 | 0.713 秒 | 68 | 0.143 秒 | 486 |
| File / 已关闭 | 1.128 秒 | 0.691 秒 | 62 | 0.149 秒 | 486 |

下一步仍在既有 quota/status 与 typed replay owner 内推进：先确定每类 decision
实际需要的 live facts 和 import，再保持公开 facade 等价性，去除无用加载或
重复共享请求。仅凭 receipt 提前返回尚未取得资格：当前暂停、scoped gates、
独立 pending intents、actor/lease/source 检查，以及归档或关闭后的 lineage
必须保留。实现后仍需相同的真实 source/wheel 生命周期，以及不插桩、匹配条件
的默认 SQLite 比较；影响到其他受支持后端时，其正确性、恢复与必需的真实
PostgreSQL 资格继续适用。本诊断没有修改生产行为、preset、budget、provider、
authority 或 installed host。既有验收、采纳和完整成本缺口继续开放。

### 2026-11-07 — Guard 成本候选取舍与 context transport 组合

隔离的源码 console 反事实在函数被调用时才加载三个 Monitor adapter 依赖。
101-Todo SQLite 生命周期仍保留原 artifact、首次完成时间、四份 receipt 与
一次 debit。guard 从 503 个模块降至 502 个，跟踪的 runtime 调用仍为 89；
只有 `todos.external_wait_contract` 消失。Monitor 历史查找与 quota accounting
admission 在 guard 路径实际执行，其依赖仍然必要。因此不选择这项 import
延迟作为下一成本缩减切片；临时 wrapper 不是生产实现或兼容性资格。

既有 context transport 候选与加载缩减分别验证：
[#8286](https://github.com/loopx-project/loopx/pull/8286) 的 `efce6cb9c` 跳过
inactive Anti-Entropy validation；
[#8290](https://github.com/loopx-project/loopx/pull/8290) 的 `9537687be` 从
status-only context validation 中省略 governing source。以 runtime base
`32167518b` 为基线的临时组合 overlay，已跑四组 File/SQLite × base/overlay
完整源码 console episode，每组含 101 Todos。已验收证据、首次完成时间、
受控 Effect 重启、四份已提交 receipt 和一次 debit 均保留；证据替换与已结算
Turn 的再次执行仍被拒绝。组合源码另通过 91 个聚焦 Python 用例与 20 个
TypeScript 用例，零 skip。验证后恢复临时变更的路径，没有新增竞争实现或合入。

| Provider | guard 跟踪调用，base → overlay | JSON 参数字节，base → overlay | 加载模块，base → overlay |
| --- | ---: | ---: | ---: |
| SQLite | 89 → 87 | 1,904,862 → 1,772,448（−6.95%） | 503 → 503 |
| File | 89 → 87 | 1,903,569 → 1,771,587（−6.93%） | 503 → 503 |

这些单次插桩观测统计传入共享 runtime 的 UTF-8 JSON 参数，不含 transport
header、retry、response、startup 或 service CPU；不是完整 wire 字节或延迟
比较。SQLite base 的两个 status-only validation 输入共 134,868 字节，
overlay 唯一保留的输入为 2,045 字节。不可变 context 与 settlement owner
继续负责原边界。这独立验证了有界 transport delta，但不证明候选 merge、组合
wheel、installed-host adoption 或完整任务收益。冻结成本研究没有变更。

剩余最大的 guard 参数生产者是 `todo.context.page`（九次调用共 789,198
字节）、`todo.quota_planning.project`（390,361 字节）和
`goal.vision_wait.project`（185,559 字节）；SQLite base 与 overlay 数值
相同。下一有界调查先在这些既有 typed projection owner 中验证 canonical
Todo source 的复用，再实现一个完整 source-read transaction。必须保留
source/provider revision、actor/lease fence、完整 exact read、archive/replay
lineage、pause/gates 与 pending intents。新增更多 leaf RPC 或有损裁剪 source
字段不能取得 transaction cutover 资格。#8123 的既有 Goal-route 工作与其未
解决的性能 hold 保持独立。新实现仍需完整 source/wheel 正确性，以及不插桩、
匹配条件的默认 SQLite 全路径比较；既有里程碑、采纳与完整成本缺口继续开放。

### 2026-11-07 — 在受管请求内复用完整 Todo 展示来源

既有 `todo.context.page` owner 可从同一完整输入来源组合 summary、planning 和
保留 terminal 的展示视图。Quota preparation 调用方显式请求 Agent 来源的三种
视图，并把保留 terminal 的 rows 传给 frontier 消费方。User 来源及通用展示调用方
保留自己请求的单一视图。来源选择和每种展示规则保留原 owner。Orchestration
fallback 复用已选 summary 的独立副本，避免再次请求相同展示。

已经固定 runtime revision 的逻辑请求内，以完整 source JSON 和请求选项约束
传输 memo。来源事实变化和后续请求重新验证；可变调用方获得独立解码值。较大的
显式组合输入保留原逐视图传输，不完整组合响应在进入 memo 前失败。Batch 输入
阈值不能预测响应大小，也不保证 socket 内联传输；显式多视图的合计输出超过
socket 限制时，仍可能使用既有私有响应 snapshot。

初始提案的独立评审发现，通用传输把单视图调用扩大为三种视图。8,000 条记录的
单次 delivery-response 调用最终输出相同，却把原约 3.73 MB 的单视图 socket
响应扩大成约 11.18 MB 的组合响应并写盘。保留这次性能失败。修复后的调用方
显式组合移除了这种隐式扩大；单视图对照和完整受管 source/wheel 验证按本次提案
revision 记录。

初始提案对照 `main@32167518b3`，源码与隔离安装 wheel 的六组 101-Todo File/SQLite
受管 CLI 生命周期通过，保留已验证产物、原完成时间、四种 settlement receipt、
一次 debit、Effect 重启恢复及 evidence 替换拒绝。刻意丢弃 write-scope 响应字段的
mutation 在独立公开 guard scope 检查失败。冻结展示 fixture 和聚焦来源隔离用例
同时覆盖既有三种视图。一次带 profiler 的 SQLite guard 观察到 JSON 参数约
1.900 MB → 1.556 MB，调用 89 → 87，已加载模块仍为 503。这些计数仅用于诊断，
不验收完整 wire 成本、延迟或长程收益。


修复后的单次调用对照在 `main@dee549663`、已评审的缺陷 head 和修复源码上
使用相同合成来源，三者最终 delivery response 相同。修复的请求参数和响应字节
与 main 完全一致；两种记录规模的非空响应 snapshot 字节都为零。每个版本五次
热调用的探索性中位数如下，包含 display 和 delivery-response 两种 projection：

| Rows | Main 秒 | 缺陷提案秒 | 修复秒 | 缺陷响应 snapshot 字节 | 修复 snapshot 字节 |
| --- | --- | --- | --- | --- | --- |
| 8,000 | 0.159 | 0.636 | 0.161 | 9,480,165 | 0 |
| 15,000 | 0.284 | 1.344 | 0.286 | 17,775,165 | 0 |

此对照复现并消除了单视图性能回归，不能验收完整 CLI、daemon CPU/内存、完整
边际成本或长程收益。


修复后的源码及隔离 wheel 通过六组 101-Todo File/SQLite 受管 CLI 生命周期，
保留已验证产物、原完成时间、四种 receipt、一次 debit、重启恢复与拒绝／重放。
递归丢弃响应中的 write scope 的 mutation 在独立公开 guard oracle 失败。隔离
PostgreSQL 16.15 的 390 个 store 和 11 个 service 用例通过，零 skipped。

三组交错、无 profiler 的默认 SQLite 完整 CLI 对照中，main 墙钟中位数为
12.24 秒，修复为 12.71 秒；两次候选更慢，范围重叠。独立 profiled guard 的参数
字节从 1,899,603 降到 1,546,162，调用从 90 降到 87，模块仍为 503。这些观察
未证明完整路径性能收益。Daemon CPU/内存、总 wire/file 写入、完整边际成本及
采纳仍未验收；完整 provider-first 事务继续作为下一验收边界。

这是可独立回滚的展示传输增量，尚非完整 provider-first Todo projection
transaction。Provider 读取、revision、lease/actor fence 和状态写入保留原 owner。
后续 canonical-source 事务、匹配的完整成本对照及受影响后端验收仍开放。I3 至少
保留一个 minor release；M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3、context/model/
split-install、adoption 和所有冻结失败继续保留。

#### 下一提案之前的 provider-first 事务要求

以下保留展示增量 revision 时的前置缺口。下一 checkpoint 记录 canonical 事务
提案及其测量限制；不会把先前观察改写成历史通过。

下一实现应组合既有经过 provider 准入的 `canonicalTodoCollection` 和 projection
owner。Native snapshot owner 把 Goal、store incarnation、provider revision、cursor
及 query 绑定到完整 rows、leases、read-model 验证和 acceptance guards。Continuation
只是同一次读取中的位置，不是可复用的 current-head handle；snapshot 变化后应重新
完整读取。展示保留来源不会获取新的 authority。

`quota_selection.py` 仍在 encoded selection rows 和 source-row facts 之外发送
完整 summary source。请求还包含时钟、actor/profile、capabilities、closure evidence、
route/handoff facts 和 resume 输入。合并传输之前，先把 canonical row materialization
放入既有 typed owner。严格 integer、boolean 或文本区别仍可观测时，保留
Python-value codec。Provider rows 不能替代独立 caller evidence 或 pending intent；
all-done 计数本身无法重建 closure declaration。

`goal_vision_wait` 还独立消费完整 lineage、scoped blockers、acceptance gaps 和
resume facts。合并事务应保留每个 causal obligation，包括两个 obligation 共用
witness 的情况。完整 note、write scopes、archive/settled identity 和来源 provenance
仍属于 parity oracle。

真实 PostgreSQL 集成用例把既有 native snapshot read 接到三种展示视图，检查
provider/source 数据不变，在独立 commit 后观察新的 provider revision，并拒绝旧
continuation。它验证的是组合前置条件；完整 guard transaction、PostgreSQL
source/wheel host journey 和完整成本收益仍开放。

### 2026-11-07 — Canonical Todo 事务提案与已观测生命周期成本

提案在绑定 actor/profile/clock 输入后，由既有 TypeScript owner materialize
canonical selection 与 resume rows。`todo.read` 组合显式请求的 Agent summary、
planning、保留 terminal 的视图，以及 User summary、quota selection 和既有
Vision-wait basis。同一次 quota 请求中，匹配的 native reference 共享一次完整
provider read。完整字段 digest、store incarnation、provider revision、cursor
和原 query/selection replay recipe 约束来源。后续请求重新读取；不缓存 provider
authority。

实际受管 CLI query 现在通过 quota preparation 保留来源 recipe。此前 status
虽保留 ephemeral origin，完整 query response 却丢失它。Query 与 selection 路径
现在都把完整 provenance 传入同一 typed transaction。Provider outage 保留原
诊断 code/payload，stale reference 在 projection 前拒绝。Reference metadata
不能覆盖 provider 原字段、Python scalar facts 或 owner 声明的 Todo source lane。
独立 caller metadata 保持完整；改写 provider row 的调用方使用完整 supplied-source
codec。严格 integer/float/bool、list/tuple 区别、旧 fnmatch facts、lane/ordinal
优先级及远期微秒时钟均保留可观察语义。不通过 Todo id 查找补回丢失的 provenance。

既有 Vision-wait owner 保留 causal obligation 与共用 witness。复用 prepared
basis 必须匹配 actor、完整 terminal rows 及逻辑 request identity；独立 lineage
调用方保留原路径。Standalone display 仍只请求一种视图。完整 note、evidence、
write scope、archive/replay identity、独立 closure/pending intent 均属于 parity
边界。既有 source-lane 常量导出给 native provenance 校验复用，没有新增 lane
词汇、持久状态或 authority。

生产代码 `66ff8bf48` 对照 `main@e2371c476`，通过四组 101-Todo 源码／隔离 wheel
× File／默认 SQLite CLI 生命周期。独立 verifier 验收 sum=45；原完成时间、四份
receipt、一次 debit、Effect 重启、无效 Vision/workspace 拒绝、证据替换拒绝与已
结算 replay 均保留。Wheel 的受影响源码与 checkout 一致。最终聚焦用例通过
132 个 Python 和 86 个 TypeScript；较广真实 CLI suite 在最后诊断／lane 修正
之前通过 244 个用例，这些最后修正由聚焦 suite 覆盖。真实 disposable PostgreSQL
16.15 通过 391 个 store 和 11 个 service 用例，零 skip；包含两种 role 的 native
quota 组合、一次完整 provider load、数据不变与 stale-source 拒绝。这不验收
PostgreSQL 源码／wheel host journey。

五组交错、无 profiler 的源码 CLI 对照使用相同 101-Todo fixture 与默认 SQLite。
每组执行 provenance、bootstrap、claim、guard、必需 context read、产物验证、
completion、反例、writeback、spend、restart 和 terminal／refusal replay。Fixture
setup 与最终 teardown 单独报告，两边同样继承正常 bytecode 条件。三组候选更慢，
墙钟范围重叠：

| 观察，未说明时为中位数 | Main | 提案 |
| --- | ---: | ---: |
| 生命周期墙钟（范围） | 14.093 秒（13.765–14.933） | 14.145 秒（13.461–15.190） |
| Fixture setup／最终 teardown | 2.403／0.062 秒 | 2.338／0.062 秒 |
| CLI child CPU＋已观测 daemon CPU（历史记录，单位换算无效） | 5.511 秒 | 5.757 秒 |
| Daemon 边界 physical footprint | 140,787,912 B | 146,456,920 B |
| CLI 最大 resident size | 70,746,112 B | 70,221,824 B |
| 已观测 daemon disk／logical writes | 9,048,064／14,090,240 B | 10,227,712／15,646,720 B |
| CLI output／必需 context 字节 | 177,595／21,168 B | 179,482／21,257 B |

上表 CPU 使用了错误的 Mach 时钟换算，不能作为 CPU 证据。三个独立 CPU busy
子进程校准确认，该 macOS arm64 runtime 的 daemon usage 单位为 Mach tick，
须先乘实测 timebase `125/3`，再把纳秒换成秒。原始观察保持不变。修正五组历史
对照后，包含 setup daemon 工作的 combined observed CPU 为 7.071→7.469 秒；
再减各自初始 daemon baseline，CLI episode 为 6.706→7.107 秒，相应 daemon
disk／logical writes 为 8,892,416／13,783,040→10,072,064／15,355,904 字节。
修正后的观察仍未证明完整路径收益。

Daemon CPU／写入是 CLI 边界处累计 process 观察，不含最终退出工作。CLI CPU
不含未采样的 provenance process 和 harness 工作。CLI RSS 与 daemon footprint
分别观测，不是连续全系统峰值。这些边界不测量完整 model cost、人工注意力或
installed-host／长程收益。

另一组独立插桩对照观察到以下通信变化；其耗时不进入墙钟比较：

| 诊断 | Main | 提案 |
| --- | ---: | ---: |
| Guard RPC／JSON 参数字节 | 90／1,903,233 | 84／837,940 |
| 完整 episode socket 发送／接收字节 | 3,537,350／6,380,604 | 1,948,250／6,388,112 |
| Guard 加载 LoopX 模块 | 503 | 503 |

Guard 两种 role 的来源均使用 native transaction。请求字节和调用减少证明传输
路径已采纳，而接收字节、已观测写入与 CPU 没有一起改善。因此完整路径提速或
完整边际成本收益**未取得资格**。后续决策保留原事务验收：独立评审，并归因剩余
生命周期成本后再提出优化。负面结果不支持另做 leaf-RPC migration 或新增 provider。

提案 rebase 到 `main@e58ba30bc`。最后 scope-fit 检查发现 read transport 的
provenance binding 超过 decision-function budget，已把完整来源绑定和 selected-row
evaluation 恢复提取为局部 helper；24 个聚焦 parity 用例和 maintainability ratchet
通过。成本表仍是提取 helper 前 `66ff8bf48` 的观察，不是最终 exact head 的成本
资格。TypeScript typecheck 与 Ruff 通过。Mypy 保留 main 已有的
`decision_capture.py` 缺少返回类型错误；标准 premerge 也保留 main 对未修改
benchmark 文档的 peer-boundary scanner 失败。两者均不是通过的 skip。Exact-head
review、验证／readiness 与 maintainer merge 授权保持独立；没有合入或 installed-host
升级。原 Task 为收益及剩余验收继续开放。I3 至少保留一个 minor release；M1/#8011、
完整 R0/V10/R1、D2/D5/D6/D3、context/model/split-install、adoption/full-cost
缺口及所有冻结失败继续保留。

### 2026-11-08 — 完整 quota 响应传输与校准成本

独立评审在 `e7db1711f` 复现剩余回归：8,000-row quota 组合结果序列化为
13,080,176 字节，产生 13,080,177 字节同步 response-snapshot 写入；main 的独立
视图低于该响应阈值。既有 Todo read adapter 现在对超过共享 8 MiB inline budget、
且处于原 complete-frame budget 内的 quota 结果使用无损 gzip，经既有 result-piece
envelope 返回 encoding 与 decoded extent，base64 分片保留 socket 预算余量。
更大的 read 保留原分页。Python adapter 验证完整 digest／identity、有界 decoded
size、精确 extent 与单一完整 gzip member；未知 encoding、截断或 trailing data
均拒绝。原响应预算值与 server 共用，不重复定义；没有 provider cache、持久状态、
rule owner 或 authority 变更。

相同 8,000-row 诊断现在为 561,291 wire 字节，response snapshot 字节为零。
完整结果与原独立 summary、planning、terminal 视图一致，未知字段和 Unicode
保真。单次诊断耗时不证明提速。12 个 transport 用例及既有 24 个 parity 用例通过；
两个 native File／SQLite 8,000-row quota 用例保留双 role 来源、完整视图及 provider
数据／revision，且不产生 response snapshot。22 个 TypeScript context／quota
用例与 8 个真实 receipt／replan／reentry 用例通过。此前过强的测试 oracle 失败，
因为 display owner 有意展示不同字段；最终比较各自完整独立输出，不修改展示行为。

生产代码 `2c9d6360e` 基于 `main@8a4d4f9da`，通过四条新 101-Todo 源码／隔离
wheel × File／默认 SQLite CLI 生命周期，含 sum=45、原完成时间、四份 receipt、
一次 debit、重启、反例与 replay。最终 wheel 与受影响源文件一致。真实 disposable
PostgreSQL 16.15 再次通过 391 个 store 和 11 个 service 用例，零 skip。
TypeScript typecheck、Ruff 与 maintainability ratchet 通过；这些检查不验收
PostgreSQL 源码／wheel host journey 或 installed-host adoption。

五组新的交错、无 profiler 默认 SQLite 对照使用不变的 101-Todo fixture、完整
反例／replay 生命周期及同样继承的 bytecode 条件，无并发重型测试。CPU 按校准
Mach timebase `125/3` 换算；daemon CPU／写入减初始 setup baseline：

| 观察，未说明时为中位数 | Main | 提案 |
| --- | ---: | ---: |
| 生命周期墙钟（范围） | 13.894 秒（12.686–16.481） | 14.974 秒（14.131–15.344） |
| Fixture setup／最终 teardown | 2.347／0.062 秒 | 2.299／0.062 秒 |
| CLI child＋setup 后已观测 daemon CPU | 7.086 秒 | 7.403 秒 |
| Setup 后已观测 daemon disk／logical writes | 8,884,224／13,795,328 B | 10,076,160／15,130,624 B |
| Daemon 边界 physical footprint | 141,032,832 B | 150,520,728 B |
| CLI 最大 resident size | 71,467,008 B | 71,696,384 B |

四组候选更慢，墙钟范围重叠。大响应写入回归已修复，但完整生命周期收益仍
**未取得资格**。最终 daemon 退出、未采样 provenance／restart helper、harness CPU、
连续系统峰值、模型成本与人工注意力仍不在上述观察中。原事务 Task 为独立 exact-head
评审及剩余 CPU／写入归因保持开放。此前 premerge peer-boundary 与 mypy 失败保留
为验证 hold，直到 exact-final 检查证明其已消除。I3 至少保留一个 minor release；
M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3、context/model/split-install、
adoption/full-cost 缺口及全部冻结失败继续保留。

### 2026-11-08 — 旧格式投影类型与剩余 native read 成本

独立评审发现，组合读取保留了来源优先级，却丢失所选投影类型。当 canonical 与
旧 asset 输入共享同一字典时，即使既有优先级选中旧 asset，对象身份仍使其走
user-summary 路径，旧字符串计数变为 null；只有 `next` 的输入绕过缺少 Todo ID
的拒绝。

生产代码 `4c3ed5d39` 让既有选择器同时返回来源和原投影类型；display／inventory
调用方仍使用同一选中来源，没有新增词汇、持久字段或 authority。
最后静态校验补充了可变长度 source-candidate tuple 注解，执行 bytecode 不变；
该类型失败保留，修正后的 mypy 仅剩 main 既有 annotation hold。13 个新用例以原
独立选择器的完整 summary 为对照，覆盖双 role、共享／复制输入、canonical 优先级
及真实 guard 选择／拒绝；前一 head 六个失败，修复后全过。首版测试错误假定旧计数
为数值，且未绑定 guard；其失败保留，最终 oracle 遵循原字符串值和绑定 Agent／Turn
路径。聚焦 suite 96 个通过。源码与隔离 wheel 独立保留旧计数并拒绝缺少 ID；四条新
101-Todo 源码／wheel × File／SQLite 生命周期保留产物、原完成时间、四份 receipt、
一次 debit、重启、反例及 replay。真实 disposable PostgreSQL 再次通过 store391、
service11，零 skip，服务已停止。

对前五组成本数据归因后，剩余增量指向 guard、writeback、spend 和 settled replay
中的重复 native read。另一组插桩完整生命周期观察到八次新增 native `todo.read`，
每次增加 147,456 个已观测 daemon disk-write 字节，共 1,179,648 字节；插桩耗时不
进入资格比较。对真实 native snapshot owner 的独立探针观察到每个 snapshot 四个
只读 SQLite connection，同样增加 147,456 个 process-counter 字节。交错 identity
read 使用一个只读 connection、36,864 字节，provider revision 和数据库内容 digest
不变。这定位了 read lifecycle 成本，不代表物理设备写入，也不支持删除 identity、
revision、WAL、lease 或 recovery 检查。

Python `invalid-vision` profile 主要是 import，两边调用数相同；其 wall 权重不能
归因成对 CPU 差异，也不支持另做 import migration。下一个既有验收步骤是研究原
SQLite read／connection lifecycle，通过 typed owner 保留新鲜 incarnation 与
revision 校验；provider 编辑先按普通流程准备 Task scope／ownership。前一修正
成本表仍对应 `2c9d6360e`，不是本次兼容性修正，不新增提速声明。原 Task、独立
exact-head 评审、完整路径／完整成本、host／model／adoption 验收继续开放；I3、
M1/#8011、R0/V10/R1、D2/D5/D6/D3、context／split-install 及全部冻结失败保留。

### 2026-11-07：已选 SQLite snapshot 的读取生命周期

既有 `AuthorityStore` 增加可选的 storage-only identity／head 成对读取，由原
canonical snapshot owner 消费。`SqliteAuthorityStore` 仅在 `existingOnly` store
提供该方法；身份和完整已验证 head 使用同一个同步只读事务，不跨调用保留连接、
head 或 provider authority。普通 SQLite、File、PostgreSQL 及未提供可选方法的
custom store 保留原 identity／load 顺序和失败优先级。不新增持久状态、wire 词表、
capability、selector 或 Python 规则 owner。

Provider 选择仍校验已配置 incarnation；snapshot 准入在读取 head 后仍独立重开
pathname。Revision、cursor、完整 read-model digest、stale continuation、WAL、
lease 和 receipt／replay 检查保留。读取 head 后真实替换数据库目录会被拒绝，且
不返回部分行。私有 mutation 取消最终 incarnation 检查后，同一公开 snapshot
oracle 失败。

Runtime commit `61f7c3c33` 通过相关 TypeScript 873 用例与类型检查、四条完整
source／wheel File／SQLite 101-Todo 生命周期，以及真实隔离 PostgreSQL 16.15
store391／service11，零 skip。生命周期保留 sum45、原完成时间、四种回执、一次
debit、重启和 refusal／replay；23 个变更 production 文件与 wheel 逐字节一致。
Native before／head 诊断保持完整响应与数据库 hash 相同，只读连接从四次降到
三次，每个 snapshot 的 observed daemon disk-counter 字节从 147,456 降到
110,592。这些计数不代表物理设备写入，插桩诊断耗时不进入性能资格。

新一轮五组交错、无插桩的默认 SQLite 完整生命周期，对照同一 101-Todo fixture
在 main `8a4d4f9da`、前一版本 `c40d8ec31` 和 runtime `61f7c3c33` 的表现，环境为
CPython3.13.5／Node26.0.0／已合格 SQLite3.53.1。十五条生命周期均保留同一独立
验证成果与原结算。

| 已观测指标，中位数（所示范围） | Main | 前一版本 | 成对读取 |
| --- | --- | --- | --- |
| 生命周期墙钟 | 14.945 秒（12.664–16.597） | 14.924 秒（14.570–19.232） | 14.871 秒（14.412–16.775） |
| setup 后 CLI＋采样 daemon CPU | 7.084 秒 | 7.388 秒 | 7.292 秒 |
| setup 后 daemon disk-counter 字节 | 8,896,512 | 10,076,160 | 8,314,880 |
| setup 后 daemon logical-write counter 字节 | 13,897,728 | 15,618,048 | 12,840,960 |
| Fixture setup／最终 teardown 墙钟 | 2.373／0.060 秒 | 2.387／0.062 秒 | 2.336／0.062 秒 |

五组中候选两次慢于 main、两次慢于前一版本，墙钟范围重叠。已观测写入减少，
但 CPU 中位数仍高于 main，完整收益未合格。CPU 使用已验证 Mach timebase125／3，
扣除每个 daemon 的 setup 基线；未采样最终退出、部分 restart helper、provenance
和 harness CPU。CLI RSS 与 daemon 边界 footprint 分开观察，不代表连续全系统
峰值。对照期间没有重型验证；model cost、人工注意力和 installed-host adoption
仍未测。新结果不替换或重标任何历史 campaign。下一步先独立评审并归因完整 CLI
剩余 CPU 成本，再选择优化，保留新鲜读取栅栏。

标准 premerge 六项直接检查及 19 项选中检查中的 18 项通过；peer hard-cut scanner
在 main 与候选上仍因同一未改动 benchmark README 失败。原 Task 的独立 exact-head
评审、匹配完整路径／完整边际成本及 host／model／adoption 验收继续开放。保留至少
一个 minor release 的 I3、M1／#8011、R0／V10／R1、D2／D5／D6／D3、context／
split-install 及全部历史失败；连接数或验证通过均不证明完整最小化内核目标已完成。

在最新 main `df67371c3` 上的集成 `ce0e41a26` 保持 23 个 PR production-source
hash 不变。重新通过四条完整 source／wheel File／SQLite 生命周期、49 个 TS
用例、类型检查和隔离 PostgreSQL391／11。保存上下文／旧格式 Python 检查为
107 passed、14 skipped；要求显式 native Codex 的用例仍未合格。Main 已修复
原 missing-return annotation，新的 kernel mypy 通过；历史失败保留。标准
premerge 仍通过六项直接及 18／19 项选中检查，既有 peer scanner 失败保留。

集成后的五组新默认 SQLite 无插桩对照保留同一 101-Todo 成果。Main／候选
生命周期墙钟中位数为 14.631／14.633 秒（范围 14.260–16.869／14.122–14.941），
setup 后已观测 CLI＋daemon CPU 为 7.038／7.080 秒，daemon disk-counter 为
8,880,128／8,290,304 字节，logical-write counter 为 14,966,784／14,282,752 字节。
Fixture setup 中位数 2.362／2.346 秒、最终 teardown 0.062／0.058 秒仍分开。
两次候选更慢，范围重叠；上文采样缺口仍适用，写入计数下降不证明完整提速、
边际成本或采纳收益。先补齐剩余成本覆盖，并确认可重复的 CPU 退化，再选后续
实现；不能假定应继续优化读取或 import。

### 完整成本覆盖与当前 main 事务检查点

既有提案在 `23227ee5` 整合 main `0c28f6004`，23 个 PR production-source hash
不变。重新通过 Python59、TypeScript63、类型／mypy、四条完整 source／wheel
File／SQLite 101-Todo 生命周期及隔离 PostgreSQL16.15 store391／service11，
这些运行零 skip。成果、原完成时间、回执、debit、重启和 refusal／replay 保留。
标准 premerge 保留既有 peer scanner 失败（六项直接和18／19项选中通过）。
第一轮 PostgreSQL 因 checkout 缺 `pg` 依赖失败，保留该失败，并单列环境补齐后的
成功验证。

当前集成的五组新默认 SQLite 无插桩对照保持同一 fixture／成果。Main／候选墙钟
中位数为14.255／14.382秒（范围13.971–14.887／13.428–14.684）；setup 后采样
CLI＋daemon CPU 为6.873／7.141秒；已观测 daemon disk counter 为
8,904,704／8,306,688B，logical-write counter 为14,491,648／13,443,072B。
Setup2.314／2.187秒、teardown0.061／0.057秒分开。三次候选更慢，范围重叠；
写入计数减少仍不证明整体或完整边际成本收益。

另一次被动退出观测扩展了记账范围。Python self／waited-child usage 覆盖
harness、provenance／verifier 和 CLI 子进程；最初归属 harness 的 daemon 已计入
waited-child，只额外加上后来的 orphan daemon。以独立的 production locator PID
要求两条 daemon 退出记录，移除任一记录会触发 coverage oracle 失败。Main 的两个
daemon 在最后采样后又消耗0.147／0.045秒 CPU，候选为0.149／0.046秒；尾部接近，
不能解释采样 CPU 差异。退出回调覆盖受控 shutdown／cache-retirement 路径；观测
写出、回调后的 native 工作、orphan native 子进程、物理设备写入和连续系统峰值
仍未覆盖。该插桩对照仅用于覆盖诊断，不是延迟资格，也不替换历史指标。

另一组插桩完整对照记录每个 RPC 的已校准进程 CPU：`todo.read` 从18增至26次，
额外消耗0.172秒采样 Node CPU，下一步应研究原 Todo／provider 读取事务。插桩耗时
不认证性能，也不支持删除 current-source、incarnation、revision、WAL、lease 或
recovery 校验。先表征同一准入请求内完整复用的机会，再实施；这些观察不支持盲目
迁移 import 或跨请求缓存权威。原 Task／vision、独立 exact-head 评审、
I3／minor-release、M1／#8011、R0／V10／R1、D2／D5／D6／D3、context／model／
split-install、PostgreSQL host／wheel、完整成本／采纳缺口和全部冻结失败保留。

### Refresh 摘要事务检查点

在整合 main `87a153d4d` 的 runtime `68fae1d0`，完整生命周期调用追踪定位到同一
refresh 资格检查分别执行两次原生 User／Agent 摘要请求。现有 Python 适配器把两份
请求交给原 TypeScript Todo 事务；该 owner 逐项校验来源和独立输入，仅在引用匹配
时共享完整 provider 物化。独立 metadata、clock／actor 绑定，以及 source／identity／
revision／WAL、lease、receipt／recovery 检查保留；supplied source 或已结束请求
scope 的引用沿用 standalone 路径。跨请求不缓存权威。既有 CLI／读回直接使用此
内部改善，不新增产品操作或配置。

同一 101-Todo 的 source／wheel File／SQLite 生命周期保持 sum45、原完成时间、
四份回执、一次 debit、重启、拒绝／重放和结算读回。递归移除 scope 的响应变异在
真实 SQLite CLI 的独立 scope oracle 处失败；隔离 daemon 已停止。插桩生命周期的
`todo.read` 从26到24，每个 refresh 摘要对从两次 RPC 到一次；这是机制诊断，
不能作为延迟结果。当前113 Python／63 TypeScript、类型／mypy和真实隔离
PostgreSQL16.15 store391／service11通过，合格运行零 skip，两个 server 均停止。
24个 PR 生产文件与隔离 wheel 一致。漏传必需参数的首次失败、缺少 service 环境
变量的首次 skip 均保留为失败／未合格尝试。标准 premerge通过六项直接及18／19
项选中检查，保留未改动的 peer hard-cut scanner 失败。公开边界子检查通过；
独立完整 local check 仍暴露其它 registered Goal 的 authority 错误。

后续 main 整合前，以 main `b78246ff7`、旧提案 `94c99e28a` 和 runtime `fc032cbf5`
各五条相同默认 SQLite source 生命周期作交错无插桩比较。每组 fixture driver 使用
该组精确 CLI 解释器；15条 prequota PID-set检查均保留同一已预热 daemon。更早的
并行验证负载、解释器不匹配对照仅作为诊断保留，未放松 runtime 隔离。

| 观察中位数 | Main | 旧提案 | Refresh 提案 |
| --- | ---: | ---: | ---: |
| 生命周期墙钟，秒 | 22.092 | 23.620 | 22.033 |
| Setup 后计时子进程＋采样 daemon CPU，秒 | 14.756 | 15.353 | 14.907 |
| 采样 daemon disk counter，字节 | 8,888,320 | 8,310,784 | 8,093,696 |
| 采样 daemon logical-write counter，字节 | 13,946,880 | 13,840,384 | 13,107,200 |
| 已观察 daemon lifetime peak，字节 | 141,508,280 | 139,887,008 | 138,510,608 |

墙钟范围为21.898–24.716／22.615–24.863／21.824–24.767秒；setup中位数
2.336／2.404／2.392秒、最终teardown0.062／0.062／0.058秒分开。五次 refresh
候选均比旧提案快，但三次比 main 慢、范围重叠。计时子进程 CPU 包含独立 verifier；
完整 exit／helper／harness、provenance、model、attention 成本未覆盖；disk counter
和采样 lifetime peak 不等于物理设备写入或连续系统峰值。完整收益仍未合格。这些
观察不认证之后的 main 整合，也不替换历史／冻结失败；整合后的生产 hash 相同，
已重新运行四条 source／wheel File／SQLite 生命周期。

原完整声明和所有历史／冻结失败保留。I3至少一个 minor release、M1／#8011、
R0／V10／R1、D2／D5／D6／D3、context／model／split-install、PostgreSQL host／
wheel、完整成本／采纳及独立 exact-head评审仍开放；不包含合入main或installed-host
升级。后续实现必须依据完整成本比较，保留所有新鲜性／权限栅栏。

### 2026-11-08 — Source／wheel 匹配全进程成本覆盖 native 退出

原提案 #8302 与 target main `21013c0c1` 整合为 `9e147ddf2`，24 个 PR
production-source hash 不变。每 arm 的 1840 个 packaged tracked source 与新建
隔离 wheel 相同；五个未变的 source-only 文件仍在 package-data pattern 外：
Canary baseline、三份 Claude plugin／config 文件及 shadow-drain test process。
这验证 packaged-source parity，不宣称 source／wheel inventory 或 fingerprint
完全相同。

冻结的默认 SQLite 对照完成每发行形态五组交替配对，共 20 条完整 101-Todo
生命周期，环境为 macOS27 arm64、CPython3.13.5／Node26.0.0。Fixture 初始化、
CLI 与 verifier 使用精确 arm 的解释器／package；先加载实际 package 再加载
checkout fixture helper，在模块 import 前建立 temporary／process 隔离。首次
wheel 准备在 authority 初始化前拒绝 source-loaded fixture provenance；失败准备
单独保留。两个 wheel 均先通过原 owner 的 Chat build。全部匹配流程的 prequota
PID 集合保留正确来源的预热 daemon；受控重启创建第二个独立记账 daemon。
Bytecode 关闭，每条 CLI 是新进程；匹配运行不与 build／validator 重叠，结果出来
后不扩充冻结数量。

全部 20 条独立保留相同 sum45 产物、原完成时间、四份 committed receipt、一次
debit、required scopes、changed-evidence refusal、completion／writeback／spend／
terminal replay、受控重启及 settled-Turn execution refusal。相同的被动 OS waiter
观察两个 native daemon 的真实退出。External `wait4` 捕获 harness 及其 waited
subtree；直接回收的 daemon waiter 已计入，只额外计 orphan native subtree 和
已观测 orphan-waiter CPU，避免双计并覆盖此前采样遗漏的 native 退出 CPU。

| 发行形态 | Whole harness 墙钟中位数（范围），秒，main → candidate | 已观测 process CPU 中位数，秒 | 完整 managed-daemon CPU 中位数，秒 |
| --- | --- | --- | --- |
| Source | 25.904（25.589–26.629）→26.477（25.603–26.643） | 17.489 →17.749 | 1.782 →1.887 |
| Wheel | 26.248（25.670–28.200）→26.489（26.135–27.437） | 17.984 →18.341 | 1.787 →1.932 |

Source setup／lifecycle／teardown 中位数为 main2.602／22.581／0.062 秒、
candidate2.669／22.807／0.058；wheel 为 2.712／22.568／0.062 和
2.651／22.878／0.062。Whole wall 从 harness 启动测到 OS 退出，包含三阶段外
的 readback 与 observer 工作；各阶段中位数无需等于 whole 中位数。Source 五组中
候选四次墙钟更慢，wheel 五组中两次；两个发行形态均有 4/5 组候选已观测 CPU
更高。墙钟范围重叠，不宣称显著改善。五个样本的 nearest-rank empirical p95
等于最大值：source26.629→26.643 秒、wheel28.200→27.437；不是稳定总体分位数。

另一个独立插桩 wheel pair 保留所有 lifecycle／provenance／exit oracle，观察
RPC367→344、JSON 参数字节 3,506,215→1,874,964。它诊断 transport 机制，不验收
速度。该 pair 的 managed-daemon CPU 为 1.864／1.896 秒，约占已观测 process CPU
的 9.5%／10.0%。其他 Node helper／probe 与 Python 启动、本地处理一同位于 CLI
waited subtree，各自贡献尚未归因；不能将全部剩余 CPU 称为 Python 启动。下一步
先归因该 subtree 的主要成本，再通过原 typed owner 选择有限改动并复验相同成果／
恢复检查。原 Task 为此工作及独立 exact-head 评审保持开放；RPC 减少或测试通过
不证明完整路径收益。

当前 main 集成通过 99 个聚焦 Python、83 个 TypeScript 用例（TS 零 skip）及
control-plane typing。此前真实 PostgreSQL16.15 store391／service11 资格属于
未变的 production source，不是当前集成 head 的新运行。当前 head 的 File 完整
CLI 与 PostgreSQL host／wheel journey 仍未合格。Observer 发布结果后的 CPU 尾部、
物理设备写入、连续系统峰值、环境／build 成本、model／attention 和 installed
adoption 仍是 unknown 或在本次范围外；未观测尾部不记为零。历史失败与冻结
campaign 不替换、不重标签，完整边际／长程收益仍未合格。保留至少一个 minor
release 的 I3、M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3、context／model／
split-install、full-cost／adoption 及完整 freshness、identity／revision／WAL／
lease／receipt／recovery 栅栏。本轮不合入 main、不升级 host。

### 2026-11-08 — CLI CPU 归因与新鲜源码扫描的路径构造

一个独立 wheel 归因 pair 对照 main `21013c0c1` 与提案 `24ca29f4e`，保留完整
101-Todo 生命周期及原 source／package、scope、预热 PID、产物、原完成时间、
四份 receipt、一次 debit、refusal／replay／restart 和 native 退出 oracle。每 arm
记录 22 个 Python console 进程、三个独立 Node helper／probe、两个 managed
daemon 的真实退出。Helper 是两个 version probe 和一个 setup helper，均在
所属调用方读回前退出。PATH 解析的 Node 保留精确 executable／argv；Python
console CPU profile 使用 `process_time`，不以该插桩 pair 验收速度。

| 诊断 CPU，秒，main → proposal | 观测 |
| --- | --- |
| Profile dump 前采样的 Python CLI self | 20.447 →20.099 |
| Python profile exclusive total | 18.230 →17.922 |
| Import machinery cumulative | 14.505 →14.403 |
| Compilation exclusive | 7.488 →7.464 |
| 完整 managed-daemon subtree | 1.780 →1.890 |
| 三个独立 Node helper／probe subtree | 0.286 →0.281 |

Import cumulative 含嵌套 compilation，不能与 compile exclusive 或 self sample
相加。模块加载可能发生在操作内部，尚未独立识别解释器启动。固定的
bytecode-disabled 条件使加载／编译突出，helper 贡献有限；解释器／wrapper／
profiler 开销及发布尾部仍使这些值不能作为无插桩成本分解。它们不解释或推翻
此前冻结 pair 的 CPU 增量，不证明提速，也不验收正常 bytecode 的 installed adoption。

同一 profile 定位原 filesystem adapter 的 source-scan 路径处理。Runtime
`a57fd3ef7` 每目录只构造一次 POSIX 相对目录前缀，免去每候选文件额外的相对
`Path` 构造。源码选择、行排序、文件元数据、`Path.stat` 及其平台错误策略保留；
完整 source hash 与 freshness／identity、revision／WAL／lease／receipt／recovery
栅栏不变。这是有限的同域 refactor，不新增 capability、provider、持久字段、
词汇或 authority cache；原生 TypeScript owner 继续持有原规则。

不可变 parent 与新 scanner 在同一真实源码树返回完全相同的 404 个 metadata
tuple。64 个聚焦 Python 用例及 kernel mypy／Ruff 通过。Before／head 的真实
`runtime.ping` 在 disposable package 中均在所属 JSON 文件变化后切换 fingerprint
与 PID；故意漏掉该 JSON source 的 mutation 在同一独立 runtime oracle 上失败，
所有自有 probe runtime 已停止。八条 before／head × source／wheel × File／SQLite
完整 CLI 生命周期保留原产物、完成时间、scope、四份 receipt、一次 debit、
refusal／replay 及两个 native 退出。这验收正确性与已执行真实路径，不验收性能。

新建单体 wheel 与 1840 个 packaged tracked source 相同；原 24 个 production
文件中仅 `effect_runtime.py` 改动，仍保留相同五个 source-only omission。独立
import probe 首次从 checkout 工作目录加载了源码，其失败准备保留；修正后的
核验使用非 checkout 工作目录、清空 `PYTHONPATH`，并在比较字节前断言模块属于
隔离安装。Lifecycle harness 始终保留自己的独立 package-root 检查。首次在错误
目录执行 Chat build 命令也保留为准备失败，随后 dashboard owner 的 build 验证
bundle。

更大加载改动仍是下一步的独立决策：先在既有 quota／context 与 refresh dispatch
owner 对照真实 normal／disabled bytecode，再移动必需依赖。路径 allocation
变化仅是局部中间结果，不新增 whole-path／边际成本收益声明。当前 PostgreSQL
host／wheel、model／attention、连续峰值、物理设备写入、installed adoption 和
完整长程资格仍开放；未变的 PostgreSQL store／service owner 保留此前真实资格，
本轮不宣称新 PG 运行。保留全部冻结失败、既有 peer hard-cut validation hold、
至少一个 minor release 的 I3、M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3 及
context／model／split-install／full-cost／adoption 验收。原 Task 与独立 exact-head
评审继续开放，本轮不合入 main、不升级 installed host。

### 2026-11-08 — 正常 bytecode 生命周期及观测限制

仅执行一对 before/head wheel，复用完整 101-Todo SQLite 生命周期：源码为
`24ca29f4eb2afe0bbf27df00002abd005febf256` /
`1a4d9d8f92491db5913cfa59061bcd58c677868d`，打包 runtime 字节为
`9e147ddf2085b080eba0cd2d7888ec896b270333` /
`a57fd3ef771013c80add55821321e98e577836e6`，目标 main 仍为
`21013c0c113445b71a30f6ce06ab61455b56f1f2`。本 checkpoint 仅改双语 RFC，
生产提案保留并等待独立评审。

两端均新建 wheel 安装，使用相同 CPython 3.13.5、依赖版本及既有 fixture helper
的全新逐字节相同副本。安装与 fixture 初始 pyc 均为零。运行前的来源核验使用
`-B`、非 checkout 工作目录及清空的 `PYTHONPATH`，先确认导入模块属于隔离安装，
再核对全部 1840 个 packaged tracked source 哈希；保留相同五个 source-only
omission。测量进程使用默认 bytecode，清除 write-disable、cache-prefix 与
importtime 变量，读回 `sys.dont_write_bytecode=False`、`sys.pycache_prefix=None`。
导入 fixture 前即隔离 data／临时目录。解释器 stdlib 与 OS 文件缓存共享，不属于
本次隔离声明。

| 缓存阶段，两端路径集合完全相同 | 全部 installed pyc | LoopX pyc |
| --- | ---: | ---: |
| 安装完成 | 0 | 0 |
| Driver 导入 fixture 前 | 1 | 0 |
| Fixture/setup 完成 | 317 | 223 |
| Bootstrap 完成 | 358 | 264 |
| Prequota 完成 | 433 | 339 |
| Guard 与 required reads 完成 | 665 | 571 |
| Settled replay 与 teardown 完成 | 699 | 605 |

Driver 首个 pyc 来自 virtualenv support。两端全新 fixture 副本在 setup 各生成
五个 pyc；最终 605 个 LoopX pyc header 全部匹配解释器 magic 及源码 timestamp／
size。这是缓存有效性与阶段观测，不是单次 guard 的模块数量，也未测出默认模式
的 compile／import CPU。

两端原生命周期均成功退出：sum45 artifact 及 digest、原 completion time、
required scopes、四个 committed receipt、一个 debit、prequota 不变的 warm
PID 集合、受控重启、evidence replacement refusal，以及 completion／writeback／
spend／terminal／settled-Turn replay 均通过。每端保留全部 22 个 console 的
command／phase／wrapper 退出记录、三个 exact-argv Node helper 退出及两个 managed
daemon 退出。Waited descendants 只计算一次，仅把 orphan daemon subtree 与已观察
的 waiter CPU 加入 outer waited subtree。

**实际 console PID 观测子检查失败。** 首个 observer 把 macOS
`proc_listchildpids` 返回的 PID 数量误作字节数，记录为 0/22。独立 disposable
child probe 复现并修正 API 解释后，才执行尚未开始的 head。Head 记录 21/22，
bootstrap 仍落在有界采样窗口之外。Wrapper PID／argv／exit 记录完整，但实际
PID 覆盖不完整；两次运行后的子检查失败都保留，不重跑任一生命周期。使用 host
不兼容 Python 3.9 的初步 parse 也失败，执行用 3.13 的语法验证通过。修复与证据
缺口保留在原 Task 的恢复记录中。

| 单对原始观测，before → head | 数值 |
| --- | ---: |
| Whole harness wall | 24.044 → 25.180 s |
| 包含 orphan additions 的 observed process CPU | 10.698 → 10.724 s |
| OS time 文件中的四舍五入 console waited-subtree CPU | 6.020 → 5.980 s |
| 完整 managed-daemon subtree CPU | 1.900 → 1.920 s |
| 三个 Node helper subtree | 0.273 → 0.291 s |
| Setup / 最终 teardown wall | 5.503 / 0.062 → 3.338 / 0.054 s |

Observer 修正使两端的有界采样等待不同，head bootstrap 另有较大 wall 延迟。
Fixture、verifier、observer 与 readback 工作仍计入 whole harness。这些观测不能
验收提速、回退或 paired CPU 差异，不能解释既有冻结成本实验，也不能与
bytecode-disabled profile 相减后声称 compile saving。Console waited CPU 包含
本地处理及 descendants，不能统称 Python startup。冻结样本不重跑、不扩充、不
重标签；publication／exit tail、物理写入、连续系统峰值、build／setup environment
成本、model／attention 与 installed adoption 仍 unknown。

这回答了有限的默认缓存正确性问题，但保留成本／归因缺口。继续保留既有 typed
transaction 与 source-scan 提案。下一段应检查既有 quota／context／refresh
caller 的必需依赖闭包，再选一个完整可回退的加载改动并验证 normal-default 语义
一致性。有效缓存本身不证明应移动必需 import 或移除 fresh read；不为补 PID
观测重复本 pair。原 Task、独立 exact-head review／readiness、peer hard-cut hold
及全部 I3/minor-release、M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3、
context／model／split-install、PostgreSQL host／wheel、full-cost／adoption 要求
继续开放；本轮不合入 main、不升级 host。

### 2026-11-08 — 默认调用方加载与不可变恢复

在 `a02be126957953b7a61796e3b99fcfe578eb5174` 上，以 Python 3.13.5 和正常字节码
设置执行完整的源码/default-SQLite 生命周期，记录 22 个实际控制台进程的加载。
原合成 101-Todo 工作流保留独立验证的 sum=45 产物、原完成时间与写范围、四张
receipt、一次 debit、重启、证据替换拒绝和结算回放；两个自有原生 daemon 均退出。
这证明当前源码的实际正确性与加载事实，不是新的配对成本样本或安装分发资格。

新鲜 guard 加载 505 个 LoopX 模块，实际 refresh 写回加载 432 个。显式 reader
reference 的上下文恢复，首次选择读取加载 71 个、后续分页加载 69 个，均未加载
capability 模块。另一个恢复探针拒绝所有 capability import，仍通过两页精确重建
原 60,888 字节 capture。修改后的 capture 仍被摘要校验拒绝；复制后具有公开文件
权限的 reference 被私有文件检查拒绝。首个 observer 错误要求控制台路径形式的
续读命令；接受实际出现的精确解释器/module 前缀后，沿同一固定 offset 继续。
保留 observer 失败和复制 reference 的准备拒绝。该探针不证明新的 authority 写成本。

成功 import 的跟踪定位到剩余组合边界：`cli_runtime` 在 refresh 验证前加载
`periodic_report.post_writeback_hook`；默认禁用报告的路径也加载报告 source、
profile 和 preset 构造依赖。已加载模块本身不证明必需性或成本。下一项有界加载改动应在
已有 provider 内分开订阅解析与报告 source/profile/preset 构造，保留实时机器默认值和 Goal
覆盖解析、模块导出与 patch 点，以及全部 hook/replay/authority 检查。修改边界前，
先刻画禁用与启用两种实际写回。已隔离的不可变 reader 不支持大范围迁移上下文读取。

反馈与词法 scope 提案仍是各自的评审依赖，本检查点的测量源码未包含两项提案。
既有 File/wheel/PostgreSQL 证据保留其原源码边界。独立评审/readiness、I3 至少一个
minor release、M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3、context/model/split-install、
PostgreSQL host/wheel、完整成本与 adoption 仍未验收。保留全部冻结失败和样本；
这里不证明加速、完整内核验收、main 合入或 host 升级。

### 2026-11-08 — 反馈校验不加载写入适配器

初始 runtime `2c5212fdb15035c26c3e158420d67197d45715f2` 将 `loopx.feedback` 中
两个既有 retained-state 写入适配器改为按需加载。适配器不可用时，纯反馈校验
仍可执行；parent 会在模块导入时失败。Reward mutation 在 registry 读取或
写入前解析两个适配器，原 guard 与 writer 调用位置仍查找当前模块 binding。
保留适配器对象 identity、调用中途 patch 行为、先 index 后 state 的锁顺序、
TypeScript guard owner、凭证拒绝及 partial-write handling。本地兼容映射、
模块 `__getattr__`／`__dir__` 与 computed `__all__` 保留全部 50 个原 star-export
名称，包括后面的 append／render 函数。不新增 capability、provider、持久词汇、
规则 owner 或跨请求 authority cache。

两个相关组分别通过 291 个反馈策略及 48 个适配器／写入／恢复用例。独立的
适配器不可用用例在 parent 上失败；head 保留纯校验，并在 reward mutation 前
拒绝缺失 writer。聚焦 mypy／Ruff 通过。Production semantic advisory 未发现
支持范围内的新 carrier；显式纳入测试文件超出该 scanner 支持的 `loopx/` 范围。
测试中的 refusal 字符串仍是本地 fixture。

重建单体 wheel 先断言隔离安装的模块所有权，再在非 checkout 工作目录、清空
`PYTHONPATH` 后核对全部 1840 个 packaged tracked source。相同五个 source-only
omission 保留；相对冻结 runtime candidate，仅 `effect_runtime.py` 和
`feedback.py` 字节变化。首次 wheel 准备因缺少 Chat bundle 失败后，既有 dashboard
Chat build 提供所需 bundle。四条新的 candidate source／wheel × File／SQLite
101-Todo CLI 生命周期使用默认 bytecode，全部通过原 artifact、completion time、
scope、四份 receipt、一次 debit、restart、refusal 与 replay oracle。每条的两个
managed native daemon 均退出。这是新 runtime 正确性检查，不是新增 matched
cost 样本。

正常缓存 profile 仅检查已结算 synthetic recovery dry-run 的加载依赖。其
410-module 闭包及嵌套 import／compile timing 不能证明默认 provider 性能、fresh
authority 行为或生命周期成本。初始 profile 文件名遮蔽 stdlib，在 CLI 前失败，
仍保留为准备失败。冻结样本及上一轮归因 pair 不重跑、不扩充、不重标签。
不新增 whole-path 收益、PostgreSQL host／wheel、model／attention 或 installed
adoption 资格。保留 peer hard-cut hold、全部历史失败、至少一个 minor release
的 I3、M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3 及
context／model／split-install／full-cost／adoption 验收。原 Task 与独立 exact-head
评审／readiness 继续开放；下一段加载决策仍属于既有 quota／context／refresh
caller。本轮不合入 main，不升级 installed host。

提交准备期间，维护者在 `481436a56b47220c7c43bd6e272c1fa59d5038fe` 合入
#8302，该 head 未包含本次反馈改动。两个尚未提交远端的反馈／checkpoint commit
移到基于目标 main `0ed909880` 的干净后续分支，反馈源码及聚焦测试字节相同。
后续 wheel 先验证安装所有权，再匹配 1841 个 packaged tracked source，保留
相同五个 omission。新的 source／wheel × File／SQLite 生命周期全部通过四组
原 oracle，每条两个 native 均退出。在此变化的集成基线上，48 个写入／恢复及
265 个共享反馈策略用例、mypy／Ruff 通过。这些检查验收后续提案的变化基线，
初始运行保留为历史证据，不重跑或新增性能样本。后续提案仍需独立 exact-head
评审／readiness，原 Task 的完整验收继续开放。

### 2026-11-08 — 声明 codec 保留按需加载 owner

继承的 TypeScript CI 失败在目标 main `94366314f0` 复现。冷启动 handler 经由
interaction／acceptance、outcome owner 加载 lease 执行，因为 work declaration
从完整 acquire runtime 导入 `normalizeWriteScopes`。Runtime `b0c42af8f` 将既有
scope／value 词法函数移到 `work_items/write_scope_codec.ts`，保留原导出。
Coercion、原请求拼写、字符类、160 字符限制及拒绝行为不变。Lease IO、admission、
repository／workspace identity 和 source／revision／WAL／receipt 栅栏仍由原
owner 持有。Digest guard 登记既有 quota action 通配 matcher，可见 64-hex
模式的拒绝保持。

两个失败 CI 用例通过；Node 22.22.3 的 19 个加载／digest 用例通过，Node 26
相关组通过 60 及 70 个用例。首组缺 PG 的 skip 随后由隔离 PostgreSQL 16.15
覆盖：store／workspace 组通过 391／32 个，独立 service suite 通过 11 个、零
skip。缺少 service 环境变量及复用 incarnation 的初始失败保留，修正后的 service
使用自己的新数据库。16 个 old／head scope 值／拒绝比较及原导出 identity、
TypeScript typing 通过。四条新的 source／wheel × File／SQLite 101-Todo CLI
生命周期保留原 artifact、completion time、scope、四份 receipt、一次 debit、
restart／refusal／replay 及两个 native 退出。Wheel 先验证安装根，再匹配 1842
个 packaged tracked source；相同五个 source-only omission 保留。这验收当前
runtime 正确性，不验收完整成本或 installed benefit。全部历史冻结失败、peer
hard-cut 证据、I3／minor-release、M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3 和
context／model／split-install／PostgreSQL-host／full-cost／adoption 缺口保持开放。
原 Task、独立评审／readiness 继续开放，本轮不合入 main、不升级 installed host。

### 2026-11-08 — Turn 装配中的报告构造加载

既有 `periodic-report` Python provider 与 CLI Turn 适配层改为按需解析报告源、
预设、阶段投影和请求导出。在 runtime 版本
`7858d96e83ef98174cc43870e283f8f0e6f40c0b`，禁用订阅不会导入这些构造 provider。
能力包已有 lazy exports，本次修复的是实际装配调用方。订阅校验仍由原 owner
负责，包括 cadence 使用 profile 中的日程校验器。Goal override、live machine
defaults、配置 warning、原导出身份与 patch 点、hook profile digest/policy version、
source 选择、receipt replay 和权限保持；不新增 capability、配置、UI、持久化词汇
或决策 owner。

与基线 `c263f7c78353fcaa914f23475d4d99d07eab7c01` 对照，11 个相同合成配置场景
保持 hook 输出、trigger 决策及完整配置错误消息一致。冷进程拒绝 oracle 在修复前
因禁用装配导入 report source 而失败；8 个聚焦场景现覆盖缺省、禁用继承、override、
非法禁用配置、无副作用读取和导出／patch 兼容。最初更强的禁止规则也拒绝了原
profile 日程校验器；该失败探针保留，本阶段保持校验器，不绕过它。

Source 与独立构建 wheel 的 File/default-SQLite console 路径通过原始完整
101-Todo 生命周期：fixture/artifact 不变、4 个 receipts、一次 debit、source/lease/
拒绝／重启／重放读回，以及每条路径 2 个自有 native 退出。先验证安装 package
归属，再比对 1,920 个实际存在的 tracked entries；17 个 source-only 或文档／媒体
省略明确保留。Wheel 测试依赖和 frontend build 准备失败仍记录为失败。
隔离 PostgreSQL 16 store/service 套件通过 391/11 场景，零 skip；service 首轮
因缺少环境声明而跳过的记录仍保留。这不资格 PostgreSQL host/wheel 路径。
报告相关套件通过 100 场景；较广的 digest inventory 检查在未改动 Todo selector
上出现一条完全相同的 baseline/head 失败，该检查仍为失败。

独立报告装配探针在 macOS arm64/CPython 3.13.5 下，两侧使用正常 bytecode、一次
不计样本的预热，对禁用／启用场景各做 8 对交替观察。禁用 wall/CPU 中位数为
修复前 0.255/0.111 s、修复后 0.246/0.107 s；启用为 0.331/0.117 s 和
0.321/0.113 s。加载的 LoopX modules 从 196 降到 179/182。小样本与并行测试下
的这些观察不证明明显提速、持续尾延迟或完整 CLI/Node/model/attention 成本收益。
早先冻结的 20-cost、归因及正常 bytecode 实验未重跑、扩充或重标签。
本阶段可独立回滚的增量是关闭态构造隔离。采纳仍需同 head review、CI/readiness
和明确的受保护合并授权。I3 至少保留一个 minor release，M1/#8011、完整
R0/V10/R1、D2/D5/D6/D3、context/model/split-install、full-cost/adoption 缺口
及所有历史失败均保持开放；既有 runtime composition 工作继续跨越这些边界。

### 2026-11-08 — 合成 Host 的 UTF-8 恢复资格

#8360 的 Windows CPython 3.11 CI arm 在六个既有原任务重验证用例中失败。
合成 Python Host 对 pipe 继承 locale decoder，而原 generic Host transport
发送 UTF-8 JSON。Disposable File-provider counterprobe 只改变该 Host decoder：
CP1252 产生 `host_failure`，没有 `task_failure`；UTF-8 到达独立产物 validator，
在 `task_postcondition` 产生 `validation_failed`，并提供
`revalidate_cached_result`。源码 macOS arm 独立通过全部12个既有重验证用例。
这些观察定位了 fixture mismatch，不支持扩大生产 failure classification。

Fixture commit `a4ce613585fdb329ba3600f0ede768864214ed84` 基于 main
`4e949f405528fd8087f0623526c62f890c1ce982`，在读取请求前显式将 stdin 配为
UTF-8。新增 File／SQLite regression 先只把生成 Host 的 decoder 设为 CP1252，
发送 Unicode brief，要求被拒绝的产物到达 task validation。修复产物后，重验证
必须沿用原 turn key 并接受结果，Host invocation 始终为一次。同一 regression
在相同 CPython3.13.5／Node22.22.3 环境中对不可变 baseline Host 失败，修正后
通过。不改变 transport、生产 owner、持久状态或协议词汇。

macOS arm64／Node22.22.3 下，CPython3.11.15 source 与新安装的隔离单体 wheel
各通过新增两个 provider 用例及全部12个既有原任务恢复用例。先在 checkout 外
验证 wheel import 归属，再加载 source-owned fixture；1920个实际 packaged
tracked runtime entry 均与源码字节相同。原17个 source-only exclusion 保留：
canary baseline、host plugin／config、fault-test helper 和 documentation／media。
这验收既有 File／SQLite 合成 Host 恢复路径，包括保留的 MCP
断连及 lock-timeout 恢复用例，不验收模型采纳、split installation 或原生 Windows。
原失败 Windows arm 保留为历史证据，直到新 exact-head Windows run 通过。
独立 Frontstage manifest 请求在有界重试后仍为 HTTP504。

原任务保持开放。保留 I3 至少一个 minor release、M1/#8011、完整 R0/V10/R1、
D2/D5/D6/D3、context/model/split-install、PostgreSQL host/wheel、whole-path／
full marginal-cost／adoption 验收及全部历史／冻结失败。不重跑或重标签成本
campaign，本 fixture 资格不证明性能收益。报告按需加载提案 #8360 保留独立
review／CI gate；下一步为本修正的 exact-head 评审与原生 Windows 读回。不包含
合入 main 或 installed-host 升级。

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
| E9 | Version 按需加载及匹配成本 | `5a5863502` / `8a79d8602`；macOS arm64 / CPython 3.13.5 | 上述 version checkpoint；既有 console/version 及 performance-diagnosis owner | 修正后两组各 30 对，共 120 个观察；公开 facade／一致性／缓存读回通过；版本查询明显改善 | 首轮 120 个观察及 facade 失败保留；仅公开安全汇总；完整 Turn/minimal、拆包安装、宿主升级及完整成本未取得资格；E7/E8 保留在 #8185 |
| E7 | 有界成对成本实验 | `a9485073f` / `86d9c542d`；上述具名环境 | 上述成对成本 checkpoint；既有入口／SQLite 夹具及 performance-diagnosis owner | 499 个观察；源码组完整，wheel 不完整；缓存归因未取得资格 | 仅公开安全汇总；不证明提速、完整 V10、拆包安装或模型成本 |
| E8 | 字节码匹配的代表性对照 | `546c08c0d` / `0df5ad792`；上述具名环境 | 上述匹配字节码 checkpoint；相同 CLI/SQLite 及 performance-diagnosis owner | 六组各 30 对，共 360 个观察；语义／拒绝及缓存读回通过；未证明明显收益 | 仅公开安全汇总；后续集成、完整 V10、minimal、拆包安装及模型成本未取得资格 |

## 附录 D：被拒绝或被取代的备选

**多仓库拆分。** 本 RFC 拒绝的原因：在 TypeScript 控制面迁移处于事务 cutover 期间违反 I1，成倍放大 CI 与发布面，且需要目前不存在的按仓库 maintainers。可重开该决策的证据：迁移 RFC Stage 4 完成；某个 `packages/` 发行物拥有两位以上非内核 maintainers，且连续三个版本无跨包破坏性变更。

## 附录 E：事故与评审经验

### 生成式 Todo 指针的读取兼容性（非规范性）

归档摘要和单条 Todo 活跃字段省略指针由既有 Todo 投影 owner 生成。实际观察到的
严格 `machine_region.py` reader 与 `8d314af49` 一致：它拒绝没有可识别 Todo
元数据的归档摘要注释，但已能通过有效的 `todo_id` 元数据接受生成的单条省略行。
两者共享 `loopx:todo-archive-pointer` 前缀不能证明 reader 漂移；只有精确安装
reader 确实拒绝该条生成行时，才能将单条省略故障归因于它。归档摘要 reader 修复
已通过 [#7834](https://github.com/loopx-project/loopx/pull/7834) 进入 `main`。
受影响的安装 reader 需要装配资格验证，不能据此重复源码修复或删除活跃投影元数据。

作者在 `97cc1e0c4` 上，以相同合成 source 和真实 File/SQLite store，在 Python
3.13、3.14 与 Node 26 下验证。旧 reader 的语法与 `8d314af49` 一致。旧快照、当前
源码及其构建 wheel 的 36 场景只覆盖普通、归档摘要和外来叙述行，未测试单条字段
省略。它们得到 20 个预期成功、16 个预期拒绝：4 个旧 reader 归档摘要失败和
12 个外来叙述失败。4 个旧 reader 失败均通过当前源码按原 Turn 身份恢复
并重放；canonical work 保持不变，quota 扣费为零。另外 8 个 source/wheel console
场景以合成 Session 身份完成 native capture 回读；fixture 准备失败仍保留为失败，
已提交 capture 的恢复未重跑准入。既有 region、projection 和 SQLite archive 回归
通过（83 个场景）。

评审资格验证在 runtime 基线 `ebb39f4db` 上，通过真实安装快照、源码和隔离 wheel
的 console 入口，在 File/SQLite、Python 3.13.5 与 Node 26 下补齐单条省略对照。
18 个场景得到 10 个成功和 8 个预期拒绝：旧 reader 拒绝 2 个归档摘要，三个发行
形态均拒绝共 6 个外来叙述场景，6 个单条省略场景全部成功。10 个成功场景执行了
实际发出的 native read 命令并重放原 Turn；2 个旧 reader 归档摘要失败均按同一
身份通过源码恢复／重放。source 和 canonical work 均未改变，quota 扣费为零。
83 个既有回归再次通过；打包 runtime 资源与源码字节一致。fixture 准备失败仍保留；
一次初始 process-exit 等待未确认，因此保留证据目录并另查 serving retirement，
不声称 process-exit 资格通过。

36 场景矩阵耗时 135.6 秒；这是观察成本，不是速度或长程收益资格。完整 model、
service CPU 与人工成本未测量。真实安装宿主的升级及回读仍未验证。I3 至少保留一个
minor release。这些证据不关闭完整 R0/V10、M1/#8011、R1、context/model 或拆分安装，
也不晋升 D2/D5/D6/D3。使用既有 self-repair lookup 中的
`marked_todo_archive_pointer_reader_drift`，组合查询
`quota_unexpected_collection_error non-generated content inside marked Todo region`。
精简 Codex App 输出可能只有错误码；只读
`loopx --format json quota status --goal-id <goal-id> --verbose` 提供详细症状。
据此区分已交付修复和安装 reader 漂移；保留外来内容拒绝，不重写活跃状态。

### 2026-11-08 — 加载提案集成与可空 staged 读取

声明 codec 提案现已整合目标 main `efd49352c`、#8339 的反馈加载组件，以及 main
已有的等价固定时钟修复。组件的批准及检查仍只证明各自旧 head，组合提案需要自己的
评审与验证。

真实的保留数值精度的 `quota replan-view --replan-stage explore` 读取，在 staged
视图仅含 string、boolean 和 null 时失败。既有 edit encoder 将整个 root 压缩为
空 property path，Python transport 随后拒绝。将该紧凑替换限定到嵌套 record，
保留 property-path 契约及既有 number／source lineage。独立 root-path oracle 在
修改前失败、修改后通过；真实读取恢复原 context／hash 和缺省 Explore basis。
不新增规则 owner、词汇、provider 或 authority。

集成后的 source 与 packaged wheel 研究记录场景保留原负结果段落、两条原始历史、
Goal 隔离及导出；packaged 对话场景也通过。此前 CI 超时在本地未复现，其平台／时序
原因仍未知，不宣称已经修复。四条新的 source／wheel × File／SQLite 101-Todo
正确性流程保留原 fixture／artifact、scope、completion time、四份 receipt、一次
debit、refusal／restart／replay 和两个自有 native 退出。初始 driver 使用 `python`，
source console 实际选择 `python3`，产生不同 runtime fingerprint，导致 warmup／
exit oracle 失败。保留该失败；后续用例采用各 console 的实际解释器，不重跑或重标签
任何冻结成本样本。

隔离 PostgreSQL 16 的 store／service 分别通过 391／11 个用例，零 skip。
已安装 wheel 的 1921 个 tracked package entry 字节一致；17 个 tracked 文档／媒体／
测试或 source-only entry 未打包，不据此推断已执行 caller 之外的通用发行覆盖。
Whole-path 成本收益、installed adoption、PostgreSQL host／wheel，以及全部
I3／minor-release、M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3、
context／model／split-install 与历史失败资格继续开放。组合提案评审之后，下一段
有用边界仍是报告订阅／构造加载；原 Task 保持开放。

### 2026-11-08 — 组合加载与恢复资格验证

组合提案在目标 main `fe36f31fd` 上采用 #8346 已有的 feedback/声明 codec
修复、#8360 的报告构造隔离和 #8365 的 UTF-8 合成 Host fixture 修复。
运行时代码资格基于 `251d7eaeff4581d3436340f68c5b6cd8c8ebf1ad`。
已有的词法 `write_scope_codec.ts` 已解除观察到的完整 lease 准入依赖，无需
增加 interaction decoder 层。错误与词汇 owner、声明 scope 拼写和保留回执均保持。

Node 22.22.3 上的加载/digest 与相邻 work-requirement、lease、effect-program
回归全部通过（60 项），控制面类型检查通过。选中 owner 的加载失败仍拒绝执行，
不会回退。四次新的源码/wheel × File/SQLite 101-Todo console 正确性路径均保留
前次不可变 fixture/artifact 指纹、scope、原完成时间、四份回执、一次扣额、
拒绝/重启/重放，以及每条路径两个自有 native 进程退出。wheel 中现存的 1,921 个
tracked 条目逐字节匹配源码，十七项源码专用、文档和媒体遗漏保持不变。

真实 PostgreSQL 16.15 store/service 在隔离临时服务上全部通过（391/11，零
跳过），自有服务随后停止。首次 service 运行漏传专用环境变量，跳过一项；
该次资格失败单独保留，与修正后的运行区分。

浏览器 fixture 的中断检查还依赖共享的五秒完成窗口，并在异步回执返回前检查请求
记录。该场景现在只保持纠偏所对应的准确 Session/Turn 事件流，确认它仍在执行，
并等待匹配的中断响应。原十秒中断消息断言完整保留。延迟回执探针使原场景失败；
仅移除候选中的流保持、保留响应等待，会复现该消息超时，而完整候选通过。
这是合成浏览器资格验证，不证明 native runtime 存在缺陷或已安装修复。
完成优先级与 stale-Turn 保护保持不变；组合 head 仍需 packaged 验证、CI 和
独立评审。

这些路径验证组合正确性，whole-path 与完整边际成本、adoption 仍未取得资格。
保留 I3 至少一个 minor release、M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3、
context/model/split-install、PostgreSQL host/wheel 以及每项冻结失败。
原任务保持开放；本检查点不授权合入，也不宣告最小化内核完成。

### 2026-11-08 — Canonical source 标量比较细化

现有 `todo.read` owner 在完整 canonical field digest 与 JSON clone 之后，对完全相等的
原始标量使用精确相等判断。容器和发生变化的标量保留完整 canonical digest 比较；
缺失字段、source lane 新增、标量 fact 保护及过期 reference 拒绝继续由原 owner 负责。
未引入 source allowlist、provider cache、protocol、配置或竞争的决策 owner。

在 `main@aee777008` 上，一次新的合成完整 CLI caller trace 确认现有双 role native
transaction 已被实际采用。Quota before/after projection 属于不同预算决策，重复的
read recipe 不能据此变成 authority cache。本次有界重构留在现有 canonical source
比较层，保留这两次决策。

`read_transaction.ts` 最终 source SHA-256 `ac0ae4311e4fe5c385ec75b3569cd8ebbe98328189e2885b5ecdcccb11c476b3`
通过 Python61、TypeScript56，以及真实隔离 PostgreSQL16.15 store391/service11；
这些已资格验证的运行均无 skip。不可变原版通过所选 native PostgreSQL source 保护用例，
将所有原始标量视为 unchanged 的 mutation 在同一用例失败。父级替换、删除和恢复，
note/scope/count 变化与 provider state 读回继续覆盖。source/wheel × File/SQLite
四次完整 101-Todo CLI lifecycle 保留 sum45、原完成时间、四份 receipt、一次 debit、
restart、refusal/replay；每次都拥有并观测两条正常 native process exit。隔离 wheel
runtime source bytes 与 checkout 一致。验证环境为 macOS arm64、CPython3.13.5、
Node22.22.3。更早的工具链/setup 失败与 service-env skip 仍作为独立准备失败保留。

本次减少重复标量 digest 工作，不代表 matched-cost、完整边际成本或 adoption 收益。
冻结的 cost、attribution 与 normal-bytecode campaign 均保持原样。继续保留原完整
transaction 与 I3/minor-release、M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3、
context/model/split-install、PostgreSQL host/wheel 及所有历史失败验收缺口。
exact-head review、必需 CI/readiness 与 maintainer merge authority 仍是独立门；
未宣称 installed-host upgrade 或 Goal 完成。
### 2026-11-09 — 复用已验证的完整 source basis bytes

现有 Python Todo compatibility transport 将已经通过 canonical field digest
验证的完整字节复用为 source basis；此前每个字段分别为 basis 和 digest 编码一次。
basis 只作为 JSON 解码，mapping insertion order 不属于 source difference。
完整 digest mismatch fallback、标量 fact 保护、request-scope provenance 与
source/revision/lease/refusal/replay guards 均保留；未新增 decoder、cache、provider、
配置、protocol 或竞争的决策 owner。

在同一次真实隔离 SQLite field observation 上，不可变原版对两个字段编码四次，
候选版编码两次；二者保留完整值与空 source differences。原版通过两个 mapping-order
与标量保护 characterization 用例，候选版通过 File/SQLite 全部九个 canonical source
用例。最终 `summary_item.py` SHA-256 `a8ca890eee547929060ac326fc8b85ca9c14a8fd5ec850deb2fe3e6586628513`
与隔离 wheel 一致。source/wheel × File/SQLite 四条完整 101-Todo CLI lifecycle
保留 sum45、原完成时间、四份 receipt、一次 debit、restart 和 refusal/replay，
每条均观测两次正常 native exit。验证环境为 macOS arm64、CPython3.13.5、Node22.22.3。

这是编码机制细化，不代表 matched-cost、完整边际成本或 adoption 资格。冻结的 cost、
attribution 与 normal-bytecode campaign 保持不变。PostgreSQL host factory 缺失
已有记录，其 store/service 验证不能成为 source/wheel host journey 资格。继续保留
原完整验收以及 I3/minor-release、M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3、
context/model/split-install、installed-host、full-cost/adoption 与全部历史失败缺口。
exact-head review、CI/readiness 和 maintainer merge authority 仍是独立门。

### 2026-11-09 — 报告与 canonical-source 完整 caller 组合资格

原 #8360 提案在目标 main `aee777008` 上组合报告构造隔离、#8372 的标量比较与
#8374 的完整已验证 source-basis 复用。运行时代码资格基于
`8b4df2fac966933e200ba15bb36b2f7afb9a194a`，保留原提案祖先。沿用报告与 typed Todo
owner，保持配置验证、完整 canonical read、source/revision/lease 保护及
receipt/refusal/replay 语义；未新增 decoder、authority cache 或 provider。

source/wheel × File/SQLite 四条新的完整 101-Todo CLI lifecycle 保留原
fixture/artifact 指纹、scope、完成时间、四份 receipt、一次 debit，以及各两次
可观察的正常 native exit。wheel 中现存 1,921 个 tracked 条目与源码逐字节一致；
十七项既有 source-only、文档和媒体遗漏保持。Node 22.22.3、CPython 3.13.5 下，
控制面 typecheck 和 105 项相邻 TypeScript 检查通过。真实隔离 PostgreSQL 16.15
store/service 通过（391/11，零 skip），自有服务随后停止。

隔离 wheel 的 packaged 浏览器在合成 upstream 下通过 `typed-actions`、
`conversation-activity` 和 `conversation-history-recovery`，保留原准确中断断言、
完成优先及 Turn 隔离。扩展报告 caller 检查得到 70 项通过、一项失败：日历
capability-action 测试预期的 `turn_envelope` 缺失。不可变目标 main `aee777008`
上的同一用例以同样错误失败；保留为未解决验证缺口，不重标签为通过。初始相对解释器、
installed-package provenance 与缺少浏览器的准备失败，与已验证运行分别保留。

组合边界验证集成正确性，不代表 whole-path、完整边际成本或 adoption 收益。
冻结 cost、attribution、normal-bytecode campaign、原全部验收缺口和历史失败保持。
#8372/#8374 暂时仍是开放独立提案，待后续协调；组合不代表已合入。
exact-head review、CI/readiness 与 maintainer 明确合入授权继续必需；原 Task 保持开放。

### 2026-11-09 — 对齐已合入加载与剩余 source 提案

发布前观察到 #8360 已以 `e37a1d691` 合入，#8011 也已进入目标 main `520a1ab47`。
剩余 source 细化沿原 #8372 提案继续，保留 `cbda59c6e` 祖先并纳入 #8374 的
完整 basis 修改；不重建已经合入的报告提案。

在该 main 上，运行时代码 `4e72980bee91c7bbfcedb2eb481b8573113340b3` 的新验证
通过 Python82、TypeScript105、typecheck、真实隔离 PostgreSQL store/service
（391/11）、source/wheel × File/SQLite 四条完整 101-Todo lifecycle，以及三个
packaged 浏览器场景。wheel 仍有 1,921 项字节匹配与十七项既有遗漏。
该 runtime revision 的 standard premerge 通过六项 direct 与十九项 selected 检查。
扩展报告检查仍为 70 项通过、一项失败，不可变 `520a1ab47` 复现同一个
`turn_envelope` 断言失败。这项 packet-contract 验证缺口与 exact-head review、
必需 CI/readiness、maintainer 合入授权共同保持为限制。初始 collection、环境和
中断的临时运行继续保留各自原始资格限制。

本次对齐已交付的加载与剩余待评审 source 边界，不关闭原 Task、installed-host、
PostgreSQL host/wheel、full-cost/adoption 或任何其他保留验收；不重跑或重标签
冻结 campaign，本 lane 未执行 main 合入。

### 2026-11-09 — 对齐已合入 status 迁移后的 M0 ratchet

#8372 的准确资格 head 在 CI 的 kernel-static 前置检查失败：已合入 #8011
将 `chat_status_api.py` 移入 `chat/status_api.py`，顶层模块数从 128 降至 127，
fixture 却仍固定为 128。下游 Stage 2C、checks 与汇总 pytest 因前置被跳过或失败
而拒绝；这些结果不能证明新的 runtime 失败或通过。

现有 M0 fixture 将测量基线记录为 main `520a1ab47`，并把 pin 降至 127。
schema、入口 allowlist、精确计数规则与超额拒绝保持。相同七项 kernel-static
检查在 CPython 3.13 和 3.11 通过；同一个隔离源码树增加未批准的第 128 个顶层
模块仍失败，仅移除该合成模块后恢复通过。原 CI 与本地 127/128 失败继续保留。

本次修复已经合入的模块迁移的验证交接，不迁移另一个 runtime owner，也不改生产
上限。Pages、独立 report packet-contract 缺口、完整 CI/review/readiness，以及
全部原 installed-host、PostgreSQL host/wheel、full-cost/adoption 和冻结资格继续
分别保留；原完整 Task 保持开放。


### 2026-11-09 — 完整源的报告调用方验证

M0 pin 修复已在 CI 通过，同一记录中的提案 head 也通过真实 PostgreSQL、typed
kernel、TypeScript、浏览器和 Stage 2C 检查。组合源码现已接入目标 main
`1cba745f5`，并保留原提案祖先。报告 calendar CLI smoke 在组合源码上仍失败，
因为它直接读取已退役的 `turn_envelope` 字段。replan 必须为 `None` 的断言也属于
旧 projection adapter：完整源保留竞争建议；最终 capability 优先级仍由现有 typed
controller 决定。

smoke 现在将单一 `work_contract` 直接交给现有 controller，在 plan、dry-run、
执行模式的 generic/Codex CLI 调用中验证相同的能力 owner 下一步命令、host
空闲、消费前不生成报告制品以及全部 effect 标记不变。保留原 source binding
却篡改命令会被拒绝；从真实 CLI 输出删除下一步命令会让独立 smoke 断言失败。
没有新增 runtime decoder、兼容 adapter 或 authority owner。

源码和隔离 wheel 的报告调用族分别 71 项全部通过，邻接 Python 101 项及控制面
typecheck 通过。本轮盘点中存在的 tracked wheel 条目全部与源码字节一致。这关闭旧调用方测试缺口，不宣称新的执行语义、完整成本/adoption
收益或 installed-host 资格。初次 reader 选择和缺 bundle 的准备失败分别保留。
Pages 仍有已知且在授权范围外的断链；新 exact-head CI、review/readiness、
maintainer 合入权限、全部原验收与冻结 campaign 仍保持开放。


### 2026-11-09 — native context 组合后的完整 CLI 验证

保留的后续计划现在执行完整 101-Todo CLI lifecycle，补齐报告用例之外的真实路径。
提案 `bb65769cc` 上源码和隔离 wheel × File/default-SQLite 四条路径均通过。
下一目标 main 快照 `3b16b8491` 加入 native preparation/navigation 改动；
组合 runtime `9baeafd8a` 重新构建 wheel 后，相同四条完整 lifecycle 也通过。

每个 case 保留原 immutable 合成 fixture 与独立校验的 sum45 artifact、完整
source/lease 拒绝、重放和恢复检查、四种 committed receipt、一次 debit、
不变的完成时间和两个已观察到的正常 native exit。既有 scalar/source-basis
owner 字节不变；本次盘点中存在的 tracked wheel 条目全部匹配该 runtime 源码。
七项 progressive-context 合同用例与 typecheck 通过。初次旧 frontend bundle
触发的 wheel 构建拒绝，与重新构建后包的验证分别保留。

本次是记录中源码/包快照的组合正确性。不重跑或重标签冻结成本、attribution 和
normal-bytecode campaign，不证明当前 browser/installed-host、PostgreSQL
host/wheel 或完整边际成本/adoption 收益。新 exact-head CI、review/readiness、
maintainer 合入权限和既有域外 Pages hold 分别保留。原任务及全部原验收保持开放。

### 2026-11-09 — 最低支持 Python 版本的完整 CLI 验证

CPython 3.11.15 在 `15c937821` 上完成 101-Todo 源码/隔离 wheel ×
File/default-SQLite 四条完整 lifecycle，补充此前的 3.13 资格。目标 main
`a3b589610` 随后更新 context reader 与结算指引；组合 `220275f6a` 对这份新输入
及重新构建的 wheel 再验证相同四条路径。两组均保留相同 immutable fixture、
独立校验的 sum45 artifact、原完成时间、完整 source/lease 拒绝及重放/恢复行为，
每个 case 四种 committed receipt、一次 debit 和两个已观察到的正常 native exit。

前一组合的源码与 wheel 分别通过九项 canonical-source 用例；当前组合另在两个
distribution 验证合并后的报告 CLI caller。已加载的 wheel 模块保持包来源；新 wheel
中直接存在的已盘点 tracked 条目与源码字节一致，不以盘点数量作收益比较。冲突协调
保留上游 module baseline、不变的 127 上限、显式 transaction detail、capability
优先级及原 source-binding 篡改负例。

私有 verifier 初次 Python 语法失败和旧 bundle 构建拒绝仍属于准备失败，与 runtime
资格分别保留。前一 head 的合成 200 项 lens-switch CI 预算失败仍为 hold；本次 CLI
检查不证明 browser 性能。不重跑或重标签冻结成本、attribution、normal-bytecode
campaign。I3/minor-release、M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3、
context/model/split-install、PostgreSQL host/wheel、installed-host、完整成本/adoption
及全部历史缺口保持开放。exact-head review、必需 CI/readiness 和 maintainer 合入
权限仍分别适用。

### 2026-11-09 — 最新 main 的必需 Vision 拒绝资格

提案接入 `main@aa475b29f` 及其已合入的 #8388 资格修正，集成 head 为
`2dd6c3d97`。现有资格适配器为必需 Vision 保留原生 `refresh-state` 的拒绝输出，
同时保留非零退出状态和原有反馈大小边界。缺少 stride 时不能追加写回或扣额；
调用方可以读取结构化拒绝、纠正同一份草稿，再结算一次。typed writer 继续拥有决策。

在 macOS arm64、CPython 3.11.15 和 Node 22.22.3 上，现有完整必需 Vision 家族
在源码和隔离 wheel 中各通过 35 项。固定到 `a3b589610` 源码的旧适配器在新增的
缺 stride 纠正用例中失败。初次对照因 pytest 插入 checkout 路径而意外加载候选，
该通过不能作为基线结果；纠正后的对照及 wheel 运行核验了全部已加载 LoopX 模块的
包来源。这些是脚本控制的合成原生 CLI 资格用例，不是真实模型结果。

wheel 的 1,921 个直接对应已跟踪 ZIP 条目字节匹配，包含资格适配器；不与历史盘点
方法比较数量。`read_transaction.ts` 和 `summary_item.py` 保留 `86d08a01c`
已验证的字节，先前完整 101-Todo File/SQLite 源码与 wheel 生命周期及 PostgreSQL
API/store 证据继续保留，不重跑或重标签。集成 head 的标准 premerge 通过六项直接
检查及十九项选中检查。此次有界重构审视无需再添加生产抽象，现有 writer 和反馈
边界已拥有该行为。

针对 `86d08a01c` 发布的 review 仍是该 head 的历史证据；更新后的提案仍需当前
head 的 CI、review 和 merge readiness，本次资格不授予合入权。原事务、I3/minor-release、
M1/#8011、完整 R0/V10/R1、D2/D5/D6/D3、context/model/split-install、
PostgreSQL host/wheel、installed-host、完整成本和 adoption 缺口及全部冻结负面结果
均保留。不宣称最小内核完成或完整路径收益。

### 2026-11-09 — 在 quota 重建中复用新鲜 canonical 字段摘要

提案接入 `main@c44eb156d`。完整 canonical 读取已为每个投影后的 Todo 字段
返回摘要；quota 重建现在复用同一次新鲜进程内读取生成的摘要，避免再次对整个
字段计算摘要。现有 selector 路径保留直接摘要比较。完整克隆、不可替换的 provider
事实、来源修订、角色检查及调用方改动拒绝仍由原 TypeScript owner 负责。该摘要
不来自调用方授权，也不跨请求保留；完整 supplied-source fallback 不变。没有新增
协议、配置或 provider。

当前源码与隔离 wheel 各通过 24 项完整来源用例；76 项相邻规划用例、23 项原生
读取用例及 control-plane 类型检查通过。交换 user/agent 摘要会被拒绝，后续合法
配对仍成功。同一反例在当前 main 基线上通过；隔离源码副本移除摘要拒绝后，File
和 SQLite 两项均失败。所有已加载 LoopX 模块均核验所选源码或 wheel 来源。
当前 main 与候选源码的 File/SQLite CLI 生命周期，以及候选 wheel 的 File/SQLite
生命周期，均保留同一 101-Todo 夹具与 sum45 产物、原完成时间、拒绝／重放／重启、
四类 committed 收据、一次扣额及每项两次正常原生退出。首次打包因 frontend bundle
过期而拒绝；由现有 owner 重建后，新 wheel 构建通过。这些是正确性检查，不进行
成本对照或重跑冻结实验。全部既有验收缺口与负向证据继续开放；新 head 仍需新鲜
review、CI 与 merge readiness。


### 2026-11-09 — 接入前瞻 M2 资格的内核集成

原 #8372 分支接入目标 main `6787fc8d5`，包含 #8433 的前瞻 Dashboard 资格规则。
内核 runtime `17d85c971` 通过源码和隔离 wheel 各 53 项 Python、十二项 focused
TypeScript 与类型检查；1,921 个直接对应已跟踪 wheel 条目字节一致。相同六条
101-Todo 基线／候选源码和候选 wheel × File/SQLite 完整 CLI lifecycle 保留原
夹具、sum45、原完成时间、完整 scopes、四类收据、一次扣额、重启／拒绝／重放
及每项两次正常 native exit。真实隔离 PostgreSQL16.15 store391/service11
零跳过通过；相同的当前 PostgreSQL 来源保护 harness 在 base/head 通过，移除
primitive 保护的 mutation 则在预期拒绝 oracle 失败。标准 premerge 通过六项
直接检查和十九项选中检查。本地 runtime 使用 CPython3.13.5；CLI 和 mutation
资格使用 Node22.22.3，focused 源码及完整 PostgreSQL 套件使用 Node26.0.0。
初次打包、fixture import 和 mutation snapshot 的准备失败分别保留。

本次验证内核集成正确性，不重跑冻结成本、attribution 或 normal-bytecode
campaign，不证明 Linux browser 恢复、installed-host、PostgreSQL host/wheel、
完整边际成本或 model/team adoption。全部历史失败与原验收继续开放；前瞻 M2
规则不会把历史冻结失败改记为通过。未变 head 的 review、必需验证与新鲜 merge
readiness 继续分别约束交付。

### 2026-11-09 — 发行资格分片提案

两次 head 未变的 Release Artifacts 运行在约 28 分钟后通过完整的 86 场景
packaged 浏览器目录和原 companion 流程，随后在既有 30 分钟 job 总限下于
snapshot 资格阶段取消。构建出 snapshot 不等于取得资格；这两次取消和此前
全部冻结延迟失败仍保留为失败。

S12 发行资格提案将 package/sdist/已安装 frontend/DSH 与 snapshot 检查和
三个隔离浏览器分片分开，复用已有 catalog、分片 selector 和 packaged
catalog/team/craft 命令。每个原场景和 companion 仍为必需；craft 命令还包含
它已有的 command-palette 检查。这改变发行浏览器并发，并重复依赖安装和
静态检查；job 总限、场景期限、M2 阈值和六条共享 Python 验证通道不变。
首次完整 Linux 资格验证
[run 42633152296](https://github.com/loopx-project/loopx/actions/runs/42633152296)
中，prepare 用时 1m43s，浏览器 worker 为 12m46s/9m25s/7m29s，最终门为 22s。
86 个唯一场景和必需 companion 全部通过，snapshot 检查完成。被测 synthetic
merge `ef048a32c` 与整合提案 `5a9fd8020` 的完整 tracked source tree 相同；
该证据只验证这个 candidate。后续 main 整合改变 product source，需重新取得
packaged 资格。完整 CI 成本和可比条件下的端到端边际成本收益仍未取得资格。

后续完整 Linux 资格验证
[run 42649914601](https://github.com/loopx-project/loopx/actions/runs/42649914601)
通过全部 86 场景、team/craft companion 与 snapshot 检查。其 product source
`4747408cc36067a9175bd4a00c440466c316ffc3` 与提案
`9b4ab03a96eff9c3866193819d7da50945912db7` 的完整 tracked tree 相同。
下载后的最终回执与 bundle 校验独立确认共同来源、目录覆盖及 package/runtime
digest。后续整合已合入的内核工作及当前 main 再次改变该 product tree；整合提案
仍需取得自己的完整资格。独立的带覆盖率源码 M2 失败仍为失败；
[#8456](https://github.com/loopx-project/loopx/pull/8456) 的前瞻测量修复不重分类
该运行，也不替代本提案的精确 head CI 和 merge-readiness 门。

Worker 安装同一个 candidate wheel，只采用 producer 精确选定的 fixture
commit/path。源码侧 fixture authority 还获得从同一 wheel 复制的预构建页面，
并对 product source 验证，不自行重建。首次分片运行漏供这些源码资源，
两条 research companion 路径失败；该失败运行单独保留。Package、checksum、snapshot 和 source digest 加 candidate
artifact id 将最终 `build` 门绑定到成功的分片回执。门禁导入真实 catalog
而不启动浏览器，在 attestation 或既有 validated-bundle 上传前拒绝缺失、
失败、未知、重复、分片错误场景、错误来源和不完整 companion。仅发布恢复保留
原成功 build 与 immutable asset 检查。本地负例资格和完整 GitHub runner
资格分别记录。

这是最小化内核工作的发行流水线资格修复，本提案不改 kernel 代码或性能归因。
合入仍需精确 head 评审、必需验证和 fresh ready=true。原任务以及
I3/minor-release、M1/#8011、R0/V10/R1、D2/D5/D6/D3、
context/model/split-install、PostgreSQL host/wheel、完整成本和 adoption
缺口继续开放。

### 2026-11-11 — 整合主线的打包发行资格

完整 Linux Release Artifacts
[run 42905934918](https://github.com/loopx-project/loopx/actions/runs/42905934918)
（attempt 1，来自 [#8531](https://github.com/loopx-project/loopx/pull/8531)）
验证了整合源码 `main@b89a574b05a3e7d5920a8e7184476dfb7b041790`。
Bundle 的 product、browser-fixture 与 workflow commit 均为
`3606315c86f907961b0c26d4f1a1dad6bbaed8cf`，没有 fixture override；其完整
tracked tree 与该已合入 main 相同。这补上了内核和发行流水线整合后的 packaged
资格缺口。

对最终产物 `13807484684` 的离线核验将下载 ZIP 与 GitHub 的 SHA-256 比较，
将每个 package/runtime 文件与 `RELEASE-SOURCES.json` 比较，并校验两份包的
checksum manifest。三个 browser receipt 均绑定 candidate artifact
`13806066680` 和同一个 release-source digest。与真实源码 catalog 和分片
selector 比较后，确认 29/29/28 条目恰好覆盖 86 个唯一通过场景。
Team、craft、command-palette 和 navigation companion 通过。Producer 还通过
干净 wheel 安装、sdist 重建、已安装 frontend、DSH 安装形态和 runtime 资格，
覆盖 upgrade、doctor、skills、frontend HTTP、license notices、无需 frontend
build tools 运行，以及 activation 失败后的恢复。

这次已完成的普通 packaged M2 观察保留 200 项、五种切换各三个样本和十五个
long-task window。五个中位数为 336.34/313.05/441.09/200.01/194.94 ms，
最大值 601.68 ms，没有样本超过 1,000 ms 目标。现有普通 median <1,000 ms
和每个样本 <2,000 ms 的规则通过，未修改阈值。这不是新的带覆盖率测量，
也不是成本或内核归因对照；历史冻结失败及其原始观察保留原有状态。

该证据验证指定整合源码的 Linux packaged 路径，不覆盖后续产品改动。
此次 pull-request 运行跳过 attestation、release upload 和 PyPI 发布，
不能据此宣称已公开发布或当前 installed-host 已升级，也不证明其他全部 CI
结果或完整 CI／边际成本。原任务以及 I3/minor-release、M1/#8011、R0/V10/R1、
D2/D5/D6/D3、context/model/split-install、PostgreSQL host/wheel、
installed-host 和 adoption 缺口继续开放。后续资格仍由这些原 owner 和验收
边界承接；最小化内核工作尚未全部完成。

### 2026-11-11 — Canonical snapshot 失败来源

分页和完整 snapshot 读取将持久化 Todo 契约被拒绝
（`canonical_snapshot_projection_invalid`）与请求参数无效区分开。现有 typed
validator 仍拒绝未知字段清单和无效记录摘要，保留原始原因，不返回部分记录或
legacy fallback；已支持的历史字段清单仍可读取。这是既有源传输入口的诊断
修正，不授予旧 reader 前向兼容能力，也不升级已安装 runtime。

真实 File/SQLite RPC 与 CLI 回归，以及隔离 PostgreSQL 读取，验证该失败边界
且不重写 authority。原 installed-host 资格、完整成本和 adoption 缺口继续开放。


### 2026-11-12 — 独立 core 产物与安装诊断

可选的 `packages/loopx-core` 源码构建器复用 tracked 运行时源码，生成可独立安装的
wheel，省略 Chat、web 资源、canary 集群及完整 CLI facade。它仍保留现有内建能力
和宿主兼容导入。这是 R1/M3 的 core 产物前提，不代表完整最小闭包已合格，也不
发布发行版本。

安装后任务沿用 generic CLI 宿主、独立产物验收及 canonical 硬租约。最初准入的
key/version 经共享 TypeScript preparation 和已校验的 Turn context 传入既有
完成 owner。进程丢失后恢复已确认结果；重放不再次调用宿主或扣额。租约更新／
释放或产物无效时拒绝完成，不产生状态写入和扣额。

深度安装诊断按实际发行物的 RECORD 和必要入口检查。健康 core 通过，未登记的
启动器或缺失运行文件仍失败；普通发行物仍要求完整 CLI。core 通过现有 handler
执行目录列出的 history action，并在缺少完整 facade 时解析全局版本选项。安装
资格覆盖默认 SQLite 和显式配置的 File 硬租约；无 Node 时可导入和查看帮助，
受治理任务保留既有 Node 前提。普通发行物的 CLI 和安装兼容有独立配对检查。
package-smoke 工作流执行这套安装后验证。

保留 D1-D7、原有里程碑、历史失败与验收边界。workspace/meta 发行、完整安装／
加载／context 闭包、独立 Standard 包、其他宿主／平台采用、配对模型效果和完整
成本仍开放。合成任务与恢复证据不证明模型收益，也不代表已升级当前宿主。

### 2026-11-12 — Replan 只消费所需 Todo 视图

Replan 写回调用方现在通过既有 quota 读取事务取得两个角色摘要及完整 Agent
terminal 行。既有 TypeScript 显示 owner 接受显式视图子集；省略选择时仍保留普通
quota 的全部三种视图。该调用方不再生成 User source-summary 行、未消费的 Agent
视图及预建 Vision 等待事实。来源引用、完整内联来源回退及 provider 错误诊断仍由
既有 owner 处理；存储、lease、scheduler 及 quota 扣额规则保持原语义。

以不可变基线 `0abd0dc27` 对照候选代码 `46f844e9d`，复用同一隔离混合状态夹具，
在真实 File／默认 SQLite 上每种负载执行六组交替配对。额外 320／3,500 条 Agent
Todo 时，SQLite replan 资格检查中位数为 93.2→83.3 ms、612.3→572.0 ms；包含
canonical 读取与资格检查的中位数为 139.4→130.0 ms、900.9→857.0 ms。严格 refresh
dry-run 拒绝路径的中位数为 142.4→135.1 ms、934.4→911.8 ms。零条额外 Todo 时，
该 refresh 中位数为 28.9→29.3 ms；保留这项观测，不推断噪声或因果。夹具包含
最多 128 条 User 行。归一化后的义务及拒绝详情一致；减少 RPC 次数本身不能作为
验收依据。70 项聚焦生产 provider 与传输检查通过，覆盖完整 terminal 尾部、
过期／缺失来源及普通显示默认值。

保留 #8624 原 head 及原标准下的 SQLite 负面资格结果（83.0→91.2 ms、
605.9→704.1 ms）。这份新范围结果只证明所消费 replan 读取的成本资格；不能从
这些计时推断完整 CLI 进程、持久写回、模型及端到端成果成本。完整最小加载／
上下文闭包、R1/M3、独立 Standard 包、workspace／meta 发布及安装宿主采用仍未
完成。


### 2026-11-12 — Dashboard 源码验收执行真实后端旅程

源码 coverage 现在通过真实的一次性 Chat 服务执行既有首次 Goal 和运行就绪旅程。
源码与打包分线均已准备经校验的 Chat bundle。声明需要后端的旅程必须完成真实后端
断言后才能记录 PASS，提前返回不能满足该条件；其他有合理 coverage 边界的场景
保留原有行为。共用浏览器代理在既有至多三次 GET/HEAD socket 恢复中纳入 EPIPE，
仅识别类型化 errno 或 Playwright 首行 socket 错误，不重试 HTTP 写请求或任意诊断文字。
研究记录搜索检查将提示关闭绑定当前键盘按钮，并要求关闭后保留原查询。

固定源码 `eedfc5e24` 在本地 macOS/Node26 下完成全部 86 场景资格：三个互不重叠的
29/29/28 分片共享同一完整场景目录和源码 tree，后端读回标记及非空合并 coverage 均已核对。
本次通过的分片用时 1,943.74 秒；此前失败运行、准备和修复仍分开记录。这是验证成本，
不构成完整模型、服务和人工成本比较。

同版本 200 项 M2 对照保留全部 15 个样本和长任务窗口。普通模式五组中位数为
225.77/172.99/310.77/128.45/146.52 ms，最大 366.08 ms；coverage 模式为
328.21/371.20/332.49/174.73/173.02 ms，最大 421.22 ms，两者超过 1 秒目标的
样本数均为 0。完整分片中的 coverage M2 也通过：中位数
325.35/270.28/326.59/164.22/166.47 ms，最大 463.88 ms。真实注入 1,200 ms 的重复看板
停顿被普通资格正确拒绝：慢组中位数 1491.77 ms、最大 1519.98 ms，全部 15 个
样本和窗口仍保留。既有严格边界及缺失样本负例通过，预算未变。

此前源码分片的 EPIPE、查询丢失和临时目录失败分别保留记录；历史冻结的 1.162 秒失败
及已取消运行也保持原判定。
本项修复源码验收执行与有界传输恢复，不改变渲染阈值、内核运行时或 authority。
首次 Goal 夹具使用退出式 Agent：登记及如实显示失败 Turn 的恢复不证明自然语言任务
完成或模型收益。完整最小闭包、R1/M3、workspace/meta 发布、安装宿主采用及完整成本
验收仍保持开放。

### 2026-11-13 — 独立发行版的自然语言启动到达既有 owner

独立安装的 core 现在将 `start-goal`、`bootstrap-command-pack`、
`register-agent` 和 `bind-agent-thread` 路由到普通产品既有的 registrar 与
handler。此前四条命令都依赖被排除的完整 CLI facade。目标预览保留完整原文，
保持只读，并返回同一套有序的 bootstrap、身份、Todo 与 host/guard 事务。
新身份注册、冲突拒绝、global/source 回读和绑定会话复用在默认 SQLite authority
上真实执行。没有新增 engine、模型服务、权限规则或 capability 激活。
普通安装仍保留完整 parser 的诊断 fallback；独立 core 的非法参数仍返回既有明确
unavailable 错误。

安装验证通过 16 项检查，覆盖 guided 身份旅程及既有 SQLite/File 执行、产物拒绝、
续租/释放后的完成拒绝和已确认结果恢复/重放。八项聚焦对照与普通 parser 和预览
一致。原始独立安装基线 wheel 拒绝自然语言启动，修复 wheel 能执行这条路径。
这是真实可执行的行为差异，不是仅修改帮助文案。

另一个隔离 generic-CLI 任务由当前 host 模型实际完成：依据 catalog/movement
源文件生成可复用库存脚本及 JSON/Markdown 报告，保留精确小数计价和排除记录。
独立 validator 接受结果。typed result 已入 journal 后杀掉 driver，再用原始 key
恢复，可完成 Todo 并只结算一次，且不再次调用模型；再次重放无状态或额度副作用。
更早一次任务前尝试在等待模型输入期间过期，其 unknown-outcome 恢复拒绝仍保留。
整个实验共两次 host transport dispatch、一个已确认模型结果，不能宣称全实验
只 dispatch 一次。

三对本地 loading census 使用 macOS arm64、Python 3.13.5、Node 26.0.0，
新 Python 进程保留普通文件系统/bytecode 缓存，每进程再运行两次 warm 调用。
core 顶层帮助加载 8 个模块，guided 帮助/预览加载 186 个；直接普通 full-facade
对照为 1,151/1,164 个。预览出现两次 subprocess 和九次 Python socket audit
事件，process/IPC 成本必须保留在闭包清单。源码/安装的缓存与启动条件不同、仅三
个样本、仅 Python audit 范围及缺失模型 token 计数，使其不能支持性能或完整
V10 结论，也不批准 D2 或 minimal preset。

源码回归因本地磁盘耗尽而中止的尝试仍为失败，恢复后的运行单独记录。
完整最小 install/load/context 闭包、initial/revised/compacted/lost-ACK 上下文
资格、受支持 host 采用、完整 V1–V5/V10、R1/M3、Standard/workspace/meta 拆包
和完整成本仍未完成。回滚路由切片时保留 task authority 并协调原始尝试。
既有 exact-head 评审、premerge、双语要求、原始失败、预算及独立研究/发布授权
继续有效。

### 2026-11-13 — 启动元数据隔离与安装上下文恢复

默认目标启动的帮助和预览此前只是为了渲染命令提示，就导入 issue-fix 执行、
candidate preflight 和仓库分析。既有命令模板现在放在 capability 自有的元数据
模块；bootstrap 与 catalog 消费它，workflow 模块保留原 Python 导入接口，显式
issue-fix 路由仍带完整 preflight 契约。独立安装 base/head 的启动输出逐字节一致；
默认帮助/预览的 LoopX 模块加载为 186→171，显式路由为 186→179。
模块数量本身只证明加载边界缩小，不是速度收益结论。

macOS arm64、Python 3.13.5、普通 OS/bytecode 缓存下，每项 workload 运行
30 对交替的独立安装 base/head，首次入口的中位数/p95 为帮助
314.73/362.55→181.25/303.01 ms，预览 542.36/670.44→311.08/572.66 ms。
每进程随后两次调用的 warm 中位数为 0.66→0.54 ms 和 11.83→8.94 ms；
峰值 RSS 中位数为 43.22→32.46 MB 和 46.05→36.18 MB。全部完整输出一致，
预览仍为 52,727 字节。保留 head 预览最大值 1,092.15 ms。首次入口包含导入，
本记录不属于 OS-cold 或完整 V10 资格。源码回归通过 348 项，1 项因缺少原生
Windows/PowerShell 环境跳过；最终安装 wheel 通过 20 项。早期失败尝试仍保留。

安装 CLI 验证通过既有 context-project/read/hook owner 交付完整源或明确恢复：
关闭功能时保留原始值；部分交付和丢 ACK 不能建立基线；未变化/修订视图保留完整
约束；独立 PostCompact 使基线失效；捕获文件被修改时拒绝读取。完整 delta framing
更贵时，warm 传输可选择 full。同一测试也在原独立 core 安装上运行，资格化既有
安装协议并补充回归边界，不新增上下文 owner。原生事件为夹具，不证明模型保留
上下文或宿主采用。

带追踪的 SQLite/File Turn witness 保留 Python、真实 Node loader 和 RPC 观察。
选定 Turn 入口仍加载 525 个 Python 模块，其中 111 个 capability 模块；Node
runtime 仍保留 eager 的共享 typed 支持。完整 work contract 与传输成本继续保留
在清单中。本诊断不资格化 endpoint/网络完整性、OS-cold 成本、模型 token 或完整
最小组合。保留早期检查失败及独立的启动/上下文边界；完整 V1–V5/V10、R1/M3、
独立 Standard/workspace 包、安装宿主采用及覆盖原始目标的完整回归仍由既有
owner 和验收约束保持开放。

安装门暴露了 project skill 的支持示例链接离开安装包的问题。链接现在指向经字节
核验的公开源文件，并固定不可变 revision；原流程、同意边界和检查范围条款保持
原义。它是按需支持资料，不是 runtime fetch 或新 authority。早期安装门失败和
受磁盘限制的 base 重放，与修复 head 的验证分别保留。

### 2026-11-13 — 安装上下文回归进入 package lane

独立 core lane 现在一起执行 `tests/test_core_distribution.py` 和
`tests/control_plane/test_installed_core_context.py`，pull request 与 main push
的路径过滤器也都观察上下文测试。此前该测试虽通过本地资格验证，lane 却未执行。
独立 workflow 检查在未改动的 base 上因这三处遗漏拒绝，在修复后的命令和过滤器上
通过。干净 Python 3.11.15 wheel 的 1,885 个 tracked runtime 文件与源码字节一致；
安装产物继续与源码测试环境隔离。

补充观察在 macOS arm64/Python 3.13.5/Node 26 上，使用同一独立 core 2.3.0、
隔离 SQLite/File 完整 Turn 夹具、合成 Host，并关闭使用遥测。两个选定 Turn
进程仍加载 525 个 LoopX Python 模块，其中 111 个 capability 模块，发出
220/221 次 Effect RPC。观察到的 1,024 次 Python 连接/地址解析事件均指向
loopback；两个 Node listener 及其 DNS lookup 也使用 loopback。包含 Host
supervisor 在内的四个 Node 进程加载 247 个不同文件。观察器自身的本地连接与
named-import 子进程对照通过；早期不完整观察器与本次核验后的观察分别保留。

这些观察识别了此夹具中的本地 RPC 流量，不证明 OS 全域网络完整性、普通遥测
行为、模型上下文采用、配对性能或完整成本。本交付补齐安装协议的 CI 覆盖缺口，
不选择 D2/D5 或最小 preset。真实 Host 的 initial/revised/compacted/lost-ACK
采用、完整 V1–V5/V10、R1/M3、Standard/workspace 发布及原始目标回归继续开放。
保留早期失败、冻结预算和未变的治理 owner。

### 2026-11-13 — 托管 Codex 准备遵守启动准入

现有内置 Codex CLI 路径可对符合条件的 hard-lease Todo 执行 typed 决策公布的
精确普通领取，随后重读同一 Todo，确认 ready 才启动 Host。这消除了调用方单独
手工领取的步骤，没有选择新的 Host，也没有授予额外权限。此前提案
`d4348db14d51952a74247e3003768a2f969c7216` 在拒绝无效 validator 输入或调度
等待之前仍会写入 lease 和 Turn 回执。修复路径先校验现有 Host 参数，通过同一
TypeScript cadence owner 取得启动准入，再原子保留精确 Turn 绑定并执行普通领取。
启动准入回调位于既有跨 runtime Turn 绑定锁内；竞争请求不能先占用另一 Todo 的
启动预约，再让不同绑定获胜。

准备与执行使用 settlement effect identity 复用同一启动预约。因此，普通领取的
ACK 在 journal 建立前丢失时，可恢复已确认的 lease 和原有未启动预约。历史 Codex
journal 使用的 transaction-key 预约保留精确恢复兼容分支；已启动请求不能借该
分支重复执行。领取结果不确定时，状态副作用报告为 unknown，不能宣称没有写入；领取成功但
后续 Host 步骤失败时，结果保留已确认的领取写入。
已结算的原 Turn 即使 lease 后来被释放，也通过现有 spend precheck
拒绝再次领取，并引导原 journal 回读。没有新增持久化 schema 或治理 owner。

隔离 File 和 SQLite 的真实源码 CLI 检查表明，无效 validator JSON/argv 和调度
等待均保持 lease、Turn 回执、journal 与 Host 调用不变；同一独立 oracle 在此前
提案上失败。普通领取后中断可恢复为一次 Host 执行、一次结算，原 journal key
重放没有副作用。独立 core 安装验证复用现有 package lane 与合成 Codex transport，
使用独立产物 validator 和结果回读。

这仅交付 V2/V3/V4 的可执行启动前置，不等于完整 R1 验收。Host 为合成 transport，
实际模型采用、完整 V1–V5/V10、默认闭包、配对完整成本、渐进产品控制、R1/M3 与
原始 Goal 回归仍未验收。保留原九项要求、此前失败尝试和冻结预算失败；本次没有
推广 minimal preset、provider 默认、包发布或付费操作。

### 2026-11-13 — 完整 Turn 延后加载未选择的 Provider 实现

此前独立 core 的已安装 Turn 在解析 Reward Memory 配置时加载 BM25 和
OpenViking，并在退休治理关闭时加载 anti-entropy 检查器。现在 context-provider
公开导出和原 builtin builder registry 在明确使用时解析实现。绑定归一化与实际
provider 共用纯 OpenViking scope owner，原 URI 语法、公开别名与 Python 序列化
身份保留。Anti-entropy 仍使用原 typed 激活规则和适用 basis，通过这些检查后才
解析 feedback builder。没有新增或放宽 capability、provider 默认值、authority、
claim、stop 或 settlement 规则。

12 项新进程检查覆盖 registry 拒绝／custom builder、真实本地 BM25 检索、绑定
分类、关闭／开启退休治理，以及 attribute/from/star 导出。原 11 项检查在不可变
基线 `441c6077e7c5c8e896ea79562761fd483baf157e` 上有五项加载 oracle 失败，
六项公开导出检查通过。两个安装版本的 16 类 URI 成功／拒绝结果与 protocol-4
pickle 字节一致，跨版本双向读取通过。定义移到纯 scope 源文件，旧模块别名
不能保留声明源码的 introspection 行为。

最终独立 Python 3.11 core wheel 的 1886 个 runtime 文件与源码字节一致，SHA256
为 `bc36c92dd437b9418e21031e185fefd04984c91a30169cbd866a3c712d92b4fa`。
源码 capability 回归通过 628 项。真实隔离 File 与 SQLite 执行提交已检查产物，
且只结算一次；原 key 重放没有 Host、state 或 quota 副作用。含 package root 的
受控完整 Turn 模块数在执行时由 525 降到 516，重放由 438 降到 432；capability
模块数分别由 111 降到 102、56 降到 50。执行 RPC 数仍为 221/220，重放仍为 11。
这些路径中不再出现未选择的 OpenViking、BM25 和 anti-entropy 检查实现。计数只
描述已观测的 Python 路径，不能代表完整最小闭包。

配对安装测量使用 macOS 27 arm64、Python 3.11.15、Node 26.0.0，每个 backend、
阶段各交替运行
五对基线／候选。每次调用启动新 Python 进程；首次执行前通过原 owner 正常停止
隔离的 managed RPC server，使其重新启动，已完成 Turn 重放则复用该 server。
准备与独立回读不计入耗时，OS、bytecode 和 compilation cache 不重置。
Nearest-rank p95 等于五个样本的最大值。

| Backend／阶段 | Wall 中位数／p95 ms，基线 → 候选 | 调用 RSS 中位数 MiB，基线 → 候选 |
| --- | --- | --- |
| SQLite／首次执行 | 2922.5 / 3561.9 → 3006.8 / 4139.9 | 81.81 → 81.25 |
| SQLite／已完成 Turn 重放 | 809.6 / 948.6 → 889.0 / 1415.0 | 44.73 → 44.42 |
| File／首次执行 | 2858.7 / 3005.5 → 2862.5 / 2985.0 | 81.09 → 81.36 |
| File／已完成 Turn 重放 | 645.4 / 679.7 → 692.5 / 773.9 | 44.73 → 44.28 |

40 次调用的独立结果／副作用指纹一致，stdout 大小保持首次执行 4002 bytes、
重放 4548 bytes。候选 SQLite 中位数较慢、尾延迟较高，本轮不取得延迟或完整成本
改善资格。调用 RSS 不包含持久 Node server，block-I/O 计数为零也不能证明没有
文件访问。独立受控 observer 验证 Python import/open/process/loopback 事件，以及
Node module/loopback/exit 事件，但不能提供 OS 级完整闭包、完整 IPC/I/O、model
token、service cost 或人工注意力核算。Host 仍为 synthetic。完整 V1–V5/V10、
真实模型采用／恢复、渐进产品控制、R1/M3、独立 Standard packages 与原目标回归
仍开放，保留原九项要求和此前冻结失败。

### 2026-11-13 — TraeX 续接读取运行时线程

公开 `start-goal` 入口现在通过既有 host-thread adapter 读取 TraeX 的
`TRAECLI_THREAD_ID`。已验证的 `traex-cli` 绑定自动复用原 Agent，无需再次
传入 `--thread-id` 或 `--agent-id`；显式线程仍优先。缺失、新线程、跨 Host 与
冲突上下文保留既有身份边界，Trae App 的绑定作用域仍独立。

隔离真实 File/SQLite CLI 对照覆盖源码、从 `09886ba7a` 构建的独立 core 与
候选 core：36 次调用在普通入口、显式对照／覆盖、未知上下文及重放中保留
目标原文、registry/state 字节与 canonical provider revision。基线仍丢失
ambient thread，两个修复后入口均复用已绑定 Agent。源码回归通过 215 项，
包含跨 Host 与冲突拒绝；已安装 core／恢复／context suite 在 Python 3.11.15
通过 23 项，源码使用 Python 3.13.5，Node 为 26.0.0。1,887 个打包 runtime
文件均与评审源码字节一致。最初夹具设置失败继续保留为失败尝试。

TraeX 激活指引先读取原生 Goal 工具或当前 Host 上下文，在续接时保留已有
active Goal 的原始目标和用户停止状态。仅当获授权的新启动确认不存在原生
Goal 时才可创建；工具／上下文不可用时仍保留可见 `/goal` 手工恢复路径。
这取得入口与指令修复证据，不能证明模型采用了指引。未新增生命周期 owner、
模型执行、preset 或已安装 Host 发布。完整 R1/V1–V5/V10、渐进控制、完整成本
证据与原目标回归仍开放，保留全部原始失败。

### 2026-11-13 — 原记录的披露关闭后停止续读

可选的原工作会话在详情响应中断时，只按同一来源、记录与字符偏移重试一次。
关闭单条记录或其工作过程分组，会取消该读取及隐藏重试；重新展开保留已读文字
与位置，由用户显式继续。原 HTTP reader、指纹校验与原生输入边界仍承担各自职责。

未修复候选的真实 HTTP／浏览器反例在关闭后发出了第二次读取。源码与隔离的
Python 3.11 wheel 现已通过真实 LoopX reader 和合成 Desktop peer，验证两层关闭、
成功响应断流恢复、拒绝响应断流不重试及完整 Unicode 回读。这只资格化可选记录
读取，不证明模型上下文保留、受管理 V4／R1、拆包 M3、已安装 App 采用或完整成本。
原目标回归、未完成验收、权限边界与冻结失败全部保持。

### 2026-11-13 — 独立 core 续接保留原安装

此前即使调用者选择隔离的 core interpreter，独立启动仍返回环境中的 `loopx`
命令；执行完整命令可能通过 PATH/PYTHONPATH 导入其他安装。旧安装测试丢弃
executable 后重放参数，掩盖了失败。Core 生成的命令现在通过既有 invocation
owner 保留同一环境的 Python，并使用 `-I -m loopx.entrypoint`。显式全局
`--cli-bin` 与普通发行默认行为保持；非法参数保留完整 parser 诊断。
多 Goal 选择也复用同一命令 renderer。

Core module 诊断核验实际导入的 distribution、记录中的 module，以及真实存在
且有记录的 console 入口。Version/catalog/help probe 使用同一隔离 module
调用，foreign PYTHONPATH 无法静默改变被检查的 artifact。缺失或重定向的 module
记录、缺失 console 记录／文件、未记录 launcher 与缺失 runtime 文件仍拒绝。
安装资格测试执行完整的返回激活与诊断命令，使用 foreign-package sentinel 和
显式 route 对照，并覆盖既有 SQLite/File 恢复与 context 检查。本轮修复命令来源，
不新增生命周期、权限、配置或 protocol owner。

完整 R1/V1–V5/V10、真实模型恢复／采用、渐进产品控制、最小依赖闭包、
M3/Standard/workspace/meta 拆包与完整成本验收仍开放。本轮不升级已安装 Host、
启动模型服务或从协议夹具推断效果。原九项要求、原 Goal 回归及此前失败全部保留。

### 2026-11-13 — 成功工作在同一路线上记录新检查点

Generic Host result 边界此前要求新 Vision 使用 `material_replan` 与
`replan_required`。即使已有新成果证据，保留路线的成功任务也只能复用 unchanged
reason。现在显式 `path_delta_mode: unchanged` 可携带既有 Vision packet，
其 `path_delta.outcome` 必须为 `continue` 或 `no_change`，且不能同时声明
unchanged reason。既有 TypeScript Vision、任务、验证与结算 owner 仍决定持久
效果；material replan、停止决定、非法 packet 与矛盾声明继续拒绝。省略 mode
保持旧推断，generic 与 Codex 指导说明同一边界。

一个既有隔离库存任务通过 stdio adapter 使用当前模型工具，以及 Python 3.11
standalone core artifact 与 SQLite。更正输入得到数量 22/10/5、价值 185.25，
此前为 172.25。独立检查核验了 13.00 差额、全部六条排除记录、可复用生成器与
五份原文件保持不变。Normalized result 写入 journal 后，SIGKILL 中断 driver
与 validator。原 Turn 从 validation 恢复，没有再次调用 Host，完成 Todo 并
只结算一次；完成后的 replay 没有增加 Host、writeback 或 spend。
首次非法 completion/replan reply 与三次 resume setup 拒绝保留为失败。
实际有两次 Host dispatch、一次模型工作；被拒绝的 reply 未冒充已确认工作。

源码 executor/driver 检查与 Codex/dsh 回归通过。安装态 File、SQLite 检查保留
legacy completion，并核验两种新 checkpoint outcome 与 replay；首次过长的
inline 测试命令触发既有上限，夹具随后采用普通 script path。未改命令预算、
权限、默认行为或历史 receipt。本轮只资格化所触及的成功／检查点与恢复路径，
不关闭完整 V1–V5/V10 或 R1。完整 context retention、最小闭包、渐进产品控制、
可比完整成本、M3/Standard/workspace 交付与原 Goal 回归仍开放。原九项要求和
冻结失败全部保留；没有升级已安装 Host 或模型服务。

### 2026-11-13 — 任务呈现省去外层执行器数据

当前内置模型 renderer 从 adapter 请求内已校验的完整 v1 来源派生
`loopx_turn_model_context_v0`。任务要求、范围、必需读取、backlog 和未知新增信息
保留；来源绑定、effect projection 与 scheduler/automation 执行数据留在外层
执行器。指向该 owner 的既有 replan 编写与 Explore 指导引用在隐藏 owner 前展开，
保留来源数字和文本。模型视图不能代替签名来源。通用 adapter stdin、私有 journal、
原始 reader 来源关系和仅携带 envelope 的历史 v0 renderer 保持既有合同。

同一捕获库存请求上，交替执行七组新进程配对 probe，每版另有 49 次 warm render。
模型 JSON 为 44,841 对 34,986 字节，Codex prompt 为 48,724 对 38,859 字节。
Warm median/p95 为 9.31/11.27 ms 对 9.02/10.26 ms；较早候选批次为
9.18/11.46 ms 对 10.09/12.44 ms。最终新进程 median/p95 为 25.03/435.52 ms
对 23.81/1,405.85 ms；保留候选较高尾部及批次波动，不宣称延迟改善。OS/file cache
为 warm。Model tokens、Node startup/IPC 分解、service/tool 成本和人工注意力仍
不可得；这些 probe 不构成完整成本或长程收益结论。

当前原生模型通过 stdio、Python 3.11 standalone core 和 SQLite，在既有隔离
库存 Goal/Agent 内完成新的流水审计。独立核验全部 14 行、六条排除记录、数量
22/10/5 与价值 185.25。故意更改报告后会拒绝且不覆盖审计产物；11 份既有输入、
生成器及报告保持 hash。前一阶段已关闭，任务准入首先拒绝；正常的证据关联 replan
建立审计阶段后才进入新 work Turn，额外 replan 结算保留为成本。完成后的 replay
不调用 Host、不 writeback、不 spend。源码与安装态 Host 检查使用合成 provider，
与该原生模型任务分开；最初兼容性失败和修复前悬空引用反例保留为负面证据。

更广的 Ark adapter 回归最初有八项 SDK 模型输入检查失败、817 项通过。旧检查仍
要求模型呈现含有 `work_contract`，其后的原 receipt 回放断言没有执行。配套检查
复用任务／来源 parity oracle，要求操作方原输入及其 receipt binding 不变，并在
任何 provider 调用前拒绝把模型视图版本用作来源。矩阵保留携带完整来源的历史
v0/v1 输入及 running/completed 回放。可选 CI 命令在首个测试失败后退出，让
traceback 在既有十分钟上限前显现；通过路径仍选择完整用例。基线与候选同在该
上限取消，不能证明失败无关或语义成功。SDK HTTP 夹具资格化 transport 与恢复，
不构成真实云端模型采用证据。

本轮只资格化 V4/R1 的任务呈现切片。完整模型上下文保留、默认最小闭包、渐进
产品控制、可比完整成本、V1–V5/V10、R1/M3、Standard/workspace 发布与原 Goal
回归仍开放。原九项要求和冻结失败全部保留；没有升级已安装 Host 或模型服务。

### 2026-11-14 — 独立安装的后继恢复保留原结算

真实依赖的后继任务可以在 replan 准入后创建。新增依赖不能抹去原生命周期来源，
导致该 Turn 无法结算。现在只有精确绑定原 review、owner/actor 一致、更新时间
明确晚于准入的后继才允许重建原来源；既有 trigger/digest 检查继续生效。
既有 runnable-successor ACK 随后可结算原 Turn，无需改写已完成 Todo。
缺失或非法 ACK 保留生命周期恢复；执行后继仍须新准入。空 operation inbox
同时延迟加载可选 workspace action-store；已有 action store 缺少 provider
继续显式失败。

当前原生模型通过 stdio、独立构建的 Python 3.11 core 和 SQLite，在既有隔离
任务内执行。正常创建后继、写回 Vision 和原 replan 结算恢复了受阻路径。
完整初始及未改任务读取产生可复用库存 close/check 工具与核验后的 JSON/Markdown：
14 笔流水、六条排除记录、价值 185.25。错误的 185.26 候选被拒绝，原产物未覆盖。
Controller 生成的修订在新任务／来源交付前对模型封存，新增六行。独立核验全部
20 笔流水、九条排除记录、数量 31/7/7、价值 250.20 和 64.95 差额；原截止日、
Decimal 规则和此前产物全部保留。

模型拒绝用旧任务执行修订，读取完整当前要求与输入。这证明模型的来源选择决定，
不构成自动拒绝过期输入的 gate。修订调用方的 JSON 回复被暂不读取后，精确的
已提交 journal 恢复没有新增 Host、writeback 或 spend；历史消费计数仍为一次。
初次原始 adapter 回复在产生效果前因 result binding 失败；受支持的同 Turn
retry 复用既有 result builder。初始有两次 Host 调用、一次产物实现，最终只
写回／消费一次；保留该修复成本。Generic bridge 没有持久 provider session id。
这些检查资格化完整任务恢复和已提交结果 replay；自动 provider 线程保留及单独
控制的真实 compaction 用例仍未测。

源码恢复回归和新独立安装回归覆盖所触及的生命周期及可选 import 路径，包括
非法 owner、时间、binding、ACK fallback 和已有但不可用的 provider。此前
失败测试保留为负面证据，原验收仍开放。完整 V1–V5/V10、R1/M3、默认闭包、
渐进产品控制、可比完整成本、Standard/workspace 交付和原 Goal 回归仍开放。
没有升级已安装 Host、启动模型服务、改写 authority history 或改变冻结预算。

### 2026-11-14 — 已安装组合结算恢复原回执链

PR #8707（`d695b40f633742fb0a5e1cb6b970a56c46dba5e9`，合入提交
`dd44cdcd0c9482bdf135e54ae0b71eb398b4cfe7`）修复 Core 在写回后显式请求的
组合结算。适配器复用 canonical parser、quota registrar 和 registry codec，
核对完整当前 CLI 前缀及原 binding，再执行已打包 entrypoint；原 TypeScript
结算 owner 与产出工作目录继续保留。

独立 Python 3.11 安装的 File/SQLite 对照覆盖默认及显式路由：基线提交写回后
因 facade 缺失而失败；候选恢复同一身份、返回三条回执，replay 后仍只消费一次。
普通 refresh 继续保留待消费步骤，不自动扣额。额外安装对照在 PYTHONPATH 和
工作目录放置导入即拒绝的 foreign module，实际仍选择原 Core。

保留的已安装治理语料在 `78e936f9d1f1a00505c4026ee0a5857de95cee27` 上，Core 与
普通版各有 16 项确认结果；此前资源失败仍为失败。扩大源码回归保留了一项既有
不完整 mocked readback 的失败，同一基线和候选都复现。现有 settled-intent
测试改为提供 canonical identity/result 类型，受影响的 32 项全部通过；没有
放宽生产 readback，也没有改变“独立 intent 不重开 work”的语义 oracle。此前
整组失败不会改写成通过记录。

这只资格化 V2/V3 的已安装结算前提。完整 V1–V5/V10、R1/M3、真实 Host
compaction／线程保留、渐进控制、可比完整成本、Standard/workspace 交付和
原 Goal 回归仍开放。协议 fixture 不表示默认激活、Host 升级、发行或冻结预算
发生变化。

### 2026-11-14 — Coordination 在选定终结路径加载证据适配器

local-authority runtime 将 `ResearchTerminalEvidenceHost` 延后到合法的
terminal lifecycle 请求选中它时加载。普通 coordination 不再加载该适配器
及其研究实现依赖。完成操作仍由现有 typed terminal owner、registry witness、
研究资格检查、writer lock 和资源清理负责；没有新增竞争的工作 schema 或
执行流水线。

新进程故障注入使 terminal 适配器不可用：普通 claim 仍落盘并重放原回执，
非法 claim 保持状态不变，选中的 terminal 请求则明确失败，不产生新回执或
状态变更。保留的基线在同一普通 claim 探针上失败。源码 authority/terminal
整组 93 项通过。同一补丁应用到 C1 的精确候选 #8266（`b15c443ee64d`）和
#8484（`b7e4e01cc423`）后，authority、context 与 recovery 分别有 188 和
193 项通过；后者还覆盖真实 SQLite admission。

这只是对齐 C1 现有 owner 的加载和故障隔离前提。合法 terminal 请求仍会加载
适配器，包括 ordinary Todo 的 guard 随后提前返回的情况。完整工作流隔离、
成对完整成本、真实 Host 采用、R1/M3 以及 C1 writer/consumer 切换仍开放。
这些测试不授权 C1 默认推广，也不证明模型收益。


### 2026-11-14 — 普通任务完成延后研究实现加载

`ResearchTerminalEvidenceHost.qualify` 在原有 Todo 适用性检查之后才加载研究执行与
配置实现。因此，即使任一研究实现不可用，没有研究或重规划适用性标记的 Todo 仍能持久化
完成证据并重放原回执。需要研究资格的 Todo 继续选中原策略和证据 owner；模块失败保留错误且不提交
完成状态或回执。原 source 检查、writer fence、资格判定、CAS 和清理继续执行。

新进程故障用例在真实 File 与 SQLite authority 上覆盖两个不可用实现。两个保留的
基线探针均在普通完成处失败；候选能够完成并独立回读结果，重放时不产生新效果，且
重复的已选中研究失败仍被拒绝。模块可用时，同一组原有 107 项 native 用例全部通过；
候选另通过故障隔离用例，共 108 项。真实研究 CLI 套件通过 11 项。同一 production
改动与故障用例和 C1 精确候选 #8266（`b0b930bfb92a`）、#8484（`b7e4e01cc423`）
组合后，分别通过 190 与 189 项 authority、上下文和恢复用例。独立构建并单独安装的 Core wheel 也通过同一 File/SQLite 故障路径，其 production 模块字节与源码一致；这不验证模型 Host 的安装或采用。

这项交付通过原研究 guard 修复已证明的普通完成依赖故障，复用 C1 的工作契约与
producer/consumer 管线。带研究或重规划标记的 Todo 仍进入原策略检查并加载其实现；策略禁用时也保留
这项待隔离的加载依赖。完整 D2/D5 闭包、真实模型
Host/上下文延续、渐进控制、可比完整成本、R1/M3 与 C1 默认切换仍未完成资格验证。

### 2026-11-14 — 关闭研究组合先解析配置再加载执行实现

既有 Explore 配置模块现在拥有面向已声明标量 mode 的组合策略和未改动的 public-safe 标识校验；研究执行与
证据复用这个 owner，原导出入口继续可用。native 终态资格先检查当前 source 并解析
同一策略，再加载研究执行。原适用性、source、资格、writer、lease、CAS、回执与
清理规则继续由既有 owner 负责。

新进程 File/SQLite 用例在组合关闭时，包括明确 Goal-off 覆盖，不加载研究执行或
图实现也能完成普通与通用重规划任务并独立回读结果。启用和仅设置继承配置的路径
保留已选中依赖故障，不写完成状态或回执。无效 mode 与 scope 标识仍被拒绝，执行
实现不可用不能掩盖配置错误。启用路径依赖失败后，明确关闭策略的当前 source 能以
同一失败操作身份完成一次并重放原回执；旧 source 请求仍被拒绝。保留的两个后端
基线探针都在原加载依赖处失败，同一候选用例通过。

同一 21 项策略和 13 项标识观察保留完整返回值与错误详情。独立构建并单独安装的
Core wheel 通过同一 File/SQLite 故障与恢复路径，production 字节与源码一致。
该改动与 C1 #8266（`b4f958186532`）、#8484（`b7e4e01cc423`）组合后也通过选中
的 authority、上下文与恢复检查，保留其原测试和 producer/consumer owner。
这验证依赖故障隔离，不测量延迟、完整成本或模型收益；配置错误现在有意优先于
不可用的执行 IO。完整所选 D2/D5 闭包、真实模型 Host/上下文采用、渐进控制、
R1/M3、C1 切换和原目标回归仍未完成资格验证。

最终反例评审还拒绝非字符串组合 mode。此前 `["explicit_only"]` 这样的单元素数组
通过字符串强制转换，却生成 inactive policy，静默跳过研究证据。既有配置 owner
现在在 IO 之前拒绝这种错误形状；已声明的标量 mode、默认值和错误语义保持不变。
真实 File/SQLite 负向用例保护这个边界，不改变合法激活规则。


### 2026-11-14 — 策略观察者恢复原始签名来源

- **基线：**`main@8d7d234c6f55`；C1 #8266 为 `c3cb8b2fe7bf`。
- **缺口：**当前 C1 的三条 guided-start 和可见 Goal 选择用例，把必要
  展示页当成原始签名决策而拒绝。原始捕获的物理摘要和完整合同校验均
  通过。同页 Todo 引用及其读取说明改变了展示正文，但保留原来源绑定。
- **交付：**既有测试策略观察者在比较决策事实前，沿展示页声明的不可变
  捕获及摘要读取原来源，复用物理捕获 reader。没有该引用的 inline/off
  输出保留现有行为。生产者、决策、哈希编码、准入、结算 owner 和默认值
  均未改变。
- **证据：**固定 C1 组合的既有真实 guided-start/可见 Goal CLI 用例通过。
  稳定 File/SQLite 回归执行共享 typed 投影，验证原来源相等、完整绑定、
  零扣额，并拒绝捕获字节被改动或缺失，以及完整校验通过但原绑定不同的替代捕获。私有匹配观察者比较区分旧 reader
  与修复 reader。历史失败及未完成运行继续保持失败或未完成。
- **剩余：**本切片修复消费者资格的前置缺口。完整 V1–V5/V10、R1/M3、
  当前 D2/D5 闭包、真实 Host/模型上下文采用、渐进产品控制、可比完整成本、
  C1 默认切换和原 Goal 回归仍开放。

### 2026-11-14 — 禁用 Turn 召回延迟加载执行依赖

- **基线：**`main@4604f938b612`；C1 #8266 为 `79d7ebc81fbc`。
- **缺口：**独立 Core 的 File/SQLite Turn journal 记录召回关闭、无上下文和
  零 provider 调用，但准入仍经召回 package 导入执行 hook 和读权限实现。
- **交付：**既有召回和 checkpoint 函数在实际调用时导入这两个实现。实验解析、
  ingest reconciliation、schema owner、导出函数标识、权限和 C1 composition
  保留既有 owner。共享编排和默认行为均未改变。
- **证据：**禁用 helper 用例和真实安装 CLI 用例在基线上失败、修复后通过。
  capability 回归覆盖启用召回、权限拒绝、provider 故障、去重和 outcome lifecycle。
  独立 Core 的安装、执行、中断恢复、重放与上下文套件在主干及固定 C1 组合上
  通过，runtime 字节与声明的源码相同。观测到的首次 File/SQLite Turn 从
  516 个 Python 模块降为 514 个，capability 模块从 102 个降为 100 个；
  RPC 仍为 223/222，重放仍为 432/50 个模块及 12 次 RPC。四次 Node 启动
  分别是两次版本探测，以及两个已加观测的 runtime/Host bridge 进程。
  这只验证加载边界，带观测开销的耗时不构成延迟或完整成本资格。
- **剩余：**内置 capability 打包与完整 D2/D5 闭包、真实模型/Host 上下文采用、
  渐进控制、可比完整成本、完整 V1–V5/V10、R1/M3 和 C1 默认切换仍未完成
  资格验证。进程 Host 与 native context 事件属于 synthetic fixture。

### 2026-11-14 — 当前模型任务结果通过 Core 恢复

- **来源：**`main@83809355f84a` 与 C1 #8266 的 `6a31d83497a6`，
  各自使用独立安装的 Core 环境。实际执行的 entrypoint、Turn driver/executor、
  完整来源 owner 和召回入口字节与声明的源码一致。
- **实际成果：**当前交互模型读取两次实际 generic Host 请求，用工具从隔离的
  synthetic 输入生成 JSON 与可阅读的 Markdown 库存报告。独立 oracle 从输入
  以 Decimal 重算，检查重复行合并、零库存、超额预留、非 ASCII SKU、排序、
  每组结果和总计。
- **恢复：**模型结果进入 durable `typed_result` 阶段后，对拥有该 Turn 的 CLI
  进程注入 SIGKILL。两个原 Turn 都在不再次调用 Host 的情况下恢复，完成独立
  校验与 Todo 读回，重放后仍只扣一次。在 C1 上，损坏报告被拒绝，Todo 保持
  开放且零扣额；恢复已校验的报告后，同一个 Turn 完成。
- **Owner 边界：**私有 rendezvous adapter 只传输当前模型的请求和结果。
  C1 与既有 Turn driver 继续拥有组合、身份、准入、校验、捕获、恢复和结算。
  没有新增执行管线、schema、产品默认行为或 capability 激活。
- **剩余：**本次增强任务结果恢复证据，不构成已安装的原生模型/App adapter、
  自然语言启动、原生 compaction 后的模型保留、渐进产品控制、完整 D2/D5 闭包、
  可比完整成本、V1–V5/V10、R1/M3 或 C1 默认切换的资格验证。模型在两次检查
  间复用了报告生成器；这些不是独立推理试验，也不是成果或成本对照实验。

### 2026-11-15 — 完整对话 Goal 草稿只需一次确认

- **缺口：**完整草稿已跳过表单，但仍需先点预览，再在抽屉确认。用户只有完成
  这两步中的第一步后，才看得到服务解析的执行器和工作区。
- **增量：**Dashboard 为可见的完整草稿准备既有 typed action，展示服务确认的 Agent 和
  工作区，提供一次明确的“创建并开始”确认。未完整草稿与只读视图不能准备创建；
  修改入口以及 typed 可用性、陈旧和恢复 gate 保留。准备前先读取既有按语言生成
  的操作身份；刷新、切换语言和重复点击复用原操作。
  可见草稿保留一个确认和恢复入口；草稿未加载时，原 canonical proposal 仍可用。
  尚未展开、不可见的历史不会发起准备。
- **证据：**`goal-draft` 覆盖准备失败、确认前零 Goal 写入、修改与取消、重复
  点击、刷新、语言和窄屏。`goal-draft-live` 在隔离状态使用真实 discovery 和
  action store，覆盖解析后的范围、丢失响应读回、原操作重试及 Agent 不可用拒绝。
  对话是合成输入，stub Agent 的首轮执行失败；该失败保留，不记作成功。
- **边界：**本次是 V4/R1 入口增量，复用既有 Goal 草稿、typed review/apply、
  创建和恢复 owner。没有新增共享 schema、授权、替代执行管线或 C1 组合 owner。
  通过该入口交付完整任务成果、已安装原生 App/模型采用、compaction 保留、完整
  D2/D5 闭包、可比总成本、V1–V5/V10、R1/M3 和 C1 默认切换仍未完成资格验证。
