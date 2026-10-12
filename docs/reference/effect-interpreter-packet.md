# Effect Interpreter Packet

This page documents the canonical read lens for `quota should-run`:

```text
effect_request -> interpretation -> observation -> next_effect
```

It does not add a new runtime contract. It names the existing packet fields
that already play each role.

## Code Lens

`loopx.control_plane.effect_program.interpret_quota_should_run_packet` maps an
existing `quota should-run` packet onto the canonical slots, and
`interpret_turn_result_packet` maps an existing `loopx_turn_result_v0` packet:

- `EffectRequest`
- `EffectInterpretation`
- `EffectObservation`
- `EffectNext`
- `EffectTurn`

Both functions are intentionally read-only. They do not replace quota
decision or turn-settlement logic; they give refactor and test code one
stable abstraction for reading the effect program shape across packet
families.

The action slot depends on the packet family:

| Observation | Verdict | `effective_action` |
| --- | --- | --- |
| Quota should-run | Quota decision | Existing decision/frontier action string |
| Turn result | `decision` carries `result_kind` | Always JSON `null` / Python `None` |
| Journal replay | `replay_legal` or `replay_blocked` | Omitted from the internal replay observation |

Turn-result readers must use `decision` for the result verdict. The result lens
ignores a host-supplied action; it cannot create a quota decision. The TS result
type fixes its action to null, while the generic quota type retains its default
string action. This changes the transient result projection, not persisted
host results, receipts, or journal plans. See the
[semantic vocabulary RFC](../architecture/rfcs/semantic-vocabulary-convergence-v0.md#m1-action-domains-and-compatibility)
for the versioned frontier migration and its compatibility boundary.

动作槽位按 packet family 区分：quota 保留决策/前沿动作字符串；Turn result 的
判决从 `decision` 读取，`effective_action` 固定为 `null`（Python 为 `None`）；
journal replay 使用自己的判决，不再输出动作字段。host 提供的 action 不会被
解释为 quota 决策。这只改变瞬时结果投射，不重写落盘的 host result、receipt
或 journal plan。

## Interaction consistency validation

The TypeScript interaction-contract owner supplies the execution-consistency
rules used by the Effect interpreter, scoped-override projection and Python
scheduler arbitration. Delivery requires an attempt; a quiet no-op cannot
coexist with required work or user action. Terminal, peer-blocked and
monitor-only modes require `quiet_noop_allowed=true`, no required action or
delivery, and no quota spend. An already-settled mode permits neither required
work/user action, delivery nor another spend. Non-blocking notifications remain valid.
Independent capability intent retains its own mode and existing authority.

This strengthens transient contract validation: typed consumers now reject the
closed-mode contradictions that scheduler arbitration already rejected, and
all consumers reject a closed mode with `spend_allowed_now=true` or
`spend_after_validation=true`. TurnEnvelope construction uses the same interpreter
before signing its action. Missing spend metadata remains false for older packets;
a present value must be boolean. Python retains structural diagnostics and cadence selection, while
semantic rules come from the existing Effect runtime. Runtime unavailability
cannot fall back to a second Python rule. No persisted receipt is rewritten and
these checks do not grant execution, lease or charging authority.

TypeScript interaction-contract owner 统一提供 Effect 解释器、scoped-override
投影与 Python 调度仲裁使用的执行一致性规则。delivery 必须有 attempt；quiet
no-op 不能与必须执行的工作或用户动作并存。terminal、peer-blocked、monitor-only
模式必须允许 quiet no-op，不得要求动作、允许交付或扣费；已结算模式也不得重新
要求工作或用户动作、允许交付或扣费。非阻塞通知仍然有效。
独立 capability intent 保留自己的模式与现有权限。

本次加强瞬时合同校验：类型化消费者开始拒绝此前调度仲裁已拒绝的关闭模式矛盾，
消费者都拒绝关闭模式的 `spend_allowed_now=true` 或 `spend_after_validation=true`；
TurnEnvelope 构造在签署动作前也使用同一解释器。旧 packet 缺失扣费字段时仍按
false 处理，显式提供的值必须是布尔值。Python 保留结构诊断与 cadence
选择，语义规则来自已有 Effect runtime；运行时不可用时不回退到另一套 Python
判断。不重写历史回执，也不授予执行、租约或扣费权限。

## Turn Journal Lens

`interpret_turn_journal` reads an existing fenced Turn journal and returns an
`EffectTurn`. It compares goal, agent owner, and Turn-key identity across the
journal, stored plan, typed settlement identity, host result, and receipt. It
also validates that completed phases are an ordered transaction prefix and
exposes retained `committed`, `stopped`, and `failed` journal tombstones.

`request.context.replay_legal` is only the effect-free terminal replay signal. Identity,
phase-order, and terminal-status failures appear together as stable typed
violation values in `request.context.violations`; semantic mismatches return a
blocked observation instead of raising an exception.

`EffectObservation.should_run` remains false and `EffectNext` remains empty, so
inspection itself never grants effect authority. The same interpreter also
projects `recovery_decision`, which is the executor's recovery plan: its action,
whether continuation is allowed, the phase to resume, whether Host must be
invoked again, a typed reason, and only the checks that participated. The real
Turn executor consumes that decision before continuing. `replay_legal=false`
therefore does not mean that an `in_progress` or `scheduler_action_required`
journal is unrecoverable.

The public read-only consumer is:

```bash
loopx turn inspect-journal \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --turn-key <sha256:64-hex-digest> \
  --format json
```

Add `--retry-failed-turn` to evaluate the same explicit failed-Turn retry used
by `turn run-once`. When the failed Host recorded `resume_session`, inspection
performs the current read-only Session Binding check; without an explicit retry
request, the decision reports `failed_retry_not_requested`.

It resolves only the canonical journal location. There is no arbitrary
`--journal-path` input. The command validates selectors, takes the existing
journal lock, schema-checks the stored JSON, and returns the versioned
`loopx_turn_journal_inspection_v1` projection. Version 1 retains every v0 replay
and integrity field and adds `journal_consistent`, `recovery_decision`, and the
optional `last_recovery` audit. The audit contains only the adopted public-safe
plan and its bounded actual status, completed phase ids, and Host-invocation
boolean. It never contains Host logs, Session content, provider payloads, or
paths.

Journal consistency is fail-closed over the canonical typed settlement
identity as well as Journal/envelope lineage and phase ordering. Settlement
goal, agent, Turn instance, binding, and effect id must all validate and bind to
the inspected Turn before the shared recovery decision can authorize any
provider call. In the current Turn driver, the binding is the envelope's
selected Todo (or adaptive primary Todo override); a different canonical Todo
identity is still inconsistent and fails closed.

JSON and Markdown render the same projection. They do not expose raw journal,
plan, host-result, or receipt bodies; request context; capabilities;
recommended actions; credentials; evidence; or resolved local paths. A
successfully interpreted `replay_blocked` journal exits zero because replay
legality and recoverability are separate diagnostic data. Invalid selectors,
missing journals, malformed
JSON, and unsupported schemas exit non-zero.

## Canonical Example

### 1. Effect Request

The agent or host proposes the next bounded turn. The inputs include:

- `goal_id`
- `agent_id`
- `available_capabilities`
- host surface and scheduler execution context
- current host RRULE where relevant

### 2. Interpretation

The harness interprets the request through:

| Packet field | Role |
|---|---|
| `work_lane_contract.lane` | Route: advancement, monitor, gate, or wait |
| `work_lane_contract.obligation` | What the selected route must do |
| `interaction_contract.mode` | The host-facing interaction mode |
| `capability_gate.action` | Capability decision when a gate is present |
| `scheduler_hint.cadence_class` | Timing decision for the next host wake |

### 3. Observation

The decision is returned as:

| Packet field | Role |
|---|---|
| `decision` | Run, skip, observe, or repair |
| `should_run` | Whether compute is allowed |
| `effective_action` | Machine-visible effective action |
| `recommended_action` | Next concrete action text |
| `action_portfolio` | Primary plus bounded typed fallbacks, when present |
| `protocol_action_packet.summary` | Optional historical actor-facing summary; no action authority |

For a source without `protocol_action_packet`, the quota lens exposes
`protocol_summary=null`; typed interaction, lane, and scheduler contracts still
supply obligations and next effects. Historical v0 and opaque summaries remain
readable observations and cannot override those contracts. The
[PR-05 migration](protocols/protocol-action-packet-decision-v0.md)
defines packet-free new quota/live/paused/recovery output from the first release
containing #4794. It names the supported bundled consumers, retained v0 format
lifetime and tested v1.1.0 rollback baseline; unknown external clients are not
automatically covered.

`EffectTurn.observation.action_portfolio` is the canonical TypeScript-owned
observation of this field. Python supplies only scope/capability-admitted todo
rows; the TypeScript reducer validates identity, removes duplicates, bounds the
list, and fixes the execution-failure trigger before the Turn envelope signs
it.

### 4. Next Effect

The observation points back into the loop:

| Packet field | Role |
|---|---|
| `interaction_contract.cli_channel.next_cli_actions` | Next CLI effects |
| `execution_mode` | Execution strategy (`serial` / `parallel` / `interleaved`) for an ordered effect program |
| `scheduler_hint.action` | Scheduler around decision |
| `scheduler_hint.cadence_class` | Cadence for the next host wake |
| `scheduler_hint.app_automation.ack_hint.cli_args` | Host ACK effect |
| `scheduler_hint.app_automation.failure_hint.cli_args` | Host failure effect |

`EffectTurn.next_effect` is the code lens for this slot. It keeps the
data-encoded handler visible: the host invokes the CLI actions and settles
success or failure through the ACK/failure hints instead of LoopX holding a
callable across turns. `execution_mode` is the data-encoded strategy when the
next effect is an ordered effect program; it defaults to `None` when the
packet does not declare one.

## Around Semantics

`capability_gate`, `interaction_contract`, `work_lane_contract`, and
`scheduler_hint` are around decisions over the canonical effect step, not
separate feature modules:

| Around layer | Can short-circuit | Can rewrite |
|---|---|---|
| `capability_gate` | `ask_owner`, `repair_bridge`, `unsupported` | Repair todo and next CLI actions |
| `interaction_contract` | `action_required`, `mode` | Primary/protocol action and notification |
| `work_lane_contract` | Monitor/inbox preemption, `must_attempt_work=false` | Lane, obligation, `next_lane` |
| `scheduler_hint` | Pause/delete heartbeat, no-spend quiet | RRULE, cadence, stateful backoff |

The ordering and effect semantics are contracts. A capability gate must not be
collapsed into a generic exception handler: `owner_missing`,
`repair_missing`, and `decision_owner` stay visible because `ask_owner` and
`repair_bridge` lead to different next effects.

A CLI packet is a higher-density effect than a single tool call: one command
can carry permission, budget, validation, execution, failure semantics, ACK,
and writeback. Vendor serial or interleaved tool APIs are execution modes
inside the interpreter, not new state machines.

## Ordered Effect Program

`loopx.control_plane.effect_program.effect_program_from_ordered_steps` maps an
existing `guided_transaction.ordered_steps` value onto `EffectProgram`:

- `EffectStep` keeps `step_id`, `kind`, `command`, and `purpose`;
- `EffectProgram` keeps ordered steps and an optional `execution_mode`.

This is still a read-only lens. The executor remains host-driven until a
LoopX runtime caller owns multi-step execution.

## Terminal Closeout Ordering

The settlement plan keeps final Goal closure distinct from ordinary Todo
continuation. Its ordered contract is:

```text
validation -> durable_writeback -> quota_spend -> terminal_closeout?
```

`terminal_closeout` is conditional: it is present only when the validated
completion declares `no_followup`. Ordinary successor completion remains a
Todo-lifecycle action and does not pretend to be a terminal settlement step.
The final closeout must prove the same effect identity and matching writeback
and spend receipts before it may make the Goal terminal.

This order is deliberate. Completing the final Todo first would make strict
terminal guards reject the spend that accounts for the same material effect.
The repair is not an after-terminal spend exception: terminal state remains
strict, and the closeout moves after spend. If closeout fails, its journaled
receipt may be retried without repeating writeback or spend. Scheduler apply
and ACK remain host handoffs outside this settlement chain.

## Relationship To State Machines

Each state family is an interpretation table over this lens:

```text
input effect -> interpreter -> decision -> observation -> next effect
```

See the
[Agent Loop Effect Interpreter RFC](../architecture/rfcs/agent-loop-effect-interpreter-v0.md)
and
[Harness Is the Effectful Program](../development/control-plane-course/01-agent-loop-effectful-program.md).
The public framing comes from 齐梦星空,
[主线一：Agent Loop 是 effectful program(1)](https://www.xiaohongshu.com/discovery/item/6a01d501000000003700c5de?source=webshare&xhsshare=pc_web&xsec_token=ABqpNuladcxhev099wLKw8M3ilhKBua0BQXNpxnBZEGkc=&xsec_source=pc_share).
