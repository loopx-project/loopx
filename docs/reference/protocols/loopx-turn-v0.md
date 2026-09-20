# LoopX Governed Turn v0

Status: experimental protocol and implementation target.

Integrators using the built-in Codex CLI host should start with the
[one-Turn quickstart](../../product/runtimes/codex-cli/loopx-turn-codex-cli-quickstart.md). This
document is the protocol and maintainer reference, not required onboarding.

`loopx_turn_v0` defines how LoopX can govern one bounded turn executed by an
external agent-loop host, such as Codex CLI, without turning that host into a
second control plane. LoopX remains authoritative for goal state, todos,
claims, gates, quota, scheduler hints, and compact evidence. The host owns
model execution, tools, and an opaque resumable session handle.

The `turn run-once` dry-run response also carries a compact planned `route`:
`kind`, `selected_todo_id` and `would_invoke_host`. This additive field lets delegation
preflight distinguish a successful preview from task admission while using the
same executor/model arguments as execution. It grants no host invocation and
changes no executing-Turn receipt; the four effect flags remain false.

The protocol is host-neutral. A Codex CLI adapter is the first target, but the
driver lifecycle must not depend on Codex-specific session files, transcript
formats, or benchmark task schemas.

## Mental Model

LoopX Turn is a four-stage control loop, not another agent runtime:

```text
LoopX decides -> agent CLI executes -> validator proves -> LoopX commits
```

| Stage | Owner | Contract |
| --- | --- | --- |
| Decide | LoopX CLI | Select one allowed action from live goal, todo, gate, capability, quota, and cadence state. |
| Execute | Host adapter plus an agent CLI such as Trae CLI or Codex CLI | Consume one typed request, run one bounded segment, and emit one typed candidate result. |
| Validate | Independent task-specific command or callback | Check the real artifact, test, remote state, or declared read-only postcondition. |
| Commit | LoopX CLI | Write durable state and spend one quota slot only after validation passes. |

This separation lets the same Turn contract govern coding, operations, data,
document, knowledge-maintenance, and other long-running workflows. The agent
CLI remains responsible for model and tool execution; it does not become the
authority for goal state or completion.

## Generic Agent CLI Quick Start

An agent CLI does not need native LoopX support. It needs a thin host adapter
and an independent validator:

1. Run `loopx turn plan` to inspect the live typed decision without launching
   the host or changing state.
2. The host adapter reads one `loopx_turn_host_request_v0` JSON object from
   stdin, invokes the selected agent CLI in the governed workspace, and writes
   exactly one `loopx_turn_host_result_v0` JSON object to stdout.
3. The validator reads the normalized host result from stdin and independently
   checks the claimed postcondition. Exit zero means passed; non-zero means the
   result is rejected. A timeout or unavailable validator is inconclusive.
4. `loopx turn run-once --execute` performs writeback and quota spend only when
   the typed result and independent validation both pass.

The adapter and validator executable names below are placeholders supplied by
the integration. They are separate programs because the executor must not
validate its own completion claim.

```bash
loopx turn plan \
  --goal-id example-goal \
  --agent-id example-worker \
  --host generic-cli \
  --execution-mode isolated-headless

loopx turn run-once \
  --goal-id example-goal \
  --agent-id example-worker \
  --host generic-cli \
  --execution-mode isolated-headless \
  --project "$PWD" \
  --host-adapter-command-json '["./tools/turn-host-adapter","--agent-cli","trae","chat"]' \
  --validation-command-json '["./tools/verify-turn-postcondition"]' \
  --execute
```

Do not pass a free-form interactive command directly as
`--host-adapter-command-json` unless it already implements the typed
stdin/stdout contract. For Trae CLI,
Codex CLI, or another conversational CLI, the adapter translates between the
Turn request/result objects and that CLI's prompt, session, and output model.
Raw transcript text, process exit zero, and the host's own completion claim are
never sufficient validation.

A reference headless adapter for TraeX (`traex exec`) lives at
`scripts/traex_turn_host_adapter.py`; it reads the bounded action text from the
Turn envelope and writes one typed result, leaving goal/todo authority and
validation to LoopX.

A DeepSeek Harness adapter lives in the `loopx.dsh_goal_mode` subpackage
(run with `python -m loopx.dsh_goal_mode`; the legacy
`scripts/dsh_turn_host_adapter.py` launcher still works); it uses
the optional `deepseek-harness-sdk` Python client to run one bounded dsh session
and parses the final assistant JSON message into the same typed Turn result.
Prefer the built-in `loopx turn run-once --host dsh` surface so structured SDK
terminal failures reach the Turn Journal. The module/subprocess invocation with
`--host generic-cli` remains the compatibility and rollback path.
See [DeepSeek Harness connector](../../integrations/deepseek-harness-connector.md).

### Host Selection

The Turn host is **selected, never inferred from an incidental environment**. An
explicit `--host` or `LOOPX_TURN_HOST` always wins; only when the operator
configured neither is the shipped default resolved from the operator's own
credential facts:

- operator credential configured: the default host is the managed `dsh`
  executor, which that credential authenticates;
- no operator credential configured: the default host is the individual
  `codex-cli` executor, because a managed host nothing can authenticate would
  otherwise refuse to run at all.

| surface | value |
| --- | --- |
| shipped default host, credential configured | `dsh` (managed executor) |
| shipped default host, no credential | `codex-cli` (individual executor) |
| explicit default selector | `LOOPX_TURN_HOST` |
| per-command override | `--host codex-cli\|claude-code\|dsh\|generic-cli` (plan), `codex-cli\|dsh\|generic-cli` (run-once) |
| authenticating credential | `DEEPSEEK_API_KEY`, optional endpoint `DEEPSEEK_BASE_URL` |

Selecting the host is not the same as choosing *what runs on it*. The managed
host resolves one **managed execution profile** — provider, model, and reasoning
effort — with explicit precedence: an explicit argument (for example
`--dsh-model`, `--dsh-reasoning-effort`) wins, then the operator's environment,
then the product default.

| execution profile field | product default | operator override | legacy lower-precedence override |
| --- | --- | --- | --- |
| provider | `deepseek-official` | `LOOPX_TURN_PROVIDER` | `DSH_PROVIDER` |
| model | `deepseek-v4-flash` | `LOOPX_TURN_MODEL` | `DSH_MODEL` |
| reasoning effort | `high` | `LOOPX_TURN_REASONING_EFFORT` | — |

The managed `managed_executor` readback reports the resolved profile as one line,
`<model>@<reasoning_effort>` (the shipped shape is `deepseek-v4-flash@high`).
The provider is prepended as `<provider>/…` only when the resolved provider is
not the shipped one, because dropping it for a deviating provider would make the
line claim a profile the Turn would not use. Whichever values the line names are
the values that run, so an owner-set model appears as itself rather than as the
shipped default. The line stays one line because every plan and execution payload
carries it and the agent-facing output budget is a contract; the field-by-field
form, with each value's source and the variable that set it, belongs to the
configuration readbacks a person reads.

An explicit argument the adapter cannot honour fails closed as
`invalid_reasoning_effort` rather than being silently coerced, and the refused
effort is named in the same line. Credentials authenticate the selected profile;
discovering `DEEPSEEK_API_KEY` never changes provider, model, or effort on its
own.

This is a default behavior change for the affected lanes. Both `plan` and
`run-once` previously defaulted to `dsh` regardless of the credential, so a lane
without one failed closed on `operator_credential_unconfigured`; the default is
now credential-resolved and a lane without a credential keeps running on the
individual CLI host. `--host dsh` remains the explicit managed path and still
fails closed with the same typed reason when nothing can authenticate it,
`--host generic-cli` remains the compatibility path, and a machine that wants
one fixed host should set `LOOPX_TURN_HOST` once instead of relying on the
ambient environment.

`plan` and `run-once` payloads carry the executor readback `managed_executor`
(`managed_executor_binding_v0`): the executor and its kind (`managed`,
`individual`, `generic`), the credential env var *name* (never its value), the
endpoint env var name, whether the executor is operator-credential-bound, and
whether it can launch here. When it cannot, `available` is `false`,
`unavailable_reason` names the missing fact:

| `unavailable_reason` | meaning | remediation |
| --- | --- | --- |
| `dsh_runtime_unavailable` | the DeepSeek Harness runtime is not importable and no explicit runner hook was supplied | install the released runtime, pass its runner hook, or select `--host codex-cli` |
| `operator_credential_unconfigured` | the managed host is selected but no operator credential or runner hook would authenticate it | set `DEEPSEEK_API_KEY`, or select `--host codex-cli` explicitly; the shipped default already resolves to `codex-cli` until a credential exists |
| `invalid_reasoning_effort` | the resolved execution profile names a reasoning effort the host adapter does not support | pass a supported `--dsh-reasoning-effort`, or clear the overriding environment variable |

The same readback also carries `unavailable_remediation`, which names those
exits as typed codes so a caller does not have to parse the reason string:

| `unavailable_remediation` | exit it names |
| --- | --- |
| `configure_operator_credential` | set the credential env var this readback reports as `credential_env` |
| `configure_dsh_runtime` | install the released runtime, or pass its runner hook |
| `correct_execution_profile` | pass a supported `--dsh-reasoning-effort`, or clear the overriding environment variable |
| `select_individual_host` | select the individual host instead of the managed one |

The list is empty for every launchable or non-managed executor, and naming an
exit selects nothing: acting on it is still an explicit credential, profile, or
`--host` change. The refusal `run-once --execute` returns on that verdict
repeats the exits as `remediation` and adds the concrete `remediation_host` and
`remediation_env_vars`.

`run-once --execute` fails closed on that verdict: status `unavailable`, no host
invocation, no Journal write, and no quota slot spend. An explicitly selected
individual host (`--host codex-cli`, `--host claude-code`) is billed to that
individual CLI login and makes no launchability claim (`available: null`).

### Five Questions For Any Agent CLI

Before wiring Trae CLI, Codex CLI, or another host, answer these five questions:

1. **How does it run unattended?** Choose an explicit non-interactive command
   and workspace. If the CLI is interactive-only, it is not an
   `isolated-headless` adapter yet.
2. **How does it return one typed result?** Prefer a native output schema or a
   dedicated result file. Do not scrape arbitrary conversation text as the
   completion contract.
3. **What is its resume handle?** Keep the opaque handle in local adapter
   state, keyed by `(goal_id, agent_id, todo_id)`. Never put it in LoopX state
   or public evidence.
4. **Which failures may resume?** A bounded timeout or lost transport may
   preserve an observed session. A rejected startup contract, incompatible
   host version, or missing session invalidates it so the next Turn starts
   cleanly.
5. **What proves the work independently?** Name a command that checks the real
   repository, artifact, service readback, document revision, or other
   postcondition without trusting the agent CLI's own claim.

This yields one reusable integration shape:

```text
TurnEnvelope
    -> host adapter -> agent CLI -> typed candidate result
    -> independent validator -> pass | repair | replan
    -> LoopX writeback -> one durable transition and one quota spend
```

A thin adapter can be implemented with this host-neutral algorithm:

```text
request = read_one_json(stdin)
todo = request.turn_envelope.action.selected_todo
session = load_local_session(goal_id, agent_id, todo.todo_id)
prompt = render_bounded_prompt(todo, request.result_contract, temporary_result_path)
invoke_agent_cli(prompt, workspace, session, explicit_timeout)
candidate = read_and_shape_temporary_result(temporary_result_path)
write_one_json(stdout, candidate with request.turn_key)
```

`render_bounded_prompt` should tell the agent CLI to work only on the selected
todo and write its candidate result to a dedicated temporary path. The adapter
must reject a missing or malformed result instead of guessing from prose. It may
discard raw conversation output after extracting the host's opaque session
handle. LoopX then passes the candidate to a separate validator; the adapter
does not call the work complete itself.

For a CLI with native structured output and resume support, the adapter is
mostly field mapping. For a CLI such as a Trae installation whose selected
command only returns conversational text, the wrapper must first establish a
dedicated typed result channel; passing `trae chat` directly as the adapter is
not sufficient. Check the installed CLI's help and pin the qualified command
shape because flags and headless behavior may vary by version.

### Repeatable Codex CLI Qualification

The repository includes an opt-in end-to-end qualification that creates an
ephemeral LoopX project and workspace. Its default mode uses a no-model Codex
fixture while exercising the built-in host adapter, independent validator,
state writeback, one quota spend, and idempotent transaction replay:

```bash
python3 examples/loopx-turn-codex-cli-e2e-smoke.py
```

Use the real mode only when a local Codex login is available and one isolated
model call is intended:

```bash
python3 examples/loopx-turn-codex-cli-e2e-smoke.py \
  --real-codex-cli \
  --codex-model <compatible-model>
```

The real mode emits only a compact LoopX qualification summary. LoopX does not
copy the prompt, transcript, stdout, or stderr into fixture state, the temporary
workspace and LoopX session binding are removed, and the disposable goal never
syncs into the global registry. Codex CLI may retain its opaque host session
according to local Codex policy so a later adapter turn can resume it. A compact
`codex_cli_model_requires_newer_codex` failure is a host-compatibility result:
the transaction must show zero state writes and zero quota spend; select a
compatible model or update Codex before retrying.

For a coding collaboration, the validator may run focused tests and inspect the
expected git diff. For operations, it may read back the declared resource
state. For data work, it may check a schema and bounded quality assertions. For
documents or knowledge maintenance, it may verify the target revision and
required sections. These are different validators over the same Turn
orchestration contract; they do not require different control loops.

## Authority Boundary

| Concern | Authority |
| --- | --- |
| Goal, todo, claim, gate, quota, and cadence | LoopX CLI and registry-backed state |
| Session creation, resume, cancellation, and tool execution | External host adapter |
| Repository write isolation | LoopX workspace guard plus repository policy |
| Validation | Task-specific validator selected by the agent or adapter |
| Durable outcome and quota spend | LoopX writeback after validation |

The host must not infer a different action from status prose. It consumes a
fresh `loopx_turn_envelope_v0` decision and preserves its action signature.
Full quota/status detail remains available through the envelope cold-path
references.

## Turn Lifecycle

One driver tick has exactly these ordered phases:

1. **Wake**: resolve `goal_id`, registered `agent_id`, host kind, explicit
   execution mode, available capabilities, and an optional opaque session
   handle.
2. **Decide**: run live `quota should-run --turn-envelope` with the observed
   capabilities. A fixture is valid only in tests and shadow replay.
3. **Route**: obey the envelope without invoking the host when the user channel
   requires action, work is throttled, a monitor is unchanged, or delivery is
   otherwise disallowed. Apply and acknowledge scheduler-only changes without
   spending quota.
4. **Prepare**: preserve the selected todo identity, claim or lease when the
   contract requires it, and satisfy the workspace guard before any repository
   write.
5. **Execute**: resume the declared host session only when the adapter marked
   it eligible, or create a new session when the execution mode permits it.
   Give the host the thin task body plus the current envelope, and request one
   bounded work segment.
6. **Validate**: classify the host result and validate the claimed artifact or
   state transition. Host process exit zero is not validation.
7. **Write back**: update or complete the current todo, create a repair or
   successor todo when required, and refresh state with compact public-safe
   evidence.
8. **Spend and schedule**: spend one quota slot only after validated writeback,
   then apply and acknowledge the latest scheduler hint. Cadence-only work does
   not spend quota.

The driver may stop after any phase. A stop must return a typed result and must
not silently continue with a different execution mode.

### One Executor Per Turn Lane

A **Turn lane** is one agent working one goal. Phase 5 launches exactly one
executing Turn per lane: while an executing Turn holds the lane, a second
executing Turn for the same goal and agent stops before the journal, the host,
and quota, and returns the typed refusal instead:

```json
{
  "status": "unavailable",
  "reason": "turn_lane_in_flight",
  "remediation": ["wait_for_in_flight_turn"],
  "in_flight": {"agent_id": "...", "operation": "loopx_turn_lane", "pid": 1234, "acquired_at": "..."}
}
```

`in_flight` names the holder so the operator can see what to wait for; the
runtime path, the lock id, and the lock policy stay out of it. The fence is a
kernel lock held by the executing process, so a crashed or killed Turn releases
the lane instead of leaving a stale claim that no later Turn can enter, and a
settled Turn releases it for the next Turn, including an idempotent replay.

Only an executing Turn takes the fence. A non-executing decision — a preview, or
a route that stops before the host — invokes no host and spends nothing, so it
always answers. Lanes stay independent: one agent on two goals, or two agents on
one goal, do not contend.

### Read-Only Journal Inspection

Maintainers can inspect one existing fenced journal without entering the live
Turn lifecycle:

```bash
loopx turn inspect-journal \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --turn-key <sha256:64-hex-digest> \
  --format markdown
```

The command resolves the canonical runtime journal path, reads it under the
existing journal lock, and projects `interpret_turn_journal` into
`loopx_turn_journal_inspection_v1`. It branches before status collection,
quota construction, scheduler context, planning, host invocation, settlement,
spend, or state writeback. Its `effects` field is therefore always an empty
list.

Exit zero means that inspection completed, including when `decision` is
`replay_blocked`. A non-zero exit means the command could not inspect the
requested journal because a selector, file, JSON document, or schema was
invalid. This surface is diagnostic evidence only: it grants no authority to
resume, retry, settle, schedule, spend, or write, and it is never an execution
gate. Settlement replay enforcement remains owned by the Turn executor.

Version 1 separates two questions that version 0 exposed through replay fields
alone:

- `replay_legal` says whether a terminal Journal can be reinterpreted without
  effects; it is not recovery permission.
- `recovery_decision` is the plan the real executor consumes for an existing
  Journal: `action`, `can_continue`, `resume_from`, `reinvoke_host`, a typed
  `reason`, and the checks that actually participated.

`journal_consistent` also requires a complete canonical typed settlement
identity. Its goal and agent must match the Journal, envelope, and requested
owner; its Turn instance must match the transaction; and its binding and effect
id must validate under the settlement schema. A Todo binding must also match
the envelope's authoritative selected Todo, including the adaptive primary Todo
override. A mismatch therefore produces a blocked recovery decision before
Host or any settlement provider is called, even when the completed phase prefix
would otherwise resume at durable writeback or a later effect. The current Turn
driver does not produce Todo-less autonomous-replan transaction Journals, so it
does not infer such a binding without authoritative Turn lineage.

For example, an `in_progress` Journal with a saved Host Result has
`replay_legal=false` but may continue from `validation` without another Host
call. `scheduler_action_required` continues from `scheduler_apply` and does not
repeat Host, writeback, or quota spend. A failed Host Session is evaluated only
when `--retry-failed-turn` is explicit and must pass the current Session Binding
check. Retryable Host failures also carry a content-free
`loopx_turn_host_failure_v0` record. The Journal persists the attempt before
Host invocation, and the TypeScript recovery decision rejects another
invocation after the declared bounded budget. Backoff is an outer-scheduler
hint; `run-once` never sleeps in-process or silently changes the selected model.
A dangling prepared effect remains owned by the existing provider
readback protocol; the recovery decision only records that the readback is the
next required check and does not claim general exactly-once execution.

When an existing Journal is continued, `run-once` persists one bounded
`loopx_turn_recovery_audit_v0` record. Its `planned` value is the adopted shared
decision; its `actual` value distinguishes `started` from `finished` and records
only final Journal status, completed phase ids, and whether this recovery
invoked Host. `inspect-journal` exposes that record as `last_recovery`. Raw Host
output, Session data, effect payloads, credentials, and local paths remain
excluded.

### User-Gate Quiet Wait

When the user owns the next step and the agent lane has no executable work, the
decision envelope carries an execution obligation of kind `user_gate_quiet_wait`
with `must_attempt_work=false` and `delivery_allowed=false`. This happens when
at least one open user-action todo exists while the agent lane exposes no first
executable item, and no agent replan obligation takes precedence.

A quiet-wait envelope is not a stall:

- the host must not invoke the model, send prompts, retry with backoff, or
  consume a heartbeat spend;
- the host must not report a stall, session failure, or dead-process risk for a
  driver that is correctly waiting at this obligation;
- the driver stays quiet until the projected user action clears the frontier or
  a later decision rotates the obligation (for example to a replan or a fresh
  runnable todo).

The obligation is machine-enforced by the control plane, not host prose: the
host reads `must_attempt_work=false` from the typed obligation and preserves the
quiet wait without re-deriving an action from status text.

For material results, schema-valid host output is only candidate evidence. The
caller or adapter must select an independent task/postcondition validator
before host execution. The generic CLI accepts a trusted JSON argv array,
passes the normalized host result on stdin, never invokes a shell, and discards
validator stdout and stderr. A missing, failed, or inconclusive validator stops
at `validation_failed`, records a typed `repair_required` or `replan_required`
recovery disposition, and cannot write state or spend quota. Typed stop results
do not require task validation because they produce no material writeback.

An independent callback validator may distinguish terminal completion from
validated intermediate progress. `status=passed` means the declared terminal
postcondition holds and uses exit code `0`. `status=progress` means a bounded,
task-facing postcondition is independently proven but the terminal
postcondition is still open; it uses an explicit non-zero marker. Both statuses
may commit exactly one Turn and one quota spend. Only `progress` permits a host
adapter to start another Turn, and only under a predeclared maximum, shared
total time budget, and no-feedback continuation policy. Every other validator
status fails closed before writeback.

Adapters that lack a separate terminal signal may declare a bounded `fixed-n`
terminal policy. Under that policy, each successful independent validator call
proves progress, while only a successful final configured Turn satisfies the
sequence terminal postcondition. The default `validator` policy continues to
interpret exit code `0` as per-Turn terminal completion. Within a bounded
multi-Turn sequence, a successful Turn that also proves a durable content
change receives one further blinded review Turn before sequence termination;
the next successful no-change Turn may terminate early. The policy is explicit
in public-safe runner prerequisites and never changes benchmark scoring.

## Turn Input

The driver input is a small composition of existing contracts:

```json
{
  "schema_version": "loopx_turn_request_v0",
  "goal_id": "example-goal",
  "agent_id": "codex-worker",
  "host": {
    "kind": "codex_cli",
    "execution_mode": "interactive_visible",
    "session_handle": "opaque-local-handle"
  },
  "wake": {
    "reason": "scheduler_due",
    "turn_key": "stable-idempotency-key",
    "available_capabilities": ["shell", "filesystem_write"]
  },
  "decision": {
    "schema_version": "loopx_turn_envelope_v0",
    "action_signature": {
      "matches": true
    }
  }
}
```

`session_handle` is local adapter state. It must not be committed, copied into
LoopX public state, or treated as identity authority. The stable control-plane
identity is `(goal_id, agent_id, selected_todo.todo_id)`.

Adapters may support two explicit execution modes:

- `interactive_visible`: user-visible and interruptible; never falls back to
  hidden execution.
- `isolated_headless`: an explicitly selected experiment or worker mode in an
  isolated workspace; never claims to preserve an interactive TUI.

Mode selection is input policy, not a retry heuristic.

## Typed Result

Every attempted tick returns one result kind:

| Result kind | Meaning | Required next state |
| --- | --- | --- |
| `validated_progress` | One bounded segment produced validated evidence. | Update current todo, refresh, spend once. |
| `validated_completion` | Acceptance for the current todo is met. | Complete todo with exactly one typed continuation (`successor`, `active_goal`, or `no_followup`), refresh, spend once. |
| `repair_required` | The todo remains sound but a recoverable execution defect blocks it. | Keep or create a concrete repair todo; do not mark success. |
| `replan_required` | The current route is exhausted or incompatible while the goal acceptance gap remains. | Write a bounded todo delta or vision replan trigger. |
| `user_action_required` | A concrete user decision, payload, or credential action is projected. | Notify with the projected action in the configured operator language; no host run and no spend. |
| `wait` | Quota, monitor, scheduler, or another typed wait contract applies. | Preserve state, apply cadence if needed, no spend. |
| `iteration_failed` | This bounded iteration did not satisfy its task-facing outcome, and no continuation was requested. | Stop this iteration without retry, successor, writeback, or spend; a later iteration requires a new decision. |
| `host_failure` | The host could not start, resume, or finish a turn. | Record the failure class and retry or repair policy. |
| `validation_failed` | Host output exists but task validation failed or is inconclusive. | Preserve failure evidence and route to repair/replan. |
| `writeback_failed` | Validated work could not be durably recorded. | Do not spend; retry idempotent writeback before more delivery. |

`validated_completion` is admitted only when the Turn caller supplies an
explicit Todo lifecycle adapter. After independent validation, the adapter must
authorize and complete the selected Todo through the existing Todo lifecycle,
then return a compact outcome for that same Todo: linked successors, an
authorized `no_followup` record, or `active_goal` continuation. The durable
Turn journal records that outcome before quota spend. A Todo completion alone
never terminates the goal; a fresh decision owns successor selection and goal
termination.

Every newly completed Todo persists `completion_continuation` and an opaque
completion identity explicitly. The
value must agree with its durable relations: `successor` requires at least one
`successor_todo_id`, `no_followup` requires `no_followup=true`, and
`active_goal` requires neither. A completed record that omits the field is not
interpreted as `active_goal`; it fails closed until an agent explicitly repairs
it by replaying `loopx todo complete`. The only post-completion transition is
the narrow #3261 recovery seam: during the original quota-bound
`completion_turn_key`, an explicit `active_goal` may be upgraded to
`no_followup` after the matching writeback and spend receipts exist. The
recovery records `completion_recovery=same_turn_terminal_closeout`; it cannot
cross a Turn or replace a successor. A completion made outside a quota Turn
instead persists a TS-derived `local_completion_*` identity. When a strict
`refresh-state` rejection later proves that only Todo lifecycle settlement is
missing, it may project that exact key through `--completion-identity-key`.
The completion fence accepts this distinct
`lifecycle_reentry_terminal_closeout` only for the matching completed Todo with
`active_goal`, no successor, and an authorized lifecycle actor. It does not
reinterpret the local key as a quota receipt or permit arbitrary cross-Turn
terminal replay.

`repair_required` and `replan_required` are distinct. Repair preserves the
current task intent. Replan changes the runnable todo set or route because the
existing task no longer advances the goal. Replan is required when any of the
following is true:

- no runnable todo exists while the active vision still has an acceptance gap;
- the selected todo is terminal, obsolete, or incompatible with observed host
  capabilities;
- validated negative evidence invalidates the current route; or
- two eligible turns produce no material progress through the same route.

A driver must not terminate merely because one todo ended. Goal termination
requires goal acceptance evidence, an explicit user stop, or a typed blocked
state with a concrete projected action.

## Recoverable Failure Classes

| Failure class | Driver behavior |
| --- | --- |
| `auth_required` | Stop for the concrete credential action; never read or upload credentials. |
| `session_unavailable` | Return `host_failure`; retry resume or start a new session only if the selected mode permits it. |
| `capability_missing` | Re-run decision with observed capabilities and use capability repair routing, not a fabricated user gate. |
| `workspace_guard_denied` | Repair or relocate the workspace before writes. |
| `executor_timeout` or `transport_lost` | Return `host_failure` with bounded retry metadata; do not infer completion. |
| `provider_capacity`, `provider_overloaded`, or `rate_limited` | Prefer an exact structured provider code, otherwise use a bounded adapter-local diagnostic fallback. Preserve only the typed class, exact attempt, same-configuration strategy, bounded exponential backoff, and maximum attempts. Never persist provider prose or silently select another model. |
| `quota_exhausted` | Treat hard plan, billing, or included-usage exhaustion as non-retryable repair. Do not collapse it into a timed rate limit. |
| `result_missing` | Return `validation_failed`; a process exit without typed result is inconclusive. |
| `validation_failed` | Preserve compact negative evidence and choose repair or replan. |
| `writeback_failed` | Retry idempotent writeback; never spend first. |
| `scheduler_apply_failed` | Preserve completed writeback, record cadence failure, and retry scheduler control without a delivery spend. |

Structured failure discrimination is fail-closed. A known `error.code` wins
over HTTP status and prose. An unknown non-empty code becomes `unknown` and
routes to repair; it must not be reinterpreted from its message. HTTP 429 is a
bounded `rate_limited` signal only when no more-specific provider code exists,
so an `insufficient_quota` response remains non-retryable even when transported
as HTTP 429. Current `codex exec --json` releases expose message-only error
events, so diagnostic matching remains the live compatibility path for that
adapter; structured Responses, app-server, and JSON-RPC envelopes are accepted
as forward-compatible inputs but are not claimed as fields emitted by the
current exec JSONL contract.

Session recovery is fail-closed:

| Host observation | Session disposition | Next Turn |
| --- | --- | --- |
| Typed result returned | Keep the opaque session eligible. | Resume when the same todo remains selected. |
| Timeout or transport loss after a session was observed | Keep it eligible, but do not infer progress. | Retry the side-effect-safe host phase. |
| Incompatible host version or rejected startup/output contract | Invalidate it. | Start a fresh session after repair. |
| Host reports the session is missing | Invalidate it. | Start a fresh session if policy still allows execution. |
| Failure before any session was observed | Store nothing. | Re-decide, then start fresh only if allowed. |

Session eligibility is recovery metadata, not evidence that work happened. It
never bypasses a fresh Turn decision, task lease, independent validation, or
writeback ordering.

## Cross-Iteration Context Policy

Each `turn plan` or `turn run-once` invocation declares an iteration context
policy independently from the Todo and Goal lifecycle:

- `resume-if-available` preserves the existing behavior and resumes a compatible
  opaque Host Session for the same Goal, Agent, and Todo;
- `fresh` ignores a compatible saved session for this invocation and starts a
  clean Host Session. Selecting `fresh` does not itself delete the prior binding;
  after a successful host start, the newly observed session becomes the eligible
  binding for later iterations. It does not imply a new Todo, successor, retry,
  or Goal.

Use a new `turn_instance_id` for each new iteration. Reuse the same id only for
an explicit replay or failed-Turn recovery. The context policy controls Host
memory, while the TurnEnvelope and durable LoopX frontier remain the sole
authority for work selection and continuation.

## Adapter Requirements

An external host adapter must provide:

- capability discovery that can be passed to `--available-capability`;
- start, resume, cancel, and bounded-timeout operations;
- a public-safe typed result channel separate from raw transcript output;
- an explicit execution mode and no silent mode fallback;
- an opaque local session handle with no authority beyond host resume;
- visibility and idle proof before injecting into an interactive session; and
- deterministic failure mapping to the result and failure classes above.

The smallest useful adapter has only three responsibilities: translate the
typed request into one bounded agent-CLI invocation, preserve an opaque local
resume handle when the host supports it, and translate the final outcome into
one typed candidate result. It must not parse LoopX status prose, write LoopX
state, spend quota, or validate its own work.

The driver may discard raw stdout and stderr, but it must not mistake their
absence for a typed result. Raw prompts, transcripts, benchmark task text,
verifier tails, credentials, and local session paths stay outside committed
fixtures and LoopX state.

## Promotion Gates

The protocol remains experimental until all of these are true:

1. shadow replay preserves the live TurnEnvelope action signature across
   delivery, user gate, monitor wait, capability repair, workspace repair,
   replan, blocked, and throttled states;
2. one real host adapter proves start/resume, typed result, validation,
   idempotent writeback, spend ordering, and scheduler acknowledgement;
3. interactive and isolated-headless modes fail closed without switching into
   each other;
4. a controlled benchmark dogfood run shows source, budget, concurrency, and
   no-feedback boundaries remain comparable; and
5. rollback can disable the adapter while leaving normal LoopX CLI state and
   Codex App heartbeat operation intact.

This protocol does not authorize benchmark launch, leaderboard submission,
production writes, credential handling, or default replacement of Codex App.
