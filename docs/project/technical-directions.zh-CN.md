# 当前技术方向

模块级反馈联系人及待确认的职责邀请见
[首轮评审分工](../../.github/GOVERNANCE.md#first-review-responsibilities)。
该分工不改变本文的成熟度、晋级门禁或实施授权。

本文是由 maintainer 维护的 LoopX 当前战略方向地图，帮助贡献者理解项目正在
投入什么、各方向成熟到什么阶段，以及一项有价值的贡献应该从哪里开始。它不是
交付承诺、release plan，也不替代已经发布的契约。

> 语言说明：本文与
> [英文版](technical-directions.md)互为语义镜像；实质差异属于缺陷。

## 如何阅读这张地图

- `main` 上的代码、已发布 artifact 和 stable reference contract 定义真实已交付行为。
- RFC 记录提案或已经接受的架构决策，其效力以 RFC 内标注的状态为准。没有实现的
  内容不会因为写进 RFC 就成为事实。
- integration branch 是实现候选，不是第二条产品基线；晋级之前不会改变 `main`
  契约。
- direction tracker 记录结果目标、边界和实质决策。只有另行拆出的有界 issue 或
  task-board 条目才可以被认领。
- 置顶的
  [当前技术方向与已知限制](https://github.com/huangruiteng/loopx/discussions/2851)
  Discussion 是本文面向社区的投影。

统一使用以下成熟度词汇：

| 阶段 | 含义 |
| --- | --- |
| Shipped / hardening | 行为或架构契约已进入 `main`；后续工作改善可靠性、parity 或易用性。 |
| Incubating / qualification | 已有真实候选，但兼容性、证据或晋级 gate 尚未通过。 |
| Active research | 正在进行会产出证据的实验；结果不会自动变成默认行为或产品结论。 |
| Draft | 欢迎设计评审；只有达成一致的最小有用切片才能进入实现。 |
| Held | 保留方向可见性，但在明确 gate 改变之前不应开始实现。 |

## 稳定基础：控制面可靠性

Goal、typed todo、quota、scheduler hint、evidence、Effect Program settlement、
recovery 与 host parity 是所有战略方向共用的底座。其可靠性工作继续通过
[Contributor Task Board](https://github.com/loopx-project/loopx/blob/main/docs/development/contributor-tasks.md)
和 `control-plane` label
推进；这是持续的产品 hardening，不是另一套方向事实源。

跨领域交付次序、全部 RFC 覆盖和产品组合验收统一见 [LoopX 整体路线总纲](../architecture/rfcs/loopx-overall-roadmap-v0.zh-CN.md)。本页继续维护贡献入口；总纲中的 S 工作流、G 里程碑和 R 执行卡不替代领域合同与实际 Todo。

## 战略方向

| 方向 | 目标 | 阶段 | 从这里开始 |
| --- | --- | --- | --- |
| 长程 Benchmark 与证据 | 产出 benchmark-native、可复现的长程能力证据，并用受控任务研究机制。 | Active research | [Tracker #3243](https://github.com/huangruiteng/loopx/issues/3243) · [RFC](../architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.zh-CN.md) |
| 可靠性诊断与治理交付 | 证明 observer-first 产品入口：先在不改变 Agent 执行的前提下诊断长程 workflow，再只在验收通过的 seam 增加 authority。 | Draft 产品方向 / 交付 qualification | [RFC](../architecture/rfcs/long-running-agent-reliability-diagnostics-governed-delivery-v0.zh-CN.md) |
| Operator Surface 与 IM Integration | 通过一致的 operator workspace，让 goal、session、decision、evidence 和有界协作清晰可操作。 | 局部已交付 / 统一旅程 qualification | [Tracker #3244](https://github.com/huangruiteng/loopx/issues/3244) · [integration branch](https://github.com/huangruiteng/loopx/tree/frontend-control-plane-im-prototype-rfc) |
| Shared Goal Authority 与跨 Host 协作 | 让多 host 围绕显式共享 goal 协作，同时避免 provider 或 host session 变成控制面权威。 | Draft contract / provider qualification | [Tracker #3245](https://github.com/huangruiteng/loopx/issues/3245) · [RFC](../architecture/rfcs/shared-goal-authority-state-provider-v0.zh-CN.md) |
| 架构与研究孵化器 | 在扩大生产代码范围之前验证架构演进与研究机制。 | 混合成熟度，见下表 | [Tracker #3246](https://github.com/huangruiteng/loopx/issues/3246) · [RFC 索引](../architecture/rfcs/README.md) |

## 长程 Benchmark 与证据

Benchmark 计划包含两条必须分开的 lane：

1. **能力证据**：在匹配条件下，LoopX 是否改变 benchmark 原生结果、效率或恢复
   能力。
2. **机制研究**：通过 stride、evidence delivery、replan、exploration、human
   attention、memory utility 与 capability evolution 研究变化为什么发生。

ALE、LHTB 与 DeepSWE 提供互补的外部效度环境。LoopX 保留每个 benchmark 的
原生结果，不发布合成总分。贡献者可以参与 deterministic adapter fixture、
treatment-integrity 检查、public-safe reducer 和分析契约。真实 case、raw task、
trajectory、verifier output、upload、官方 scoring 和未公开比较仍由 maintainer
负责。

## 可靠性诊断与治理交付

已有 default-off L1 原型与 DSH event adapter，见 [capability README](../../loopx/capabilities/reliability_diagnostics/README.zh-CN.md)；C0/C1 与开销资格仍未完成。该产品方向把更广的商业化判断收窄成一个有边界的 entry offer。第一个 operating level
是在 native harness 与完整 LoopX adoption 之间的 shadow observer：它单向消费 event、
写入独立 diagnostic ledger，不得注入 prompt，也不得 schedule、retry、stop、resume、
gate 或修改 worker state。在考虑任何 control authority 前，必须用 matched native/passive
benchmark arm 同时证明 diagnostic value 与 non-interference。

后续等级明确区分 advisory recommendation、seam-scoped governed command 与完整
semantic-control-plane adoption。每个正式 pilot 都必须具备 outcome owner、fixed budget、
matched baseline（或显式标为更弱的 baseline）、acceptance criteria、reusable asset path
与 rollback。本文仍是 draft 产品与交付合同，不证明付费 PMF，也不授权在 promotion gate
通过前建设 managed service。

## Operator Surface 与 IM Integration

截至 `f1166e81e`，本地管家对话、executor settings、team-plan 确认、attached/managed 基础及部分 Lark vertical 已进入 `main`；统一跨入口执行与恢复仍未完整验收。[#3167](https://github.com/huangruiteng/loopx/pull/3167) 与其集成分支是历史孵化输入，不能代表当前所有前端能力都未交付。以 [Desktop RFC](../architecture/rfcs/desktop-execution-frontends-v0.zh-CN.md)、选定发布包和总纲 S4/S5 的逐入口验收为准。

进入 `main` 的 promotion ledger 为：

1. 先用 fixture 刻画共享 projection 与 session contract；
2. 隔离 provider-neutral backend、delivery 与 receipt 边界；
3. 通过 parity check 晋级内聚的 runtime 或 projection 切片；
4. source projection 与 authority 边界稳定后再晋级 UI；首屏变化需要 owner preview；
5. credential、provider payload、private receipt、本地路径和 raw session 不得进入
   public fixture 或浏览器状态。

`@maxliux5` 是当前 implementation lead，不代表仓库级 maintainer 任命。Lark 专属
路径遵循[项目治理](https://github.com/loopx-project/loopx/blob/main/.github/GOVERNANCE.md)
记录的 subsystem review route；
跨子域和 mainline 晋级决策仍由 lead maintainer 负责。

## Shared Goal Authority 与跨 Host 协作

本方向刻意不叫“共享元信息数据库”。NoKV 是位于 LoopX authority 之后、尚未晋级
的可选 provider candidate，而不是 authority 本身。Agent 不直接连接 NoKV。
Run history、status、quota、scheduler state、host session 与 evidence 继续由原有
边界负责。

当前已有 provider-neutral store、File/SQLite 候选、PostgreSQL conformance 与 in-process service admission/identity rotation 基础。下一阶段是总纲 R5 的本地持久化 D1–D3 资格，以及 R6 的真实认证跨 host 服务、分布式预算与恢复；不重做已交付的初始 `claim_work` 提取，也不把 service seam 当作已部署服务。NoKV 或其他 provider 的晋升继续服从各自真实 backend、兼容、保留与明确批准门槛。

## 架构与研究孵化器

| 探索 | 阶段 | 当前入口 | 实现规则 |
| --- | --- | --- | --- |
| Effect Program 与 settlement algebra | Accepted / runtime hardening | [RFC](../architecture/rfcs/agent-loop-effect-interpreter-v0.zh-CN.md) | 改善共享 typed contract 与 negative coverage；明确 scheduler ownership 和 domain-local ACK 语义。 |
| TypeScript 控制面迁移 | Accepted / transaction-payoff 阶段 | [RFC](../architecture/rfcs/typescript-control-plane-migration-v0.zh-CN.md) | Cut over 完整 transaction，删除 Python 语义/facade 债务，并报告 bridge traffic 与迁移经济性；delivery/vision 决策保持 domain-local reducer，不泛化成 generic Effect Program step。 |
| 分层 Agent stride | Active research | [#3203](https://github.com/huangruiteng/loopx/issues/3203) | 引入 adaptive selection 前先验证 read-only 与 shadow evidence。 |
| 研究型探索控制面 | Draft / typed frontier | [RFC](../architecture/rfcs/research-exploration-control-plane-v0.zh-CN.md) | 保持 Explore、goal-frontier 和 execution authority 分离。 |
| Human Attention Wishlist | Draft / non-blocking sidecar | [#3179](https://github.com/huangruiteng/loopx/issues/3179) | 不改变 user gate、selected work、quota 或 notification authority。 |
| Goal artifact lifecycle projection | Draft / read model | [RFC](../architecture/rfcs/goal-artifact-lifecycle-projection-v0.zh-CN.md) | 先以 read-only 方式推导 milestone 与合法 next transition。 |
| 结果后 memory utility | Draft / research | [#3214](https://github.com/huangruiteng/loopx/issues/3214) | 只在 verified outcome 后归因；retrieval 与 model judgment 保持 advisory。 |
| Goal Channel 与 Agent IM/OpenViking 边界 | Draft / integration exploration | [RFC 索引](../architecture/rfcs/README.md) | delivery、durable control state 与 scoped context 分属不同 owner。 |
| Agent 会话执行模式 | Draft / 跨宿主接入契约 | [RFC](../architecture/rfcs/agent-session-execution-modes-v0.zh-CN.md) | 宿主绑定会话前必须显式声明按绑定模式、保证单执行器与经过验证的回写；桌面产品流程、服务生命周期与延续仍归各自文档。 |

探索只有在具备真实 caller 或兼容契约、达成一致的最小切片和聚焦 qualification 后，
才进入 implementation-ready。不能只因 RFC 描述了未来可能性，就加入 speculative
module 或重复 authority。

## 贡献与治理闭环

1. 选择最接近的 direction tracker，阅读当前阶段与边界。
2. 在 [Contributor Task Board](https://github.com/loopx-project/loopx/blob/main/docs/development/contributor-tasks.md)
   寻找有界任务；如果没有，
   用 contributor task 模板创建 issue，写明方向、目标 base branch、最小切片、
   non-goal 与验证方式。
3. 孵化工作必须说明 PR 面向 `main` 还是 integration branch。面向 `main` 的 PR
   不得悄悄依赖只存在于未晋级分支的契约。
4. Umbrella issue 用于方向讨论与决策；具体实现和 review 使用独立 issue 或 PR。

当跨方向问题适合实时讨论时，阶段性的
[开放战略 Review](../community/open-strategy-reviews.zh-CN.md)可以比较最多四个方向。
Review 只记录 disposition、owner、下一产物或证据要求及复核 trigger，不通过投票把
方向写入 `main`，也不改变 RFC stage 或直接授权实现。

阶段、owner、integration branch、promotion gate 或 scope 出现实质变化时，必须通过
PR 更新本文；如果 RFC index 或 task board 的路由也发生变化，应在同一 PR 更新。
合并后由 maintainer 更新置顶 Discussion；Discussion 不能覆盖仓库已合并事实。

四个 `direction/*` label 只负责路由，不代表成熟度或 authority。对 implementation
lead 的认可记录当前公开工作，不会静默授予仓库权限或 maintainer 身份。
