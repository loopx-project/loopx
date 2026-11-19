# loop_turn_loop_disposition_v0

`loop_turn_loop_disposition_v0` is the pure Turn Loop Controller transition
contract. It decides what a governed loop does next from one validated Turn
receipt plus a fresh quota/scheduler decision, and nothing else.

`loopx turn run-once` remains the atomic governed executor: decide, execute one
bounded host segment, validate independently, write back, spend once. The
controller does not replace it, schedule processes, call host wake APIs, invoke
a model, sleep, write state, or spend quota. Scheduler process management,
host-specific wake adapters, and operator presentation are later slices in the
Turn Loop Controller plan.

The controller is an exported transition API, not a loop implicitly started by
`loopx turn run-once`. `loopx turn managed-step` calls `decide_loop_disposition`
for one already-journaled failed Turn and returns the typed answer without
executing anything, which is the first production consumer of the transition.
The CLI still does not persist a `BoundedTurnBudget`; both `max_turns` and
`completed_turns` must be supplied by an integrating caller, and there is no CLI
or product default of three Turns. The budget applies to continued
`validated_progress` on the same Todo, not a chain of completed Todos and not
fine-grained planning mode.

The proposed [controller model session](../../architecture/rfcs/controller-executor-unified-contract-v0.md)
is a separate role: it reasons about work and exchanges tasks/results with an
executor. This deterministic transition owner continues to validate disposition
inputs; the model session neither replaces it nor acquires scheduler authority.

中文：[controller 模型会话提案](../../architecture/rfcs/controller-executor-unified-contract-v0.zh-CN.md)
中的 controller 负责工作判断与任务/结果交互；本页的纯状态转移 owner 继续验证
接续输入。模型会话不替代该 owner，也不因此取得 scheduler 权限。

## Inputs

| Input | Shape | Notes |
| --- | --- | --- |
| `turn_receipt` | one `ValidatedTurnReceipt` qualified from `loopx_turn_execution_v0` | may be absent when no Turn has run yet; material results require the complete M7 settlement evidence described below |
| `quota_decision` | fresh `loopx_turn_envelope_v0` | must satisfy the shared typed envelope contract |
| `predecessor_turn_key` | causal binding supplied by the outer continuation adapter | required with a receipt and must equal its `turn_key`; it is deliberately not an unsigned field inside the quota envelope |
| `bounded_turn_budget` | one `BoundedTurnBudget` | required when the receipt is `validated_progress` |

Inputs are typed and validated at the boundary. The controller does not accept
caller-authored `result_kind + lineage` or phase-only mappings. A receipt is
qualified from one public `loopx_turn_execution_v0` whose transaction receipt
has `ok=true`, a supported result kind, full `(goal_id, agent_id, todo_id)`
lineage, and a `turn_key`. Material results (`validated_completion` /
`validated_progress`) additionally require all of these facts:

- execution and transaction receipt are both `committed`;
- the core M7 settlement succeeded and emitted exactly the ordered
  `validation -> durable_writeback -> quota_spend` receipt chain;
- every settlement receipt is committed under the same `effect_id`, and that
  id matches the transaction's typed settlement identity;
- public execution effects prove durable state write and one quota spend;
- the scheduler handoff completed;
- `validated_completion` carries the durable Todo lifecycle outcome
  (`successor`, `active_goal`, or `no_followup`) from the required explicit
  `completion_continuation` field. Missing or contradictory completed state is
  rejected rather than inferred.

This keeps settlement truth in the core Effect Program rather than duplicating
it as Turn-controller phase logic. A budget must carry strict integer domains (`type(...) is int`,
`max_turns > 0`, `0 <= completed_turns <= max_turns`) and the same lineage as
the fresh decision. When a receipt is supplied, the outer adapter must bind the
fresh decision with a separate `predecessor_turn_key` equal to the receipt's
`turn_key`; an old receipt cannot be replayed against a later envelope. Invalid
or stale input raises `ValueError`; it is never encoded as a disposition.

## Output

Exactly one typed disposition:

| disposition | meaning | quota |
| --- | --- | --- |
| `run_now` | fresh decision allows the next delivery Turn | no spend by the controller |
| `capability_action_required` | a signed capability intent requires its adapter before a host Turn | no spend |
| `wait` | quiet cadence or blocked delivery | no spend |
| `stop` | the current iteration ended without authorizing a retry or successor | no spend |
| `user_action_required` | a concrete user action is projected by receipt or decision | no spend |
| `repair` | repair-class recovery is required before any successor Turn | no spend |
| `replan` | replan-class recovery; see continuation boundary below | no spend |
| `terminal` | fresh Goal frontier plus durable no-follow-up prove Goal closure | no spend |

The output space is exactly these eight dispositions. There is no
`contract_error` disposition: contract failures are rejected at the typed-input
boundary. Every payload carries `spends_quota=false`, `launches_host=false`,
and `writes_state=false`.

The capability handoff carries the signed intent and does not require a
selected Todo or create a host transaction. If it follows committed progress
or completion, predecessor and Goal/Agent identity are still checked. It does
not consume a host-turn budget or authorize another host invocation. The
adapter must return its own receipt and re-enter planning; a missing adapter
must remain explicit rather than turning into `wait` or a successful delivery.

## Decision Table

| receipt | fresh decision | disposition |
| --- | --- | --- |
| none | delivery allowed | `run_now` |
| none / validated progress or completion | pending capability intent | `capability_action_required` |
| none | quiet / cadence-only | `wait` |
| none | fresh `terminal_no_followup` Goal frontier | `terminal` |
| `validated_completion` + durable `successor` | selected Todo is a declared successor | route the fresh decision (`run_now`, `wait`, `repair`, `replan`, or user action) |
| `validated_completion` + durable `active_goal` | fresh Goal frontier selects a different Todo | route the fresh decision |
| `validated_completion` + durable `no_followup` | fresh Goal frontier is also terminal no-follow-up | `terminal` |
| `validated_completion` | stale, missing, or undeclared continuation | `ValueError` |
| `validated_progress`, budget remaining | delivery allowed | `run_now` |
| `validated_progress`, budget exhausted | any | `replan` with bounded-delta requirement |
| `validated_progress` | no delivery | `wait` |
| `repair_required` | any | `repair` |
| `replan_required` | any | `replan` |
| `user_action_required` | any | `user_action_required` |
| durable `no_followup` + fresh terminal frontier + decision user action | — | `terminal` (proven Goal closure wins) |
| continuing completion + decision user action | — | `user_action_required` |
| `wait` | any | `wait` |
| `iteration_failed` | no decision user action | `stop` (iteration-scoped, not Goal terminal) |
| `iteration_failed` | decision user action | `user_action_required` (fresh decision precedence) |
| retryable `host_failure`, attempt budget remains | delivery or wait | `wait` with a same-Turn bounded-backoff continuation |
| retryable `host_failure`, attempt budget exhausted | any | `repair` |
| non-retryable or legacy `host_failure` / `validation_failed` / `writeback_failed` / `quota_spend_failed` | any | `repair` (route before any successor Turn) |
| replan-class decision action (`autonomous_replan*`) | — | `replan` |
| repair-class decision action (`*_repair*`) | — | `repair` |
| user action projected by decision | — | `user_action_required` |

## Precedence And Fail-Closed Rules

- `validated_completion` proves a Todo transition, not Goal closure. A
  declared successor or active Goal frontier continues through the fresh
  decision. Only durable `no_followup` plus a fresh terminal Goal frontier may
  produce `terminal`. An undeclared successor, reselected completed Todo, or
  missing lifecycle outcome raises `ValueError`.
- The lifecycle may recover only an explicit `active_goal` completion to
  `no_followup` within the same `completion_turn_key`. That audited recovery is
  not a fourth continuation and never weakens the controller's fresh-frontier
  requirement.
- A validation-only intermediate, phase-only committed mapping, incomplete
  settlement receipt chain, mismatched effect identity, missing durable
  effects, or incomplete scheduler handoff cannot drive `terminal`, `run_now`,
  or any other material continuation.
- When a receipt is supplied, the outer adapter must supply a separate
  `predecessor_turn_key` equal to the receipt's `turn_key`. A missing or
  mismatched key raises `ValueError` (`stale_receipt`); this closes the stale
  replay gap without adding an unsigned field to `loopx_turn_envelope_v0`.
- Every other user-action signal (from receipt or decision) routes to
  `user_action_required` before delivery dispositions.
- A typed retryable Host failure never authorizes a different model or a new
  Todo. The controller returns `wait` with the exact attempt, maximum attempts,
  retry delay, `same_turn=true`, and `model_fallback_allowed=false`. The outer
  scheduler may wake that same failed Turn with explicit retry authority after
  the delay. Once the attempt budget is exhausted, the controller returns
  `repair`; legacy or malformed failure metadata cannot opt into retry.
- `iteration_failed` is an ordinary bounded-loop outcome, not an infrastructure
  failure. It stops only the current iteration, sets neither retry nor replan
  continuation, creates no successor, and does not claim Goal terminal closure.
  A later iteration starts only through a new explicit controller decision.
- The fresh decision must satisfy the shared Turn envelope contract
  (`loopx_turn_envelope_v0` schema, non-empty equal signature hashes, and an
  in-budget compaction) via the same typed route the Turn plan driver uses;
  forged or truncated envelopes raise `ValueError`, never `run_now`.
- `validated_progress` may continue to another host Turn only with a proven `BoundedTurnBudget`
  whose lineage matches the fresh decision; without it the controller raises
  `ValueError` instead of guessing an unbounded continuation. Budget
  exhaustion routes to `replan`, not `terminal`, because a bounded Turn chain
  ending is not evidence that the Goal ended.
- Input validity is enforced at the typed-input boundary, not encoded as an
  separate error disposition. The transition output space is always one of the eight
  dispositions above.

## Replan Continuation Boundary

`replan` never permits rerunning the same stale todo merely because a host
session is resumable. The disposition payload carries
`replan_continuation`:

- `requires_bounded_delta=true`: a bounded `todo_delta` or `vision_delta` must
  be written before any successor Turn;
- `fresh_envelope_required=true`: the next Turn must come from a fresh
  TurnEnvelope, not a replayed one;
- `stale_todo_rerun_allowed=false`.

This mirrors the autonomous-replan and two-stall contracts: no runnable todo
with an open acceptance gap, a terminal/obsolete/incompatible selected todo,
validated negative evidence, or two eligible turns without material progress
all require replan rather than another delivery attempt.

## Managed Step Surface

`loopx turn managed-step` is the CLI surface that consumes this transition for
one already-journaled Turn:

```bash
loopx turn managed-step \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --turn-key <sha256:64-hex-digest> \
  --format json
```

It rebuilds the `ValidatedTurnReceipt` from the canonical Journal, projects the
current control-plane decision as a fresh `loopx_turn_envelope_v0`, and returns
`loopx_turn_managed_step_v0`: the disposition, its reason, the goal/agent/Todo
lineage, and, on `wait`, the typed `retry_continuation` block.

The command is read-only and grants no authority of its own. It never launches
a host, writes state, spends quota, sleeps, or mints a Turn. On `wait` the
answer only describes the bounded backoff after which the outer scheduler may
wake the *same* Turn; carrying the existing `--retry-failed-turn` /
`--resume-turn-key` flags on the next `run-once` stays the caller's decision.

The Turn Journal remains the sole authority for the attempt count and retry
ceiling. `--observed-attempt` and `--observed-max-attempts` are reconciled
against it and refused on disagreement, so a caller's bookkeeping can be
checked but never substituted. A Journal that is not a finished failed Turn,
whose typed host failure is not retryable, or whose current snapshot fails the
canonical TypeScript journal consistency checks is refused before the transition
is reached. A stored recovery audit describes an earlier attempt, not current
eligibility. The eventual `run-once` still revalidates host-session binding and
execution authority before retrying.

## Boundary

The controller is a pure function. It must not invoke a model, sleep, mutate a
host scheduler, write state, or spend quota. Invalid or stale input is rejected
at the typed-input boundary with a `ValueError`; it never guesses a recovery
or fabricates a host, gate, or user action.
