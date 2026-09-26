# RFC: Goal Artifact Lifecycle Projection (milestone / guard / next-transition) v0

- Status: Accepted
- Supersedes / closes: none
- Proposed by: LoopX maintainers
- Date: 2026-08-12
- Scope: a read-only, goal-level lifecycle projection derived from existing
  typed state, with a global aggregation view as a later slice; no runtime
  state-machine change, no process engine
- Source baseline: LoopX `ef5a8acb1`
- Language note: the
  [Chinese version](./goal-artifact-lifecycle-projection-v0.zh-CN.md) and this
  English version are semantic mirrors. A difference between them is a defect.

---

## Background: Derived From Artifact-Centric Business Process Management

This RFC is derived from **artifact-based business process management (ABPM)**,
the artifact-centric line of process research that models work around business
artifacts rather than flowcharts. In ABPM, a business artifact is an entity
with identity and an explicit lifecycle; **guard conditions** gate each state
transition, and **milestones** are the externally meaningful progress points a
case can reach. The Guard-Stage-Milestone (GSM) model is the best-known form of
this semantics.

LoopX already follows part of that philosophy: goals, todos, gates, evidence,
leases, and run history are typed, and several bounded contexts (content items,
benchmark cases, observable artifact handles) already have artifact-like
lifecycles. What is missing is the artifact lens at the goal layer itself.

This RFC adopts only the artifact-lifecycle vocabulary (milestone / guard /
next-transition) as a read-only projection for goals. It deliberately does not
adopt process engines, flowcharts, or a unified lifecycle abstraction; those
are non-goals in Section 2.

## 0. An Example to Help Everyone Understand

An operator opens the dashboard for a long-running benchmark-qualification
goal. The goal has been running for weeks. The dashboard shows todo counts,
quota state, reason codes, and the latest run classification. None of these
answers the three questions the operator actually has:

- **Where is this goal in its lifecycle?** (is it still starting, actively
  qualifying, waiting for owner decisions, or closing?)
- **Which milestones has it reached?** (environment ready, baseline pass,
  release-candidate evidence collected)
- **Which guard blocks the next step, and who owns it?** (the baseline was
  reached, but "owner approves baseline" is still open and no other transition
  is legal)

The operator has to reconstruct these answers from scattered todo status,
gate lists, evidence keys, and run-history classifications. LoopX already
models every piece below the goal as typed state; the goal itself is the least
typed layer that operators look at.

## 1. What This RFC Chooses

This RFC proposes one derived, read-only projection,
`goal_artifact_lifecycle_projection_v0`, that treats a goal as a business
artifact with a lifecycle:

```text
goal_id
lifecycle_phase            (derived, not a new stored state)
milestones[]               (id, label, reached, reached_evidence_refs[])
guards[]                   (id, kind, blocked, owner, decision_scope,
                            evidence_required)
next_transitions[]         (target_phase, precondition, reason_codes[])
```

The projection is **derived only** from state LoopX already owns:

- registry goal fields and active-state todos (task class, status, action kind,
  claimed owner);
- user gates and decision scopes (`blocks_agent`, `resume_when`,
  `required_decision_scope`, and `validation_command` once that field ships);
- evidence and run history (compact evidence refs, delivery outcome, material
  milestone runs, classification);
- goal frontier and work-lane reason codes (what the control plane says is the
  next legal action).

Milestone reachability starts from declared acceptance markers where the goal
records them, and falls back to evidence-derived markers (a bounded
`primary_goal_outcome` or `compact_evidence` run with a material batch scale).
Guards are open owner decisions or unmet evidence preconditions. Next
transitions come from the existing frontier/lane derivation, not from a new
state machine.

## 2. Non-Goals

- No BPMN/process engine, workflow DSL, or flowchart execution.
- No unification of the bounded lifecycles that already exist (todo, monitor,
  content item, benchmark case, PR, observable artifact handle each keep their
  own legal transitions).
- No milestone-based enforcement yet: this RFC does not change what "done"
  means or add a new gate.
- No new stored goal state: `lifecycle_phase` and milestones are projections,
  not authoritative fields.

## 3. Short-Term Applications (next 1-2 releases)

1. One module, `loopx/control_plane/goals/artifact_lifecycle.py`, that derives
   the projection from a status/goal payload (pure function, no file reads).
2. A focused fixture smoke proving derivation on a synthetic goal: declared
   milestones, evidence-reached milestones, an open owner gate as a blocking
   guard, and a legal next transition from the work lane.
3. First consumer: goal detail in status markdown and the dashboard
   (`goal_artifact_lifecycle_projection_v0` alongside `goal_channel_projection_v0`),
   so an operator can answer the three questions from the example.
4. Public-safe boundary stays identical to existing projections: no raw logs,
   evidence bodies, credentials, or local paths.

## 4. Mid-Term Applications (2-4 releases)

1. **Global goal board**: aggregate the per-goal projection across goals into
   one cross-goal view (which goals reached which milestones, which guards
   block how many goals, what is the next transition per goal). This is the
   natural home in the existing `/loopx-global-*` family and the operator
   dashboard.
2. **Periodic report digest**: milestone and guard deltas become report rows
   ("milestone reached", "guard opened/closed") instead of free-text run
   summaries.
3. **Replan novelty substrate**: "covered milestones / attempted transitions"
   becomes a structured input for replan novelty, replacing part of the
   evidence-log string scan with a typed covered-state query.
4. **Verified-completion guard surface**: when `validation_command` ships, it
   plugs into `guards[]` as an evidence requirement, giving the completion
   path one place to explain why a transition is blocked.

## 5. Long-Term Applications (after real callers exist)

Each of these is gated by an active production call site and must pass
scope-fit review before implementation:

1. **Milestone-based closeout semantics**: if a caller needs enforceable
   completion, define "goal done" as all declared milestones reached with
   evidence, rather than todo completion plus no-follow-up.
2. **Case-ownership views**: use claims, leases, and guard owners to answer
   "who owns this case and which guard blocks it" for multi-agent goals.
3. **Artifact history as covered-state ledger**: expose a typed
   "covered states" query over run history and rollout events, only if a
   second consumer besides replan needs it.

## 6. Alternatives Considered

- **Unify all bounded lifecycles into one artifact abstraction**: rejected.
  The bounded contexts have different legal transitions and would merge
  duplicate knowledge (AGENTS.md duplicate-knowledge gate).
- **Add a workflow/process engine**: rejected. LoopX's value is typed state
  and evidence gates, not orchestration.
- **Formalize evidence history only**: accepted as a substrate, not a
  standalone line; it is folded into the milestone derivation.
- **Status quo**: rejected; the goal layer remains the least typed layer
  operators see.

## 7. Smallest Useful Implementation Slice

One pure derivation module plus one fixture smoke and one readout in status
markdown. No runtime behavior changes, no new stored fields, no quota or
settlement impact. The slice proves the vocabulary before any consumer or
enforcement is built.

## 8. Validation Criteria

- Fixture smoke asserts milestone/guard/next-transition derivation from a
  synthetic goal payload, including negative cases (unreached milestone,
  blocking guard with no legal transition).
- The projection stays public-safe: no raw evidence bodies, credentials,
  local paths, or private payloads.
- `examples/docs-governance-smoke.py` and `loopx check` on the new files pass.
- No existing status/quota/settlement smoke changes.

## 9. Open Questions

- Milestone declaration: should milestones come from declared goal acceptance
  fields first, or from evidence-derived markers first?
- Should `lifecycle_phase` be constrained to a fixed enum (e.g.
  `starting / qualifying / waiting_owner / closing / closed`) or left as a
  derived label?
- When should milestone semantics become enforceable, if ever?
