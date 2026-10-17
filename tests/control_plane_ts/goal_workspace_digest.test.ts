import assert from "node:assert/strict";
import test from "node:test";

import {
  GOAL_WORK_DISPLAY_CLASSES,
  compileGoalWorkspaceDigest,
  isGoalWorkTodo,
  type GoalWorkspaceDigestInput,
} from "../../loopx/control_plane/presentation/goal_workspace_digest.ts";

const observedAt = "2026-10-11T10:00:00.000Z";
const base: GoalWorkspaceDigestInput = { observedAt, activation: "active", todos: [], attention: [],
  attentionSummary: { blocking: 0, optional: 0, coverage: "complete" } };
const running = (observed: boolean, quiet = false, lastActivityAt: string | null = "2026-10-11T09:58:00.000Z") =>
  ({ kind: "running" as const, observed, quiet, lastActivityAt });

test("a declared Goal objective is independent of work, progress and optional acceptance", () => {
  assert.equal(compileGoalWorkspaceDigest({ ...base, objective: "  Preserve the original research question.  " }).objective,
    "Preserve the original research question.");
  for (const objective of [undefined, null, "", "  "]) {
    const digest = compileGoalWorkspaceDigest({ ...base, objective,
      todos: [{todoId: "work", done: false}], execution: running(true) });
    assert.equal(digest.objective, null, "Missing objective content is not inferred from activity or work");
  }
});

test("absent usage is unknown, never zero, and a measured zero stays zero", () => {
  for (const usage of [undefined, null, {}]) {
    const digest = compileGoalWorkspaceDigest({ ...base, usage });
    assert.deepEqual(digest.usage, { window: null, agentTimeMs: null, costUsd: null, coverage: "unknown" });
  }
  const zero = compileGoalWorkspaceDigest({ ...base, usage: { costUsd24h: 0, durationMs24h: 0 } });
  assert.deepEqual([zero.usage.window, zero.usage.costUsd, zero.usage.agentTimeMs], ["24h", 0, 0]);
  const nonFinite = compileGoalWorkspaceDigest({ ...base, usage: { costUsd7d: Number.NaN } });
  assert.equal(nonFinite.usage.window, null);
});

test("Agent time and Cost share the widest measured window without borrowing a narrower value", () => {
  const digest = compileGoalWorkspaceDigest({ ...base, usage: { costUsd7d: 18.4, durationMs24h: 60_000 } });
  assert.equal(digest.usage.window, "7d");
  assert.equal(digest.usage.costUsd, 18.4);
  assert.equal(digest.usage.agentTimeMs, null, "a 24h duration must not appear beside a 7-day cost");
  const costless = compileGoalWorkspaceDigest({ ...base, usage: { durationMs7d: 3_600_000, costUsd24h: 3.1 } });
  assert.deepEqual([costless.usage.window, costless.usage.agentTimeMs, costless.usage.costUsd], ["7d", 3_600_000, null],
    "a 24h cost must not appear beside a 7-day duration");
  const narrow = compileGoalWorkspaceDigest({ ...base, usage: { costUsd24h: 3.1, durationMs24h: 120_000 } });
  assert.deepEqual([narrow.usage.window, narrow.usage.costUsd, narrow.usage.agentTimeMs], ["24h", 3.1, 120_000]);
});

test("work counts exclude monitors, take done from the owner and agree with the ribbon", () => {
  const todos = [
    { todoId: "a", done: false, status: "open", taskClass: "advancement_task" },
    { todoId: "b", done: false, status: "blocked", taskClass: "advancement_task" },
    { todoId: "c", done: false, status: "deferred", taskClass: "advancement_task" },
    { todoId: "m", done: false, status: "open", taskClass: "continuous_monitor" },
    { todoId: "d", done: true, status: "done", taskClass: "advancement_task" },
  ];
  const digest = compileGoalWorkspaceDigest({ ...base, todos, doneCount: 54, openCount: 4 });
  assert.equal(digest.work.open, 3);
  assert.equal(digest.work.done, 54);
  assert.equal(digest.work.total, 57);
  assert.deepEqual(digest.work.classes, { running_observed: 0, needs_you: 0, waiting_dependency: 0, open: 1, blocked: 1, deferred: 1, done: 54 });
  const ribbon = GOAL_WORK_DISPLAY_CLASSES.reduce((sum, name) => sum + digest.work.classes[name], 0);
  assert.equal(ribbon, digest.work.total, "every counted Todo lands in exactly one segment");
  assert.equal(todos.filter(isGoalWorkTodo).filter((todo) => !todo.done).length, digest.work.open,
    "the board predicate and the strip count the same open work");
});

test("a done count below the listed completions cannot shrink, and no owner count stays unknown", () => {
  const todos = [{ todoId: "x", done: true }, { todoId: "y", done: true }, { todoId: "z", done: false }];
  assert.equal(compileGoalWorkspaceDigest({ ...base, todos, doneCount: 1 }).work.done, 2);
  const unknown = compileGoalWorkspaceDigest({ ...base, todos });
  assert.equal(unknown.work.done, null);
  assert.equal(unknown.work.total, null);
  assert.equal(unknown.work.open, 1);
});

test("a projected open count above the listed items marks the work count partial", () => {
  const todos = [{ todoId: "a", done: false }, { todoId: "m", done: false, taskClass: "continuous_monitor" }];
  assert.equal(compileGoalWorkspaceDigest({ ...base, todos, openCount: 2 }).work.coverage, "complete");
  const truncated = compileGoalWorkspaceDigest({ ...base, todos, openCount: 9 });
  assert.equal(truncated.work.coverage, "partial");
  assert.deepEqual([truncated.work.listedOpen, truncated.work.projectedOpen], [2, 9]);
});

test("a bounded list without owner totals never becomes complete work coverage", () => {
  const todos = [{ todoId: "visible", done: false }];
  const unknown = compileGoalWorkspaceDigest({ ...base, todos });
  assert.equal(unknown.work.open, 1, "The listed open work remains useful");
  assert.equal(unknown.work.done, null);
  assert.equal(unknown.work.total, null);
  assert.equal(unknown.work.coverage, "unknown", "A missing owner total does not prove the list is exhaustive");
  const knownDone = compileGoalWorkspaceDigest({ ...base, todos, doneCount: 0 });
  assert.equal(knownDone.work.done, 0, "A measured zero is retained");
  assert.equal(knownDone.work.total, null, "A known done count does not establish the missing open total");
  assert.equal(knownDone.work.coverage, "unknown");
  const complete = compileGoalWorkspaceDigest({ ...base, todos, doneCount: 0, openCount: 1 });
  assert.equal(complete.work.coverage, "complete");
  assert.equal(complete.work.total, 1);
});

test("running treatment needs an observed, fresh execution and the timeline's running step", () => {
  const todos = [{ todoId: "a", done: false, status: "open" }, { todoId: "b", done: false, status: "open" }];
  const live = compileGoalWorkspaceDigest({ ...base, todos, runningTodoIds: ["a"], execution: running(true) });
  assert.equal(live.work.classes.running_observed, 1);
  assert.equal(live.work.classes.open, 1);
  for (const execution of [running(true, true), running(false), { kind: "idle" as const }, { kind: "unknown" as const }, undefined]) {
    const digest = compileGoalWorkspaceDigest({ ...base, todos, runningTodoIds: ["a"], execution });
    assert.equal(digest.work.classes.running_observed, 0, JSON.stringify(execution));
  }
});

test("only a listed unfinished prerequisite makes work wait", () => {
  const todos = [
    { todoId: "first", done: false, status: "open" },
    { todoId: "second", done: false, status: "open", dependencies: ["first"] },
    { todoId: "third", done: false, status: "open", dependencies: ["finished", "unlisted"] },
    { todoId: "self", done: false, status: "open", dependencies: ["self"] },
    { todoId: "finished", done: true },
  ];
  const digest = compileGoalWorkspaceDigest({ ...base, todos });
  assert.equal(digest.work.classes.waiting_dependency, 1);
  assert.equal(digest.work.classes.open, 3);
});

test("needs-you counts blocking decisions, keeps optional ones apart and reports unreadable as unavailable", () => {
  const digest = compileGoalWorkspaceDigest({ ...base, attention: [
    { blocking: true, updatedAt: "2026-10-11T08:00:00.000Z" },
    { blocking: true, updatedAt: "2026-10-11T06:30:00.000Z" },
    { blocking: true, updatedAt: "not a time" },
    { blocking: false, updatedAt: "2026-10-10T00:00:00.000Z" },
  ], attentionSummary: { blocking: 3, optional: 1, coverage: "complete" } });
  assert.deepEqual(digest.needsYou, { availability: "ok", blocking: 3, optional: 1, coverage: "complete", listed: 4, oldestWaitingSince: "2026-10-11T06:30:00.000Z" });
  assert.deepEqual(compileGoalWorkspaceDigest({ ...base, attention: null }).needsYou, { availability: "unavailable" });
  assert.deepEqual(compileGoalWorkspaceDigest(base).needsYou, { availability: "ok", blocking: 0, optional: 0, coverage: "complete", listed: 0, oldestWaitingSince: null });
});

test("21 owner decisions stay 21 when the overview lists only three; their oldest time is unknown", () => {
  const attention = Array.from({ length: 3 }, () => ({ blocking: true, updatedAt: "2026-10-11T08:00:00.000Z" }));
  const digest = compileGoalWorkspaceDigest({ ...base, attention,
    attentionSummary: { blocking: 21, optional: 0, coverage: "partial" } });
  assert.deepEqual(digest.needsYou, { availability: "ok", blocking: 21, optional: 0,
    coverage: "partial", listed: 3, oldestWaitingSince: null });
  const optional = compileGoalWorkspaceDigest({ ...base, attention: attention.map(item => ({ ...item, blocking: false })),
    attentionSummary: { blocking: 0, optional: 21, coverage: "partial" } });
  assert.deepEqual(optional.needsYou, { availability: "ok", blocking: 0, optional: 21,
    coverage: "partial", listed: 3, oldestWaitingSince: null });
});

test("empty or missing overview coverage cannot invent zero, a complete count or a global oldest time", () => {
  const attention = [{ blocking: true, updatedAt: "2026-10-10T00:00:00Z" }];
  const missing = compileGoalWorkspaceDigest({ ...base, attention, attentionSummary: undefined });
  assert.deepEqual(missing.needsYou, { availability: "ok", blocking: null, optional: null,
    coverage: "unknown", listed: 1, oldestWaitingSince: null });
  const empty = compileGoalWorkspaceDigest({ ...base, attention: [],
    attentionSummary: { blocking: 21, optional: 0, coverage: "partial" } });
  assert.deepEqual(empty.needsYou, { availability: "ok", blocking: 21, optional: 0,
    coverage: "partial", listed: 0, oldestWaitingSince: null });
  for (const count of [0, -1, NaN, Infinity, 0.5]) {
    const bad = compileGoalWorkspaceDigest({ ...base, attention,
      attentionSummary: { blocking: count, optional: 0, coverage: "complete" } });
    assert.equal(bad.needsYou.availability, "ok");
    if (bad.needsYou.availability === "ok") {
      assert.equal(bad.needsYou.blocking, null);
      assert.equal(bad.needsYou.coverage, "unknown");
      assert.equal(bad.needsYou.oldestWaitingSince, null);
    }
  }
});

test("a pause reads back a finishing Turn only while one is observed", () => {
  const stopped = { ...base, activation: "stopped" as const };
  assert.equal(compileGoalWorkspaceDigest({ ...stopped, execution: running(true) }).lifecycle.finishingTurn, true);
  assert.equal(compileGoalWorkspaceDigest({ ...stopped, execution: running(true, true) }).lifecycle.finishingTurn, false);
  assert.equal(compileGoalWorkspaceDigest({ ...stopped, execution: running(false) }).lifecycle.finishingTurn, false);
  assert.equal(compileGoalWorkspaceDigest({ ...stopped, execution: { kind: "idle" } }).lifecycle.finishingTurn, false);
  assert.equal(compileGoalWorkspaceDigest({ ...base, execution: running(true) }).lifecycle.finishingTurn, false);
});

test("last activity is the newest observed fact and never a claim", () => {
  const digest = compileGoalWorkspaceDigest({ ...base, execution: running(true, false, "2026-10-11T09:40:00.000Z"), activity: [
    { actor: "review-agent", at: "2026-10-11T09:20:00.000Z" },
    { actor: "data-agent", at: "2026-10-11T09:51:00.000Z" },
    { actor: "broken", at: "yesterday" },
    { actor: "never", at: null },
  ] });
  assert.deepEqual(digest.lastActivity, { at: "2026-10-11T09:51:00.000Z", actor: "data-agent" });
  const claimOnly = compileGoalWorkspaceDigest({ ...base, execution: running(false, false, "2026-10-11T09:59:00.000Z") });
  assert.equal(claimOnly.lastActivity, null);
  const turn = compileGoalWorkspaceDigest({ ...base, execution: running(true, true, "2026-10-11T09:00:00.000Z") });
  assert.deepEqual(turn.lastActivity, { at: "2026-10-11T09:00:00.000Z", actor: "" }, "a quiet Turn still dates the last observed fact");
});

test("the reducer is deterministic and leaves its input untouched", () => {
  const input = Object.freeze({ ...base, todos: Object.freeze([Object.freeze({ todoId: "a", done: false })]),
    attention: Object.freeze([Object.freeze({ blocking: true })]), activity: Object.freeze([]) });
  assert.deepEqual(compileGoalWorkspaceDigest(input), compileGoalWorkspaceDigest(input));
  assert.equal(compileGoalWorkspaceDigest(input).schema, "goal_workspace_digest_v0");
});

test("one classification feeds responsibility groups and the all-work rail", () => {
  const digest = compileGoalWorkspaceDigest({ ...base, execution: running(true), runningTodoIds: ["live"],
    attention: [{ todoId: "decision", blocking: true }], todos: [
      { todoId: "live", done: false, ownerId: "worker-b" },
      { todoId: "held", done: false, ownerId: "worker-a", dependencies: ["decision"] },
      { todoId: "blocked", done: false, ownerId: "worker-a", status: "blocked" },
      { todoId: "waiting", done: false, ownerId: "worker-c", dependencies: ["live"] },
      { todoId: "next", done: false },
      { todoId: "later", done: false, ownerId: "worker-b", status: "deferred" },
      { todoId: "done", done: true, ownerId: "worker-b" },
      { todoId: "monitor", done: false, taskClass: "continuous_monitor" },
    ],
  });
  assert.deepEqual(digest.work.groups.map(group => group.ownerId), ["worker-a", "worker-b", "worker-c", null]);
  assert.deepEqual(digest.work.groups[0].todoIds, ["held", "blocked"]);
  assert.deepEqual(digest.work.rail.open, ["live", "next", "waiting"]);
  assert.deepEqual(digest.work.rail.needsYou, ["held"]);
  assert.deepEqual(digest.work.rail.blocked, ["blocked", "later"]);
  assert.deepEqual(digest.work.rail.done, ["done"]);
  assert.equal(digest.work.classes.needs_you, 1);
  assert.equal(digest.work.rows.find(row => row.todoId === "monitor"), undefined);
  assert.equal(digest.work.groups.flatMap(group => group.todoIds).length, 5);
  assert.equal(digest.work.rows.length, 7);
});

test("responsibility order is deterministic and follows provided dependency depth before Todo id", () => {
  const todos = [
    { todoId: "b", done: false, ownerId: "worker", dependencyOrder: 0 },
    { todoId: "a", done: false, ownerId: "worker", dependencyOrder: 1 },
    { todoId: "z", done: false, ownerId: "worker", dependencyOrder: 0 },
  ];
  const digest = compileGoalWorkspaceDigest({ ...base, todos });
  assert.deepEqual(digest.work.groups[0].todoIds, ["b", "z", "a"]);
  assert.deepEqual(compileGoalWorkspaceDigest({ ...base, todos: [...todos].reverse() }).work.groups, digest.work.groups);
});

test("claims, missing dependencies and unrelated decisions cannot invent work state", () => {
  const digest = compileGoalWorkspaceDigest({ ...base, attention: [{ todoId: "unrelated", blocking: true }],
    runningTodoIds: ["claimed"], execution: { kind: "unknown" },
    todos: [{ todoId: "claimed", done: false, ownerId: "worker", dependencies: ["missing"] }],
  });
  assert.equal(digest.work.rows[0].displayClass, "open");
  assert.equal(digest.work.groups[0].displayClass, "open");
  assert.equal(digest.work.classes.needs_you, 0);
});
