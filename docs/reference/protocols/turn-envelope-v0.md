# TurnEnvelope v0

`loopx_turn_envelope_v0` is an additive, bounded read model over an already
computed `quota should-run` decision. It gives an agent the next action and its
safety contract without replaying every diagnostic lane in the full quota
payload.

Preview it explicitly:

```bash
loopx --format json quota should-run --goal-id <goal-id> --agent-id <agent-id> --turn-envelope
```

For a tool with bounded output, optionally capture the full decided payload before
its display projection. Choose a **new directory for each invocation**, even when
reentering the same Turn:

```bash
loopx --format json quota should-run --goal-id <goal-id> --agent-id <agent-id> \
  --turn-instance-id <turn-id> --turn-envelope --decision-output-dir ./guard-001
cat ./guard-001/decision.json
```

`--decision-output-dir` is valid only with `quota should-run` and an explicit
`--turn-instance-id`. Its parent must exist. LoopX creates the directory privately
(0700 on POSIX) before running the guard, rejects existing paths including
symlinks, and atomically publishes `decision.json` (0600 on POSIX) before printing
the result. The file contains the unprojected decision and its original receipt;
it excludes display-only host poll metadata. It can contain private Goal context:
keep it out of public artifacts. No capture is created without this option.

If a tool truncates the displayed JSON, read this file instead of rerunning the
guard merely to recover the observation. The file is **not fresh authority**:
Todo selection, lease changes, cancellation, quota changes or other required
revalidation still use a fresh guard invocation and a new capture directory.
Check the saved Goal/Agent/Turn identity before use; a cache filename does not
prove identity. Nothing here bypasses the owning mutation-time validation.
Capture also preserves decided rejection/diagnostic payloads; inspect `ok` before
acting. A late disk failure can happen after the guard has committed its receipt;
the CLI reports that boundary rather than claiming rollback. An empty capture
directory is not a usable decision. Omit the option to disable capture; remove
unneeded local captures through ordinary file management.

When both capture and `--turn-envelope` are requested, `detail_ref.full_decision`
points to the saved file using a quoted POSIX `cat --` command. A file-capable host
can read `detail_ref.captured_decision.path` directly instead. The typed projector
includes the expected Goal/Agent/Turn and a reference to the existing canonical
source-decision hash (a hash of the JSON value, not the formatted file bytes).
Verify those against the read observation, including `ok`; the link itself does
not verify file integrity or make saved admission current. Missing, malformed,
or mismatched files require recovery, never automatic guard replay. Consumers already referring to `full_decision`, such as compacted capability
context and peer diagnostics, now read this same observation. Scheduler-specific
legacy detail requests are unchanged. Replan obligations and fresh selection/lease/quota checks retain their existing owners.
Without capture, the historical full-decision route and all default outputs are
unchanged. This opt-in detail link does not change action-signature coverage.

中文：同时开启短包和完整 decision 保存时，详情入口读取本次保存的文件，不再
为补读上下文重跑 guard。读取前核对身份和源哈希；旧观察不提供新的执行权限。
文件损坏或丢失不能自动重跑，选 Todo、lease 变化等仍需按原契约重新准入。

This is a CLI transport primitive under the existing quota/context owner, not a
new capability or Python decision rule. It does not automatically switch workers
to compact packets, select replan history, or qualify model efficiency. The
frontend and Lark do not consume these local files; their entrypoints are unchanged.

The envelope flag selects a projection of the full decision. The original v0
contract left the default `quota should-run` output unchanged; the
[PR-05 migration](protocol-action-packet-decision-v0.md) omits
`protocol_action_packet` from new full decisions, including live, paused and
recovery output, from the first release containing #4794. Historical v0 reads
remain supported for the v0 reader lifetime. The v0 envelope keeps:

- the selected todo, claim, and effective action;
- the bounded action portfolio when the agent must choose among multiple
  admitted actions before delivery;
- the bounded read-only planning horizon when selected work has strategic
  Todo, relation, or goal-acceptance context;
- concrete user actions and gate reasons;
- required reads;
- write scope, approvals, guards, workspace/capability gates, and stop rule;
- delivery, repair, safe-bypass, and blocked-action policy;
- validation/writeback and quota-spend policy;
- the current scheduler action and cadence acknowledgement command.

The envelope also carries a bounded `contract_capsule` for interaction mode,
work-lane and execution obligations, successor/replan duties, automation
liveness, vision/handoff state, and actionable warning references. A canonical
`action_signature` is independently built from the full decision and from the
envelope; matching hashes prove the covered action dimensions agree for that
projection. They do not prove that every possible quota state has test
coverage.

Action-signature coverage is versioned independently from the envelope schema.
`turn_envelope_action_dimensions_v0` covers the original action projection;
`turn_envelope_action_dimensions_v1` additionally covers a blocking user
gate's `response_plan`; `turn_envelope_action_dimensions_v2` additionally signs
`action.action_portfolio`; `turn_envelope_action_dimensions_v3` additionally
signs `action.planning_horizon`; v4 additionally signs capability `agent_context`;
`turn_envelope_action_dimensions_v5` additionally preserves the canonical
`writeback.settlement_plan`. Base/head qualification accepts a declared
coverage migration as a review signal. The bounded, JSON-only v2 and v3
migration budgets apply only to their named schema transitions; ordinary
growth limits resume once the new version is the baseline. A digest change
without a supported coverage migration, or a projection above its one-version
budget, still fails closed.

For a decision carrying a settlement plan, the opt-in envelope now transports
that plan intact: effect identity, ordered steps, command conditions, expected
receipts and the host-owned handoff. It also keeps the corresponding next CLI
commands untruncated. This repairs the earlier preview that could report matching
action hashes while omitting the settlement plan. The plan remains owned by the
shared settlement algebra; the envelope neither rebuilds it nor grants authority
to execute an unadmitted step. Packets without a plan keep their previous coverage
and do not acquire one from a historical heartbeat receipt. Stored v0–v4 signatures
are not rewritten. The v5 migration is an explicit semantic review signal and
has **no additional size allowance**; overflow still requires the existing budget
analysis. Default full quota output and settlement rules are unchanged.

Newly generated turn-scoped settlement commands include global `--format json`
before the subcommand. This changes the generated command output default for App
heartbeat, generic CLI, and visible Goal lanes, in both full decisions and
TurnEnvelope. It also applies to `settlement_owed.command` after writeback.
Execute the returned command after filling its declared placeholders; do not
append `--format json` after `quota spend-slot`, where it is not a subcommand
option. Inspect both the exit code and JSON `ok`. Direct CLI invocations retain
their existing output defaults; stored commands and receipts are not rewritten.
This is command rendering over the existing settlement owner: validation,
identity, step conditions, authority and one-spend semantics are unchanged.

中文：新生成的结算命令自带位置正确的全局 JSON 参数，完整包、短包及写回后的
补结算命令保持一致。填充占位项后直接执行，检查退出码和 JSON `ok`；不再需要
手工在子命令后追加格式参数。直接 CLI 的默认格式、历史回执与结算权限均不变。

The shared CLI plan also explains delivery classification before writeback.
Validated evidence that excludes a route and informs the next decision can be
`outcome_progress` even when the attempted candidate does not improve the target
metric. A failed attempt, an unchanged metric, or a new Todo alone does not prove
progress. Record the validated evidence and its consequence. `outcome_gap` is the
existing blocked-settlement path: it requires `--progress-result-class blocked`,
blocker/evidence IDs, and the existing continuation checks. These authoring hints
neither judge evidence nor relax validation, completion, lease, or spend rules;
an invalid `outcome_gap` plus `advanced` still fails before writeback. The same
plan reaches full CLI output and the opt-in envelope. This changes agent-facing
guidance in both views. Full, compact, brief and thin heartbeat prompts scope
no-refresh/spend closeout to exact monitor settlement; auxiliary polling leaves
the original work settlement due. Admitted work follows its settlement plan;
an unchanged artifact alone does not imply no progress. The shared heartbeat
renderer supplies this wording, while the TypeScript settlement owner retains
classification semantics. This adds no capability, automatic classification, or
frontend/Lark operation. Model error-rate reduction remains unqualified.

中文：短包现在完整保留已有结算计划及执行命令，签名 v5 覆盖结算身份、步骤顺序、
条件和宿主边界；没有计划的输入不会凭空获得结算权限。旧签名保留，默认完整输出
不变，大小预算不放宽。此修复是短上下文实验的前置条件，尚不证明模型收益。

`quota_planning_horizon_v0` remains advisory even when carried by the envelope.
Its `selection_contract` points back to `selected_todo` and `action_portfolio`,
and `horizon_changes_selection=false`. Effect Program transports this
observation; the TypeScript work-item reducer owns its ordering and bounds.
The quota projection keeps the horizon's typed `detail_refs`. TurnEnvelope does
not copy those commands a second time: it emits
`action.planning_horizon.detail_refs_ref="$.detail_ref"`, and the existing
top-level cold path owns the full-decision, Todo, and status reads. This
transport compaction is covered by the same action signature and does not
change horizon completeness or selection authority.
See [`quota_planning_horizon_v0`](quota-planning-horizon-v0.md).

For `quota_action_portfolio_v2`, the envelope carries the recommendation and
bounded, non-exhaustive `suggested_actions`, but neither is a settlement
identity or permission list. When the full interaction contract says
`selection_required=true`, the agent must rerun quota in the same turn with any
currently authoritative, same-agent, capability-ready Todo. The full decision's
`selection_command.command_args_template` is a rendering template, not a
permission list. It and `candidate_discovery_args` share one bound
`route_prefix`; the discovery route exposes the current open agent queue
when the bounded suggestions are insufficient. The requested Todo remains
pending until the second guard re-runs current lane arbitration and eligibility;
only a qualified request upgrades the identity-less receipt. A newly due hard
lane leaves the receipt unbound, and only the resulting receipt-bound envelope
is a delivery contract.

An unbound selection that no longer qualifies is a preflight outcome, not a
settlement-identity conflict. The full quota response preserves the TypeScript
`action_selection_qualification_v0` result and returns
`quota_action_selection_deferred` or `quota_action_selection_rejected`, including
the exact current preemption or eligibility reason. An existing identity-less
receipt appends an identity-less `pending_action_selection` revision when an
otherwise eligible explicit choice is deferred. That revision is not delivery
or settlement authority: it preserves the explicit choice only so a no-argument
same-Turn reentry cannot replace it with the current recommendation. An
ineligible/rejected choice still replays the receipt without mutation; a
first-call rejection reports
`heartbeat_receipt.status=not_committed` and writes no receipt event.

For an eligible explicit choice deferred solely by runnable autonomous replan,
the typed qualifier adds `inline_reentry_allowed=true`. After durably retaining
that choice, the CLI now executes one same-Turn guard reentry and returns its
fresh result directly, including in TurnEnvelope mode. This changes the previous
default of returning a failed selection before asking the caller to reenter.
It grants no delivery or spend authority: the fresh guard and receipt owner
still decide the Todo/replan binding, and may return a newly changed gate. It
does not retry other hard lanes, missing candidates, or a first-call refusal
without a durable choice receipt. The reentry has no explicit selection, so it
cannot recursively retry. Hosts that do not execute this CLI path can continue
to use the existing recovery command.

For other refusals, the agent receives
`recovery_action=reenter_guard_without_selection` and one executable
same-Turn guard in the full decision's `cli_channel.next_cli_actions`; the compact
envelope preserves the recovery in its action and writeback preview. The failed
selection exposes no settlement plan, spend command, or unadmitted replan action
packet. Execute that guard without
a Todo/replan argument before following the resulting binding or portfolio. A
receipt already bound to a different Todo or autonomous replan obligation
remains a hard `heartbeat_receipt_identity_conflict`. On reentry, an identical
projected Todo may bind normally. If a hard autonomous replan owns the current
lane, the replan receives the Turn's settlement identity and the retained Todo
is reported as `deferred_to_fresh_turn`; a different recommended Todo never
inherits the retained choice or its authority.
When a due monitor is visible only as auxiliary context for an advancement lane,
the typed reason is
`auxiliary_monitor_not_selectable_in_advancement_lane`. The agent selects a
current advancement Todo or retries after the monitor becomes the hard lane;
this state is never reported as a receipt write failure.

A selection may also qualify while repository delivery is temporarily blocked
by the peer workspace guard. In that case the response and bound receipt keep
the selected Todo, `effective_action=agent_workspace_repair`, and the typed
worktree recovery instruction. Moving to an independent worktree and rerunning
the guard with the same Turn id resumes the selected Todo; the wrapper must not
rewrite this recoverable state as a settlement-identity conflict.

For admitted local delivery, the workspace hint points to the registered Goal
workspace and defers isolation requirements to the current workspace guard and
repository rules. A peer identity alone does not require moving to another
worktree. This corrects the previous unconditional Git-peer hint in both full
quota output and TurnEnvelope; admission and settlement enforcement are unchanged.

An executed, turn-scoped `quota monitor-poll` is a no-spend closeout only when
its observed Todo exactly matches the Turn's `settlement_todo_id`. That response
includes `turn_continuation.next_turn_required=true` and requires a fresh Turn
before unrelated work. An admitted auxiliary monitor uses its own observed Todo
while retaining the advancement Todo as `settlement_todo_id`; its continuation
keeps `current_turn_settled=false` and `next_turn_required=false` so the original
writeback and spend can finish. A missing exact or typed auxiliary binding never
claims settlement.

Portfolio v2 preserves v1's selection policy, candidate ordering, and
settlement rules, and adds an optional `continuation_hint` to each suggested
action. The default quota producer and Turn controller now require v2. The
compact quota CLI view uses the independently versioned
`quota_cli_action_portfolio_compaction_v1` detail marker and inlines candidate
`text`, `priority`, `action_kind`, and `continuation_hint` alongside the v1
identity fields. TurnEnvelope keeps the same `loopx_turn_envelope_v0` outer
schema and v2 action-signature coverage; only its nested action portfolio
version changes. Hosts that strictly accept v1 must update before consuming
the new default. LoopX does not dual-emit or negotiate a v1 downgrade, so an
unknown nested portfolio version must fail closed. Ignoring an absent
`continuation_hint` remains valid when reading stored v1 evidence, but it does
not make a v1-only live decoder compatible with the v2 producer.

`loopx turn plan` and `loopx turn run-once` have no agent selection phase before
they build the host transaction. When such a Turn sees a v2 portfolio, its
outer controller binds the advisory primary by rerunning the same current
eligibility qualification, retains the portfolio in the envelope for audit,
and marks the selected Todo with
`selected_by=turn_controller_advisory_primary`. This deterministic compatibility
path does not apply to heartbeat/model turns: their first response remains
identity-less and delivery-blocked until the agent explicitly chooses.

The compact envelope does not truncate those executable commands into unusable
strings. It carries non-exhaustive `writeback.suggested_todo_ids` plus
`selection_command_ref`; the full decision remains the authority for exact argv.

Historical full decisions may carry `protocol_action_packet`. For those inputs,
the envelope reconstructs its ordered semantic fields from `action`, `user`,
work-lane, automation, and scheduler contracts, while carrying the explicit
`llm_policy=no_api` invariant. Exact reconstruction retains the source summary
hash and derivation status; a differing compact action retains field-level
`residue`; an opaque summary follows `unverified_retain_summary`. These remain
historical read paths and do not rewrite stored packets or envelopes.

Under PR-05, a new source without a packet produces no
`contract_capsule.protocol_action_packet` witness. The ordered semantic
projection `protocol_action_packet_fields` and historical summary renderer
remain; packet absence does not remove typed obligations or their signature
coverage. Source and envelope signature documents must match for that input.
Compared with a packet-bearing source, the document may lack the capsule's
packet witness and have a different hash. This is not a cross-version hash
compatibility promise; existing signature checks and historical signatures
remain intact. The v1.1.0 reader/host accepts the tested new and stored v0 examples.
The [migration contract](protocol-action-packet-decision-v0.md) defines the
release boundary, v0 reader lifetime, consumer set and rollback steps; unknown
external readers and complete private archives are not implicitly qualified.

Large todo summaries, frontier diagnostics, readiness history, compatibility
fields, and warning collections stay on the referenced full-decision/status
cold paths. The envelope has an **8 KiB compact UTF-8 JSON performance target**,
not an execution-admission limit. `compaction.envelope_utf8_bytes` measures the
final packet, including diagnostics. The historical `source_json_bytes` and
`envelope_json_bytes` fields still count Unicode code points for v0 compatibility;
do not use them as wire-byte measurements.

### Budget warnings and allocation

Oversize valid envelopes keep their normal Turn plan/controller route. They
report `compaction.within_budget=false` and a structured
`warning.code=turn_envelope_budget_exceeded`, with `excess_bytes`, additive
`section_bytes` and `over_target_sections`. JSON carries this through the Turn
plan and host request; Markdown plan/envelope output calls out the warning.
Schema, signatures, identity, permissions, receipt validation and execution
quota are still hard gates. This changes previous behavior for **all Turn hosts**:
packet growth alone no longer produces `contract_error` or stops a Turn loop.

The TypeScript owner keeps review allocations totaling 8,192 bytes. These are
diagnostic targets, not permission to truncate fields or hard per-section caps:

| Section | Target bytes | Included fields |
| --- | ---: | --- |
| action | 800 | action, user, required reads, replan packet, response plan |
| boundary | 2,000 | boundary and execution policy |
| writeback | 600 | validation/settlement commands and policy |
| scheduler | 600 | scheduler action and acknowledgement |
| contracts | 1,800 | contract capsule |
| context | 1,400 | capability context and task orchestration |
| transport | 992 | identity/metadata, signatures, cold-read commands, diagnostics |

Counts include JSON property names, delimiters and UTF-8 text. Their sum equals
the measured final packet; dividing each by `envelope_utf8_bytes` gives its
share. Diagnostic detail is emitted only on overflow, not every normal Turn.
Use the existing `quota should-run --turn-envelope` or `turn plan` JSON output
to inspect the breakdown. Record a public-safe reproduction and compare each
section with the same fixture on the baseline before changing its owner.
First remove repeated presentation or move non-actionable detail to an existing
cold read. Never trim write scope, executable arguments, signatures or required
reads to silence a warning, and do not simply raise the target. The cold-read
commands remain; their redundant human-readable `contains` inventory is retired.

Repository size/parity canaries remain blocking **delivery-time regression
checks**, independent of runtime warning semantics. Representative fixtures must
still fit the target. A warning is a performance investigation signal, not an
automatic Todo, new authority, or permission to spend an extra Turn.

中文：TurnEnvelope 超出 8 KiB 后产生可分析的 warning，不再仅因大小中断合法
Turn。按最终 UTF-8 字节数统计各部分占比，先压缩重复展示内容，再检查对应规则
所属模块；不得截断权限、签名或执行指令，也不应单纯提高预算掩盖增长。
身份、权限、签名和执行配额仍是硬门禁；仓库的体积与语义回归检查仍阻止交付。

Hot-path fields may use explicit references when the inline value would only
repeat another authoritative field. In particular,
`action.selected_todo.text_ref = action.recommended_action` means the selected
todo text is already present as the recommended action. Scheduler reset plans
keep the exact acknowledgement argv inline when it satisfies the executable
argv limits; the failure argv stays behind `failure_cli_args_detail_ref` until
the host update actually fails. Consumers must follow these references instead
of treating the omitted duplicate as missing state.

This contract is a projection only. It does not change quota selection, todo
routing, scheduler state, history writes, or state transitions. Promoting it to
the default agent view requires separate parity evidence across delivery,
monitor, user-gate, capability-gate, workspace-guard, and blocked states.

## Multi-State Parity Evidence

`tests/fixtures/turn_envelope_state_matrix.json` is the durable synthetic
promotion fixture. It covers delivery, monitor quiet-skip, user gate,
capability gate, workspace guard, autonomous replan, successor replan,
blocked, and throttled decisions. Every case must preserve the canonical action
signature and remain within the 8 KiB budget. Historical packet-bearing inputs
must retain their reconstruction, residue, or opaque-summary witness; inputs
without a packet must preserve typed obligations without inventing a witness.

The matrix records exact measurements in validation rather than treating a
dated size range as the contract. This keeps the projection available as an
opt-in host view. It is not sufficient to change
the default CLI response: default promotion still requires shadow parity from a
real host integration, no consumer regression with the full decision available
as a cold path, and explicit compatibility acceptance for the default-view
change.
