# Goal-oriented focus, zero-ceremony progressive entry, and structural moats (2026-11-11)

[中文版](2026-11-11-goal-oriented-focus-and-progressive-governance.zh-CN.md)

This entry records a structural clarity and baseline-reconciliation update to
[`loopx-overall-roadmap-v0.md`](../../loopx-overall-roadmap-v0.md),
[`target-yardstick-stride-decisions-v0.md`](../../target-yardstick-stride-decisions-v0.md),
and
[`long-horizon-harness-benchmark-research-program-v0.md`](../../long-horizon-harness-benchmark-research-program-v0.md)
against `main` `bf8a66eb9c` (`2026-11-11`).

## The gap

Two recurring failure modes appeared for working Agents and contributors reading
the overall roadmap and research/decision RFCs:

1. **Control-plane ceremony drift and prompt micro-scheduling:** LoopX requires a
   strict, single-owner typed kernel (`AuthorityStore`, CAS, `claim/lease`,
   `receipt`, `Turn` fences, `Stride/Yardstick` records), but without an explicit
   progressive-entry rule, implementation slices risk imposing upfront governance
   ceremony, extra RPC round trips, or brittle prompt micro-scheduling on
   unchanged uncontested single-Agent turns—degrading net long-horizon
   efficiency as base models strengthen.
2. **Checkpoint lag across `2026-10-23`–`2026-11-11` landings (`F7` discipline):**
   `Current priorities` remained pinned solely to `5550e91bab` (`2026-10-23`),
   and Section 4 rows in `loopx-overall-roadmap-v0` lagged behind merged `main`
   boundaries through `bf8a66eb9c` (`2026-11-11`): bounded M1 `backup-state
   verify` + packaged Settings inert inspection in
   [`complete-state-recovery-v0.md`](../../complete-state-recovery-v0.md),
   cited-measurement canonical-prefix reuse (`#8382`), Stride Explore/reversal
   projection (`#8385`), the
   [budgeted research planning companion](../../../../research/budgeted-research-planning.md)
   and three-stage policy boundary (`#8441`, `#8461`), the T02 history
   working-set proposal (`#8450`,
   [`history-working-set-context-policy-v0.md`](../../history-working-set-context-policy-v0.md)),
   the `main` EdgeBench single-worker `--worker turn-resume` profile with
   trusted `--validation-command-json` and original-wake recovery (`#8499`, with
   corresponding C1 integration-branch validation in `#8480`), quiet causally
   blocked prerequisite waits (`#8491`), semantic-replan Todo binding retention
   (`#8495`), and the RFC header/index registration for
   [`self-update-explicit-runtime-route-v0.md`](../../self-update-explicit-runtime-route-v0.md)
   (`#8470`) needed by `generate_rfc_status_index.py --check`.

## What changed

1. **Heavy kernel, zero-ceremony progressive entry (`loopx-overall-roadmap-v0` §1, §7; `target-yardstick-stride-decisions-v0` §5.5):**
   codified that routine onboarding and unchanged single-Agent turns require zero
   upfront governance ceremony (`git init`-like entry and silent single-owner
   claim/lease handling on uncontested tasks), governed controls disclose only on
   real triggers (recovery, local-to-cloud handoff, contention, explicit approval
   gates, multi-Turn stalls, or proxy-versus-native reversals), and control-plane
   token/RPC/attention overhead counts against complete task budget $B$.
2. **Three compounding structural moats (`loopx-overall-roadmap-v0` §1):**
   directed engineering and research investment away from brittle hand-written
   prompt micro-scheduling toward three structural capabilities outside a single
   forward pass:
   - heterogeneous local/cloud continuity and authority boundaries (`S2/S4/S9`,
     `R5/R6`);
   - deterministic quiescent checkpoints and same-state counterfactual
     continuation (`S4/S10/S11`, `R5`, `T01/T03`, $\tau_B(s; a, a_0, \pi)$);
   - closed-loop trajectory-to-memory and budgeted allocation flywheel
     (`S6/S7/S11`, `T01–T04`, `current-model pilot -> calibrated estimates ->
     learned allocation`).
3. **Goal-oriented execution compass, research decision rules, and composable stage consolidation (`loopx-overall-roadmap-v0` §7; `target-yardstick-stride-decisions-v0` §5.8; `long-horizon-harness-benchmark-research-program-v0` §6.2, §8.7, §11.2):**
   anchored slice selection and benchmark evaluation on net terminal accepted
   outcome $\Delta J(B \mid s_0) = \mathbb{E}[Y_B^\pi - Y_B^{\pi_0} \mid s_0]$
   under complete cost accounting, reinforced confirmation-deadline
   back-scheduling and scoped neighborhood yardstick fidelity (versus global
   evaluator blacklists), required accumulating decision/research methodologies
   (multi-point fidelity, confirmation back-scheduling, paired counterfactuals,
   mechanism-island evolutionary selection) to consolidate shared stage
   invariants onto one typed methodology kernel rather than duplicating codecs
   (companion proposal `#8516`), and rejected schema fields, read receipts,
   graph edges, replan counts, leaf-RPC migrations, and dated status paragraphs
   as outcomes.
4. **Reconciled `main` `bf8a66eb9c` (`2026-11-11`) boundaries (`loopx-overall-roadmap-v0` header, §2, §4):**
   updated `Current priorities`, S7, and Section 4 rows for
   `complete-state-recovery-v0`, `history-working-set-context-policy-v0`,
   `target-yardstick-stride-decisions-v0`, and
   `long-horizon-harness-benchmark-research-program-v0` while preserving the
   `5550e91bab` baseline facts and all existing normative clauses.

## What this entry does not establish

- **No runtime, wire-schema, or default-profile change:** zero TypeScript or
  Python runtime behavior, M1 wire fields, quota rules, or benchmark runner
  defaults changed in this documentation slice.
- **No claim of measured uplift or promotion:** `#8382`, `#8385`, `#8441`,
  `#8461`, `#8480`, `#8491`, `#8495`, and `#8499` are recorded strictly at their
  verified source/engineering boundaries, and `#8516` is referenced strictly as
  an open companion consolidation proposal. None of them establishes numerical
  yardstick fidelity, quiescent checkpoint-fork equivalence on EdgeBench, E3/C2
  net uplift ($\Delta J(B \mid s_0) > 0$), live host waiting-state cadence, M3
  state reactivation, T02 shipped history policies, or C1/C4 default promotion.
- **Orthogonal in-flight RFCs untouched:** `controller-executor-unified-contract-v0`
  and `shared-goal-authority-state-provider-v0` remain owned by their dedicated
  tracks without conflicting edits here.

## Validation

```text
uv run --extra test python scripts/generate_rfc_status_index.py --check
uv run --extra test python scripts/check_rfc_bilingual.py --base origin/main --head HEAD
uv run --extra test python examples/docs-governance-smoke.py
uv run --extra test python -m pytest -q tests/architecture/test_rfc_status_index.py tests/canary/test_rfc_bilingual.py
uv run --extra test loopx canary premerge --from-git-diff --git-diff-base origin/main
```
