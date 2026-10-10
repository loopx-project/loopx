import {executeCoordinationTodoArchiveCompleted} from "../../loopx/control_plane/coordination/todo_archive.ts";
/** Shared real command fixture over the complete production-scale head. */
import assert from "node:assert/strict";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {TODO_DOMAIN_ITEM_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {executeCoordinationTodoCreate} from "../../loopx/control_plane/coordination/todo_create.ts";
import {executeCoordinationTodoClaim} from "../../loopx/control_plane/coordination/todo_claim.ts";
import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";
import {executeCoordinationMonitorPoll} from "../../loopx/control_plane/coordination/todo_monitor_poll.ts";
import {executeCoordinationTodoTerminalLifecycle} from "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";
import {productionScaleCoordinationFixture, PRODUCTION_SCALE_VALIDATION_DECLARATION} from "./production_scale_coordination_fixture.ts";
import type {AuthoritySourceCheck} from "../../loopx/control_plane/coordination/authority_source.ts";


export type Command = "create" | "claim" | "update" | "complete" | "supersede" | "archive" | "monitor";

export async function coordinationCommandFixture(store: AuthorityStore, command: Command) {
  const goal_id = "goal-a";
  const fixture = productionScaleCoordinationFixture(goal_id);
  const projection = fixture.projection;
  // This recovery arm deliberately has no Monitor execution lease. The leased
  // arm separately proves current proof and historical replay in hard mode.
  if (command === "monitor") projection.handoff_mode = "legacy";
  const todos = projection.todos as JsonObject[];
  const leased = new Set((projection.leases as JsonObject[]).map(row => row.todo_id));
  const claimTodo = todos.find(row => row.role === "agent" && row.status === "open" &&
    row.task_class === "advancement_task" && !leased.has(row.todo_id))!;
  const monitor = todos.find(row => row.role === "agent" && row.status === "open" &&
    row.task_class === "continuous_monitor" && !leased.has(row.todo_id))!;
  assert.ok(claimTodo); assert.ok(monitor);
  const operation_id = `recover-${command}`;
  const common = {goal_id, operation_id, registered_agents: fixture.registered_agents,
    dry_run: false, now: new Date("2026-09-07T07:00:00Z")};
  assert.equal((await store.commitAuthority({operation_id: "seed-recovery", expected_provider_revision: null,
    events: [], receipts: [], next_projection: projection})).status, "applied");
  const invoke = (target: AuthorityStore, options: {
    identity?: string; authoritySourcesCurrent?: AuthoritySourceCheck; dryRun?: boolean; pendingValidation?: boolean;
  } = {}): Promise<JsonObject> => {
    const request = {...common, operation_id: options.identity ?? operation_id, dry_run: options.dryRun ?? false};
    const sourceCheck = options.authoritySourcesCurrent;
    if (command === "create") return executeCoordinationTodoCreate(target, {...request,
      actor_agent_id: "agent-a", todo: {schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_recovery_created",
        role: "agent", status: "open", done: false, archive_state: "active", text: "Recover the accepted create"}}, sourceCheck);
    if (command === "claim") return executeCoordinationTodoClaim(target, {...request,
      actor_agent_id: String(claimTodo.claimed_by), claimed_by: String(claimTodo.claimed_by),
      todo_id: String(claimTodo.todo_id), expected_role: "agent",
      lease_request: {idempotency_key: "recovery-lease", expected_version: 0, ttl_seconds: 2700}}, sourceCheck);
    if (command === "update") return executeCoordinationTodoUpdate(target, {...request,
      actor_agent_id: "agent-a", todo_id: fixture.completion_todo_id, expected_role: "agent",
      patch: {text: "Recover the accepted copy edit"}, clear_fields: [],
      lease_idempotency_key: fixture.completion_lease_idempotency_key,
      lease_expected_version: fixture.completion_lease_expected_version}, sourceCheck);
    if (command === "archive") return executeCoordinationTodoArchiveCompleted(target, {...request,
      role: "agent", max_active_done: 5});
    if (command === "monitor") return executeCoordinationMonitorPoll(target, {...request,
      actor_agent_id: String(monitor.claimed_by),
      observation: {todo_id: monitor.todo_id, result_hash: "new-recovery-evidence", material_change: true,
        generated_at: "2026-09-07T07:00:00Z"},
      intent: {next_agent_todo: "Advance the recovered material change", next_action_kind: "implement"}}, sourceCheck);
    const {operation_id: terminalId, ...terminalRequest} = request;
    return executeCoordinationTodoTerminalLifecycle(target, {...terminalRequest, command,
      operation_identity: {kind: "explicit", operation_id: terminalId},
      todo_id: fixture.completion_todo_id, actor_agent_id: "agent-a", expected_role: "agent",
      lifecycle_grants: [], authority_reason: null, decision_outcome: null,
      lease_idempotency_key: fixture.completion_lease_idempotency_key,
      lease_expected_version: fixture.completion_lease_expected_version,
      allow_user_gate_auto_acquire: false, requested_no_followup: command === "complete",
      requested_completion_turn_key: null, requested_completion_identity_source: null,
      linked_successor_todo_ids: [], successor_intents: [], note: null, evidence: "synthetic validation",
      reason: "Replace the scoped work", clear_claim: false, completion_policy_request: null,
      validation_declaration: command === "complete" ? PRODUCTION_SCALE_VALIDATION_DECLARATION : null,
      validation_receipt: command === "complete" && !options.pendingValidation && !request.dry_run ? {schema_version: "issue_fix_validation_command_v0", command_label: "production-scale fixture validation",
        exit_code: 0, passed: true, status: "passed", summary: "synthetic validation passed",
        stdout_captured: false, stderr_captured: false, local_path_captured: false} : null}, sourceCheck);
  };
  return {invoke, operation_id, completion_todo_id: fixture.completion_todo_id,
    initial: await store.loadAuthority()};
}
