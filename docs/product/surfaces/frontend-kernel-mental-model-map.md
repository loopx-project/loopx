# Frontend Kernel-to-Mental-Model Map

LoopX needs a rich kernel because long-running agent work has real failure
modes: drifting goals, hidden gates, duplicated work, stale evidence, lost
handoffs, and uncontrolled compute. The frontend should not expose that kernel
as the user's everyday vocabulary.

The product rule is:

> Keep the kernel explicit for correctness, but compress it into a smaller
> mental model for daily operation.

## Five User Concepts

The default management surface should teach only five concepts.

| User concept | User question | Primary UI job |
| --- | --- | --- |
| Goal | What are we trying to achieve now? | Show current objective, boundary, and selected anchor. |
| Next step | What will the agent do next? | Show one bounded action plus a few nearby candidates when useful. |
| Blocker / permission | Where is human judgment required or forbidden? | Show concrete decisions, gates, and safety boundaries. |
| Evidence | Why should I believe progress happened? | Show compact validation, artifacts, and confidence. |
| Continue state | Can I hand this back to the agent? | Show run/wait/observe/repair/handoff readiness. |

Every top-level card, navigation label, and empty state in the ops surface
should map to one of these concepts.

## Kernel Concepts

The kernel can keep the concepts it needs for correctness.

| Kernel concept | Why it exists | Default exposure |
| --- | --- | --- |
| `goal_state` | Durable truth about objective, boundary, and current belief. | Compressed into **Goal**. |
| `user_gate` | Human decision boundary that the agent cannot cross. | Visible only when action is needed, under **Blocker / permission**. |
| `todo` | Executable work units and owner/user tasks. | Visible as **Next step** plus a small queue. |
| `claim` | Multi-agent collision avoidance. | Hidden by default; show as lane owner or diagnostic detail. |
| `scope` | What a specific agent can and cannot do. | Summarized under agent lane or blocker detail. |
| `evidence` | Proof that a state transition is trustworthy. | Visible as compact **Evidence**. |
| `run_history` | Audit log and replay/debug substrate. | Folded behind evidence or diagnostics. |
| `quota` | Whether an automatic turn may spend compute now. | Rendered as **Continue state**, not raw counters. |
| `handoff` | Context package for the next loop or agent. | Rendered only when copying, resuming, or debugging. |

These objects are not redundant. They separate truth, work, authority,
ownership, proof, compute, and transfer. The UI's job is to prevent users from
paying that complexity cost unless they are debugging.

## Projection Contract

The read model should provide an explicit compression layer instead of letting
each component invent labels.

```yaml
mental_model_projection_v0:
  goal:
    title: "Current objective"
    boundary_summary: "What is in and out of scope"
    anchor: "Optional selected proof path"
  next_step:
    primary_action: "One bounded action"
    nearby_candidates: ["Optional small queue"]
    selected_reason: "Why this action is first"
  blocker_permission:
    status: clear | needs_user | forbidden | needs_repair
    concrete_question: "Only when needs_user"
    boundary_reason: "Only when forbidden or needs_repair"
  evidence:
    latest_summary: "Compact validation or artifact pointer"
    confidence: strong | partial | missing
    drilldown_ref: "Run or artifact id"
  continue_state:
    status: can_run | waiting | observe_only | needs_repair | handoff_ready
    reason: "Short operator-readable reason"
    next_safe_transition: "Optional CLI/control-plane transition"
  diagnostics:
    goal_state_ref: "debug only"
    todo_ids: ["debug/search"]
    claims: ["debug/multi-agent"]
    quota_ref: "debug only"
    run_history_refs: ["debug/audit"]
```

The same kernel source can still power search, debug, and audit views. The
default surface should read from the compressed fields first.

## Interaction Rules

- Default navigation and first-screen headings should use the five user
  concepts, not kernel names.
- Search can still accept todo ids, run ids, agent ids, and kernel terms because
  debugging needs exact handles.
- `claim`, `quota`, `scope`, and `handoff` should appear as detail chips,
  tooltips, or diagnostic rows unless they require user action.
- A user gate must show the concrete decision
  and the consequence of approving, rejecting, or deferring.
- Evidence should be compact by default and link to audit details. Raw logs,
  private traces, local paths, and sensitive material must not appear in public
  fixtures.
- A continue state should never say "run" only because quota allows compute. It
  must also respect gates, scope, write boundary, and evidence requirements.
- Review-feed cards should produce feedback in user language, then map it back
  to typed events such as `review_event_v0`, `feedback_signal_v0`, or
  `todo_update`.

## Dashboard Layout Implication

For the ops surface, a simple first screen is:

1. **Goal**: objective, active anchor, and boundary summary.
2. **Next step**: selected action, a small todo queue, and why it was selected.
3. **Needs your judgment**: concrete user gates and forbidden actions.
4. **Evidence**: last validated result, confidence, and artifact pointer.
5. **Can continue?**: run/wait/observe/repair/handoff state.

Diagnostics can sit behind expandable panels:

- todo explorer;
- agent lane details;
- claims and scope;
- quota and run history;
- handoff packet.

This keeps LoopX honest for long-running work while making the product feel like
a management surface, not a schema browser.

## Acceptance Criteria

The frontend mapping is acceptable when:

- a new user can explain the first screen using the five concepts above;
- every visible kernel label has a reason to be visible, such as search,
  debugging, or an active user decision;
- all diagnostic expansion still preserves exact ids for audit and recovery;
- the same projection can represent one project, all projects, and
  multi-agent lanes;
- review-feed actions use user-facing labels but write typed LoopX events.
