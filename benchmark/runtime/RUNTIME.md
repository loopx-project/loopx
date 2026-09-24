# Shared Codex benchmark execution

LHTB, SWE-Marathon and other Harbor tasks use
`benchmark.runtime.harbor:BenchmarkCodex`, with the repository root on
`PYTHONPATH`. This research runner is not another installed product package.
Native tasks, environment, phases, feedback, verifier and scores stay in Harbor.

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
      task_entry: seeded-todo
      iteration_context: fresh
      reasoning_effort: max
      codex_sandbox: danger-full-access
      turn_timeout_sec: 4700
      scheduler_timeout_sec: 5080
      replan_after_todos: 3
```

| Mode | Execution/continuation | LoopX skills and state |
| --- | --- | --- |
| `plain` | One Codex exec, native Goals disabled | Absent |
| `native-goal` | Installed native Goal transport; objective `Finish the task.` | Absent |
| `heartbeat` | Product thin heartbeat + external scheduler, fresh each wake | Present |
| `turn` | Public Turn CLI, typed result, independent validation, settlement | Present |
| `loopx-goal` | Product Goal body + installed native Goal transport | Present |

Only `turn` accepts `iteration_context: resume-if-available`. Core session
compatibility determines whether it actually resumes, including after changing
Todo. Native Goal continuation stays with Codex; blocked Goals are not
automatically unblocked. Plain exec versus Goal app-server also changes
transport; it does not isolate the continuation effect alone.

`turn` requires `validation_command`, an argv list for an independently
protected validator available inside the task environment. It receives the
normalized candidate result on stdin. Missing validation fails before execution.
The runner supplies no HEAD-moved/clean-worktree/exit-only substitute and never
calls hidden benchmark verification to provide intermediate feedback. Independent
validator protection remains the environment owner's responsibility.

## Task entry and planning ablation

`task_entry` is independent of the execution mode:

- `seeded-todo` (the compatibility default) writes a generic execution Todo.
  Follow-up phases update that Todo while it remains live and owned by this
  agent; completed or deferred work gets a new Todo. Updates preserve blocked
  state. The agent can still plan and replan during execution.
- `loopx-planned` runs the installed `$loopx` skill against the public
  `loopx todo plan` checkpoint before execution. The checkpoint shares the
  product's planner and continuation-aware Todo delta; it creates no planning
  Todo and starts no host loop. Select it only for heartbeat, Turn or LoopX Goal.

The model writes or reuses actual task Todos through the public CLI. The worker
reads the product packet again and checks the input digest, identity, Todo ids
and runnable/blocked state. A fabricated id, changed input, wrong owner, failed
planning process or missing result fails the entry; it never falls back to a
generic Todo. A blocked entry retains the referenced blockers and starts no
execution driver. Readback proves state and ownership, not semantic plan quality.

Planning uses a separate fresh `codex exec` session with native Goals disabled
for that call. Its session is not inserted into core Turn session bindings or
resumed by the subsequent execution. This is a planning-contract ablation, not
an exact reproduction of same-conversation interactive `$loopx` startup.
The default `planning_timeout_sec` is 300; planning and preparation consume the
same `scheduler_timeout_sec` phase budget as execution. Planning sessions are
included in native session/token aggregation. No planning checkpoint is counted
as a completed advancement Todo or settled work Turn.

Each phase keeps an immutable task document. New phases preserve Goal/Agent
identity and expose existing Todos to the planner; they do not clear waiting
state or force the agent active. An unresolved Turn must be recovered before
another phase can replace its task input. These wait/recovery rules apply to
both entry policies; they correct the earlier unconditional phase reset.
Every scheduler wake caps its host timeout against the remaining phase budget
before opening an execution. If only startup and settlement reserve remains,
it records a budget-exhausted no-op without creating a pending Turn.
The deadline uses the task environment's clock, including remote Harbor backends.

To compare entry policies, hold the execution mode, session policy, model,
effort, tools, feedback and total budget fixed, and use separate trials:

```yaml
kwargs:
  execution_mode: heartbeat
  task_entry: loopx-planned
  planning_timeout_sec: 300
  iteration_context: fresh
  turn_timeout_sec: 4700
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
its state. LoopX modes use the formal installer and doctor readback.

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
