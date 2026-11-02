import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import test from "node:test";
import {fileURLToPath} from "node:url";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {settlementIdentity, settlementIdentityPayload, type JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import {goalVisionStateIsOpen, qualifiesOpenTodoSemanticCheckpoint} from "../../loopx/control_plane/goals/vision_checkpoint.ts";

const identity = settlementIdentity({goal_id: "goal", agent_id: "agent", todo_id: "todo_checkpoint", turn_instance_id: "turn"});
const writeback: JsonObject = {
  goal_id: identity.goal_id, agent_id: identity.agent_id, todo_id: identity.todo_id,
  turn_instance_id: identity.turn_instance_id, settlement_identity: settlementIdentityPayload(identity),
  delivery_outcome: "outcome_progress",
  agent_vision: {schema_version: "goal_vision_replan_contract_v0", goal_id: "goal", agent_id: "agent", state: "active",
    vision_patch: {acceptance_summary: "The current result advances the open comparison."},
    path_delta: {outcome: "continue", prior_assumption: "The incumbent may be sufficient.",
      observed_reality: "A new comparison leaves further work open.", retained: ["Original acceptance and incumbent."],
      evidence_refs: ["evidence:new-comparison"]}},
  vision_checkpoint: {schema_version: "vision_checkpoint_v0", agent_id: "agent", required: true, satisfied: true,
    decision: "patched", delivery_boundary: "semantic_closeout",
    triggers: [{kind: "material_delivery_outcome", delivery_outcome: "outcome_progress"}]},
};
const changed = (path: string[], value: unknown): JsonObject => {
  const copy = structuredClone(writeback);
  let owner = copy;
  for (const key of path.slice(0, -1)) owner = owner[key] as JsonObject;
  owner[path.at(-1)!] = value;
  return copy;
};

test("open Todo semantic evidence has one read-only registered qualification path", async () => {
  const handlers = createEffectRuntimeHandlers({fingerprint: "checkpoint-test", requestShutdown() {}});
  const before = structuredClone(writeback);
  assert.equal(qualifiesOpenTodoSemanticCheckpoint(writeback, identity), true);
  assert.equal(await dispatchEffectRuntimeMethod(handlers, "settlement.open_todo_semantic_checkpoint",
    {identity, writeback}), true);
  assert.deepEqual(writeback, before);
  assert.equal(qualifiesOpenTodoSemanticCheckpoint(changed(["agent_vision", "path_delta", "outcome"], "no_change"), identity), true);
  const compact = structuredClone(writeback);
  delete (compact.agent_vision as JsonObject).goal_id;
  assert.equal(qualifiesOpenTodoSemanticCheckpoint(compact, identity), true);
  await assert.rejects(dispatchEffectRuntimeMethod(handlers, "settlement.open_todo_semantic_checkpoint",
    {identity: {goal_id: "goal", todo_id: "todo_checkpoint", turn_instance_id: "turn"}, writeback}), /identity.agent_id/);
});

test("checkpoint qualification cannot borrow another binding, reuse old context or accept completion", async () => {
  const handlers = createEffectRuntimeHandlers({fingerprint: "checkpoint-negatives", requestShutdown() {}});
  const rejected: Array<[string[], unknown]> = [
    ...["goal_id", "agent_id", "todo_id", "turn_instance_id"].flatMap(field =>
      [[[field], "foreign"], [["settlement_identity", field], "foreign"]] as Array<[string[], unknown]>),
    [["settlement_identity", "effect_id"], "foreign-effect"],
    [["settlement_identity", "schema_version"], "unknown"],
    [["delivery_outcome"], "surface_only"], [["delivery_outcome"], "primary_goal_outcome"],
    [["completion_todo_id"], identity.todo_id], [["agent_vision"], null],
    [["agent_vision", "goal_id"], "foreign"], [["agent_vision", "agent_id"], "foreign"],
    [["agent_vision", "vision_patch", "acceptance_summary"], ""],
    [["agent_vision", "path_delta"], null], [["agent_vision", "path_delta", "evidence_refs"], []],
    [["agent_vision", "path_delta", "outcome"], "stop"], [["agent_vision", "path_delta", "outcome"], "replan"],
    [["agent_vision", "state"], null], [["agent_vision", "state"], undefined], [["agent_vision", "state"], ""],
    [["vision_checkpoint", "agent_id"], "foreign"], [["vision_checkpoint", "satisfied"], false],
    [["vision_checkpoint", "required"], false], [["vision_checkpoint", "decision"], "unchanged_with_reason"],
    [["vision_checkpoint", "triggers"], []], [["vision_checkpoint", "delivery_boundary"], "in_flight_continuation"],
    ...["vision_closed", "retired", "retired_or_superseded", "superseded", "no_followup",
      "closed", "satisfied", "vision_satisfied", "vision_retired", "vision_superseded",
      "vision_no_followup", "closed_no_followup", "no_follow_up", "invalid state"].map(state =>
      [["agent_vision", "state"], state] as [string[], unknown]),
  ];
  for (const [path, value] of rejected) {
    const candidate = changed(path, value), before = structuredClone(candidate);
    assert.equal(qualifiesOpenTodoSemanticCheckpoint(candidate, identity), false, path.join(".") + "=" + value);
    assert.equal(await dispatchEffectRuntimeMethod(handlers, "settlement.open_todo_semantic_checkpoint",
      {identity, writeback: candidate}), false, path.join("."));
    assert.deepEqual(candidate, before);
  }
});

test("the shared closed vocabulary preserves Python aliases and custom open states", () => {
  const closed = ["vision_closed", "retired", "retired_or_superseded", "superseded", "no_followup",
    "closed", "satisfied", "vision_satisfied", "vision_retired", "vision_superseded",
    "vision_no_followup", "closed_no_followup", "no_follow_up"];
  const states = [...closed, "active", "vision_patch_proposed", "custom_open_state", "invalid state", null];
  const expected = [...closed.map(() => false), true, true, true, false, true];
  assert.deepEqual(states.map(goalVisionStateIsOpen), expected);
  const python = spawnSync(resolveTestPython(), ["-c",
    "import json,sys; from loopx.control_plane.goals.goal_vision_state import normalize_goal_vision_state,goal_vision_state_is_closed\n" +
    "def opened(value):\n try: return not goal_vision_state_is_closed(normalize_goal_vision_state(value))\n except ValueError: return False\n" +
    "print(json.dumps([opened(value) for value in json.load(sys.stdin)]))"],
    {cwd: fileURLToPath(new URL("../../", import.meta.url)), input: JSON.stringify(states), encoding: "utf8"});
  assert.equal(python.status, 0, python.stderr);
  assert.deepEqual(JSON.parse(python.stdout), expected);
});
