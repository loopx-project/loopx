# Runner defaults and controlled ablations

New LoopX executions default to **loopx-planned** task entry and replanning
after **6 settled effective work Turns**, matching the product default. This applies to
Harbor heartbeat, Turn and LoopX Goal modes, and EdgeBench heartbeat-resume and
heartbeat-explore profiles. Planned entry replaces the seeded-todo default; pass
`task_entry: seeded-todo` / `--task-entry seeded-todo` to retain the previous
entry. Explicit settings, archived study configs and existing attempts retain
their original meaning. Official, single and native-goal profiles do not start
LoopX planning. The task-entry value recorded for these non-LoopX profiles is
`seeded-todo`, an inert compatibility value.

Planning decomposes the authorized task before execution; it is not proof that
planned entry improves scores. Treat this default as an operational choice and
use matched repetitions to test its effect.

## Defaults versus study settings

| Setting | New-run behavior | Explicit study choice |
| --- | --- | --- |
| Task entry | LoopX modes: loopx-planned | Record planned or seeded for every arm |
| Explore | Off in heartbeat-resume; on in heartbeat-explore | Keep resume as the reference |
| Turn envelope | Off | Enable only in its ablation |
| Replan cadence | 6 settled effective work Turns | Use explicit completed-Todo cadence or another Turn count for ablation |
| Iteration context | Harbor: fresh; EdgeBench heartbeat: resume | Freeze the provider and context within a comparison |
| Evaluator feedback | EdgeBench: best-only (strict new-best snapshot notifications) | Native and blind remain explicit controls; freeze feedback mode for every comparison |
| Model and effort | Caller-selected | Pin both; never infer them from a profile name |
| Time and sampling | EdgeBench task defaults in [task settings](../edgebench/README.md#trial-timeouts); explicit flags override | Pin resolved seconds in the study manifest |

`replan_after_turns` counts settled effective work turns through the shared
control-plane contract, not tool calls or idle heartbeat wakes. The threshold is 6 by
default, independent of task entry and TurnEnvelope. This is a deterministic
threshold, not a random per-wake probability; other replan triggers may act sooner.
An accepted replan resets the same Agent's periodic and repeated-progress history
windows. A failed or unaccepted replan does not; current acceptance gaps remain
independent triggers. Six reduces periodic interruptions relative to the previous
default of three, but does not promise lower total replan time or better scores.
Task timeouts bound attempts, not a requirement to consume every second.

## Recommended small study

Start with a planned Resume reference and vary one factor per comparison.
These are recommendations, not automatically launched experiments.

| Arm | EdgeBench flags relative to the reference | Question |
| --- | --- | --- |
| Reference | `--worker heartbeat-resume --task-entry loopx-planned --replan-after-turns 6` | Planned entry with effective-turn replanning |
| Seeded | Replace only `--task-entry` with `seeded-todo` | Does initial task decomposition help? |
| Explore | Replace only `--worker` with `heartbeat-explore` | Are recorded evidence and subsequent route choices useful? |
| Short envelope | Add `--turn-envelope` | Does progressive context loading reduce overhead without losing decisions? |
| Todo cadence | Replace `--replan-after-turns 6` with `--replan-after-todos 3` | How do units and thresholds change interruptions? This is a combined ablation. |
| No LoopX | `--worker official`; omit LoopX-specific flags | What is the net effect of the whole LoopX treatment? |

The no-LoopX comparison changes several mechanisms; do not attribute its delta
to one component. Native, blind and best-only feedback are a separate factor: repeat
selected matched arms within each feedback setting rather than mixing them.
Prioritize entry and cadence first; enable the other arms after verifying that
planning, settlement and evaluation work. For Explore, inspect whether evidence
was written, retrieved and adopted; an enabled hook with an empty graph is not
full mechanism use. For the envelope, check deferred context retrieval as well
as initial prompt size.

A Portfolio reference command after source, image and credential preflight:

```sh
python -m benchmark.edgebench.run \
  --task portfolio_risk_calibration --tasks-dir "$TASKS_DIR" \
  --log-dir "$RUNS_DIR" --run-id "$NEW_ATTEMPT_ID" \
  --worker heartbeat-resume --task-entry loopx-planned \
  --replan-after-turns 6 --feedback best-only \
  --model "$MODEL" --effort xhigh --timeout 43200 --eval-interval 300 \
  --judge-url "$JUDGE_URL" --api-proxy-url "$API_PROXY_URL"
```

Use a fresh attempt id for each arm; set the runtime's documented source pins,
credentials and API proxy before launch. This example fixes the comparison
budget and sampling explicitly rather than relying on changing defaults.
For Lean, use `--task lean_analysis_proofs --eval-interval 1800` and the same
explicit total budget for all arms. Evaluator concurrency is an independently
recorded service setting, not inherited from another task.

Harbor uses the same task-entry owner. In the agent kwargs, set
`execution_mode: heartbeat`, `task_entry: loopx-planned`,
`replan_after_turns: 6`, `iteration_context: resume` and `turn_envelope: false`
for an equivalent mechanism reference. For the previous benchmark default, set
`replan_after_turns: 3`.
For Todo cadence, remove
`replan_after_turns` and set `replan_after_todos: 3`; the two cadence fields are
mutually exclusive. The named Explore profile in this matrix is EdgeBench-specific; do not assume
an equivalent Harbor kwargs switch. Keep the dataset, provider and validation
configuration unchanged.

## Readback and interpretation

Inspect resolved runtime/profile receipts, installed source revisions and the
first planning/seed checkpoint before scoring a treatment. Pin task, evaluator,
images, model, effort, budget and sampling schedule. Retain missing points as
missing; compare common elapsed-time windows, and keep each terminal snapshot.
Record early closure, planning cost, useful work turns and evidence adoption
alongside score. A single stochastic run or a cross-version historical delta
cannot isolate an individual fix.

Old attempts are immutable references. A rerun after multiple fixes measures
the combined revision change unless each fix has a matched control. Roll back
the entry default through explicit seeded entry and the cadence default through
`--replan-after-turns 3` / `replan_after_turns: 3` in a new attempt. Do not rewrite
old receipts or change an active worker. None of these settings grants model,
submission, credential or launch authority.
