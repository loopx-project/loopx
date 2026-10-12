---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
product_contract_source: owner-confirmed-design
---

# DeepSeek Harness Native LoopX Skill, Driver, And GoalBar

## Goal

Give a visible DeepSeek Harness (DSH) Session a small, native LoopX surface:

- plugin startup installs or repairs the LoopX CLI and DSH-facing LoopX Skills
  before its Loader row becomes ready; `/loopx-init` remains the repair entry;
- the `loopx` Skill teaches the model to use the authoritative LoopX CLI;
- one globally loaded but passive Driver becomes eligible only after the exact
  Session successfully invokes the `loopx` Skill, then continues quota-approved
  work through the exact live DSH Agent;
- one compact GoalBar projects the exact bound Goal, Agent-lane progress, and
  lifecycle state, and offers Start/Pause through LoopX lifecycle mutations.

LoopX remains the only durable Goal, Agent, Todo, binding, quota, receipt,
lifecycle, progress, and scheduler authority. DSH remains the model, tool,
inbox, same-session execution, and UI-transport authority.

## Placement

- Capability owner: existing LoopX host integration and workflow-skill
  installation contracts.
- Provider: optional `dsh-loopx-plugin` package under `packages/`.
- Delivery: extension package, not a new built-in LoopX capability.
- The package owns a DSH command, same-Session Driver, loopback Host service,
  and compact web GoalBar. The Host and Client are a thin projection and
  interaction layer over LoopX; they do not introduce model tools, a second
  control plane, or plugin-owned durable business state.
- Existing `loopx/dsh_goal_mode` remains the separate external/headless
  `deepseek-harness` Turn adapter.

## Public Host Names

- External/headless connector: `deepseek-harness`, with its existing `dsh`
  compatibility alias.
- Visible same-session integration: `deepseek-harness-native`, with the
  `dsh-native` alias.

The new integration must not repurpose the existing `dsh` alias.

## Automatic Initialization And `/loopx-init` Repair Contract

The plugin's init row automatically runs the typed initialization sequence and
awaits it before the row becomes ready. A startup failure is reduced to safe
stage/kind diagnostics, does not fail DSH boot, and leaves `/loopx-init`
available as a global repair command. Automatic startup queues no Agent input
and spends no model calls. For an explicit repair, the settled native
`CommandResult` remains authoritative; bounded model-visible status prompts do
not participate in installation or reload decisions.

The init row publishes a typed `loopxBootstrap` service only after that
success or safe failure settles. Only LoopX's Host and Driver depend on this
service. DSH's native Loader wait puts initialization before the printed URL;
the shared Web server and runtime must remain independent of this optional
package so disabling or uninstalling it cannot stop DSH. The failure value
contains only the safe stage and cause kind; it settles initialization without
granting LoopX readiness or hiding the repair command.

The command accepts no free-form input. Invalid input returns the usage error
before any followup or CLI probe. An exact invocation performs this bounded
sequence:

1. Queue one plugin-authored start followup that welcomes the user and says the
   CLI and DSH workflow skills are being checked or installed.
2. Probe a usable `loopx` executable and the DSH-native workflow-skill
   installation capability.
3. If the CLI is missing or incompatible, run one fixed-argv private install:
   `<compatible-python> -m pip install --upgrade --target
   <agents-home>/runtime/dsh-loopx-plugin/site-packages 'loopx>=0.5.4'`, then
   write a managed launcher beside that target. An explicit `PYTHON_BIN` wins;
   otherwise the plugin probes `python3` and named Python 3.11-3.14
   executables, then keeps the selected interpreter and launcher for readback,
   Driver/GoalBar calls, and generated skill commands. This does not mutate an
   externally managed system Python or use `--break-system-packages`.
   The package requirement is `loopx>=0.5.4`, the first release contract that
   both includes the workflow-skill resources and discovers them after the
   Linux `pip --target` install used by the managed runtime.
4. Resolve the resulting executable again; fail if it is still unavailable.
5. Run `loopx workflow-skills --install --skills-dir ~/.agents/skills
   --host-surface deepseek-harness-native`.
6. Validate every actual packaged-skill and entry-skill mutation status and
   derive the typed `skillsChanged` result; missing or unknown status fails
   closed at the `install_skills` boundary.
7. Run the read-only workflow-skill inspection and require a healthy managed
   readback.
8. Construct the bounded authoritative UI result. Unless the operation was
   cancelled, queue one completion followup from the same typed success or
   failure facts, then return the native result. Neither surface exposes raw
   subprocess output or local absolute paths.

A valid, uncancelled explicit `/loopx-init` execution normally adds two model
calls: one start turn and one result turn, including when initialization fails.
A cancelled execution leaves only the already queued start turn, and invalid
input adds no model call. Both followups instruct the model not to call tools,
run commands, reinstall, or expand diagnostics. Queue failures are logged
safely, never retried, and cannot alter the native result or cause a second
install mutation.

`skillsChanged` is true when any packaged skill is `created` or `updated`, or
when the entry skill is `created`, `updated`, or
`upgraded_legacy_managed`. DSH's filesystem skill provider invalidates its
catalog and loads every successful mutation without a restart.

A healthy compatible LoopX CLI is preserved rather than upgraded. Subprocesses
use fixed argv without a shell, honor the command AbortSignal, and are never
blindly retried. A failed package installation is returned as an actionable
command error.

The initialization followups use the stable
`dsh-loopx-plugin/init-command` plugin source, distinct from
`dsh-loopx-plugin/driver`. They are ordinary competing plugin input, not Driver
reservations, do not activate the Driver, and do not change Driver admission
authority or command barriers.

This command cannot install the plugin that defines it. `install.sh` remains
the source-checkout plugin bootstrap and tells the user to start DSH and use
`/loopx`; it does not require an explicit initialization command.
When DSH configures `DSH_AGENTS_HOME`, the command uses its `skills` child;
`~/.agents/skills` is the default.

## Workflow Skill Contract

Extend the existing `workflow-skills` command with the explicit
`deepseek-harness-native` host surface. The default Codex installation behavior
stays unchanged.

The DSH-native generated `loopx` entry must:

- treat the complete original visible user task as `goalText`, without a
  `/loopx` command layer or semantic-routing envelope;
- invoke the LoopX CLI through the DSH shell tool, never through plugin model
  tools;
- pass `--host-surface deepseek-harness-native` and the exact opaque
  `$DSH_SESSION_ID` as `--thread-id`;
- follow returned selection, registration, binding, Todo, quota, and writeback
  commands exactly;
- never guess Goal or Agent ids and never edit registries directly;
- use the installed `loopx-project` Skill for advanced operations.

The host-specific entry must not depend on `$ARGUMENTS` substitution because
DSH injects Skill instructions while preserving the original user message.
`DSH_SESSION_ID` is a DSH-managed shell fact derived from the active
`agent.session.header.id`; the plugin does not invent or persist it.

## Binding Resolution

Reuse the existing project-local thread-to-Agent binding records. Add only the
read-only resolution needed by the Driver:

- input: project, `deepseek-harness-native`, and exact DSH Session id;
- output: no binding, one exact `{goal_id, agent_id}` binding, or typed
  ambiguity/unhealthy failure;
- scan connected project Goals and fail closed when more than one binding
  matches;
- never introduce a new sidecar, mirror, lease file, or plugin-owned durable
  binding cache.

The Skill owns the existing explicit register/bind command choreography. The
Driver never creates or changes identity bindings.

## Same-Session Driver

The Driver is modeled on DSH's native goal-round lifecycle but consumes LoopX
quota instead of `ctx.goals`. Its service is loaded with the plugin so it can
observe typed Session events, but loading, Agent creation, Session start, and
idle status are passive. Before activation the Driver may only fold bounded
in-memory Session history and clean up local work; it performs no LoopX CLI,
binding, quota, scheduler, or heartbeat call, creates no timer, and queues no
followup.

Activation belongs to one exact current Session and accepts only:

- a `user/message` source with exact `kind: skill-invocation`, exact
  `name: loopx`, and exact `form: instructions`; or
- a `tool/call` named exactly `skill` whose arguments parse as a non-array JSON
  object with exact `name: loopx`, followed by a successful `tool/result`
  paired by the same call id.

A call request alone is not sufficient. Failed, malformed, unmatched, or
superseded model calls fail closed, as do ordinary prose, skill catalogs, shell
text, `/loopx-init` lifecycle events, plugin-authored init or heartbeat input,
installed files, CLI presence, registries, Goals, and existing bindings.

Observation and Session start rebuild this eligibility projection only by
folding the exact Session's existing typed event history. A replacement or
cleared Session does not inherit activation from the prior Session. The plugin
stores no durable activation record and performs no migration binding scan. An
older or compacted Session without recognizable invocation evidence remains
inactive until the installed `loopx` Skill is invoked once in that Session.

Activation is intent, not authority. After activation, at an exact live Agent
idle checkpoint the Driver:

1. yields to ordinary human or plugin input;
2. resolves the current Session binding from LoopX;
3. calls `quota should-run` with the resolved Goal/Agent and one stable attempt
   identity;
4. stops, schedules the typed next wakeup, or asks LoopX for the canonical thin
   task body bound to that same exact `turn_instance_id`;
5. queues at most one Driver-authored `Agent.followup()` for that automatic
   admission in the same Session, with typed LoopX continuation attribution;
6. revalidates the exact Agent, Session, reservation, competing input, and
   binding before the queued message enters a model step;
7. requires the accountable task to execute the guard-projected typed
   settlement plan, whose writeback and spend commands use the original exact
   turn identity and deterministic effect id.

The typed DSH message source is durable attribution used to match the queued
message to its reservation. It grants no execution authority: admission and
settlement still require the matching LoopX receipt and fresh quota guard.

There is one Agent-local serialized evaluation and one pending automatic
reservation. Human input wins before the automatic reservation is admitted.
Plugin reload, Agent replacement, Session start/fork, cancellation, and command
execution invalidate unadmitted work. The Driver stores no durable lifecycle
state and exposes no public coordinator.

The process-local activation projection only admits an evaluation for one
Session. It does not create a binding, select a Goal or Agent, spend quota, or
grant tool authority. Durable exact binding plus fresh LoopX quota remains the
continuation authority; the existing scheduler/heartbeat, reservation,
serialization, human-priority, cancellation, and pre-step revalidation
contracts remain unchanged after activation.

## Retry Ownership

- DSH `dsh-llm-retry` owns provider/model request retry.
- The LoopX Driver owns only its CLI calls.
- Resolve and task-body reads may retry at most twice after the first attempt
  for timeout/transport failures.
- `quota should-run` may use the same bounded retry only with the exact same
  `turn_instance_id`; LoopX's heartbeat receipt makes that replay idempotent.
  A typed failure response is authoritative and is not retried.
- Typed permanent, identity, ambiguity, malformed-output, cancellation, and
  unknown-write outcomes do not retry.
- After the finite retry budget is exhausted, the Driver stops that automatic
  evaluation. It does not maintain an eight-failure breaker or an indefinite
  retry loop.

## Plugin Installer

Retain the existing `install.sh` approach:

1. validate Node, pnpm, package name, and package version;
2. install locked dependencies and build/pack a tarball;
3. install or update the tarball in the DSH `web` profile;
4. read the profile back and verify exactly the `loopx-init` command row and
   Driver row in dependency order;
5. retain the built artifact and print DSH startup plus `/loopx` use as the
   next action.

The installer no longer requires LoopX to be present before the plugin can be
installed.

## Explicitly Removed From The Design

- `/loopx` command grammar and semantic fallback;
- model-facing `loopx_*` tools;
- provider-neutral model-execution Service abstraction;
- coordinator registry or durable public/private coordinator protocol;
- sidecar state, plugin-owned durable activation state, activation epochs, and
  failure suppression counters;
- custom operation receipts and planning checkpoints;
- `switchConfirmation` and plugin-owned Goal switching;
- raw CLI or registry mutation performed on behalf of model prose.

The compact GoalBar is not removed by these exclusions. It owns only
process-local waiters, action serialization, watch cursors, pending UI state,
and error state. Binding, lifecycle, and progress are rebuilt from LoopX; DSH
Session events and Agent status only wake or update the live transport view.

## Verification

Focused validation must cover:

- `/loopx-init` healthy-skip, missing-CLI install, incompatible-CLI repair,
  package failure, Skill failure, and readback failure, with one install
  mutation at most;
- automatic startup awaiting the same successful initialization before
  readiness, exposing skills immediately, and isolating a safe failure while
  retaining the repair command;
- invalid init arguments producing no followup or probe; start/result ordering;
  two followups for uncancelled success or failure; only the start followup for
  cancellation; and followup queue failure preserving the native result;
- actual `created`, `updated`, `unchanged`, and `upgraded_legacy_managed` skill
  status projection, hot loading without restart, and incomplete or
  unknown status failing closed;
- init followup source isolation from the Driver reservation source, bounded
  safe messages, and no raw subprocess output or local absolute paths;
- DSH-native generated Skill text, exact host/session flags, and absence of
  `$ARGUMENTS` dependence;
- external `deepseek-harness`/`dsh` compatibility;
- zero/one/ambiguous Session binding resolution;
- plugin load, Agent creation, Session start, ordinary events, and repeated idle
  transitions remaining at zero runner calls and zero timers before activation;
- exact user and successful paired model `loopx` Skill activation, recovery
  from typed current-Session history, cross-Agent isolation, replacement-
  Session recomputation, and fail-closed negative invocation shapes;
- legacy or compacted Sessions without recognizable evidence remaining
  inactive until `loopx` is invoked once in each intended Session;
- post-activation idle admission, human-input priority, exact reservation
  checks, Agent or Session replacement, command collision, cancellation, wait
  scheduling, and the one-Driver-followup maximum per automatic admission;
- one exact turn identity across quota admission, heartbeat task, typed DSH
  source, both pre-step checks, typed settlement plan, durable writeback, and
  quota spend, with one idempotent receipt and no unbound fallback spend;
- finite CLI retry with stable quota idempotency and no retry for unsafe
  outcomes;
- GoalBar reconstruction after Host/Client restart, source events that force an
  authoritative reread, runtime-only events that cannot create business state,
  stale binding rejection, serialized lifecycle mutations, and post-mutation
  readback;
- built tarball installation and real DSH profile readback containing the
  intended command, Driver, Host, and Client faces.

## Definition Of Done

- A user installs the plugin, starts DSH, and can invoke `/loopx <task>` without
  a separate initialization command or restart; `/loopx-init` remains a bounded
  explicit repair path.
- successful skill creation, update, or managed legacy-entry upgrade becomes
  visible through DSH's live skill catalog without a restart.
- a task handled through the `loopx` Skill uses authoritative CLI calls, not a
  plugin `/loopx` command, semantic routing, or model tools.
- an inactive Session performs no LoopX call or timer work; only a successfully
  invoked `loopx` Skill activates that exact Session.
- An activated, bound visible DSH Session continues only fresh quota-approved
  work through its exact live Agent, with one exact identity from admission
  through durable writeback and spend.
- The GoalBar stays hidden without one exact binding, reconstructs from LoopX
  after restart, and never becomes a Goal/Todo/lifecycle authority.
- Existing external DSH Turn mode remains compatible.
- No rejected model-tools, binding-sidecar, or durable plugin state design
  survives in production code or public guidance.
