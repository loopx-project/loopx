# DeepSWE practice

This note generalizes the current DeepSWE pilot without publishing task text,
case-specific trajectories, verifier output, credentials, local paths, private
runner details, or unqualified score claims.

## Research question

Compare the same model on the same pinned DeepSWE task under two harness arms:

- baseline: native Codex Goal with no LoopX state or continuation protocol;
- treatment: the preregistered LoopX product path.

The comparison is about harness value, not a model-only leaderboard result.
Every claim remains scoped to the pinned task set, runner, model, permissions,
budget, and verifier.

## Frozen selection and replacement

Freeze candidate order before observing new outcomes. Screen baseline cases in
that order with concurrency one and the preregistered retry policy. A candidate
enters the treatment tranche only when the baseline attempt is countable and
its official outcome satisfies the preregistered selection rule.

Infrastructure, setup, agent-start, terminal-closeout, or verifier failures are
not scored task failures. Replace an uncountable attempt with the next case from
the frozen queue. Do not rerun a case after its verifier result has informed the
controller, and never select replacements by browsing raw task or solution
artifacts.

## Native Goal proof

The baseline must use the Codex app-server Goal API, not `codex exec`, a prompt
whose first token is `/goal`, or an outer polling loop labeled as Goal mode.
The transaction is:

```text
initialize(experimentalApi=true)
  -> initialized
  -> thread/start
  -> thread/goal/set(status=active)
  -> thread/goal/get
  -> turn/start
  -> observe correlated turn terminal event
  -> thread/goal/get
  -> while Goal remains active, observe the next automatic continuation turn
  -> stop only after Goal leaves active or the shared Goal timeout expires
  -> stop worker
  -> independent verifier
```

`turn/start` acceptance is not completion. The benchmark host must continue
serving any environment bridge while it drains app-server events. It starts
only the initial task turn; Codex owns automatic continuation turns while the
Goal remains active. If the response turn id and event-stream turn id differ,
the event-stream id becomes canonical. The installed transaction, stdio
transport, event reducer, and receipt live in
[`benchmark_toolkit.native_codex_goal`](../../loopx/capabilities/benchmark_toolkit/native_codex_goal.py).
[`../native_codex_goal.py`](../native_codex_goal.py) is intentionally only a
compatibility import, so runner code and examples cannot drift into a second
implementation.

### Real Codex connection

The runnable example calls `codex app-server --listen stdio:// --enable goals`,
performs the transaction above, and prints only a compact receipt. Keep the
objective and task in files so raw text is not duplicated into command history:

```bash
python benchmark/deepswe/run_native_codex_goal.py \
  --cwd <task-worktree> \
  --objective-file <objective.txt> \
  --task-file <task.txt> \
  --model <model-route>
```

Use `--preflight-only` to verify initialize, thread creation, and Goal
attachment without starting a model turn. On Linux, the same runnable can opt
into the toolkit's host-filesystem boundary:

```bash
python benchmark/deepswe/run_native_codex_goal.py \
  --cwd <task-worktree> \
  --objective-file <objective.txt> \
  --task-file <task.txt> \
  --isolate \
  --isolation-work-dir <runner-created-per-run-dir> \
  --private-root <controller-private-root> \
  --profile-root <per-run-installed-profile>
```

Isolation is explicit; the default invocation is unchanged. The work directory
must be outside the private root and task workspace. The optional profile is a
writable process input, so create it per run or restore it from a pinned snapshot
rather than sharing it across trials. Use a deterministic per-run work directory:
if the worker is killed before normal cleanup, the next identical invocation
repairs stale workspace-alias references before launch and restores host paths on
exit.

When task-local LoopX control state exists, both `.loopx/registry.json` and
`.loopx/runtime/registry.global.json` must exist. Neither file means there is no
control state to relocate; only one file is treated as incomplete state and fails
closed.

A benchmark adapter can reuse the same runtime directly while keeping its
environment bridge active in another task:

```python
from loopx.capabilities.benchmark_toolkit.native_codex_goal import (
    NativeGoalConfig,
    run_native_goal_process_until_terminal,
)

turn = run_native_goal_process_until_terminal(
    NativeGoalConfig(
        cwd=task_worktree,
        objective=objective,
        task_instruction=instruction,
        model=model,
        sandbox_policy=runner_owned_sandbox_policy,
    ),
    process_command=runner_owned_isolated_app_server_command,
    process_env=runner_owned_environment,
    process_cwd=runner_control_directory,
    goal_timeout_sec=timeout_seconds,
)
```

The imported runtime owns no evaluator access, task command bridge, credential
policy, or score authority. Those remain explicit runner responsibilities. A
separate `process_cwd` is useful when the Goal-visible `cwd` exists only inside
the runner's mount namespace.

The treatment also needs three independent product-path proofs:

1. a Goal body generated for the `codex_app_ssh_goal` profile;
2. LoopX skills installed into the exact `CODEX_HOME` used by app-server;
3. the LoopX release-snapshot CLI named by that Goal body.

Prepare the latter two with
`benchmark_toolkit.native_codex_profile.install_native_codex_profile`. Do not copy
`SKILL.md` files into a runner image. Generate the first input with
`render_native_codex_goal_prompt`, keep app-server on the credential-free
`native_codex_profile_environment`, and route its provider through
`serve_runner_owned_provider_gateway`. The app-server receives only the gateway
URL and a fixed non-secret env sentinel. A Linux host-side worker must also run
inside `native_codex_isolation`; filtering child env alone does not prevent a
danger-full-access agent from reading parent process environments or ambient
HOME files. `native_codex_app_server_shell_policy_args` remains defense in depth
for model-created shells, not the credential boundary. Set
`NativeGoalConfig.required_skill_ids=profile.required_skill_ids`. The runtime then
uses `skills/list` before thread creation and fails before model work unless Codex
actually discovers the installed skill set. A filesystem check alone is not
treatment-fidelity evidence.

### One read-only Turn with a retained decision

Use the native runner with both `--decision-capture` and `--decision-sha256`
to test a single ephemeral Codex Turn against an existing LoopX capture. Supply
the absolute path to the original private `decision.json` and its digest. The
runner verifies the UTF-8 bytes before sending them as standalone tool data
with explicit Codex retention. This mode uses a read-only sandbox and ends
with the first Turn. It does not attach a persistent native Goal.

Use a Codex build that supports wrapped `thread/inject_items` retention, such
as 0.171.0. Captures above 48,000 bytes and digest mismatches fail before
`turn/start`. The receipt records the source digest and native Turn status.
To qualify Compaction, inspect the provider request immediately after a
mid-Turn window change and compare its tool data to the original bytes.

## Authority and anti-cheating

Both arms receive the same task-visible filesystem, network, sandbox, approval
policy, model credential envelope, and tool surface. Neither arm may read:

- evaluator answers, hidden references, verifier source, or expected patches;
- another trial's workspace, state, or trajectory;
- controller-private manifests or evidence;
- official reward or verifier feedback during the agent phase.

A private structured audit checks observed tool access against runner-owned
isolation attestations. The public receipt contains only stable labels, counts,
digests, and reason codes. Integrity qualification and treatment fidelity are
separate gates: a clean run can still be uncountable when the treatment did not
execute the preregistered LoopX path.

## Preflight and lifecycle

Before each launch, a no-agent preflight must prove:

- pinned runner and task-set revisions;
- exact case and arm identity;
- model, effort, time, token, concurrency, and retry envelope;
- answer/verifier denial and cross-trial isolation;
- no-upload and no-submission policy;
- formal installed LoopX CLI and skill readback from the pinned revision;
- real app-server discovery of the required LoopX skills;
- worker-before-verifier ordering;
- exact-job container binding before runtime isolation inspection when concurrent
  jobs can share an image;
- compact result and terminal-closeout destinations.

The runner owns task execution and verifier invocation. LoopX settlement occurs
only after controller validation of a compact terminal result. A successful
state write, Todo transition, or quota spend cannot turn an invalid benchmark
attempt into evidence.

## Public evidence

Record enough compact information to reproduce classification without exposing
protected material:

- manifest and runner revision digests;
- arm, model, effort, budget, retry, and permission labels;
- lifecycle phase and failure attribution;
- native Goal method/status evidence;
- integrity and treatment-fidelity dispositions;
- official score only after independent scoring and countability checks.

Keep raw tasks, trajectories, tool arguments, logs, diffs, credentials,
verifier output, private audit references, and local paths in ignored private
storage. Promote concrete result tables only after the matched study is solid
enough to support the stated claim level.

### Public trajectory lifecycle summary

For exploratory observations rather than a complete score release, see
[DeepSWE behavior discoveries](behavior-discovery/README.md): a standalone article
and bounded `behavior_finding` records. The note describes selected cases and
explicitly scoped duration comparisons, not a benchmark-wide outcome conclusion.

The runnable native Goal adapter now emits a nested
`public_trajectory_summary_v0` beside its existing compact receipt. The summary
is derived only from the receipt's typed Goal lifecycle counters: notification
kinds, item-event counts, completed turns, continuation turns, error events, and
Goal-status polls. `goal_terminal` means the adapter observed a completed turn
and then read a non-active Goal status; `attached`, `in_progress`, and
`turn_terminal` remain explicitly incomplete.

The summary does not inspect or retain event payloads, task or assistant text,
tool arguments or output, verifier output, credentials, or paths. Its item-type
counts are event counts, not inferred tool-call counts; the coverage block keeps
that limitation machine-readable. Malformed, missing, or internally
inconsistent lifecycle facts fail closed instead of producing a partial public
artifact. This is the active native-runner contract and does not restore or
depend on the archived legacy benchmark reducer.
