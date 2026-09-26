# Codex App Control-Plane Hook Cache

Status: experimental design note, default off.

This note defines the boundary for a possible Codex App host hook that exposes a
compact LoopX control-plane snapshot to a heartbeat worker. The purpose is to
reduce repeated token-heavy status/quota inspection in long-running Codex App
heartbeats without weakening LoopX's source-of-truth, gate, privacy, or
writeback rules.

The feature must stay disabled by default. It can become a user-visible option
only after the evidence gates in this document pass. It can become a default
path only after a separate adoption decision records the rollout evidence and
the fallback behavior.

## Problem

Today a trusted Codex App heartbeat usually runs:

```bash
loopx --format json --registry "$HOME/.loopx/registry.global.json" \
  quota should-run --goal-id <goal-id> --agent-id <agent-id>
```

That command is the correct authority boundary, but the full JSON payload can be
large. A worker often needs only a small routing summary:

- whether a user action is required;
- whether the current agent must attempt work;
- the selected todo id and compact action text;
- scheduler action and reset/backoff hint;
- workspace guard and capability gate decision;
- next cold-path command when the hint is stale or incomplete.

The product question is whether Codex App can provide that compact routing
summary as a local host hint, so the heartbeat prompt does not repeatedly absorb
the full control-plane payload.

## Non-Goals

The hook cache must not:

- replace LoopX registry, active state, run history, or quota accounting as the
  source of truth;
- bypass `quota should-run`, user/controller gates, workspace guard, capability
  gates, public/private boundary scans, or spend-after-validation rules;
- enable itself for all users or all goals;
- store raw active-state bodies, chat transcripts, private material, local
  absolute paths, credentials, or full run-history records;
- turn a stale or missing hint into permission to run.

## Experimental Contract

The experimental payload is `codex_app_control_plane_snapshot_v0`:

```yaml
schema_version: codex_app_control_plane_snapshot_v0
enabled_by_default: false
mode: advisory_hint
goal_id: example-goal
agent_id: codex-evidence-peer
generated_at: "2026-01-01T00:00:00Z"
source_fingerprint:
  registry_goal_updated_at: "2026-01-01T00:00:00Z"
  active_state_sha256_16: "publicsafehash"
  runtime_index_mtime: "2026-01-01T00:00:00Z"
summary:
  should_run: true
  effective_action: normal_run
  user_action_required: false
  agent_must_attempt: true
  selected_todo_id: todo_publicsafe
  compact_action: "Run one bounded validated batch."
  scheduler_action: run_now
fallback:
  cold_path: quota_should_run
  command: loopx --format json quota should-run --goal-id example-goal --agent-id codex-evidence-peer
  required_when: stale_missing_mismatch_or_delivery_write
privacy:
  contains_raw_state: false
  contains_raw_history: false
  contains_private_paths: false
```

The payload is a host-runtime hint. LoopX remains authoritative. A worker may
use the hint to decide whether to fetch a cold path, but any file edit, external
action, PR publication, or quota spend still needs the normal LoopX guard unless
a later explicitly enabled experiment proves that the compact snapshot is a
fresh, parity-checked projection of that guard.

## Freshness And Invalidation

A hint is fresh only when all identity keys match:

- `goal_id`;
- `agent_id`;
- selected todo id or effective action;
- scheduler action;
- active state fingerprint;
- registry goal update fingerprint;
- runtime index fingerprint.

Invalidate immediately when:

- user feedback arrives;
- a todo is added, claimed, completed, deferred, superseded, or reassigned;
- a gate is resolved or introduced;
- scheduler action changes;
- workspace guard or capability gate changes;
- run history receives a material transition;
- the app cannot prove the snapshot came from the same local registry path.

Missing, stale, or mismatched hints fall back to the CLI. Fallback is success,
not an error.

## Activation Levels

| Level | Name | Allowed Behavior | Default |
| --- | --- | --- | --- |
| 0 | `disabled` | No hook/cache. Heartbeats call the CLI as they do today. | Yes |
| 1 | `advisory_hint_lab` | App may display or inject a compact hint, but workers still run CLI before delivery work. | No |
| 2 | `quiet_wait_cache_lab` | App may satisfy quiet wait/no-op routing from a fresh parity-checked hint; run-now delivery still uses CLI. | No |
| 3 | `guard_projection_lab` | App may project a compact guard for run-now routing only for explicitly opted-in goals after parity evidence. | No |
| 4 | `default_candidate` | Candidate for default, blocked until the evidence gates and rollout decision pass. | No |

Enablement must be explicit, local, and reversible, for example a future
registry or app setting such as:

```yaml
codex_app_control_plane_hook_cache:
  enabled: true
  level: advisory_hint_lab
  owner_decision_id: todo_publicsafe
```

LoopX should not infer enablement from the presence of Codex App, a heartbeat,
or a large payload.

## Evidence Gates

Before level 2 or above:

1. **Parity:** at least 100 sampled heartbeat decisions compare the hook
   snapshot against fresh `quota should-run` output with zero false run and zero
   false quiet-skip decisions.
2. **Staleness:** fixture and live tests cover user feedback, todo mutation,
   gate changes, scheduler reset, workspace guard changes, and run-history
   material transitions.
3. **Privacy:** public/private scans prove the snapshot does not contain raw
   active-state bodies, chat transcripts, local paths, credentials, private
   links, or raw run-history records.
4. **Fallback:** every missing, stale, malformed, or mismatched hint triggers the
   normal CLI path and does not spend quota for the fallback decision itself.
5. **Cost:** measured prompt-token and latency savings are material enough to
   justify the extra host-runtime complexity.
6. **Operator Review:** the owner or maintainer records a rollout decision that
   names the enabled level, rollback path, and metrics to monitor.

Before default candidate:

- evidence must include multiple goals, at least one peer task workspace guard,
  at least one user-gated goal, at least one monitor-only wait, and at least one
  active run-now goal;
- the default must be reversible without migrating project state;
- the CLI-only path must remain documented and tested.

## Fallback CLI Path

The fallback remains:

```bash
loopx --format json --registry "$HOME/.loopx/registry.global.json" \
  quota should-run --goal-id <goal-id> --agent-id <agent-id>
```

Workers may request a compact local summary with `jq` or a future LoopX command,
but the fallback must preserve the same `interaction_contract`,
`scheduler_hint`, `workspace_guard`, `goal_boundary`, and todo projection
semantics.

## Recommended First Experiment

Start with level 1 only:

1. Add a host-provided compact snapshot fixture outside the default heartbeat
   path.
2. Compare it against a fresh CLI guard in a smoke and in one manual heartbeat
   canary.
3. Record token/latency savings and every mismatch.
4. Keep all delivery, writeback, publication, and quota spend decisions on the
   existing CLI guard.

This gives LoopX a measurable product path without silently changing the
behavior of existing Codex App heartbeats.
