---
name: loopx-self-repair
description: Diagnose and repair LoopX control-plane drift or agent behavior drift. Use when a LoopX task makes unexpectedly small progress, follows a stale or contradictory recommended_action, ignores a higher-priority blocked item while doing fallback work, reports vague owner/user gates, loses todo projection, misaligns benchmark treatment with the real product path, mixes temporary artifacts into commits, or when the user asks for root-cause analysis, self-repair, or why the harness/agent behaved unexpectedly.
---

# LoopX Self Repair

Use this skill to turn a surprising LoopX behavior into a durable fix,
not only an apology or a one-off explanation.

## Repair Loop

1. **Pause delivery selection.** Do not spend quota or continue adapter work
   until the control-plane facts explain why that work is valid.
2. **Reuse evidence before collecting more.** Start with the current failed
   command's structured response, error code and operation identity. An already
   loaded packet is evidence for that observation, not permission for a later
   write. Fetch fresh authority when required by its admission/lease contract.
   Read [targeted diagnostics](references/targeted-diagnostics.md) when deciding
   which missing fact to collect or investigating slow commands. Do not run
   diagnose, status, quota and history as a fixed preflight: diagnose already
   composes status and quota work. Recording an already-understood repair Todo
   does not require rediscovering the incident.
3. **Look up the symptom.** Run `python3 scripts/find_pattern.py --query
   '<error code or symptom terms>'` from this skill directory, or invoke its
   absolute path. Use `--id <returned-id>` to read the relevant full guidance.
   [Search instructions](references/pattern-lookup.md) explain pagination and
   fallback. Do not load the complete catalog, paginate it into context, or
   reread unchanged references already available in this task. If no pattern
   fits, diagnose from current facts and add one after the fix.
4. **Assign the responsible layer.** Separate:
   - agent behavior mistake;
   - state projection or quota payload bug;
   - active-state authoring gap;
   - benchmark harness mismatch;
   - docs/process hygiene gap.
5. **Repair at the lowest durable layer.**
   - If it is a one-off agent mistake, write back the correct state/todo and
     size the next scoped effort to its verifiable result, evidence and risk.
   - If the machine projection misled the agent, fix CLI/status/quota
     projection and add a focused smoke.
   - If the user correction changes the goal acceptance, says the agent missed
     the intended loop, or exposes a product bottleneck that is not visible in
     quota/status, write a bounded `goal_vision_replan_contract_v0` packet with
     `replan_trigger_summary` through normal `loopx refresh-state --vision-*`
     fields, using the same `--agent-id` as the current lane, or
     `--agent-vision-json` for generated multi-field patches, before returning
     to delivery. If the next executable step is already known, also add or
     link the concrete successor todo; do not leave the correction only in chat
     or an incident note.
   - If a design rule is missing, update the interaction model or todo list
     before implementing broad behavior.
   - If benchmark evidence is not attributable, add posthoc trace/parity
     checks before claiming uplift or regression.
6. **Validate before resuming.** Run the smallest smoke or CLI check that would
   have caught the issue, plus `loopx check` on changed public surfaces
   when docs/contracts changed.
7. **Write back the lesson.** Update active goal state, docs, contributor
   tasks, or this skill so the same failure mode is visible next time.

When work repeatedly enters a receipt-owned retry, first check the caller's
typed outcome against the selected Todo. Rejecting a candidate with new,
validated evidence can advance an evaluation Todo while leaving the broader
stage open; it is not automatically an execution blocker. Pure reuse or setup
does not qualify as progress. Use `blocked` only when the Todo's actual next
step cannot execute, preserve genuine retry clocks, and replan finite answered
questions through the existing Todo/vision. Never erase receipts or fabricate
progress to escape a wait. See [settlement classification](../../docs/quota-allocation.md#allocation-contract).

## Upstream Issue Escalation

A public GitHub issue is an optional final escalation, not a default side
effect of self-repair. Consider it only when the responsible layer is a
reusable LoopX product, CLI, skill, installer, or control-plane gap and durable
upstream tracking adds value beyond the local repair or PR.

Read `references/upstream-issue-escalation.md` before publishing anything.
Invoking this skill never grants publication permission. The guarded path must:

1. reject private, project-specific, support-only, and security-sensitive
   reports;
2. reduce the evidence to a minimal public-safe reproduction and scan the
   draft with `loopx check`;
3. search open and closed issues by a stable fingerprint before creating one;
4. auto-submit only under explicit current-turn approval or durable owner
   opt-in; otherwise show the exact draft and ask once for confirmation;
5. create at most one issue per repair turn, then record the existing or new
   issue URL in the relevant LoopX todo/evidence writeback.

If qualification, authority, authentication, boundary scanning, or duplicate
search is uncertain, preserve the draft and stop before publication. Prefer a
direct fix or PR when no separate issue is needed for coordination.

## Vision / Replan Writeback

Use the bounded vision contract when self-repair discovers that LoopX did not
notice a missing outcome, route, or acceptance condition by itself. The packet is
the bridge from human or agent insight to quota-visible replan state:

```json
{
  "schema_version": "goal_vision_replan_contract_v0",
  "state": "vision_drift_detected",
  "vision_patch": {
    "vision_summary": "Name the corrected route or acceptance target.",
    "acceptance_summary": "Name the machine-visible condition that must hold.",
    "replan_trigger_summary": "Name why the current frontier is insufficient."
  },
  "todo_delta": ["create_successor"]
}
```

Record it with normal inline `refresh-state --vision-summary
--vision-acceptance --vision-replan-trigger` fields using the same `--agent-id`
that ran the repair. Use `--agent-vision-json` when a generated patch is clearer
than a command line. Replan closes only through a typed semantic observation or
an atomic Todo transition bound with `--replan-obligation-id`, a typed
`--action-kind`, and a stable `--target-key` or Explore node ref; do not append
a second `--autonomous-replan-recorded` repair ACK. A vision patch without a
runnable Todo is still useful: `quota should-run` can promote its
`replan_trigger_summary` into `goal_frontier_projection.acceptance_gaps[]` when
the advancement frontier is empty.

If the repair concludes that the existing per-agent vision is still correct,
close the required checkpoint with `--vision-unchanged-reason` instead of
writing a fake patch. If a material `refresh-state` lacks both a patch and an
unchanged/no-follow-up decision, LoopX should preserve a per-agent
`vision_checkpoint_v0` with `decision=missing_required` so the same agent's
next quota check can enter replan. A scheduler wake alone is not a material
vision boundary: when quota explicitly projects a normally admitted open
advancement Todo as `delivery_boundary=in_flight_continuation`, use the
projected settlement command and do not invent a vision patch. The next
heartbeat keeps that same Todo selected only after accountable
`outcome_progress`; Todo completion, blocker/gap, durable Next Action change,
replan, or terminal closeout must return to the strict semantic checkpoint.

## Evidence Discipline

- Do not read or commit raw private logs, trajectories, verifier output,
  credentials, internal links, or production material.
- Do not solve contradictory payloads by guessing. If `recommended_action`,
  `goal_boundary.write_scope`, todos, and interaction contract disagree, treat
  that as a projection bug or state authoring bug first.
- Do not let fallback work hide the primary blocker. When a higher-priority
  path is gated but safe fallback is valid, report both the concrete gate and
  the fallback progress.
- Do not equate bounded work with a small operation. If turns repeatedly stop
  after setup or surface-only edits, check whether a verifiable result could
  have been reached within scope and budget. Repair the premature stop, not by
  imposing a minimum number of calls/files or ignoring explicit stop conditions.

## Reference Routes

- For known symptom-to-repair mappings, search with `scripts/find_pattern.py`;
  `references/pattern-lookup.md` explains the lookup, not a required full read.
- For missing facts, slow commands and response truncation, read
  `references/targeted-diagnostics.md`.
- For guarded public GitHub issue escalation, read
  `references/upstream-issue-escalation.md`.
- For user/agent/state channel semantics, read
  `../../docs/state-interaction-model.md` and
  `../../docs/concepts/interaction-pattern-catalog.md`.
- For quota and heartbeat decisions, read
  `../../docs/quota-allocation.md` and
  `../../docs/heartbeat-automation-prompt.md`.
- For commit/PR hygiene failures, read `../../AGENTS.md`.
