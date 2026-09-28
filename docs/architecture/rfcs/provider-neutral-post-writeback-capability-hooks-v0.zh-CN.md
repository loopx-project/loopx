# RFC：Provider-Neutral Post-Writeback Capability Hooks v0

| 字段 | 内容 |
|---|---|
| 状态 | 已接受 |
| 替代 / 关闭 | 无 |
| 日期 | 2026-08-26 |
| 跟踪 issue | [#3479](https://github.com/huangruiteng/loopx/issues/3479) |
| 源码基线 | LoopX `11824ef5f` |
| 决策边界 | 已安装 capability 如何在成功 durable writeback 后提出有边界的后续工作，同时不加入主事务、也不获得 effect 权限 |
| Core owner | Turn settlement 与 capability-hook 生命周期 |
| Capability owner | 决定是否提出 intent、提出哪类 intent 的策略 |
| Effect owner | 单独授权的 governed executor 或 sink |

> 语言说明：本文与
> [英文版](./provider-neutral-post-writeback-capability-hooks-v0.md)
> 互为语义镜像；两者存在语义差异即为缺陷。

## 1. 决策摘要

LoopX 应新增 provider-neutral 的 `post_writeback` capability-hook phase。
该 phase 只在主 durable-writeback step 已产生合法、committed receipt 之后运行。
已安装 hook 接收一份精简、public-safe 的 receipt projection，并可返回零个或多个
typed、幂等的 **intent proposal**。

Proposal 不执行外部 effect。Core 对其验证后写入有边界的 sidecar journal 或
outbox。之后必须由另一个 governed executor 根据自身 capability、write scope、
budget 与授权策略重新准入，才允许发生任何 effect。

权威拆分如下：

```text
主 Turn
  -> validation
  -> durable writeback + committed receipt        （core 生命周期权威）
  -> 隔离的 post-writeback hook dispatch
       -> typed intent proposal                    （capability 策略权威）
       -> validated sidecar receipt                （core 监督权威）
  -> quota spend 只依赖主 receipt

typed intent proposal
  -> 单独准入的 governed execution
  -> renderer / connector / external sink          （effect 权威）
```

Hook 不是新增 settlement step。Hook 失败不得回滚主 writeback、阻止与之匹配的
quota spend、创建 user gate，也不得隐式继承主 Turn 的 write 权限。

## 2. 问题

LoopX 已有若干必要前件，但它们尚未形成自动 post-writeback contract：

- `loopx_capability_hook_registration_v0` 已支持只读的
  `interaction_projection` phase，验证了 composition-root 注册、TypeScript
  语义校验、有界输出、slot 冲突处理和故障隔离。
- `periodic_report` 已能把 durable、public-safe rollout event 规约为 typed
  trigger decision，但 runtime producer 仅能显式调用；Todo 完成或 replan
  writeback 后，控制面不会自动调用它。
- Turn settlement runtime 已有 typed identity、有序 receipt、replay 和
  durable-writeback checkpoint。
- rollout-event append 是 best-effort 诊断日志，设计上不会把一个成功的主命令
  变成失败。
- 部分 interaction contract 会投影 command 形态的 `post_writeback_actions`。
  这些 operator hint 不是 registry、typed provider result 或权限 contract。

缺少共享 post-writeback 边界时，每个 capability 只能选择手动调用、让 core
直接 import capability、接受任意 callback，或复制生命周期逻辑。第一种无法自动化
长程 Goal；后三种会制造第二事实源或隐式 effect 权限。

第一个需求是：在有边界的阶段完工或进入 replan 后产生 periodic-report trigger。
但 core contract 必须以调用者所需 outcome 命名和设计，不能按第一个 provider 命名。

## 3. 目标与非目标

### 3.1 目标

V0 contract 必须：

1. 只从 committed 主 durable-writeback receipt dispatch；
2. 保持注册和结果校验 provider-neutral、由 core 拥有；
3. 仅暴露精简、public-safe 的 receipt projection；
4. 只接受 typed、幂等的 intent proposal；
5. 隔离注册、producer、超时、校验与冲突失败；
6. 让 replay、去重、budget 与顺序确定可复现；
7. 继续分离 capability 策略、core 生命周期与 effect 权威；
8. 能以真实 completion/replan writeback 为起点，验证产生 periodic-report
   intent 的闭环，但不执行外部写。

### 3.2 非目标

本文不做以下事情：

- 自动发送报告、消息、邮件、webhook 或更新文档；
- 以任意 shell command、import string 或用户 callback 作为 hook 接口；
- 允许 core settlement 代码 import `periodic_report` 或其他具体 capability；
- 把 hook execution 放进 `append_rollout_event_once` 等底层持久化 helper；
- 授予 hook 主 Turn 的仓库、网络、凭据、quota 或 write 权限；
- 让 hook 完成成为主 quota settlement 的前置条件；
- 把现有 command 形态的 `post_writeback_actions` 解释成可信 extension 注册；
- 定义通用 workflow engine 或 hook 依赖图；
- 因为发布 RFC 就宣称实现已经交付。

## 4. Ownership 与组合边界

Contract 有四类 owner，职责互不重叠：

| 关注点 | Owner | 权威 |
|---|---|---|
| 合法 dispatch 点、receipt 校验、registry 准入、budget、顺序、journal、replay 与失败 receipt | LoopX core | 仅控制面生命周期 |
| Receipt 是否相关、应提出哪种 intent | 已安装 capability | 仅策略 proposal |
| 把具体实现绑定到 capability registration | Host 或 CLI composition root | 仅安装与配置 |
| Renderer、connector 调用、外部写和 readback | Governed executor 或 sink | 单独准入的 effect 权威 |

Core 不得按 capability identifier 分支。Composition root 可以注册已安装 provider，
但“已安装”只证明 provider 可用且 contract 兼容，不证明它提出的 intent 已获执行授权。

TypeScript 控制面继续作为 registration、input、result 和 dispatch receipt 校验的
语义 owner。Python 可以像现有 interaction-projection hook 一样持有 callable adapter
并调用 TypeScript validator，但不得重新实现一套接受策略。

## 5. Dispatch 点与主事务边界

### 5.1 合法 source

只有同时满足以下条件，dispatch 才合法：

- settlement identity 具有非空 goal、agent、Todo、Turn 与 effect identity；
- durable-writeback receipt 与当前 Turn settlement plan 使用相同 effect identity；
- durable-writeback step 是 committed，而非 prepared、rejected、推断结果或仅存在于 prose；
- receipt 已完成足够强的 durable checkpoint，使 replay 能恢复同一 dispatch identity；
- 当前 execution profile 启用了 post-writeback hook。

成功 append diagnostic rollout event 不是权威来源。Committed receipt 准入 dispatch
后，rollout event 可以向 capability 提供有界事实；但 diagnostic event 丢失不得改变
主 settlement 的事实。

### 5.2 位置

Orchestrator boundary 在 committed writeback receipt 之后 dispatch。具体 host 可以
在主 quota spend 前或后安排执行，但不得改变下列语义：

- quota-spend eligibility 只依赖匹配的主 durable receipt；
- hook dispatch 有独立的 sidecar checkpoint 和 idempotency identity；
- primary writeback 后进程崩溃，仍能 replay 同一 hook dispatch；
- hook 失败不删除、不改变主 receipt。

因此它是 **post-writeback** phase，但不属于主 settlement 的有序 step list。若将它
作为第五个主 step，可选 capability 的健康状态就会支配 accounting，本文明确拒绝。

## 6. Registration Contract

V0 引入 phase-specific registration，不削弱只读 `interaction_projection` schema：

```json
{
  "schema_version": "loopx_post_writeback_capability_hook_registration_v0",
  "hook_id": "periodic_report.runtime_trigger",
  "capability_id": "periodic_report",
  "phase": "post_writeback",
  "event_kinds": ["todo_completed", "replan_recorded"],
  "intent_kinds": ["periodic_report.trigger_evaluation"],
  "requested_read_scope": ["settlement_identity", "bounded_event_projection"],
  "budget": {
    "max_invocations_per_dispatch": 1,
    "max_intents_per_dispatch": 1,
    "max_result_bytes": 16384,
    "timeout_ms": 1000
  },
  "failure_policy": "isolate"
}
```

Core 在调用 provider 之前校验 exact fields、有界 token arrays、已知 event/intent
kind、无重复 identity、size/timeout limit，以及强制 `isolate` policy。

Registration 不声明可执行 command，也不声明 write scope。Intent proposal 可以声明
未来 executor 所需的 scope，但这只是准入请求，不是 hook 已持有的权限。

Registration 顺序不代表执行优先级。Core 按稳定 `hook_id` 排序后 dispatch。V0 中
hook 不得依赖另一个 hook 的 result；真实依赖应进入单独 governed workflow。

## 7. Input Contract

每个 admitted hook 接收一个 immutable `loopx_post_writeback_hook_input_v0`：

```json
{
  "schema_version": "loopx_post_writeback_hook_input_v0",
  "dispatch_id": "pwh_sha256_opaque",
  "hook_id": "periodic_report.runtime_trigger",
  "capability_id": "periodic_report",
  "source": {
    "receipt_id": "receipt_opaque",
    "effect_id": "effect_opaque",
    "step_kind": "durable_writeback",
    "goal_id": "goal_opaque",
    "agent_id": "agent_opaque",
    "todo_id": "todo_opaque",
    "turn_instance_id": "turn_opaque",
    "event_kind": "todo_completed",
    "state_revision": "revision_opaque",
    "committed_at": "2026-08-26T00:00:00Z"
  },
  "projection": {
    "schema_version": "loopx_post_writeback_event_projection_v0",
    "transition": "segment_completed",
    "fact_refs": ["fact_opaque"]
  },
  "boundary": {
    "raw_task_text_recorded": false,
    "raw_logs_recorded": false,
    "raw_trajectory_recorded": false,
    "raw_session_transcript_recorded": false,
    "credential_values_recorded": false,
    "absolute_paths_recorded": false
  }
}
```

Source identity 是 public-safe opaque identifier，不是 display name 或 raw provider
payload。Core 为每种 event kind 选择有界 projection schema；registration 不得请求
完整 writeback payload。

Input 必须排除 task prose、prompt、log、trajectory、transcript、credential、环境变量值、
local path、仓库内容和未注册外部引用。如果 boundary 无法证明 projection 安全，则不调用
该 hook，并生成隔离的 failure receipt。

## 8. Typed Intent Result

Provider 返回 `loopx_post_writeback_hook_result_v0`：要么是没有 intent 的
`not_applicable`，要么是带有界 intent list 的 `proposed`。示例 intent：

```json
{
  "schema_version": "loopx_post_writeback_intent_v0",
  "intent_id": "pwi_sha256_opaque",
  "hook_id": "periodic_report.runtime_trigger",
  "capability_id": "periodic_report",
  "source_dispatch_id": "pwh_sha256_opaque",
  "intent_kind": "periodic_report.trigger_evaluation",
  "operation": "evaluate_runtime_trigger",
  "policy_version": "weekly_v0",
  "payload": {
    "segment_ref": "segment_opaque",
    "source_event_refs": ["event_opaque"]
  },
  "budget": {
    "max_attempts": 1,
    "max_result_bytes": 16384
  },
  "requested_write_scope": [],
  "failure_policy": "isolate",
  "grants_new_action_authority": false,
  "external_write_performed": false
}
```

Core 仅在以下条件满足时接受 intent：

- hook、capability、dispatch 和 kind 与 admitted registration 匹配；
- 序列化结果没有超过 budget；
- payload 使用该 intent kind 的已知 schema；
- idempotency identity 与 semantic input 匹配；
- 没有宣称已执行外部写或获得新权限；
- requested scope 仅作为未来 admission 的声明输入。

对 periodic report 而言，最小可用 intent 只请求 trigger evaluation。既有
capability-owned trigger reducer 继续拥有 promotion 判断。后续执行仍使用 governed
`compose-run -> renderer -> authorized sink` 边界；hook 不跳过任何阶段。

## 9. 幂等、Replay 与冲突规则

`dispatch_id` 是 committed receipt identity、hook identity、registration schema
version 与 event kind 的稳定 digest。`intent_id` 是 dispatch identity、intent kind、
policy version 与 canonical typed payload 的稳定 digest。

Sidecar journal 强制以下规则：

- 同一 dispatch 与同一 canonical result 的 replay 是 no-op，并返回原 receipt；
- 同一 dispatch identity 产生不同 result 时判定冲突，拒绝新 result；
- registration、policy version 或 source receipt 改变时产生新 identity，不覆盖历史；
- primary writeback 与 sidecar checkpoint 之间崩溃时可重试同一 dispatch；
- completed sidecar receipt 不得从日志文本或 provider 自述中重建；
- external executor 还要基于 `intent_id` 再次去重，因为 hook 记录与 effect 执行是两个事务。

Journal 只保存有界 typed packet 与精简 failure code，不保存 raw provider exception、
task context、credential 或 external payload。

## 10. 监督、Budget 与故障隔离

Core 对每个 hook 和每次 dispatch 施加 invocation count、intent count、bytes 与 wall
time 上限。Profile 可以关闭整个 phase，或只准入已安装 hook allowlist。V0 按稳定顺序
独立运行各 hook；一个 hook 失败不会消耗另一个 hook 的 result slot。

Failure receipt 使用稳定 code，例如：

- `registration_rejected`；
- `input_boundary_rejected`；
- `producer_failed`；
- `producer_timed_out`；
- `result_contract_rejected`；
- `intent_conflict`；
- `dispatch_budget_exhausted`。

失败可观察，也可在有界 policy 下用相同 dispatch identity 重试。但它不：

- 改变主 durable receipt；
- 阻止匹配的 quota spend；
- 改变 Todo state 或 selected work；
- 创建 blocker 或 user-action gate；
- 调用 external sink；
- 消耗另一个 capability 的 quota。

重复失败未来可以产生 maintainer diagnostic projection 或单独准入的 repair Todo，
但该策略不属于 v0，也不得从 exception text 推断。

## 11. 最小可用实现切片

### Slice 1：Contract 与 inert registry

- 新增 TypeScript-owned registration、input、result、intent、dispatch receipt validator；
- 新增 Python callable adapter 与 deterministic registry；
- 覆盖 disabled、not-applicable、replay、conflict、budget 与 isolated failure 测试；
- 不接生产 settlement path，也不接具体 capability。

### Slice 2：一条主生命周期 seam

- 在一个 orchestrator-owned durable-writeback receipt boundary 接入 dispatcher；
- 持久化 sidecar dispatch receipt，并能在 replay 时恢复；
- 证明 quota-spend eligibility 与主 receipt 不变；
- 使用 inert synthetic hook，默认关闭。

### Slice 3：Periodic-report intent producer

- 在 composition root 注册 `periodic_report.runtime_trigger`；
- 把符合条件的 completion/replan projection 映射为一个 trigger-evaluation intent；
- 通过 fake scheduler 或 governed executor 把 intent 交给既有 periodic-report producer；
- 到 external sink 之前停止。

### Slice 4：验证后扩展

- 只有 receipt/replay parity 得到证明后，才扩展到其他 primary writeback path；
- authorized sink path 作为单独改动，显式定义 credential、write scope、readback 与 rollback。

每个 slice 都应能独立 review。Slice 1 发布不代表已有自动报告；Slice 3 发布也不代表
报告投递已经授权。

## 12. 验证矩阵

| Case | 必须得到的结果 |
|---|---|
| Phase disabled 或 capability 未安装 | Provider 零调用，无 sidecar intent |
| Registration 非法或超过 budget | Provider 调用前拒绝 registration |
| 主 validation 或 writeback 失败 | Post-writeback 零调用 |
| Committed writeback | 每个 admitted hook 恰好一个 dispatch identity |
| 主 writeback replay | 返回同一 dispatch receipt，不重复 intent |
| 同一 identity、不同 payload | 冲突被拒绝，原 receipt 保留 |
| Provider 抛错、超时或返回非法结果 | 精简隔离失败；主 receipt 与 spend eligibility 不变 |
| 多 hook | 稳定顺序、独立 budget、故障隔离 |
| Intent 请求 scope | 只记录 proposal，不授予权限、不执行 effect |
| Periodic-report completion threshold | Fake scheduler 下产生一个 typed trigger-evaluation intent |
| Periodic-report replan transition | Fake scheduler 下产生一个 typed trigger-evaluation intent |
| External-write 断言 | 单独 governed admission 前无网络或 sink 调用 |
| Public-boundary scan | 无私有名称、URL、路径、credential、transcript、raw log 或 provider payload |

E2E acceptance test 必须从真实 committed completion/replan writeback boundary 开始，
不能直接调用 periodic-report producer；它结束于 validated intent receipt，不结束于外部服务。

## 13. 被拒绝的替代方案

### 从 settlement 代码 import `periodic_report`

这会把一个 capability 变成 core 生命周期的一部分，并迫使未来 capability 重复耦合。

### 在 rollout-event append helper 内运行 hook

该 helper 是 best-effort diagnostic persistence。赋予它 orchestration 权威，要么让
diagnostic 阻塞主工作，要么让 hook 丢失被成功主 receipt 掩盖。

### 执行 `post_writeback_actions` 中的 command

Command string 没有 typed provider result、有界 payload、确定性 dedupe 或 effect
authority。它继续是 operator guidance，不是 v0 hook contract。

### 让 hook 直接调用 sink

这会合并策略与 effect 权威，绕过 write-scope admission/readback，并让重试不安全。

### 把 hook 增加为主 settlement step

可选 provider 的健康状态会支配 quota accounting 与 Turn completion。Sidecar 边界
才能保留主 settlement 事实。

### 原样复用 `interaction_projection`

该 phase 是 read-time、write scope 为空、映射 typed projection slot，且在 primary
effect 之前评估。Post-writeback dispatch 则有 receipt source、replay identity、
sidecar journal 与 intent output。复用同一 schema 会掩盖实质不同的生命周期语义。

## 14. Promotion Gate 与开放实现选择

以上架构决策对 v0 已稳定。第一条 wiring PR 仍需确定两个实现选择：

1. sidecar journal 放在 Turn journal 旁，还是独立 hook-outbox 路径；无论选择哪种，
   都必须保持 atomic per-dispatch dedupe，并可从 committed receipt 恢复；
2. host 在 checkpoint 后立即 dispatch，还是从 recovery queue dispatch；两者必须保持
   相同 identity、故障隔离与主 spend 语义。

只有 maintainer 接受 ownership 拆分、且一份公开测试 packet 通过 Slice 2 的验证矩阵，
一般实现才通过资格验证；RFC 合入即接受设计。自动 external delivery 还需要单独接受 effect-boundary 改动。
