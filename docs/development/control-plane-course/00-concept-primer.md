# 概念导读：先把 LoopX 放进一张图

> **导读结论：** LoopX 的目标是让长程任务在无人干预时能跑稳、有人干预时能跑好。
> 它把模型有限上下文之外仍需成立的目标、工作、权限、证据、时间与恢复状态持久化，
> 再把当前状态编译成每一轮可执行的 CLI packet。模型负责推理和执行，LoopX 负责让
> 多轮结果仍属于同一个可恢复、可审计的生命周期。

建议时长：35 分钟。本导读面向第一次接触 LoopX 的读者；读完后再进入
[第 2 讲](02-goal-control-plane-architecture.md)的架构和代码路径。

## 先接受两个工程预设

### 模型上下文是工作内存，不是长期状态

模型只能在当前上下文窗口中推理。即使 host 会保留聊天记录，长程任务仍会经历上下文压缩、
session 结束、模型切换、Agent 交接和外部世界变化。完整 transcript 既可能超出窗口，也混合了
已验证事实、临时猜测、过期观察和未提交计划。

因此，长期运行不能建立在“模型应该还记得”上。真正需要跨 Turn 延续的内容必须被提炼成
有身份、有作用域、可验证的外置状态：

| 必须延续的问题 | 不能只留在聊天里的原因 | LoopX 中的外置形态 |
| --- | --- | --- |
| 最终要达到什么，什么算完成？ | 摘要可能丢失约束，局部任务可能替代最终目标 | goal、vision、acceptance、boundary |
| 现在有哪些工作，谁正在做？ | 多个 session 会重复领取或遗漏 successor | todo、frontier、claim、lease |
| 哪些动作允许执行？ | 能力、工作归属和外部写权限不是同一件事 | authority、gate、capability、workspace guard |
| 哪些判断已经被证明？ | “我运行过”不等于结果可信或 effect 已发生 | evidence、effect receipt、fresh observation |
| 何时再看，何时停止？ | 周期唤醒会热轮询，空 todo 也可能误停 | monitor、scheduler hint、backoff、terminal audit |
| 失败或换人后从哪里继续？ | transcript 不能提供稳定 identity 和幂等边界 | event、run history、lineage、replay、repair |

外置状态只保存下一轮重建正确决定所需的紧凑事实，旧推理
可以丢弃，当前环境必须重探测。

### 长程质量同时包含“跑稳”和“跑好”

LoopX 面向两种同时存在的运行状态：

| 运行状态 | 好的系统应做到什么 | 典型失败 |
| --- | --- | --- |
| 无人干预 | 不重复不可逆 effect，不把等待冒充推进，按新鲜证据重算 frontier，合理退避并在严格终局停止 | 热轮询、重复发 PR、false-ready、局部完成后误停 |
| 有人干预 | 把反馈绑定到正确 goal、agent、run 或 action scope，更新下一步路线，同时保留 authority 与证据边界 | 反馈只留在聊天、一个 gate 冻结全局、偏好被误当授权 |

“跑稳”主要依赖确定性的状态、权限、幂等、调度和恢复合同；“跑好”还需要把人的判断转成
scoped decision、vision patch、todo、reward 或可复用经验。二者共用同一状态内核，否则人工
干预越多，系统反而越难恢复。

## 一个有用但不完整的比喻：面向 Agent 的可执行看板

可以先把 LoopX 理解成面向长程 Agent 的 Kanban。不同之处在于，普通看板主要帮助人
整理卡片，LoopX 还要让 Agent 在无人值守、跨 session 和多人接力时正确推进卡片。因此，
卡片、列和移动都必须对应可验证的状态合同：

| 看板直觉 | LoopX 对应对象 | 增加的工程约束 |
| --- | --- | --- |
| Card | Todo | 稳定 `todo_id`、owner、依赖、scope、evidence 与 continuation |
| Column | 从 canonical state 派生的 lane/view | 列由 lifecycle、task class、routing、proof/time 等维度组合，不是单个字符串 |
| Move card | Typed transition | 每次移动都检查 authority、前置条件、验证结果和 writeback |
| WIP limit | Claim、lease、quota、workspace guard | 限制谁能领取、何时运行、在哪里写以及可以消耗多少资源 |
| Waiting | User gate、monitor、deferred 与 `resume_when` | 等待对象、恢复条件和下一次观察时间都必须明确 |
| Done | Accepted writeback、receipt 与 terminal audit | 勾选卡片不能替代验收、外部 readback 或 goal closure |
| Domain swimlane | Capability Pack + Domain State projection | Issue Fix、Single-Agent Auto ML、Auto Research 等领域可以增加泳道，但不能创建第二套 Kernel |

这组映射提供产品直觉，不改变事实归属。Dashboard、Lark Kanban 和其他看板是 projection；
canonical todo/event/state contract 才能接受 transition。领域泳道也不是新增 Kernel
枚举：`feasibility -> patch -> CI -> review -> merge` 可以是 Issue Fix 的领域视图，
`candidate -> launch -> monitor -> evaluate -> promote/no-promote` 可以是 Single-Agent
Auto ML 的领域视图，`hypothesis -> execute -> evaluate -> promote/retire` 可以是 Auto
Research 的领域视图，而 claim、gate、monitor、quota、writeback 和 recovery 仍由同一
Kernel 管理。

```text
Kanban is the picture; the control plane is the contract.
```

## 从普通对话、原生 Goal 到 LoopX

理解 LoopX 最容易的方法：

| 形态 | 外置了什么 | 擅长解决什么 | 仍未覆盖什么 |
| --- | --- | --- | --- |
| 普通 Agent 对话 | 主要依赖当前 transcript | 一次交互中的推理、工具使用和交付 | session 之外的稳定目标、工作图与恢复 |
| Codex 原生 Goal | thread 上的 objective、status 与可选 token budget | 让 host 持续围绕同一目标运行，并维护 active、paused、budget-limited、complete 生命周期 | 项目拥有的 todo/claim/gate、外部 effect receipt、跨 host scheduler 与领域状态 |
| LoopX control plane | 结构化 canonical state，以及由它派生的每轮 CLI packet | 跨 session、Agent、host 和外部系统组织可审计的长期生命周期 | 领域判断、模型推理和外部系统本身的正确性 |

Codex 的自动化基线通过 `thread/goal/set` 持久化 objective/status，通过
`thread/goal/get` 读回，再用 `turn/start` 继续执行。交互式 `/goal` 是可见的人工入口。原生 Goal 的关键价值，是把“这次
对话要做什么”提升成 host 可持续维护和判断的 goal lifecycle。抽象来看，它提供的是一个
稳定 objective，加上 host 对继续、暂停、预算受限或完成的结果判断。

Host 可以自行安排后续 Turn，模型也可以在执行中调整方案；区别不在于系统“会不会再跑、会不会改计划”，而在于 cadence、
路线变化及其证据是否成为跨 host、跨 Agent 可重放的结构化事实。

LoopX 不否定这层能力，而是继续外置两层：

1. **外置目标**：Goal 让 objective 与一次 prompt 分离；
2. **外置结构化状态**：LoopX 记录 goal 之外的 frontier、authority、evidence、cadence 与 recovery；
3. **外置过程协议**：LoopX 每轮把当前结构化状态编译成 compact CLI packet。Operator 侧常用
   deliver / wait / ask / replan / repair / quiet 描述意图；typed Turn 合同则使用
   `LoopXTurnRoute` 与 `LoopXTurnResultKind`（含 progress/completion 分裂与 failure kinds）。
   验证后怎样 writeback/spend 以这些合同为准，见
   [architecture overview](../../architecture.md#turn-decision-vocabulary)。

```text
finite model context
  <- objective + current CLI packet + bounded evidence refs
  -> one bounded Turn
  -> validated writeback / receipt

Codex Goal object          LoopX canonical state
objective · lifecycle     goal · todo · authority · quota · evidence · recovery
          \                 /
           host/runtime executes the current Turn
```

CLI packet 是带版本、lineage 和
下一步命令的当前决策投影；下一 Turn 必须重新读取并重新生成，不能靠模型记住上一份 packet。
Host 中长期保留的是稳定的 re-entry body：它只说明怎样找到 goal、调用 LoopX CLI 并取得
当前 packet，不承载某一轮的 packet 内容。

> 已有远端 Agent runner、custom CLI 或 workflow supervisor 的读者，可以直接读
> [把 LoopX 嵌入你的 Agent Runner](../../guides/custom-agent-runner-integration.zh-CN.md)：
> CLI 是 truth，轻量 skill/re-entry instruction 约束 Agent 行为，现有 runner 继续拥有
> wake、session 和 workspace；Agent 之间通过 todo/successor 接力，不依赖中央 leader。

## 先从数据面与控制面开始

假设一个开源项目让 Agent 持续把公开 issue 推进成可审阅的修复 PR。它需要判断 issue 是否
可行动、读取代码、复现问题、在独立 worktree 修改、运行测试，并跟进 CI、review 与 merge。

两类系统承担不同责任：

| 平面 | 回答的问题 | 典型对象 |
| --- | --- | --- |
| 数据面 / execution plane | 具体读写、测试、构建、review 或合并怎样执行？ | repository、Git host、CI service、runtime、provider API |
| 控制面 / control plane | 为什么现在做这一步，谁有权做，完成后怎样继续？ | goal、todo、gate、quota、scheduler、evidence、receipt |

代码仓库最清楚当前提交，Git host 最清楚 issue、review 与 merge state，CI 服务最清楚检查结果。
它们继续拥有领域权威事实。LoopX 不复制这些系统，而是记录为什么观察它们、
哪个结果满足哪个验收条件，以及下一轮应继续、等待、询问、重规划、修复还是结束。

一句话区分两者：

```text
数据面执行工作；控制面组织工作的生命周期。
```

## 一张总图

```mermaid
flowchart LR
  U["User / reviewer<br/>目标与 scoped decision"]
  K["LoopX State Kernel<br/>goal · todo · authority · quota · recovery"]
  I["Decision + CLI packet<br/>interaction contract · next commands"]
  C["Capability Pack<br/>领域事实 -> typed proposal"]
  H["Host / scheduler<br/>唤醒与一次有界执行"]
  A["Agent runtime<br/>推理、工具与 session"]
  P["Provider<br/>external call · observation · readback"]
  X["External truth<br/>repository · Git host · CI"]
  E["Evidence + receipt<br/>验证、lineage 与 writeback"]
  V["Projection<br/>status · dashboard · report"]

  U --> K
  K --> I
  I --> H
  H --> A
  A --> C
  C --> P
  P --> X
  X --> P
  P --> C
  C --> E
  E --> K
  K --> V
  V --> U
```

先按四种运行责任读这张图：

- **Agent:** 通过 host/runtime 完成方案、分析、工具使用和一次有界执行；
- **Provider:** 负责外部调用，并返回 bounded observation、effect result 与 readback；
- **Capability:** 负责归一化事实、验证结果并提出有限的 typed transition；
- **Kernel:** 负责接受或拒绝 transition，并持久化 todo、gate、monitor、writeback、quota 与调度。

Domain State、evidence 和 receipt 是这些角色之间传递的工件。Extension
则是 provider 的交付和生命周期边界。Host/runtime 承载 session、
工具和调用，同样不新增领域 decision owner。

每次 writeback 后，Kernel 都要根据新事实重新决定：

```text
continue | wait | ask | replan | repair | terminal
```

## 第一组概念：目标与工作

### Goal 不是一条 prompt

**Goal** 是跨 session 持续存在的目标边界。它至少需要说明：

- 期望结果和验收标准；
- 不允许越过的权限、成本或安全边界；
- 当前可用的 capability 和资源；
- 哪些事实仍不确定；
- 怎样判断已经完成或应停止。

聊天记录可以帮助理解 goal，但不能作为唯一事实源。session 结束、模型更换或上下文压缩后，
goal lifecycle 仍应可恢复。

### Todo 不是普通 checklist

**Todo** 是工作图中的一个有身份对象。它可以表示：

- 当前可执行的 advancement work；
- 等待外部变化的 monitor；
- 需要明确决定的 user action；
- 前一步成功后才出现的 successor；
- terminal closeout 前必须满足的证明。

Todo 之间可以有依赖、归属、证据和 continuation 关系。勾选一项并不自动证明 goal 完成；
Kernel 还要检查它是否提交了预期 transition，以及是否仍有未满足的 acceptance。

### Frontier 是“此刻可能推进的边界”

**Frontier** 不是所有 todo 的列表，而是结合依赖、claim、gate、capability、quota 和新鲜度后，
当前 Agent 真正可见、可运行或需要等待的工作集合。多个 equal peer 可以看到不同 frontier，
而不需要一个长期拥有全局上下文的中心 Agent。

## 第二组概念：能力与实现

### Capability 按调用者结果命名

**Capability** 是稳定的调用者合同：输入什么，承诺产生什么结果，需要哪些证据，可能怎样失败。

例如，“判断 issue 是否适合形成修复 PR”是 capability；“调用 Git host CLI”通常只是实现机制。前者对调用者有
独立价值，后者应由 provider 或内部 helper 承担。

### Provider 是具体实现者

**Provider** 连接真实系统并实现 capability，例如 repository host、通知服务、Git host 或
CI service。Provider 可以替换，但必须保留 capability 的输入、结果、错误和 receipt 合同。

外部系统仍是事实源。Provider 应读取、执行并返回 bounded observation，不应悄悄维护第二套
goal、todo 或 quota 状态机。

### Extension 是交付边界

**Extension** 是可独立安装、升级、启停或分发的 provider 包。它可以实现已有 capability，
也可以携带只属于自己的命令和生命周期。

不要为了让一个 extension 可安装就虚构 capability。只有当 LoopX 调用者真的需要一个
provider-neutral 的稳定结果合同时，才应把它提升为公共 capability。

### Capability Pack 与 Domain State

**Capability Pack** 理解领域 observation，并把它翻译成有限的 typed proposal；
**Domain State** 保存紧凑、稳定、有 lineage 的领域连续性。

它们都不能替代 Kernel：

- Capability 可以建议创建 successor，不能绕过 todo authority 直接分配工作；
- Domain State 可以保存 PR lifecycle observation，不能因为单项检查通过就自行获得 merge 权限；
- Provider 可以执行已授权 effect，不能把调用成功冒充 goal 完成。

## 第三组概念：执行与时间

### Agent、runtime 与 host

这三个名词解决不同问题：

| 对象 | 主要责任 |
| --- | --- |
| Agent lane | 工作和证据归属于谁，可看见哪部分 frontier |
| Runtime | 使用哪个模型、工具、session 和执行环境完成一次推理 |
| Host | 怎样启动 runtime、提供 capability、执行 effect、回收进程并返回结果 |

Agent 是控制面身份，不等同于某个固定模型或进程。更换 runtime 不应改变 todo 的归属、授权
或证据 lineage。

### Turn 是一次有界动作

**Turn** 从一份带版本和 lineage 的只读 snapshot 开始，完成一个有界判断或动作，返回 typed
result。

一次 Turn 可以：

- 读取当前 frontier；
- 选择并执行一项已授权工作；
- 观察外部事实；
- 形成 proposal、evidence 或 receipt；
- 写回后结束。

下一 Turn 应重新读取 canonical state，而不是默认继承上一次模型的记忆。

### CLI packet 是本轮过程协议

Canonical state 通常比一次模型上下文能安全消费的内容更大，也包含不应全部暴露给当前 Agent
的状态。LoopX 因此先投影，再执行：

```text
canonical state + fresh environment
  -> status / quota decision
  -> interaction contract + evidence refs + next CLI actions
  -> host task body / visible packet
  -> bounded Turn
```

Packet 要足够薄，只携带本轮 identity、允许动作、必要 gate、验证条件和 writeback 命令；但又
不能薄到遗漏 required proof、scope 或 terminal gap。Packet 的压缩质量因此是控制面正确性的一部分。

### Scheduler 与 heartbeat 只负责“何时再看”

**Scheduler** 管理 cadence、due time 和 stateful backoff；**heartbeat** 是 host 的周期唤醒入口。
它们不决定业务上该发布、晋级或结束，也不会因为被唤醒就自动获得写权限。

典型过程是：

```text
Kernel 提议下一次 cadence
  -> host 应用 RRULE / timer
  -> host readback 实际值
  -> ACK 绑定当前 proposal
  -> 到期后 heartbeat 重新读取状态
```

“timer 已更新”“Agent 被唤醒”“本轮没有动作”是三种不同事实。

## 第四组概念：权限与占用

### Claim、lease 与 gate 不是同一种约束

| 概念 | 回答的问题 | 典型生命周期 |
| --- | --- | --- |
| Claim | 这项工作由哪个 Agent lane 接手？ | 可交接、释放或完成 |
| Lease | 当前执行窗口是否被占用，何时过期？ | 短期、可续租、可回收 |
| Gate | 哪个 scoped decision 尚未满足？ | 由对应 authority 明确解决 |

Claim 不自动授予生产写权限。
Gate 必须带清晰 scope；若 scope 缺失，应修复投影或状态，而不是猜测它约束所有 Agent。

### Authority 决定谁能提交哪种 transition

LoopX 将“能看见”“能提出 proposal”“能执行 effect”“能提交 terminal”分开。一个 Agent 可以
有能力分析发布风险，却没有权执行发布；host 可以执行 API 调用，却不能自行扩大 proposal scope。

这种区分使人工审批可以只约束不可逆步骤，而不阻塞其他可验证工作。

## 第五组概念：事实、证明与视图

### Evidence 证明判断所依赖的事实

**Evidence** 可以是测试结果、指标、版本、diff、外部状态或经过裁剪的日志摘要。高质量 evidence
应说明 source、lineage、时间、新鲜度和适用范围。

“命令返回 0”通常只能证明进程退出成功，不能证明业务效果已发生。

### Effect receipt 证明外部动作确实发生

**Proposal** 表示准备做什么；**effect receipt** 表示哪个 provider 在什么目标上实际做了什么，
并绑定 proposal identity。必要时还要 readback 外部系统的最终状态。

```text
proposal -> authority check -> provider effect -> readback -> receipt -> state commit
```

如果进程在 effect 成功后、receipt 写回前崩溃，稳定 identity 和幂等规则应让恢复逻辑先 reconcile，
而不是盲目重复动作。

### Canonical state 与 projection

**Canonical state** 是可重放的生命周期事实；**projection** 是从这些事实生成的 status、dashboard、
报告或通知。

Projection 可以针对不同角色重新组织信息，但不能反向成为第二套真相。发现 dashboard 与 CLI
状态矛盾时，应修 projection 或 source contract，而不是手工修改两个地方让它们暂时一致。

## 第六组概念：路线与系统修复

### Replan 修路线

当新证据表明原路线不可行、长期无实质推进、候选空间耗尽或 acceptance 改变时，**replan** 更新
vision/frontier。它回答：“目标不变或已修订的情况下，下一条合理路线是什么？”

重试同一个失败动作、延长 monitor 或改写一段说明都不自动构成 replan。

### Self-repair 修控制面或 Agent 行为

当问题来自状态投影漂移、错误 attribution、缺失 scope、adapter 合同破坏、重复无效动作或
Agent 持续违反运行协议时，**self-repair** 修的是系统规则、状态或行为合同。

它不能通过降低 gate、丢弃矛盾证据或把失败标成完成来恢复运行。

## 人的干预怎样真正改善下一轮

人工输入对生命周期的含义不同，必须写入对应的结构：

| 人工输入 | 正确落点 | 对下一轮的影响 |
| --- | --- | --- |
| “允许合并这个 exact head” | scoped operator gate / decision receipt | 只解锁对应不可逆 action |
| “当前路线错了，先缩小复现范围” | evidence + vision patch / replan + successor todo | 改变当前 executable frontier |
| “这次结果好，但只评价这个 run” | run-bound reward | 形成评价证据，不直接改变 authority |
| “以后这个仓库的 PR 摘要应更短” | reviewed, scoped soft preference candidate | 在匹配 surface 中影响排序或改写 |
| “这个模块过去常因某类边界失败” | procedural experience candidate | 经当前 artifact 复核后影响诊断与验证计划 |

**Reward Memory** 解决的是“有价值的人类判断怎样跨 run 复用”。它把
run reward、hard policy、soft preference、procedural experience 与 working context 分开，
并要求 source、scope、authority、freshness、supersession 和 application receipt。记忆的
confidence 不会扩大权限，召回的技术经验也必须在当前代码和证据上重新验证。

因此，有人干预时“跑好”的完整链路是：

```text
human input
  -> identify actor + goal/agent/run/action scope
  -> classify as decision / evidence / route correction / reward / reusable memory
  -> validate authority and freshness
  -> write the matching state transition
  -> regenerate frontier and next CLI packet
```

默认关闭的 Reward Memory 只是这条链路中可选的长期策略层。即使不开启它，scoped gate、todo、
vision/replan 和 run reward 仍能让当前 goal 正确吸收人工干预。

## 一个中性的端到端例子

下面用 LoopX 的公开 Auto PR Issue Fix 场景串起这些概念。

1. 用户把“为公开 issue 形成小而聚焦、验证充分的修复 PR，并跟进到明确终局”写成 goal，
   同时声明允许的仓库、写入范围和 merge authority。
2. `issue-feasibility` capability 读取 issue 与 repository context，判断它应进入 `fix_pr`、
   `comment`、`triage` 或 `no_followup`。Domain State 保存稳定 issue identity、紧凑 observation 和
   fingerprint，不复制 raw issue body。
3. Kernel 根据 feasibility proposal 创建修复 todo。Agent lane claim 后，runtime 在一次 Turn 中
   复现问题、定位 owner，并给出变更与验证计划；claim 只表示工作归属，不自动获得 merge 权限。
4. Repository provider 在独立 worktree 应用聚焦修改并运行本地验证。Commit 与测试 evidence 写回后，
   host 才执行已授权的 push/create-PR effect，并记录与 proposal 绑定的 receipt。
5. PR 进入 monitor。Scheduler 在 checks 或 review 可能变化时再次唤醒；monitor 只刷新 Git host 的
   权威 observation，不把“仍在等待”冒充进展，也不重复创建 PR。
6. 若 CI 失败，Capability Pack 产生 runnable repair successor；若收到 `CHANGES_REQUESTED`，则把
   当前 reviewer correction 作为 scoped evidence 创建修复 successor。启用 Reward Memory 时，
   可复用的表达或工程经验还可形成待验证候选，但不会直接授权修改或合并；若 checks 尚未结束，
   则保留 monitor continuation。
7. 当 exact head 的 checks 和 review 满足验收时，merge proposal 仍受 scoped authority gate 约束。
   其他不依赖该 gate 的工作可以继续，Agent 不能因为 CI 绿色自行扩大权限。
8. 获得批准后，host 执行 merge，Git provider readback merged commit，并把 effect receipt 写回
   canonical state。若 merge 已发生但 writeback 中断，恢复过程先 reconcile，而不是再次 merge。
9. `MERGED`、issue closed 或明确 no-follow-up observation 触发 terminal closeout。Status、dashboard
   和报告都从同一组 canonical state 与 evidence 重建。

这条链路里，repository、Git host 和 CI 决定外部事实；Provider 获取事实并返回 bounded
observation/readback；Capability Pack 解释这些事实；Kernel 决定生命周期；Agent 通过
host/runtime 完成一次有界执行；scheduler 决定何时再观察。

## LoopX 不负责什么

理解边界与理解能力同样重要。LoopX 不会自动提供：

- 正确的 issue 优先级、修复方案或风险接受标准；
- 对任意外部系统可靠的 provider；
- repository access、merge authority 或安全例外；
- Agent 输出天然可信的保证；
- 用一个通用状态机消除所有领域差异；
- 在 evidence 不足时替用户做高风险价值判断。

LoopX 提供的是让这些能力可以被组合、约束、观察和恢复的共同内核。

## 带着八个问题进入后续课程

之后阅读任何一讲或评审一个控制面改动，都先问：

1. 哪些信息必须跨有限上下文继续成立，哪些只是临时推理？
2. 事实由谁拥有？
3. 哪个对象把 observation 翻译成 proposal？
4. 谁有权执行和提交 transition？
5. 什么 evidence 或 receipt 证明它发生了？
6. 当前 packet 为什么只暴露这些工作、gate 和命令？
7. 下一轮为何是 continue、wait、ask、replan、repair 或 terminal？
8. 崩溃、重复、超时或证据冲突时怎样恢复？

## 阅读导航

| 想继续理解的问题 | 对应课程 |
| --- | --- |
| Kernel、Capability Pack、Domain State 怎样分层？ | [第 2 讲](02-goal-control-plane-architecture.md) |
| 一个 goal 第一次怎样真正跑起来？ | [第 3 讲](03-first-real-loop.md) |
| registry、event、active state、projection 各自拥有什么？ | [第 4 讲](04-state-substrate.md) |
| todo、claim、lease、gate 和 equal peer 怎样组合？ | [第 5 讲](05-work-graph-and-peers.md) |
| should-run 怎样压成 interaction contract，以及它与 typed Turn 枚举有何不同？ | [第 6 讲](06-quota-decision-kernel.md)、[architecture Turn vocabulary](../../architecture.md#turn-decision-vocabulary) |
| scheduler、heartbeat、RRULE 和 ACK 的边界是什么？ | [第 7 讲](07-host-scheduler-and-heartbeat.md) |
| evidence、replan 和 self-repair 何时触发？ | [第 8 讲](08-evidence-refresh-and-self-repair.md) |
| 怎样实现和验证一条新的控制面规则？ | [第 9 讲](09-engineering-a-control-plane-rule.md) |
| 自主 Agent 交付需要哪些分层质量门禁？ | [第 10 讲](10-autonomous-agent-quality-gates.md) |
| Extension、Explore、Reward Memory 与领域产品怎样复用 Kernel？ | [第 11 讲](11-extension-layer.md) |

完成导读后，从[第 2 讲](02-goal-control-plane-architecture.md)开始按真实代码路径学习。
