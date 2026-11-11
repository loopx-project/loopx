# Stage Methodology Operator Contract v0

Status: supported TypeScript control-plane contract for composable long-horizon
decision methodologies and advisory quantitative planning operators.

As LoopX grows beyond pairwise work comparisons into multi-point proxy
calibration, 18h+ confirmation-budgeted continuation, same-checkpoint
counterfactual attribution, and evolutionary population search, implementing
each methodology as bespoke stage glue across `admission`, `compile_context`,
`execute_verify`, `writeback_settle`, and `feedback_evolve` repeatedly produces
five recurring failure classes:

1. **Feature-off and quiet-turn leakage**: truthy flag coercion or untriggered
   cues inflate prompt entry cost and control-plane overhead.
2. **Frozen-prefix and late-replay drift**: mixing append order with wall-clock
   timestamps, tied commit clocks, or post-prefix repeated citations mutates
   historical receipts on retry or replay.
3. **Unbounded compact projections**: variable-length findings or measurement
   tables leak into hot-path turn context or Effect RPC payloads without
   explicit item caps, omitted counts, or a cold-path `details_command`.
4. **Cross-scope contamination and silent zero coercion**: comparing across
   different `node_id`, `comparison_scope`, `input_revision`, `evaluation_ref`,
   `todo_id`, or `target` bindings—or coercing unknown `null` measurements and
   resource bounds to `0`—corrupts quantitative direction choices.
5. **Advisory-to-authority escalation**: advisory planning cues accidentally
   emit admission, lease, gate-override, or execution-authorization fields.

`stage_methodology_operator_catalog_v0` (`loopx/control_plane/capabilities/stage_methodology_operator.ts`)
establishes one browser-safe TypeScript kernel for all methodology operators.
Existing control-plane mechanisms (`work_yardstick` in `yardstick_decision.ts` /
`yardstick_source.ts` and `stride_review` in `stride_policy.ts`) and quantitative
research operators (`yardstick_fidelity.ts`, `budgeted_continuation.ts`,
`counterfactual_continuation.ts`, `evolutionary_search.ts`) share the same
activation, replay-window, scope-tuple, digest, bounded-projection, and
non-escalation primitives. Python CLI/API surfaces act strictly as transport and
canonical state loaders; dashboard readers reuse the same output verifier.

## Five Canonical Control Stages (`LOOPX_CONTROL_STAGES`)

| Stage ID | Lifecycle Responsibility | Methodology Operator Contract |
| --- | --- | --- |
| `admission` | `quota should-run`, trigger matching, lease/claim eligibility, stop/wait boundaries | Strict boolean opt-in (`evaluateMethodologyActivation`); zero silent spend or claim creation |
| `compile_context` | `turn-context`, progressive hot-path cues, cold-path `details_command` | Bounded advisory cues (`projectBoundedMethodologyItems`) over frozen canonical prefixes |
| `execute_verify` | Agent work execution, candidate probes, evaluator runs, isolated branches | Exact scope-tuple matching (`matchesExactScopeTuple`) and checkpoint/workspace isolation |
| `writeback_settle` | `explore finding`, `refresh-state`, replay-deterministic delivery receipts | Canonical append-prefix & committed-clock windowing (`selectCanonicalEvidenceWindow`, `isHistoricalReplayPrecedingCurrent`) |
| `feedback_evolve` | Proxy fidelity, confirmation back-scheduling, counterfactual attribution, evolutionary selection | Read-only typed schemas verified at runtime by `assertMethodologyOperatorOutput` |

## Shared Anti-Entropy Primitives

1. **Strict Feature-Off & Quiet-Turn Isolation (`evaluateMethodologyActivation`)**:
   Requires `enabled === true` (never truthy `"true"` or `1`). Suppresses cues
   when `quiet_turn_policy === "silent_when_untriggered"` and `trigger_active === false`,
   or when any `required_scope` key is blank.
2. **Canonical Prefix & Replay-Precedence Guards (`normalizeCanonicalSourceSnapshot`, `selectCanonicalEvidenceWindow`, `isHistoricalReplayPrecedingCurrent`)**:
   Validates lowercase 64-hex SHA-256 digests (`isValidContentSha256`) through
   the single `content_digest.ts` owner. Uncited work observations enter a
   post-decision window only when both `recorded_order >= workObservationStart`
   and committed timestamp strictly exceeds the decision timestamp
   (`committed > decisionTimeMicros`). Tied or missing clocks never promote a
   delayed replay into new evidence.
3. **Exact Scope-Tuple & Work-Identity Isolation (`matchesExactScopeTuple`, `matchesWorkIdentity`)**:
   Compares exact values across every declared scope key (`metric`, `unit`,
   `higher_is_better`, `comparison_scope`, `input_revision`, `evaluation_ref`)
   and work binding (`goal_id`, `agent_id`, `todo_id`). Unknown measurements and
   resource bounds remain `null`.
4. **Bounded Hot-Path Projections (`projectBoundedMethodologyItems`)**:
   Slices hot-path arrays to `maxItems`, returning exact `omitted_count` and an
   optional `details_command`.
5. **Runtime Non-Escalation Output Verifier (`verifyMethodologyOperatorOutput`, `assertMethodologyOperatorOutput`)**:
   Asserts `advisory_only: true`, `grants_execution_authority: false`, zero
   forbidden authority fields (`should_run`, `normal_delivery_allowed`,
   `lease_granted`, `gate_override`, `admin_bypass`, `execution_authorized`),
   finite numbers (rejecting `NaN`/`Infinity`), no control characters, and a
   64 KiB UTF-8 payload cap.

## Built-In Operator Catalog (`stage_methodology_operator_catalog_v0`)

Inspect registered operators via `loopx --format json explore methodology-operators [--stage <stage>]`:

| `operator_id` | Primary Stage | Companion Stages | Transition Schema |
| --- | --- | --- | --- |
| `work_yardstick` | `execute_verify` | `compile_context`, `writeback_settle`, `feedback_evolve` | `work_yardstick_decision_v0` |
| `stride_review` | `feedback_evolve` | `compile_context`, `writeback_settle` | `stride_decision_v2` |
| `scoped_yardstick_fidelity` | `execute_verify` | `compile_context`, `feedback_evolve` | `scoped_yardstick_fidelity_v0` |
| `budgeted_continuation` | `feedback_evolve` | `admission`, `compile_context` | `budgeted_continuation_plan_v0` |
| `counterfactual_continuation` | `feedback_evolve` | `execute_verify` | `counterfactual_continuation_eval_v0` |
| `evolutionary_population` | `feedback_evolve` | `compile_context`, `execute_verify` | `evolutionary_population_plan_v0` |

## Quantitative & Search Operator Schemas

### 1. `scoped_yardstick_fidelity_v0` (`explore yardstick-fidelity`)

Evaluates multi-point agreement between a fast `proxy` profile and an
`authoritative` outcome profile on one `node_id`:

- Pairs confirmed, finite measurements per `candidate_ref` under exact
  scope-tuple equality (`metric`, `unit`, `higher_is_better`, `comparison_scope`,
  `input_revision`, `evaluation_ref`).
- Computes direction-normalized `rank_concordance` (`kendall_tau_b`,
  `spearman_rho`), `linear_calibration` (`utility_slope`, `residual_mad`,
  `r_squared`), and top-$K$ `frontier_neighborhood` (`frontier_k`,
  `frontier_kendall_tau_b`, `bias_cliff_detected`).
- Emits `fidelity_status`: `direct_only`, `insufficient_paired_evidence`,
  `noisy_indistinguishable`, `aligned_in_tested_scope`, `frontier_bias_cliff`,
  `order_reversed`, or `mixed_or_flat`.
- Audits `unconfirmed_proxy_winners` (proxy-only candidates exceeding the best
  paired proxy score) and `proxy_false_negatives` without globally blacklisting
  any evaluator (`scope_bound: true`, `global_evaluator_blacklist: false`).
- Includes every candidate tied at the authoritative top-K cutoff. The returned
  `frontier_k` is the number of candidates analyzed after including those ties.

### 2. `budgeted_continuation_plan_v0` (`explore budgeted-plan`)

Back-schedules authoritative confirmation reserve from `remaining_budget`
(`wall_minutes`, `token_budget`, `authoritative_eval_slots`; keeping unknown
bounds `null`) and evaluates candidate continuation routes:

- Computes `latest_confirmation_start_minute` and `remaining_work_window_minutes`
  from the remaining time and the duration of confirmation. The remaining
  cooldown starts at the planning boundary and can overlap useful work.
  `pending_in_flight_minutes` reserves time for a result the planned route
  depends on. Confirmation starts at the later of candidate readiness and
  cooldown expiry.
- Uses the sum of known nonnegative token costs as a lower bound. A route
  above the ceiling is infeasible even when its full cost is unknown.
- Uses a baseline route's expected gain as a decision reference only when its
  costs fit the known budget. Otherwise the measured incumbent supplies a
  zero-gain reference. Without a measured incumbent the reference is `null`.
- Accepts explicit `null` gain estimates and checks the order of the available
  estimates. A missing estimate leaves its corresponding break-even probability
  unknown. Dominance requires a known upper gain bound and a known reference.
- Under LoopX's retained-incumbent rule, a regressed candidate does not replace
  the verified incumbent, so floor post-confirmation gain is
  $g_{\text{floor}} = \max(0, g_{\min})$ and break-even success probability
  against `effective_safe_gain` is:
  $$p^* = \operatorname{clamp}_{[0,1]}\!\left(\frac{g_{\text{safe}} - g_{\text{floor}}}{g_{\text{target}} - g_{\text{floor}}}\right)$$
- Classifies each route's `feasibility_status` (`feasible`,
  `feasible_with_unknown_bounds`, `deadline_infeasible`, `token_infeasible`,
  `eval_slot_infeasible`) and `recommended_disposition` (`reject_infeasible`,
  `dominated_by_baseline`, `probe_then_decide`, `viable_direct_continuation`,
  `upside_tail_only`).

### 3. `counterfactual_continuation_eval_v0` (`explore counterfactual-eval`)

Evaluates matched `treatment` and `control` continuations forked from an
identical frozen `checkpoint` (`source_state_sha256`, `input_revision`,
`evaluation_ref`, `fork_recorded_at`). Use the same `terminal_boundary_ref`
on the checkpoint and both continuations.

- Checks distinct, unreused `workspace_isolation_id` values, matching
  checkpoint and terminal boundary, and an observed context time at or before
  `fork_recorded_at` for each run. Strict mode rejects failed checks.
  Audit mode excludes those pairs from the estimates.
- Reports first-action divergence separately from score delta. Missing action
  evidence is `action_unobserved`. Direction-normalized deltas combine only
  within the same declared comparison scope, metric, unit, direction, input,
  evaluator and terminal boundary. For one comparable scope, it computes the
  mean and median delta, exact two-sided paired sign-flip $p$-value, and
  `complete_marginal_costs` across `wall_seconds`, `total_tokens`,
  `evaluator_runs`, `control_plane_requests`, and `recovery_events`.

### 4. `evolutionary_population_plan_v0` (`explore evolutionary-plan`)

Performs mechanism-diverse evolutionary selection over confirmed Explore Graph
measurements without collapsing into single-mechanism hyperparameter tweaks or
Goodharted proxy winners:

- Groups confirmed `authoritative` candidates by the observed executed method
  or explicit `mechanism_by_candidate` mapping. A candidate with unknown
  mechanism keeps its score and can fill an elite slot. Crossovers and targeted
  mutations use candidates with identified mechanisms.
- **Island elitism (`elites`)**: preserves the top champion of each distinct
  mechanism island first (`selection_reason: "island_champion"`), filling
  remaining slots up to `elite_limit` by utility (`"utility_fill"`).
- **Crossover proposals (`crossover_pairs`)**: pairs elites across distinct
  identified mechanisms. The combination requires its own validation.
- **Bottleneck mutation (`mutation_targets`)**: emits `island_champion_refinement`
  or `tie_breaking_bottleneck_probe` targets on top elites.
- **Goodhart proxy quarantine (`proxy_quarantine`)**: delegates proxy screening
  to `evaluateScopedYardstickFidelity`; unconfirmed proxy winners are never
  admitted into `elites` or `crossover_pairs`, receiving disposition
  `quarantined_proxy_cliff` under `frontier_bias_cliff` / `order_reversed` and
  `confirm_before_breeding` otherwise.
