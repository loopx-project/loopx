import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {AGENT_OPERATION_REVISION, MANAGED_OPERATION_REVISION, managedOperationBindingCurrent, deriveAgentOperationActor, normalizeAgentOperationExecutor, planAgentOperationHandoff,
  projectAgentOperationInbox, projectManagedOperationTransport} from "../../loopx/control_plane/work_items/operation_agent_handoff.ts";

function input(): JsonObject {
  const executor = {kind: "agent_session", host_surface: "codex-app", thread_id: "original-thread",
    revision: AGENT_OPERATION_REVISION};
  const parameters = {goal_id: "test-goal", agent_id: "test-agent", executor,
    payload_digest: "payload", projection_digest: "projection", destination_account_ref: "opaque-account",
    expires_at: "2030-01-01T01:00:00Z", authorized_principals: ["opaque-principal"]};
  const operation = {...parameters, operation_id: "operation-1", executor_revision: executor.revision,
    confirmation_digest: "confirmation", lifecycle_state: "claimed",
    confirmation: {decision: "confirm", confirmation_digest: "confirmation"}, claim: {claim_id: "claim-1"}};
  return {action: "consume", now: "2030-01-01T00:00:00Z", binding_current: true, consumption_id: "attempt-1",
    actor: {goal_id: "test-goal", agent_id: "test-agent", host_surface: "codex-app", thread_id: "original-thread"},
    digests: {payload_digest: "payload", projection_digest: "projection", confirmation_digest: "confirmation",
      outcome_digest: "unknown-result-digest"},
    proposal: {action_kind: "operation.execute", proposal_id: "operation-1", status: "applying",
      normalized_parameters: parameters, operation}};
}
const operation = (value: JsonObject) => (value.proposal as JsonObject).operation as JsonObject;

test("operator transport preflight projects pinned configuration without claiming authentication or runtime qualification", () => {
  const input = {host: "codex-cli", sandbox: "read-only", model: "test-model", reasoning_effort: "xhigh"};
  const projected = projectManagedOperationTransport(input);
  assert.equal(projected.reason, null);
  assert.equal((projected.transport as JsonObject).configuration_valid, true);
  assert.equal((projected.transport as JsonObject).runtime_qualified, false);
  assert.equal((projected.transport as JsonObject).source_conversation_is_executor, false);
  for (const invalid of [{host: "dsh"}, {sandbox: "danger-full-access"}, {model: null}, {reasoning_effort: "unknown"}]) {
    const refused = projectManagedOperationTransport({...input, ...invalid});
    assert.equal((refused.transport as JsonObject).configuration_valid, false);
    assert.notEqual(refused.reason, null);
  }
});

test("operation transport accepts the explicit CLI effort vocabulary without qualifying a model", () => {
  for (const reasoning_effort of ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]) {
    for (const sandbox of ["read-only", "workspace-write"]) {
      const projected = projectManagedOperationTransport({host: "codex-cli", sandbox,
        model: "test-model", reasoning_effort});
      assert.equal(projected.reason, null, reasoning_effort);
      const transport = projected.transport as JsonObject;
      assert.equal(transport.configuration_valid, true);
      assert.equal(transport.runtime_qualified, false);
      assert.equal(transport.human_confirmation_required, true);
      assert.equal(transport.first_consumption_required, true);
      assert.equal(transport.source_conversation_is_executor, false);
    }
  }
  for (const reasoning_effort of [null, "", "unknown", "MAX", 1, ["max"]]) {
    const projected = projectManagedOperationTransport({host: "codex-cli", sandbox: "read-only",
      model: "test-model", reasoning_effort});
    assert.equal(projected.reason, "operation_transport_profile_required");
    assert.equal((projected.transport as JsonObject).configuration_valid, false);
  }
});

function managedInput(): JsonObject {
  const value = input();
  const parameters = (value.proposal as JsonObject).normalized_parameters as JsonObject;
  parameters.executor = {kind: "managed_turn", todo_id: "todo-worker", session_id: "owned-thread",
    profile_digest: "a".repeat(64), model: "test-model", reasoning_effort: "xhigh", revision: MANAGED_OPERATION_REVISION};
  parameters.source_route = {...value.actor as JsonObject};
  operation(value).executor_revision = MANAGED_OPERATION_REVISION;
  value.actor = {goal_id: "test-goal", agent_id: "test-agent", host_surface: "loopx-managed-codex",
    thread_id: "owned-thread", todo_id: "todo-worker", profile_digest: "a".repeat(64), host_turn_id: "native-turn"};
  return value;
}

function nativeStartInput(): JsonObject {
  const value = managedInput();
  value.action = "observe_host_start";
  value.turn_key = "sha256:" + "d".repeat(64);
  Object.assign(value.actor as JsonObject, {model: "test-model", reasoning_effort: "xhigh"});
  Object.assign(operation(value).confirmation as JsonObject,
    {event_id: "authenticated-confirmation-event", confirmed_at: "2029-12-31T23:59:59Z"});
  return value;
}

test("transport accepted start joins the original confirmation and claim without granting execution", () => {
  const value = nativeStartInput();
  const plan = planAgentOperationHandoff(value);
  const receipt = plan.write_host_start as JsonObject;
  assert.equal(plan.execution_allowed, false);
  assert.equal(plan.recorded, true);
  assert.equal(receipt.confirmation_event_id, "authenticated-confirmation-event");
  assert.equal(receipt.claim_id, "claim-1");
  assert.equal(receipt.host_turn_id, "native-turn");
  assert.equal(receipt.turn_key, value.turn_key);
  assert.equal(receipt.trigger_kind, "canonical_operation_inbox");
  assert.equal(receipt.accepted_at, value.now);
  assert.equal(receipt.external_write_performed, false);
  assert.equal(operation(value).agent_handoff, undefined);
  operation(value).host_start = receipt;
  // First observation is immutable even after another normally admitted Turn.
  const replay = planAgentOperationHandoff({...value, now: "2030-01-01T00:10:00Z",
    actor: {...value.actor as JsonObject, host_turn_id: "later-native-turn"}});
  assert.equal(replay.recorded, false);
  assert.equal(replay.write_host_start, undefined);
  assert.deepEqual(replay.host_start, receipt);
  assert.equal(replay.host_delivery, "native_start_accepted");
  assert.equal(planAgentOperationHandoff({...value, action: "consume"}).execution_allowed, true);
});

test("native start refuses drift, expiry, missing acceptance identity and effect/reconciliation state", () => {
  const changes: Array<(value: JsonObject) => void> = [
    value => {value.binding_current = false;},
    value => {(value.actor as JsonObject).todo_id = "other-todo";},
    value => {(value.actor as JsonObject).thread_id = "source-thread";},
    value => {(value.actor as JsonObject).profile_digest = "b".repeat(64);},
    value => {(value.actor as JsonObject).model = "other-model";},
    value => {(value.actor as JsonObject).reasoning_effort = "high";},
    value => {(value.actor as JsonObject).host_turn_id = null;},
    value => {value.turn_key = "unaccepted-process-launch";},
    value => {value.now = "2030-01-01T01:00:00Z";},
    value => {(operation(value).confirmation as JsonObject).confirmed_at = "2030-01-01T00:01:00Z";},
    value => {operation(value).confirmation = null;},
    value => {operation(value).claim = null;},
    value => {operation(value).agent_handoff = {consumption_id: "already-consumed"};},
    value => {operation(value).outcome = {outcome: "submission_unknown"};},
  ];
  for (const change of changes) {
    const value = nativeStartInput(); change(value);
    assert.throws(() => planAgentOperationHandoff(value));
  }
  assert.throws(() => planAgentOperationHandoff({...input(), action: "observe_host_start"}));
});

test("exact managed scope is filtered before bounded inbox pagination", () => {
  const route = nativeStartInput().actor as JsonObject;
  const items = Array.from({length: 25}, (_, index) => ({operation_id: `operation-${index}`, route: {...route, todo_id: "other-todo"}}));
  items.push({operation_id: "operation-matching", route: {...route}});
  const projected = projectAgentOperationInbox({items, executor_route: route, cursor_scope: "a".repeat(64)});
  assert.equal(projected.pending_count, 1);
  assert.equal((projected.items as JsonObject[])[0].operation_id, "operation-matching");
  assert.equal(projected.next_cursor, null);
});

test("source context is not managed execution identity and old approvals never migrate", () => {
  const value = managedInput();
  const plan = planAgentOperationHandoff(value);
  assert.equal(plan.execution_allowed, true);
  assert.equal(plan.host_authentication_required, false);
  assert.equal((plan.source_route as JsonObject).host_surface, "codex-app");
  assert.equal((plan.route as JsonObject).host_surface, "loopx-managed-codex");
  assert.equal((plan.write_handoff as JsonObject).host_turn_id, "native-turn");
  for (const change of [
    {thread_id: "other-thread"}, {profile_digest: "b".repeat(64)}, {todo_id: "todo-other"},
    {host_surface: "codex-app"}, {host_turn_id: null},
  ]) assert.throws(() => planAgentOperationHandoff({...value, actor: {...value.actor as JsonObject, ...change}}));
  assert.throws(() => planAgentOperationHandoff({...input(), actor: value.actor}));
  operation(value).agent_handoff = plan.write_handoff;
  assert.equal(planAgentOperationHandoff(value).execution_allowed, false);
});

test("managed binding is the existing exact Goal/Todo/session/profile owner, never a source route", () => {
  const parameters = (managedInput().proposal as JsonObject).normalized_parameters as JsonObject;
  parameters.origin_goal_ref = {goal_id: "test-goal", goal_instance_id: "instance-1"};
  const session = {schema_version: "loopx_codex_cli_session_v1", goal_id: "test-goal", agent_id: "test-agent",
    todo_id: "todo-worker", session_id: "owned-thread", operation_profile_digest: "a".repeat(64),
    operation_transport: "app-server-operation-tools-v0",
    operation_model: "test-model", operation_reasoning_effort: "xhigh",
    goal_ref: {goal_instance_id: "instance-1", goal_id: "test-goal"}};
  assert.equal(managedOperationBindingCurrent({parameters, session}).current, true);
  for (const changed of [{session_id: "replacement"}, {todo_id: "other"}, {agent_id: "other"},
    {operation_profile_digest: "b".repeat(64)}, {operation_transport: null}, {goal_ref: {goal_id: "test-goal", goal_instance_id: "instance-2"}},
    {schema_version: "future"}]) assert.equal(managedOperationBindingCurrent({parameters, session: {...session, ...changed}}).current, false);
  assert.equal(managedOperationBindingCurrent({parameters, session: null}).current, false);
});

test("public caller identity stays blocked, including exact same-user environment forgery", () => {
  const requested = input().actor as JsonObject;
  for (const thread_id of [null, "", "another-thread", "original-thread"]) {
    assert.throws(() => deriveAgentOperationActor({requested, ambient: {host_surface: "codex-app", thread_id}}),
      {code: "operation_host_authentication_unavailable", kind: "request_rejected"});
  }
  assert.throws(() => deriveAgentOperationActor({requested,
    ambient: {host_surface: "unsupported-host", thread_id: "original-thread"}}));
  for (const claimedProof of [{verified: true}, {signature: "self-signed", issuer: "local-cli"},
    {caller_context_source: "authenticated_host_transport"}]) {
    assert.throws(() => deriveAgentOperationActor({requested, ambient: requested, proof: claimedProof}),
      {code: "operation_host_authentication_unavailable"});
  }
});

test("only the first consumed canonical confirmation grants the original session execution", () => {
  const value = input();
  const plan = planAgentOperationHandoff(value);
  assert.equal(plan.execution_allowed, true);
  assert.equal(plan.host_delivery, "not_attempted");
  operation(value).agent_handoff = plan.write_handoff;
  for (const consumption_id of ["attempt-1", "new-attempt"]) {
    const replay = planAgentOperationHandoff({...value, consumption_id});
    assert.equal(replay.execution_allowed, false);
    assert.equal(replay.needs_reconciliation, true);
  }
});

test("each immutable term, confirmation, route and current binding fails closed independently", () => {
  const mutate: Array<(value: JsonObject) => void> = [
    value => {operation(value).confirmation = null;},
    value => {operation(value).claim = null;},
    value => {operation(value).authorized_principals = ["other-principal"];},
    value => {operation(value).expires_at = "2030-01-01T02:00:00Z";},
    value => {operation(value).destination_account_ref = "other-account";},
    value => {(value.actor as JsonObject).thread_id = "replacement-thread";},
    value => {value.binding_current = false;},
    value => {value.now = "2030-01-01T01:00:00Z";},
  ];
  for (const change of mutate) {
    const value = input(); change(value);
    assert.throws(() => planAgentOperationHandoff(value));
  }
  assert.throws(() => normalizeAgentOperationExecutor({executor: {
    ...((input().proposal as JsonObject).normalized_parameters as JsonObject).executor as JsonObject,
    resume_prompt: "Injected instructions are not an executor binding",
  }}));
});

test("unknown submission remains a reconciliation obligation after expiry with no new execution", () => {
  const value = input();
  operation(value).agent_handoff = planAgentOperationHandoff(value).write_handoff;
  operation(value).lifecycle_state = "outcome_observed";
  operation(value).outcome = {outcome: "submission_unknown"};
  const projected = planAgentOperationHandoff({...value, action: "project", now: "2031-01-01T00:00:00Z"});
  assert.equal(projected.status, "submission_unknown");
  assert.equal(projected.needs_reconciliation, true);
  assert.equal(projected.execution_allowed, false);
  const outcome: JsonObject = {schema_version: "loopx_operation_outcome_v0", operation_id: "operation-1",
    payload_digest: "payload", confirmation_digest: "confirmation", claim_id: "claim-1",
    executor_revision: AGENT_OPERATION_REVISION, consumption_id: "attempt-1", outcome: "not_executed",
    projection_verified: true, simulation: false, external_write_performed: false,
    evidence_refs: ["receipt:fixture-original"]};
  assert.throws(() => planAgentOperationHandoff({...value, action: "report", outcome}));
  outcome.reconciles_outcome_digest = "unknown-result-digest";
  const final = planAgentOperationHandoff({...value, action: "report", now: "2031-01-01T00:00:00Z",
    binding_current: false, outcome});
  assert.equal(final.execution_allowed, false);
  assert.equal(final.write_reconciliation, true);
});

test("a currently bound replacement owns historical reconciliation, never the original consumption", () => {
  const value = input();
  operation(value).agent_handoff = planAgentOperationHandoff(value).write_handoff;
  operation(value).lifecycle_state = "outcome_observed";
  operation(value).outcome = {outcome: "submission_unknown"};
  const replacement = {...value.actor as JsonObject, thread_id: "replacement-thread"};
  const recovery = {...value, actor: replacement, binding_current: false, actor_binding_current: true,
    now: "2031-01-01T00:00:00Z"};
  const inspected = planAgentOperationHandoff({...recovery, action: "inspect"});
  assert.deepEqual(inspected.route, value.actor);
  assert.deepEqual((inspected.access as JsonObject).owner, replacement);
  assert.equal((inspected.access as JsonObject).permission, "historical_evidence_only");
  assert.equal(inspected.execution_allowed, false);
  assert.throws(() => planAgentOperationHandoff({...recovery, action: "consume", consumption_id: "attempt-2"}));
  const outcome: JsonObject = {schema_version: "loopx_operation_outcome_v0", operation_id: "operation-1",
    payload_digest: "payload", confirmation_digest: "confirmation", claim_id: "claim-1",
    executor_revision: AGENT_OPERATION_REVISION, consumption_id: "attempt-1", outcome: "not_executed",
    projection_verified: true, simulation: false, external_write_performed: false,
    evidence_refs: ["receipt:original-system-reconciliation"], reconciles_outcome_digest: "unknown-result-digest"};
  const report = planAgentOperationHandoff({...recovery, action: "report", outcome});
  assert.equal(report.execution_allowed, false);
  assert.equal(report.write_reconciliation, true);
  assert.deepEqual((report.report_provenance as JsonObject).owner, replacement);
  assert.deepEqual((report.report_provenance as JsonObject).original_route, value.actor);
  for (const rejected of [
    {...recovery, actor_binding_current: false},
    {...recovery, binding_current: true},
    {...recovery, actor: {...replacement, agent_id: "other-agent"}},
    {...recovery, actor: {...replacement, goal_id: "other-goal"}},
  ]) {
    assert.throws(() => planAgentOperationHandoff({...rejected, action: "inspect"}));
    assert.throws(() => planAgentOperationHandoff({...rejected, action: "report", outcome}));
  }
  operation(value).agent_handoff = null;
  assert.throws(() => planAgentOperationHandoff({...recovery, action: "inspect"}));
});

test("bounded inbox retains recovery first and declares overflow instead of silently discarding it", () => {
  const items = Array.from({length: 22}, (_, index) => ({operation_id: `operation-${String(index).padStart(2, "0")}`,
    needs_reconciliation: index === 21, execution_allowed: false}));
  const cursor_scope = "a".repeat(64);
  const page = projectAgentOperationInbox({items, cursor_scope});
  assert.equal((page.items as JsonObject[]).length, 20);
  assert.equal((page.items as JsonObject[])[0].operation_id, "operation-21");
  assert.equal(page.pending_count, 22);
  assert.equal((page.overflow as JsonObject).count, 2);
  assert.equal((page.overflow as JsonObject).next_operation_id, "operation-19");
  assert.equal(items[0].operation_id, "operation-00");
  const rest = projectAgentOperationInbox({items, cursor_scope, cursor: page.next_cursor});
  assert.deepEqual((rest.items as JsonObject[]).map(item => item.operation_id), ["operation-19", "operation-20"]);
  assert.equal(rest.next_cursor, null);
  assert.equal(rest.pending_count, 22);
  assert.throws(() => projectAgentOperationInbox({items, cursor_scope: "b".repeat(64), cursor: page.next_cursor}));
});
