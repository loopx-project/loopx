import assert from "node:assert/strict";
import { goalExecution, goalWorkKind, presentGoalActivity } from "../../../node_modules/.cache/loopx-goal-activity/goal-activity.js";

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

function executionOf(sessions, observation) {
  const execution = goalExecution(sessions, "goal-a", observation, now);
  assert.equal(execution.kind, "running");
  return execution;
}

// Managed turns carry execution activity.
const managed = executionOf([session("managed", "managed", recent)]);
assert.equal(managed.lastActivityAt, recent);
assert.equal(managed.claimedAt, null);
assert.equal(managed.hostClaimed, false);
assert.equal(managed.quiet, false);

// A silent managed turn is quiet from its own clock, with no claim in sight.
const quiet = executionOf([session("managed", "managed", silent)]);
assert.equal(quiet.lastActivityAt, silent);
assert.equal(quiet.claimedAt, null);
assert.equal(quiet.quiet, true);
assert.equal(presentGoalActivity({ activationState: "active", execution: quiet, state: "推进" }).live, false);

// An attached turn carries only its claim; it is never execution activity.
const claim = executionOf([session("attached", "attached_host", recent)]);
assert.equal(claim.hostClaimed, true);
assert.equal(claim.lastActivityAt, null);
assert.equal(claim.claimedAt, recent);
assert.equal(claim.quiet, false);
assert.equal(presentGoalActivity({ activationState: "active", execution: claim, state: "推进" }).live, false);

// The reported counterexample: a fresh claim beside a silent managed turn must
// not make the managed turn look live, and the claim stays a separate fact.
const silentWithFreshClaim = executionOf([session("managed", "managed", silent), session("attached", "attached_host", recent)]);
assert.equal(silentWithFreshClaim.hostClaimed, false);
assert.equal(silentWithFreshClaim.lastActivityAt, silent, "a claim must not refresh managed activity");
assert.equal(silentWithFreshClaim.claimedAt, recent);
assert.equal(silentWithFreshClaim.quiet, true);
assert.equal(presentGoalActivity({ activationState: "active", execution: silentWithFreshClaim, state: "推进" }).live, false);

// The opposite mixture: recent managed activity is live and the older claim is
// still reported as a claim rather than as the execution time.
const recentWithOldClaim = executionOf([session("managed", "managed", recent), session("attached", "attached_host", old)]);
assert.equal(recentWithOldClaim.lastActivityAt, recent);
assert.equal(recentWithOldClaim.claimedAt, old);
assert.equal(recentWithOldClaim.quiet, false);
assert.equal(presentGoalActivity({ activationState: "active", execution: recentWithOldClaim, state: "推进" }).live, true);

// Several managed turns report the newest one; a closed session is not open work.
assert.equal(executionOf([session("a", "managed", silent), session("b", "managed", recent)]).lastActivityAt, recent);
assert.equal(goalExecution([{ ...session("closed", "managed", recent), status: "closed" }], "goal-a", undefined, now).kind, "idle");
assert.equal(goalExecution([session("other", "managed", recent, null)], "goal-a", undefined, now).kind, "idle");
assert.equal(goalExecution(null, "goal-a", undefined, now).kind, "unknown");
assert.equal(goalExecution([session("other-goal", "managed", recent)], "goal-b", undefined, now).kind, "idle");

// Native observation joins actual activity; it does not turn a fresh attached
// claim into execution or let that claim refresh a silent host Turn.
const hostObservation = (lastEventAt, completeness = "complete") => ({
  completeness,
  threads: [{ hostSurface: "codex-app", state: "turn_open", lastEventAt }],
});
const hostWithClaim = executionOf([session("attached", "attached_host", recent)], hostObservation(silent));
assert.equal(hostWithClaim.lastActivityAt, silent);
assert.equal(hostWithClaim.claimedAt, recent);
assert.equal(hostWithClaim.hostClaimed, false);
assert.equal(hostWithClaim.quiet, true);
assert.equal(presentGoalActivity({ activationState: "active", execution: hostWithClaim, state: "推进" }).live, false);
const freshHost = executionOf([session("managed", "managed", silent)], hostObservation(recent, "incomplete"));
assert.equal(freshHost.lastActivityAt, recent);
assert.equal(freshHost.quiet, false);
assert.equal(presentGoalActivity({ activationState: "active", execution: freshHost, state: "推进" }).live, true);
assert.equal(goalExecution(undefined, "goal-a", hostObservation(recent), now).kind, "running");
assert.equal(goalExecution(null, "goal-a", hostObservation(minutesAgo(8 * 60)), now).kind, "unknown");
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

console.log("Goal execution read-model invariants passed");
