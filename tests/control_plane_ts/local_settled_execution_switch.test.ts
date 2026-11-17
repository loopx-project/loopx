import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdir, mkdtemp, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import {settlementIdentity, type JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {
  claimLocalCoordinationTodo,
  LOCAL_COORDINATION_TODO_MANAGED_CLAIM_REQUEST_SCHEMA,
} from "../../loopx/control_plane/coordination/local_authority_runtime.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {engageCanonicalRoute} from "./local_promotion_fixture.ts";

test("local managed claim verifies settlement and atomically switches overlapping execution", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-local-settled-switch-"));
  try {
    await engageCanonicalRoute(root);
    const registryPath = join(root, "registry.json");
    await writeFile(registryPath, "{}");
    const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
    const repository = "git:github.com/example/project";
    const todos = ["todo_source", "todo_target"].map(todo_id => ({todo_id,
      role: "agent", status: "open", done: false, archive_state: "active",
      text: `Execute ${todo_id}`, task_class: "advancement_task", task_domain: "code",
      task_repository: repository, required_write_scopes: ["src/**"]}));
    assert.equal((await store.commitAuthority({operation_id: "seed-switch",
      expected_provider_revision: null,
      next_projection: authorityProjectionFixture("goal-a", todos, [], "native",
        {handoff_mode: "hard_lease"}), events: [], receipts: []})).status, "applied");
    const base = {schema_version: LOCAL_COORDINATION_TODO_MANAGED_CLAIM_REQUEST_SCHEMA,
      runtime_root: root, goal_id: "goal-a", role: "agent", claimed_by: "agent-a",
      actor_agent_id: "agent-a", registered_agents: ["agent-a"], manage_execution: true,
      registry_source: {path: registryPath,
        sha256: createHash("sha256").update("{}").digest("hex")},
      lease_request: null, observed_at: "2026-09-13T10:00:00Z", dry_run: false};
    const sourceClaim = await claimLocalCoordinationTodo({...base,
      todo_id: "todo_source", operation_id: "claim-source"});
    assert.equal(sourceClaim.status, "applied", JSON.stringify(sourceClaim));

    const turn = "turn-source-settled";
    const identity = settlementIdentity({goal_id: "goal-a", agent_id: "agent-a",
      todo_id: "todo_source", turn_instance_id: turn});
    const goalRoot = join(root, "goals", "goal-a"), runsRoot = join(goalRoot, "runs");
    await mkdir(runsRoot, {recursive: true});
    const event = (kind: string, id: string) => ({schema_version: "loopx_rollout_event_v0",
      event_id: id, event_kind: kind, goal_id: "goal-a", agent_id: "agent-a",
      run_id: turn, details: {todo_id: "todo_source", settlement_effect_id: identity.effect_id}});
    await writeFile(join(goalRoot, "rollout-event-log.jsonl"),
      [event("quota_should_run", "guard"), event("refresh_state", "writeback"),
        event("quota_spend", "spend")].map(value => JSON.stringify(value)).join("\n") + "\n");
    const checkpoint = {schema_version: "vision_checkpoint_v0", agent_id: "agent-a",
      satisfied: true, delivery_boundary: "in_flight_continuation", decision: "unchanged",
      triggers: [{kind: "in_flight_continuation", todo_id: "todo_source"}]};
    await writeFile(join(runsRoot, "index.jsonl"), [
      {classification: "state_refreshed", delivery_outcome: "outcome_progress",
        goal_id: "goal-a", agent_id: "agent-a", todo_id: "todo_source",
        turn_instance_id: turn, settlement_identity: identity, vision_checkpoint: checkpoint},
      {classification: "quota_slot_spent", goal_id: "goal-a", agent_id: "agent-a",
        todo_id: "todo_source", turn_instance_id: turn, settlement_identity: identity},
    ].map(value => JSON.stringify(value)).join("\n") + "\n");

    const switched = await claimLocalCoordinationTodo({...base,
      todo_id: "todo_target", operation_id: "claim-target",
      allow_settled_execution_switch: true});
    assert.equal(switched.status, "applied", JSON.stringify(switched));
    assert.equal((switched.execution_switch as JsonObject).source_todo_id, "todo_source");
    const head = await store.loadAuthority();
    assert.equal(head.status, "loaded");
    if (head.status !== "loaded") return;
    const leases = head.head.leases as JsonObject[];
    assert.equal(leases.find(row => row.todo_id === "todo_source")?.status, "released");
    assert.equal(leases.find(row => row.todo_id === "todo_target")?.status, "active");
  } finally {await rm(root, {recursive: true, force: true});}
});
