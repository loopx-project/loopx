import assert from "node:assert/strict";
import {appendFile, mkdir, mkdtemp, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import {settlementIdentity} from "../../loopx/control_plane/effect_program.ts";
import {verifySettledExecutionSwitch} from "../../loopx/control_plane/coordination/settled_execution_switch.ts";

const goal = "switch-goal";
const agent = "agent-a";
const source = "todo_source";
const target = "todo_target";
const turn = "turn-source-settled";

async function fixture(spent = true): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "loopx-settled-switch-"));
  const goalRoot = join(root, "goals", goal);
  const runsRoot = join(goalRoot, "runs");
  await mkdir(runsRoot, {recursive: true});
  const identity = settlementIdentity({goal_id: goal, agent_id: agent,
    todo_id: source, turn_instance_id: turn});
  const event = (kind: string, id: string) => ({
    schema_version: "loopx_rollout_event_v0",
    event_id: id,
    event_kind: kind,
    goal_id: goal,
    agent_id: agent,
    run_id: turn,
    details: {todo_id: source, settlement_effect_id: identity.effect_id},
  });
  const events = [event("quota_should_run", "guard"), event("refresh_state", "writeback"),
    ...(spent ? [event("quota_spend", "spend")] : [])];
  const runs = [{classification: "state_refreshed", delivery_outcome: "outcome_progress",
    goal_id: goal, agent_id: agent, todo_id: source, turn_instance_id: turn,
    settlement_identity: identity, vision_checkpoint: {
      schema_version: "vision_checkpoint_v0", agent_id: agent, satisfied: true,
      delivery_boundary: "in_flight_continuation", decision: "unchanged",
      triggers: [{kind: "in_flight_continuation", todo_id: source}],
    }}, ...(spent ? [{classification: "quota_slot_spent", goal_id: goal,
      agent_id: agent, todo_id: source, turn_instance_id: turn,
      settlement_identity: identity}] : [])];
  await writeFile(join(goalRoot, "rollout-event-log.jsonl"),
    `${events.map(value => JSON.stringify(value)).join("\n")}\n`);
  await writeFile(join(runsRoot, "index.jsonl"),
    `${runs.map(value => JSON.stringify(value)).join("\n")}\n`);
  return root;
}

function request() {
  return {goal_id: goal, source_todo_id: source, target_todo_id: target,
    agent_id: agent, source_lease_idempotency_key: "source-key",
    source_lease_version: 3, source_lease_epoch: 5};
}

test("settled execution switch returns an exact source lease and Turn proof", async () => {
  const root = await fixture();
  try {
    const proof = await verifySettledExecutionSwitch(root, request());
    assert.equal(proof.source_lease_idempotency_key, "source-key");
    assert.equal(proof.source_lease_version, 3);
    assert.equal(proof.source_lease_epoch, 5);
    assert.equal(proof.settlement_identity.turn_instance_id, turn);
    assert.equal(proof.settlement_identity.todo_id, source);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("settled execution switch rejects an incomplete or superseded source Turn", async t => {
  await t.test("missing spend", async () => {
    const root = await fixture(false);
    try {
      await assert.rejects(verifySettledExecutionSwitch(root, request()),
        /complete writeback and quota receipts/);
    } finally {await rm(root, {recursive: true, force: true});}
  });
  await t.test("newer source Turn", async () => {
    const root = await fixture();
    try {
      const identity = settlementIdentity({goal_id: goal, agent_id: agent,
        todo_id: source, turn_instance_id: "turn-source-newer"});
      await appendFile(join(root, "goals", goal, "rollout-event-log.jsonl"),
        `${JSON.stringify({schema_version: "loopx_rollout_event_v0", event_id: "newer-guard",
          event_kind: "quota_should_run", goal_id: goal, agent_id: agent,
          run_id: "turn-source-newer", details: {todo_id: source,
            settlement_effect_id: identity.effect_id}})}\n`);
      await assert.rejects(verifySettledExecutionSwitch(root, request()),
        /latest source Todo Turn's complete writeback and quota receipts/);
    } finally {await rm(root, {recursive: true, force: true});}
  });
});
