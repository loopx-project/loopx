# Domain Capability Packs

Status: design target.

Principle: LoopX defaults to a generic control plane. It should
guarantee state, evidence, boundaries, handoff, and validation routing before it
tries to make domain judgments. Domain packs can make LoopX smarter, but
they must be explicit about capability and permission.

## Why This Exists

Long-running projects often start to look domain-specific. An ML experiment
agent, a benchmark agent, a deployment agent, and a documentation agent all need
durable state and compact evidence, but only some of them should know about
primary metrics, dataset windows, training jobs, or promotion decisions.

The default control plane should therefore do two things:

- recognize that a domain pack might help;
- stop before enabling domain-specific autonomy until the registry or owner
  records that permission.

This avoids a quiet escalation from "track my goal" into "interpret and launch
domain work." It also keeps LoopX useful for ordinary engineering todos
that do not have experiment boards, primary metrics, guardrails, or production
job handles.

## Domain Lanes Over One Control Plane

The agent-native Kanban metaphor provides a practical placement rule:

| Surface | Owner | Example |
| --- | --- | --- |
| Generic card lifecycle | LoopX Kernel | claim, gate, monitor, defer, complete, supersede, quota, recovery |
| Domain lane and stage | Capability Pack | Issue Fix review stage or experiment evaluation stage |
| External fact and effect | Provider | check status, review state, metric result, job readback |
| Visible board column | Projection | runnable, waiting, monitoring, review, done |

A domain lane is a read model over domain state plus accepted Kernel state. It
may explain that an issue moved from patching to CI review, or that a hypothesis
moved from execution to holdout evaluation. It does not own the underlying
todo, claim, gate, quota, or schedule.

This keeps two different kinds of change separate:

- adding a new domain stage usually changes the pack's Domain State schema,
  transition proposal, and projection;
- adding a new cross-domain lifecycle rule changes the Kernel only when the
  rule has a provider-neutral contract and real callers outside one pack.

Do not add `ci_review`, `holdout`, or `promotion_candidate` to a generic Kernel
status enum merely to draw a useful board. The pack should derive those labels,
then submit any consequential claim, gate, monitor, successor, or closeout
through the existing typed transition boundary.

## Contract Shape

`domain_pack_contract_v0` is a registry-owned goal-boundary extension:

```yaml
domain_packs:
  ml_experiment:
    enabled: false
    autonomy: suggest_only
    allowed_actions:
      - observe_external_jobs
      - ingest_metrics
      - classify_results
      - propose_replan
    capability_requirements:
      - external_evidence_poll
    primary_metric_authority: explicit_board_only
    auto_launch_requires: board_selected_and_verified
```

Allowed `autonomy` values:

| Value | Meaning |
| --- | --- |
| `suggest_only` | Detect signals and recommend enabling a pack. Do not write domain conclusions. |
| `advisory` | Write compact results, hypotheses, and replan proposals. Do not launch, stop, restart, or sync production code. |
| `delivery` | Perform authorized domain actions only inside the goal boundary, quota guard, capability gate, and validation/writeback lifecycle. |

Default behavior:

- `enabled=false`: status may say "this goal looks like an ML experiment; enable
  the ml_experiment pack if that is intended."
- `autonomy=suggest_only`: the pack may explain why it would help, but it does
  not write experiment results or replan decisions.
- `autonomy=advisory`: the pack may write compact evidence, structured result
  summaries, and proposed replans, but it still cannot launch jobs.
- `autonomy=delivery`: launch/stop/restart/sync actions require explicit goal
  boundary authorization, fresh quota, selected and verified board authority,
  preflight validation, and compact writeback.

The first enablement should be a visible registry or owner decision. Later
turns may use the enabled pack autonomously only within that recorded goal
boundary.

## Generic Capabilities

These capabilities are not ML-specific and should be available in the default
LoopX control plane.

| Capability | Default Role |
| --- | --- |
| `observable_artifact_handle_v0` | Describe external jobs, CI runs, benchmark attempts, evaluations, deploys, or other long tasks with an observable handle, allowed poll command, artifact refs, terminal markers, and read boundary. |
| `validation_surface_map_v0` | Require each executable todo to name how it will be validated: code tests, document scans, external evidence, review packet, or blocker writeback. |
| `result_event_v0` | Record terminal state, evidence pointer, validation status, outcome classification, and next action without assuming a domain metric schema. |
| `reward_style_hint_preview` | Provide read-only candidate-ranking hints from compact reward/todo evidence. Hints may explain ordering but cannot override user gates, claims, scopes, capabilities, workspace guard, or goal boundary. |
| `handoff_packet_v0` | Package objective, current state, validation, risk, next action, and stop condition for a human or another agent. Domain packs may add fields, but handoff itself is generic. |
| `status_frontstage_plain_language_projection` | Explain what happened, what is blocked, what comes next, and what user action is needed in ordinary language. Domain packs may add specialized cards. |

These should stay safe for any project type. A project-specific adapter decides
what handles, refs, and validations it can expose, but the protocol shape is
generic.

## ML Experiment Pack

The ML experiment pack is a domain capability pack. It should stay default-off
because it carries assumptions that ordinary engineering work may not share.

Pack-controlled capabilities:

| Capability | Why It Is Domain-Specific |
| --- | --- |
| `ml_experiment_result_v0` | Uses primary metric, baseline delta, guardrail metrics, decision windows, aligned evaluation, and train-only guardrail concepts. |
| `dataset_window_contract_v0` | Models date/hour coverage, matched windows, missing-hour handling, fairness labels, and sample comparability. |
| `hypothesis_ledger_v0` | Tracks mechanism family, route, positive and negative evidence, near-neighbor exclusions, retirement rules, and portability. |
| `experiment_replan_v0` | Chooses next experiment batches, exploration/exploitation allocation, quota use, promotion, and retirement proposals. |
| `external_training_job_adapter` | Polls external training or evaluation jobs, ingests metrics, checks workspace markers, terminal markers, and lineage. |
| `auto_launch_experiment` | Launch/stop/restart/sync actions are delivery authority, not generic control-plane behavior. |
| `dreaming_experiment_proposal_v1` | Generates candidate mechanisms, research directions, and archive suggestions from experiment history; useful, but advisory until reviewed. |

The pack should never infer that a metric board, dataset path, training system,
or launch command is authorized merely because text in a todo looks familiar.
Authority comes from the registry, goal boundary, and compact owner decision.

### Quick Trial

Algorithm experiment users can trial the advisory shape without enabling
delivery authority:

```bash
loopx ml-experiment preview --format json \
  --experiment-id exp_preview_v1 \
  --primary-metric offline_auc \
  --baseline-value 0.421 \
  --candidate-value 0.437 \
  --guardrail-status clean \
  --train-window train_2026w24 \
  --eval-window eval_2026w25 \
  --hypothesis-id h_route_mix_v1 \
  --mechanism-family "candidate retrieval mix" \
  --route route_mix \
  --positive-evidence offline_eval_delta_positive \
  --next-candidate holdout_eval
```

The preview writes no state and launches nothing. It returns compact public-safe
`ml_experiment_result_v0`, `dataset_window_contract_v0`,
`hypothesis_ledger_v0`, and `experiment_replan_v0` sections with
`launch_actions_enabled=false` and `production_actions_enabled=false`.
Use artifact aliases instead of raw logs, private paths, internal links, or
credential-bearing metric dumps.

### Volc/MLP Task Packet

For external training/eval systems, LoopX can also render a compact
`volc_mlp_task_packet_v0` fact packet. This is an observation and handoff
format, not a launcher. It captures task identity, task state, train/eval
windows, code/model lineage, metric artifact aliases, and the allowed polling
contract. It deliberately does not store raw command lines, environment dumps,
credentials, production paths, workspace paths, or private logs. If a caller
passes a raw path or URL as a workspace or metric reference, LoopX emits an
irreversible `redacted:<digest>` handle instead.

```bash
loopx ml-experiment volc-task-packet --format json \
  --task-id task-candidate-0 \
  --task-name external_slice_cross_screen \
  --state Running \
  --priority 4 \
  --retried-times 0 \
  --train-window 20251002-20260501 \
  --eval-window 20260501-20260508 \
  --code-ref codex/example-feature-cross@abc1234 \
  --model-name candidate_model_abc1234 \
  --mechanism-family explicit_context_item_crosses \
  --source-task-id task-baseline-0 \
  --metric-ref metrics/eval-summary.json \
  --primary-metric target_slice_auc \
  --guardrail-metric overall_auc
```

The packet keeps `launch_actions_enabled=false` and
`production_actions_enabled=false`. A project-specific adapter may use it as
durable compact evidence, but actual create/stop/restart/sync actions still
require explicit delivery authority, quota, preflight verification, and
writeback.

When the task reaches material evidence, the agent can render a
`volc_mlp_result_ledger_v0` row. This is the benchmark-ledger layer on top of
the task packet: it records same-window metric deltas, guardrail state,
train-metric-as-guardrail policy, failure attribution labels, and the compact
promotion/no-promotion route. It is useful for long-running model iteration
because it prevents agents from repeatedly retrying weak near-neighbor
experiments after a no-promote result, while still preserving enough public-safe
evidence to replan.

```bash
loopx ml-experiment volc-result-ledger --format json \
  --experiment-id external_slice_screen \
  --task-id task-candidate-1 \
  --task-name external_slice_cross_screen \
  --state Completed \
  --train-window 20251002-20260501 \
  --eval-window 20260501-20260508 \
  --code-ref codex/example-feature-cross@abc1234 \
  --model-name candidate_model_abc1234 \
  --mechanism-family explicit_context_item_crosses \
  --primary-metric target_slice_auc \
  --baseline-value 0.731 \
  --candidate-value 0.742 \
  --guardrail-status clean \
  --guardrail-metric guardrail_slice_a_auc \
  --guardrail-metric guardrail_slice_b_auc \
  --positive-evidence same_window_target_slice_auc_up
```

For failed startup/eval attempts, omit metric values and pass compact failure
labels instead:

```bash
loopx ml-experiment volc-result-ledger --format markdown \
  --experiment-id external_slice_screen \
  --task-id task-candidate-0 \
  --task-name external_slice_cross_screen \
  --state Failed \
  --train-window 20251002-20260501 \
  --eval-window 20260501-20260508 \
  --code-ref codex/example-feature-cross@abc1234 \
  --model-name candidate_model_abc1234 \
  --mechanism-family explicit_context_item_crosses \
  --primary-metric target_slice_auc \
  --failure-label stale_model_py_root \
  --failure-label missing_restore_checkpoint \
  --negative-evidence failed_before_eval_metrics
```

The result ledger still keeps `launch_actions_enabled=false` and
`production_actions_enabled=false`; it is a portable fact/decision row, not a
Volc connector with create/stop/restart authority.

## Detection

`domain_pack_detection_v0` is a suggest-only detector. It may inspect public-safe
goal metadata and compact state for signals such as:

- experiment board or evaluation artifact references;
- primary metric or guardrail labels;
- external job evidence handles;
- repeated metric/classification/result todos;
- explicit owner language asking for experiment advisory or delivery.

The detector returns suggestions, not permissions:

```json
{
  "schema_version": "domain_pack_detection_v0",
  "suggested_pack": "ml_experiment",
  "confidence": "medium",
  "reason_codes": ["primary_metric_label", "external_job_handle"],
  "allowed_next_action": "suggest_enablement",
  "requires_owner_or_registry_decision": true
}
```

If the pack is disabled, the agent may recommend enabling it and may continue
generic control-plane work. It must not write experiment conclusions, mark a
winner, launch jobs, or treat a domain hint as a gate bypass.

## Result Flow

When an ML experiment pack is enabled in advisory mode, the generic result event
still wraps the domain result:

```yaml
result_event_v0:
  terminal_state: done
  evidence_pointer: compact_metrics_artifact
  validation_status: validated
  outcome_classification: outcome_progress
  next_action: propose_replan
  domain_extension:
    kind: ml_experiment_result_v0
    primary_metric_status: improved
    guardrail_status: clean
    decision_status: candidate_not_winner_yet
```

The generic fields keep status, quota, review packets, and frontstage surfaces
stable. The domain extension adds useful interpretation only after the pack is
enabled.

## State Placement

ML experiment state should be stored in three layers instead of being appended
directly into the core active state.

| Layer | Default Location | Owns | Does Not Own |
| --- | --- | --- | --- |
| Core LoopX state | registry, `ACTIVE_GOAL_STATE.md`, todos, run history, rollout events | current next action, gates, claims, compact evidence digest, quota/spend lifecycle | per-task metric history, raw external job details, experiment-board-sized ledgers |
| Domain state | `.loopx/domain-state/<goal-id>/<domain-pack>/...` | task/result rows, dataset-window contracts, same-window comparisons, guardrail summaries, promote/retire decisions | credentials, raw logs, raw launch commands, large metric dumps |
| Raw/private artifacts | project-local ignored adapter storage such as `.local/` or a private connector cache | raw logs, command snapshots, workspace paths, debug bundles, large metric artifacts | status truth, todo ownership, quota authority |

The domain-state layer is project-local and gitignored, so it may contain
operator-private task ids and compact metric facts that should not be published.
It is still a read model for agents, not a raw evidence bucket. Keeping it
compact prevents status, replanning, and handoff prompts from inheriting noisy
or sensitive operational traces while avoiding the opposite problem of stuffing
ML-specific state into the generic control plane.

The CLI writes this layer when callers pass `--goal-id`:

```bash
loopx ml-experiment volc-result-ledger \
  --goal-id example-goal \
  --experiment-id external_slice_screen \
  --task-id task-candidate-1 \
  --task-name external_slice_cross_screen \
  --state Completed \
  --train-window 20251002-20260501 \
  --eval-window 20260501-20260508 \
  --code-ref codex/example-feature-cross@abc1234 \
  --model-name candidate_model_abc1234
```

The default target is
`.loopx/domain-state/example-goal/ml_experiment/ledger.jsonl`. A caller may pass
`--ledger-path` for migration or tests, but normal project use should prefer the
goal-bound default path.

Code should follow the same split:

| Code Area | Owns |
| --- | --- |
| `loopx/domain_state.py` | cross-pack path conventions, local locks, atomic JSONL upserts, and small storage primitives |
| `loopx/domain_packs/<pack>.py` | pack-specific schemas, metric interpretation, renderers, and ledger-key selection |
| legacy top-level modules such as `loopx/ml_experiment.py` | compatibility re-exports only when older imports already exist |

## Roadmap

P0: split the boundary.

1. Design and validate `domain_pack_contract_v0`: `enabled`, `autonomy`,
   `allowed_actions`, `capability_requirements`, and boundary semantics.
2. Implement `domain_pack_detection_v0` as suggest-only. It can recognize
   experiment-shaped goals, but it cannot enable itself.
3. Promote `observable_artifact_handle_v0` as a default generic capability for
   long-running work.

P1: add ML experiment advisory mode.

4. Define `ml_experiment_result_v0` over compact metric/evaluation artifacts.
5. Define `dataset_window_contract_v0` for date/hour coverage, intersection,
   fairness labels, and conclusion eligibility.
6. Define `hypothesis_ledger_v0` for mechanism families, positive/negative
   evidence, near-neighbor exclusions, and promote/retire conditions.
7. Add `experiment_replan_preview` that proposes candidates and validation
   needs without launching jobs.

P2: add controlled delivery.

8. Implement `ml_experiment` delivery mode behind explicit registry boundary,
   board selection, preflight verification, quota, capability gates, and
   writeback.
9. Add an `ml_experiment_frontstage` card that explains the current best route.
10. Add an `experiment_dreaming_lane` for advisory candidate generation and
    archive suggestions. Promotion to executable agent todo requires operator
    review.

## Non-Goals

- Do not ship ML experiment assumptions in the default control plane.
- Do not auto-launch or stop external jobs from detection alone.
- Do not infer primary metric authority from arbitrary text.
- Do not store raw metrics dumps, private job logs, credentials, local paths,
  or production artifacts in public docs or generic status projections.
- Do not let reward-style or replan hints override gates, claims, scope,
  capabilities, workspace guard, or goal boundary.
