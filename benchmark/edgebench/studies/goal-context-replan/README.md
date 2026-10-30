# Goal-centred replan: proposed late-checkpoint study

[中文](README.zh-CN.md)

**Draft research proposal. No treatment implementation, new setting, experiment
result or product-default change is supplied by this document.** The next work
is a source-qualified packet prototype, then a prospectively selected late-point
study. Existing checkpoint and retained/fresh studies keep their own protocol.

## Question and existing behavior

Can an explicit whole-goal review, combined with better organization of relevant
evidence, improve the decisions and outcomes of long-running research?

The baseline already has an original Goal. The existing TypeScript
[`projectCoreGoal`](../../../../loopx/control_plane/work_items/replan_context.ts)
projects its same-source objective and acceptance; the optional
[`planning horizon`](../../../../docs/reference/protocols/quota-planning-horizon-v0.md)
can expose that Goal before selection. Existing replan guidance preserves
priorities, marginal cost, scoped counterevidence and justified continuation.
Replacing a missing-Goal bug or merely adding the field again is not this study.

The hypothesis is about how the agent uses that information: a review can stay
anchored to the current Todo, recent activity or an inherited plan even when
the original objective is present. A request to reconsider the whole Goal may
redirect attention toward the most consequential remaining gap. This is a
hypothesis, not evidence of a reproducible treatment benefit or a diagnosed
failure in every unchanged plan.

## Two independently testable changes

**G — whole-goal review instruction.** Keep the ordinary packet and its Goal.
At a due replan, append this candidate instruction:

> Step back to the original Goal and the whole available evidence, then plan
> the next useful work. Reconsider what matters most now, and justify whether
> the present path should continue or change.

Use the existing Vision/path-delta and Todo writeback. Do not add a mandatory
answer template, a new settlement obligation or a requirement to change methods.
The change is agent-consumed guidance, so its eventual implementation is a
behavior change even though machine admission rules stay the same.

**C — source-bound decision context.** Keep the complete ordinary packet and
the same accessible source pool; add a compact, reference-bearing reading index:

1. Original objective/priorities and still-unmet acceptance, from their owners.
2. Actual current candidate and retained incumbent, linked to released outcomes.
3. Applicable positive, negative and unresolved evidence; its tested conditions.
4. Previous decision, subsequent observed work and unresolved dependencies or
   alternatives, with missing/omitted coverage stated.

Present original Goal facts before the inherited plan. The agent assesses the
main remaining gap after reading the evidence; a renderer must not invent a
"bottleneck" fact or rank methods with an implicit scorer. Prefer existing
source excerpts and references over a second model-written summary. If an
additional model call is later proposed, treat it as another intervention and
charge its complete cost.

This first C comparison tests organization and salience, not privileged access
or more retrieved knowledge. The index may expose only facts already delivered
in the frozen ordinary packet; mark missing items unknown rather than fetching
extra records or filling them from operator notes. Both arms retain the same
original Goal, complete packet, source versions and authorized read paths.
Richer retrieval would be a separate intervention. Charge the added index and
any additional reads; a positive result would not isolate ordering from the
index's repetition or added tokens. An equal-length/reordering control is a
subsequent discriminating test if that distinction matters.

## Placement and boundaries

This is a study under `benchmark-toolkit` with the native EdgeBench provider,
not a new capability or provider. A future prototype belongs to the existing
TypeScript replan/context and planning-horizon owners. Reuse their source and
reference resolution; keep Python in transport/adaptation. Do not create a
second Goal store, history, relevance classifier or decision authority.

Some structured followthrough/Explore basis is currently projected only with
the stride strategy. C must not silently enable stride to obtain it. Qualify
reuse of the read-only owner separately, preserving stride-off policy. Preserve
the actual availability, provenance, omission and error states of each source.
All baseline guidance and machine obligations remain available in every cell.

G/C are local study labels, not shared protocol literals or supported CLI
options. This Draft adds no frontend, Lark or CLI control. If a product option
is later introduced, use its existing configuration owner and deliver effective
readback/disable plus necessary packaged frontend and other caller companions.
Do not infer shipped opt-in support from this proposal.

## Late-point admission and controls

Use newly captured, qualified checkpoints at a natural boundary **before the
model performs the due replan**. Select prospectively by source age and quality,
not the score or apparent attractiveness of the history. An example late rule
is at least 75% of the original source budget; freeze the task-specific rule
before selecting points. Old or early recovery points do not qualify by being
restored successfully.

Read back original Goal, code/incumbent, scientific files and dependencies,
native public feedback/history, remaining opportunity/cooldown and Explore
nodes/findings/relations. Record relation availability separately from research
quality; absent edges are neither fabricated nor interpreted as no research.
Qualify the real ordinary receiver and dynamic dependencies before model entry.

Hold fresh-session reconstruction constant across G/C cells. Fresh means replan
from the original Goal and captured research evidence, not execute a decision
already made in the retained session. This study is orthogonal to retained
versus fresh: switching both factors in one comparison cannot identify G or C.

Pin the same receiver/product/runner/native sources, model/effort, full research
files, resources, replan cadence and feedback for all cells. Keep Goal horizon
on, stride off and TurnEnvelope off. Do not inject host deadlines, countdowns or
persistent runtime guidance into model inputs. Preserve original permissions,
Task/Turn/obligation identity, source fencing and incumbent ranking. No private
post-checkpoint observation, other arm's later result or hidden evaluator detail
may be added to a cell's context.

## Proposed factorial and execution order

| Local study label | Whole-goal instruction | Decision-context index |
|---|---|---|
| G0C0 | Current production guidance | Current production context |
| G1C0 | Candidate G | Current production context |
| G0C1 | Current production guidance | Candidate C |
| G1C1 | Candidate G | Candidate C |

The factorial estimates instruction and context differences, and whether their
combination behaves differently. A single replay per cell is descriptive;
independent repetitions and more tasks are needed for robust effect estimates.
Provider determinism or a reproducible model seed must not be assumed.

1. Build a disposable packet prototype. Verify source/reference correctness,
   off-state parity, unchanged obligations and complete ordinary inputs. Missing
   Goal, stale/wrong-lifetime evidence, unavailable sources, contradictory
   outcomes, truncated history and no prior decision need explicit cases.
   Code or text checks qualify transport, not model use or scientific benefit.
2. Freeze one eligible late point, exact packets/digests and the analysis rubric
   before seeing treatment outputs. Pilot all four fresh replans when capacity
   permits; retain failed, unchanged and inconclusive decisions. Store native
   model requests/responses privately for treatment-fidelity inspection.
3. Measure actual continuation through a meaningful experiment and the next
   decision after its result, including negative results. Use common prospective
   observation windows; do not reward changed language or a plan that never ran.
   Replicate the factorial, or preregister a selected comparison on an independent
   point. A comparison selected after pilot outcomes is exploratory.

Fit the study inside existing run authority and capacity. A four-cell pilot on
one case is not permission for twelve simultaneous cells or for replacing the
primary retained/fresh study. Respect at most three tasks/six research workers;
queue or stage work only with preserved budget and opportunity accounting.
If a 36h cumulative continuation ceiling is authorized, it includes source age,
setup and chargeable waiting; it is not another 36h after restore. Keep native
cooldown measured from accepted submission, no banked opportunities and no extra
tail grading. Record sequence/shared-host effects. Reusing an earlier control
requires its preregistered protocol and source/window equivalence, not a similar
label or favorable score. This document launches no jobs.

## Program sequence and the outcome-refinement candidate

Keep the existing retained/fresh comparison first, then the single opt-in stride
study, then G/C. Necessary correctness repairs and new FROM-ZERO late-checkpoint
production precede all three. Qualification of an upgraded receiver cannot turn
an older donor into a new cold start. Record the donor, receiver, runner, native
evaluator and actual installed sources independently.

| Stage | Entry and useful exit | Capacity and accounting |
|---|---|---|
| Reliability and new late points | Qualify the ordinary installed receiver and its continuation; select the first chronologically eligible complete natural pre-replan point from the admitted FROM-ZERO source. Preserve failed captures and premature exits. | Read actual active workers, paired free slots, source age and absolute deadline from the private manifest. A merge or an empty slot is not experiment admission. |
| Primary retained/fresh | Restore the same immutable research state, Goal, incumbent and native cursor; vary only whether the due replan uses retained or fresh session context. Continue through released feedback and the next executed decision. | Drain a replaced donor only when its qualified replacement is ready. Preserve original opportunities, accepted-submission cooldown and cumulative ceiling. |
| Existing stride | Qualify actual adoption of the single declared grow/switch/verify strategy, the ensuing complete experiment and the next evidence-informed decision. | Release paired slots from preceding work; do not combine stride changes with G/C or treat a declaration as execution. |
| G/C | Qualify the actual current client/receiver, freeze packets, episode/rubric definitions and matched windows, then execute the declared factorial or a prospectively narrowed comparison. | Keep the four-cell design above. Stage only when source and comparable windows remain qualified; a narrower pair cannot retrospectively become the factorial. |
| Outcome refinement, lower priority | Resolve overlap with G/C and independently qualify an isolatable functional difference before considering a matched comparison. | No automatic extra cells, doubled factorial or renewed budget. Insufficient compatible source, slots or original remaining time leaves a candidate pending. |

Where a 36h cumulative ceiling is authorized, remaining time is at most
`36h - source age - subsequent setup/wait/recovery elapsed`, measured on the
owning absolute clock. A point captured at 9h or 13.5h therefore leaves at most
27h or 22.5h before those later costs. These are upper bounds, not allocations.
Model inputs still receive no host deadline or persistent runtime guidance.

[PR #6784](https://github.com/loopx-project/loopx/pull/6784), inspected at
`a1e9cee8ae9959eeacfc2bb14053446d02cc4afe`, is a lower-priority candidate:
original-intent/cumulative-requirement preservation, per-requirement evidence
review and uncovered-gap planning. Its default workflow has no overall enabled
switch; optional executable checks are an evidence path, not a control for the
whole intervention. The inspected proposal is not live-model or efficacy proof.

A possible counterfactual is one qualified common base Q versus Q plus only
the isolated outcome-refinement difference, with all necessary common fixes.
Verify ordinary installation and state compatibility before preregistration.
Restore the same immutable pre-new-write point into separate states; never feed
newer requirement-state writes back into an incompatible older binary. Do not
invent a disabled configuration, second Goal store or selectable policy version.
If the difference cannot be isolated, retain engineering characterization only.

G tests an additional review instruction; C organizes already-delivered facts;
outcome refinement also changes structured requirement maintenance/review.
Merge equivalent questions instead of duplicating cells. For partial overlap,
hold G/C, stride and fresh reconstruction fixed and name the incremental package.
If outcome refinement becomes the common base, later G/C estimates are increments
on that base, not evidence of outcome refinement's own effect.

## Measurement and decision

Reuse the [measurement and episode contract](../../../../docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md#6-measurement-model)
and the shipped [trajectory-assessment owner](../../../../docs/reference/trajectory-assessment.md).
Use the chain **delivery → actual adoption → research evidence/decision →
adopted candidate → native outcome and cost** to explain a comparison. This is
an evidence chain to investigate, not an automatically inferred causal graph.

### Freeze episodes, eligibility and coverage

A research episode spans a question/competing explanation, intervention,
observation and subsequent decision, potentially across Turns. Freeze its start,
end rule, prior-evidence scope, eligibility and observation window before judging
arm labels or gains. Unfinished episodes stay visible and right-censored where
appropriate. Calls, Turns, screenshots, graph nodes and repeated inputs are not
independent episodes. Overlapping episodes share dependencies and costs.

The curator declares episode eligibility before model execution. Report the
selection rule, known population or unknown population, exhaustive flag, omitted
material and excluded no-follow-up opportunities. A purposive debugging sample
or `exhaustive=false` cannot yield a whole-run rate. Never let the model remove
difficult cases or silently repair a denominator after seeing its labels.

### Existing six process judgments

All six reuse the existing TypeScript vocabulary and reducer. They remain
`uncalibrated_model`; this study introduces neither a second scorer nor a
weighted progress total. For each row, report yes/no/unknown/eligible. The
denominator and endpoint differ by metric, even on the same episode pack.

| Metric and decision question | Eligible denominator and window | Supporting evidence and interpretation |
|---|---|---|
| `goal_alignment`: does executed work address the original objective's limiting component? | Predeclared eligible executed episodes in the fixed window. | Original objective/priorities plus executed code/input and a justified bottleneck link. Restating the Goal is insufficient; a small improvement can be aligned. |
| `benefit_test`: was claimed utility actually tested? | Eligible completed episodes. | Native evaluation or an independently justified task-representative proxy on the objective path. A valid negative test can be yes; prerequisite-only correctness is no, not worthless. Unknown proxy representativeness stays unknown. |
| `discrimination`: were competing explanations distinguished? | Predeclared completed experiments. | Frozen alternatives, intervention, controls, validity and observation. Invalid experiments are no; missing validity is unknown. Passing unrelated checks or one favorable proxy is insufficient. |
| `information_gain`: was a relevant unresolved uncertainty resolved? | Predeclared eligible episodes with their available prior-evidence scope. | Prior evidence, new observation and the uncertainty it resolves. Useful negatives count; noisy unresolved results and missing prior coverage remain unknown. |
| `evidence_adoption`: did applicable feedback inform later action? | Applicable-evidence episodes with a real subsequent action opportunity before the cutoff; explicitly exclude no-opportunity cases. | Released feedback, availability time and source-bound later code, experiment or deliberately retained artifact. Citation/Todo alone is insufficient. Missing linkage stays unknown. |
| `redundancy`: was the same question/intervention/input repeated without a useful reason? | Assessable eligible episodes under the frozen prior-evidence scope. | Method/input identity plus prior result, later execution and replication/noise/recovery rationale. Here yes indicates a problem. Necessary verification and legitimate replication are not automatically redundant. |

For every row, `eligible = yes + no + unknown` and
`assessed_fraction = (yes + no) / eligible`. Keep unknowns in the denominator.
`[yes / eligible, (yes + unknown) / eligible]` bounds missing labels in this
sample; it is not a confidence interval, calibration or population estimate.
An eligible count of zero is N/A. An adverse-direction redundancy verdict must
not be displayed as useful progress. Numerical summaries do not certify that
cited lines entail a model's judgment.

### Facts beside the six judgments

These are existing fact readbacks, calculations from bound evidence, or proposed
study diagnostics as marked below. They are not additional model-score fields.

| Mechanism or outcome | Fixed unit, denominator/window and unknown rule | Source, association and current availability |
|---|---|---|
| Treatment delivery and use | Attempted due-replan inputs; distinguish delivered, undelivered and unknown. Separately inspect subsequent actual use. | Exact client-visible input/response, packet digest, Goal/Turn/obligation and following action. The prototype qualifies framing; an output file is not proof of receipt, and receipt is not use. Current real receiver/model journey remains pending. |
| Feedback response lag | Applicable released feedback with a qualifying subsequent action: action time minus feedback-available time. Censor no action at the fixed endpoint; missing timestamps are unknown. | Native delivery and solver action timestamps, with version-bound evidence. Do not measure from submission or infer adoption from adjacency. Derivable where both events and their relationship are qualified; no generic all-run reducer is claimed here. |
| Proxy-to-native transfer and retention | Proxy-improved candidates with eligible completed valid native follow-up: count improved/unchanged/regressed against the fixed anchor. Show no-follow-up candidates separately. | Same candidate source/input, representative-proxy basis, native scorer/conditions and actual retain/reject readback. Proposed study reduction; a staged candidate, next working tree and saved incumbent remain distinct. |
| Native initial/window-best/retained/latest-valid/final | Fixed starting incumbent and common elapsed window; preserve submission order, metric direction and valid/invalid/missing distinctions. Final remains pending while running. Threshold time/cost is censored when unreached. | Existing native projection supplies bounded first/best/retained/final-valid facts when its coverage permits. A complete prefix is needed for submission-one initial; capture-final-valid is not study-final. Retention is provider-selected, not mathematical best. Matched windows/threshold costs still need study qualification. |
| Operational loss and checkpoint utility | Separate failures/attempts for wake, replan, capture and restore; then qualified restore-and-continue points/attempted points. Preserve every failed admitted arm. | Existing receipts, source manifests and actual continuation. File presence does not prove completeness; exit zero can still be premature host termination. Recovery cost is separate from scientific strategy quality. Cross-stage study reduction remains to be qualified. |
| Complete cost | Per-arm continuation plus study-level source cost once; fixed observation window with unknown/unallocated intervals visible. | Solver, analysis-job, native, setup/restore/retry and operator evidence retain separate owners. Complete-session usage projection exists; donor-plus-continuation, shared-host, price and full-study totals are not automatically available. |

Difference cumulative usage only within its qualified session history; subtract
an inherited baseline once and retain resets/gaps as unsupported. Reprocessing
inherited context is new cost. Cached input is part of input; reasoning may
already be part of output. Concurrent wall time uses interval unions. Separate
analyzer development/maintenance cost from every analysis invocation, including
failures and retries; include both in the corresponding full-study accounting.
Unknown prices, human attention or intervals do not become invented dollars,
savings or a waste percentage. An entire work Turn is not pure replan overhead.

### Analyzer qualification and example design

Before scientific G/C comparison, curate existing authorized positive,
negative, unchanged, unadopted, missing-coverage and operational-failure cases.
Keep raw trajectories and exact case readbacks private. The following are
reusable **diagnostic designs**, not G/C treatment results or fixed model labels:

| Case design | Trace to verify | Discriminating interpretation |
|---|---|---|
| Evidence was captured but a bounded index was mistaken for the whole history | Checkpoint/report digest → visible projection and omissions → repeated method → corrective audit → next executed action and its cost. | Separates file loss from retrieval/coverage interpretation. A later correction may adopt useful negative evidence; not all corrective-Turn cost is waste. |
| Local improvement fails native evaluation | Proxy conditions → exact evaluated source → released native regression → actual incumbent restoration → next experiment. | Benefit testing and useful information can coexist with no native improvement. Preserve positive controls and timing/quality decomposition; do not attribute the whole result to one changed module. |
| A promising local candidate is deliberately not submitted | Complete diagnostic evidence, including negative controls → rejection/retained artifact → real next action. | No native follow-up is missing measurement, not zero. Evidence adoption needs the later opportunity and action; hypothetical saved evaluation cost is unknown. |
| A valid positive candidate and a premature-exit counterexample | Bind the positive candidate archive to native feedback and actual retention; separately bind automatic wait/resume ownership to the terminal worker trace. | Positive native transfer does not prove component-level causality. Premature host exit belongs to operational loss, not a valid strategy negative or a reset evaluation opportunity. |

First review episode boundaries and eligibility independently without arm labels
or outcome magnitudes, then audit citation entailment and alternative readings.
Use an independent blind rater, report agreement/disagreement on shared eligible
pairs and coverage of unknowns, and reserve held-out cases for calibration.
Freeze the revised evidence packs, rubric/method and analysis configuration
before comparing arms. Until actual calibration, retain `uncalibrated_model`.

The production owner already binds normalized complete input, rubric/method,
model, effort and requested tier into the analysis key; registration verifies
source hashes/excerpts, and the reducer requires each eligible pair exactly
once. Reuse it. A changed method/input/configuration creates a new applicable
key/pack; keep old outputs historical and never change standards for only one
arm. Range validation is not semantic entailment; automatic episode discovery,
whole-run coverage, causal adoption links and evaluator calibration remain
gaps. Improve the evidence/curation method first; propose a bounded owner change
only for a demonstrated missing production contract.

Evidence curation, source/hash checks and blind-method preparation can proceed
offline alongside donor capture. An analysis preview, status read or fact graph
uses no extra model call. Paid analysis needs existing explicit admission,
source/configuration qualification and cancel/retry ownership. Record every
analysis attempt separately. Warm receiver and frozen late-point continuation
qualification precede scientific comparison; old-source synthetic success
cannot substitute. No analysis finding is sent back to a frozen solver. Feeding
analysis back would be another prospectively declared intervention.

Compare G1C0−G0C0, G0C1−G0C0 and the interaction only using actually matched
units. Correlated episodes, repeated inputs and overlapping windows are not
independent samples. Do not allocate native gains across episodes or report an
uncalibrated combined score. A justified continuation can be useful; more pivots
or reviews are not inherently better.

No default promotion follows from a better-looking plan. Require actual
adoption, native outcome and complete-cost evidence across qualified late cases.
Preserve regressions; stop/revise for treatment leakage, incomplete recovery or
an ineffective high-cost intervention, not low score or unchanged method alone.
If only the combined arm works, do not credit both components without separate
comparisons. Publication and a successful analysis job do not close the study.

## Remaining author work

An initial disposable, **offline** packet-composition probe now uses real
isolated File/SQLite CLI output. It reuses the existing TypeScript
`checkpointArtifacts` pre-decision selector and `validateReplanContext`; it does
not reproduce their admission rules. The unmodified ordinary UTF-8 packet is
bound to its digest, Goal, Agent, Turn and obligation. G0C0 preserves every byte;
the other three cells append only the declared G instruction and/or C index.
Each index value carries a JSON pointer into that same packet. No source read,
model summary, relevance ranking or missing-fact repair occurs in composition.

The probe verifies unchanged complete input, identical C additions across G
conditions, reference/value equality and explicit missing coverage. Twelve
negative inputs per store cover changed digest/identity, inconsistent context,
missing Goal, malformed evidence, non-replan and uncommitted boundaries.
Contradictory observations and truncated coverage remain visible. For these
synthetic fixtures, G adds 197 UTF-8 bytes and C adds approximately 4.3 KB.
These are packet-size observations, not token, latency or utility measurements.
The disposable composer is not installed in the runner or published as a new
runtime API; its fixtures and outputs are not benchmark results.

One transport constraint is now explicit: with stride off, the ordinary context
does not supply the stride-only structured decision-followthrough view. The
first C index must preserve that unknown rather than enable stride or fetch
another history. It also cannot infer a candidate/incumbent association from
free-text summaries. This limits the first C treatment to the facts actually
delivered, rather than the fuller desired context list above.

A disposable CLI transport prototype now preserves one valid JSON response. It invokes the
real destination CLI once, retains every original response member and its raw
digest, then composes a separate study-local advisory from that same response.
G0C0 and disabled calls retain exact byte parity. G/C do not replace the current
Goal, commands, Turn or obligation. The C index preserves the original kernel
projection envelope, including freshness alerts and omitted coverage; an adapter
must not restamp retained facts as a fresh source read. The plain-text fragments
above remain offline characterization, not a production JSON serializer.

Real isolated File/SQLite CLI checks cover this framing, unchanged original
values, identical C across G, ordinary/error parity, wrong destination, and a
lost-display retry of the same committed guard without another debit. A
composition failure preserves the already committed raw response and records
missing treatment; it must not silently certify delivery or rerun an effect.
The complete caller checkout and actual interpreter must be source-qualified;
matching a few unchanged owner files cannot qualify a newer CLI entrypoint.

Advisory delivery and checkpoint capture have different lifetimes. The existing
restore barrier correctly prevents another capture before the initial restored
review and native cursor are accepted. A first replan may occur inside model
preparation, while that capture gate is closed. G/C delivery must therefore
observe the current due-replan input independently, without opening the capture
gate or enabling TurnEnvelope. Common fresh reconstruction already asks the
agent to read the original objective, failed alternatives and retained best;
G estimates additional global reflection beyond that shared baseline.

Bind the sealed source, current destination response and advisory separately.
A source packet remains historical evidence, never destination admission or
executable source commands. Model-consumer qualification must trace the actual
client-visible tool response, evidence-linked replan/writeback and subsequent
useful artifact, rather than infer receipt from a file labelled model-visible.
Retain startup/recovery failures and their complete cost. Synthetic engineering
journeys qualify transport only; they do not qualify native checkpoint recovery,
benchmark evidence adoption or G/C scientific benefit.

Real warm-receiver qualification, frozen late-point manifests and empirical
continuation comparisons remain unfinished. Keep this as a Draft while that
author work remains; review, merge, installation and scientific acceptance are
separate. The bounded future-facing pass reuses existing typed readers and
pre-decision selection; no production abstraction, runtime option or frontend
control is supplied. Current donors and the primary retained/fresh study remain
unchanged. Do not borrow an existing fresh arm as an unplanned G0C0 control or
start four extra cells without four available slots and the original remaining
cumulative budget. Freeze any narrower comparison prospectively; it does not
retroactively complete the proposed factorial.
