import assert from "node:assert/strict";
import test from "node:test";
import {
  assessChangeScopeBudget,
  CHANGE_SCOPE_BUDGET_SCHEMA_VERSION,
  CHANGE_SCOPE_BUDGET_STATUSES,
} from "../../loopx/control_plane/capabilities/change_scope_budget.ts";

test("status vocabulary stays explicit and bounded", () => {
  assert.deepEqual(CHANGE_SCOPE_BUDGET_STATUSES, [
    "within_budget",
    "net_subtraction",
    "elevated_scope",
    "large_scope",
  ]);
});

test("bounded diff is within_budget with no remediation", () => {
  const result = assessChangeScopeBudget({
    changed_files: 6,
    additions: 180,
    deletions: 40,
    commit_count: 3,
    merge_commit_count: 0,
    request_changes_rounds: 1,
  });
  assert.equal(result.schema_version, CHANGE_SCOPE_BUDGET_SCHEMA_VERSION);
  assert.equal(result.status, "within_budget");
  assert.equal("split_required" in result, false);
  assert.equal(result.net_additions, 140);
  assert.deepEqual(result.triggered_signals, []);
  assert.equal(result.remediation, "none");
});

test("net_subtraction exempts large cleanups up to 1500 additions", () => {
  const result = assessChangeScopeBudget({
    changed_files: 80,
    additions: 1500,
    deletions: 4200,
    commit_count: 45,
    merge_commit_count: 4,
    request_changes_rounds: 6,
  });
  assert.equal(result.status, "net_subtraction");
  assert.equal("split_required" in result, false);
  assert.equal(result.net_additions, -2700);
  assert.deepEqual(result.triggered_signals, []);
  assert.equal(result.remediation, "none");
});

test("net_subtraction exemption stops above 1500 additions", () => {
  const result = assessChangeScopeBudget({
    changed_files: 20,
    additions: 1501,
    deletions: 3000,
    merge_commit_count: 3,
  });
  assert.equal(result.status, "large_scope");
  assert.equal("split_required" in result, false);
  assert.deepEqual(result.triggered_signals, ["repeated_mainline_merge_commits"]);
  assert.equal(result.remediation, "verify_goal_proportionality");
});

test("large_scope marks high-addition, multi-commit, merge, and ping-pong signals without mandatory split", () => {
  const surface = assessChangeScopeBudget({
    changed_files: 35,
    additions: 2500,
    deletions: 2499,
  });
  assert.equal(surface.status, "large_scope");
  assert.equal("split_required" in surface, false);
  assert.deepEqual(surface.triggered_signals, ["excessive_addition_surface"]);
  assert.equal(surface.remediation, "verify_goal_proportionality");

  const spiral = assessChangeScopeBudget({
    changed_files: 8,
    additions: 1200,
    deletions: 1200,
    request_changes_rounds: 5,
  });
  assert.equal(spiral.status, "large_scope");
  assert.deepEqual(spiral.triggered_signals, ["review_ping_pong_spiral"]);
  assert.equal(spiral.remediation, "verify_goal_proportionality");

  const churn = assessChangeScopeBudget({
    changed_files: 8,
    additions: 1200,
    deletions: 800,
    commit_count: 40,
  });
  assert.equal(churn.status, "large_scope");
  assert.deepEqual(churn.triggered_signals, ["excessive_commit_churn"]);

  const mergesExempt = assessChangeScopeBudget({
    changed_files: 5,
    additions: 800,
    deletions: 900,
    merge_commit_count: 3,
  });
  assert.equal(mergesExempt.status, "net_subtraction");

  const merges = assessChangeScopeBudget({
    changed_files: 5,
    additions: 800,
    deletions: 800,
    merge_commit_count: 3,
  });
  assert.equal(merges.status, "large_scope");
  assert.deepEqual(merges.triggered_signals, ["repeated_mainline_merge_commits"]);

  const mergesAboveCeiling = assessChangeScopeBudget({
    changed_files: 5,
    additions: 1600,
    deletions: 1700,
    merge_commit_count: 3,
  });
  assert.equal(mergesAboveCeiling.status, "large_scope");
  assert.deepEqual(mergesAboveCeiling.triggered_signals, [
    "repeated_mainline_merge_commits",
  ]);
});

test("elevated_scope reports non-blocking diagnostic warnings", () => {
  const largeSlice = assessChangeScopeBudget({
    changed_files: 10,
    additions: 1000,
    deletions: 200,
    commit_count: 20,
    merge_commit_count: 2,
    request_changes_rounds: 3,
  });
  assert.equal(largeSlice.status, "elevated_scope");
  assert.equal(largeSlice.remediation, "prefer_smaller_slice");
  assert.deepEqual(largeSlice.triggered_signals, [
    "elevated_addition_surface",
    "elevated_review_rounds",
    "elevated_commit_count",
    "repeated_mainline_merge_commits",
  ]);

  const wideEqualSlice = assessChangeScopeBudget({
    changed_files: 20,
    additions: 400,
    deletions: 400,
  });
  assert.equal(wideEqualSlice.status, "elevated_scope");
  assert.deepEqual(wideEqualSlice.triggered_signals, ["elevated_addition_surface"]);
});

test("rejects negative, non-integer, and unsupported fields", () => {
  assert.throws(() =>
    assessChangeScopeBudget({changed_files: -1, additions: 1, deletions: 0}),
  );
  assert.throws(() =>
    assessChangeScopeBudget({changed_files: 1, additions: 1.5, deletions: 0}),
  );
  assert.throws(() =>
    assessChangeScopeBudget({
      changed_files: 1,
      additions: 1,
      deletions: 0,
      commit_count: -2,
    }),
  );
  assert.throws(() =>
    assessChangeScopeBudget({
      changed_files: 1,
      additions: 1,
      deletions: 0,
      extra: 1,
    }),
  );
});
