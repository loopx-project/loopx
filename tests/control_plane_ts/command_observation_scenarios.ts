/** Real command entrypoints over the mixed production-scale graph. The scenario
 * owns intent and expected behavior; the race harness owns only read ordering. */
import assert from "node:assert/strict";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {executeCoordinationTodoCreate} from "../../loopx/control_plane/coordination/todo_create.ts";
import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";
import {executeCoordinationTodoArchiveCompleted} from "../../loopx/control_plane/coordination/todo_archive.ts";
import {executeCoordinationTodoTerminalLifecycle} from "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";
import {executeCoordinationMonitorPoll} from "../../loopx/control_plane/coordination/todo_monitor_poll.ts";
import {executeCanonicalTaskLeaseAcquire} from "../../loopx/control_plane/coordination/task_lease_acquire.ts";
import {executeCanonicalTaskLeaseLifecycle} from "../../loopx/control_plane/coordination/task_lease_lifecycle.ts";
import {executeCoordinationTodoClaim} from "../../loopx/control_plane/coordination/todo_claim.ts";
import {configureGoalAcceptance, commitGoalAcceptanceVerification, inspectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {TODO_DOMAIN_ITEM_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {productionScaleCoordinationFixture, productionScaleLeasedMonitorFixture,
  productionScaleLeaseAcquisitionFixture, productionScaleLeaseLifecycleFixture} from "./production_scale_coordination_fixture.ts";

export const commandObservationCases = ["create", "update", "claim", "claim-acquire", "archive", "supersede",
  "monitor", "acquire", "renew", "release", "transfer", "acceptance-configure", "acceptance-verify"] as const;
export type CommandObservationCase = typeof commandObservationCases[number];

export async function commandObservationScenario(kind: CommandObservationCase, schema: "native" | "legacy", store: AuthorityStore) {
  const goal = "goal-a", target = "todo_observation_target";
  const agents = ["agent-a", "agent-b"], now = new Date("2026-09-13T10:05:00Z");
  const f = productionScaleCoordinationFixture(goal, schema);
  const todo = {todo_id: target, role: "agent", text: "Deliver an independently verifiable result",
    task_class: "advancement_task", action_kind: "implement", status: "open", done: false,
    archive_state: "active", required_write_scopes: ["command-observation/**"]};
  let projection = authorityProjectionFixture(goal, [...f.projection.todos as JsonObject[], todo],
    f.projection.leases as JsonObject[], schema, {handoff_mode: "legacy"});
  const monitor = productionScaleLeasedMonitorFixture(goal, schema);
  const acquire = productionScaleLeaseAcquisitionFixture(goal, schema);
  const lifecycle = productionScaleLeaseLifecycleFixture(goal, schema);
  if (kind === "monitor") projection = monitor.projection;
  if (kind === "acquire") projection = acquire.projection;
  if (["renew", "release", "transfer"].includes(kind)) projection = lifecycle.projection;
  if (kind === "claim-acquire") projection.handoff_mode = "hard_lease";
  assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    next_projection: projection, events: [], receipts: []})).status, "applied");
  const initial = await store.loadAuthority();
  assert.equal(initial.status, "loaded");
  if (initial.status !== "loaded") throw new Error("fixture head missing");
  const common = {goal_id: goal, actor_agent_id: "agent-a", registered_agents: agents,
    operation_id: `observe-${kind}`, dry_run: false, now};
  const expected_provider_revision = initial.provider_revision;
  const document = {objective: "Deliver an independently verifiable result", non_goals: [],
    criteria: [{id: "outcome", description: "The bounded validation passes",
      validation_argv: [process.execPath, "-e", "process.exit(0)"]}],
    bindings: [{todo_id: target, criterion_ids: ["outcome"]}]};
  let run: (backend: AuthorityStore) => Promise<JsonObject>;
  switch (kind) {
    case "create":
      run = backend => executeCoordinationTodoCreate(backend, {...common,
        todo: {...todo, schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_observation_created", text: "A distinct independently deliverable task"}});
      break;
    case "update":
      run = backend => executeCoordinationTodoUpdate(backend, {...common, todo_id: target, expected_role: "agent",
        patch: {text: "Refined acceptance", note: "Preserve independent evidence"}, clear_fields: [], expected_provider_revision});
      break;
    case "claim":
    case "claim-acquire":
      run = backend => executeCoordinationTodoClaim(backend, {...common, todo_id: target, expected_role: "agent",
        claimed_by: "agent-a", expected_provider_revision,
        ...(kind === "claim-acquire" ? {lease_request: {idempotency_key: "execution-a", expected_version: 0, ttl_seconds: 600}} : {})});
      break;
    case "archive":
      run = backend => executeCoordinationTodoArchiveCompleted(backend, {...common, role: "agent", max_active_done: 0,
        expected_provider_revision});
      break;
    case "supersede":
      run = backend => executeCoordinationTodoTerminalLifecycle(backend, {...common, todo_id: target,
        command: "supersede", expected_role: "agent", lifecycle_grants: [], authority_reason: null,
        decision_outcome: null, lease_idempotency_key: null, lease_expected_version: null,
        allow_user_gate_auto_acquire: false, requested_no_followup: false,
        requested_completion_turn_key: null, requested_completion_identity_source: null,
        linked_successor_todo_ids: [], successor_intents: [], note: "Replacement owns continuation",
        evidence: null, reason: "The owner retired this work", clear_claim: false,
        validation_declaration: null, validation_receipt: null, completion_policy_request: null,
        review_basis: {provider_revision: expected_provider_revision, registry_sha256: "a".repeat(64)}});
      break;
    case "monitor":
      run = backend => executeCoordinationMonitorPoll(backend, {...common, now: monitor.now,
        observation: {todo_id: monitor.target, generated_at: monitor.now.toISOString(), result_hash: "new-evidence", material_change: true},
        intent: {next_agent_todo: "React to changed public evidence", next_action_kind: "implement", next_claimed_by: "agent-a"},
        lease_proof: monitor.proof});
      break;
    case "acquire":
      run = backend => executeCanonicalTaskLeaseAcquire(backend, {goal_id: goal, todo_id: acquire.target, owner: "agent-a",
        idempotency_key: acquire.acquisition.execution_key, expected_version: 0, ttl_seconds: acquire.acquisition.ttl_seconds,
        write_scopes: acquire.acquisition.write_scopes, registered_agents: agents, now: new Date(acquire.scenario.now)});
      break;
    case "renew":
    case "release":
    case "transfer":
      run = backend => executeCanonicalTaskLeaseLifecycle(backend, {goal_id: goal, todo_id: lifecycle.target,
        operation: kind, owner: lifecycle.scenario.owner, idempotency_key: lifecycle.scenario.execution_key,
        expected_version: lifecycle.scenario.version, ttl_seconds: kind === "release" ? null : 600,
        ...(kind === "transfer" ? {new_owner: "agent-b", new_idempotency_key: "receiver-b"} : {}),
        registered_agents: agents, now: new Date(lifecycle.scenario.now)});
      break;
    case "acceptance-configure":
      run = backend => configureGoalAcceptance(backend, {goal_id: goal, actor_agent_id: null,
        operation_id: common.operation_id, expected_provider_revision, document});
      break;
    case "acceptance-verify": {
      assert.equal((await configureGoalAcceptance(store, {goal_id: goal, actor_agent_id: null,
        operation_id: "configure", expected_provider_revision, document})).status, "applied");
      const basis = await inspectGoalAcceptance(store, goal);
      run = backend => commitGoalAcceptanceVerification(backend, {goal_id: goal, operation_id: common.operation_id,
        expected_provider_revision: basis.provider_revision, revision: basis.revision, contract_digest: basis.contract_digest,
        results: [{criterion_id: "outcome", passed: true, exit_code: 0}]});
      break;
    }
  }
  return {run, projection};
}
