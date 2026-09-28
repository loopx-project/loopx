# Progress-Review Sentinel

[中文](README.zh-CN.md)

The progress-review sentinel lets a Goal consume **typed drift receipts** that
an optional, external, bounded reviewer writes after each captured work
transition. It is default-off. In `shadow` the core only records and displays
receipts. In `assist` a run of consecutive completed drift receipts becomes the
**existing** `autonomous_replan_obligation`; nothing else changes.

It exists because the typed repeat fuse is blind by construction to one
pattern: an Agent that keeps declaring `advanced`, keeps changing its
`hypothesis_id`, and keeps the tests green while its scoped file delta only
renames identifiers or reorders fields. That work is caught today only by the
periodic review after 20 durable runs.

## What the core does and does not do

| The core | Never |
| --- | --- |
| Reads receipts through one strict schema, `progress_review_receipt_v0` | Calls a model, reads a raw delta, or imports the observer package |
| Joins receipts to run rows by `turn_instance_id`, else by `(generated_at, agent_id, todo_id)` | Overwrites or supplements the Agent's own `progress_observation` |
| Counts only `completed` receipts whose selected drift signal is `True` | Counts `unknown`, `abstained`, `failed`, `stale` or missing receipts |
| Stops the streak at an acknowledged autonomous replan and re-arms | Pauses turns, opens user gates, or settles Goal acceptance |
| Binds the newest typed `progress_observation` in the window as the obligation's `progress_baseline` and carries every distinct claim of the window as `progress_window`; the shared outcome policy refuses an acknowledgement that replays any of them or only renames identifiers over their evidence ids | Lets the observer or its model acknowledge, or accepts a renamed or replayed identifier as a pivot |
| Keeps a formed obligation open while newer transitions are unevaluated, failed, abstained, stale or unattributable, and reports how many | Treats missing evaluation as evidence that the drift was handled |
| Skips neutral bookkeeping rows (quota spend/void) like the existing replan policy | Treats a bookkeeping row as a gap or as progress |
| Counts only receipts bound to the revision the Goal owner pinned, and reports when newer receipts are bound elsewhere | Follows the observer basis on its own; the pin is manual |

The typed repeat fuse keeps precedence. A receipt streak only adds evidence
when that fuse is quiet.

## Policy

```bash
loopx configure-goal --goal-id <goal-id> --progress-review-mode shadow --execute
loopx configure-goal --goal-id <goal-id> --progress-review-mode assist \
  --progress-review-signal noul --progress-review-drift-threshold 2 --execute
loopx configure-goal --goal-id <goal-id> --clear-progress-review-configuration --execute
```

| Field | Values | Meaning |
| --- | --- | --- |
| `mode` | `off`, `shadow`, `assist` | `off` loads nothing; `shadow` records and displays; `assist` may raise the obligation |
| `signal` | `noul`, `choice` | Which receipt judgment pair counts as drift |
| `drift_threshold` | 2–20 | Consecutive completed drift receipts before an obligation |
| `contract_revision` | sha256 or empty | The observer basis revision receipts must be bound to; printed by `loopx-jev drift init`. Required for `assist`. The pin is manual: receipts bound to other revisions are never counted, changing the basis does not retire earlier receipts by itself, and status reports `rebind_hint: newer_receipts_under_unpinned_revision` when the newest receipt is bound elsewhere |

The policy lives at `control_plane.progress_review` in the goal registry and is
visible in `loopx configure-goal --goal-id <goal-id>` under `feature_summary`
and in the Dashboard capability editor. A malformed block fails closed to `off`.

## Receipts

Receipts are written to
`<runtime-root>/goals/<goal-id>/progress-review/receipts/<event-id>.json` by
the observer in the optional `loopx-jev-pilot` distribution
([`packages/loopx-jev/DRIFT_SHADOW.md`](../../../packages/loopx-jev/DRIFT_SHADOW.md)).
Each receipt carries only typed fields:

- identity: `goal_id`, `event_id`, `evidence_id`, `contract_revision`, `sequence`,
  and the run's `turn_instance_id`, `generated_at`, `agent_id`, `todo_id`;
- `status`: `completed`, `abstained`, `failed`, `not_evaluated`, `stale`;
- `judgments.choice`: `relation` and `increment` labels or null;
- `judgments.noul`: probabilities for `behavior_change`, `serves_acceptance`,
  `evidence_increment`, or null;
- `drift_signal.noul` and `drift_signal.choice`: `true`, `false` or null;
- `timing_ns`, `usage`, `label_probability_threshold`, `recorded_at`.

`sequence` is the writing observer's local counter and restarts at zero when
`drift init` creates a new observer state. The core never compares sequences
across observers: receipts are ordered by the run's `generated_at`, then
`recorded_at`, then `sequence` (`progress_review_receipt_order_key`), in the
loader, in the load limit, in the newest-revision check and in the join of two
receipts for one transition.

The drift signals follow rule `progress_review_signal_rule_v1` with the label
threshold `t`; the core recomputes them from the typed judgments when it reads a
receipt and rejects any receipt whose booleans disagree:

- `noul`: `P(serves_acceptance) ≤ 1−t` **and** `P(evidence_increment) ≤ 1−t` is
  drift; either probability `≥ t` is not drift; anything else is null.
  `behavior_change` is recorded but not gating.
- `choice`: `relation = off_goal` **and** `increment = no_new_evidence` is
  drift; `on_goal`, `necessary_prerequisite` or `new_evidence` is not drift;
  anything else is null.

Both questions are asked about the change between checkpoints, not the after
state as a whole, so churn on a file that already satisfies acceptance is drift,
while documentation, a negative finding or a prerequisite test that serves a
criterion or adds evidence about it is not.

Receipts found by `turn_instance_id` must name the same nonempty Agent and
exactly the same Todo, including absence on both sides for unbound work. Missing
identity is not a wildcard. Contradictory Agent/Todo claims for one Turn, in
receipts or retained retries, make it unattributable; clocks cannot resolve an
identity conflict. Later evaluations of the same identity still use recency.
Only genuinely absent Turn identity permits the `(generated_at, agent_id, todo_id)`
fallback, which must be unique on both the run and evidence sides. Invalid or
conflicting direct/settlement Turn IDs cannot fall back. ACKs apply only to the
named Agent lane; anonymous rows cannot acknowledge it or supply its progress
baseline. A valid lane ACK still applies on neutral bookkeeping rows.

This tightens the original stage-0 `assist` compatibility: incompletely bound
historical receipts remain visible in shadow/status but cannot form or clear an
obligation. Existing off behavior, model requests, manual revision pins and typed
replan outcomes are unchanged. Every captured transition is one of three things: **drift**
(completed, selected signal `true`, pinned revision), **on-goal** (completed,
signal `false`) or **unevaluated** for one typed reason (`pending`, `failed`,
`abstained`, `stale`, `undecided`, `missing`, `unattributed`,
`identity_conflict`, `other_revision`, `not_evaluated`). Formation is
conservative: an obligation needs `drift_threshold` consecutive drift
transitions with no unevaluated transition between them. Persistence is not:
once formed, newer unevaluated transitions neither extend nor dissolve the
obligation, and their count is reported as `unevaluated_transitions` on the
trigger. Only an acknowledged replan or a newer completed on-goal verdict ends
it; the Goal owner can also set the mode back to `shadow` or `off`. Receipts
bound to a revision other than the pinned one are unevaluated history and
never counted. The scan covers the run history the Goal keeps
(`latest_runs`), so an obligation can only be as old as that window.

## Discharge

An `assist` obligation is discharged only the way every autonomous replan
obligation is: the Agent's next `refresh-state` must carry typed evidence that
the shared outcome policy (`work_item.replan_semantics`) accepts for this
source. The obligation binds the newest typed progress observation in the
window, evaluated or not, as `progress_baseline`, and carries every distinct
claim of the window as `progress_window`, so no claim already on record can
acknowledge. Against that window the policy accepts:

- a new surface, hypothesis or probe family **that cites at least one evidence
  id absent from the baseline and from every claim in the window**; renaming
  identifiers over those evidence ids is refused with
  `progress_identity_without_new_evidence`, and replaying a claim already made
  in the window is refused with `progress_observation_replayed`. Returning to an
  earlier hypothesis on genuinely new evidence is a typed pivot and is accepted;
- a new concrete blocker, or a coverage-backed terminal state;
- a fresh evidence-linked vision path (`continue`, `no_change` or `replan`)
  with an acceptance summary and evidence refs, so an Agent that reviews the
  evaluated work and keeps its plan has a typed exit.

Re-submitting the bound observation, or the same hypothesis with new evidence
ids, is refused. The trigger only fires when the window carries a typed
observation to bind; Agents that do not write typed observations get receipts
and status, never an obligation. The heartbeat's requirements projection names
both exits: `cli_semantic_args` for the progress path and
`alternative_cli_semantic_args` for the vision path.

## What you see

`loopx status --format json` adds `external_progress_review` to a goal item and
its `project_asset` whenever the policy is not `off`: receipt counts by status,
drift counts per signal, and the latest receipt's typed judgments. In `assist`,
a qualifying streak appears as an `autonomous_replan_obligation` whose trigger
`kind` is `external_progress_review_drift`, with `frontier_identity`
`progress_review:<evidence-id>`, a P1 todo action, and the usual
`required: true`, `stop_condition` and ack contract. The heartbeat prompt
already instructs Agents to obey that obligation and acknowledge it with a
typed replan.

## Verifying the differential

`packages/loopx-jev` ships a comparison harness:

```bash
loopx-jev sentinel compare \
  --matrix packages/loopx-jev/tests/fixtures/sentinel/matrix.json \
  --responses packages/loopx-jev/tests/fixtures/sentinel/responses \
  --output /tmp/sentinel-comparison.json
```

For every recorded sequence it reports the first round at which the typed
repeat fuse would fire (never, within the sequence, for self-declared
advancement), the first round at which each receipt signal flags drift, and the
false flags on sequences whose gold label is on-goal. Without `--live` it
replays committed provider responses, so the numbers reproduce in CI without a
key. `python3 examples/progress-review-sentinel-smoke.py` runs the same replay.

## Recorded differential

The committed live recording of the 16-sequence matrix (question set
`scoped-progress-sentinel-v2`, `jev-1.13.0`, 35 rounds, every round
self-reporting `advanced`; one answer rejected by the strict decoder):

| | Typed repeat fuse | `noul` receipts | `choice` receipts |
| --- | --- | --- | --- |
| Drift sequences flagged at their gold round | 0/9 | 9/9 | 5/9 |
| Reaching the `assist` obligation at threshold 2 | 0/9 | 9/9 | 2/9 |
| Real on-goal upstream commits falsely flagged | 0/7 | 0/6 evaluated | 0/6 evaluated |
| Real on-goal commits with no verdict (failed closed) | — | 1/7 | 1/7 |

Purely cosmetic sequences were flagged at round 1, mixed sequences at their
drift round; a second independent live run reproduced every outcome. The v2
wording was revised after the earlier recording missed post-implementation
churn on these constructed sequences, so they are no longer held-out for the
wording; the seven real commits were not used to tune anything. See the
[operation guide](../../../packages/loopx-jev/DRIFT_SHADOW.md) for the full
table, latency, variance and what remains unproven.

## Adoption path

The observer lives in the optional `loopx-jev-pilot` distribution; the policy
lives in the Goal registry. Adopt it one Goal at a time:

1. **Install the observer** next to the LoopX checkout:
   `uv pip install -e '.[test]' -e packages/loopx-jev`. Put `TYPESAFE_API_KEY`
   in the environment; nothing in the repository or registry stores it.
2. **Bind one Goal.** Write a basis JSON with the Goal's objective, acceptance
   criteria and optional evidence files, list the files the Agent is expected to
   change, and run
   `loopx-jev drift init --state-dir <dir> --config <config> --workspace <repo> --basis <basis> --runtime-root <runtime> --path <file> ...`.
   Note the printed `contract_revision`.
3. **Wrap the real refresh.** Where the Agent (or its host) runs
   `loopx refresh-state`, call
   `loopx-jev drift refresh --state-dir <dir> --config <config> -- <the same loopx arguments>`
   instead. The original command, stdout and exit code are unchanged.
4. **Run the consumer separately**, for example
   `loopx-jev drift drain --state-dir <dir> --config <config> --watch-seconds 600`.
   It writes one receipt per evaluated event under the Goal runtime.
5. **Turn on `shadow`:**
   `loopx configure-goal --goal-id <goal-id> --progress-review-mode shadow --execute`.
   `loopx status --format json` now shows `external_progress_review` for the Goal.
6. **Label what you see.** `loopx-jev drift status --state-dir <dir>` lists
   receipts; `loopx-jev drift label --state-dir <dir> --event-id <id> --truth drift|on_goal|unknown`
   records your judgment and the status shows agreement per signal.
7. **Only then consider `assist`:** pin the revision from step 2 with
   `--progress-review-contract-revision <sha256>` and set `--progress-review-mode assist`.
   Consecutive drift receipts now raise the existing replan obligation the Agent
   must acknowledge. The pin does not follow the basis: after `drift init` is
   re-run, status shows `rebind_hint` until you pin the new revision.
   `--clear-progress-review-configuration` returns to `off`.

## Readiness ladder

| Stage | What is allowed | Entry evidence | Owner of the decision |
| --- | --- | --- | --- |
| **0 · Default-off preview (this PR)** | Capability registered, `shadow` and pinned `assist` available, nothing on by default | Green CI on the exact head; review findings fixed deterministically; differential reproducible from committed recordings; bilingual docs; no new authority | Maintainer merge; control-plane changes are never self-merged |
| **1 · Shadow on real Goals** | `shadow` on two or more long-running Goals | At least two weeks or forty receipts per Goal, labelled with `drift label`; the false-flag rate and lead time over the periodic review reported by the Goal owner; at least one real drift labelled before round 20 | Goal owner |
| **2 · Assist on one pinned Goal** | `assist` with a pinned revision on one Goal | Stage 1 owner judges the false-flag cost acceptable; the Agent's acknowledgements are read: did it change its slice, how many rounds until acknowledgement, how many obligations were wrong | Goal owner plus maintainer |
| **3 · Broader defaults, escalation, pause** | Not provided by this capability | A separately authorized intervention study showing reduced wasted work against the unchanged workflow | Project decision |

Stage 0 is what this pull request asks for. Stages 1 and 2 are operator
choices made with the tool; stage 3 is out of scope here. Stopping at any stage,
or keeping the existing workflow, is a valid outcome.

## Boundaries

Escalation (a user gate after an ignored obligation) and pause remain future
work and are not granted here. The observer's prediction quality is a separate
question from this integration: the committed differential shows what one
question set does on one frozen matrix, not what it will do on your Goals.
