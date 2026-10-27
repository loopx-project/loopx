# RFC: LoopX × LayerFS — Engineering Exploration and Evaluation (v0)

- **RFC status:** Accepted (effective on merge; this change is a proposal)
- **Supersedes / closes:** none
- **Delivery maturity:** Proposal; no integration, experiment result or product promotion is claimed.
- **Authors / owners:** LoopX exploration and benchmark maintainers; LayerFS maintainers are invited to review the provider boundary, not assigned work.
- **Created:** 2026-10-26
- **Last normative revision:** 2026-10-26
- **Implementation baseline:** LoopX `15803cd7dcc2fc517049809bbe58998f04f5acac`; LayerFS source `ad78914e36b258b360daf13aec164b5655814689`.
- **Tracking:** [LoopX #7406](https://github.com/loopx-project/loopx/issues/7406) · [LayerFS #461](https://github.com/Ephemeral-AI-Lab/layerfs/issues/461).
- **Language mirror:** [中文版](layerfs-engineering-exploration-evaluation-v0.zh-CN.md)
- **Related contracts:** [Explore](research-exploration-control-plane-v0.md), [research-state branching](hierarchical-agent-stride-control-v0.md#77-research-state-branching-and-matched-continuation), [measurement](long-horizon-harness-benchmark-research-program-v0.md#6-measurement-model), [overall roadmap](loopx-overall-roadmap-v0.md).

## Document map and maintenance contract

Sections 1–3 and 5–11 define the proposed experiment and integration boundaries;
section 4 records source observations, not live qualification. Section 12 lists
decisions needed before execution. Appendix A records provenance. Keep the
Chinese mirror synchronized. Merge accepts a design basis, not a runtime
default, benchmark result, spending commitment or upstream implementation promise.
Future measured checkpoints belong in the existing [RFC ledger](ledger/README.md).

## 1. Decision summary

Evaluate LayerFS as an **optional execution-workspace provider for multi-agent
engineering exploration**. LoopX keeps questions, hypotheses, evidence, work
ownership, budget and continuation; LayerFS keeps branchable filesystem state;
the host keeps model sessions and tool execution. Git remains the reviewed
source-delivery boundary. No new generic capability or scheduler is proposed.

The first experiment asks whether several agents can investigate a hard repair,
reuse a prepared environment, reject an approach, hand off a promising checkpoint
and deliver an independently verified patch with less total time or cost.
It does not equate more branches, cheaper mounts or more checkpoints with progress.

Start with one host and three workers, using the same model and aggregate budget
as a warm Git-worktree control. Qualify workspace correctness before model runs.
Keep native/passive/governed controls for attributing joint benefit, and report
an absence of benefit as a useful result. Integration remains default-off.

## 2. Problem and hypotheses

In a multi-package repair, alternative explanations often require different
dependency versions, generated code, build outputs and test state. Git preserves
tracked source well, while environment reconstruction and transferring useful
untracked state can dominate short branches. Copying complete environments can
consume disk; sharing writable caches can contaminate comparisons. Conversely,
long-lived warm worktrees already avoid much repeated setup.

| Hypothesis | Observable evidence | Falsifier or limit |
| --- | --- | --- |
| H1: branching a prepared full environment reduces exploration overhead | Lower init-amortized fork→test-ready, capture and handoff cost at equal restored-state fidelity | Mount is fast but commit, I/O, cleanup or cold import cancels the saving |
| H2: structured evidence plus cheap environment branches improves engineering outcomes | More independently accepted repairs at fixed total budget, or lower complete cost at comparable accepted outcomes | More attempts or attractive history with unchanged/worse outcomes |
| H3: a receiver can reuse a useful candidate without repeating setup or investigation | Receiver verifies the exact candidate, consumes a relevant finding and performs the next accepted work | A read receipt, filesystem fork or copied summary without adopted work |

The likely fit is frequent branching with expensive, reusable environment state.
Read-only code search, a few persistent checkouts, large sequential I/O, or
workloads dominated entirely by model thinking may gain little or regress.
Filesystem deduplication does not deduplicate model reasoning or context tokens.

## 3. Scope and non-goals

The slice covers public engineering fixtures, isolated disposable workspaces,
candidate/result lineage, one end-to-end demo and a reproducible comparative
study. It maps to roadmap **S4/S8/S11**, with existing S3/S7 authority and budget
constraints; it is not a replacement for R2/R3 team qualification.

Excluded: a replacement Goal store, global memory, a new agent framework,
production durability, untrusted multi-tenant isolation, cross-machine shared
Store writes, automatic Git publication, full process/VM snapshots, autonomous
MCTS policy, and promotion of LayerFS 2.0 roadmap items as current support.

## 4. Audited current-system contract

### LoopX

- [`explore`](../../../loopx/capabilities/explore/README.md) already owns
  node-linked findings, candidate/input identities, bounded measurement feedback
  and execution observations. A measurement or observation is producer evidence,
  not proof of benefit. Reuse its TypeScript decision owners.
- [Stride §7.7](hierarchical-agent-stride-control-v0.md#77-research-state-branching-and-matched-continuation)
  defines a complete research point across conversation, evidence, artifacts and
  evaluation opportunities. The existing [capture adapter](../../../benchmark/runtime/checkpoint.py)
  and [artifact owner](../../../loopx/control_plane/runtime/checkpoint_artifacts.ts)
  do not already accept arbitrary LayerFS references. The current file-capture
  path rejects symlinks; a pnpm tree cannot simply be passed through that path.
- The [DSH plugin](../../../packages/dsh-loopx-plugin/README.md) supplies native
  binding, continuation and Goal readback. It is a possible composition host,
  not an existing LayerFS integration.

### LayerFS

The following facts refer to the pinned source above; release 1.1.0 and moving
web documentation are not interchangeable with that commit. Source evidence is
listed in Appendix A.

| Mechanism | Integration consequence |
| --- | --- |
| SQLite content-addressed Store; fork shares committed objects; Workspace mounts a writable view | Candidate branches can retain source, dependencies and generated outputs without full payload copies; initialization and later writes still cost work |
| `@layerfs/agent`, `@layerfs/backend`, `@layerfs/runtime` split | Use public APIs at one exact source version; do not build against `@layerfs/agent/internal` |
| File mutations commit; `workspace.exec`, `workspace.commit`, `workspace.unmount` are separate calls | The integration must implement the per-tool lifecycle; a Bash command is not automatically a durable checkpoint |
| Direct Store open requires one Linux kernel and local ext4/xfs/btrfs; macOS/Windows have a worker route | First qualify Linux or one Linux VM; macOS worker setup must be tested explicitly, including upstream [#456](https://github.com/Ephemeral-AI-Lab/layerfs/issues/456) |
| Disposable profile, WAL with synchronous OFF; no crash/power-loss durability claim | Keep authoritative LoopX state and an independent source/result copy outside the Store; do not advertise backup or compare fsync speed as equal durability |
| Shared publication lane; conflicts, `.lww`, bounded diagnostics and explicit unknown outcomes | Measure contention and refusals; protect source, lockfiles and evaluator inputs from last-writer-wins; never turn a lost reply into an automatic retry |
| Append-only history; no branch retirement/unreachable-history GC ([#411](https://github.com/Ephemeral-AI-Lab/layerfs/issues/411)) | Discarding a Workspace does not reclaim committed branch history; account for all failed candidates and bound each experiment's Store lifetime |
| Commands can write outside the Workspace; process memory, network effects and external services are not captured | Use a declared execution envelope and disposable external fixtures; a filesystem rewind is not an experiment rewind |

The published v1.0.0 [dedup study](https://layerfs.ai/benchmarks/dsh-dedup/)
reports 157 historical trees retained in about 100 MB versus about 1,995 MB for
Btrfs+zstd under its rewrite workload. Its [fio study](https://layerfs.ai/benchmarks/fio/)
also reports sequential-write/read and random-write regressions. These are
upstream measurements at `e5c7aaae05`, not reproduced here, not 1.1.0 results,
and not evidence of engineering-task success or lower total agent cost.

## 5. Proposed architecture and demo

### Ownership and placement

| Owner | Responsibility |
| --- | --- |
| LoopX existing Explore / Todo / quota owners | Hypothesis and finding lineage, claims/leases, admitted work, result adoption, remaining budget and replan |
| Host/runtime | Native sessions, model/tool routing, cancellation, settled capture boundary and execution isolation |
| Optional LayerFS adapter | Map a candidate to exact Store/project/Commit/root and image identities; fork, mount, inspect, commit and return evidence |
| Existing benchmark toolkit / task evaluator | Frozen workload, independent oracle, complete cost collection and comparative analysis |
| Existing Git/review workflow | Export the selected source patch, validate in a clean checkout and propose it for review |

Placement rationale: `capability_id: explore`, current built-in provider
`loopx-core`; LayerFS is a candidate optional provider implementation, initially
experiment-owned under the benchmark workspace. A production distribution would
follow the [extension placement rules](../../reference/extensions.md#placement-decision-for-agents)
only after a real caller qualifies. Do not register a provider id, new shared
enum or package solely for this design. New provider-neutral decisions belong
in the existing TypeScript owner; specialized capture remains an adapter.

Keep two graphs: the Explore graph explains questions and findings; the LayerFS
graph identifies filesystem ancestry. They are linked, not one-to-one. Several
tool commits can implement one experiment, and one filesystem checkpoint can
support several findings. A LayerFS Commit is not a Git commit or an accepted
LoopX outcome.

The experiment manifest binds existing task/experiment/result ids to provider
source revision, Store/project identity, parent and result Commit/root, runtime
image digest, environment manifest, evaluator revision and artifact digests.
Mutable branch names are display handles, never sufficient evidence identities.
Keep raw manifests and traces outside model packets; attach bounded summaries
and opaque evidence refs using existing Explore fields. This is a local study
record, not a silently introduced shared runtime schema.

### A complete small demo

Use the public failure in [LoopX #6869](https://github.com/loopx-project/loopx/issues/6869)
as the first **candidate fixture**, starting at source
`907ede6e1faff1fab0f2f765dde4358c69c7b7ff`: an unreadable spend artifact can
silently reduce accounted spend. Source inspection confirms the missing-event
path; the first milestone must reproduce the real CLI failure before admitting
this fixture. [PR #6875](https://github.com/loopx-project/loopx/pull/6875) is existing
repair work, not an accepted oracle or work to duplicate in this proposal.
The demo is an isolated historical repair exercise, not a claim on that issue.

1. Prepare a standalone clone at the pinned base, with locked dependencies,
   built TypeScript runtime and synthetic File/SQLite task data. The solver's
   source repository is distinct from the installed LoopX controller and its
   runtime state. Remove credentials and host-local data before import. A Git
   worktree's `.git` pointer is not a portable complete repository.
2. Give three workers the same task and declared tool/feedback envelope. They
   choose and record hypotheses, may share findings and may reject or continue
   approaches. Do not script their conclusions or force one branch to win.
3. Start branches from an exact prepared checkpoint. A deterministic conformance
   run first exercises competing edits, a negative result and a receiver handoff;
   a separate model run determines whether those mechanisms are actually used.
4. Record each probe's code/environment identity, test result and cost. The
   independent evaluator checks that missing, malformed and truncated spend
   evidence never silently increases authorization; valid spend/void behavior,
   history and admission stay consistent, including at time-window boundaries.
   Do not use blanket permanent throttling or one patch's expected output as
   the correctness oracle.
5. A receiver forks a selected immutable candidate, verifies its manifest,
   reads relevant findings and performs the next test or repair. Then export
   only the selected source patch into a fresh checkout and rerun the evaluator
   with clean dependencies. An apparently passing cache or branch is insufficient.
6. Present the resulting patch, failed alternatives, receiver's actual work,
   held-out result and full cost breakdown. A recorded UI replay is labeled as
   such; scripted conformance is not autonomous model adoption.

This public historical task may be contaminated by prior model exposure. It
qualifies the demo path only. The study must add previously unseen, independently
prepared tasks in at least a TypeScript monorepo and a Rust workspace, including
dependency/generated-code changes and a low-branching negative-control stratum.
Fixtures retain real package graphs, lockfiles, symlinks, ignored dependencies,
build outputs and representative error logs. Artificial mutations are labeled;
repeated strings or empty files cannot substitute for those state shapes.

### Execution and failure transitions

For the first host, investigate co-installing the existing DSH plugins in a
disposable profile; pin DSH, both plugins and LayerFS together. Existing
co-installability, tool routing and session inheritance are **unverified**.
DSH availability does not authorize silently changing the study's host.

Before each filesystem command, the existing host admits the work and the
adapter binds its candidate. Direct file calls use the SDK; shell calls use
mount → exec → explicit commit/readback → clean unmount. Inspect nonzero exits
as experiment evidence, not successful fixes. Do not accidentally discard
useful failed-test state; an explicit discard is recorded when chosen. Same
Workspace operations remain sequential; workers own separate Workspaces.

After a conflict, preserve the original inputs and inspect every affected path,
including paginated/truncated diagnostics. Resolve on new working forks and
reevaluate. For merges, freeze input heads first; a passing source branch's test
does not cover a newly merged root. On a lost response, reconcile exact history
and Workspace status before any new operation. If success cannot be established,
keep the attempt unknown and stop that branch; no blind command replay or forced
commit. Cleanup never claims an unresolved operation succeeded.

For filesystem-only forks, start fresh sessions with the same declared context
in both arms. A later same-conversation continuation experiment must satisfy
Stride §7.7's host cursor, evidence, artifacts and opportunity barrier. LayerFS
can replace only the artifact participant after its provider contract qualifies;
it cannot make the current file-only capture accept symlinks or restore leases,
budgets, evaluator attempts or external side effects by copying their files.

## 6. Alternatives and comparisons

| Option | Why retain it |
| --- | --- |
| Warm Git worktrees + locked dependency cache | Primary practical control; allow realistic caching and preserve needed untracked outputs in its checkpoint path |
| Btrfs/reflink or overlay snapshot on a suitable Linux host | Strong alternative for cheap full environments; compare when available, disclose unavailable coverage |
| LayerFS workspace provider | Test shared complete-tree history, incremental capture and branch handoff, including FUSE and publication costs |
| Ordinary persistent single-worker checkout | Negative control when branching is rare; establishes whether parallel coordination itself helps |

Do not compare a prewarmed LayerFS tree only against cold package downloads.
Measure both cold setup and repeated warm reuse, each with equal reachable
state and retention requirements. Keep dependency caches immutable or isolated
per branch. Native snapshots can remain the better choice.

Start each attempt's wall clock before its environment preparation and include
terminal validation/export. Report one-time provider build/image preparation
separately and in a declared amortized total; do not hide it or charge it only
to the control. The steady-state view supplements the cold total, not replaces it.

## 7. Safety, privacy and compatibility

Run on disposable public fixtures in one Linux kernel, with fixed CPU/RAM/disk
limits and separately controlled network access. Do not import a developer's
working directory: LayerFS intentionally captures ignored files too. Keep
credentials, model histories, live LoopX state, evaluator internals, daemon
endpoints and Docker access outside agent-controlled trees. Use a fixed empty
`.lww` policy for the first study; no automatic newer-wins for lockfiles, source
or tests. A policy change makes a different experimental treatment.

Check executable modes, symlinks, hardlinks, absolute paths, native dependency
ABI and packages writing outside the root. Imported environments must be built
for the same guest OS/architecture/image; an arm64 macOS dependency tree is not
a Linux environment. LayerFS isolation is not evidence of tenant security.

Feature-off must keep existing execution and state unchanged. Adapter failure
is visible; a fallback starts a separately labeled attempt rather than silently
changing an arm. No model-visible instruction set can substitute for verifying
where tools actually read and write. LoopX control calls stay on their owning
host, outside the candidate tree being repaired.

## 8. Migration and rollback

No existing Store or Goal migration is proposed. Initialize a new study Store
from an independently preserved fixture. Keep selected source patches and
verification evidence outside it; test clean-checkout replay before declaring
success. Stop new admissions, reconcile in-flight operations, export qualified
results and close owned Workspaces/Sandboxes before removing the study's Store.
Never describe Workspace discard as historical GC. Disable the optional adapter
to return to the original workspace path. A source-format upgrade starts a new
study generation and is not an in-place repair of old evidence.

## 9. Evaluation and acceptance

### Separate substrate effects from agent effects

First replay the same predetermined filesystem/build/test operations on both
workspace providers, with 1/3/8 workers and branch counts 1/4/16 where resource
limits allow. Record excluded cells before running. Keep topology, retained
states and command semantics equal. This estimates substrate overhead only.

Then use this controlled matrix, with the same host/model/reasoning settings,
task split, test visibility, aggregate resource budgets and three-worker ceiling:

| Control policy | Warm worktrees (W) | LayerFS (F) |
| --- | --- | --- |
| Native host (N) | N-W | N-F |
| Passive LoopX (P) | P-W | P-F |
| Governed LoopX (G) | G-W | G-F |

The first bounded model pilot may run **G-W vs G-F**; it estimates workspace
effect under that LoopX policy, not LoopX's benefit or joint synergy. The full
matrix separates observation overhead, governance and workspace effects. Claim
an interaction only from a predeclared difference-of-differences on a common
outcome scale, with uncertainty; superiority of G-F alone is insufficient.

Freeze task ids/base commits, mutation generator and evaluator hashes, model
revision, SDK/image/plugin pins, branch/depth limits, feedback schedule, dollar/
token/compute/wall ceilings and missing-data rules before model evaluation.
Charge coordinator and all workers to **one aggregate budget**. Model-selected
branching may differ in the agent study but stays within that ceiling. Never
compensate one provider's overhead with extra time, worker slots or tokens.

Use paired tasks/seeds, interleaved arm order and at least five repetitions per
promoted comparison cell, following the existing research contract; five is not
a power justification. Select tasks and any sequential stop rule before seeing
comparative results. Reserve held-out tasks/evaluation from all solver-accessible
files and network routes. Treat development feedback, scheduled evaluation and
terminal grading as distinct opportunities. Retain setup failures, cancellation,
timeouts, missing treatment and invalid evaluation in the denominator with their
own classifications. Freeze a power/precision plan before making general claims.

### Metrics

Primary: independently accepted repair rate at fixed aggregate budget and
best valid retained result over time. For equivalent accepted outcomes, report
time and complete cost, with all attempts included. Estimate any break-even
branch count from measured cold setup and marginal reuse costs, not fork latency
alone. Secondary metrics explain the result:

- setup, dependency acquisition, mount/fork, tool execution, capture/commit,
  conflict repair, handoff, validation, export and cleanup wall/CPU time;
- uncached/cached input and output tokens, actual billed model cost, control and
  evaluator work, human interventions and measured attention time;
- physical allocated Store plus WAL, overlays, container scratch, caches,
  independent exports and peak disk/RSS; logical history bytes separately;
- contention, refused/unknown operations, recovery work, duplicated probes and
  evidence→decision→executed receiver work, without rewarding branch counts.

Use LayerFS traces/backend counters as diagnostics, plus an independent runner
clock, OS resource measurements and provider usage. Trace writes can be lost;
missing trace data is not zero cost. Follow the existing measurement contract:
packet bytes are not model tokens, dedup ratios are not total disk saved, and
unknown billing/attention prevents a complete-cost claim. Do not sum overlapping
wall intervals as elapsed duration; also report total worker compute time.

### Acceptance rows

| Claim | Required evidence | Pass boundary |
| --- | --- | --- |
| A1: real environment branch fidelity | Deterministic tree/content/mode/link checks and same tests after fresh remount, including ignored outputs and a symlink-rich package tree | Every selected checkpoint has matching declared state; metadata differences are explicitly normalized, not ignored wholesale |
| A2: isolation and conflict integrity | Parallel independent edits, same-file conflict, merge-root retest, external-path sentinel and no-op/read-only calls | No candidate contamination or silent overwrite; no fabricated Commit for unchanged state |
| A3: truthful failure/recovery | Nonzero tool exit, lost reply, interrupted Workspace, absent worker, disk cap and missing evidence | Unknown remains unknown; no duplicate execution, false success or automatic authority restore |
| A4: useful handoff and output | Receiver verifies candidate, uses a scoped finding, performs new work; exported patch passes independent clean-checkout evaluation | Demonstrates the complete scripted path; a separate model run must prove actual adoption |
| A5: causal efficiency evidence | Frozen matched cells, complete attempt accounting, paired uncertainty and native outcomes | Claim only the scope supported by the contrast; no promotion from a demo or microbenchmark |
| A6: usable optional journey | Packaged host discovers availability, performs authorized run, shows conflict/unavailable states, reads results and disables provider | Required before product promotion; CLI-only experiment is labeled partial |

Before the confirmatory study, preregister the primary contrast, minimum useful
effect and acceptable outcome-loss margin using pilot variance and operator
costs. These values cannot be chosen after inspecting the confirmatory result.
If the confidence interval spans material harm and benefit, keep the conclusion
inconclusive. Correctness/isolation failures block qualification regardless of
speed. Benefit restricted to storage is reported as storage benefit.

## 10. Operational and product contract

Use the existing Goal/Explore research history and DSH conversation for task
and outcome readback. LayerFS Work/History may show the filesystem lineage;
it cannot replace the explanation of why a candidate was kept. No new top-level
screen is required for the experiment. A shipped provider still needs existing
settings/capability readiness, authorized launch, unavailable/error recovery and
disable/readback coverage in App/CLI and any advertised Lark entrypoint.

Cap branch count, total Store growth, concurrent workers and command duration
at the experiment runner. Forecast retention from failed as well as successful
branches; cleanup only this study's resources. Show stale/unknown checkpoints
and unavailable observations. Do not feed raw logs or growing history into
every model turn; detail is fetched by immutable reference and measured as
actual consumed context. This RFC changes no runtime packet or compute quota.

## 11. Normative delivery plan

| Milestone | Entry | Exit evidence | Rollback / owner |
| --- | --- | --- | --- |
| M0: freeze the demo | This proposal and public fixture candidate | Reproduced base failure; reviewed independent oracle; pinned host/provider manifest and explicit bounded run budget | No live state change; LoopX experiment owner |
| M1: one reliable integration path | M0; isolated runtime; compatible SDK/native/image | A1–A4 deterministic conformance through real LayerFS, LoopX readback and clean exported patch; disabled path parity | Remove disposable profile/provider; LoopX adapter owner, upstream consulted on API gaps |
| M2: comparative demo and pilot | M1 | G-W/G-F paired pilot, failed alternatives, full cost coverage and an honest effect/limit report; preregistration for any larger study | Retain evidence; no default change; benchmark owner |
| M3: attribution and optional adoption decision | Adequate pilot evidence and predeclared study | Native/passive/governed matrix, held-out task strata, A5 and independently repeated results; A6 before any product claim | Keep provider experimental or reject it; existing maintainers decide |

One LoopX tracking issue owns this sequence; one LayerFS issue requests API and
evaluation feedback. Reuse upstream #411/#456 and roadmap
[#446](https://github.com/Ephemeral-AI-Lab/layerfs/issues/446) rather than filing
duplicate GC, worker or generic worktree-benchmark tasks. Portable substrate
measurements may contribute to `layerfs-benchmark`; LoopX owns governance and
task-outcome experiments. Upstream assent is useful but does not block local
fixture/oracle preparation with currently available interfaces.

## 12. Open decisions

| Decision / owner | Recommendation | Evidence required / due |
| --- | --- | --- |
| Host profile / LoopX host maintainer | DSH composition first; retain runtime-neutral design | Verify plugin versions, control-call routing, worker inheritance, cancellation and no double commit before M1 |
| Fixture and budget / experiment owner | Public #6869 demo; held-out TS/Rust task strata for effectiveness | Reproduce base, license/privacy review, oracle and cost caps before M0 exits |
| Provider API / LayerFS maintainer feedback | Public 1.1-era packages at one exact commit; no internal API or durability changes | Confirm worker settings, exact-root comparison, ambiguous-operation readback and cleanup scope before claiming provider support |
| Promotion / existing LoopX maintainers | Remain optional until replicated value and A6 | Predeclared practical-effect thresholds, power/precision and actual journey evidence before M3 closes |

## Appendix A: Evidence registry

Inspected 2026-10-26. Source inspection is not a reproduced benchmark or a live
integration. No LayerFS build, model experiment or runtime installation was run
for this RFC; a local Docker availability check only informs feasibility.

| Evidence | Finding / limitation |
| --- | --- |
| [LayerFS pinned README](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/README.md) | Released-source/API split, Disposable restriction and explicitly scoped v1.0 measurements |
| [Agent SDK guide](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/api/how-to-use/agent.md) and [exec implementation](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/api/agent/sdk/src/workspace/exec.js) | Public calls, explicit commit lifecycle, guest settings, streamed output and bounded read/merge diagnostics |
| [Runtime guide](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/api/how-to-use/runtime.md) | Host-owned initialization, macOS/Windows worker and resource cleanup; no proof of plugin coexistence |
| [Backend/trace guide](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/api/how-to-use/backend.md) | Root comparison limitations, storage accounting and lossy diagnostic trace |
| [Store architecture](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/core/docs/architecture/84-global-sqlite-store.md) | Local-kernel boundary, publication semantics and no unreachable-history GC |
| [DSH integration](https://github.com/Ephemeral-AI-Lab/dsh-layerfs/blob/baac1b92b97f0bd492002c285f6a13401b4f4c0b/README.md) | Existing Bash/SDK route and Work/History UI; its LayerFS pin must be reconciled with the proposed source pin before execution |
| [LoopX historical readback](https://github.com/loopx-project/loopx/blob/907ede6e1faff1fab0f2f765dde4358c69c7b7ff/loopx/control_plane/quota/ledger_readback.py) | Missing/unreadable events yield no contribution; real-entry reproduction and correct repair oracle remain M0 work |

The website's broad shared-storage wording does not supersede the supported
single-kernel Store contract. Older generic concept pages and pre-1.1 SDK names
are not implementation guidance for this study. Open issue reports are useful
risks, not independently reproduced defects or promised fixes.
