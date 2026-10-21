import assert from "node:assert/strict";
import { goalExecution, goalObservationGaps, goalWorkKind, presentGoalActivity, taskSessionStatus } from "../../../node_modules/.cache/loopx-goal-activity/goal-activity.js";

// One Goal's session facts, mixed by mode. `updated_at` stands for the time the
// owner last recorded anything for that session.
const session = (sessionId, mode, updatedAt, activeTurnId = `turn-${sessionId}`) => ({
  goal_id: "goal-a",
  agent_id: "codex",
  active_turn_id: activeTurnId,
  last_activity_at: updatedAt,
  session_mode: mode,
  status: "busy",
  updated_at: updatedAt,
});

const now = Date.parse("2026-09-27T12:00:00Z");
const minutesAgo = (minutes) => new Date(now - minutes * 60_000).toISOString();
const recent = minutesAgo(2);
const silent = minutesAgo(30);
const old = minutesAgo(45);

assert.deepEqual(goalObservationGaps({ hostThreadActivity: {completeness: "complete", threads: [
  { agentId: "codex", hostSurface: "codex-app", state: "idle", lastEventAt: recent, deliveryWindow: { state: "unknown", reason: "no_expectation" } },
] } }), {missing: 0, stale: 0, unavailable: false, incomplete: false}, "an unavailable cadence expectation is not a missed window");

function executionOf(sessions, observation) {
  const execution = goalExecution(sessions, "goal-a", observation, now);
  assert.equal(execution.kind, "running");
  return execution;
}

// Managed turns carry execution activity.
const managed = executionOf([session("managed", "managed_runtime", recent)]);
assert.equal(managed.lastActivityAt, recent);
assert.equal(managed.claimedAt, null);
assert.equal(managed.work, "executing");
assert.equal(managed.quiet, false);

// A silent managed turn is quiet from its own clock, with no claim in sight.
const quiet = executionOf([session("managed", "managed_runtime", silent)]);
assert.equal(quiet.lastActivityAt, silent);
assert.equal(quiet.claimedAt, null);
assert.equal(quiet.quiet, true);
assert.equal(presentGoalActivity({ activationState: "active", execution: quiet, state: "推进" }).live, false);

// An attached turn carries only its claim; it is never execution activity.
const claim = executionOf([session("attached", "attached_host", recent)]);
assert.equal(claim.work, "claimed");
assert.equal(claim.lastActivityAt, null);
assert.equal(claim.claimedAt, recent);
assert.equal(claim.quiet, false);
assert.equal(presentGoalActivity({ activationState: "active", execution: claim, state: "推进" }).live, false);

// The reported counterexample: a fresh claim beside a silent managed turn must
// not make the managed turn look live, and the claim stays a separate fact.
const silentWithFreshClaim = executionOf([session("managed", "managed_runtime", silent), session("attached", "attached_host", recent)]);
assert.equal(silentWithFreshClaim.work, "executing");
assert.equal(silentWithFreshClaim.lastActivityAt, silent, "a claim must not refresh managed activity");
assert.equal(silentWithFreshClaim.claimedAt, recent);
assert.equal(silentWithFreshClaim.quiet, true);
assert.equal(presentGoalActivity({ activationState: "active", execution: silentWithFreshClaim, state: "推进" }).live, false);

// The opposite mixture: recent managed activity is live and the older claim is
// still reported as a claim rather than as the execution time.
const recentWithOldClaim = executionOf([session("managed", "managed_runtime", recent), session("attached", "attached_host", old)]);
assert.equal(recentWithOldClaim.lastActivityAt, recent);
assert.equal(recentWithOldClaim.claimedAt, old);
assert.equal(recentWithOldClaim.quiet, false);
assert.equal(presentGoalActivity({ activationState: "active", execution: recentWithOldClaim, state: "推进" }).live, true);

// Several managed turns report the newest one; a closed session is not open work.
assert.equal(executionOf([session("a", "managed_runtime", silent), session("b", "managed_runtime", recent)]).lastActivityAt, recent);
assert.equal(goalExecution([{ ...session("closed", "managed_runtime", recent), status: "closed" }], "goal-a", undefined, now).kind, "idle");
assert.equal(goalExecution([session("other", "managed_runtime", recent, null)], "goal-a", undefined, now).kind, "idle");
assert.equal(goalExecution(null, "goal-a", undefined, now).kind, "unknown");
assert.equal(goalExecution([session("other-goal", "managed_runtime", recent)], "goal-b", undefined, now).kind, "idle");

// Native observation joins actual activity; it does not turn a fresh attached
// claim into execution or let that claim refresh a silent host Turn.
const hostObservation = (lastEventAt, completeness = "complete") => ({
  completeness,
  threads: [{ hostSurface: "codex-app", state: "turn_open", lastEventAt }],
});
const hostWithClaim = executionOf([session("attached", "attached_host", recent)], hostObservation(silent));
assert.equal(hostWithClaim.lastActivityAt, silent);
assert.equal(hostWithClaim.claimedAt, recent);
assert.equal(hostWithClaim.work, "executing");
assert.equal(hostWithClaim.quiet, true);
assert.equal(presentGoalActivity({ activationState: "active", execution: hostWithClaim, state: "推进" }).live, false);
const freshHost = executionOf([session("managed", "managed_runtime", silent)], hostObservation(recent, "incomplete"));
assert.equal(freshHost.lastActivityAt, recent);
assert.equal(freshHost.quiet, false);
assert.equal(presentGoalActivity({ activationState: "active", execution: freshHost, state: "推进" }).live, true);
assert.equal(goalExecution(undefined, "goal-a", hostObservation(recent), now).kind, "running");
assert.equal(goalExecution(null, "goal-a", hostObservation(minutesAgo(8 * 60)), now).kind, "unknown");
assert.equal(goalExecution([], "goal-a", hostObservation(minutesAgo(8 * 60)), now).kind, "unknown", "An old open marker proves neither current execution nor an ended turn");
const unreadable = { hostSurface: "codex-app", state: "unknown", reason: "no_turn_marker", lastEventAt: null,
  deliveryWindow: { state: "missing", reason: "no_observation" } };
const mixedHosts = { completeness: "complete", threads: [...hostObservation(recent).threads, unreadable] };
assert.equal(executionOf([], mixedHosts).lastActivityAt, recent, "An unreadable lane cannot replace another lane's clock");
assert.equal(goalObservationGaps({hostThreadActivity: mixedHosts}).unavailable, false);
assert.equal(goalObservationGaps({hostThreadActivity: mixedHosts}).missing, 1);
for (const reason of ["unsupported_host", "thread_not_found", "store_unavailable", "record_unrecognized", "no_turn_marker"]) {
  const observation = {completeness: "complete", threads: [{...unreadable, reason}]};
  for (const sessions of [undefined, null, []]) {
    assert.equal(goalExecution(sessions, "goal-a", observation, now).kind, "unknown", `${reason}: absent host evidence cannot establish idleness`);
  }
  assert.equal(goalObservationGaps({hostThreadActivity: observation}).unavailable, true);
  assert.equal(executionOf([session("managed", "managed_runtime", recent)], observation).lastActivityAt, recent,
    "Managed execution also survives a missing host observation");
}
const endedAndMissing = {completeness: "complete", threads: [
  {hostSurface: "codex-app", state: "idle", lastEventAt: recent, deliveryWindow: {state: "fresh"}}, unreadable,
]};
assert.equal(goalExecution([], "goal-a", endedAndMissing, now).kind, "unknown", "Recent completed activity is not execution or proof of all-host idleness");
assert.deepEqual(goalObservationGaps({hostThreadActivity: endedAndMissing}), {missing: 1, stale: 0, unavailable: false, incomplete: false});
assert.equal(goalExecution([], "goal-a", {completeness: "incomplete", threads: []}, now).kind, "unknown");
assert.equal(goalExecution([], "goal-a", {completeness: "complete", threads: [{hostSurface: "codex-app", state: "archived", lastEventAt: null}]}, now).kind, "idle");
assert.equal(goalObservationGaps({hostThreadActivity: {completeness: "complete", threads: [
  {hostSurface: "codex-app", state: "idle", lastEventAt: old, deliveryWindow: {state: "stale"}}, unreadable,
]}}).stale, 1, "An actual cadence miss stays visible beside missing coverage");
assert.deepEqual(goalObservationGaps({hostThreadActivity: {completeness: "complete", threads: [
  {hostSurface: "codex-app", state: "turn_open", lastEventAt: "unreadable", deliveryWindow: {state: "stale"}},
]}}), {missing: 1, stale: 0, incomplete: false, unavailable: true}, "One unreadable lane is counted once even when its cadence payload is inconsistent");
assert.equal(presentGoalActivity({activationState: "active", state: "等待条件", execution: {kind: "unknown"}, hostThreadActivity: {completeness: "complete", threads: [unreadable]}}).labelKey,
  "state.waiting", "A known wait condition survives missing execution coverage");
for (const completeness of ["incomplete", undefined]) {
  const partialIdle = presentGoalActivity({
    activationState: "active", state: "已安排", boundHostSurfaces: ["codex-app"],
    hostThreadActivity: { completeness, threads: [{ hostSurface: "codex-app", state: "idle", lastEventAt: recent }] },
  });
  assert.equal(partialIdle.alsoKey, "activity.inHost", "an incomplete sample never proves all threads idle");
}

// `goalWorkKind` is the single rule the brief and the home lanes share: a claim
// is never execution, and only a managed turn counts as executing.
assert.equal(goalWorkKind({ execution: managed }), "executing", "A managed turn executes");
assert.equal(goalWorkKind({ execution: quiet }), "executing", "A silent managed turn is still executing, not claimed");
assert.equal(goalWorkKind({ execution: claim }), "claimed", "An attached-only claim is a claim, not execution");
assert.equal(goalWorkKind({ execution: silentWithFreshClaim }), "executing", "A fresh claim never downgrades a managed turn");
assert.equal(goalWorkKind({ execution: recentWithOldClaim }), "executing", "A stale claim never downgrades a managed turn");
assert.equal(goalWorkKind({ execution: hostWithClaim }), "executing", "An observed host thread turn is execution, not a bare claim");
assert.equal(goalWorkKind({ execution: { kind: "idle", hostSurfaces: [] } }), "none", "An idle Goal carries no unfinished turn");
assert.equal(goalWorkKind({ execution: { kind: "unknown" } }), "none", "Unreadable execution is not unfinished work");
assert.equal(goalWorkKind({}), "none", "A Goal without an execution fact carries no work kind");
assert.equal(presentGoalActivity({ activationState: "active", execution: claim, state: "推进" }).labelKey, "activity.hostClaimed", "The chip names the claim");
assert.equal(presentGoalActivity({ activationState: "active", execution: managed, state: "推进" }).labelKey, "activity.running", "The chip names managed execution");

// Unknown wire modes carry no execution evidence, including beside a host claim.
for (const mode of [undefined, null, "", "managed", "future_mode"]) {
  const unknown = executionOf([session("unknown", mode, recent)]);
  assert.equal(goalWorkKind({execution: unknown}), "unrecognized", `${String(mode)} is not execution`);
  assert.equal(unknown.lastActivityAt, null);
  assert.equal(unknown.claimedAt, null);
  assert.equal(presentGoalActivity({activationState: "active", execution: unknown, state: "推进"}).live, false);
  const mixed = executionOf([session("managed", "managed_runtime", silent), session("unknown", mode, recent)]);
  assert.equal(mixed.lastActivityAt, silent, "Unknown mode cannot refresh a managed execution clock");
  assert.equal(mixed.quiet, true);
  const claimed = executionOf([session("attached", "attached_host", silent), session("unknown", mode, recent)]);
  assert.equal(goalWorkKind({execution: claimed}), "claimed", "Unknown mode cannot upgrade a host claim");
}

const unknown = executionOf([session("unknown", "future_mode", recent)]);
for (const state of ["等你", "需修复"]) {
  assert.equal(presentGoalActivity({activationState: "active", execution: unknown, state}).alsoKey, "activity.alsoModeUnrecognized",
    "A gate cannot describe an unknown execution as running");
}
assert.equal(presentGoalActivity({activationState: "stopped", execution: unknown, state: "已停止"}).live, false);
const observed = executionOf([session("unknown", "future_mode", recent)], hostObservation(recent));
assert.equal(goalWorkKind({execution: observed}), "executing", "Independent host observation still establishes execution");

// Historical answers and Todo completion cannot close a later active or failed Session.
for (const [scenario, activeTurnId, status, done, hasResult, expected] of [
  ["active follow-up with an earlier answer", "turn-new", "busy", false, true, "running"],
  ["active follow-up on a completed Todo", "turn-new", "busy", true, true, "running"],
  ["failed resume with an earlier answer", null, "resume_failed", false, true, "failed"],
  ["failed resume on a completed Todo", null, "resume_failed", true, true, "failed"],
  ["fresh active execution", "turn-new", "busy", false, false, "running"],
  ["finished execution retaining its answer", null, "ready", false, true, "completed"],
  ["completed Todo without a Session answer", null, "ready", true, false, "completed"],
  ["open work without a result", null, "ready", false, false, "waiting"],
]) {
  assert.equal(taskSessionStatus({ active_turn_id: activeTurnId, status }, { done, hasResult }), expected, scenario);
}

console.log("Goal and task execution read-model invariants passed");
