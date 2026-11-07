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

暂无。
