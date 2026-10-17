# Handoff, Feedback And Authorized Child Work

Read before relaying a review packet, recording human reward, or using enabled capability context for separately authorized child work. A handoff, context packet or installed skill never grants authority.

## Generate A Review Packet

When a project agent, controller thread, or local shell needs the current
operator packet, prefer the CLI packet over asking the user to find a dashboard
copy button:

```bash
loopx review-packet --goal-id <STABLE_GOAL_ID>
```

For machine-readable inspection, put the global format flag before the
subcommand:

```bash
loopx --format json review-packet --goal-id <STABLE_GOAL_ID>
```

To relay current target-agent context within existing authorization, use the
handoff form. Ordinary handoffs add no new approval; actual operator gates
still apply:

```bash
loopx review-packet --goal-id <STABLE_GOAL_ID> --handoff-only
```

This read-only command assembles agent context directly from current status.
The full Review Packet consumes the same context and adds human presentation.
Neither path grants authority or changes work state. JSON `handoff_text` and
`project_agent_handoff` always contain complete prepared text. On overflow,
`project_agent_handoff_fragments` contains all ordered shards including index 0;
Markdown prints the shard set. The complete text may exceed 16 lines / 1800
characters; each shard fits that budget. In-budget output keeps its old shape. If the selected queue item is legacy/raw
fallback rather than project-asset-backed, do not treat raw queue fields as
owner, gate, or stop-condition authority.

Collect and restore the entire producer output before using a sharded handoff:

```bash
loopx --format json review-packet --goal-id <STABLE_GOAL_ID> --handoff-only > handoff.json
loopx handoff restore --input handoff.json --format json
```

For raw sharded Markdown use `--input-format markdown`; `--input -` reads stdin.
JSON avoids relying on a renderer preserving HTML comment envelopes. A fragment
title without its envelope, or an indented envelope, is rejected rather than
returned as plain text. If both title and envelope disappear, only the original
JSON output can establish completeness. Complete unfragmented handoff-only
Markdown is accepted as plain text without an integrity claim; use JSON for
unfragmented full Review Packets. Missing,
reordered, duplicate, mixed or changed parts fail with a nonzero exit and
`error_code`, without partial text. Obtain the original complete output and
retry. There is no collector or business-request deduplication by content hash.

A successful restore is content recovery only: it does not execute a command,
adopt work, change Todo/claim/lease or start a session. Check current goal,
source freshness, write scope and real gates using existing status/quota and
ownership workflows. `handoff prepare/inspect/adopt` retains its separate
ownership contract; restore is not a replacement for it. Do not infer sender
identity or permission from a checksum.

Read the packet in order:

- `人只需判断`: the user or controller decides in the dashboard/operator view or
  trusted local shell. A target project agent cannot self-approve this section.
- `用户本地 Gate 记录草稿`: a local preview for the user/controller. The target
  project agent must not run this draft as its own command.
- `给项目 Agent`: the executable handoff context only after the packet's
  forwarding condition is already satisfied. If its stop condition fires, stop
  and report the exact blocker instead of continuing.

## Record Human Reward

When the user gives a clear reward judgment for an exact run, first validate
the overlay and active-state writeback:

```bash
loopx reward \
  --goal-id <STABLE_GOAL_ID> \
  --run-generated-at <RUN_GENERATED_AT> \
  --decision <DECISION_LABEL> \
  --reward positive \
  --reason-summary "<PUBLIC_SAFE_CHINESE_REASON>" \
  --follow-up "<PUBLIC_SAFE_NEXT_ACTION>" \
  --write-active-state-summary \
  --dry-run
```

The preview may stay anonymous. Only after the user has explicitly approved
recording the reward, rerun without `--dry-run` and add exactly one reviewed
actor classification: `--actor-kind owner` for the owner's judgment or
`--actor-kind controller` for an authorized controller's judgment. Never infer
that classification from the process, Agent id, or command defaults. The
durable source of truth is still the run-bound `human_reward` overlay. The
active-state writeback is a `Progress Ledger` summary for future agents;
project agents should read the reward through the returned
`project_agent_visibility.history_command`.

## Capability Context And Child Models

Read `interaction_contract.agent_context` at planning time (or
`turn_envelope.agent_context` in LoopX Turn, resolving its detail reference when
compacted). If the host supplies neither, use the read-only
`loopx agent-context --goal-id <goal> --agent-id <agent> --phase before_plan`.
If context is absent, disabled, or the read fails, preserve the existing single
agent workflow: do not seek delegation splits or invoke child tools because of
this capability. Tool availability and installed skills do not activate it.
Only apply delegation guidance from a non-null, current-scope enabled context;
the capability provider owns that policy. Context never grants spawn authority.

When enabled context and separate authorization allow native child tools
outside LoopX Turn, read the same capability
context at each boundary, using the current registry, Goal and Agent:

```bash
loopx agent-context --goal-id <goal> --agent-id <agent> --phase before_delegate
# Launch the bounded child work; continue useful coordinator work; collect results.
loopx agent-context --goal-id <goal> --agent-id <agent> --phase after_delegate_result
```

These are read-only calls, not new turns or quota spends. Read `before_plan`
through this command too if the host did not supply an interaction contract.
LoopX Turn already carries `delegation_context` in its host request and
`agent_context` in its reconciled result. Apply the relevant guidance once per
boundary, not on every poll. Validate returned evidence, explain acceptance or
rejection and link accepted work to the plan/deliverable. A context packet's
`delivery: projected` proves generation only, not model reading, execution or
adoption. Native CLI reads cannot certify host receipts.

For an authorized child-worker task, read
`goal_boundary.orchestration.model_config` when present and explicitly pass
its `model` and optional `reasoning_effort` through the native host's supported
launch arguments. Check host support before launch; report unavailable settings
instead of silently inheriting or substituting the coordinator model. Persist
preferences through `configure-goal --subagent-model <id>
--subagent-reasoning-effort <effort> --execute`, and remove them together with
`--clear-subagent-model-config`. These preferences do not enable spawning or
widen authority. Read configuration back with `loopx configure-goal --goal-id <goal>` before
relying on model preferences. For read-heavy child work, provide the current
authority source, evidence boundary, expected output, non-goals and validation;
a child reports evidence while the authorized coordinator accepts results and
settles the turn. Keep repository-writing child scopes disjoint in independent
worktrees. Context choice does not transfer a lease or widen authority.

LoopX cannot transparently intercept arbitrary native host tools. Managed result
reconciliation is observation-only: a receipt makes evidence eligible for parent
review, not for automatic settlement, publication, external writes or production
actions. Read actual host receipts and validate original evidence before claiming
execution or adoption; configuration readback alone proves neither.
