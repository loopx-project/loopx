# Evidence-bound trajectory assessment

Status: an opt-in analysis producer, source-verified pack registry and metric
protocol. The packaged Goal-view consumer and independent evaluator calibration
remain separate acceptance work.
This implements the read-only producer slice of the S5/S11
[research episode assessment](../architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md#evidence-based-episode-evaluation-proposed-refinement).
It does not change solver prompts, benchmark scoring, checkpoints or research budgets.

## What the measurements should answer

Use one **research episode** as the process unit: a bounded question or competing
explanation, intervention, observation and subsequent decision. An episode can
span Turns; a tool call, a repeated capture or every test input is not an episode.
Freeze episode boundaries, eligibility and the observation window before judging
outcomes. Keep unfinished work and reasonable replication visible.

The following vector is motivated by long-horizon benchmark failure modes. It is
not a weighted total or a universal leaderboard. The first producer implements
only the six explicitly labelled model assessments; measured results and costs
continue to come from their existing authorities.

| Question and metric | Definition and denominator | What the observation changes |
| --- | --- | --- |
| **Native progress and cost to useful result** | Best and final valid native result, gain over the fixed starting incumbent, elapsed/model/resource cost to a predeclared threshold; unreached thresholds are censored, not zero time. Show prefix, continuation and total study cost separately. | Distinguish true objective improvement from proxy gains, timing noise and expensive marginal gains. Decompose quality and runtime penalties using the task's actual scoring rule. |
| **Bottleneck-directed work** (`goal_alignment`, model assessed) | Executed episodes with a supported link to a current bottleneck of the original objective / predeclared eligible executed episodes. Keep unknowns in the denominator. | A new model family is not inherently better than a small optimization. Detect when provisional agent gates displace the original objective. |
| **Benefit-test completion** (`benefit_test`, model assessed) | Completed episodes that actually test the claimed utility on the original objective path, through native evaluation or an independently justified task-representative proxy / eligible completed episodes. A negative utility test counts; a numerical prerequisite alone does not. | Detect an indefinitely lengthening chain of locally useful prerequisites. Report together with prerequisite cost and time until the first utility test; this is not an instruction to skip necessary correctness checks. |
| **Discriminating experiments** (`discrimination`, model assessed) | Completed experiments that validly distinguish the stated competing explanations / predeclared completed experiments. Invalid experiments do not become useful because they passed unrelated checks. | Prefer experiments that can change a decision; identify long prerequisite chains that never test the proposed benefit. |
| **Decision-relevant new evidence** (`information_gain`, model assessed) | Episodes resolving a relevant uncertainty not already settled by the available prior evidence / predeclared eligible episodes. Useful negative results count; noisy unresolved evidence stays unknown. | A flat native curve can contain useful progress. More graph nodes or more detailed prose are not evidence of that progress. |
| **Evidence adoption and response lag** (`evidence_adoption`, model assessed; lag measured separately) | Applicable evidence with a subsequent action demonstrably informed by it / applicable evidence with a real follow-up opportunity. Measure feedback-available → first qualifying action latency where timestamps exist. | A citation or replan statement alone is not adoption. Retaining an incumbent can be correct adoption. No later opportunity is excluded explicitly, not scored as neglect. |
| **Avoidable repetition and its cost** (`redundancy`, model assessed; cost measured separately) | Repeated same-question/intervention/input episodes with no new information or justified replication/recovery purpose / assessable eligible episodes. Also show attributed redundant resource cost / covered resource cost. | Separate looping from legitimate noise estimation, correctness validation and fault recovery. A repeated unchanged capture is not proof of redundant research. Here a positive verdict indicates a problem. |
| **Proxy-to-native transfer** | Among proxy-improved candidates with an eligible, completed valid native follow-up, count native improvement / unchanged / regression; show those lacking native follow-up separately. Bind candidate source and compare matched conditions. | Local quality improvement or a faster component cannot establish full-task benefit. Adaptive candidate selection limits generalization. |
| **Operational loss and checkpoint utility** | Failure counts / attempts separately for wake, replan, capture and restore; recovery wall-time union and resource costs with coverage. Qualified restore-and-continue points / attempted points. | Wake failure need not end the run; later recovery may continue. Failure frequency is not a fraction of useful work lost. File presence alone does not establish a reusable checkpoint. |

Route changes are a **descriptive distribution**, not an optimization target:
local refinement, representation/model/objective change, and correctness or
recovery work need a rationale and evidence. A substantive new route may still
repeat an already-refuted assumption. More switches can mean worse research.
Do not infer a route or adoption edge just because two events are adjacent.

## Coverage and interpretation

Every displayed process metric has `yes`, `no`, `unknown`, and `eligible` counts.
`assessed_fraction = (yes + no) / eligible`. Empty eligibility yields null, not
zero. `[yes / eligible, (yes + unknown) / eligible]` is a **missing-data bound on
this sample**, not a confidence interval and not a claim that model labels are
correct. `selection.exhaustive=false` or unknown population forbids presenting
these counts as a whole-run rate. Eligibility is supplied before model execution;
the model cannot silently drop difficult episodes or choose its denominator.

Separate source facts, model interpretation and independently reviewed findings.
The producer labels every result `uncalibrated_model`. Citation range validation
only proves that a reference exists in the supplied pack, not that the claim is
entailed. Scientific use still requires independent human boundary/label review,
held-out evaluator calibration and matched source, feedback, budget and window
rules from the research RFC. A selected debugging case is not a treatment effect.

For cost, retain unknown and unassigned intervals. Concurrent wall time uses the
union of intervals; resource usage and overlapping waiting labels are separate.
Do not add reasoning tokens again if the provider includes them in output tokens.
Ancestor response usage is deduplicated, but processing inherited context again
is new cost. Analysis-job usage is separate from solver and native-evaluation cost.
Never impute missing dollars from an unverified model price.

## Producer and consumer boundary

Capability owner: existing `benchmark-toolkit`. Shared input validation, fixed
rubric, applicability identity and count reduction live in TypeScript at
`loopx/control_plane/capabilities/trajectory_assessment.ts`; browser-safe shapes
and metric vocabulary live in the adjacent `trajectory_assessment_types.ts`.
The built-in Codex
adapter only owns local process and file I/O; it is not another solver runner,
LoopX Turn, scheduler, Explore writer, native judge or capability.

The input schema is `trajectory_assessment_input_v1`:

- `scope`: Goal, case, run, revision and observation-window identities;
- `objective`: original objective and priorities, distinct from provisional gates;
- `selection`: selection rule, known population episode count or null, exhaustive flag;
- `sources`: unique IDs, artifact reference, full artifact SHA-256, first line and
  literal excerpt text. The caller must read and hash the authorized original;
  the validator cannot independently verify a full-file digest from an excerpt;
- `episodes`: fixed IDs/questions, allowed source IDs and eligible metric names.

The analysis key binds the normalized complete input, rubric/method version,
model, reasoning effort and requested service tier. Input changes produce a new
key; old output must not appear current. Each eligible pair must appear exactly
once in model output. Decisive labels require source IDs and inclusive 1-based
line ranges within the episode's supplied evidence. Invented references, omitted
unknowns and duplicated pairs reject the whole result.

`assessment.json` is a `trajectory_assessment_v1` sidecar containing the key,
method, scope, selection, evidence manifest, per-episode assessments, count vector
and limitations. It contains no writable canonical graph edges, HTML or score
updates. Rendering must escape model text and distinguish inferred links from
recorded relations. Private evidence remains private; consumers must apply the
existing source audience/access rules before presenting it. No hidden evaluator
content may enter an active solver or its feedback.

## Explicit execution and readback

Prepare an authorized local evidence pack, then preview without a model call:

```sh
loopx benchmark trajectory-assess --input-json evidence-pack.json \
  --output-dir .local/trajectory-analysis --format json
```

Add `--execute` to invoke the installed Codex CLI using its existing account.
Defaults are `--model gpt-6.1-sol --effort max --service-tier fast`; override these
explicitly for another supported local configuration. Unsupported configuration
fails visibly rather than falling back. The receipt records requested settings;
`served_service_tier=null` means the adapter has no service-tier readback and does
not establish fast-mode delivery or a speedup. The CLI uses an ephemeral session,
ignores user configuration/rules, disables shell tools and web search, and passes
only the evidence pack in its prompt. It is not a security sandbox for hostile
executables or an authorization to read arbitrary machine files.

The command runs synchronously, with a default 900-second analysis timeout. A host
may supervise it asynchronously and read its immediate receipt:

```sh
loopx benchmark trajectory-assessment-status --directory .local/trajectory-analysis/KEY --format json
loopx benchmark trajectory-assessment-cancel --directory .local/trajectory-analysis/KEY --format json
```

`receipt.json` reports `running`, `succeeded`, `failed` or `cancelled`, the separate
analysis wall time and observed Codex usage. Cancellation is cooperative: a
request file instructs the owning adapter to terminate its child; the command
never signals a PID copied from disk. Same-key execution is exclusive (`busy`
while claimed); successful reuse revalidates output against the current input.
A failed/cancelled attempt needs explicit `--retry`, preserving the prior attempt.
A crashed adapter's stale lock/running receipt requires operator inspection;
this slice deliberately does not guess process ownership or silently re-launch.
No model call occurs on opening a history page, preview or status read.

## Register a fixed case for the research workbench

A trusted operator registers an authorized, fixed evidence pack for the owner
Goal. This is preparation by the Agent, not a path-entry task assigned to the
workbench user:

```sh
loopx benchmark trajectory-pack-register --goal-id OWNER_GOAL \
  --input-json evidence-pack.json --source-root authorized-capture \
  --title "Case · fixed observation window" --format json
loopx benchmark trajectory-packs --goal-id OWNER_GOAL --format json
```

The command uses the selected registry/runtime. Registration verifies every full
artifact SHA-256 and literal excerpt against files within the supplied source
root, rejecting traversal and escaping symlinks. It stores an immutable input
snapshot and opaque pack ID in private runtime storage. Registration does not
launch a model, copy permissions or alter the solver Goal. Multiple case/window
packs can belong to one research workbench; the most recently registered pack is
the default selection. A pack's actual solver Goal/case/run/window and sample
coverage must remain visible instead of being relabelled as the owner Goal's
whole history. This registry does not infer episode boundaries from every log
line or automatically qualify arbitrary captures.

The existing authenticated Goal host uses the provider in
`trajectory_assessment_store.py`: `discover`, `inspect_pack`, `execute_pack`,
`cancel_pack`, and `read_citation`. The host resolves the authorized runtime and
owner Goal before each call. Renderer requests contain only opaque pack IDs,
analysis keys and cited ranges; no file paths, shell commands or free-form model
configuration. There is no renderer registration endpoint. The filesystem and
Codex adapters remain Python because they own local provider I/O; shared
assessment/eligibility rules remain in the existing TypeScript owner.

Opening or polling a pack only reads. Explicit execution rechecks source hashes
and excerpts, resolves the registered configuration and calls the same exclusive
process owner. Results are requalified from model output; citation reads return
only exact ranges already cited in that result, from the immutable captured
excerpt. Missing/changed sources block new execution but preserve the historical
result and captured citations with a historical applicability label. A changed
assessment method requires a newly registered pack and does not silently relabel
old judgments as current. Historical display verifies the original receipt,
input/config/key and qualified sidecar digest without applying the new rubric.
Legacy results lacking that proof remain unavailable for automatic historical
display; original raw artifacts are retained for host inspection. While the
original method is still available, explicit same-key reuse requalifies its
output and records the proof. Raw provider errors, local paths and process IDs are not projected
to the workbench. Same-key success reuses the prior result; explicit retry is
reserved for failed/cancelled attempts.

The Goal research-history product owner consumes this API. Its companion journey
must finish case selection, explicit trigger, progress/cancel/retry, unavailable
and historical states, inline source return and packaged frontend/backend
readback. Present the key finding and uncertainty first, episode decisions next,
and citations/method/cost on demand. CLI success alone does not complete that
journey or qualify the model assessor.

## Measured solver session cost (optional)

### Native outcome readback (optional, separate from analyzer judgments)

The trusted provider/Agent may add `--native-outcomes-json native-outcomes.json`
to the existing `benchmark trajectory-pack-register` operation. The file is
relative to its already authorized `--source-root`; the renderer cannot register
paths or invent measurements. Reading it does not execute analysis or change a
solver, evaluator, retained archive or study board. Omitting it preserves the
previous pack identity and response shape.

The provider-exported JSON has this local diagnostic contract:

```json
{
  "schema_version": "trajectory_native_outcomes_v1",
  "scope": {"goal_id":"solver", "case_id":"case", "run_id":"run", "revision":"fixed", "window":"declared capture"},
  "start": "2026-01-01T00:00:00Z",
  "end": "2026-01-01T00:10:00Z",
  "complete": true,
  "metric": {"name":"error", "unit":"points", "higher_is_better":false},
  "retained_result_id": "result-1",
  "samples": [{
    "submission_index":1, "result_id":"result-1", "candidate_ref":"candidate-a",
    "submitted_at":"2026-01-01T00:01:00Z", "observed_at":"2026-01-01T00:02:00Z",
    "valid":true, "value":2,
    "citation":{"source_id":"native-source", "first_line":1, "last_line":1}
  }]
}
```

Every scope field must equal the registered assessment input. Source IDs and
line ranges must belong to that input; measurement/candidate IDs are opaque.
UTC timestamps must name real calendar instants (including leap-day validity),
not dates that a JavaScript parser silently normalizes into another window.
The source must contain provider-recorded released outcomes, not assessor
ratings or hidden evaluator material. The input curator/provider remains
responsible for the claim's truth and validity. `complete` declares prefix
coverage, not official integrity or score countability. A complete prefix has
contiguous submission indices starting at one. An invalid record uses
`valid:false,value:null`; zero is a valid measured value, never a missing value.
`retained_result_id:null` means retention is unknown, not that the mathematically
best value was adopted.
An archived measurement may explicitly use `submitted_at:null` when only its
readback time is known; `observed_at` must still be inside the capture. This does
not invent an original submission time, a full evaluation duration or prefix
coverage. Known submission times retain all existing calendar/window checks.

The existing typed benchmark-toolkit prepare owner returns a separate bounded
`native_outcomes` projection: first valid submission-one value when the prefix
is complete, best valid measurement within the capture under the explicit native
metric direction, provider-selected retained result, and final valid submission.
These are distinct: delayed feedback does not promote an older submission to
the final candidate, and a better measured score does not override retention or
guardrails. No generic score, monetary estimate or causal benefit is derived.
The original analysis key/model input is unchanged; native capture provenance
instead versions the pack identity. Changed/unavailable files leave historical
captured values visible with their warning, rather than borrowing another pack.

The existing Goal research source disclosure renders those facts and opens only
their bound original citations, even before model analysis exists. Solver
capture, native outcome and analyzed-episode windows remain separate. The cost
disclosure also shows the current analysis attempt's observed receipt usage;
earlier failures, native evaluation, recovery, operator costs and unmeasured
windows are not silently combined into a full-study bill. This does not complete
threshold-cost, gain-area, matched-study or scientific qualification.

Synthetic packaged native readback (not benchmark findings):

![Native outcome source disclosure](../development/assets/research-analysis/native-outcomes-desktop.png)

[Narrow-screen source disclosure](../development/assets/research-analysis/native-outcomes-mobile.png).
Detailed native facts live in the existing source disclosure, with costs in
their separate disclosure.

#### Quantified retained improvement

To expose observed outcome value alongside the six process judgments, the same
provider may optionally add `comparison` to its native capture:

```json
"comparison": {
  "baseline_result_id": "result-1",
  "retention_citation": {"source_id":"adoption-source", "first_line":4, "last_line":8}
}
```

The provider fixes a comparable baseline under the same native objective,
metric, unit and evaluation conditions; it must not select a baseline after
seeing the gain. Its result must be valid and present in this same scoped
capture, no later in submission order than the retained result. An incomplete
prefix may still contain an explicitly known baseline; that does not establish
the study's initial value. The retention citation must bind original evidence
of actual retained source/result identity, not a proposed decision or the
mathematically best score. Source binding checks provenance, not the truth or
comparability of provider claims.

The TypeScript owner adds `retained_improvement`: baseline, retention citation,
signed `value` and separate `final_candidate_delta`. Higher-is-better uses
retained minus baseline; lower-is-better reverses the subtraction. For example,
native error 7 → retained 3 yields **+4 points**. Retaining 7 after rejecting a
candidate with error 9 yields **0**, with candidate delta **−2**; avoiding that
regression is not a new realized benefit. An actually retained regression is
negative. Baseline or retention may explicitly be `null`; missing baseline,
retained result or retention evidence leaves realized value `null`, never zero.
Invalid references, unbound citations and non-finite differences are rejected.
Omitting `comparison` preserves the previous projection shape and does not
invent a baseline from the first/best result. Native-only registration still
does not execute an analysis or alter its key.

The Goal shows one compact **Retained improvement / 已采用成果增量** value before
the process judgments. Hover, keyboard focus or tap explains the metric;
expansion exposes both measured endpoints, retention evidence and candidate
change. Historical/unreadable provenance remains visible. The value describes
the declared native comparison, not objective completion, a general utility
score, matched LoopX uplift or causal attribution. Do not add across tasks,
units, overlapping windows or inherited results. Displayed precision is rounded;
original endpoint values remain readable in their citations. Pack selection
creates browser history so Back/Forward restores its scope and evidence.

### Solver capture registration

A trusted pack curator can bind a **complete captured Codex session** separately
from the model evidence. The existing register command accepts:

```sh
loopx benchmark trajectory-pack-register --goal-id OWNER_GOAL \
  --input-json evidence-pack.json --source-root authorized-capture \
  --title "Case · observation window" \
  --usage-jsonl solver-session.jsonl --usage-session-id EXPECTED_SESSION_ID \
  --format json
```

The capture path is relative to `source-root`, under the same containment rules.
The expected session ID must be supplied with it. Registration reads the original
file, verifies one unforked session with complete measured Turns, and retains its
hash and a deterministic numeric projection. It never sends the session text to
the analysis model. It does not execute, resume or change the solver.

`solver_usage.projection` is returned by existing discovery and pack inspection,
without a model call. It includes actual first/last event times, per-Turn source
lines, input, cached input, uncached input and output tokens. Codex cumulative
counter observations are differenced across Turns; summing the cumulative
`turn.completed` snapshots would charge earlier work repeatedly. Cached input is
part of input, and reasoning is part of output, not another additive cost.

Complete counter histories use the existing private local snapshot transport
when they exceed the ordinary 2 MiB RPC wire. Only this explicitly allowed
method opts in; reduced snapshots retain the 64 MiB ceiling and digest checks.
Oversized captures fail explicitly rather than dropping earlier Turns.

This adapter deliberately rejects missing prefixes/counter gaps, resets, wrong
session IDs, forked sessions, unmatched or unmeasured completions and a capture
ending inside a Turn. Unsupported captures are not zero-cost sessions. The
current adapter does not combine donor and continuation sessions. A curator must
verify that the session belongs to the selected run; identity and hash checks do
not independently establish that scientific association.

The projection's own event window can differ from the curated episode window.
`elapsed_seconds` spans its first Turn start to its last completion;
`turn_elapsed_seconds` sums those non-overlapping timestamp intervals, excluding
between-Turn gaps. Both include model/tool/orchestration time and are neither CPU
usage nor token-generation time. They deliberately use event timestamps rather
than mixing in provider-rounded `duration_ms`. Dollar, evaluator and inherited
study costs remain unknown; no token price or waste fraction is inferred.

Usage facts have their own provenance and applicability. A changed or missing
capture leaves the captured values visible as historical, while unrelated model
evidence retains its existing availability. Changing a bound capture produces a
new pack ID but does not invalidate a reusable assessment of unchanged evidence.
The source binding and projection are covered by that ID. Omit both options to
retain the previous registration identity and response shape. To stop using this
projection, register/select a pack without the optional capture; no solver change
or destructive removal of historical evidence is necessary.

Ownership stays with `benchmark-toolkit`: this built-in provider adapter produces
a read-only observation-window diagnostic. The existing quota Codex reader owns
live cumulative observations and idempotent ledger booking; it intentionally
accepts partial active sessions and does not validate complete per-Turn windows.
This diagnostic neither books usage nor replaces that authority. Python performs
file containment and counter-only transport; TypeScript owns window validation
and reduction, and the browser-safe assessment types expose the result.

This is the measured-cost producer for the existing research workbench. Its
HTTP consumer can read the same inspection field; numerical presentation and the
full packaged user journey remain a companion acceptance. No new renderer path
or command input, model execution permission, score authority or default analysis
is introduced.

## Goal workspace reader

The Goal overview offers **Deep analysis** beside the recorded Explore history.
The primary summary displays the existing six process judgments, each with
its yes/eligible, no and unknown counts. An eligible denominator of zero is
N/A, and missing analysis stays unanalysed. One metric disclosure shows the
bound episode, explanation and citations; original source reads reuse the
same reader as detailed analysis. Coverage and missing-data bounds come from
the assessment owner, not a frontend score. Avoidable repetition has the
opposite interpretation to a useful positive observation. Costs are optional
secondary context. These are not native outcome magnitudes or a total score.

Choose a registered source, inspect its fixed scope and model request, then
explicitly run or retry analysis. Reading, refreshing, switching sources and
following citations never launches a job. Running work can be cancelled;
changed or missing sources preserve the historical result with a visible warning.

Browser back/forward applies the same source transition as the selector: a
different pack clears the prior pack's judgments, costs and citation readback
before loading. If that read fails, the selected source remains unavailable
until retry; another pack's result is not displayed under its identity. A failed
refresh of the same pack may retain its last readback with an explicit warning.

![Selected source unavailable after browser history navigation](../development/assets/research-analysis/history-unavailable-mobile.png)

Synthetic packaged recovery state: the selector names the requested source;
another source's results are absent, and retry reads that same source without
launching analysis.

The existing toolkit owns registration, model admission, output validation,
metrics and source-bound reuse; the browser cannot supply local paths, model
settings or arbitrary commands. Native utility remains unknown without its
measured projection. Agent judgments are uncalibrated and do not establish
scientific gains or a whole-run success rate.

The synthetic packaged example below illustrates the reader and its scope
controls; its numbers are test data, not benchmark findings. Real registered
checkpoint reads are qualified separately from this presentation example.

![Goal overview with registered trajectory analysis](../development/assets/research-analysis/first-screen.png)

![Scoped interpretation and usage reader](../development/assets/research-analysis/summary.png)
