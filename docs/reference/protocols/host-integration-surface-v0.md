# Host Integration Surface v0

LoopX host integrations let an agent host use the LoopX control
plane without becoming a second LoopX runtime. The compatibility
baseline remains the CLI. Hook, MCP, and server adapters are thin facades over
the same registry, active state, run history, quota, todo, gate, optional
lease, and public/private boundary contracts.

The v0 protocol contract is intentionally small: thin hook activation,
lifecycle reads, controlled todo/gate writes, optional explicit lease writes,
compact status projection, CLI fallback, and public/private boundary
invariants. It does not prove that any adapter is installed, and it does not
grant write authority beyond the existing CLI-equivalent LoopX
lifecycle.

Codex App slash command parsing is covered by
[`codex_app_host_command_registry_v0`](codex-app-host-command-registry-v0.md):
the host recognizes `/loopx`, `/loopx <goal text>`, and `/loopx-global-*`
before ordinary chat, then hands off to the same CLI-backed lifecycle.

## Roles

| Surface | Job | Must Not Do |
| --- | --- | --- |
| Hook activation | Start a host turn with the current LoopX lifecycle contract and route the agent toward `quota should-run`. | Embed stale project policy, schedule hidden work, or replace the user's visible TUI/control surface. |
| MCP adapter | Expose read and controlled write tools to a host that already understands tool calls. | Store raw transcripts, bypass LoopX CLI semantics, or invent host-specific permission rules. |
| Loopback server adapter | Provide compact status and controlled write endpoints for local dashboards or host runtimes. | Bind remotely by default, publish private state, or make browser/frontstage/server writes authoritative without CLI-equivalent dry-run. |
| CLI fallback | Preserve a deterministic path for every read and write when the hook/MCP/server layer is absent or unhealthy. | Become a hidden headless execution path for TUI-first bootstrap unless the user explicitly opted in. |

## Thin Hook Activation

A host hook may only activate the current LoopX lifecycle. It should:

1. resolve the goal id and registered agent id;
2. run or instruct the host to run `loopx doctor` if the CLI is missing;
3. read `quota should-run` with the shared global registry;
4. pass the resulting `interaction_contract`, `goal_boundary`, and selected
   `agent_lane_next_action` into the host turn;
5. stop when the user channel requires a concrete question or payload todo; and
6. leave scheduling, quota spend, and writeback to the normal LoopX
   lifecycle.

The hook body should stay thin like a generated heartbeat prompt. Project
policy belongs in registry metadata, active state, authority sources, and
adapter output. If a hook needs project-specific branches, treat that as a
LoopX product gap before copying policy into host code.

For Codex CLI /goal visible TUI bootstrap, hook activation must preserve the
visible TUI as the primary surface. It may generate the thin `/goal` body or a
copyable bootstrap message, but it must not silently switch to hidden
`codex exec`, read session transcripts, or claim same-TUI automation without
the visible proof and idle-detection contracts.

## Ark Managed Agent host

`ark-managed-agent` is a one-shot goal host, not a LoopX Turn driver. LoopX
generates one short, transport-neutral goal prompt; the Managed Agent goal
runtime owns all inner iteration and continuation.

The prompt uses the same 4,000-character interface budget and the same guarded
goal policy as the Codex App/CLI visible-goal hosts; only the host ownership
preamble differs.

Generate the prompt with:

```bash
loopx heartbeat-prompt --thin --goal-id <GOAL_ID> --agent-id <AGENT_ID> \
  --runtime-profile ark_managed_agent_goal
```

The same contract is visible through first-class onboarding with
`--agent-type ark-managed-agent`. Onboarding is a read-only verifier, not an
installer or an installation prerequisite. Because this host does not use a
Codex-specific skill directory, the fixed installer shared with Codex writes
the LoopX workflow skills into a host-native target root. The packaged
no-clone path is:

```bash
curl -fsSL \
  https://loopx-project.github.io/loopx/install.sh \
  | env LOOPX_SKILLS_DIR=<PROJECT_WORKSPACE>/.agents/skills \
      LOOPX_ENTRY_HOST_SURFACE=ark-managed-agent \
      LOOPX_INSTALL_SLASH_COMMANDS=0 bash
```

For a contributor checkout, the equivalent command is:

```bash
LOOPX_SKILLS_DIR=<PROJECT_WORKSPACE>/.agents/skills \
  LOOPX_ENTRY_HOST_SURFACE=ark-managed-agent \
  LOOPX_INSTALL_SLASH_COMMANDS=0 \
  <LOOPX_CHECKOUT>/scripts/install-local.sh
```

This is also the supported canary path for an untrusted or dirty checkout.
With an explicit `LOOPX_SKILLS_DIR`, the script materializes the release-owned
workflow skills, including the generated `$loopx` task-entry skill, and writes
`.loopx-skill-install.json` without promoting the checkout as the default
`loopx` executable. The Managed Agent's ordinary task turn starts with
`$loopx <task>`; that skill writes the business todo before the host submits the
generated Goal task body exactly once. The controller must not pre-seed that
business todo. The generated entry skill binds the exact Managed Agent host at
install time, and its start-goal transaction preserves that host, task text,
and declared capabilities across bootstrap inspection. Without an explicit
target, a canary-only install leaves the existing default skill root unchanged.

The installer is the sole owner of filesystem mutation for both Codex and Ark
Managed Agent. Its default target is the Codex skill root; Ark Managed Agent
supplies a host-native `LOOPX_SKILLS_DIR` and binds the generated entry with
`LOOPX_ENTRY_HOST_SURFACE`. The manifest records the materialized skill ids,
source revision, and per-skill content digests so `doctor` and onboarding can
verify delivery read-only without becoming second installers. Running either
check is optional for installation. Run a check with the same
`LOOPX_SKILLS_DIR` to report the filesystem readback status and source
revision. Filesystem materialization is distinct from the host's runtime
loaded-skill readback; the latter is still required before claiming that the
skills were injected into an active agent context.

Local-development and cloud transports must send the exact same `task_body` as
their goal prompt. They may differ in endpoint, authentication, session id, or
wire envelope, but those fields do not change the prompt or become LoopX
policy. The host has no automation mode, and it must not wrap every inner goal
iteration in `loopx turn run-once`.

The generated `host_contract` states that activation happens once, the goal
runtime owns continuation, the lifecycle scope is the registered Goal until
terminal, phase handoff is not allowed, host session state is
non-authoritative, and the LoopX Turn driver is not required. A bounded
delivery segment is progress inside that Goal, not permission to replace it
with a successor host Goal after screening, implementation, review, or another
ordinary phase transition. Durable policy remains in current `quota
should-run.interaction_contract`, active state, todos, vision, and writeback.

For `--runtime-profile ark_managed_agent_goal`, the same quota read also emits
`scheduler_hint.goal_runtime_continuation` with schema
`goal_runtime_continuation_v0`. Its disposition is `continue_now`, `defer`, or
`complete`. A deferred result includes a bounded `recheck_after_seconds` and a
typed `wake_policy=state_change_or_deadline`: the host reruns quota when a
durable frontier write changes the sibling `scheduler_hint.reset_policy`
identity, or no later than the recheck deadline. The continuation packet does
not duplicate that identity or `scheduler_hint.reason_code`; their source refs
are declared by the Host contract. The deadline makes a due monitor runnable
even without a push signal; provider-specific CI/review observation remains
owned by its capability connector. This is the machine continuation contract.
The Goal prompt is not rewritten to teach waiting policy, and the model is not
used as a mechanical polling loop.

The state identity includes the selected Todo id, action, target, claim owner,
and capability binding ref. Switching work or admission authority therefore
wakes the Goal even when the rendered recommendation is unchanged; diagnostic
notes and other non-contract detail do not create a wakeup.

When the frontier carries explicit `next_due_at` values, the deadline is the
earliest exact due time. The coarser host cadence remains an automation concern
and must not delay a Goal-runtime wake past that boundary.

`defer` is a whole-frontier decision, not a per-PR wait. A quiet CI/review
monitor remains auxiliary context while any independent advancement todo is
runnable, so that mixed frontier projects `continue_now`. Only a frontier with
no executable advancement or due monitor may enter the deferred wake policy.

A dependent work step may begin only after material upstream results have
crossed the durable boundary: update the current todo evidence and the next
executable todo with any scope, acceptance, or non-goal delta, then refresh
state and read back quota. Chat/model summaries are not durable state.

Runtime capabilities discovered after activation do not regenerate the Goal
prompt. `quota should-run` returns the existing
`runtime_capability_reentry_v0` packet in
`interaction_contract.cli_channel.runtime_capability_reentry` and projects the
same packet near the beginning of JSON output as
`runtime_capability_reentry`. The early copy prevents bounded tool-result
capture from hiding the canonical packet behind large diagnostics.

Every candidate still requires a successful real-callsite observation before
the generated re-entry command may declare the capability. Follow-up
`next_cli_actions` inherit verified session capabilities; LoopX does not
persist the observation as a durable permission grant.

Issue-fix qualification on this host uses a staged evidence contract. A
validated patch proves the worker path, while Goal satisfaction must be read
from the host separately. See
[`ark-managed-agent-issue-fix-qualification-v0`](ark-managed-agent-issue-fix-qualification-v0.md).

Pause, replacement-session, and ambiguous-failure qualification is defined in
[`ark-managed-agent-goal-continuity-qualification-v0`](ark-managed-agent-goal-continuity-qualification-v0.md).
In particular, a surviving session id or a present Goal journal is not enough
to claim recovery; the replacement host must reconstruct the LoopX frontier
and the Goal runtime must prove journal rehydration without duplicate effects.

## Lifecycle Reads

Host integrations should expose read methods that map directly to CLI reads:

| Capability | CLI Baseline | Output Shape |
| --- | --- | --- |
| Health and installation | `loopx doctor` | compact readiness plus missing pieces |
| Registry and goal boundary | `loopx registry` and `quota should-run` | goal id, adapter status, write scope, registered agents, stop condition |
| Status and attention queue | `loopx --format json status` | first-screen status, user todos, agent todos, gate state, freshness warnings, optional read-only projections such as `task_graph_projection_v0` and `local_agent_launch_plan_v1` |
| Quota decision | `loopx --format json quota should-run --goal-id <goal-id> --agent-id <agent-id>` | `interaction_contract`, execution obligation, workspace guard, spend policy |
| Review packet | `loopx --format json review-packet --goal-id <goal-id>` | human/controller decision packet and agent handoff context |
| Run history | `loopx history` or status projections | compact run ids, classification, outcome, validation, blocker pointers |

Read methods return compact control facts. They must not return raw session
logs, raw benchmark task text, raw trajectories, private document bodies,
credentials, local absolute paths, or host auth material.
Optional projections such as `task_graph_projection_v0`,
`local_agent_launch_plan_v1`, and `cadence_hint_v0` are read-only
inputs to a host integration. They do not add graph write authority, launch
workers, change quota gates, or create a new source of truth.

## Controlled Writes

Writes must be CLI-equivalent, idempotent where possible, and fail closed when
the host lacks authority. A host adapter may expose these write classes:

| Write Class | CLI Baseline | Required Guards |
| --- | --- | --- |
| Todo claim and lifecycle | `loopx todo claim/update/complete` | registered agent id, active-state file lock, task class, active task-lease execution key when present, optional successor handoff with `blocks_agent` / `unblocks_todo_id` |
| User/agent todo creation | `loopx todo add --role user --task-class user_gate\|user_action` / `--role agent` | public-safe text, concrete actor, duplicate detection |
| Gate decision | `loopx operator-gate --decision approve|reject|defer` | explicit controller/user decision, dry-run preview before write |
| Human reward | `loopx reward ... --dry-run` then explicit write | run-bound judgment, public-safe reason, no score impersonation |
| Soft claim or optional hard lease | `claimed_by` by default; explicit `loopx task-lease acquire/renew/transfer/release/inspect` when a host needs hard write-scope exclusion | `(goal_id, todo_id)` contention key; `task_lease_v0` is opt-in and is not enforced by `quota should-run` |
| State refresh and quota spend | `refresh-state`, then `quota spend-slot --todo-id <SELECTED_TODO_ID> --source heartbeat --execute` | validation evidence first, one spend per completed automatic turn, bound to the selected todo |

The adapter must not translate a host approval, model confidence, browser click,
frontstage action, server callback, or scheduler timer into a protected write
unless the corresponding LoopX contract allows that write.
Browser/frontstage/server writes remain non-authoritative by default unless a
loopback capability advertises a dry-run/preview endpoint and the same
operation has a CLI fallback.

## Compact Status Projection

The host-facing status projection should be small enough for dashboards,
hooks, and MCP clients:

```json
{
  "schema_version": "host_integration_surface_v0",
  "goal_id": "loopx-meta",
  "agent_id": "codex-side-bypass",
  "host_kind": "codex_cli_tui",
  "activation": {
    "mode": "thin_hook",
    "visible_surface_required": true
  },
  "lifecycle_reads": ["doctor", "status", "quota_should_run", "review_packet"],
  "projection_inputs": [
    "task_graph_projection_v0",
    "local_agent_launch_plan_v1",
    "cadence_hint_v0"
  ],
  "write_capabilities": ["todo_lifecycle", "gate_decision"],
  "optional_write_capabilities": ["task_lease_v0"],
  "cli_fallback": {
    "available": true,
    "required_for_writes": true
  },
  "boundary": {
    "raw_transcripts_copied": false,
    "credentials_copied": false,
    "private_paths_copied": false,
    "remote_bind_default": false
  }
}
```

This projection is not project truth. It is a host capability map plus the
current LoopX lifecycle pointers. The registry, active state, event
ledger, todos, gates, quota, and optional task leases remain authoritative. A
host may consume task graph or cadence projections, but those projections
remain derived read-only facts and never grant write authority.

## CLI Fallback

Every host integration must document the CLI fallback for the same operation.
Minimum fallback set:

```bash
loopx doctor
loopx --format json status --agent-id <agent-id>
loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota should-run --goal-id <goal-id> --agent-id <agent-id>
loopx todo claim --goal-id <goal-id> --todo-id <todo_id> --claimed-by <agent-id>
loopx todo complete --goal-id <goal-id> --todo-id <todo_id> --claimed-by <agent-id> --evidence "<public-safe evidence>"
loopx refresh-state --goal-id <goal-id> --agent-id <agent-id>
loopx quota spend-slot --goal-id <goal-id> --todo-id <selected-todo-id> --slots 1 --source heartbeat --execute --agent-id <agent-id>
```

When a host explicitly advertises `task_lease_v0`, it must also expose the
equivalent CLI fallback. After canonical authority promotion, a `hard_lease`
host can acquire the lease and claim atomically; LoopX derives write scopes
from the canonical Todo:

```bash
loopx todo claim --goal-id <goal-id> --todo-id <todo_id> --claimed-by <agent-id> --agent-id <agent-id> --claim-operation-id <operation-id> --task-lease-idempotency-key <turn-key> --task-lease-expected-version <lease-version>
```

The atomic options fail closed before promotion. The existing two-command
fallback remains available where the lease is acquired separately. Acquiring
a hard lease does not replace quota, capability, write-scope, or workspace
guards:

```bash
loopx task-lease acquire --goal-id <goal-id> --todo-id <todo_id> --owner <agent-id> --idempotency-key <turn-key> --write-scope <scope>
loopx todo complete --goal-id <goal-id> --todo-id <todo_id> --claimed-by <agent-id> --task-lease-idempotency-key <turn-key> --task-lease-expected-version <lease-version> --evidence "<public-safe evidence>"
```

The acquire key and returned version form the execution-instance fence. A
lifecycle writer cannot rely on `agent_id` alone because multiple host
processes may share one registered peer identity. While an effective lease
exists, `todo complete` and `todo supersede` require both fields, hold the lease
lock through canonical state writeback, and reject a missing, stale, or
mismatched fence before creating successors. Renew, transfer, and release also
require `--expected-version`. Release retains an inactive terminal record so a
later acquire advances the per-todo version and `lease_epoch` instead of
recreating version 1.

If the host adapter is unavailable, the user or automation can run those
commands and preserve the same state transitions. If a host offers an operation
without a CLI fallback, that operation is experimental and must not be used as
the default project control path.

## Public/Private Boundary

Host integrations must preserve these invariants:

- Raw host transcripts, raw tool outputs, raw benchmark task text,
  trajectories, verifier tails, credentials, production logs, and local private
  paths stay in the host or private project store.
- LoopX state stores compact summaries, public-safe evidence pointers,
  decision labels, todo ids, gate ids, lease ids, and run ids.
- Loopback servers bind locally by default and reject remote write authority
  unless a separate deployment contract says otherwise.
- MCP/server tools must report denied or missing authority as structured
  blockers instead of guessing around gates.
- Hook prompts and adapter code must not carry long project-specific policy
  branches; regenerate or read current LoopX state each turn.
- The Codex CLI TUI path remains visible-first. Hidden headless execution is
  only an explicit fallback, not the default bootstrap or same-session proof.

## Acceptance Checks

A host adapter is acceptable when:

1. `quota should-run` remains the first delivery gate;
2. user-channel action requirements surface concrete user todos/questions;
3. every write class has a CLI-equivalent command and dry-run/preview when the
   write affects gates, reward, leases, or browser-triggered actions;
4. duplicate todo claim, stale lease, stale status, and daemon-down cases fail
   closed or fall back to CLI;
5. compact status projection excludes raw/private material and marks optional
   projections as read-only inputs rather than authority; and
6. validation covers one hook activation packet, one lifecycle read, one
   controlled write preview, one CLI fallback path, and one public/private
   boundary trap.
