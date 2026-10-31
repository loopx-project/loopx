# Local benchmark campaign reports

Use this read-only developer script to turn an explicit campaign selection into
Markdown, CSV and JSON. It covers progress, distinct-case incidents, outstanding
reruns and normalized-score comparisons on identical case intersections.

```bash
node --experimental-strip-types scripts/benchmark-campaign-report.ts \
  campaign-input.json reports/campaign
```

Node 22.22.3 or later is required. The JSON report records the input digest.
The command performs no network calls, run admission, cancellation, score
normalization, integrity adjudication or Board mutation. It writes only the
requested output files; keep private inputs and reports outside tracked paths.

This is a developer helper for `benchmark-toolkit`, not another experiment ledger
or provider. The adapter remains responsible for reducing runtime-owned
observations into a minimized projection. The script is the common report
calculation; it has no knowledge of a particular benchmark, model, task suite,
organization or host. It uses TypeScript without adding a Python decision owner.

## Input and ownership

Each declared arm must have exactly one selected run for every declared case.
The caller chooses replacements independently of score and preserves old attempts
in the source ledger. A queued replacement does not inherit its earlier score.

Minimal example:

```json
{
  "schema_version": "benchmark_campaign_report_input_v1",
  "title": "Example campaign",
  "observed_at": "2026-01-01T00:00:00Z",
  "case_ids": ["case-a"],
  "arms": [
    {"arm_id": "baseline", "label": "Baseline"},
    {"arm_id": "candidate", "label": "Candidate"}
  ],
  "selection": [
    {
      "arm_id": "baseline", "case_id": "case-a", "run_id": "run-1",
      "status": "completed", "protocol_id": "protocol-a",
      "score": {
        "value": 40, "normalization_id": "mapping-a",
        "evidence_ref": "receipt:run-1-score",
        "origin": "official", "countable": false
      },
      "exclude_reason": null, "rerun_reason": null
    },
    {
      "arm_id": "candidate", "case_id": "case-a", "run_id": "run-2",
      "status": "running", "protocol_id": "protocol-a",
      "score": null, "exclude_reason": null, "rerun_reason": null
    }
  ],
  "incidents": [],
  "notes": ["No terminal common-case comparison is available yet."]
}
```

`status` reuses the Board vocabulary: `planned`, `running`, `completed`,
`cancelled`, `runner_invalid`. Planned renders as queued; the three terminal
states render as ended. Ended does not imply correctness, score presence or
qualification. Completed results may legitimately have no score.

Each score is already normalized to 0–100 by an authoritative task adapter.
`normalization_id` identifies the mapping and its implementation revision.
Mappings must match within each compared case; a common numeric unit alone is
insufficient. Non-finite and out-of-range numbers are rejected. Unknown scores
are null, never zero. An explicit campaign policy can supply
`origin=authorized_failure_zero`, value zero and an evidence reference;
such a policy value cannot claim official countability.

Each selection may also carry a minimized `effort` projection with
`duration_ms`, `token_count`, `model_request_count`, and `evidence_ref`.
Unknown numeric fields remain null; an effort object with every metric unknown
is rejected. The report shows both totals and per-metric coverage, so a partial
projection cannot silently turn missing cost into zero or support an efficiency
claim across arms with different coverage.

Optional `score_samples` preserve a run's normalized score/time series. Every
sample has strictly increasing `elapsed_ms`, a finite 0–100 `value`, the
`normalization_id`, and an `evidence_ref`. JSON flattens the samples with their
arm, case, and run identities for plotting. The helper does not invent missing
points or calculate a cross-case mean curve from changing sample coverage.

`countable` projects existing score authority; the report does not grant it.
`exclude_reason` removes a known invalid or unresolved result from comparisons.
Cancelled runs and live runs are always excluded. A failed run can retain a
usable observed score; the adapter decides whether the declared analysis permits
that score and must disclose this in notes. Failed-run scores never become
countable official results.

Incidents have `arm_id`, `case_id`, `run_id`, `fault`, `initial_attempt` and
`evidence_ref`. Their vocabulary is report-local:
`product`, `infrastructure`, `scoring`, `operator_cancelled`, `unknown`.
The adapter owns attribution; the report never infers it from exception prose.
Repeated incidents count once per case/category. Categories may overlap.

`rerun_reason` is null when no rerun is outstanding, otherwise one primary
incident category, `comparison_change`, or `integrity_review`. It includes
reruns already in progress. These are report-local annotations, not scheduler
commands or persisted run states. Keep secondary issues in the source ledger.
Notes must name incomplete history coverage and unresolved attribution.

## Terminal execution evidence

An optional `execution_audit` separates execution outcomes from fault ownership
and score availability. Each row has `arm_id`, `case_id`, `run_id`,
`initial_attempt`, `outcome`, `attribution`, `goal_verdict`, and nonempty
`evidence_refs`. One row describes the final reconciled state of one attempt;
each arm/case has at most one initial attempt. The caller retains Session-level
recovery history. An error recovered within that same attempt is not a final
runtime failure; a replacement attempt never erases the original failure.

- `outcome`: `normal_end`, `runtime_failure`, `budget_exhausted`,
  `operator_cancelled`, `unknown`, or `running`.
- `attribution`: `product`, `service`, `provider`, `benchmark`, `unknown`, or
  `not_applicable`. Runtime failures require an explicit or unknown attribution;
  other outcomes use `not_applicable`. Exception text alone is not ownership proof.
- `goal_verdict`: `satisfied`, `failed`, `needs_revision`, `unavailable`, or
  `not_applicable`. A normal Goal end needs `satisfied`; a workflow without a
  Goal can use `not_applicable`. Idle, end-turn, process exit zero and a valid
  artifact score do not establish Goal satisfaction.

The first-attempt table reports unknown and unreviewed cases alongside confirmed
normal endings, runtime failures, budgets and operator censoring. Missing audits
render as unreviewed, not verified zeros. Historical incident counts are only
observed records and never imply complete attribution coverage. No failure-rate
comparison is computed: shared case/attempt denominators, censoring, exposure,
deployment versions and observation coverage must be qualified independently.
The audit does not change score selection, normalization or countability.

## Reading the outputs

- Progress has a fixed case denominator for each arm. Score coverage is a
  separate count; history cannot inflate progress.
- Historical failure and pending reruns answer different questions. Historical
  failure remains visible after a successful replacement.
- Each pair and the all-arm comparison use exactly the same case list for
  every mean in that row. JSON records the list, digest, exclusions, score
  difference and win/tie/loss counts. An empty intersection has null means.
- Different rows can have different intersections. Do not rank all arms by
  comparing numbers from different rows.
- `qualified_common_n` counts shared results carrying existing countability,
  not proven causal matched comparisons. Protocol, source, resource, feedback
  and treatment fidelity still need the toolkit's matched-comparison checks.
  The helper labels every comparison descriptive and never upgrades integrity.
- CSV provides one row per selected case/arm with score provenance and rerun
  reasons, effort fields, and sample count. Spreadsheet formula prefixes are
  neutralized. Full score/time samples remain in JSON.

Validation:

```bash
node --experimental-strip-types --test \
  tests/control_plane_ts/benchmark_campaign_report.test.ts
```

The suite exercises the real CLI and missing versus zero scores, replacements,
unique-case incidents, incompatible normalization, known-invalid exclusions and
qualification contradictions.
