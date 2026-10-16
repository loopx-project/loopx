# Auto-Research Product Metrics

This note defines the user-facing metrics for LoopX auto research. These are
not implementation counters. They should help a maintainer, research lead, or
operator answer a product question:

> Did the agent network create useful research progress under a protected
> evaluator, with less human coordination cost than a manual loop?

The source of truth is the public-safe LoopX graph: `research_contract_v0`,
todo claims, `research_hypothesis_v0`, `research_evidence_event_v0`,
promotion/retirement candidates, user gates, and rollout events. Raw logs,
private paths, protected evaluator bodies, and local transcripts are not metric
inputs.

## Metrics That Matter

| Metric | Product question | Primary source | Good movement |
| --- | --- | --- | --- |
| Time to first scored attempt | How quickly did the system turn a research contract into real evaluator feedback? | first `research_evidence_event_v0` with `eval_status=scored` on the dev split | Lower time without weakening boundary checks |
| Useful hypotheses per active day | How much reusable search did the agent network produce? | hypotheses with scored evidence, retired negative evidence, or resumable retry evidence | More useful hypotheses, not more raw attempts |
| Held-out lift | Did the best candidate improve outside the iteration split? | best held-out metric compared with contract baseline and direction | Higher lift with clean protected boundary |
| Negative-evidence reuse | Did failed directions save future work? | contradicted/retired hypotheses referenced by later hypotheses, frontier pruning, or narrator summary | More explicit reuse of clean negative evidence |
| Retry recovery rate | Do incomplete attempts become useful instead of disappearing? | `needs_retry` evidence followed by scored, retired, or clearly blocked status | More retries closed with evidence |
| Human promotion decisions required | How much judgment did the user need to spend before a result was promotable? | promotion gates, user todos, review packets, reward overlays | Fewer ambiguous gates; every required gate is concrete |

These metrics are run-level and product-level. A single k-NN showcase may
highlight held-out lift and time to first scored attempt. A longer autonomous
research run should also report negative-evidence reuse, retry recovery, and
human promotion decisions required.

## Metric Definitions

### Time To First Scored Attempt

Start time is the first durable source record for the run:

- `research_contract_v0` creation when available;
- otherwise the first todo-backed `research_hypothesis_v0` for that goal.

End time is the earliest `research_evidence_event_v0` for the run where:

- `split=dev`;
- `eval_status=scored`;
- `protected_scope_clean=true`;
- `raw_logs_recorded=false`;
- `private_artifacts_recorded=false`.

This is not "time to first command." A command that only scaffolds files,
prints a frontier, or fails before the evaluator is not a scored attempt.

### Useful Hypotheses Per Active Day

A hypothesis is useful when it leaves one of these reusable outcomes:

- **Supported:** dev evidence improves under the contract metric direction.
- **Promotable:** dev and held-out evidence improve with a clean boundary.
- **Retired cleanly:** a regression, guardrail failure, or exactness failure is
  captured as negative evidence that future agents can see.
- **Resumable retry:** an inconclusive attempt preserves branch or artifact
  refs and a clear retry/retire policy.

Do not count every generated idea. Do not count duplicate hypotheses that
repeat the same mechanism without new evidence.

### Held-Out Lift

Held-out lift is the strongest user-value metric for optimization-style auto
research:

```text
held_out_lift = best_holdout_metric - baseline_metric
```

For maximize metrics, positive lift is good. For minimize metrics, invert the
sign or report the relative reduction. A held-out result is product-worthy only
when it is paired with:

- matching dev evidence;
- a clean editable/protected boundary;
- the promotion policy required by the research contract;
- an explicit statement of what was promoted and what was not.

### Negative-Evidence Reuse

Negative evidence has product value when it prevents repeated waste. Count it
only when later state uses it:

- a later hypothesis cites the retired/contradicted hypothesis as a source ref;
- a frontier projection prunes or deprioritizes the same mechanism family;
- a product narrator explains why a visible branch was retired;
- a retry policy chooses retire instead of relaunch because the evidence is
  already decisive.

The value statement should read like: "two exactness-breaking approximation
paths were retired and kept out of the next frontier," not "two failure rows
exist."

### Retry Recovery Rate

Retry recovery rate measures whether LoopX keeps incomplete work from turning
into silent loss:

```text
retry_recovery_rate =
  needs_retry_attempts_closed_with_scored_or_retired_evidence
  / total_needs_retry_attempts
```

Recovered retries may become scored evidence, clean retirement evidence, or a
concrete blocker. They should not remain as vague "try again later" notes.

### Human Promotion Decisions Required

Auto research still needs human judgment at promotion boundaries. The product
metric is not "zero humans." It is whether the human decision is small,
concrete, and valuable:

- Is the gate about promotion, private boundary, novelty, cost, or publication?
- Is the question concrete enough to answer in one decision?
- Does the gate unblock a specific hypothesis or result?
- Did the agent preserve safe non-gated work while waiting?

Report both count and quality. One crisp promotion gate is better than three
ambiguous approval pings.

## Proposed episode-level process analysis

The six definitions above remain unchanged. The proposed [research measurement
method](../../../architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md#evidence-based-episode-evaluation-proposed-refinement)
adds a read-only analysis view over question/experiment/evidence/decision episodes,
not a seventh qualified product metric or a composite progress score. Report
native outcomes, information, discrimination, actual adoption, repetition and
full cost/coverage separately. A useful-exploration fraction needs an explicit
eligible population and independent coding; unknown population means no fraction,
and unknown observations are not zero. Count- and resource-weighted views differ.
Late/common windows and feedback availability are declared before outcome review.

The existing Negative-Evidence Reuse definition includes communication and
frontier-selection signals. It is not interchangeable with the proposed stricter
**actual adoption** measure, which requires a version-bound artifact or behavior
consequence after an opportunity to use the evidence. Citation, narration or
visibility alone does not establish that consequence or downstream benefit.
Neither missing LoopX metadata in a baseline nor a lack of subsequent opportunity
proves no useful work or ignored evidence.

Research may analyze permitted private evidence under its own contract; product
extraction still consumes only authorized safe versioned projections with coverage.
The Public-Safe Extraction boundary below is unchanged. No hidden evaluator
material is eligible until both solver termination and completed scoring, and it
never returns to solver feedback. The [product reading design](long-horizon-progress-design.md)
connects the original curve to one selected episode and inline evidence, without
another graph or ledger. Independent scientific coding/held-out judge calibration
and packaged reading/comprehension remain separate open acceptance; no evaluator
or UI is installed by this design.

## What Not To Use As Product Metrics

Avoid metrics that mostly prove implementation activity:

- number of files touched;
- number of docs pages or UI panels;
- number of smoke tests;
- number of CLI commands printed;
- number of agents spawned;
- number of rows in a dashboard.

Those can be validation or engineering health signals. They are not user value
unless tied to a research outcome, a shorter decision path, or less repeated
work.

## Product Board Shape

The product board should present metrics in this order:

1. **Run value:** best held-out lift, promoted hypothesis, and boundary status.
2. **Search progress:** useful hypotheses per active day and first scored
   attempt time.
3. **Reuse:** retired directions and negative-evidence reuse.
4. **Recovery:** retry recovery rate and remaining retry blockers.
5. **Human attention:** promotion decisions required and unresolved gates.

The board may also show implementation health below the fold, but it should not
lead with it.

## Public-Safe Extraction

Metric extraction should read only public-safe projections:

- `research_evidence_graph_v0` for best dev/held-out metrics, negative
  evidence, and retry counts;
- `decentralized_research_frontier_v0` for currently runnable, blocked,
  promotion, and retirement candidates;
- `research_evidence_graph_v0` for any future public case page inputs;
- `loopx_rollout_event_v0` summaries for timestamps and lifecycle transitions;
- user/operator gates after they have been compacted into public-safe gate
  labels and todo ids.

Do not parse raw evaluator logs, raw benchmark traces, private source docs,
local filesystem paths, or chat transcripts to compute product metrics.

## Acceptance Checks

A product metric packet is acceptable when:

- every metric names the source record type it can be recomputed from;
- held-out lift is separated from dev-only progress;
- negative evidence is counted only when reused or made visible to future
  frontier selection;
- retry recovery distinguishes scored, retired, blocked, and still-open
  attempts;
- human promotion decisions are concrete gates, not generic approval status;
- all examples are public-safe and avoid local paths, credentials, private
  links, raw logs, and protected evaluator details.
