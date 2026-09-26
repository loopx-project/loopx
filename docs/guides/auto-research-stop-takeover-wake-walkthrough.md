# Auto Research Stop, Takeover, And State-Aware Wake

Contributor walkthrough for the shipped Auto Research control transitions.
Reuse the existing one-command path. Do not add a second launcher, and do not
change the README first screen without maintainer preview.

Runtime control transitions landed in
[#2786](https://github.com/huangruiteng/loopx/pull/2786) (related
[#2783](https://github.com/huangruiteng/loopx/issues/2783)). This guide is the
operator-facing proof path for GH-C43.

Canonical command path:
[Auto-research command path](../../demo/auto_research/README.md).

## What This Walkthrough Covers

| Transition | Operator signal | Result |
| --- | --- | --- |
| External stop | Place `workspace/.loopx-auto-research-stop` | Worker-loop exits with `stop_reason = operator_stop_requested` before the next round |
| Operator takeover | `auto-research start … --execute --attach` | Attach to the visible tmux session; skip default background wake so the operator acts first |
| State-aware wake | `--wake-visible-after-launch` with `--no-attach` | Only lanes with a selected runnable todo and `quota.should_run` receive the fixed A2A wake |
| Quota pause | Live `quota.ok && !quota.should_run` | Worker turn returns `mode = paused_by_quota`; the loop reports `stop_reason = quota_paused` |

Individual `worker-turn` calls are not gated by the stop marker. That preserves
manual lane advance while the multi-round loop is paused.

## Boundaries

- Stay on the shipped `loopx auto-research start` / `worker-loop` / `worker-turn`
  commands. Do not invent a parallel demo launcher.
- Use synthetic or redacted public-safe evidence only. No credentials, raw
  Codex transcripts, private paths, or live model claims.
- `--attach` and `--wake-visible-after-launch` conflict by design: choose
  operator takeover or background wake, not both.
- Stopping the worker-loop is not the same as killing the tmux session. Remove
  the stop marker to resume the loop; use `tmux kill-session` only when the
  visible rehearsal itself should end.

## 1. Start From The Existing Command Path

Export one explicit goal and workspace identity first, and reuse it in every
later command. Without `--goal-id`, `start` creates a fresh timestamped goal
that the stop/resume commands below could not address:

```bash
export GOAL_ID="loopx-auto-research-demo"
export WORKSPACE="$HOME/loopx-auto-research-demo"

export LOOPX_REGISTRY="$HOME/.loopx/registry.global.json"
export LOOPX_RUNTIME_ROOT="$HOME/.loopx"

loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  auto-research start "How should we evaluate autonomous research agents?" \
  --goal-id "$GOAL_ID" \
  --workspace "$WORKSPACE" \
  --create-workspace

loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  auto-research start "How should we evaluate autonomous research agents?" \
  --goal-id "$GOAL_ID" \
  --workspace "$WORKSPACE" \
  --create-workspace \
  --execute \
  --replace-existing
```

For JSON automation that records wake evidence without attaching:

```bash
loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  --format json auto-research start \
  "How should we evaluate autonomous research agents?" \
  --goal-id "$GOAL_ID" \
  --workspace "$WORKSPACE" \
  --create-workspace \
  --execute \
  --no-attach \
  --wake-visible-after-launch
```

## 2. Stop The Worker Loop Without Killing State

While a multi-round worker-loop is running against the `$WORKSPACE` research
workspace, place the stop marker there. `worker-loop` reads the marker from
its own working directory, so run it from `$WORKSPACE`:

```bash
touch "$WORKSPACE/.loopx-auto-research-stop"
```

The next round check exits with:

```json
{
  "ok": true,
  "stop_reason": "operator_stop_requested",
  "turn_count": 0
}
```

when the marker is present before round 1, or with prior turns retained when the
marker appears between rounds. The marker is checked at the top of every round,
not only at process entry.

Resume by removing the marker and calling `worker-loop` again from the same
workspace with the same goal:

```bash
rm -f "$WORKSPACE/.loopx-auto-research-stop"

cd "$WORKSPACE"

loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  --format json auto-research worker-loop \
  --goal-id "$GOAL_ID" \
  --agent-id research-curator \
  --agent-id hypothesis-proposer \
  --agent-id research-executor \
  --agent-id evaluator-promoter \
  --max-rounds 2 \
  --execute \
  --complete-selected-todo
```

`operator_stop_requested` is distinct from `quota_paused`, `no_executed_turns`,
`no_runnable_frontier`, and `max_rounds`.

## 3. Take Over A Visible Lane

Immediate operator takeover uses the same start command with `--attach`,
targeting the same explicit goal and workspace. This skips the default
visible-role wake so the operator enters the tmux session first:

```bash
loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  auto-research start "How should we evaluate autonomous research agents?" \
  --goal-id "$GOAL_ID" \
  --workspace "$WORKSPACE" \
  --create-workspace \
  --execute \
  --attach
```

Inside the session, interrupt a pane, run a single-lane worker-turn or advance
todos manually, then continue. Attach later without relaunching:

```bash
tmux attach -t loopx-auto-research
```

Stop only the visible rehearsal (not the LoopX goal state) with:

```bash
tmux kill-session -t loopx-auto-research
```

## 4. State-Aware Wake Filter

When wake is requested after launch, Auto Research loads each lane frontier
through `load_auto_research_worker_frontier()` and skips lanes that should not
receive the fixed A2A prompt:

| Skip reason | Meaning |
| --- | --- |
| `quiet_completion_allowed` | Goal already allows quiet completion |
| `no_selected_todo` | Frontier has no selected runnable todo |
| `quota_should_run_false` | Scheduler blocked the lane |
| `no_agent_mapping` | Started lane has no agent id mapping |
| `frontier_load_failed` | Frontier load failed; receipt uses `error_code`, never a raw exception string |

If every lane is filtered, the wake receipt is a public-safe no-op:

```json
{
  "ok": true,
  "schema_version": "multi_agent_pane_a2a_wakeup_v0",
  "mode": "no_op_all_filtered",
  "target_lanes": [],
  "prompt_delivery": "skipped_no_ready_lanes",
  "wakeup_model": "state_aware_filter_no_ready_lanes"
}
```

An empty ready set must not call the underlying wake helper with `[]`, because
legacy wake semantics treat an empty list as “all lanes”.

Filtered lanes appear under `state_aware_filter` for auditability. The
broadcaster still does not select todos or write LoopX research truth.

## 5. Quota Pause Versus Operator Stop

When a turn sees `quota.ok` and `quota.should_run == false`, it returns
`mode = paused_by_quota` before execution. If every turn in a round is paused
that way, the worker-loop stops with `stop_reason = quota_paused`.

Do not treat resource pressure as operator intent:

- `quota_paused` — scheduler said not to spend;
- `operator_stop_requested` — operator placed the stop marker.

## Reproducible Validation

No live model is required. Run the synthetic smokes that pin each transition,
then the optional cycle smoke:

```bash
python3 examples/auto-research-stop-marker-smoke.py
python3 examples/auto-research-state-aware-wake-smoke.py
python3 examples/auto-research-quota-pause-smoke.py
python3 examples/auto-research-stop-takeover-walkthrough-smoke.py
python3 examples/auto-research-demo-e2e-worker-loop-smoke.py
python3 examples/auto-research-visible-worker-hook-smoke.py
python3 examples/showcase-catalog-smoke.py
loopx check --scan-path docs/showcases --scan-path docs/guides
```

## Evidence Boundary

This walkthrough documents shipped public contracts with synthetic fixtures.
It does not claim that a research finding is production-ready, does not record
raw logs or credentials, and does not promote Auto Research as a second core
scheduler. Promotion still requires rollout-backed evidence and normal LoopX
gate or writeback rules.
