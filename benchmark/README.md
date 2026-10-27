# Benchmark research workspace

This is LoopX's workspace for active benchmark practice, narrow runner
examples, and explicitly identified immutable experiment archives. Its authority is the
[Long-Horizon Harness Benchmark and Research Program v0](../docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md).

The workspace follows three rules:

1. benchmark-native task, runner, verifier, and score semantics remain
   authoritative;
2. current real-run practice outranks legacy LoopX benchmark abstractions;
3. active work admits only generalized code and public-safe conclusions;
   immutable historical experiment snapshots may also be stored here under
   the archive placement rules below, without becoming active practice.

Reusable product policy stays in
[`benchmark-toolkit`](../loopx/capabilities/benchmark_toolkit/README.md). The
toolkit owns provider-neutral permission, artifact, and integrity boundaries.
This directory may contain thin examples and practice notes, but it is not
installed as a second LoopX Python package and does not grant execution or
publication authority.

## Engineering build map

The canonical engineering plan is [Section 11 of the English research
RFC](../docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md#11-engineering-construction-plan)
and [中文第 11 节](../docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.zh-CN.md#11-工程建设计划).
It separates repository readiness from benchmark claims:

- **E0--E1:** typed contracts, public/private boundaries, native runner
  preflight, and one conformance slice;
- **E2:** experiment-board lifecycle, concurrency admission, exact runtime
  observation, continuity, reconciliation, and safe closeout;
- **E3:** preregistered matched arms, treatment fidelity, integrity,
  countability, and uncertainty;
- **E4:** cross-benchmark replication and non-benchmark product qualification.

The active workspace should keep benchmark-family launch, verifier, scoring, and
failure semantics in adapters while reusable permission, integrity, lifecycle,
and public-safe projection rules stay in
[`benchmark-toolkit`](../loopx/capabilities/benchmark_toolkit/README.md).
Contributors should start with a bounded synthetic fixture or conformance seam;
live tasks, hidden evaluation, credentials, raw trajectories, submissions, and
unpublished comparisons remain maintainer-owned. Engineering readiness does not
by itself establish a C2 uplift claim.

The [unified work contract and session-role RFC](../docs/architecture/rfcs/controller-executor-unified-contract-v0.md)
extends this build map with C7: benchmark workers should reuse the same qualified
product profile and governed lifecycle as other entrypoints. Benchmark-family
adapters retain task, environment, verifier and scoring semantics. Proposed
single-/two-session comparisons count both sessions and all recovery costs;
helper delivery neither launches trials nor changes frozen historical arms.

中文：[统一工作契约与会话角色提案](../docs/architecture/rfcs/controller-executor-unified-contract-v0.zh-CN.md)
的 C7 延伸本建设图：worker 复用同一已验收产品模式和治理生命周期，benchmark 适配器
保留题目、环境、验证与评分语义。拟议单双会话对照计入双方及全部恢复成本；
交付 helper 不启动实验，也不修改已冻结的历史对照臂。

## Current work

- [Execution observations through ordinary Goal planning](runtime/EXECUTION_OBSERVATIONS.md)
  covers independent local measurements with ordinary Goal/Todo planning and
  stride selection, complete initial Goal retention, and migration from the
  removed `research_loop` preset. Historical trials keep their original versions
  and treatment definitions. Engineering checks do not establish autonomous
  research benefit or efficiency.
- **LHTB result highlight:** GPT-5.6 Sol + LoopX 1.0.3 Heartbeat reaches
  **0.4948 mean reward** on 46 tasks: **+17.3% vs Plain Codex** and
  **+10.6% vs native Codex Goal**. Strict solves are 7/46, matching Plain and
  above Goal's 4/46. At the supplementary post-hoc threshold ≥0.80, LoopX /
  Plain / Goal reach **15/46 / 12/46 / 14/46** (32.6% / 26.1% / 30.4%);
  no score equals 0.80 in this study. One effective trial per task-arm, designated replacements
  and unequal execution budgets make this an exploratory system comparison,
  not an equal-budget efficiency or isolated causal result.
  [Full comparison and limits](LHTB/studies/five-arm-gpt56sol-max/README.md)
  · [Interactive brief](https://loopx-project.github.io/loopx/benchmarks/lhtb/)
  · [LHTB official website](https://zli12321.github.io/LHTB/index.html).
- [`swe-marathon/README.md`](swe-marathon/README.md) links the published
  [SWE-Marathon research brief](https://huangruiteng.github.io/loopx/benchmarks/swe-marathon/).
- [`LHTB/studies/five-arm-gpt56sol-max/README.md`](LHTB/studies/five-arm-gpt56sol-max/README.md)
  documents the public-safe aggregate behind the bilingual
  [LHTB research brief](https://huangruiteng.github.io/loopx/benchmarks/lhtb/).
- [`deepswe/behavior-discovery/README.md`](deepswe/behavior-discovery/README.md)
  links the standalone
  [DeepSWE behavior-discovery article](https://huangruiteng.github.io/loopx/benchmarks/deepswe/behavior-discovery/).
  It publishes scoped exploratory findings and permitted duration comparisons,
  not the complete study outcome table.
- [`deepswe/README.md`](deepswe/README.md) records the current public-safe
  DeepSWE method: frozen selection, matched-arm authority, native Goal proof,
  independent verification, invalid-run replacement, and compact evidence.
- [`native_codex_goal.py`](native_codex_goal.py) is a compatibility import for
  the benchmark toolkit's installed native Goal runtime. The runnable
  [`deepswe/run_native_codex_goal.py`](deepswe/run_native_codex_goal.py) example
  connects that runtime to a real `codex app-server`. Benchmark-family adapters
  should import the installed runtime and its formal isolated profile helper,
  then retain only their isolation, environment bridge, verifier, and scoring
  concerns. The profile helper renders the real Goal prompt with its installed
  CLI and proves that the prompt, discovered skills, and release-snapshot CLI
  belong to one pinned product path.

## Archive placement

This section owns benchmark archive placement. Two kinds of archive have
different homes:

- Retired benchmark implementations, legacy abstractions, and standalone dated
  research packets stay in Git history rather than the working tree. The removed
  [legacy benchmark tree](https://github.com/loopx-project/loopx/tree/54d42da44283bb414b24f71d16642c25cbcaf5dd/deprecate/benchmark-legacy)
  remains available at its final retained revision for source archaeology.
- A named, immutable experiment snapshot may live in a versioned directory
  under `benchmark/` when its exact source bytes are needed to inspect a
  historical result. This is a historical reference, not an active runner,
  reusable implementation, or entry in the Current work list.

An experiment snapshot stored here must meet all of these conditions:

1. Its files are public-safe: no credentials, private configuration, raw task
   text, trajectories, or private evidence.
2. Companion documentation outside the frozen tree identifies its version,
   provenance and provenance limits, runtime dependencies, and known defects.
   Applicable withdrawn-result notices appear at the beginning of that document.
3. A pinned content hash and a read-only check verify the preserved bytes and
   executable modes. The check states its coverage; identity or syntax checks
   do not prove runtime correctness or validate historical scores.
4. The snapshot remains inert: no product imports, automatic task/model
   execution, or active CI benchmark runs. Later execution fixes require a
   separately identified change and must not overwrite the historical bytes
   or inherit their result attribution.

The [DeepSWE GPT xhigh v1 archive note](deepswe-gptxhigh-versions.md) registers
`deepswe-gptxhigh-v1/` under this rule. Its original files and result table are
preserved for inspection; the companion notice governs their current evidence
status. Neither archive category is the architecture for new work or grants
runner, verifier, submission, or publication authority.

## Matched model provider routing

For a custom Responses-compatible model endpoint, set `OPENAI_BASE_URL` and
`OPENAI_API_KEY` in the private runner environment before invoking
`python -m benchmark.edgebench.run` for either arm. The native SForge config,
official Codex home and LoopX worker use the same endpoint and environment-key
credential contract. A base URL without a key fails before creating the attempt;
the custom route does not copy or fall back to `CODEX_AUTH_JSON_PATH`. Credentials
are not embedded in Codex settings or the logged scheduler command. The operator
still owns endpoint access, actual model/tool qualification and private evidence.

With `OPENAI_BASE_URL` unset, login-file behavior is unchanged, including the
required `CODEX_AUTH_JSON_PATH`; an ambient API key alone does not select the
custom route. Change routes only between fresh attempts. Checkpoint continuation
must retain the captured endpoint/configuration and supply fresh credentials
externally. This routing repair enables matched experiments under the
[long-horizon research contract](../docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md);
it establishes neither sustained operation nor a solver-quality improvement.

## Inert checkpoint capture

This is a developer prerequisite for the [research checkpoint contract](../docs/architecture/rfcs/hierarchical-agent-stride-control-v0.md#77-research-state-branching-and-matched-continuation),
not a qualified research continuation. The EdgeBench CLI accepts an explicit
`--checkpoint-request /path/to/request.json` **host** file and stages its exact
bytes into the heartbeat worker's protected control directory. The declaration
is validated before trial creation/registration and its digest appears in the
runtime receipt. The programmatic `BenchmarkCodex` and heartbeat `SForgeWorker`
adapters also accept `checkpoint_request_path` to an already staged normalized
absolute **guest** file. Ambient host variables do not enable capture; omitting
these options disables the hook. Official and native-goal profiles reject them.

The request contains exactly these fields; select only authorized private
artifacts and exclude credentials and hidden evaluation material:

```json
{
  "destination": "/logs/agent/checkpoints/research-point",
  "components": [
    {"id": "code", "source_path": "/workspace/solution.py"},
    {"id": "evidence", "source_path": "/control/source-evidence.json"}
  ],
  "point_metadata": {"purpose": "inert source capture"}
}
```

Caller component IDs are preserved. Automatic LoopX-state, research-context and
session-binding participants choose an unused local ID when their preferred
name is already declared; source-path deduplication and byte witnesses still apply.

The destination must be a new direct child of the trial log owner's
`checkpoints` directory. Capture runs after a successful execute wake's original
settlement, before the next wake. It uses the remaining phase budget with a
30-second capture ceiling, preserves SQLite's committed WAL through the existing
backup owner, rejects observed source changes/symlinks and never overwrites a
destination. Capture failures are recorded separately in the wake receipt;
they do not replay work or change its settled outcome. The ceiling bounds
publication eligibility and RPC waits, not an operating-system I/O cancellation
guarantee. Capture cost remains part of the trial's elapsed time.

For readback, call the existing `effect_runtime_result` adapter with method
`runtime.checkpoint_artifacts`, action `verify`, the captured `root` and the
receipt's `expected_sha256`. Action `materialize` with those fields and a new
`destination` verifies and copies the unit into isolation. Neither action adopts
state or starts execution. The new `loopx_checkpoint_artifact_set_v0` manifest
is an internal artifact contract; `point_metadata` is unverified evidence.
`research_point_qualified`, `execution_authority_granted` and `privacy_certified`
remain false. Integrity checks cover the declared members and observed stable
source boundary, not adversarial filesystem mutation or a cross-owner transaction.
Native session reconstruction, effect reconciliation, fresh authority admission
and evaluator cooldown/opportunity inheritance must pass before matched continuation.

An explicitly checkpoint-enabled POSIX heartbeat CLI worker sets its own soft
core-dump limit to zero before starting the native Host and its tools. This
prevents crash memory dumps from growing a later workspace snapshot. The hard
limit and parent launcher stay unchanged; workers without this opt-in retain
their inherited limits. Existing files are never removed or silently excluded:
retain any existing crash diagnostic outside the declared participants before
requesting a new point. Omit the checkpoint declaration on a new worker to
retain ordinary launch behavior. This is file/session reconstruction, not
process-memory recovery or permission to inspect crash memory.

For pre-decision capture at a **fresh replan projected at the start of a heartbeat
wake**, add `"trigger": "fresh_replan"` and set `destination` to the trial's
`checkpoints` directory itself. The Host preloads that wake's original quota guard
once, the typed owner validates its committed semantic replan identity, and the adapter
captures from the latest successful, settled work wake before invoking the model.
Work selection may still be owed on this new guard: the exact registered Agent,
Goal, Turn and semantic obligation must match, but inert capture does not invent
a causal settlement identity or authorize delivery before selection. An already
selected guard retains its existing settlement-identity checks.
The model reads the complete preloaded guard and follows its existing selection,
required-read, recovery and settlement contract. Each obligation has one immutable
destination; a repeated read verifies that point instead of replacing it with later
work. The after-wake hook cannot substitute a post-decision state. Capture failures
remain explicit and do not settle or complete the new work.
The first attempt is also retained: if it fails and the model proceeds, retries
cannot substitute a later state for that missed pre-decision point.
Losing, denying access to or corrupting the staged declaration records a capture
failure while the ordinary wake continues. The Host still obtains and delivers
the original quota guard; an unavailable admission owner stops work as before.
If that guard projects a fresh replan, the missed first attempt is retained even
without readable declaration bytes, so repairing the file cannot capture later
state under the same obligation. Subsequent distinct replans remain eligible.

A Host can now explicitly hold a **mid-wake quota response** using
`quota should-run ... --decision-output-root CAPTURE_ROOT
--decision-handoff-socket SOCKET`. The socket must already be listening in a
private physical directory owned by the same user (Unix only). After writing the
complete original decision, the CLI sends one newline-delimited JSON object
`{"path": ".../decision.json", "sha256": "..."}` and withholds successful output
until it receives exactly `{"released": true, "sha256": "..."}` for those bytes.
The existing selection command preserves both transport options. Ordinary CLI
output and admission remain unchanged when the option is absent; this option
does not enable TurnEnvelope.

`benchmark.runtime.checkpoint.held_quota_decision` is the matching local Host
context: it validates the declared capture root/digest and asks the existing
TypeScript replan selector for capture intent, then releases the original
response on normal context exit. A Host exception, disconnected/lost reply,
changed capture or the 120-second transport deadline fails the call while
retaining the original admission and captured decision. Recover that original
identity; do not obtain a replacement guard to reconstruct a missed point.

An explicit native CLI caller can pass its live `child_process` handle as
`native_process` to `held_quota_decision`. The Host interrupts its owned process
group, requires graceful leader exit, and reaps descendants before calling the
selector or entering the capture body. Failed interruption stops that capture;
it does not turn an unfinished native history into a completed cursor. After a
successful context exit, `continuation_body` contains the original guard for an
explicit resume of **that source session**. The killed tool remains aborted in
native history; the packet is supplied on a new native turn and is never rerun.
A dead source socket needs no acknowledgement in this path. Omit the process
argument to retain the original output-only transport.

A real native CLI regression holds this interrupted boundary beyond the tool's
yield interval and verifies retained context, explicit original-packet delivery
and one guard receipt. This qualifies interruption/recovery after the Host
receives the packet, not absence of earlier inference, independent writer
quiescence, or an automatically captured research checkpoint.

The heartbeat worker now installs that listener when its explicit checkpoint
request uses `fresh_replan`. The renderer carries the private handoff endpoint
into guard, selection and reentry commands; this path also uses the existing
captured TurnEnvelope. With checkpointing off the renderer, process lifecycle
and model-visible output stay unchanged. Ordinary guards and repeated replan
obligations are released without restarting the native CLI. On a first replan,
the worker selects through the typed owner, interrupts the source, records its
session, and captures explicitly declared inert files and optional native history. Failed optional capture
still resumes the source with its original packet; altered packet bytes or lost
session identity fail closed. All resumptions share the original wake deadline.

This is **not a qualified complete research checkpoint**. Interrupted native
capture is qualified on the tested Linux profile. On Darwin, a first mid-wake
replan with `native_session` is explicitly refused **before** signalling the
native CLI: its original tool result continues and the missed point is recorded
once. The observed Darwin Host can omit the pending command from its interrupted
history; delayed reads and leader-only signalling did not recover that cursor.
Completed-wake capture remains separate. No history/output predicate is relaxed.
On the qualified profile, the actual worker can seal an explicit interrupted cursor: all retained
pages, the failed final guard command with no released output or later visible
item, its parsed CLI routes/Turn identity, and the original held decision bytes.
A second matching guard is ambiguous and rejects. The exact renderer Turn
assignment is supported; other shell programs and expansions are unsupported
by this conservative provider profile. History is
rechecked against the byte witness before publication. A native fork reconstructs
that entire captured prefix, including the aborted tool, without using a completed
Turn as a substitute; completed-cursor checks stay unchanged. Lost fork replies
reuse the existing one-attempt recovery path.

Full capture uses the external controller path below. Native-only history plus
declared files remains an inert subset. An interrupted cursor can now enter the
existing explicit session-binding admission for a distinct registered destination
Goal/Agent. Its pending source guard remains historical input; the receiver must
obtain a new work admission and lease. Binding alone grants no execution or
evaluation opportunity.
Remove the checkpoint request to disable automatic handoff; a direct caller can
omit the socket option to disable only the transport.

Add `"native_session": true` to include Codex's completed native cursor, all
retained history pages, rollout/compaction records and native index/history SQLite
stores. The adapter verifies the native session's actual workspace, reads its full
cursor before the byte probe and reads it again after that probe. A changed cursor,
configuration, source session binding or participant inventory rejects before publication; the artifact
owner then verifies the same physical witness through capture. This prevents later
Turns on the selected source session from being sealed under an earlier cursor. The adapter excludes
credential files, logs, memories and the symlinked skill installation. Record the
effective configuration digest and Host version; tools/skills and credentials need
their existing installation/authentication owners. Native state is opaque and its
storage layout remains a pinned provider qualification.

`loopx.hosts.codex_checkpoint.fork_native_checkpoint` verifies a unit and
reconstructs a distinct native session in a **new isolated environment preserving
the captured guest paths**, with matching configuration and Host version. It checks
the full history digest and cursor, starts no model and registers no LoopX binding.
A failed or partial reconstruction is not an admitted session. This is a session
participant prerequisite, not full research continuation or arbitrary path import.

An explicit controller can then prepare that participant through the existing
registry and session owners. Prepare a fresh governed benchmark Goal and
distinct Agent identity in the isolated guest; set its destination `LOOPX_REGISTRY`,
`LOOPX_RUNTIME_ROOT`, `LOOPX_GOAL_ID` and `LOOPX_AGENT_ID`, plus the qualified
`CODEX_BIN`, `CODEX_HOME`, `LOOPX_PROJECT`, `MODEL_NAME` and `REASONING_EFFORT`.
The captured session profile must match the destination, and native automatic
Goals must remain explicitly disabled. Fork deferral preserves native Goal data;
it does not remove historical authority. The controller environment and explicit
configuration are not exported as authority or credentials.

```sh
python -m loopx.hosts.codex_checkpoint \
  --root /private/captured-point --expected-sha256 CAPTURE_DIGEST \
  --config-path /private/qualified-config.toml --sandbox read-only
```

This command starts no model and claims no work. It stores the verified fork for
the registered destination Goal/Agent, without replacing an existing binding.
The registry owner's fence guards the configuration check and binding write;
this preserves the existing profile's lifetime semantics. The lifecycle-only
`source_session_v1` profile is rejected by its owner because it cannot run
governed benchmark work. The binding atomically retains the captured artifact digest.
If the binding committed but its controller reply was lost, repeat this exact
command: it reads the original binding and verifies current Goal/Agent/profile,
Host/configuration and complete native history, returning the same admission
without another fork or model call. Changed or unmarked bindings reject; this
cannot retrofit recovery onto older bindings. Once native work has advanced, use
the existing continuation rather than retrying admission at the old cursor.
Provenance survives ordinary observations of the same session, but does not follow
a replacement session, profile or Goal lifetime. Before dispatching the native
fork, the controller durably records the artifact/destination basis and original
native thread identities under the existing process lock. If the fork reply is
lost **before binding**, retry the same command in the same isolated environment.
It admits only one new native identity whose native parent, workspace and complete
history match the captured source. It never dispatches another fork on recovery.
Missing or multiple outcomes, changed destinations/configuration and advanced
history remain unresolved and require reconciliation; do not erase the attempt.
At the qualified Host version, the interactive `thread/list` omits a fresh fork
until its first user event. Recovery therefore locates opaque IDs through a
read-only native SQLite index, then verifies them through `thread/read` and
`thread/turns/list`; it never edits native rows or treats the index as authority.
Omit
this command to leave the unit inert. Code/research authority, unresolved effects,
native evaluator admission and remaining whole-trial budget still require their
owners before executing a research continuation. This source-checkout controller
entrypoint is a partial participant journey; it does not ship a frontend restore
operation or qualify a complete research point.

The explicit real-Host tests below also qualify this CLI and the worker's native
resume transport: a local static provider receives the retained conversation,
runs a read-only verification of materialized code/negative evidence and completes
a new native Turn plus its original LoopX guard/writeback/spend. Later source
candidate/evidence and original native participants remain unchanged. These tests
swap fixed paths only after their synthetic source is quiescent; they do not prove
complete environment isolation. Goal configuration change, unsupported lifecycle
profile, profile change and duplicate binding are rejected. This proves protocol adoption
and synthetic continued work, not model research utility or native score gains.

For the native feedback protocol with **no automatic evaluations**, start an
explicit checkpoint-aware native judge:

```sh
python -m benchmark.edgebench.checkpoint_judge --checkpoint-capture \
  --tasks-dir /path/to/tasks --log-dir /path/to/private-run-logs
```

The supported public checkpoint provider is the compatibility fork at
[`071a884012754127f28f5b84be10f533a42db527`](https://github.com/loopx-agent/EdgeBench/commit/071a884012754127f28f5b84be10f533a42db527).
It contains `sforge.harness.checkpoint` and the native
`create_app(..., checkpoints=True)` seam used by the controller below. The earlier
`0e20fb499ee248f012d6467c00b3dec00a29fe85` compatibility pin and upstream
`sforge==1.1.0` do not contain that API and cannot qualify joint continuation.
Enabling capture against an unsupported provider fails before adding routes.
This is pinned proposed source, not an upstream release. From this LoopX checkout,
prepare an isolated Python 3.12 environment and verify the imported API:

```sh
CKPT_PROVIDER_ROOT=$(mktemp -d)
git clone https://github.com/loopx-agent/EdgeBench.git "$CKPT_PROVIDER_ROOT/provider"
git -C "$CKPT_PROVIDER_ROOT/provider" checkout --detach 071a884012754127f28f5b84be10f533a42db527
uv venv --python 3.12 "$CKPT_PROVIDER_ROOT/venv"
uv pip install --python "$CKPT_PROVIDER_ROOT/venv/bin/python" \
  -e '.[test]' -e "$CKPT_PROVIDER_ROOT/provider" 'httpx>=0.28,<1'
"$CKPT_PROVIDER_ROOT/venv/bin/python" -c 'import inspect,sforge,loopx,sforge.harness.checkpoint; from sforge.harness.judge_server import create_app; assert "checkpoints" in inspect.signature(create_app).parameters; print(sforge.__file__); print(loopx.__file__)'
"$CKPT_PROVIDER_ROOT/venv/bin/python" -m pytest -q benchmark/tests/test_checkpoint_judge.py
# Linux with an owned disposable Docker daemon; grades only synthetic fixtures.
LOOPX_EDGEBENCH_DOCKER_SMOKE=1 "$CKPT_PROVIDER_ROOT/venv/bin/python" -m pytest -q \
  benchmark/tests/test_edgebench_feedback_docker.py::test_checkpoint_opportunity_uses_native_admission_and_real_grade
```

The Docker journey checks a real grade, remaining rounds/cooldown, incumbent bytes,
and the cold-registration counterexample; it does not restore evaluator authority.
Native session qualification is separate: the tested Host is `codex-cli 0.160.0`.
Retain the complete paginated history/compaction and actual native stores from an
authorized completed source, then run capture and fork in isolation with that
same binary and fixed guest paths. Compare every retained Turn ID and the full
history digest, including exclusion of later history; mocked pages alone do not
qualify this participant. No model call or LoopX registration is implied by fork.

The current cursor/workspace regressions also exercise `codex-cli 0.162.0-alpha.2`
through real native writer/readback and the actual CLI/typed artifact owners. A
static localhost response stream supplies synthetic source Turns; this tests the
storage protocol and capture boundary, not model behavior or research utility.
Run with an explicit native binary; ordinary runs keep the qualification skip:

```sh
LOOPX_NATIVE_CHECKPOINT_BIN=/path/to/pinned/codex python -m pytest -q \
  tests/control_plane/test_research_checkpoint_capture.py::test_real_native_cursor_must_match_sealed_store_basis \
  benchmark/tests/test_native_checkpoint.py::test_real_native_workspace_readback_rejects_foreign_code_basis
```

Its default-off factory otherwise preserves the native routes. With
`"evaluation_opportunity": true` in the worker declaration, the capture hook
reads that registration's released history and complete public result details,
next round counters, accepted submission clock, remaining cooldown, task/native
source identity and original trial deadline. It also preserves the native-selected
incumbent's opaque submission archive, separate from the current working candidate.
Automatic participants prefer `evaluation_opportunity` and `evaluation_incumbent`
and allocate unused IDs when a caller already owns either name. The sealed
`point_metadata.evaluation_opportunity` maps `component_id` and
`incumbent_component_id` to the retained evidence and archive; the latter is null
when no native incumbent exists. The source witness follows this same mapping.
Native ranking chooses its original released round;
the result identity, archive digest and byte count bind its download to the same
judge epoch and consumed-round state. Missing or moved selected bytes reject
capture; no selected result is explicitly recorded as absent. The transport never
extracts archive members, overwrites a prior participant or silently accepts a
truncated response. Its checked file witness must match the descriptor before
the artifact owner publishes the unit. The provider uses the existing
native admission/history locks and rejects an outstanding grade, unreconciled
consumed round, missing result details, automatic-evaluation history or closed
registration. It never returns tokens or hidden task material and never grades,
registers or refills an opportunity. Download is limited to the same registration
and a matching capture digest; source movement requires a new owned boundary.
The local `sforge_checkpoint_opportunity_v0`
artifact records these facts; it grants no restored admission. Disabling capture
means omitting both the judge flag and worker declaration option on a new run.

For an isolated single-Goal trial, `"loopx_state": true` adds the existing
state-backup owner's inventory: registered active state, project research state,
runtime authority/effect/quota journals and registry. The declared worker and
physical routes must match that registry; missing registered state, incomplete
inventory, symlink routes and narrower overlapping components reject capture.
File and SQLite authority providers remain inert artifacts after materialization;
copying their old leases or settlement records grants no new execution authority.

With this option, capture also retains the **complete paged canonical Todo read**
at one provider revision when its source coordination is promoted, as a private
artifact. That context also preserves the complete validated Explore event log
(nodes, edges and findings, including dead ends) and all indexed vision/checkpoint
records, including their original stride decisions. These are historical inputs,
not imported destination graph, vision or effect authority. Empty histories are
explicit; malformed rows and incomplete appends reject capture. The original
source-file digests/absence must match the artifact owner's source witness, so an
append between the context read and sealing cannot mix research points. It rechecks the canonical head and exported bytes before sealing;
missing promoted authority or a moved revision fails this optional capture while
ordinary work continues. An unpromoted source keeps the previous inert inventory
capture and explicitly records canonical context as unavailable; it cannot use
this context-adoption path. Leases are excluded from the read, while historical
claim/status fields remain evidence. Declarations without `loopx_state` keep
their previous path.

The generic [research-checkpoint CLI](../docs/reference/research-checkpoint.md)
owns ordinary Goal capture, native-session admission and research-context adoption.
The benchmark adapter adds task workspace, evaluator and budget participants;
Core does not import the benchmark package.

After separately admitting the checkpoint's native session, an operator can
adopt this retained context on an **already claimed and leased destination Todo**
whose note is empty:

```sh
python -m loopx.hosts.research_checkpoint \
  --root /private/checkpoints/point --expected-sha256 CHECKPOINT_SHA256 \
  --source-todo-id SOURCE_TODO --todo-id DESTINATION_TODO \
  --operation-id CONTEXT_OPERATION --expected-revision CURRENT_REVISION \
  --lease-key CURRENT_LEASE_KEY --lease-version CURRENT_LEASE_VERSION
```

Supply the destination `LOOPX_PROJECT`, `LOOPX_REGISTRY`, `LOOPX_RUNTIME_ROOT`,
`LOOPX_GOAL_ID`, `LOOPX_AGENT_ID`, `CODEX_BIN`, `CODEX_HOME`, `MODEL_NAME`,
`REASONING_EFFORT` and `LOOPX_CODEX_SANDBOX` explicitly. The Goal and Agent must
differ from the captured source. The session owner must admit the distinct
current native binding and matching execution profile. Before prepare and adopt,
readback must match the captured complete native history, Host and configuration;
a branch that has already continued cannot adopt context from this older point. The adapter uses the existing continuation prepare/adopt owners,
then reads back the canonical note and Markdown projection. It retains the full
source read in a private workspace-relative file, with its checkpoint and source
revision references; current requirements, status, claim and lease stay governed
by their existing owners. The operation starts no model and submits no candidate.
The option is disabled by omitting the command; an adopted note is ordinary
current Todo context and can be changed through its owner.

This is **Todo context adoption**, not canonical graph/vision/effect reactivation
or a complete research continuation. A deterministic local-provider qualification
uses the real native Host fork and resume, CLI and canonical continuation owner:
a tool reads the retained candidate, negative Todo/Explore evidence and vision, excludes
later source evidence, then settles current governed work. This proves mechanical
adoption and use, not model judgment or research benefit. It does not replace
current Todo requirements with source requirements, reactivate source claims, or
certify Host budget and evaluator participants as part of the same point. If prepare/adopt commits but
its response/projection is uncertain, repeat this command with the **same original
operation ID, expected revision, checkpoint and lease proof**. It replays the
existing prepare/adopt receipts and reconciles projection; it does not create a
new note or reset execution authority. Changed notes, artifacts, native history,
leases or request identity still refuse recovery. Do not substitute a new session,
claim, revision or context operation to conceal uncertainty. This qualifies only
context-command recovery; joint participant recovery and the packaged frontend
journey remain open.


The composed local qualification also seals the native evaluator capsule with the
conversation/code/context artifact. An actual resumed Codex tool reads captured
research and public feedback, computes a synthetic candidate from captured code,
and submits through the native HTTP owner. It retains consumed rounds and the
active cooldown, rejects a further submission at the inherited limit, and enters
through the existing SForge Host phase check with the captured remaining duration.
Later source evidence stays excluded and the source service remains unchanged.
The fixture quiesces participants itself, uses a local deterministic model and
synthetic grading, and does not activate a destination Explore graph or old
vision/effects. It qualifies this composed mechanical path, not an automatic
cross-owner barrier, ambiguous activation recovery, isolated Linux solver restore,
or research utility. Those remain requirements before scientific continuation.

Declare authorized workspace code/research files, effective configuration and
installed source/profile pins as non-overlapping components. The native and
opportunity participants do not imply that omitted state was saved. Every source
remains private; inspect the verified inventory before qualifying a complete point.
Automatic native-store participants allocate unused artifact IDs without changing
caller IDs. The sealed `native_session.store_members` mapping binds those IDs to
the existing allowlisted native filenames. Reconstruction accepts earlier fixed-ID
artifacts, rejects duplicate/undeclared mappings and keeps source-history checks.

Fresh replan can arise at Host preflight or inside a running native wake. The
explicit handoff retains the original guard and rejects replacement by a later
point. Full-state capture additionally requires the controller writer barrier
below. Capturing that point does not admit a destination session or prove useful
continued research.

On a restored conversation, both hooks still require an accepted current-Goal
semantic replan citing the source checkpoint and a new completed native
continuation. With `"native_session": true`, preflight reads only the latest
Turn's identity/status (`itemsView: "notLoaded"`), after checking the bound native
session and workspace. This provisional cursor admits a capture attempt; it does
not certify the retained history. Both full native observations during capture
must verify the retained history prefix and this completed continuation before
sealing. After the explicitly bound initial context reset, that prefix is empty;
the checkpoint/review/new-session binding remains required, and a new completed
native Turn is still mandatory. Neither an absent prefix declaration nor a
semantic-only review can replace those checks. A changed checkpoint/session, missing cursor or altered prefix fails
capture. Preparation and declarations without native capture keep full-history
preflight. Canonical review, first-boundary deduplication, current authority and
the default-off path keep their existing rules.

### Controller-only native capsule transport

The read-only participant above does **not** implement the newer native capsule
API. The separate controller adapter targets the API proposed in
[EdgeBench PR #23](https://github.com/ByteDance-Seed/EdgeBench/pull/23), using the
public compatibility pin `071a884012754127f28f5b84be10f533a42db527` above.
Start that pinned native judge with `sforge serve --checkpoints`.
Its private controller configuration file has exactly `url`, `admin_secret` and
`bank_root` (the native log directory's `checkpoints` directory). Keep this file,
the bank and controller process outside every solver's filesystem/network authority.
An owner-only mode is a storage precaution, not proof of solver exclusion.

When the selected native runner supports `host_registration_callback`, the
adapter binds enabled checkpoint/feedback observers through that host callback
before container creation. It does not inject the registration token into the
solver environment. Frozen older providers retain their original environment
binding path; runs without either observer add no callback. Blind modes also
remove the newer `SFORGE_FEEDBACK_URL` from the solver environment. A restored
registration may include the native provider's nonempty `feedback_relay_id`;
all original activation identities must still match, and unknown fields fail
closed. Neither compatibility path changes scoring or grants solver authority.

```sh
python -m benchmark.edgebench.checkpoint_controller \
  --controller-file /private/source-controller.json capture \
  --token-file /private/source-token --capture-id 0123456789abcdef0123456789abcdef \
  --destination /private/new-native-export
python -m benchmark.edgebench.checkpoint_controller \
  --controller-file /private/fresh-controller.json restore \
  --root /private/new-native-export --expected-sha256 EXPORT_DIGEST \
  --run-id isolated-branch --result-file /private/fresh-registration.json
```

Use a stable declared capture identity for one point; retries read its original
native state, including after the source improves. The existing typed artifact
owner seals and verifies the native capsule. The native owner
alone validates evaluator compatibility, ranking, released feedback, consumed
rounds, resources and retained cooldown, then creates a fresh registration.
The adapter never grades, extracts incumbent archives or adds controller
configuration/live registration token records to the export. Opaque incumbent
bytes and released reports still require private handling; privacy is not certified.
Restore writes its new token only to the explicit
private result file; stdout contains its path. Wrong authority, corrupt bytes,
aliases, foreign bank entries, source-run reuse and duplicate native admission
fail without a fresh evaluation. Redirects cannot forward the admin secret.
Controller HTTP requests connect directly to the configured origin, ignoring
environment proxy settings. This applies only to the explicitly invoked native
capsule adapter; it does not change model API or installation proxy behavior.
After an uncertain restore response, repeat the same controller `restore` command
with `--recover`, preserving the same capsule digest, run identity, result-file
and optional phase-deadline-file. Recovery reads the original native activation
through `/api/v1/checkpoint/restore-result`; it does not allocate a registration,
replay grading or reset cooldown. A successful result may be read back again.
An absent original activation or lost live authority refuses recovery. Do not
switch identities or perform ordinary fresh registration as a retry.

For an explicitly owned Docker worker, `capture-worker` can additionally freeze
its writers while copying declared files and capturing the native capsule. Supply
`--worker-request` with a physical JSON file containing the full `container_id`,
`components: [{"id": "code", "source_path": "/task/code"}]`, and the separate
`phase_deadline_path` inside that container. Use the same controller, token-file,
capture-id and destination arguments as `capture`; no phase file is read through
an executing container process. The controller uses the Docker daemon's
pause/archive/unpause path, then seals the copied files and nested native capsule
with the existing typed artifact owner. Component IDs are preserved when they
collide with the automatic native capsule name. Neither Docker access nor judge
administration is passed into the solver. Omit this command to retain ordinary
capture behavior; there is no automatic activation or retention policy.

The export destination's filesystem also holds each raw Docker archive while
its members are extracted. Provision space for the raw transfer, extracted
participants and sealing copies; moving the destination does not reduce those
bytes or extend the capture deadline. The archive spool uses the private export
staging directory instead of ambient temporary storage. If that filesystem fills,
capture still fails and runs its existing reader/barrier cleanup without publishing an
outer artifact; it does not retry on another volume or substitute a later point.

The controller checks its original shared deadline during download progress, tar
header and file reads, safety filtering and permission restoration, including
standalone SQLite companions, the held request and the phase file. It retains
HTTP read timeouts instead of using Docker's process-stream reader, which disables
them. These checks are cooperative between IO operations, not preemption of a
blocked filesystem call or a new allowance per member. On expiry the daemon reader
closes before this operation's writer cleanup; no native point or outer artifact
is published from the expired export. Safe extraction still rejects the complete
invalid inventory before writing member payloads, uses Python's data filter and
restores ordinary source modes without owners or special permission bits.

The container must initially be running and unpaused. Cancellation, failed file
reads, rejected native capture and a lost pause response attempt to release only
this operation's barrier. Failure to resume the worker is reported and publishes
no outer artifact. An initially observed pause or a definite Docker conflict
rejects without unpausing. Controlled captures on the same host/user share a
daemon-ID/container-ID lock under `~/.cache/loopx/checkpoint-workers`, across
controller banks and Docker endpoint aliases. External actors do not participate
in this lock: an uncertain transport outcome retains the original attempt and
cleanup obligation, not proof of exclusive ownership against arbitrary actors.
The first attempt owns the capture identity; a failure cannot silently pair
later files with an older native point. Missing paths, archive links, traversal and special members reject.
Declare only the intended private code/state stores: a whole home can contain
credentials, and neither archive filtering nor a freeze is privacy certification.

This explicit command produces an **inert joint file snapshot**, still
`research_point_qualified=false`. It does not snapshot process memory, freeze
external writers or activate canonical research state. Ordinary native restore
rejects the outer unit; callers must use the declared native capsule and existing
fresh-authority admission paths.

For automatic capture, the heartbeat declaration must include `"trigger":
"fresh_replan"`, `"native_session": true`, `"loopx_state": true`,
`"evaluation_opportunity": true`, and `"controller_handoff": true`, in addition
to its authorized code components, destination and metadata. Pass both
`--checkpoint-request REQUEST.json` and `--checkpoint-controller-file CONTROLLER.json`
to `benchmark.edgebench.run`, with native feedback. The controller file remains
host-private; only the declaration enters the worker. Missing or mismatched opt-ins
reject before trial creation. The backend binds the supervisor to its actual
container and native registration, rather than accepting a solver-supplied token.

The worker prepares native history, the original decision, canonical research
context and its file witness while holding the model boundary. The controller
pauses that worker, rereads the exact live handoff and copies declared sources
through Docker archive. A differing source witness or expired/removed request
rejects **before** native evaluator capture. Ordinary permission bits survive safe
archive extraction so private authority directories do not falsely appear changed.
The controller seals the frozen files with incumbent, used opportunities, cooldown
and remaining Host phase. It releases the barrier before publishing a private reply;
the source continues from the original packet and deadline. Optional capture
failure records a missed point, never a later replacement or a new model/eval budget.
A barrier that cannot be released remains an explicit controller recovery problem.

The explicitly enabled full controller capture hooks now share a 90-second window for complete
native/research preparation, stability checks and live-source capture (previously
30 seconds). Both hooks cap it by the original trial phase; the controller keeps
that same absolute deadline. After releasing the source, it may spend up to 30
more seconds sealing its immutable exports, also within the original phase. This
allowance changes neither the trial budget nor cooldown, completeness or retry
rules. A missed point remains missed; direct controller commands retain their
caller-supplied timeout, and other capture declarations keep their 30-second
window. Capture remains off unless explicitly enabled.

A real native CLI/worker plus isolated Docker/native-HTTP qualification covers the
mid-wake path, complete canonical context, conserved evaluator opportunity and
original source continuation. It uses a deterministic local model and synthetic
grading. A fresh isolated Linux receiver can bind that interrupted native prefix,
adopt captured research context on its currently leased Todo, restore native
evaluation/Host allowances and run a new admitted Turn. Qualification covers a
resumed tool consuming retained code/context, producing a synthetic candidate,
receiving its next native grade and settling the destination Turn exactly once.
Lost binding replies and native activation readback retain their original
identities; wrong-source/digest and advanced-history admission reject. No old
lease, quota, timer or token is activated. Scientific restart still requires the
chosen installed source/profile and experiment inputs to be admitted; this
mechanical fixture proves neither model judgment nor policy benefit.
The completed-source qualification also runs six actual settled native work wakes
without fabricated history, with stride enabled and Explore planning separately
off/on. The resulting periodic review is captured before policy selection; a
recorded grow decision is followed by a larger synthetic work slice and normal
settlement. This proves mechanical composition, not model-selected strategy
quality. Retained research remains inert destination context: fresh graph,
vision/policy review adoption and same-point scientific comparison remain open.
Omit the controller file and `controller_handoff` on a new run to disable automatic
controller capture; removing the checkpoint request disables capture entirely.
Existing artifacts remain inert, and the source trial is not restarted by disabling it.

The native handoff accepts and reads one bounded request before validating its
physical capture root. A missing or symlinked root closes that connection;
the original source can correct its path and continue through the same listener.
The refusal neither captures nor releases the old packet and does not renew the
original wake deadline. The transport does not create or follow a foreign root.
Host failures before a connection is accepted remain execution failures. They
are not packet refusals and cannot enter the source-preserving retry loop.

To reproduce **joint source capture and both fresh-guest receivers**, use
[`qualify_checkpoint_joint.py`](tests/qualify_checkpoint_joint.py) from the current
clean dedicated LoopX checkout; keep it frozen for the whole command. Run the
controller on Linux as the ordinary owner of an explicitly
owned disposable Docker daemon. Prepare the provider/venv above, install this
checkout's Node dependencies with `npm ci`, and put a compatible **Host** Node on
`PATH`. Separately supply Linux **amd64** binaries for `codex-cli 0.160.0` and Node
`24.21.0`, and a caller-prepared `python:3.12-slim` image. These inputs are explicit;
the qualification does not download a native CLI, pull an image or use model
credentials. The receiver also imports `harbor==0.20.0` to exercise the actual
phase-command builder; it does not launch a Harbor job. Build Python 3.12 guest
dependencies from the pinned provider plus this qualification prerequisite:

```sh
set -eu
"$CKPT_PROVIDER_ROOT/venv/bin/python" -c 'import sys,tomllib; from pathlib import Path; d=tomllib.loads((Path(sys.argv[1])/"pyproject.toml").read_text()); print("\n".join(d["project"]["dependencies"]+d["dependency-groups"]["dev"]+["harbor==0.20.0"]))' \
  "$CKPT_PROVIDER_ROOT/provider" > "$CKPT_PROVIDER_ROOT/guest-requirements.txt"
mkdir "$CKPT_PROVIDER_ROOT/guest-deps"
docker image inspect python:3.12-slim >/dev/null
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
  -v "$CKPT_PROVIDER_ROOT:/qualification" python:3.12-slim \
  python -m pip install --target /qualification/guest-deps \
  -r /qualification/guest-requirements.txt
# Set these to the explicitly prepared Linux amd64 binaries.
CKPT_GUEST_CODEX=/absolute/path/to/codex
CKPT_GUEST_NODE=/absolute/path/to/node
"$CKPT_PROVIDER_ROOT/venv/bin/python" -m benchmark.tests.qualify_checkpoint_joint \
  --provider-source "$CKPT_PROVIDER_ROOT/provider" \
  --native-binary "$CKPT_GUEST_CODEX" --guest-node "$CKPT_GUEST_NODE" \
  --guest-dependencies "$CKPT_PROVIDER_ROOT/guest-deps" \
  --output "$CKPT_PROVIDER_ROOT/joint-result"
```

The public fixture runs six actual native work wakes, captures the pending
pre-decision guard and continues the source; the ordinary and stride receivers
then use separate fresh guests with no mounted source authority. It includes a
whole declared workspace directory and a read-only Git member. Receivers run as
the ordinary non-root owner, select the workspace by its sealed source path and
materialize it through the typed artifact owner. Copied source registry/state
files remain historical: current authority uses a fresh external registry/runtime,
and the old Goal cannot be admitted through it. Only the model
response stream and grading/backend fixtures are synthetic; the native CLI,
canonical SQLite authority, worker, supervisor and capsule transport are real.
The retained-history recapture variant keeps the controller and its bank outside
every guest mount. It performs and settles actual candidate work, reaches a genuinely
new due replan, and captures research, new native history, code, incumbent,
opportunity and the original phase through the real controller barrier. Another
isolated receiver restores that newly sealed point and performs actual work.
`recapture.log` and `grandreceiver.log` retain those assertions. The synthetic service
uses host networking only for this controller-backed variant; its public worker
registration enters the guest, while the controller configuration and bank do not.

To reuse an already sealed **synthetic qualification** point, append
`--reuse-point /private/sealed-point --captured-config /private/captured-config.toml`
to the command above. The matching configuration digest and every source member
are verified; the original source test is explicitly not counted as passed.
This avoids replaying the six source work wakes. For an independently selected
compatible integration, also pass `--provider-revision EXACT_COMMIT` with its
`--provider-source` checkout; the declared pin must match the clean source.
The default remains the documented compatibility revision. A capsule remains
bound to the captured native provider's complete source fingerprint: selecting
a different provider revision can correctly refuse restore. Generate and qualify
a new source at that revision; never rewrite an older capsule or relax its fence.
The parent wait covers the synthetic fixture's original phase plus cleanup;
capture deadlines and worker phase checks remain unchanged. All receiver work still runs;
this option neither admits a real study cell nor turns an earlier source failure
into a pass. Output must remain fresh, physical and outside every source checkout.

Check `result.json`, `source.log`, `ordinary.log` and `stride.log` in the private
output directory; failure is an error, including missing prerequisites, and a
skipped test cannot produce a passed receipt. A second attempt needs a new output
directory. Keep its controller config, native
stores, reports and logs private. Cleanup removes only containers owned by this
invocation. This reproduces mechanical continuation; the receipt remains
`research_point_qualified=false`. A real later research point, scientific policy
benefit and the packaged frontend restore journey still need separate acceptance.

An enabled checkpoint rendezvous returns an explicit failed quota readback to
its original caller without interrupting the writer or attempting capture.
The original failure and recovery requirements remain unchanged; delivery of
that error grants no work or settlement authority. Successful, foreign and
malformed readbacks retain the existing typed capture checks. This contains
optional capture failure; it does not repair the underlying quota error.

The qualifier exports only the source's digest-bound native configuration through
the existing Docker archive transport into a controller-owned private directory.
It works with a root source guest and ordinary controller/receivers, preserves
source permissions, and never exports the source authentication files.

An explicit **SForge phase-clock participant** preserves a declared Host file's
remaining duration alongside native opportunities. Add
`--phase-deadline-file /private/source-phase-deadline` to capture, and
`--phase-deadline-file /private/new-host-control/phase-deadline` to restore.
Supply the original physical integer deadline from the admitted source Host,
through an authorized controller read route. The destination file must be new,
outside the artifact and native bank, and installed at the Host's existing
phase-file route before starting its ordinary entry command.

Planning input no longer projects the Host's deadline, remaining time or
execution reserve to the model. The original task-planning packet is preserved;
no replacement Host runtime guidance is added to planning or execution wakes.
The Host still enforces its finite budget and retains clocks in runtime state
and receipts. Task-defined time constraints and evaluation cooldowns are unchanged.

This is a planning prompt default change. Execution inputs, scoring, acceptance,
TurnEnvelope and terminal authority are unchanged. Old session archives may still
contain historical deadline messages: this change does not rewrite or neutralize
those messages. An authorized continuation must address stale context at its
restore boundary, and a terminal Goal still requires explicit resume. This PR
prevents new planning-clock injection; it does not qualify old-archive recovery
or claim improved model behavior or scores.

Only the explicit `loopx-planned` benchmark worker adds research-planning
guidance around the unchanged product planning packet: check decision-critical
assumptions, compare with and retain the strongest available reference, and keep
an informative negative experiment separate from Goal completion. This is a
provider planning treatment, not new default Core/CLI guidance or a checkpoint
requirement. Ordinary Goal entry, seeded work and official native workers do not
use this wrapper. Pin this provider revision when comparing runs; the guidance
does not establish model adoption, initial-quality parity or long-horizon gain.

仅显式 `loopx-planned` benchmark worker 在未修改的产品规划 packet 外附加研究
指引：核对关键假设、对照并保留最强起点、区分有信息量的负实验与 Goal 完成。
这是 provider 规划处理，不是 Core/CLI 默认指引或 checkpoint 要求。普通 Goal、
预置任务和 official 原生 worker 不使用此包装；对比运行须固定 provider revision。
这些指引不证明模型采用、首次质量持平或长程收益。

The sealed deadline and native capture timestamp determine the remainder; the
receiver recomputes it. A point with two hours remaining keeps that duration
after an eight-hour offline gap. It does not reuse the old absolute epoch or
initialize another configured twelve-hour trial. Native admission still owns
consumed rounds and remaining cooldown. Host time begins immediately before
native activation, including its transport time; the integer phase format can
round the retained allowance down by less than one second. The existing worker's
startup/settlement reserve remains in force.

A native-only point or visited native/Host capture attempt cannot acquire a later Host clock. A capsule
with this participant requires the matching explicit restore option; omitting it
cannot discard its allowance. Existing/aliased files, source movement, changed
sealed bytes and exhausted work allowance refuse activation. An uncertain native
response leaves the reserved phase file empty and unusable by the ordinary Host
entry, preventing a fresh-budget fallback. Before native activation, the adapter
syncs a private sibling `<phase-file>.restore.json` containing the exact attempt
binding and destination deadline, without tokens. Explicit `--recover` uses that
original deadline: recovery delay consumes the remaining allowance, and expiry
refuses continuation. Result/phase locks prevent concurrent controller recovery;
partial records, aliases, changed identities and missing records fail closed.
Do not edit, copy independently or replace the attempt files to retry. The record
is local provider state, not part of the portable capsule or a new authorization
source. This recovery is explicit, with no automatic replay or new run. Omit the
option on **new captures** to retain native-only transport. Full Host source attribution, source/native clock alignment and the
joint session/research/effect barrier remain qualification requirements.

This is an independently usable **native participant transport**, still partial
relative to full research continuation. It does not join the worker's replan
barrier or restore Codex/LoopX authority. Its optional declared phase clock does
not qualify full research-state adoption or a same-point scientific comparison.
Never put its controller configuration in `--checkpoint-request` or a worker
payload. Omit both these controller operations and the native `--checkpoints`
flag to disable this transport on a fresh judge; preserved capsules stay inert.
Native HTTP tests cover lost replies, exact readback, unavailable authority and
invalid local recovery state. The explicitly enabled Docker qualification also
loses a reply after real native commit, recovers the original deadline, runs the
ordinary SForge phase-entry shell in Linux, and produces and grades a synthetic
candidate without refilling rounds/cooldown. It excludes model inference and
does not establish full research continuation. Run these checks with:

```sh
python -m pytest -q benchmark/tests/test_checkpoint_controller.py
LOOPX_EDGEBENCH_DOCKER_SMOKE=1 python -m pytest -q \
  benchmark/tests/test_checkpoint_controller.py::test_real_docker_controller_export_import_and_next_grade
```

新的 controller transport 使用上述 PR #23 固定源及 native `--checkpoints`，
不复用旧只读接口来伪装接续。admin 配置和 bank 必须由 solver 权限范围外的
controller 保管，不能放进 worker；0600 不能证明隔离。原生 owner 保留历史、
已用轮次和剩余冷却，生成新注册；完整研究接续仍需联合会话、LoopX 新权限、
原点剩余总预算及 replan 前决策边界。此切片不启动研究、不证明收益，也不改历史源。

Docker 原始归档在显式导出目标的文件系统中暂存，再解包到该私有 staging 目录，
不再依赖环境默认的临时卷。目标容量仍须覆盖原始传输、已解包参与者及封存副本；
改变目标位置不会减少这些字节或延长捕获截止时间。目标卷空间不足仍明确失败、
执行现有 reader/barrier 清理且不发布外层产物，不自动换卷重试或用稍后状态替代原点。

这是研究 checkpoint 的开发前置切片。EdgeBench CLI 的显式
`--checkpoint-request` 会把 host JSON 原文安全放入 heartbeat worker，并在
runtime receipt 绑定摘要；programmatic adapter 也可指定已放入 guest 的请求
路径。省略选项即关闭，Official/native-goal 不接受。产物
验证和隔离物化不授予执行权，不代表研究点已资格化。请求中的 metadata 只是
证据，不能代替会话、效果、状态采用及评测机会的联合验收。
已放入 guest 的声明文件丢失、权限失败或损坏只记录捕获失败，原工作仍按
完整的原始 quota guard 继续；准入 owner 不可用仍停止。首次 replan 捕获
失败后，修复声明不得把后来的状态补记成同一原点。评测参与者使用上文明确的
公开 SForge fork/pin 和 Python 3.12；普通 upstream 安装缺少该 seam 时明确
拒绝。Codex 原生会话的真实分页/compaction 与隔离 fork 须单独验证。
中途原生捕获将实际 TurnEnvelope 开关绑定到封存的 interrupted guard；
关闭开关的普通 guard 可以捕获，恢复时模式不匹配仍拒绝。旧产物缺少该字段
时保留其原有开启语义；这只是历史校验兼容，不会为当前运行启用 TurnEnvelope。
评测机会及 incumbent 参与者也分配未占用的 ID，并在封存的
`point_metadata.evaluation_opportunity` 中记录实际映射；无 incumbent 时对应值为 null。
文件 witness 校验按该映射定位，不会把同名调用者文件误当原生候选。
自动原生存储参与者分配未占用的 artifact ID，保留调用者原 ID；封存的
`native_session.store_members` 绑定这些 ID 与既有允许的原生文件名。恢复仍
支持旧固定 ID，拒绝重复或未声明的映射，并保留源历史一致性校验。

显式 `"loopx_state": true` 复用现有 state-backup inventory，捕获单 Goal 试验的
注册状态、项目研究状态、runtime journal 和 registry；File/SQLite authority
物化后仍是惰性历史。缺少来源、共享注册表、路径别名或重叠声明会拒绝，捕获
不授予新的租约、配额、效果或执行权，不能替代完整接续验收。

Host 预检已提交 fresh replan，但尚待 Agent 选择工作时，仍可在模型决策前捕获。
类型化 owner 核对原 Goal、已注册 Agent、Turn 和语义义务；不会伪造尚未形成的
settlement identity，也不授予选择前的交付权限。源边界仍须来自上轮已结算的成功工作；
已选 guard 的原结算身份检查保持。

恢复会话的两处 hook 仍须先有引用源 checkpoint 的当前 Goal 已接受语义 replan，
以及新的已完成原生接续。显式 `"native_session": true` 时，预检核对绑定的
session/workspace，并仅读取最新 Turn 的身份/状态、不加载 items。此临时 cursor
只准入一次捕获尝试，不证明完整历史；实际捕获的两次完整原生观测均须在封存前
核验原历史前缀及该已完成接续。checkpoint/session 变化、cursor 缺失或前缀变化
都拒绝捕获。准备验收及不捕获 native 的声明保持完整历史预检；canonical review、
首边界去重、当前权限和默认关闭路径保持原规则。

运行中出现的 fresh replan 可通过显式交接捕获。完整捕获需同时声明
`controller_handoff`、`native_session`、`loopx_state`、`evaluation_opportunity`
为 true，使用 `fresh_replan`，并在 host CLI 提供 `--checkpoint-controller-file`。
controller 配置及 admin 权限不进入 solver；backend 绑定真实 container 与原生注册。
worker 保留原 guard，controller 暂停全部容器写入者，核对仍有效的请求和准备时
文件 witness，再封存原生评测机会、incumbent 及剩余预算。源变化、过期或失去
交接会拒绝；可恢复的捕获失败不取消原研究，也不得改用稍后的状态。解除暂停后，
源会话继续使用原 packet 和 deadline。
原生交接先接收并读取一条有界请求，再校验捕获根目录的物理路径。目录缺失或为
符号链接时关闭该连接，原来源可纠正路径后经同一监听器继续；拒绝不捕获、不释放
旧 packet，不延长原 wake 截止时间，也不创建或跟随外来根目录。
连接尚未接收时的 Host 故障仍是执行失败，不是一次 packet 拒绝，不进入保留
来源的请求重试循环。
显式开启的两处完整 controller 捕获 hook 共用 90 秒窗口（原为 30 秒），覆盖完整原生/研究上下文
准备、一致性复核与 live-source 捕获，并受原试验 phase 截止时间约束。controller
沿用同一个绝对截止时间；解除暂停后，只能在原 phase 内再用至多 30 秒封存静态副本。
这不增加试验预算、冷却或机会，不改变完整性及首次尝试规则；丢失的点仍不能补记，
直接 controller 命令仍受调用者给定的 timeout 约束，其他捕获声明保持 30 秒窗口。
未显式开启时仍不捕获。
自动 supervisor 的准备截止时间仍约束暂停、文件导出及原生捕获。成功解除暂停后，
controller 只对自己持有的静态导出副本继续校验、封存，使用已捕获的原 Host phase
截止时间；该截止时间同时传给类型化产物 owner，过期不能发布。源可以继续工作，
稍后产生的文件不会进入旧点；不会增加预算、冷却或评测机会。直接调用 controller
默认仍以显式 timeout 约束整项操作。关闭 handoff/controller 配置即停止自动路径。
上文的公开复现命令使用固定 provider `071a884`、Python 3.12、显式 Linux amd64
Codex/Node 及调用者准备的 Docker 镜像。非 root controller 从六个真实工作 wake
形成模型决策前捕获，保留完整声明的目录及只读 Git 对象；普通与 stride receiver
以非 root 身份在两个全新 guest 按封存源路径定位任务目录，由类型化 owner 物化；
原注册表/状态只是历史，新的外置 registry/runtime 不授予原 Goal 权限。
分别验证新权限准入、完整历史、剩余预算/冷却及实际接续。模型响应和评分为合成
fixture，原生 CLI、worker、supervisor、SQLite 和 capsule transport 使用真实路径。
结果及三份日志只存私有输出，失败或缺少前置条件明确报错，重试须使用新目录；
产物仍是 `research_point_qualified=false`。真实中后期研究点、策略净收益及安装后的
frontend restore 旅程仍须另行验收。新运行省略
controller 文件及 handoff 开关即可关闭；移除 checkpoint 请求关闭全部捕获，
已有产物保持惰性。

### Explicit warm worker composition (qualification in progress)

The native heartbeat launcher accepts a complete point through
`--restore-checkpoint POINT --restore-sha256 SHA --restore-config CONFIG
--restore-goal NEW_GOAL --restore-agent NEW_AGENT --restore-source-todo SOURCE_TODO`.
All six inputs are required together. Keep the existing native
`--eval-interval 0`, full `--capture-before-replan` handoff and
`--checkpoint-controller-file` configuration. The native provider must expose
`RestoredRegistration`; an older provider fails instead of cold-registering.

This adapter composes existing artifact, Codex session, Todo-continuation and
native-judge owners. A captured parent directory may contain the native task
workspace: the receiver resolves exactly one physical task subtree from the
verified component and restores only that subtree. Captured siblings remain
inert unless separately selected as research data; absent, ambiguous or
noncanonical roots reject before the receiver workspace changes.

It preserves the captured workspace and effective Codex configuration, admits
a distinct native session and fresh Goal/Agent, and adopts
retained research as evidence on a current preparation Todo. Historical registry,
claims, leases and settlement records are not installed as current authority.
If the sealed context contains Explore events, adoption also seeds the fresh
Goal's ordinary Explore log. Nodes, findings, scoped counterexamples, topology,
original attribution and timestamps survive, with each event bound to the
checkpoint digest and source event through its opaque run reference. Operational
research-observation lineage stays in the archive; it is not destination
execution proof. Recovery accepts only the same immutable initial prefix, may
finish a partial prefix, and never reapplies old statuses after newer evidence.
Malformed, foreign or interleaved destination history rejects before model work.
Legacy context-only points keep their original continuation behavior.
Fresh credentials come from the ordinary runner credential source. The native
activation's absolute deadline includes receiver installation and preparation;
restarting the entry cannot grant another full budget or submission allowance.

To continue with a different model or reasoning effort, retain the exact sealed
source settings in `--restore-config` and explicitly pass
`--restore-model-config DESTINATION_CONFIG`. Only the top-level `model` and
`model_reasoning_effort` may differ; provider, tools, permissions and other
settings must remain semantically identical. The destination model/effort must
also match `--model`/`--effort`. The native session participant records both
configuration digests, verifies the original session profile and full retained
history, then binds the new profile to a distinct destination session. Retry
uses that same source/destination intent; it cannot switch an already running
receiver. Without the option, exact-configuration admission is unchanged.
The host controller exposes the same explicit transition as
`--destination-model-config`; this remains a CLI/Host journey, with frontend
restore support still open. Changing the model changes the experiment treatment:
do not claim a matched mechanism comparison solely from shared checkpoint data.
It grants no extra native submissions, cooldown relief, permission or time.

切换模型或思考深度时，`--restore-config` 仍传封存源配置，另显式传入
`--restore-model-config DESTINATION_CONFIG`。仅顶层 `model` 与
`model_reasoning_effort` 可变化，其余 provider、工具、权限及配置语义保持一致，
目标选择还须匹配 `--model`/`--effort`。接收端保留源配置摘要和完整历史校验，
在全新会话绑定目标配置；恢复重试不得换意图或改写已运行接收端。省略选项仍要求
配置完全一致，不增加评测机会、豁免冷却、权限或预算。宿主 CLI 对应选项为
`--destination-model-config`；frontend restore 旅程仍未交付。模型变化是新的实验因素，
共享 checkpoint 本身不能证明机制收益。

Warm verification hashes the complete checkpoint with a read window of up to
60 seconds, capped by the original execution deadline. It rejects a read that
finishes after that deadline. The launcher reuses this verified metadata before
native registration; subsequent participant checks still verify the retained
bytes. Workspace restoration uses the same bounded full-artifact read allowance.
Native conversation admission and fresh-replan revalidation allow up to 90 seconds
per read, capped by the original phase deadline, while preserving full-history,
cursor and identity checks. Core research-context adoption also gives complete
artifact and native-history reads up to 90 seconds each (previously the generic
10-second artifact RPC and 30-second native read defaults). Warm preparation
passes its original phase deadline through adoption; expiration rejects the next
write, and uncertain prior writes must replay their original operation. Ordinary
Core callers remain lease-governed and may supply an absolute adoption deadline.
These allowances grant no extra execution time and
do not establish sustained performance for growing checkpoints.

Concurrent receivers importing the same native point serialize bank publication
with a bounded per-point lock. Each receiver still obtains its own native run and
credentials; an existing bank member must match the sealed bytes. Conflicting or
partial members fail closed. A lock timeout leaves the attempt for inspection;
it does not authorize reusing a failed trial directory.

Both comparison arms first settle actual preparation at cadence one and consume
the retained decision through a current semantic replan. The existing joint
continuation gate must accept that replan before the declared normal cadence
returns. Preparation and context-reading cost belong in the comparison. Omit all
restore flags for the unchanged cold entry; never reuse an ambiguous trial
folder as a new attempt.

Warm preparation retains the original evidence-reading task on its existing Todo;
it does not add a prompt paragraph interpreting historical deadlines. Current
Goal/guard authority, task time constraints, native cooldowns and user stops
remain binding. Preparation reads the current claimed Todo and actual quota
result before reporting `preparation_actionable=true` in the existing `checkpoint-context.json`
receipt. Closed or unavailable work and a deadline that expires during readback
fail before model execution; an old ready plan is not current admission. This
readback neither grants execution nor qualifies subsequent research or recapture.
It applies only to the existing explicitly admitted complete-checkpoint path;
ordinary terminal archives are not converted into complete checkpoints.

The receiver brackets the guard with the exact preparation Todo's canonical
authority revision. Completion, transfer or another canonical edit during that
observation rejects readiness; a later required review cannot revive an earlier
open snapshot. The existing context receipt includes `preparation_readback` with
that revision, the selected Todo and current replan identity, so an operator can
inspect what was actually observed instead of relying on a readiness boolean.
This is a bounded observation, not an execution lease; the worker must still use
fresh ordinary admission. Readiness is validated before changing cadence, so a
rejected preparation does not change the Goal's cadence. An ambiguous cadence
write must be inspected, not blindly rolled back across an owed current review.
Creation retries read the original `checkpoint-preparation` receipt and replay
its exact accepted task through the canonical create owner, including receivers
created with the earlier longer task. They never mint a second operation or Todo.
This covers partial creation, not arbitrary retries after context adoption or
worker execution. Such uncertain operations retain their original receipt,
revision and lease recovery boundary; actual model/research adoption is separate.

Warm 准备保留既有 Todo 中原本的证据读取任务，不再增加解释历史截止时间的提示段落。
当前 Goal、guard、题目时限、原生冷却、用户门槛及停止权限仍有效。
准备步骤须读回当前 claimed Todo 和实际 quota 结果，才在既有
`checkpoint-context.json` 回执中报告 `preparation_actionable=true`；工作已闭合、不可用或读回
期间期限耗尽时，在模型执行前失败。旧 ready 计划不等于当前准入；此读回不授予
执行权，也不认证后续研究或再次捕获，仅作用于显式准入的完整检查点路径。
普通终态资料归档不会因此被升级为完整检查点。

接收端用准确准备 Todo 的规范权威修订号夹住 guard 读取；期间完成、转交或其他规范
修改都会拒绝 ready，后来的 required review 不能复活较早的 open 快照。既有上下文
回执的 `preparation_readback` 返回该修订、当前选中 Todo 与 replan 身份，便于检查
实际观察依据；它不是执行租约，worker 仍须重新走普通准入。先验证准备资格，再改
cadence，因此拒绝准备不会改变 Goal 的 cadence；不确定的配置写入须检查，不能盲目
回滚而抹掉欠下的当前复盘。创建重试读回原 `checkpoint-preparation` 回执，通过规范
创建 owner 重放当时接受的准确任务（兼容此前较长任务），不创建第二个操作或 Todo。
此兼容范围是部分创建，不是上下文采用或 worker 执行后的任意重试；不确定的操作仍须
保留原回执、修订与租约的恢复边界，真实模型/研究采用也需单独验证。

To test a new decision from the same pre-replan state, use
`--restore-fresh-replan`. The point must contain a verified pending replan packet.
The first destination model call starts a fresh native conversation, before
preparation and the first current-Goal replan. It reads the original task,
retained research evidence, code and incumbent; no post-checkpoint decision or
old native message prefix is supplied. Preparation remains charged work, and
capture remains closed until the ordinary current review/settlement gate passes.
Omit the flag for retained-history continuation. Change treatments only in a new attempt; the
same checkpoint budget, opportunities and cooldown remain binding.

同一个决策前 checkpoint 的重新决策实验使用 `--restore-fresh-replan`：首次模型调用
就在 fresh 原生会话中准备并完成当前目标的 replan，读取原任务、研究证据、代码和最佳
候选，不预置 checkpoint 之后的决定或旧原生消息前缀。准备成本仍计入原预算，后续捕获
仍须通过当前复盘和结算校验。省略该选项保持历史接续。
必须用新 attempt 改变处理方式，不能重置预算、机会或冷却。

Warm continuation preserves the admitted native conversation across the current
replan and subsequent work. The post-replan context-reset option
`--restore-fresh-after-replan` has been removed; supplying it is a CLI error
before trial creation or native registration. The joined qualification retains full controller-backed
recapture and actual work in another isolated receiver, using the complete
retained native history. Controller configuration and bank stay outside guest
mounts. Scripted model and grader responses qualify the mechanical chain;
research benefit and sustained growing-point reliability remain unproven.
This is a benchmark operator transport; the generic packaged restore UI remains
an open acceptance boundary.

暖恢复在当前 replan 及后续工作中保留已准入的原生会话。复盘后清空上下文的选项
已删除；旧参数会在创建 trial 或原生注册前作为未知参数拒绝。联合资格仍覆盖
controller 完整再捕获及另一隔离接收端的实际工作，并使用完整保留的原生历史。
controller 配置和 bank 不进入 guest 挂载。脚本模型与 grader 只验证机械链路，
研究收益及 checkpoint 持续增大时的可靠性仍未证明。该入口属于 benchmark
操作者传输，通用打包 restore UI 仍须另验收。

Research files outside the task workspace need explicit capture **and** explicit
receiver selection. Add each required directory or file to the source checkpoint
request's existing `components` array before capture, for example
`{"id": "research_trials", "source_path": "/tmp/research-trials"}`. Create that
owned directory before the first capture and keep durable experiment inputs,
candidates and reports there (or inside the task workspace). Do not capture all
of `/tmp`, credentials, or unrelated files. The archive cannot discover a missing
dependency from a conversation reference.

At continuation, inspect the sealed `worker_file_snapshot.components` paths and
pass `--restore-research-component research_trials`; repeat the option for each
selected component. Only recorded files below `/tmp/` or `/home/workspace/`,
separate from the full task workspace, are eligible. The receiver restores the
original guest path, requires an absent target and an existing physical parent,
and verifies copied bytes/modes through the artifact owner before model work.
Unknown/duplicate IDs, overlapping roots, occupied targets and symlink parents
reject. Selection does not grant control-plane authority; caller-declared files
are private artifacts and are not automatically filtered for credentials.

Omitting the option preserves workspace-only restoration; unselected components
remain inert in the archive. A point that never captured a dependency cannot be
repaired by adding a later copy. Qualify the actual experiment's required files
in a receiver before describing its research state as complete. An interrupted
copy leaves a failed attempt to inspect; do not reuse that receiver or overwrite
its workspace backup. Disable this opt-in by omitting the selection on a new
attempt; preserve previous evidence. This provider CLI transport does not close
the generic checkpoint frontend or automatic dependency-discovery gaps.

The default continuation retains the captured remaining time. For a separately
authorized longer study, explicitly add `--restore-phase-extension-seconds N`
and set `--timeout` large enough for the extended continuation. For example,
changing a source's total budget from 18h to 36h adds `64800` seconds; it does
not grant 36 fresh hours after the checkpoint. Both arms need the same declared
extension. The controller-only `restore` command exposes the corresponding
`--phase-extension-seconds N` option. This is an operator-supplied budget, not
permission to spend: record the authorization and new study conditions before
admission. The first activation binds the extension and absolute deadline;
recovery requires the same value and never refills elapsed time. Native history,
accepted round counts, cooldown and scoring remain unchanged. Omit the option
for the original-budget behavior; a changed budget requires a fresh destination,
not a rewrite of a running study or its original results.

The ordinary installed worker has passed a synthetic joined qualification:
native session continuation, current preparation and accepted replan, restored
normal cadence, candidate work and released native feedback under the original
remaining allowance. It reused one immutable captured prefix and preserved its
source. Native app-server-generated skills are preserved outside discovery before
linking the current installed skills; ordinary skill identity checks still apply.
This used real Codex processes and native HTTP with deterministic local inference
and synthetic grading. It does **not** qualify scientific research utility,
matched strategy results, or the frontend restore journey. Actual task/profile
admission and measured continuation outcomes remain required.
