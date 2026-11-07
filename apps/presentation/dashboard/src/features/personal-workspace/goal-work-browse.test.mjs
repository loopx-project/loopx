import assert from "node:assert/strict";
import test from "node:test";
import {defaultGoalWorkBrowse, readGoalWorkBrowse, writeGoalWorkBrowse} from "./goal-work-browse.tsx";

test("task browsing restores only inside the same Goal and source", () => {
  const state = {lens: "map", view: "list", owner: "agent-1", filter: "blocked", query: "同名任务 🌱"};
  const route = writeGoalWorkBrowse(state, "goal-1", "/status.json?source_namespace=source-a");
  assert.deepEqual(readGoalWorkBrowse(route, "goal-1", "/status.json?source_namespace=source-a"), state);
  assert.deepEqual(readGoalWorkBrowse(route, "goal-2", "/status.json?source_namespace=source-a"), defaultGoalWorkBrowse);
  assert.deepEqual(readGoalWorkBrowse(route, "goal-1", "/status.json?source_namespace=source-b"), defaultGoalWorkBrowse);
  assert.deepEqual(readGoalWorkBrowse(route, "", "/status.json?source_namespace=source-a"), defaultGoalWorkBrowse);
});

test("unassigned is distinct from all owners and from an unavailable named owner", () => {
  for (const owner of ["", "all", "temporarily-unlisted-agent"]) {
    const value = {...defaultGoalWorkBrowse, owner};
    const route = JSON.parse(JSON.stringify(writeGoalWorkBrowse(value, "goal-1", "/status.json")));
    assert.deepEqual(readGoalWorkBrowse(route, "goal-1", "/status.json"), value);
  }
});

test("old links and malformed view choices retain the existing defaults", () => {
  assert.deepEqual(readGoalWorkBrowse({goalId: "goal-1", todo: "same-task"}, "goal-1", "/status.json"), defaultGoalWorkBrowse);
  assert.deepEqual(readGoalWorkBrowse({workGoal: "goal-1", workSource: "/status.json",
    workLens: ["map"], taskView: "unknown", taskOwner: {id: "agent-1"}, taskFilter: "running", taskQuery: ["private"]},
    "goal-1", "/status.json"), defaultGoalWorkBrowse);
});

test("clearing filters does not replace the lens or list presentation", () => {
  const current = {lens: "board", view: "list", owner: "agent-1", filter: "needsYou", query: "explicit requirement"};
  const cleared = {...current, owner: "all", filter: "all", query: ""};
  assert.deepEqual(readGoalWorkBrowse(writeGoalWorkBrowse(cleared, "goal-1", "/status.json"), "goal-1", "/status.json"), cleared);
});
