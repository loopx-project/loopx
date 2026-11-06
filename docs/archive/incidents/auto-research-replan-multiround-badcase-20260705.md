# Auto-Research Replan And Multi-Round Demo Bad Case

> Historical incident: the tmux auto-research demo has been retired. Commands and
> code references below describe the original incident, not current entrypoints.

Date: 2026-07-05

Audience: LoopX auto-research owners, quota/status maintainers, todo/replan
owners, and multi-agent demo maintainers.

## Summary

A visible auto-research demo reached a state where the operator expected
continued multi-agent research and improvement, but the active lane did not
enter replan. The visible experience also looked like a short single pass
rather than several minutes of role-authored research. Earlier status updates
could make this look better than it was by treating worker-loop plumbing,
summary artifacts, or pane-local ticks as proof of meaningful multi-round
research.

The reusable failure has two parts:

1. A completed auto-research advancement left no runnable successor for the
   current agent, but quota still selected quiet monitor instead of a bounded
   replan action.
2. The demo separated "research mechanism advanced" from "visible research
   improved the artifact" too weakly, so internal loops or summaries could be
   confused with real collaborative research.

This record is public-safe. It does not include raw active-state bodies, local
runtime paths, raw logs, trajectories, credentials, private artifacts, or
operator-specific planning context.

## Observed Public-Safe Shape

The relevant quota shape was:

```text
quota should-run --goal-id <goal-id> --agent-id <side-agent>
should_run = false
effective_action = monitor_quiet_skip
interaction_contract.mode = monitor_quiet_skip
user_todo_summary.open_count = 0
current_agent_claimed_advancement_count = 0
current_agent_claimed_monitor_count > 0
first_executable_items = 0
goal_frontier_projection.replan_required = false
agent_todo_summary.todo_succession_warning =
  completed_advancement_without_successor
```

The important detail is that LoopX did notice a succession problem, but only as
a warning. The selected interaction still told the agent to stay quiet. That is
reasonable for a pure monitor lane; it is wrong when the monitor-only shape is
caused by an unfinished advancement slice that lost its next executable step.

## Why It Did Not Enter Replan

LoopX selected the local truth that was easiest to obey: there were no user
todos, no current-agent advancement candidates, and the remaining claimed work
was monitor or blocker class. In that view, `monitor_quiet_skip` was a valid
no-spend action.

The missing rule is that a current-agent `completed_advancement_without_successor`
warning should promote into an executable routing repair when all of these are
true:

```text
completed advancement was tracked for successor continuity
no current-agent advancement candidate exists
goal or operator intent still expects follow-up advancement
no harder safety gate is present
```

In that state, quota should not ask the agent to do ordinary delivery. It should
ask for a bounded control-plane replan: add/link a successor todo, mark
`no_followup=true` with a reason, or explicitly hand off the next frontier to a
different agent.

## Why It Looked Like Single-Round Research

The implementation already corrected one earlier fake path: real research
actions in `demo/auto_research/worker_runtime.py` now return a
manual-research-required result instead of silently fabricating research
outputs. That is the right authenticity boundary.

The visible product problem remains: several surfaces still describe progress
using mechanism terms such as worker-loop rounds, pane-local ticks, compact
summaries, or precomputed metric summaries. Those are useful plumbing signals,
but they are not equivalent to visible role-authored research. A high-quality
demo should show roles reading the contract, proposing a hypothesis, changing
or evaluating the artifact, recording evidence, reviewing it, and then routing
the next frontier. If that chain only happens once, the UI should say one
visible research pass happened. It should not imply multi-round improvement.

The KNN demo can be real: the generated workspace supplies a baseline solution,
editable scope, protected scope, and eval commands through
`demo/auto_research/knn_demo_workspace.py`. The question text does
not need to carry the baseline when the preset creates that contract. The bad
case is not that KNN lacks a baseline; it is that the visible flow did not make
successive, role-authored research and improvement obvious enough, and the
control plane let the follow-up frontier disappear.

One concrete visible-run subcase is an evaluator pane waking before the executor
has appended evidence. That is not a reason to add a central workflow driver.
The correct first-demo shape is Codex CLI pane/goal driven on the outside and
LoopX state/frontier driven on the inside: the evaluator todo should be
resumable on the executor evidence step, so the evaluator waits rather than
recording `evidence=0` and closing itself early.

A follow-up visible run confirmed the smaller fix path:

- The evaluator seed todo waits on the executor dev-evidence todo through
  `resume_when`, so the first evaluator turn reports a wait state instead of
  closing on `evidence=0`.
- The fixed wake broadcaster must resolve panes inside the requested tmux
  session only and target stable lane metadata, not mutable Codex pane titles or
  all tmux panes on the host.
- Long-running Codex TUI panes may only show the bottom input prompt in the
  capture window. Wake readiness and submit retry must recognize that footer
  shape and retry when the fixed prompt tail remains in the input box.

With those fixes, a real visible KNN run advanced through executor evidence,
evaluator review, and a curator successor handoff without a hidden workflow
driver. The remaining product question is how much of this state should be
summarized as a compact `research_contract_v0` projection so future runs can
explain the current stage without reading pane transcripts.

## Responsibility Split

This was partly a LoopX control-plane gap:

- A completed advancement without a successor was projected as a warning, not
  as an executable replan obligation.
- Quiet monitor took precedence over "repair the missing successor" even though
  the current lane had no remaining advancement frontier.
- Auto-research state does not yet have a compact research contract projection
  that says which research stage is current, what evidence is required, and
  when the next role todo must be projected.

It was also an agent/process failure:

- I accepted mechanism evidence as product evidence and overstated the
  significance of worker-loop or tick-based progress.
- I closed or treated the slice as quiet without first ensuring a successor
  todo, explicit `no_followup` rationale, or handoff.
- I did not challenge the contradiction early enough: the operator wanted
  continued visible research, while quota allowed a monitor-only no-op.

## Desired Semantics

Auto-research needs a small state-level contract, not a large research-specific
framework. A minimal contract should be enough:

```json
{
  "schema_version": "research_contract_v0",
  "question": "public-safe research question",
  "current_stage": "contract|hypothesis|dev_eval|holdout_eval|review|replan",
  "target": {
    "visible_rounds_min": 2,
    "evidence_required": ["hypothesis", "dev_eval", "holdout_eval", "review"]
  },
  "frontier": {
    "next_role": "research-executor",
    "next_action": "run_dev_eval",
    "claim_boundary": "public-safe editable/protected scope"
  },
  "gates": {
    "user_required": false,
    "private_material_required": false
  }
}
```

This contract should be state, not extra business logic in the user or
auto-research entry layer. The user surface stays thin: it starts a topic and
optionally selects a preset/workspace. Auto-research stays thin: it creates the
contract and role frontier. The generic todo/quota/replan machinery keeps the
next executable step alive.

## Follow-Up Work

### P0: Promote Missing Successor To Replan

When a current-agent completed advancement has `succession_tracked=true` and no
successor or explicit `no_followup` reason, quota should select a bounded
replan/writeback action instead of `monitor_quiet_skip`. The allowed action is
control-plane repair only: add/link a successor todo, record terminal rationale,
or hand off to the correct role.

### P0: Make Auto-Research Continuation State-Driven

Introduce the smallest useful `research_contract_v0` projection in LoopX state.
It should identify the current research stage, expected evidence, next role,
next action, and gate status. If the contract is not satisfied and there is no
runnable frontier, replan must project the next role todo.

### P0: Stop Claiming Multi-Round Research From Plumbing Alone

Visible multi-round verification must require visible role-authored evidence
and at least two collective passes through the role set. Worker-loop summaries,
pane-local tick counts, and generic evaluation summaries can support
diagnostics, but they must not be presented as research improvement by
themselves.

### P1: Improve The Visible Pane Experience

The first screen of each research pane should emphasize the role's actual
research payload: hypothesis, edit/eval result, evidence, review decision, and
next frontier. It should not foreground quota JSON, transcript paths, or
diagnostic commands unless the role is genuinely in repair mode.

### P1: Tighten No-Followup Completion

Auto-research completions should avoid defaulting to terminal
`no_followup=true`. A role can close a todo without a successor only when the
research contract is satisfied, a harder gate is present, or the completion
records a public-safe terminal rationale.

## Related Patterns

- `monitor_replan_noop_loop`
- `agent_scoped_replan_broadcast_gap`
- `todo_succession_gap`
- `tiny_turn_under_delivery`
- `agent_scoped_no_candidate_gap`
- `agent_scoped_replan_precedence_gap`
