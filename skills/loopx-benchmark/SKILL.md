---
name: loopx-benchmark
description: Use when a LoopX-managed goal runs, tracks, scores, or analyzes a benchmark experiment through benchmark-toolkit, including experiment-board rows, solver arms, integrity qualification, matched comparisons, or case insights. Do not use for casual benchmark discussion, ordinary software microbenchmarks, or eval mentions without LoopX experiment state.
---

# LoopX Benchmark Workflow

Use this skill for a LoopX-managed benchmark experiment. The builtin
`benchmark-toolkit` capability owns provider-neutral experiment state and
integrity boundaries. This packaged skill is its task-triggered Agent playbook.

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
- **Analyze a terminal run:** wait until solving is terminal and scoring is
  complete before reading hidden evaluator evidence or writing a case insight.

For a generic library microbenchmark or an eval with no LoopX Goal/board, use
the task's normal tools instead of imposing this workflow.

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

   For runners that invoke multiple agent segments, also rotate fixed-path
   transient artifacts before every segment, mint a fresh opaque nonce, and
   qualify the returned receipt against that nonce and the observed segment
   time window before accepting its result:

   ```bash
   loopx benchmark segment-receipt \
     --expected-segment-nonce <RUNNER_NONCE> \
     --receipt-segment-nonce <RECEIPT_NONCE> \
     --segment-started-at <ISO_TIME> \
     --receipt-written-at <ISO_TIME> \
     --segment-ended-at <ISO_TIME> \
     --prior-receipt-nonce <PRIOR_NONCE> \
     --require-qualified --format json
   ```

   Missing, wrong-segment, outside-window, or replayed receipts fail closed.
   The runner still owns cleanup, nonce generation, process execution, retries,
   and the decision to launch another segment.

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
- The solver lane must not read hidden tests, verifier sources, gold answers, or
  official feedback during the solving phase. The post-run analyst may read full
  private evidence only after the solver is terminal and scoring is complete.
- `capability bind` selects an external provider implementation for a Goal; it
  is not the activation mechanism for this builtin capability. Todo
  `required_capability` fields remain runtime prerequisites, not product
  capability switches.

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
