# Experiment Controller Milestone

LoopX should connect a long-running experiment controller only when the
connection improves the work beyond bare Codex App goal mode. The milestone is
not "start watching another repo." It is a product gate for better experiment
context, better human reward capture, and easier multi-project operation.

This document is intentionally generic and public-safe. Project-specific run
ids, local paths, production metrics, and private conclusions belong in a
private adapter or run payload.

## When To Connect

Connect an experiment controller when all three gates are true:

- The project has a stable objective, current experiment hypothesis, latest
  comparable result, and next gating condition that can be represented without
  exposing private details.
- LoopX can preserve context across runs better than a normal goal-mode
  thread: objective, active branch, experiment route, metric target, known
  failure modes, blocked state, and latest recommended action survive reloads.
- The dashboard or status export gives the operator a clearer multi-project
  view than the Codex App thread list: what needs human judgment, what is ready
  for Codex work, what is waiting on external evidence, and what is unsafe to
  advance.

Do not connect if the adapter can only mirror a chat summary, if the current
experiment result is not comparable, or if the next action depends on private
production evidence that cannot be safely summarized.

## Experiment Board

Long-window experiment controllers should have a project-local experiment
board. The board is the current evidence surface for the adapter; it is not a
raw log dump.

The board should separate:

- objective and primary decision metric;
- decision window and comparable baseline window;
- guardrail metrics;
- non-goals and unsafe shortcuts;
- active tasks and what to watch;
- completed anchors and route history;
- launch or compute quota;
- next handoff condition.

Adapters should anchor the goal identity in the decisive evidence named by the
board. Runtime risks, training-only movement, or non-comparable metrics may be
guardrails, but they should not replace the primary goal.

## Better Than Bare Goal Mode

The comparison target is the default Codex App goal loop with one thread and
local chat context.

LoopX must add:

- **Durable context**: every run writes a compact public-safe index plus a
  private payload when needed. The next controller tick should know the latest
  experiment state without rereading the whole thread.
- **Explicit gates**: the run record says whether the next action is run,
  inspect, wait, ask user, or block. A stale or incomparable result should not
  look like progress.
- **Reward capture**: human feedback is structured as reward events, not lost
  in chat. The operator can say which result, judgment, or route choice was
  good, bad, surprising, or worth repeating.
- **Cross-project queueing**: one screen can compare several goals by waiting
  party, severity, latest run health, and handoff state.
- **Safety boundary**: public examples and status exports stay sanitized while
  private adapters can keep richer local evidence.

If these fields are not available, the experiment should remain in normal
Codex App goal mode until the adapter catches up.

## Reward Signal Model

Human reward is most useful when it is close to the decision being judged. An
experiment-controller adapter should support small reward records with this
shape:

```json
{
  "recorded_at": "2026-06-01T00:00:00+00:00",
  "goal_id": "example-experiment-goal",
  "run_id": "2026-06-01T00-00-00Z",
  "decision": "continue_route",
  "reward": "positive",
  "reason_summary": "latest comparable metric beat the previous route and validation was aligned",
  "follow_up": "promote the route to the next longer-window check"
}
```

The public index should keep only compact, non-sensitive fields. Private
payloads may keep richer evidence. `loopx status` keeps only
`recorded_at`, `decision`, `reward`, `reason_summary`, and `follow_up` under
`human_reward`, so the dashboard can show that a human reward signal exists and
what class of decision it judged.

Use `loopx reward` to append this compact signal to an existing run:

```bash
loopx reward \
  --goal-id example-experiment-goal \
  --actor-kind owner \
  --run-generated-at 2026-06-01T00:00:00+00:00 \
  --decision continue_route \
  --reward positive \
  --reason-summary "latest comparable metric beat the previous route and validation was aligned" \
  --follow-up "promote the route to the next longer-window check"
```

The writer appends an index overlay; it does not rewrite the private run
payload. That keeps operator feedback close to the judged decision while
preserving the public/private evidence boundary.

The dry-run and append response also carry coordination fields. Codex can copy
the Chinese `active_state_summary` into the active state after the real append,
while project agents should use `project_agent_visibility.history_command` to
read the reward from run history. This avoids making chat text, dashboard copy
packets, or active-state prose the durable reward source.
When that active-state summary should be written by the CLI, use the explicit
`--write-active-state-summary` flag; `--dry-run --write-active-state-summary`
previews both the overlay and the state edit.

The dashboard may generate a `Reward CLI Draft` for the selected goal and
latest run. That draft should default to `--dry-run`; the operator still records
the judgment through the CLI until browser-side writes can enforce the same
local-only boundary and public-safe text checks.

For local dashboards backed by `loopx serve-status`, the draft can also
round-trip through `POST /reward/dry-run`. That endpoint validates the selected
goal/run and compact reward text but always returns `appended=false`.

Browser-side append remains a separate opt-in design gate. The safety boundary
is defined in
[dashboard-reward-write-boundary.md](../../reference/contracts/dashboard-reward-write-boundary.md).

## Controller Readiness Model

Readiness is different from reward. It answers "what level of controller
connection is safe right now?"

Experiment-controller adapters should summarize readiness with compact fields:

```json
{
  "classification": "ready_for_read_only_not_decision",
  "read_only_observer_ready": true,
  "decision_advisor_ready": false,
  "write_controller_ready": false,
  "missing_gates": [
    "human_reward_capture",
    "aligned_eval_decision_evidence"
  ],
  "review_judgment": "safe for read-only observation, not route decisions",
  "next_handoff_condition": "record aligned eval evidence and one human reward event"
}
```

The common progression is:

- read-only observer: safe when durable context and multi-project visibility
  are present.
- decision advisor: safe after comparable evidence and human reward are
  recorded.
- write controller: remains opt-in; LoopX should not imply mutation
  permission from observation or advice readiness.

## Readiness Checklist

Before connecting a real experiment controller, validate:

- `loopx status` exposes the goal, latest run, contract health, and
  attention queue without local path leaks.
- The adapter writes compact run history with `classification`,
  `recommended_action`, `health_check`, and artifact availability.
- The latest run includes controller readiness so the operator can see whether
  the goal is observation-only, decision-advisor ready, or still missing gates.
- The next action separates "run another experiment" from "wait for comparable
  evidence" and "ask a human to judge a tradeoff."
- The dashboard can show the goal next to unrelated goals without collapsing
  into a single-thread view.
- A reward event can be written and later summarized without copying private
  production evidence into public files.

## Current Implementation Slice

The current public slice is a read-only experiment-controller contract:

- adapter classification vocabulary for `await_eval`, `inspect_result`,
  `design_next_experiment`, `needs_human_reward`, and `blocked_by_safety`;
- compact controller-readiness schema and dashboard panel;
- compact reward-event schema and status export field;
- dashboard badge and run-history panel showing whether the latest run has
  controller readiness and human reward;
- dashboard reward dry-run validation through the loopback status server;
- example sanitized run and reward files.

Private projects can opt into the adapter by writing a project-local state file
and one compact run record. They should connect a real controller only after
the readiness checklist above is true.
