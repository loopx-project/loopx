import assert from "node:assert/strict";
import test from "node:test";

import {
  PEER_COORDINATION_DIGEST_FIELD_OVERHEAD_BYTES,
  PEER_COORDINATION_DIGEST_MAX_BYTES,
  PEER_COORDINATION_DIGEST_REQUEST_SCHEMA_VERSION,
  PEER_COORDINATION_DIGEST_SCHEMA_VERSION,
  projectPeerCoordinationDigest,
} from "../../loopx/control_plane/goals/peer_coordination_digest.ts";
import {
  buildTurnEnvelope,
  composeCompleteTurnDecision,
  turnEnvelopeActionSignatureDocument,
} from "../../loopx/control_plane/quota/turn_envelope.ts";
import { evaluateTurnEnvelope } from "../../loopx/control_plane/quota/turn_envelope.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";

const OBSERVED_AT = "2026-11-27T05:00:00Z";

function multiAgentWorkItems() {
  return [
    {
      todo_id: "todo_caller_verify",
      status: "open",
      done: false,
      task_class: "advancement_task",
      claimed_by: "agent-a",
      bound_agent: null,
      priority: "P0",
      action_kind: "verify",
      task_domain: "control_plane",
      task_repository: "https://github.com/loopx-project/loopx.git",
      target_key: "turn-envelope",
      required_write_scopes: ["loopx/control_plane/quota/"],
      completion_validation_required: false,
      excluded_agents: [],
      lease: {
        schema_version: "task_lease_v0",
        status: "active",
        owner: "agent-a",
        epoch: 1,
        lease_version: 1,
        expires_at: "2026-11-27T06:00:00Z",
      },
    },
    {
      todo_id: "todo_peer_collision_verify",
      status: "open",
      done: false,
      task_class: "advancement_task",
      claimed_by: "agent-b",
      bound_agent: null,
      priority: "P0",
      action_kind: "refactor",
      task_domain: "control_plane",
      task_repository: "git:github.com/loopx-project/loopx",
      target_key: "turn-envelope",
      required_write_scopes: ["loopx/control_plane/quota/turn_envelope.ts"],
      completion_validation_required: true,
      excluded_agents: [],
      lease: {
        schema_version: "task_lease_v0",
        status: "active",
        owner: "agent-b",
        epoch: 1,
        lease_version: 1,
        expires_at: "2026-11-27T06:00:00Z",
      },
    },
    {
      todo_id: "todo_peer_unleased_p0",
      status: "open",
      done: false,
      task_class: "advancement_task",
      claimed_by: "agent-b",
      bound_agent: null,
      priority: "P0",
      action_kind: "refactor",
      task_domain: "control_plane",
      task_repository: "git:github.com/loopx-project/loopx",
      target_key: "scheduler-hint",
      required_write_scopes: ["loopx/control_plane/scheduler/"],
      completion_validation_required: false,
      excluded_agents: [],
      lease: null,
    },
    {
      todo_id: "todo_peer_synergy_only",
      status: "open",
      done: false,
      task_class: "advancement_task",
      claimed_by: "agent-b",
      bound_agent: null,
      priority: "P2",
      action_kind: "docs",
      task_domain: "control_plane",
      task_repository: "git:github.com/loopx-project/loopx",
      target_key: "docs-sync",
      required_write_scopes: ["docs/architecture/"],
      completion_validation_required: false,
      excluded_agents: [],
      lease: null,
    },
  ];
}

function minimalTurnPayload(overrides: JsonObject = {}): JsonObject {
  return {
    ok: true,
    goal_id: "goal-coord",
    decision: "run",
    should_run: true,
    effective_action: "normal_run",
    state: "eligible",
    reason: "ready to execute",
    action_required: false,
    open_count: 0,
    normal_delivery_allowed: true,
    agent_identity: { agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"] },
    selected_todo: { todo_id: "todo_caller_verify", text: "Verify PR slice" },
    interaction_contract: {
      schema_version: "loopx_interaction_contract_v0",
      mode: "bounded_delivery",
      user_channel: { action_required: false, notify: "DONT_NOTIFY", max_items: 0, actions: [], non_blocking: true },
      agent_channel: {
        must_attempt: true,
        delivery_allowed: true,
        quiet_noop_allowed: false,
        primary_action: "Verify PR slice",
        required_reads: [],
        work_context: { complete: true, selected_todo_ref: "selected_todo", instruction: "Read selected todo." },
      },
      cli_channel: {
        spend_allowed_now: true,
        spend_after_validation: true,
        spend_policy: "normal",
        next_cli_actions: ["loopx quota spend-slot --goal-id goal-coord"],
      },
    },
    ...overrides,
  };
}

test("single-agent goal emits null digest with 0 serialized delta bytes", () => {
  const result = projectPeerCoordinationDigest({
    schema_version: PEER_COORDINATION_DIGEST_REQUEST_SCHEMA_VERSION,
    goal_id: "goal-solo",
    agent_id: "agent-a",
    registered_agent_count: 1,
    work_items: multiAgentWorkItems(),
    observed_at: OBSERVED_AT,
  });
  assert.equal(result.digest, null);
  assert.deepEqual(result.active_opportunity_ids, []);
  assert.deepEqual(result.new_opportunity_ids, []);
  assert.equal(result.serialized_delta_bytes, 0);
});

test("busy multi-agent Turn surfaces only collision & verify within 300 B and excludes unleased/synergy", () => {
  const result = projectPeerCoordinationDigest({
    schema_version: PEER_COORDINATION_DIGEST_REQUEST_SCHEMA_VERSION,
    goal_id: "goal-coord",
    agent_id: "agent-a",
    registered_agent_count: 2,
    work_items: multiAgentWorkItems(),
    observed_at: OBSERVED_AT,
  });
  const digest = result.digest as JsonObject;
  assert.ok(digest !== null && typeof digest === "object");
  assert.equal(digest.schema, PEER_COORDINATION_DIGEST_SCHEMA_VERSION);
  assert.equal(digest.read_only, true);
  assert.equal(digest.grants_authority, false);
  const serializedBytes = result.serialized_delta_bytes as number;
  assert.ok(serializedBytes > 0 && serializedBytes <= PEER_COORDINATION_DIGEST_MAX_BYTES);
  const actualWireBytes = PEER_COORDINATION_DIGEST_FIELD_OVERHEAD_BYTES
    + Buffer.byteLength(JSON.stringify(digest), "utf8");
  assert.equal(serializedBytes, actualWireBytes);

  const items = digest.items as JsonObject[];
  const kinds = items.map((item) => item.kind);
  assert.ok(kinds.includes("collision") || kinds.includes("verify"));
  assert.ok(!kinds.includes("unleased"));
  assert.ok(!kinds.includes("synergy"));

  const seenAll = projectPeerCoordinationDigest({
    schema_version: PEER_COORDINATION_DIGEST_REQUEST_SCHEMA_VERSION,
    goal_id: "goal-coord",
    agent_id: "agent-a",
    registered_agent_count: 2,
    seen_opportunity_ids: result.active_opportunity_ids,
    work_items: multiAgentWorkItems(),
    observed_at: OBSERVED_AT,
  });
  assert.equal(seenAll.digest, null);
  assert.equal(seenAll.serialized_delta_bytes, 0);
  assert.deepEqual(seenAll.new_opportunity_ids, []);
});

test("idle multi-agent caller discovers verify & unleased P0/P1 without synergy noise", () => {
  const result = projectPeerCoordinationDigest({
    schema_version: PEER_COORDINATION_DIGEST_REQUEST_SCHEMA_VERSION,
    goal_id: "goal-coord",
    agent_id: "agent-c",
    registered_agent_count: 3,
    work_items: multiAgentWorkItems(),
    observed_at: OBSERVED_AT,
  });
  const digest = result.digest as JsonObject;
  assert.ok(digest !== null);
  assert.ok((result.serialized_delta_bytes as number) <= PEER_COORDINATION_DIGEST_MAX_BYTES);
  const activeCount = (result.active_opportunity_ids as string[]).length;
  assert.equal(activeCount, 2);
  const items = digest.items as JsonObject[];
  const kinds = new Set(items.map((item) => item.kind));
  assert.ok(kinds.has("verify") || kinds.has("unleased"));
  assert.ok(!kinds.has("collision"));
  assert.ok(!kinds.has("synergy"));
});

test("strict <= 300 B cap emits omitted count without breaking budget even on long ids", () => {
  const longAgent = "agent_" + "x".repeat(48);
  const longPeerTodo = "todo_" + "p".repeat(56);
  const items = Array.from({ length: 5 }, (_, idx) => ({
    todo_id: `${longPeerTodo.slice(0, 60)}_${idx}`,
    status: "open",
    done: false,
    task_class: "advancement_task",
    claimed_by: longAgent,
    bound_agent: null,
    priority: "P0",
    action_kind: "refactor",
    task_domain: "control_plane",
    task_repository: "git:github.com/loopx-project/loopx",
    target_key: "shared-target",
    required_write_scopes: ["loopx/control_plane/quota/"],
    completion_validation_required: true,
    excluded_agents: [],
    lease: null,
  }));
  const result = projectPeerCoordinationDigest({
    schema_version: PEER_COORDINATION_DIGEST_REQUEST_SCHEMA_VERSION,
    goal_id: "goal-long",
    agent_id: "agent-idle-observer",
    registered_agent_count: 2,
    work_items: items,
    observed_at: OBSERVED_AT,
  });
  const digest = result.digest as JsonObject;
  assert.ok(digest !== null);
  assert.ok((result.serialized_delta_bytes as number) <= PEER_COORDINATION_DIGEST_MAX_BYTES);
  assert.ok(typeof digest.omitted === "number" && (digest.omitted as number) >= 1);
  const digestItems = digest.items as JsonObject[];
  assert.deepEqual(
    result.displayed_opportunity_ids,
    digestItems.map((item) => item.id as string),
  );
});

test("omitted overflowing opportunities are not marked seen and surface on follow-up turns", () => {
  const workItems = [0, 1, 2].map((idx) => ({
    todo_id: `todo_overflow_${idx}_` + "x".repeat(12),
    status: "open",
    done: false,
    task_class: "advancement_task",
    claimed_by: "agent-b",
    bound_agent: null,
    priority: "P0",
    action_kind: "refactor",
    task_domain: "control_plane",
    task_repository: "git:github.com/loopx-project/loopx",
    target_key: `module-${idx}`,
    required_write_scopes: [`loopx/control_plane/mod_${idx}/`],
    completion_validation_required: true,
    excluded_agents: [],
    lease: null,
  }));

  const seen: string[] = [];
  const displayedAll: string[] = [];
  let expectedActiveIds: string[] = [];
  let sawOmitted = false;
  for (let turn = 0; turn < 10; turn += 1) {
    const step = projectPeerCoordinationDigest({
      schema_version: PEER_COORDINATION_DIGEST_REQUEST_SCHEMA_VERSION,
      goal_id: "goal-overflow",
      agent_id: "agent-a",
      registered_agent_count: 2,
      work_items: workItems,
      seen_opportunity_ids: seen,
      observed_at: OBSERVED_AT,
    });
    if (step.digest === null) break;
    assert.ok((step.serialized_delta_bytes as number) <= PEER_COORDINATION_DIGEST_MAX_BYTES);
    const stepDigest = step.digest as JsonObject;
    const stepItems = stepDigest.items as JsonObject[];
    const stepDisplayed = step.displayed_opportunity_ids as string[];
    assert.deepEqual(
      stepDisplayed,
      stepItems.map((item) => item.id as string),
    );
    assert.ok(stepDisplayed.length >= 1);
    if (turn === 0) {
      expectedActiveIds = [...(step.active_opportunity_ids as string[])];
      sawOmitted = typeof stepDigest.omitted === "number" && (stepDigest.omitted as number) >= 1;
      assert.ok(stepDisplayed.length < expectedActiveIds.length);
    }
    for (const id of stepDisplayed) {
      assert.ok(!seen.includes(id));
      seen.push(id);
      displayedAll.push(id);
    }
  }
  assert.ok(sawOmitted);
  assert.ok(expectedActiveIds.length >= 3);
  assert.deepEqual([...displayedAll].sort(), [...expectedActiveIds].sort());
});

test("TurnEnvelope v0/v1 and progressive opening preserve peer_coordination_digest cleanly", () => {
  const digest = {
    schema: PEER_COORDINATION_DIGEST_SCHEMA_VERSION,
    read_only: true,
    grants_authority: false,
    items: [{ id: "opp_0123456789abcdef", kind: "verify", peer: "agent-b", todo: "todo_peer_1" }],
  };
  const withoutDigest = minimalTurnPayload();
  const withDigest = minimalTurnPayload({ peer_coordination_digest: digest });

  const envWithout = buildTurnEnvelope({ payload: withoutDigest, scheduler_execution_args: "" });
  const envWith = buildTurnEnvelope({ payload: withDigest, scheduler_execution_args: "" });
  assert.equal((envWithout as JsonObject).peer_coordination_digest, undefined);
  assert.deepEqual((envWith as JsonObject).peer_coordination_digest, digest);
  assert.equal((envWith.action_signature as JsonObject).matches, true);
  assert.deepEqual(
    (turnEnvelopeActionSignatureDocument(envWith) as JsonObject).peer_coordination_digest,
    digest,
  );

  const complete = composeCompleteTurnDecision({ payload: withDigest, scheduler_execution_args: "" });
  assert.deepEqual((complete as JsonObject).peer_coordination_digest, digest);
  const selected = evaluateTurnEnvelope({
    operation: "context_select",
    select: [""],
    list_fields: true,
    include_initial_read_plan: true,
    context: complete,
  });
  const selections = ((selected.initial_read_plan as JsonObject)?.selections ?? []) as JsonObject[];
  assert.ok(selections.some((entry) => entry.pointer === "/peer_coordination_digest"));
});
