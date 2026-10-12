---
name: loopx-benchmark
description: "Operate or analyze LoopX-managed benchmark experiments through benchmark-toolkit, including trajectory-based effectiveness and efficiency diagnosis, matched comparisons, run selection and integrity. Solving an assigned task under an existing runner does not by itself select this skill. Excludes casual benchmark discussion and ordinary software microbenchmarks."
---

# LoopX Benchmark Workflow

Use this skill to operate or analyze a LoopX-managed benchmark experiment. The builtin
`benchmark-toolkit` capability owns provider-neutral experiment state and
integrity boundaries. This packaged skill is its task-triggered Agent playbook.

An assigned solver follows its task instructions and current execution contract.
The words “benchmark”, “evaluation”, or “submission” in that task do not grant
the operator role or require experiment-board discovery. Use this workflow when
the requested work actually includes run management, analysis of an active run,
or post-run analysis; an explicit request to use the skill still applies. Keep
the solver's task-local validation and authorized submission path distinct from
experiment management.

The capability is catalog-ready without a per-Goal enable switch. Installing
this skill does not grant runner, shell, network, credential, private-evidence,
or Goal mutation authority. Respect the selected todo's required capabilities,
any external provider binding, host permissions, and user gates.

## Capability surface

- `loopx capability show benchmark-toolkit --format json` — catalog entry with
  usage hints, role boundaries, and the post-run case-insight template.
- `loopx benchmark --help` — subcommands (experiment-board-show,
  experiment-board-upsert, source-revision-fence, integrity-qualification,
  classify-artifacts).

## Share a study through the public-safe contract

When another benchmark developer needs portable study data, use the capability's
typed study flow rather than sharing a runner-specific ledger or raw evidence:

1. Validate `benchmark_study_manifest_v0` with `benchmark study-validate`.
2. Wrap one allowlisted manifest, experiment-board row, redacted insight, or runtime
   observation with `benchmark upload-envelope`.
3. Run `benchmark upload-local` without `--execute` first, then explicitly execute
   against a caller-selected local JSONL store.
4. Verify the record/digest/revision binding with `benchmark upload-readback`.
5. Derive the campaign/arm/case/run packet with `benchmark study-dashboard`; pass a
   compact four-arm contract only when the study preregistered that design.

For `case_insight_projection`, first upload the same run's active terminal
experiment-board row with `insight.status=complete`. The case, run, and outcome
must match; the run row remains the only arm, score, countability, integrity, and
treatment-fidelity authority. Reduce private post-run evidence to bounded prose
and public-safe handles or digests before building the envelope.

The local provider is a no-network simulation. It does not grant remote upload,
publication, credentials, retention, or benchmark submission authority. Adapters
keep their native metric names and reduce private post-run evidence before envelope
construction.

## Share exploratory behavior findings

When the owner authorizes selected behavioral observations but not a complete
study release, use `behavior_finding` records and `benchmark behavior-report`.
See `docs/reference/benchmark-behavior-findings.md` for the contract. These records
require selection rules, sample denominators, observations, interpretations,
limitations, counterevidence, and evidence digests; they require neither a run-row
upload nor a full study manifest and have no score authority.

Freeze the authorized disclosure projection before rendering. Review the same
scope in visible text, foldouts, embedded data, downloads, and PR attachments.
Permission to share duration does not grant permission to share outcome totals
or deltas. Schema validity and a producer redaction attestation are not publication
approval or verification of unshared evidence. Keep selected-case observations
explicitly exploratory and retain the relevant limitations and counterexamples.

## Select the operating lane

- **Inspect or explain:** use `capability show` and `benchmark --help`; remain
  read-only. Do not create an experiment-board row merely because the user asks
  what the toolkit does.
- **Plan, select, or launch a run:** follow the experiment sequence below. The
  first action is to read the board; a launch still requires an authorized
  runner and admitted source.
- **Monitor an active campaign:** read the board and runtime-owned projections;
  update only on material run transitions. Do not manufacture progress from a
  timer tick.
- **Diagnose effectiveness or trajectory efficiency:** follow the analysis below.
  For active runs, use authorized solver/runtime observations and released score
  projections; keep findings provisional and hidden evaluator evidence closed.
- **Analyze a terminal run:** wait until solving is terminal and scoring is
  complete before reading hidden evaluator evidence or writing a case insight.

For a generic library microbenchmark or an eval with no LoopX Goal/board, use
the task's normal tools instead of imposing this workflow.

## Diagnose effectiveness and trajectory efficiency

Start with the task's real success criterion and evaluator behavior, then explain
which work produced useful progress. Keep score quality and operational efficiency
separate: fewer bytes, calls or tokens are not proof of a better task outcome.

1. Read the board and align source, model, task, budget, feedback mode, sampling,
   evaluator and concurrency. Compare common elapsed windows and label unmatched
   history as diagnostic. Missing or invalid scores remain distinct from zero;
   use the task's native ranking when identifying a retained best result.
2. Inspect representative early, middle and late trajectory segments, including
   stalls and counterexamples. Record the selection rule and sample denominator.
   Connect actions to changed artifacts, validation and scored snapshots; count
   planning, control reads, tool recovery and actual task work separately. Use
   hidden task/evaluator evidence only after solver and scoring are terminal.
3. Separate candidate causes: task difficulty or strategy, model token throughput,
   control interaction overhead, tool failures, and evaluator/feedback delay.
   Align artifact capture, grading and delivery times with solver activity.
   Compare both wall time and useful progress per token/active work interval where
   measured; a score slope or a token-rate difference alone cannot identify the
   cause. Preserve unknowns when telemetry is absent.
4. Turn the strongest supported cause into a small discriminating intervention
   or ablation within existing authority. Reuse current qualified baselines and
   change one relevant factor where possible; disclose unavoidable confounds.
   Prioritize expected task benefit and technical depth over packet size or PR
   count. If repeated reads are wasteful, verify the consumer's obligations before
   reducing them; a planning or recovery change must still lead to useful work.
5. Report observations, causal hypotheses and validated effects distinctly,
   including regressions and remaining uncertainty. Use
   [loopx-performance-diagnosis](../loopx-performance-diagnosis/SKILL.md) only for
   an evidenced owned-process cost; return its measurements to this task-level
   analysis. Do not substitute profiler hotspots for outcome evidence or require
   profiling to inspect duplicate content and unnecessary interactions.

## Experiment sequence

1. **Read the experiment board before launching or selecting a case.**
   ```bash
   loopx benchmark experiment-board-show --goal-id <GOAL_ID> --format json
   ```
   Inspect baseline, treatment, explore, countability, effort, and insight rows
   before choosing the next arm.

2. **Qualify the source revision before each new run admission.**
   ```bash
   loopx benchmark source-revision-fence \
     --source-checkout <clean-source> \
     --expected-revision <PIN> \
     --observed-reference-revision <OBSERVED_HEAD> \
     --require-admitted --format json
   ```
   The fence fails closed unless the clean pinned source matches the observed
   reference head.

3. **Preview, then preregister or mark the run row when it starts.**
   ```bash
   loopx benchmark experiment-board-upsert --goal-id <GOAL_ID> \
     --row-json <running-row.json> --format json
   loopx benchmark experiment-board-upsert --goal-id <GOAL_ID> \
     --row-json <running-row.json> --execute --format json
   ```
   The running row uses `status=running`, empty `metrics`, and
   `countability={integrity_qualified:false, official_result_present:false,
   score_countable:false}`. Keep the same stable `run_id` for every transition.

4. **Preview and upsert terminal score, countability, effort, and insight.**
   First run integrity qualification. An automated restricted-access match is a
   countable suspicion, not a cheating verdict. After solver and scoring are
   terminal, inspect the real solver trajectory, tool results, and final
   workspace. Pass a compact
   `benchmark_restricted_access_adjudication_v0` only after that review; confirm
   cheating only when restricted material was actually disclosed and causally
   entered a solving or validation decision.

   ```bash
   loopx benchmark experiment-board-upsert --goal-id <GOAL_ID> \
     --row-json <terminal-row.json> --execute --format json
   ```
   The terminal row sets `status=completed`, fills `metrics` (primary metric plus
   guardrails), and updates `countability`. Only mark `score_countable=true` when
   `integrity_qualified=true` and `official_result_present=true`. Fill `effort`
   and set `insight.status` to `complete` after the post-run analysis.

   For non-baseline arms, also reduce the reviewed mechanism facts separately:
   ```bash
   loopx benchmark treatment-continuation-receipt \
     --observation-json <compact-post-run-observation.json> --format json
   ```
   This receipt distinguishes qualified startup from post-start semantic control
   persistence. It is analysis-only and must not change score countability,
   integrity qualification, treatment fidelity, or matched-pair eligibility.

5. **Read matched comparisons before selecting the next arm.**
   ```bash
   loopx benchmark experiment-board-show --goal-id <GOAL_ID> --format json
   ```
   Only claim paired results from `matched_pair_countable` comparisons. Keep
   diagnostic-only explore rows in a separate evidence lane.

## Run-row contract

- `benchmark_id`, `study_id`, `case_id`, `run_id`, `arm_id`, `arm_role`,
  `attempt`, `status`, `observed_at`, `model_id`, `protocol_id`,
  `comparison_protocol_id`, `claim_scope`, `primary_metric`,
  `guardrail_metrics`, `metrics`, `countability`, `treatment_fidelity`,
  `effort`, `insight` are the canonical row fields (`schema_version` =
  `benchmark_experiment_board_row_v0`).
- Baseline rows must use `treatment_fidelity=not_applicable` and cannot name a
  `comparison_anchor_run_id`. Non-baseline rows must name a
  `comparison_anchor_run_id`.
- Metrics are `{"name": {"value": <number>, "unit": <str>,
  "higher_is_better": <bool>}}`; at most 16 entries. The `primary_metric` must
  not also be a guardrail metric.
- `score_countable` requires `status=completed`, `integrity_qualified=true`,
  and `official_result_present=true`. `score=0` is a valid completed result.

## Source, integrity, and artifact boundaries

- `source-revision-fence` is read-only and caller-observed: it performs no
  fetch, install, or launch. It blocks new admissions only.
- `integrity-qualification` reduces private trajectory and runner isolation
  evidence to a compact public-safe receipt (hashes, counts, reason codes).
- Scanner hits for restricted source access or host-boundary escape probes set
  `restricted_access_review=suspected` while keeping the run score-eligible. Use
  `--restricted-access-adjudication-json` for the post-run agent decision; only
  confirmed disclosure plus causal use disqualifies the score.
- `classify-artifacts` classifies benchmark artifact paths without reading them;
  use it before reading or publishing any candidate artifact.
- The solver lane must not read hidden tests, verifier sources or gold answers.
  During solving, official feedback is limited to what the declared run protocol
  releases to that solver. The post-run analyst may read full private evidence
  only after the solver is terminal and scoring is complete.
- `capability bind` selects an external provider implementation for a Goal; it
  is not the activation mechanism for this builtin capability. Todo
  `required_capability` fields remain runtime prerequisites, not product
  capability switches.

## Curate live comparison views

When the user wants a persistent experiment overview, keep a stable per-task
entry point backed by an explicit maintained selection, rather than sending a
new long run-query URL after every restart. Keep task switching and full history
one interaction away. Use the existing board/runtime projections for run state
and the provider's authorized score projection; a display selection must not
become a second source of score, integrity, or countability truth.

- Include the requested baseline families and feedback modes, current experiments,
  and important mechanism ablations. Do not silently reduce baselines to the
  official runner: single-task and native-Goal controls may be essential. If a
  requested baseline is unavailable, say so instead of substituting another arm.
- By default, move superseded or problem-stopped attempts out of the core view,
  while retaining their original traces, scores, retirement reason and replacement
  reference in history. Never select by score or hide a valid low-scoring arm.
  An explicitly requested historical baseline may remain visible, with its actual
  status and incomplete duration labeled; display inclusion is not qualification.
- Keep historical baselines distinct from current experiments. Expose each arm's
  runner setting, source/scorer version, feedback mode, budget and sampling cadence
  through concise labels and accessible details. Mark unmatched versions or budgets
  as diagnostic context rather than implying a causal comparison.
- Compare common elapsed sampling windows. Show each completed score promptly,
  with pending, evaluating, failed and missing points distinguishable; never fill
  missing scores with zero or splice different attempts into one curve. Preserve
  original capture times and terminal samples.
- Reconcile the selection on admission, replacement and retirement while keeping
  the entry URL stable. Maintain campaign-specific identifiers in private operator
  state; put only reusable guidance in the shipped skill.
- Verify the rendered view: requested baselines and active ablations appear,
  retired attempts are reachable through history, and links survive a selection
  update. Review the populated first viewport for readable labels and navigation;
  a correct query alone does not establish a usable comparison view.

## Campaign monitoring and post-run insight

- When a campaign starts and the caller authorizes ongoing monitoring, add one
  `continuous_monitor` todo. Refresh aggregate score/coverage and write
  `benchmark_case_insight_v0` on material scored-case transitions, with bounded
  periodic reviews while the campaign remains active.
- Treat that monitor as an observation lane, not executable delivery. When a
  material poll discovers bounded repository, runner-repair, or experiment work,
  use `quota monitor-poll --material-change --next-agent-todo` with explicit
  `--next-action-kind`, repository, and required capabilities so it creates an
  independent runnable `advancement_task`. An unchanged poll creates no successor
  and spends no delivery quota.
- If the main campaign advancement Todo is waiting for a monitor transition, keep
  it `open` and pair `resume_when=monitor_changed:<monitor-todo-id>` with an
  already-created independent runnable successor. Do not mark the wait `blocked`,
  and do not treat the monitor itself as delivery work.
- Report only public-safe conclusions (countable baselines, countable
  treatments, matched pairs, aggregate primary metric by arm, improved/flat/
  regressed pair counts). Never copy raw private evidence into a user update.
- After a solver stops and scoring completes, read the task, real trajectory,
  final workspace, hidden tests, verifier, and failure/score details; write one
  `benchmark_case_insight_v0` explaining the decisive evidence, why the outcome
  happened, and what LoopX should test next.
- For treatment arms, record whether qualified startup was followed by semantic
  Todo transitions, technical replans, or control closeout. Use `startup_only`
  only when a complete authorized post-run review observed no such transition;
  otherwise absence is `unknown`. Keep terminal settlement separate.
- Do not send a repetitive user update when nothing material changed.
