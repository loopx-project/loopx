# 开发者贡献地图与协议入口

给 LoopX 做贡献不只等于修改 Kernel，也不只等于开发 Extension。外部开发者可以改进协议规则、
Capability 与 Domain State、Provider、Host/Runner、Projection、Dashboard、文档、fixture 和
独立分发包。第一步不是挑目录，而是判断这次贡献要交付什么结果、由哪份合同拥有它。

阅读源码最容易走错的路径，是先打开最大的 Python 文件，再沿函数调用不断向下钻。这样能看见
实现，却很难判断一段行为为什么存在、改动后哪些消费者必须保持兼容。更可靠的入口是：

```text
开发任务
  -> 贡献结果与 placement
  -> 所属协议族
  -> 协议拥有的不变量
  -> 对应 bounded context
  -> 当前实现与验证
```

本章给出一张协议优先的源码地图。它不列完整 API，也不要求你记住当前版本的函数名；目标是让你
在准备一个 Issue 或 PR 时，先说清自己正在改变哪份合同。

## 本章目标

读完后，你应该能：

- 判断贡献属于 Control Plane、Capability、Provider、Host/Runner、Projection/Docs 还是 Extension；
- 记录 capability id、provider id 与 built-in/extension-delivered placement；
- 把协议级任务归入状态、工作图、Turn/Host 或证据恢复协议族；
- 区分 canonical contract、read model、host adapter 与 renderer；
- 根据 change reason 选择 bounded context，而不是根据文件名猜位置；
- 从公开 Contributor Task、Issue 和协议文档形成可审阅的最小切片；
- 用协议、不变量和验证描述改动，而不是提交一份函数清单。

## 先写一张协议卡

开始读代码前，先为问题写一张短卡：

```text
Reader-visible problem:
Current protocol:
Source of truth:
Invariant at risk:
Allowed transition:
Forbidden outcome:
Expected receipt:
Validation surface:
```

例如，问题是“一个非阻塞用户提醒错误地满足了 publish 权限”：

```text
Current protocol: decision_scope_v0
Source of truth: typed gate and todo requirements
Invariant at risk: a notice cannot grant authority
Allowed transition: matching approved gate consumes only covered scope
Forbidden outcome: unrelated or non-blocking notice unblocks publish
Expected receipt: linked decision and lifecycle event
Validation surface: decision table + quota integration smoke
```

这张卡比“准备修改 `quota.py`”更有信息量。文件可能移动，协议责任和错误结果却仍然可以被评审。

## 先选择贡献结果和放置位置

开始实现前，先回答四个 placement 问题：

```text
Capability id:
Provider id:
Delivery: built-in | extension-delivered | standalone package
Why the nearest existing owner is or is not sufficient:
```

然后按调用者结果选择贡献面：

| 贡献面 | 它拥有的合同 | 典型交付 | 不能顺手拥有 |
| --- | --- | --- | --- |
| Kernel / Control Plane | 通用 Goal、Todo、Gate、quota、scheduler 与 lifecycle invariant | typed transition、decision rule、recovery 修复 | 某个领域的全部业务状态 |
| Capability / Domain State | 调用者可依赖的 outcome、领域 policy 与结果生命周期 | domain command、typed result、admission/read model | Provider 凭据或通用控制面复制品 |
| Provider / 外部系统 | bounded request、外部调用、observation/effect readback | built-in provider 或 extension-delivered implementation | Goal authority、完成判断或 service auth 替代品 |
| Host / Runner / Session Runtime | typed execution、可见性、resume handle 与 host-owned effect | Host adapter、runner、scheduler-owner integration | LoopX canonical state 或自验证 completion |
| Projection / Dashboard / Docs / fixtures | 面向读者的 read model、解释与 public-safe evidence | CLI renderer、dashboard、协议文档、synthetic fixture | browser write authority 或第二套状态机 |
| Extension / package lifecycle | 独立安装、启停、doctor、升级、回滚与兼容 | standalone package 或 Capability Provider 的交付单元 | Capability domain policy 或自动权限授予 |

这些贡献面可以组合，但不能混成一个模糊的 “plugin”。例如：

- 新增稳定调用者结果：先定义 Capability 和 Domain State，再决定由 core provider 还是 Extension
  实现；
- 只替换外部服务实现：保留已有 Capability，新增 Provider，并选择 built-in 或
  extension-delivered lifecycle；
- 只增加一张操作看板：从 public-safe projection 读取，不解析项目私有文件，也不创建写路径；
- 只改 Host continuation：复用 quota、scheduler 与 Turn contracts，不在 runner 内增加第二套
  scheduler；
- 交付一个零权限、确定性的独立命令：可以使用 standalone Extension，不必先创建虚假的
  Capability。

新增目录、CLI option 或 schema 前，必须有真实 caller、active call site 或明确 compatibility
contract。只有未来可能使用的 provider、runner 或 projection，先留在设计或 Todo 中，不要把
假设性结构提交到 production tree。

## 五个核心协议族

协议数量会随产品增长，但外部贡献者不需要从目录首字母开始逐个阅读。先按任务选择协议族。

### 1. 状态与投影

这组协议回答：

> 事实存在哪里，谁可以写，读模型如何重建？

主要入口：

- [`event_sourced_state_contract_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/event-sourced-state-contract-v0.md)：
  append-only event、replay、idempotency 与 privacy partition；
- [`active_state_structured_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/active-state-structured-projection-v0.md)：
  从 active-state workbench 生成 typed、read-only projection；
- [`task_graph_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/task-graph-projection-v0.md)：
  以只读图表达 Todo、Gate、dependency、validation 与 handoff；
- [`local_state_write_correctness_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/local-state-write-correctness-v0.md)：
  revision、lock、idempotency key、conflict 与 durable write。

适合从这组协议开始的任务包括：

- status 与 event 显示不一致；
- active-state parser 丢失字段；
- task graph 缺少 lineage 或 truncation diagnostics；
- lifecycle writer 在 retry 后重复产生效果；
- dashboard 想增加一个新字段。

最后一个例子尤其重要。Dashboard 需求通常先回到“这个字段由哪个 source 拥有”，而不是直接给
UI 增加一份可编辑状态。

### 2. 工作图、权限与 Peer

这组协议回答：

> 当前谁可以做哪项工作，什么条件阻塞它，结束后由谁继续？

主要入口：

- [`decision_scope_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/decision-scope-v0.md)：
  Gate 的 kind、granularity、scope coverage 与 fail-closed 行为；
- [`goal_vision_replan_contract_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/goal-vision-replan-contract-v0.md)：
  per-Agent Vision、checkpoint、replan 与 bounded route；
- [`peer_agent_runtime_v1`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/peer-agent-runtime-v1.md)：
  equal peer identity、claim、continuation 与协作边界。

适合从这组协议开始的任务包括：

- 某个 Gate 错误阻塞全部 Agent；
- claim 被当成 lock 或全局 authority；
- handoff 完成后没有 successor；
- monitor 与 advancement 的 precedence 错误；
- Host 有能力执行，但缺少所需 decision scope。

评审这类 PR 时，先问“authority 来自哪里”，再问“代码进入哪个分支”。文本里出现
`approved`、`owner` 或 `waiting for user`，都不能替代 typed scope relation。

### 3. Quota、Interaction 与调度

这组协议回答：

> 复杂状态如何被编译成这一轮的 user、agent 和 CLI 责任？

主要入口：

- [`turn_envelope_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/turn-envelope-v0.md)：
  在已完成的 quota decision 上提供 bounded next-action read model；
- [`protocol_action_packet_decision_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/protocol-action-packet-decision-v0.md)：
  action packet 的决策语义；
- [Status Data Contract](https://github.com/huangruiteng/loopx/blob/main/docs/status-data-contract.md)：
  status、attention 与 operator-facing 数据边界；
- [State Machines](https://github.com/huangruiteng/loopx/blob/main/docs/product/core-control-plane/state-machine.md)：
  Todo、Gate、Quota、Evidence 和 Scheduler 如何组合。

这组协议的核心不是一个 `should_run` 布尔值，而是有优先级的最终合同：

```text
source facts
  -> normalized projections
  -> ordered policy
  -> interaction_contract
  -> scheduler_hint
```

一个用户 Gate 可以要求用户回答，同时 agent channel 仍要求执行不依赖该 Gate 的安全工作。把两个
channel 压成一个布尔值，会同时损坏交互和调度。

### 4. Bounded Turn 与 Host Effect

这组协议回答：

> 一轮外部执行如何被提议、执行、独立验证并写回？

主要入口：

- [`loopx_turn_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/loopx-turn-v0.md)：
  experimental 的 decide、execute、validate、writeback 与 spend transaction；
- [`session_runtime_loopx_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/session-runtime-loopx-projection-v0.md)：
  外部 runtime 到 LoopX 的 read-only first-screen projection；
- [`session_runtime_controlled_writeback_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/session-runtime-controlled-writeback-v0.md)：
  session runtime metadata controlled writeback 的 draft 边界；
- [`host_integration_surface_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/host-integration-surface-v0.md)：
  Host lifecycle read、CLI-equivalent controlled write、能力声明和 fallback；
- [`rollback_packet_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/rollback-packet-v0.md)：
  补偿、回滚与证据链。

这里要保持三个责任分离：

| 责任 | 所有者 | 不能替代 |
| --- | --- | --- |
| 选择当前 action | LoopX control plane | Host 根据 prose 自行猜测 |
| 执行 bounded effect | Host adapter | LoopX 假装外部动作已发生 |
| 判断 postcondition | 独立 validator | Host 的自然语言自报成功 |

`session_handle`、raw stdout 和 transcript 可以帮助 Host 恢复，但不能成为 Goal authority 或 completion
proof。

### 5. 证据、恢复与质量

这组合同回答：

> 如何证明一条规则正确，失败后如何恢复，并确保回执属于当前 revision？

主要入口：

- [`model_behavior_qualification_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/model-behavior-qualification-v0.md)：
  何时需要真实模型行为验证；
- [Benchmark 研究 RFC](https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md)：
  如何保证研究双臂可比并由独立 outcome evidence 支撑；
- [Testing and Quality](https://github.com/huangruiteng/loopx/blob/main/docs/development/testing-and-quality.md)：
  unit、contract、smoke、decision replay、canary 与 release gate；
- [Public/Private Boundary](https://github.com/huangruiteng/loopx/blob/main/docs/public-private-boundary.md)：
  哪些证据可以进入公开仓库。

这不是最后才补的“测试部分”。协议卡中的 forbidden outcome 和 expected receipt 会直接决定验证形态。

## 从协议族落到 bounded context

协议说明跨模块合同；bounded context 说明代码由哪个 change reason 拥有。当前核心地图可以压缩为：

| Context | 主要责任 |
| --- | --- |
| `goals` | Goal state、Vision、goal-level planning 与 frontier |
| `todos` | Todo lifecycle、scope、resume、monitor 与 handoff summary |
| `agents` | Agent identity、agent-scoped routing 与 capability |
| `quota` | 把已投影事实编译成当前 interaction decision |
| `scheduler` | cadence、backoff、reset 与 ACK |
| `runtime` | Turn/session projection 与 bounded execution state |
| `handoff` | 跨 runtime handoff、review packet 与 owner route |
| `work_items` | attention、selection 与 operator-facing work read model |

在 `v0.5.4` 的迁移基线上，bounded context 和实现语言是两个维度。`goals`、`todos`、`quota`、
`scheduler`、`work_items` 与 `turn_driver` 已有完整 transaction 由 TypeScript module 拥有语义；
典型例子包括 Vision refresh、本地 task-lease lifecycle、quota spend/void/monitor-poll commit 和
receipt-bound scheduler follow-up。旁边的 Python facade/module 可能只负责 CLI transport、legacy
projection、明确的外部 Provider/Host effect 或尚未迁移的写回。定位 owner 时应先读
[TypeScript Control-Plane Migration RFC](/loopx/docs/architecture/rfcs/typescript-control-plane-migration-v0/)
的 shipped baseline，再沿真实 request handler 和 caller 判断；不要因为入口仍是 Python，就默认
Python 仍拥有该 decision。

选择位置时问：

```text
什么原因会让这段规则改变？
```

而不是：

```text
哪个文件现在已经引用了相似字段？
```

例如：

- “Gate 是否覆盖 action”属于 authority/todo contract；
- “已仲裁结果如何显示给用户”属于 projection/renderer；
- “多久再次唤醒”属于 scheduler；
- “Host 如何应用一次 effect”属于 adapter；
- “效果是否满足 acceptance”属于 validator。

一个 PR 可以跨多个 context，但每处修改都应服务同一条协议链。

## 从贡献面落到仓库 owner

当前仓库的稳定路由应理解为 owner，而不是看到目录名就自动放入：

| 目标 | 优先查找 |
| --- | --- |
| 通用控制面规则 | `loopx/control_plane/<bounded-context>/` 与对应 protocol/decision table |
| 已有 Capability 的结果或领域状态 | `loopx/capabilities/<capability>/` |
| Capability/Provider 注册与组合 | `loopx/capabilities/registry.py`、catalog 与 manifest contract |
| Extension 通用 manifest、readiness、runtime | `loopx/extensions/` |
| 独立安装的 package | `packages/<package-id>/` 或独立 repository |
| Host/Runner 集成 | runtime connector、Turn/Host contracts 与对应 adapter |
| 操作投影 | status/frontstage/projection owner；renderer 只消费 typed read model |
| 文档与验证 | owning protocol 文档、`tests/`、`examples/` 或 public-safe fixture |

当前 capability 目录采用 package-owned documentation 与 `catalog_entry.py` 注册。贡献前运行
`loopx capability list --format json` 和 `loopx capability show <capability-id> --format json`，
确认 capability 是否真实注册、有哪些 entry command、Provider 边界与 durable validation。仅有
目录或 README 不证明能力已经发布。

`loopx/capabilities/<name>/` 中有代码不自动证明它是公开 Capability；需要显式注册和真实 caller
contract。`loopx/extensions/` 也不是“所有外部集成”的收纳箱：只有独立 provider lifecycle 才属于
这里。私有 helper 留在最近的 owner 中，不因为跨了几个文件就升级为新 Capability 或 Extension。

## 函数名是搜索锚点，不是课程目录

官方核心课程和源码会提供当前版本的搜索入口。使用它们时遵循三条规则：

1. 先读协议和 decision table，再搜索当前 implementation anchor；
2. 沿输入和输出确认它仍承担同一责任，不因名字相似就假定 owner；
3. 在 PR 说明中引用 invariant 和 contract，函数名只用来帮助 reviewer 定位 diff。

例如，当前版本可以从 `quota should-run` 的 builder、Turn driver 或 task-graph builder 开始搜索。
这些名字未来可能移动到更合适的 bounded context；你的理解不应因此失效。

如果一篇文档需要列二十个函数才能解释行为，通常说明它在复制实现，而没有提炼协议。

## 选择一个公开贡献入口

外部开发者不应从本地 maintainer state 猜工作。公开入口是：

1. 阅读
   [Contributor Task Board](https://github.com/huangruiteng/loopx/blob/main/docs/development/contributor-tasks.md)；
2. 选择 `Starter`、`Focused` 或已达成设计共识的任务；
3. 阅读任务涉及的协议和 validation；
4. 在关联 Issue 中声明准备处理的最小切片；
5. 等待 behavior-changing 或大范围任务获得 maintainer 反馈；
6. 用干净分支完成一条可独立审阅、回滚的协议变更。

不要从这些内容创建公开任务：

- `.loopx/`、`.loopx/goals/` 或 live active state；
- private benchmark trace、raw agent session 或 verifier output；
- 内部文档、生产凭据、本机路径；
- `Maintainer-owned` live run 的推测性复刻。

公开贡献需要从 public-safe protocol、Issue 和 fixture 建立上下文。

贡献不要求修改 runtime code。官方任务中也包括：

- 协议、迁移说明与 contributor walkthrough；
- deterministic decision table、negative test 与 public-safe replay fixture；
- read-only dashboard、可访问性与 operator explanation；
- fake-host、fake-provider 或 no-sink integration example；
- Extension scaffold、manifest compatibility 与 lifecycle smoke。

无论交付类型是什么，都要说明它改变了哪个读者结果、由哪个 authority 保持事实，以及什么事件会
使文档、fixture 或 compatibility claim 过期。

### 社区信号怎样变成有边界的工作

本节与英文对应章节保持语义镜像；案例、状态、结论或链接目标的实质差异属于文档缺陷。

<!-- community-casebook:signal-to-bounded-work:start -->
<!-- community-casebook:question-before-fix -->

**问题也可以是贡献。** 在
[“怎么给任务设置停止点？”](https://github.com/huangruiteng/loopx/discussions/3069)
中，用户报告的是“任务完成后仍空转”。在确认它属于产品缺陷前，社区先把问题拆成 Goal
acceptance、terminal closure、quota budget 与 monitor cadence 四个可检查假设。好的 Q&A
不是立刻猜一处代码，而是把模糊体验变成最小诊断路径。

<!-- community-casebook:user-idea-to-contract -->

**方法论建议先映射已有合同。**
[“look back and retain”](https://github.com/huangruiteng/loopx/issues/2353)
从用户长期使用经验出发，提出 Agent 应解释路线为什么改变、哪些旧工作仍有效。讨论没有直接创建
第二套 memory 系统，而是先对照 evidence log、Vision acceptance 与 `goal_path_delta_v0`，再确认
剩余语义缺口。这类 Issue 可以在不写代码的情况下改进产品方向。

<!-- community-casebook:claim-before-code -->

**认领要先收窄 authority。**
[Pi `task_lease_v0` 任务](https://github.com/huangruiteng/loopx/issues/3549)
把 existing capability owner、Host facade、in-scope、non-goals、目标 base branch 与验证命令写在
实现之前。它没有把“Pi 需要 lease 操作”扩张成新的 scheduler、存储或自动 lease lifecycle。

这些记录只是学习样本，不是当前任务状态的副本。准备参与时仍要重新打开 Issue，确认它尚未被
关闭、改向或认领，并以
[Contributor Task Board](https://github.com/huangruiteng/loopx/blob/main/docs/development/contributor-tasks.md)
为当前公开入口；`Maintainer-owned` 工作只能请求独立 helper slice，不能平行复刻。
<!-- community-casebook:signal-to-bounded-work:end -->

### RFC Review Lab：先判状态，再谈实现

本节与英文对应章节保持语义镜像。

<!-- community-casebook:rfc-review-lab:start -->
<!-- community-casebook:rfc-status -->

先从 [RFC Index](https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/README.md)
读取正式状态。`Accepted`、`Active research`、`Draft` 与 `Draft integration proposal` 允许的动作
不同；一份 RFC 或 Discussion 存在，不等于某个实现已经发布，也不自动生成可认领任务。

<!-- community-casebook:rfc-community-proposal -->

例如社区 Discussion
[#3157](https://github.com/huangruiteng/loopx/discussions/3157)
提出 event-driven control plane 与统一 policy decision。评审时不要从“目录是否漂亮”开始，应先问：

1. 当前 quota、scheduler、event store 与 worker 的 canonical authority / owner 分别是谁？
2. 提案是在组合现有规则，还是复制第二套 decision authority？
3. event store 是否被误当成具备 delivery semantics 的 event bus？
4. 哪一阶段第一次改变默认行为，是否有独立 migration gate？
5. 最小可验证切片是什么，失败后如何回到当前路径？

<!-- community-casebook:rfc-output -->

一份有用的 RFC review 应输出 `accept`、`revise`、`require_evidence` 或 `defer` 之类的明确
disposition，并指出 owner、下一产物与复核条件。需要跨方向同步讨论时，可以进入
[Open Strategy Review](https://github.com/huangruiteng/loopx/blob/main/docs/community/open-strategy-reviews.md)；
会议不会替代版本化 RFC、bounded Issue、PR review 或 maintainer authority。
<!-- community-casebook:rfc-review-lab:end -->

## 判断改动是否过大

一个合适的贡献切片通常能用一句协议结果描述：

> 让 `decision_scope_v0` 在缺失 scope relation 时产生 typed repair，而不是把 Gate 当成全局阻塞。

以下描述往往过大：

> 重构 status、quota、scheduler 和所有测试。

缩小切片不是只减少行数，而是保持一条完整因果链：

```text
source
  -> invariant
  -> decision
  -> projection or effect
  -> receipt
  -> validation
```

不要只提交链条中间的 helper，也不要为了“未来扩展”提前增加没有调用方的 enum、CLI flag 或
adapter。

## 本章检查表

准备进入源码前，确认你已经能回答：

- [ ] 这个问题属于哪个协议族？
- [ ] canonical source 和 primary writer 是谁？
- [ ] 哪条 invariant 可能被破坏？
- [ ] 合法与非法 transition 分别是什么？
- [ ] 哪个 bounded context 对该 change reason 负责？
- [ ] 哪个公开 fixture 或 smoke 能证明真实链路？
- [ ] 任务是否已经公开、可认领且不属于 maintainer-owned live work？
- [ ] PR 是否可以用一条完整协议结果描述？

下一章选择一个 scoped Gate 场景，沿 source、projection、decision、Turn、receipt 和 replay 走完一条
真实协议链。
