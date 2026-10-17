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

## Measurement and decision

Use the existing [measurement and episode
contract](../../../../docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md#6-measurement-model):

- **Native outcome:** valid best and final result, native sub-scores and
  time/cost to a predeclared material threshold. Keep quality and resource
  penalties distinct. An absent submission is unmeasured, not a measured
  zero gain.
- **Actual research:** independently assess goal relevance, experimental
  discrimination, new information, applicable evidence adoption and uninformative
  repetition. Trace the claim to code/input/result and the next executed action.
  A justified continuation or informative negative result can be useful.
- **Complete cost and coverage:** source cost once at study level, per-arm
  inherited age, setup, marginal replan and continuation costs; cached/uncached
  input, output, tools, control, evaluation, recovery and measured attention.
  Retain unknown/unallocated cost; use interval unions for concurrent wall time.

Freeze episode boundaries/rubric before inspecting arm labels and gains. Use
independent blinded assessment and report disagreement/unknowns before promoting
model-judge ratios to a scientific metric. A purpose-selected checkpoint cannot
estimate whole-run useful-exploration coverage. Compare G1C0−G0C0,
G0C1−G0C0 and the interaction using the actual matched units; correlated tasks,
repeated inputs and overlapping windows are not independent samples.

No default promotion follows from a better-looking plan. Require real adoption
and native outcome/full-cost evidence across qualified late cases. Preserve
regressions and stop/revise on treatment leakage, incomplete recovery or an
ineffective high-cost intervention. Low scores or an unchanged method alone
are not stopping rules. If only the combined arm works, do not credit both
components without their separate comparisons.

## Remaining author work

The proposed guidance and experiment are reviewable here. The packet prototype,
real receiver/model qualification, frozen run manifests and empirical comparison
remain unfinished. Keep this as a Draft while that author work remains; review,
merge, installation and scientific acceptance are separate. The bounded
future-facing improvement is to reuse existing read models across the study,
not add an unused runtime abstraction before the hypothesis is tested.
