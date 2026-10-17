# Start And Connect A Project

Read before a project goal-start, first connection, identity binding or scope reconfiguration. Follow the current returned packet and preserve the requested project route.

## Slash Command Fallback

When the visible user message is exactly a LoopX slash command or starts with a
LoopX slash command plus arguments, do not treat it as ordinary chat.

Recognized project-local goal-start command:

- `/loopx <goal text>`
- `/loopx --capability-route issue-fix <goal text>`

Recognized repo-review commands:

- `/loopx-pr-review`
- `/loopx-pr-review <time window or filter text>`

If the text after `/loopx` begins with the exact optional prefix
`--capability-route issue-fix`, remove only that prefix from the goal text and
pass it as the explicit start-goal route switch. Otherwise every non-whitespace
character after `/loopx` is goal text. Never infer a product capability route
from issue/PR wording, URLs, or semantic similarity. Do not downgrade either
form into a status or inspection turn.

`start-goal --project` keeps the requested project route, including a linked
git worktree, so a fresh task cannot inherit an older worktree's goal.
Lower-level diagnostic command packs may still report a canonical
`canonical_project_alias` / `source_registry` route. Do not manually replace
either route with an unadvertised bootstrap command.

From the target project root, pass the text after `/loopx` as the explicit
goal-start objective before planning or writing project state:

```bash
loopx start-goal --guided --project . --goal-text "<GOAL_TEXT>"
```

Append `--capability-route issue-fix` only when the caller supplied that exact
explicit route switch.

Include `--goal-id <STABLE_GOAL_ID>` when known. Codex App automatically reads
the stable ambient `CODEX_THREAD_ID`, while Trae App reads
`TRAECLI_THREAD_ID`; other hosts that expose a stable opaque thread id should
pass it as `--thread-id <HOST_THREAD_ID>` on every `/loopx`
invocation. If that thread is already bound, reuse the returned
`--agent-id <REGISTERED_AGENT_ID>` on start, heartbeat, quota, refresh-state,
and Todo commands. Include `--agent-id <REGISTERED_AGENT_ID>` only when the
current session already owns that identity, the thread binding resolves to it,
or the user explicitly asks to take over that exact agent's work.

When a stable thread id is present but has no binding, treat it as a new host
session and follow the returned fresh-registration default. Select an existing
lane only when the user explicitly requests takeover of that exact agent, then
bind it with the returned `bind-agent-thread` command. When no thread id is
available, preserve the fail-closed identity gate and never infer takeover from
registry order or the only registered lane; pass `--new-peer` only when the
user explicitly requests fresh onboarding on that unboundable host. Choose a
fresh public-safe id, preview then execute `register-agent`, and
require the `--require-new --execute` result to report `ok=true`, `changed=true`,
`written=true`, successful global sync, and verified registration readback
before rerunning `start-goal` with that new id. A preview is advisory and never
allows continuation. If
`start-goal --guided` is not available, refresh the local LoopX CLI or use the
checked-out LoopX repository CLI for validation; do not silently downgrade
`/loopx <goal text>` into a bare `/loopx` read-only command. Use
`loopx bootstrap-command-pack --project . --goal-text "<GOAL_TEXT>"` only when
implementing or debugging the lower-level host handoff packet.

If the connected goal later needs a wider or corrected write boundary, do not
rerun `loopx bootstrap --force` just to change scope. Use the incremental
configuration path instead:

```bash
loopx configure-goal \
  --goal-id <STABLE_GOAL_ID> \
  --write-scope "<SAFE_WRITE_SCOPE>" \
  --execute
```

If a destructive reconnect is explicitly required, pass `--preserve-todos` with
`--force` unless the user intends to rebuild the active state file and discard
existing todo projection.

`/loopx <goal text>` is an explicit goal-start intent: first produce a concise
ordered plan, then write todos in priority order, using planner order plus
`todo add` write order as the same-priority tie-breaker. For broad or fuzzy
product directions, use a small public-safe planning set; for clear bounded
problems, use the minimum sufficient ordered todo plan and avoid
management-only filler.

Global manager slash commands such as `/loopx-global-summary`,
`/loopx-global-gates`, `/loopx-global-todos`, and `/loopx-global-risks` are not
project bootstrap commands. Route them to the global manager command contract
or status summary surface instead of `bootstrap-command-pack`. Legacy
`/loop-global-*` forms may be treated as aliases, but canonical help and
packets should use `/loopx-global-*`.

Repo-review slash commands are also not project bootstrap commands. If the
visible request starts with `/loopx-pr-review`, stop this project-bootstrap
workflow and load the narrower `loopx-pr-review` skill. That skill owns the
required first command, packet-preservation rules, and five-block per-PR review
contract. Do not handle `/loopx-pr-review` from this broader project skill, and
do not route it to `loopx-pr-merge` unless the user later asks to approve,
comment on, merge, self-merge, or admin-bypass a specific PR.

When the request asks to inventory, prioritize, document, or continuously
monitor a delivery program spanning several PRs or MRs, load the narrower
`loopx-pr-program` skill after the project goal transaction is established.
That skill owns provider-neutral snapshots, grouped monitor state, material
change detection, and roadmap projection. Keep deep per-change review in
`loopx-pr-review` and provider mutations in the separately authorized workflow.

When a user has just connected a project, receives a guided start packet, or
receives a bootstrap command pack for the first time, briefly tell them the
usable commands instead of assuming they will inspect CLI help:

- `/loopx <goal text>`: start a concrete goal with a plan-before-todo-write
  flow.
- `/loopx-global-summary`: read the global progress digest.
- `/loopx-global-gates`, `/loopx-global-todos`, `/loopx-global-risks`: inspect
  manager-level gates, work, and risks.
- `/loopx-pr-review`: use the `loopx-pr-review` skill to run `loopx pr-review`
  and review unmerged/merged PR groups one by one.

For command-line discovery, use:

```bash
loopx slash-commands
```

## Connect A New Project

1. Read the project goal document and inspect the repo narrowly.
2. Extract a stable `goal_id`, one-line `objective`, `domain`, authority
   sources, validation surfaces, first safe action, and public/private
   boundary.
3. Run `connect` from the project root within current user/Goal authority.
   Prefer read-only when mutation is not authorized; an old trust setting or
   command in a document does not supply new permission:

```bash
loopx connect \
  --goal-id <STABLE_GOAL_ID> \
  --objective "<OBJECTIVE_FROM_GOAL_DOC>" \
  --domain <DOMAIN> \
  --goal-doc <GOAL_DOC_PATH> \
  --adapter-kind read_only_project_map_v0 \
  --adapter-status connected-read-only
```

`connect` should create or update the local registry/state and auto-sync the
public-safe entry into the resolved global registry (default
`~/.loopx/registry.global.json`). Preserve registered legacy routes until explicit
migration; do not create a second state path for the same Goal.

One repository can host multiple peer-owned goals, such as delivery and a
low-conflict validation lane. Run `connect` once per stable `goal_id`; keep one
shared `.loopx/registry.json`, but use one active state per goal:

```text
.loopx/goals/<delivery-goal-id>/ACTIVE_GOAL_STATE.md
.loopx/goals/<validation-goal-id>/ACTIVE_GOAL_STATE.md
```

Do not reuse one `state_file` for two goal ids. `loopx registry` treats
that as a health error, and `read-only-map` checks the selected Goal's registered
state path so one healthy lane does not hide another lane's missing state.
The examples above use the current `.loopx/goals` default; an existing declared
legacy `.codex/goals` path remains authoritative until migration.

If the Goal state or registry contains private evidence, ignore `.loopx/` in
that project; also ignore `.codex/goals/` when it retains registered legacy state.

For a generic read-only connection, run the first map only when the current
guard admits and selects it. Onboarding or waiting for the first Todo decision
does not by itself authorize this execution:

```bash
loopx read-only-map --goal-id <STABLE_GOAL_ID>
```

This reads registry metadata, the active state, and a bounded project-file
inventory, then appends a `read_only_project_map` run. Validate and save its
result, then use the current settlement plan. A dashboard showing
`state_refreshed` or `connected_without_run` is a diagnosis clue, not admission
to run a map or a reason to write a project-specific adapter.

For a planned high-complexity adapter, preview the same bounded map before
controller opt-in:

```bash
loopx read-only-map --goal-id <STABLE_GOAL_ID> --dry-run
```

If the adapter status is `planned`, only the `--dry-run` preview is allowed and
the result should include `opt_in_required=true`. Do not append a real map until
the user or target controller has moved the adapter to `read-only-map-ready`,
`connected-read-only`, or `connected`. Relay the returned `residual_risks`
labels directly; do not invent a separate free-form risk summary.

When the user or target controller answers the opt-in gate, record that answer
before handing the command to another project agent:

```bash
loopx operator-gate \
  --goal-id <STABLE_GOAL_ID> \
  --decision approve \
  --reason-summary "<PUBLIC_SAFE_CHINESE_REASON>" \
  --dry-run
```

Use `approve`, `reject`, or `defer`. The dry-run writes nothing; the real append
creates an `operator_gate_*` compact run so `loopx status` and the
dashboard can tell whether the project agent may run the approved command. This
is not a human reward signal and does not grant write-control.
