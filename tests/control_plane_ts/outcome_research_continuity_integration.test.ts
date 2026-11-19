import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {projectReplanSemantics} from "../../loopx/control_plane/work_items/replan_semantics.ts";

test("mixed complete Outcome sets and owner holds require every scoped checkpoint", () => {
  const agent = "agent-a";
  const outcomes = Array.from({length: 5}, (_, index) => ({kind: "goal_outcome_uncovered",
    source: "goal_acceptance_contract", agent_id: agent, outcome_id: `tour-${index}`, frontier_revision: "a".repeat(64)}));
  const outcomeCheckpoint = {kind: outcomes[0].kind, frontier_revision: outcomes[0].frontier_revision};
  for (const kind of ["goal_acceptance_unbound", "goal_acceptance_stale"]) {
    const holds = ["b", "c", "d"].map((hash, index) => ({kind, source: "goal_acceptance_contract", agent_id: agent,
      vision_todo_ids: [`todo-held-${index}`], frontier_revision: hash.repeat(64), generated_at: "2026-10-01T00:02:00Z"}));
    const delta = {accepted: true, obligation_id: "replan-1111111111111111",
      outcomes: ["new_concrete_blocker"], satisfying_outcomes: ["new_concrete_blocker"],
      trigger_kinds: [outcomes[0].kind, kind], trigger_checkpoints: [...holds, outcomeCheckpoint]};
    const ack = {recorded: true, agent_id: agent, generated_at: "2026-10-01T00:01:00Z", semantic_delta: delta};
    const covered = (gaps: JsonObject[], semantic_delta: JsonObject = delta, changes: JsonObject = {}) => projectReplanSemantics({
      operation: "vision_ack", acceptance_gaps: gaps, ack: {...ack, semantic_delta, ...changes}}).acknowledged;
    assert.equal(covered([...outcomes, ...holds]), true, "one aggregate and three exact owner checkpoints cover the complete set");
    assert.equal(covered([...holds, ...outcomes]), true, "formal holds tolerate later display timestamps");
    for (const checkpoint of delta.trigger_checkpoints) {
      assert.equal(covered([...outcomes, ...holds], {...delta,
        trigger_checkpoints: delta.trigger_checkpoints.filter(row => row !== checkpoint)}), false);
    }
    for (const missing of delta.trigger_kinds) {
      assert.equal(covered([...outcomes, ...holds], {...delta, trigger_kinds: delta.trigger_kinds.filter(value => value !== missing)}), false);
    }
    assert.equal(covered([...outcomes, ...holds], delta, {agent_id: "agent-b"}), false);
    assert.equal(covered([...outcomes, ...holds], delta, {agent_id: null}), false);
    assert.equal(covered([...outcomes.map(row => ({...row, agent_id: "agent-b"})), ...holds]), false);
    assert.equal(covered([...outcomes.slice(0, 3), {...outcomes[3], frontier_revision: "e".repeat(64)}, outcomes[4], ...holds]), false);
    assert.equal(covered([...outcomes, {...holds[0], frontier_revision: "e".repeat(64)}, ...holds.slice(1)]), false);
    assert.equal(covered([...outcomes, {...holds[0], source: "unconfirmed_display"}, ...holds.slice(1)]), false);
    assert.equal(covered([...outcomes, ...holds], {...delta, satisfying_outcomes: ["fresh_vision_path_outcome"]}), false);
    const old = holds.map((hold, index) => ({...hold, legacy_frontier_revision: ["e", "f", "0"][index].repeat(64),
      generated_at: "2026-10-01T00:00:00Z"}));
    const legacy = {...delta, trigger_checkpoints: [...old.map(hold => ({kind: hold.kind,
      frontier_revision: hold.legacy_frontier_revision})), outcomeCheckpoint]};
    assert.equal(covered([...outcomes, ...old], legacy), true);
    for (const changes of [{legacy_frontier_revision: "9".repeat(64)}, {generated_at: "2026-10-01T00:02:00Z"},
      {source: "unconfirmed_display"}]) {
      assert.equal(covered([...outcomes, {...old[0], ...changes}, ...old.slice(1)], legacy), false);
    }
    assert.equal(covered([...outcomes, ...holds, {kind: "vision_acceptance_gap", generated_at: "2026-10-01T00:02:00Z"}]), false);
  }
});
