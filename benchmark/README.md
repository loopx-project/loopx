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

## Current work

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

- The retired benchmark implementation, legacy abstractions, and standalone
  dated research packets belong under
  [`deprecate/benchmark-legacy/`](../deprecate/benchmark-legacy/README.md).
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

`benchmark.runtime.native_checkpoint.fork_native_checkpoint` verifies a unit and
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
python -m benchmark.runtime.native_checkpoint \
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

After separately admitting the checkpoint's native session, an operator can
adopt this retained context on an **already claimed and leased destination Todo**
whose note is empty:

```sh
python -m benchmark.runtime.research_checkpoint \
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
Check `result.json`, `source.log`, `ordinary.log` and `stride.log` in the private
output directory; failure is an error, including missing prerequisites, and a
skipped test cannot produce a passed receipt. A second attempt needs a new output
directory. Keep its controller config, native
stores, reports and logs private. Cleanup removes only containers owned by this
invocation. This reproduces mechanical continuation; the receipt remains
`research_point_qualified=false`. A real later research point, scientific policy
benefit and the packaged frontend restore journey still need separate acceptance.

An explicit **SForge phase-clock participant** preserves a declared Host file's
remaining duration alongside native opportunities. Add
`--phase-deadline-file /private/source-phase-deadline` to capture, and
`--phase-deadline-file /private/new-host-control/phase-deadline` to restore.
Supply the original physical integer deadline from the admitted source Host,
through an authorized controller read route. The destination file must be new,
outside the artifact and native bank, and installed at the Host's existing
phase-file route before starting its ordinary entry command.

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

运行中出现的 fresh replan 可通过显式交接捕获。完整捕获需同时声明
`controller_handoff`、`native_session`、`loopx_state`、`evaluation_opportunity`
为 true，使用 `fresh_replan`，并在 host CLI 提供 `--checkpoint-controller-file`。
controller 配置及 admin 权限不进入 solver；backend 绑定真实 container 与原生注册。
worker 保留原 guard，controller 暂停全部容器写入者，核对仍有效的请求和准备时
文件 witness，再封存原生评测机会、incumbent 及剩余预算。源变化、过期或失去
交接会拒绝；可恢复的捕获失败不取消原研究，也不得改用稍后的状态。解除暂停后，
源会话继续使用原 packet 和 deadline。
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
