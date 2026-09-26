import assert from "node:assert/strict";
import { goalExecutionFromSessions, presentGoalActivity } from "../../../node_modules/.cache/loopx-goal-activity/goal-activity.js";

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

function executionOf(sessions) {
  const execution = goalExecutionFromSessions(sessions, "goal-a", now);
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
assert.equal(goalExecutionFromSessions([{ ...session("closed", "managed", recent), status: "closed" }], "goal-a", now).kind, "idle");
assert.equal(goalExecutionFromSessions([session("other", "managed", recent, null)], "goal-a", now).kind, "idle");
assert.equal(goalExecutionFromSessions(null, "goal-a", now).kind, "unknown");
assert.equal(goalExecutionFromSessions([session("other-goal", "managed", recent)], "goal-b", now).kind, "idle");

console.log("Goal execution read-model invariants passed");
