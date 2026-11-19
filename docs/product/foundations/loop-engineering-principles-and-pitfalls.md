# Loop Engineering Principles And Pitfalls

This is the short public-safe digest for people who want the LoopX operating
model before reading the longer product notes.

Loop Engineering is the practice of making long-running agent work recoverable,
reviewable, and steerable across many executor turns. The core problem is not
"keep a model process alive." The core problem is keeping the goal, human
judgment, evidence, cost, and next action coherent after the first prompt stops
being enough.

## Principles

### 1. Dynamic goals need a control plane

A static prompt is enough for a short task. A long-running Loop Agent needs a
durable place for goal state, user gates, todos, claims, scope, evidence, run
history, quota, and handoff. Chat memory is not a control plane.

### 2. Human gates are decisions, not interruptions

Human-in-the-loop should not mean asking for confirmation every few minutes. A
good gate records a concrete decision, the route it blocks, the safe default if
the user does not answer, and the work that may continue independently.

### 3. Safe fallback keeps progress honest

When a P0 route waits on a human or external signal, unrelated safe P1/P2 work
can continue. The fallback must stay visible as fallback; it must not hide the
blocked route or pretend the gate was resolved.

### 4. Guidance should preserve bounded autonomy

Priorities and recommended next actions should make a long-running loop easier
to steer, not turn a convenient shortlist into an accidental permission list.
The agent may choose any currently eligible action inside typed claims, scope,
capability, gate, quota, and safety boundaries, then bind and validate that
choice through the normal control-plane path.

### 5. Feedback is not permission

Human reward, review, and preference signals can influence future ranking and
planning, but they do not override gates, claims, scope, capability checks,
public/private boundaries, or quota. A useful hint is still only a hint.

### 6. Evidence should be compact and inspectable

The next agent turn needs enough evidence to recover the plot, but public
surfaces should not copy raw logs, transcripts, benchmark traces, credentials,
private notes, or local paths. Store compact artifacts and source references.

### 7. Quota protects attention as much as compute

A loop can waste user trust even when it uses cheap tokens. Quota should count
whether a turn produced a verified transition, not just whether an agent spoke.
Monitor-only or status-only turns should stay quiet unless something changed.

### 8. Loop Agents need performance review

A Loop Agent should be evaluated by project-level value, not only by a
single-task benchmark score. Track output quantity, output quality, token cost,
and user attention cost so the human operator can decide which lanes deserve
more trust.

## Pitfalls

### 1. Treating "run longer" as the product

Longer loops without better state usually create larger drift. The product is
recoverable work, not background motion.

### 2. Letting summaries replace writeback

If a route, todo, gate, lesson, or priority only exists in chat, the next agent
will eventually lose it. Important plans should become typed todos, gates,
evidence, review notes, or refresh-state records.

### 3. Hiding blocked work behind busy fallback

Safe side work is valuable only when the blocked primary route remains visible.
Otherwise the user sees activity but loses control.

### 4. Confusing review feed taste with hard policy

"This was useful" can rank future work. "Do not publish this" is a boundary or
gate. The UI and control plane should keep those effects separate.

### 5. Counting every artifact as outcome progress

A doc, smoke, or status refresh can be useful preparation, but it should not
claim primary outcome progress unless it directly moves the selected outcome.

### 6. Building a second source of truth in the frontend

Dashboards should project LoopX state and call typed writes. They should not
invent hidden queues, private rankings, or browser-only control decisions that
the CLI and future agents cannot read.

### 7. Over-claiming benchmark uplift

Benchmarks are evidence, not the whole product. LoopX should claim the narrower
value it can show: long-running agent work becomes easier to inspect, steer,
recover, and compare.

## Minimum Viable Loop

A useful LoopX-managed loop should answer five questions:

1. What is the current goal and boundary?
2. What human decision, if any, is blocking which route?
3. What did the last agent turn validate?
4. What will happen next if the user does nothing?
5. What feedback would change the plan?

If those answers are visible, a human can manage a Loop Agent. If they are not,
the system is probably just running an agent repeatedly.

## Related Notes

- [Intelligent management surface](../surfaces/intelligent-management-surface.md)
- [Non-technical operator status model](../surfaces/nontechnical-operator-status-model.md)
- [Project-level reward model](project-level-reward-model.md)
- [Reward-style replanning hints](reward-style-replanning.md)
