import assert from "node:assert/strict";
import { test } from "node:test";
import { goalHierarchy, goalSubtree, visibleGoalTree } from "../../../node_modules/.cache/loopx-goal-order/goal-hierarchy.js";

const goals = [
  {goalId: "daily"}, {goalId: "research"},
  {goalId: "portfolio", parentGoalId: "research"},
  {goalId: "candidate", parentGoalId: "portfolio"},
  {goalId: "graph", parentGoalId: "research"},
  {goalId: "orphan", parentGoalId: "not-readable"},
  {goalId: "cycle-a", parentGoalId: "cycle-b"},
  {goalId: "cycle-b", parentGoalId: "cycle-a"},
  {goalId: "self", parentGoalId: "self"},
];

test("organization follows declared parents, with no implicit run or Agent relationship", () => {
  assert.equal(goalSubtree(goals), goals, "Unscoped behavior retains the existing list");
  assert.deepEqual(goalSubtree(goals, "research").map(g => g.goalId), ["research", "portfolio", "candidate", "graph"]);
  assert.deepEqual(goalSubtree(goals, "missing"), [], "Never substitute all work for an unavailable root");
  assert.deepEqual(visibleGoalTree(goals, new Set()).map(row => row.goal.goalId),
    ["daily", "research", "orphan", "cycle-a", "cycle-b", "self"]);
  assert.deepEqual(visibleGoalTree(goals, new Set(["research", "portfolio"])).slice(1, 5)
    .map(row => [row.goal.goalId, row.depth]), [["research", 0], ["portfolio", 1], ["candidate", 2], ["graph", 1]]);
});

test("malformed ancestry is visible without fabricating links or looping", () => {
  const tree = goalHierarchy(goals);
  assert.deepEqual([...tree.invalidParents], ["orphan", "cycle-a", "cycle-b", "self"]);
  assert.deepEqual(goalSubtree(goals, "cycle-a").map(goal => goal.goalId), ["cycle-a"]);
  const deep = Array.from({length: 10_000}, (_, i) => ({goalId: String(i), parentGoalId: i ? String(i - 1) : null}));
  assert.equal(goalSubtree(deep, "0").length, deep.length, "No call-stack limit truncates deep history");
  assert.equal(visibleGoalTree(deep, new Set(deep.map(g => g.goalId))).length, deep.length);
});

test("same logical IDs from different sources cannot reuse another source's parentage", () => {
  assert.deepEqual(goalSubtree([{goalId: "research"}, {goalId: "benchmark-goal", parentGoalId: "research"}], "research")
    .map(goal => goal.goalId), ["research", "benchmark-goal"]);
  assert.deepEqual(goalSubtree([{goalId: "research"}, {goalId: "benchmark-goal"}], "research")
    .map(goal => goal.goalId), ["research"]);
});
