# Shared Codex benchmark execution

LHTB, SWE-Marathon and other Harbor tasks use
`benchmark.runtime.harbor:BenchmarkCodex`, with the repository root on
`PYTHONPATH`. This research runner is not another installed product package.
Native tasks, environment, phases, feedback, verifier and scores stay in Harbor.

The shared adapter reads `PATH` from the task container before installing its
isolated profile. Worker and login shells retain those task toolchain directories;
LoopX modes prepend their staged Node and CLI. No operator-host PATH or other
ambient environment variables are copied. A missing/unreadable task PATH fails
installation. This changes tool discovery for newly installed trials only; keep
existing trials pinned when comparing runner versions.

## Configure the native job

Use this agent in the benchmark's existing job config, retaining its dataset
and environment settings:

```yaml
agents:
  - import_path: benchmark.runtime.harbor:BenchmarkCodex
    model_name: openai/gpt-5.6-sol
    override_timeout_sec: 5400
    kwargs:
      execution_mode: heartbeat
      task_entry: loopx-planned
      iteration_context: fresh
      reasoning_effort: max
      codex_sandbox: danger-full-access
      turn_timeout_sec: null
      scheduler_timeout_sec: 5080
      replan_after_turns: 6
```

For an explicitly selected **heartbeat-only** context experiment, add
`turn_envelope: true` to `kwargs` (default `false`). Each wake creates a private
capture root and asks the product renderer for a short TurnEnvelope dispatcher.
Full decisions remain available through `detail_ref.full_decision` from the same
guard invocation. Selection/reentry still evaluate current authority; generated
selection commands retain the capture root. When enabled, the setting appears in install,
execution and wake receipts as `turn_envelope: true`. When disabled, the field
and worker environment override are absent, preserving the pre-opt-in shape. Invalid native-Goal/plain/Turn-driver combinations
fail before execution. Set it back to `false` for the next run to roll back;
do not change a running trial's treatment. Captures contain private Goal context.
Transport tests do not establish lower token cost or better model decisions.

Checkpoint capture is independent of this display experiment. A `fresh_replan`
declaration may establish a private capture/handoff socket while
`turn_envelope` remains false: the worker keeps the ordinary dispatcher and quota
input, and only an explicit true setting selects the short view. This replaces
the earlier implicit activation by checkpoint handoff. Do not relabel older
receipts or trajectories as feature-off; qualify their actual input separately.

| Mode | Execution/continuation | LoopX skills and state |
| --- | --- | --- |
| `plain` | One Codex exec, native Goals disabled | Absent |
| `native-goal` | Installed native Goal transport; objective `Finish the task.` | Absent |
| `heartbeat` | Product thin heartbeat + external scheduler, fresh or same-session resume | Present |
| `turn` | Public Turn CLI, typed result, independent validation, settlement | Present |
| `loopx-goal` | Product Goal body + installed native Goal transport | Present |

`heartbeat` and `turn` accept `iteration_context: resume`. The first invocation
creates one Codex conversation; later planning checkpoints and execution wakes
resume that exact native session ID for the trial's Goal and Agent, including
when the selected Todo changes. Both drivers share the product's agent-scoped
Codex session store. `fresh` remains the runner default and starts a new session
on each invocation. The former context name is rejected, with no alias.

Resume never falls back to a new conversation when a binding is corrupt, the
trial home/workspace/model/settings change, or Codex returns a different ID.
Repair the configuration or explicitly select `fresh`; the next observed fresh
session replaces the binding. A timeout preserves an observed ID without
claiming progress. Wakes remain serialized by the outer controller. Private
wake receipts record the requested action and confirmed native session ID;
aggregate trajectories copy each native session once.

Native Goal continuation stays with Codex; blocked Goals are not automatically
unblocked. Plain exec versus Goal app-server also changes transport; it does
not isolate the continuation effect alone.

Native worker receipts retain both the latest Turn status and Goal status.
`native_goal_outcome` distinguishes `complete`, `blocked`, `paused`,
`budget_exhausted`, `usage_limited`, `cancelled`, `failed`, and `unknown`.
A failed Turn takes precedence over a blocked or complete Goal and exits nonzero;
intentional blocking/pausing and normal budget exhaustion do not become execution
failures. `ok` indicates a handled stop, never task acceptance. Only completion
may enter the explicitly enabled continuation option below.

Terminal errors retain a digest, an allowlisted native error variant and an
optional structured HTTP status. Unrecognized errors remain unknown; message
text is not parsed into a category or copied into the receipt. The existing
`error_event_count` counts error notifications, not requests: a terminal event
that repeats an error does not increment it. A later native continuation clears
the terminal-error fields and may complete normally.

The EdgeBench adapter carries this outcome through artifact collection into
`runtime-receipt.json`. If a native Goal fails after execution starts, its
`partial_result.json` retains the complete native scoring result, with explicit
`runtime_status` and `native_goal_outcome`. Failed, cancelled, usage-limited and
unknown native outcomes do not publish `final_result.json`: existing SForge
consumers use that file as a completion marker without interpreting the outcome
labels. Handled terminal stops retain the existing final-result behavior;
neither artifact grants result qualification. Ordinary non-native profile
handling is unchanged. Adopt this behavior only for newly pinned attempts;
frozen histories retain their original receipts and require explicit post-run
classification. Roll back by pinning the previous source for another new attempt,
not by renaming artifacts in a frozen run.

The optional `native_goal_continue: true` loop retains the original task's
requirements and evaluation rules after completion. Its continuation prompt no
longer requires a candidate to improve on an accepted best before submission.
Continuation remains off by default, with the same deadline and reserve rules.

`turn` requires `validation_command`, an argv list for an independently
protected validator available inside the task environment. It receives the
normalized candidate result on stdin. Missing validation fails before execution.
The runner supplies no HEAD-moved/clean-worktree/exit-only substitute and never
calls hidden benchmark verification to provide intermediate feedback. Independent
validator protection remains the environment owner's responsibility.

## Offline and git-less task images

Task containers are often slim images without a git binary or any route to
package registries. The LoopX install inside a trial stays offline when the
host prepares two inputs before staging:

- Build the Chat bundle once on the host with `python3 scripts/chat_bundle.py
  ensure`. Source staging validates the prepared `loopx/web/chat` delivery
  against its manifest and carries it into the archive, so in-container
  installation never needs `npm ci` or a registry. Without a prepared bundle,
  the in-container build records the staged source revision through
  `LOOPX_RESOLVED_SOURCE_GIT_COMMIT`, which the Harbor adapter supplies from
  the commit it verified during staging; a missing git binary takes the same
  fallback as a failed `rev-parse`.
- Models served through gateway aliases that the pinned Codex binary does not
  ship in its built-in catalog get a reduced tool surface (with some binaries
  only the bare `exec` tool), and the `web_search` setting has no effect.
  Stage a `model-catalog.json` next to the offline Codex binary
  (`CODEX_OFFLINE_DIR`); the installer copies it into the container and the
  trial home references it via `model_catalog_json`. The `web_search` agent
  kwarg then takes effect in the shared runtime (default remains `disabled`),
  and the requested model is unchanged.

  A catalog entry is **not** capability-only. Real entries can select a tool
  mode and carry `model_messages` that become prompt content; a valid
  `code_mode_only` entry makes the real client send `tools: []`, which is
  strictly worse than no catalog. The renderer therefore rejects entries whose
  `tool_mode` is set, and rejects catalogs with no entry for the requested
  model. Because configuration cannot prove delivered tools, qualify every
  pinned binary/catalog pair once at the first-request boundary — a local
  stub captures the request and stops inference with a deliberate 400, so no
  credentials or model spend are involved — and keep the receipt with the
  study:

  ```bash
  python3 -m benchmark.runtime.toolface --codex-bin "$CODEX_OFFLINE_DIR/codex" \
      --model <gateway-alias> --model-catalog "$CODEX_OFFLINE_DIR/model-catalog.json" \
      --web-search live --expect web_search --expect apply_patch \
      --receipt toolface-receipt.json
  ```

  Do not hand-write a "minimal" entry: the entry schema is internal to the
  client and changes between versions. The pinned `codex-cli 0.151.0` refuses
  to start without `display_name`, `model_messages`,
  `supported_reasoning_levels`, `truncation_policy` and further
  non-capability fields, while newer builds reject different omissions first
  (a 0.162 alpha fails on a missing `display_name` alone) — all before any
  HTTP request. Derive the entry instead: take a complete entry your pinned
  binary already accepts (from its shipped catalog, or a gateway-published
  catalog for an official model), copy it verbatim, and change only `slug`
  to the gateway alias. The capability fields that shape the toolface
  (`supports_search_tool`, `web_search_tool_type`, `apply_patch_tool_type`,
  `shell_type`) are inherited from the donor entry; leave `tool_mode` unset
  as above. Then qualify the pinned binary/catalog pair and keep the receipt
  — a client that cannot parse the entry now fails the qualification fast
  with its own stderr, instead of a silent timeout.

## Task entry and planning ablation

`task_entry` is independent of the execution mode:

- `seeded-todo` (explicit compatibility/ablation choice) writes a generic execution Todo.
  Follow-up phases update that Todo while it remains live and owned by this
  agent; completed or deferred work gets a new Todo. Updates preserve blocked
  state. The agent can still plan and replan during execution. The seed asks the
  worker to read, implement and validate the task against the referenced task's
  full requirements and acceptance criteria, keeping unmet requirements explicit.
  It leaves task decomposition to the worker and the existing task protocol, and
  removes the previous unconditional successor instruction for newly seeded or
  updated phases; existing trials are unchanged. This is task-scoping guidance,
  not a new completion gate, forced successor, or instruction to consume the
  whole budget.
- `loopx-planned` (the default for LoopX modes) runs the installed `$loopx` skill against the public
  `loopx todo plan` checkpoint before execution. The checkpoint shares the
  product's planner and continuation-aware Todo delta; it creates no planning
  Todo and starts no host loop. Select it only for heartbeat, Turn or LoopX Goal.

For a task-entry comparison, pin the same source and vary only `task_entry`;
keep task inputs, scoring, model, budget and evaluation windows fixed. Include
planning time in the common run budget.

New `loopx-planned` invocations deliver the product planning packet without the
previous benchmark research-strategy prefix. Only explicitly selected execution
observation tool context is added. This applies to both Harbor and SForge callers;
task content, the Core first-delivery contract, identity/readback validation and
host deadline enforcement remain unchanged. Pin the runner revision when comparing
planning policies. This adapter change does not qualify model adoption, earlier
evaluation or outcome improvement.

The model writes or reuses actual task Todos through the public CLI. The worker
reads the product packet again and checks the input digest, identity, Todo ids
and runnable/blocked state. A fabricated id, changed input, wrong owner, failed
planning process or missing result fails the entry; it never falls back to a
generic Todo. A blocked entry retains the referenced blockers and starts no
execution driver. Readback proves state and ownership, not semantic plan quality.
It makes no claim about a future execution session. For heartbeat and Turn,
compare the native IDs in the planning and execution wake receipts' `session`
fields to verify continuity; the context policy alone is not observation evidence.

Planning follows the chosen context policy for heartbeat and Turn. With
`resume`, planning and execution share the same conversation, including later
phase planning; each checkpoint still renders fresh public task inputs and
validates actual Todo readback. With `fresh`, each planning/execution invocation
starts a new conversation. LoopX Goal planning uses a separate exec conversation
because native Goal execution owns its app-server thread lifecycle. The runner
does not claim exact equivalence to interactive `$loopx` startup.
Harbor and SForge have no independent planning timeout. Planning and execution
share the absolute phase deadline, including native resumes. The shared worker
uses `LOOPX_PHASE_DEADLINE_EPOCH`; Harbor derives it from the remaining
`scheduler_timeout_sec` budget using the task environment's clock. Remove old
non-null `planning_timeout_sec` settings, `--planning-timeout` arguments and
`LOOPX_PLANNING_TIMEOUT_SEC` environment values: they are rejected instead of
silently changing their meaning. A legacy `planning_timeout_sec: null` remains
accepted. SForge still strips inherited stage-limit environment values and
preserves its existing null receipt field for compatibility.
Planning and preparation consume the same `scheduler_timeout_sec` phase budget
as execution. Planning sessions are
included in native session/token aggregation. No planning checkpoint is counted
as a completed advancement Todo or settled work Turn.

Each phase keeps an immutable task document. New phases preserve Goal/Agent
identity and expose existing Todos to the planner; they do not clear waiting
state or force the agent active. An unresolved Turn must be recovered before
another phase can replace its task input. These wait/recovery rules apply to
both entry policies; they correct the earlier unconditional phase reset.
Every scheduler wake caps its host timeout against the remaining phase budget
before opening an execution. If only startup and settlement reserve remains,
it records a budget-exhausted no-op without creating a pending Turn. In the
heartbeat/turn scheduler path, that receipt exits the wake with code 75 and the
configured shell worker stops normally. It does not re-admit empty wakes, mark
the task complete or spend quota. Direct plain/native-Goal calls retain exit 0
for normal budget exhaustion.
The deadline uses the task environment's clock, including remote Harbor backends.

To compare entry policies, hold the execution mode, session policy, model,
effort, tools, feedback and total budget fixed, and use separate trials:

```yaml
kwargs:
  execution_mode: heartbeat
  task_entry: loopx-planned
  iteration_context: fresh
  turn_timeout_sec: null
  scheduler_timeout_sec: 5080
```

## Install and isolate

From the candidate worktree, provide:

```sh
export LOOPX_SRC_DIR="$PWD"
export LOOPX_EXPECTED_COMMIT="$(git rev-parse HEAD)"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
```

Also set `CODEX_OFFLINE_DIR` (Codex, code-mode sidecar, rg),
`LOOPX_PORTABLE_PYTHON` (Python >=3.11 distribution) and `LOOPX_NODE_DIR`
(Node >=22.22.3 distribution). Staging archives the verified commit SHA, never local run
artifacts. The host import must come from that checkout, whose tracked files
must match HEAD. Commit the candidate before real validation. Baselines stage only
the runner/native transport, without installing LoopX skills or initializing
its state. Best-only plain workers also stage the configured portable Python for
the delivery hook, without installing LoopX. LoopX modes use the formal installer
and doctor readback.

Supply `OPENAI_BASE_URL`, `OPENAI_API_KEY` and optionally `CODEX_WIRE_API`
(default `responses`), or stage standard Codex authentication using
`CODEX_AUTH_JSON_PATH`. Credentials are excluded from session/log collection.

Each trial owns one isolated Codex home. Config, provider and skills stay fixed;
fresh creates a new conversation and resume reuses a compatible conversation.
Workspace and LoopX state persist. Memory generation and injection are disabled
explicitly; confirm support in the pinned Codex version. Fresh does not make
historical files inaccessible or reset task work. Hold model, effort, tool,
feedback and time-budget settings fixed when comparing modes.

`danger-full-access` explicitly delegates isolation to the task environment.
It does not certify that environment's security. Core Turn still defaults to
`read-only`; callers may select `workspace-write` or `read-only` consistently
across comparison arms. No wrapper silently replaces sandbox flags with bypass.
LoopX arms record the trial's task-workspace write authorization through
`configure-goal --boundary-authority-scope`. Turn carries that checkpointed
approval in its envelope; publishing and production actions keep their gates.

Staging uses Harbor upload/exec methods, without Docker-label container discovery.
Backend-specific networking stays in the benchmark's native launcher.

## Results, timeout and recovery

Private per-wake receipts distinguish process success, Turn settlement and
native benchmark results. Sessions are collected once per native filename;
resume updates that copy instead of counting the old prefix in every wake.
Harbor converts each session independently and phase token counts use deltas.

Timeout preserves partial task artifacts for native scoring. Goal receipts
retain the observed transaction on timeout. Cancellation reaps child processes.
Harbor deadlines must exceed scheduler deadlines, which must exceed host
timeouts plus validation and cleanup allowance.
The scheduler wake deadline is derived from the host timeout plus 150 seconds;
the retired LHTB `LOOPX_WAKE_TIMEOUT_SEC` setting is no longer used.

Pending controlled Turns retain their identity across worker restarts. Core
`--resume-turn-key --retry-failed-turn` decides recovery eligibility and retry limits. The runner
does not delete homes or session bindings, edit registries directly, or
monkeypatch CLI internals.

## Migration and qualification

This is the native runtime bridge/adapter slice in the research program's
[engineering plan](../../docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md#11-engineering-construction-plan).
Synthetic Harbor conformance does not qualify a matched study or a benchmark
score claim. Full LHTB and SWE-Marathon studies retain their own acceptance.

Existing named LHTB/SWE agent imports remain thin compatibility entries; new
studies use the shared entry with explicit modes. Retired WEN controls fail
instead of silently changing their meaning. The old fixed-stage Turn and
automatic-unblock implementations are available in Git at
`8330a974cc2631ffd006d1fb7bd1627d2d690e85`. Historical results and withdrawal
notices retain their original provenance; no old scores are reassigned.

LHTB now uses a **trial home instead of a per-wake home**. This is a disclosed
behavior change, not strict execution parity. Roll back by using the previous
revision and a new trial; do not rewrite active homes or historical receipts.

```sh
uv run --extra test python -m pytest benchmark/tests/test_shared_codex_runtime.py \
  benchmark/tests/test_native_codex_goal.py tests/test_loopx_turn_codex_cli.py
```

Install the intended Harbor version for the adapter tests. Real qualification
also needs installed Codex, the native Harbor backend and independently checked
task output. Unit tests establish no score or model-uplift claim. Validate small
jobs through each benchmark's native configuration before launching a study.

By default, worker calls have no independent turn deadline. Harbor derives their available time from the remaining total phase budget, reserving cleanup and settlement time. An explicit `turn_timeout_sec` remains supported as an operator override.

### Native SForge task entry

The EdgeBench runner accepts `--task-entry seeded-todo|loopx-planned` for
`heartbeat-resume` and `heartbeat-explore`; the heartbeat default is `loopx-planned`.
Official, single and native-Goal profiles reject planned entry before creating
an attempt. Select only this flag for a task-entry ablation and keep all other
inputs fixed. Runtime/profile receipts record the selected entry.

Planned entry uses the existing public planning worker inside SForge's timed,
network-isolated execution process, so it inherits the same API-only proxy and
counts toward the persisted phase deadline. Setup hooks do not invoke a model.
A verified initial planning receipt is reused after an abnormal process resume;
its task identity and Todo lineage are checked again. A failed, missing, stale
or blocked plan cannot start execution. Reusing the receipt does not bypass the
ordinary scheduler's quota, claim, blocker or settlement checks. Retain receipts
and native sessions when comparing planning cost; no active attempt is changed
by selecting this option for a new run.

New planned-entry prompts expose the Host's existing time limits alongside the
unchanged product planning packet. SForge supplies the absolute phase deadline
and observed remaining time, shared by planning and execution, including the
worker's existing startup/settlement reserve and the work window before it. Harbor's separate
planning invocation limit is labeled as a stage cap; it must not be mistaken for
the whole task budget. The observation ages as planning proceeds and does not
refill time, change startup/settlement reserves, or grant execution authority.
This changes planning context for new planned-entry runs. Task identity, Todo
readback and runtime timeout enforcement retain their existing owners; seeded
entry and execution wakes are unchanged. Record the runner revision when comparing
plans. Delivery of the time information does not prove that a model uses it or
improves task outcomes.


### Default effective-Turn cadence

Harbor LoopX modes (`heartbeat`, `turn`, `loopx-goal`) default to
`replan_after_turns: 6`; native EdgeBench `heartbeat-resume` and
`heartbeat-explore` default to `--replan-after-turns 6`. This passes the existing Goal option
`--execution-replan-after-turns` and verifies the persisted
`replan_after_effective_turns` value before execution. The shared TypeScript
control plane still owns which settled work Turns count; adapters do not count
records or completed Todos themselves.

Omitting both cadence options selects six effective work Turns, matching the
product default. The previous benchmark default was three effective work Turns;
the previous product default was five. Explicit settings retain their values.
Idle wakes, tool calls and the planning checkpoint do
not count as effective work Turns; this is a deterministic threshold rather than
a per-wake probability, and other replan triggers can act sooner.
To retain the previous benchmark default in a new trial, pass
`replan_after_turns: 3` in Harbor or `--replan-after-turns 3` in EdgeBench.
For completed-Todo cadence, use `replan_after_todos` / `--replan-after-todos`.
Explicit Turn/Todo settings are mutually
exclusive; effective-Turn counts accept one through six and completed-Todo counts
accept one through five. Resolved runtime and worker
receipts name the selected unit even when no flag was supplied. Non-LoopX
profiles retain their existing behavior. No active attempt, task, scoring, feedback,
spawn permission, or total-budget change is implied.

The two options are independent: `--task-entry` selects where the initial Todo
comes from, while `--replan-after-turns` selects which cadence the shared control
plane uses afterwards. A trial may set either, both, or neither; receipts record
both selections so a comparison keeps every other input fixed.

See [default settings and recommended ablations](SETTINGS.md) for explicit launch flags,
matched controls and rollback.
