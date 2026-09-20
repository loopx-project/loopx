import {registerUserCompletionUpdateConformance} from "./user_completion_update_conformance.ts";
import {registerLeaseAcquisitionConformance} from "./lease_acquisition_conformance.ts";
import {registerClaimTransferConformance} from "./claim_transfer_conformance.ts";
import {registerLeasedMonitorConformance} from "./monitor_poll_lease_conformance.ts";
import {registerMonitorObservationUpdateConformance} from "./monitor_observation_update_conformance.ts";
import {registerLeaseLifecycleConformance} from "./lease_lifecycle_conformance.ts";
import {registerMonitorConfigurationConformance} from "./monitor_configuration_conformance.ts";
import {registerAuthorityScanConformance} from "./authority_scan_conformance.ts";
import {executeCoordinationTodoArchiveCompleted} from "../../loopx/control_plane/coordination/todo_archive.ts";
import {registerHandoffModeConformance} from "./handoff_mode_conformance.ts";
import {registerOwnershipObservationConformance} from "./ownership_observation_conformance.ts";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import {registerCoordinationReceiptConformance} from "./coordination_receipt_conformance.ts";
import {registerAuthoritySourceConformance} from "./authority_source_conformance.ts";
import {registerNativePlanningUpdateConformance} from "./native_planning_update_conformance.ts";

import type {
  AuthorityStore,
  AuthorityStoreCommit,
} from "../../loopx/control_plane/coordination/authority_store.ts";
import {
  canonicalAuthorityBytes,
  canonicalAuthoritySha256,
} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {
  TODO_CANONICAL_READ_RECORD_FIELDS,
  TODO_CANONICAL_READ_RECORD_SCHEMA,
  TODO_DOMAIN_ITEM_SCHEMA,
  TODO_DOMAIN_READ_RECORD_SCHEMA,
  TODO_DOMAIN_RECORD_CONTRACT,
  TODO_ITEM_SCHEMA,
} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {
  coordinationTodoReadModel,
  prepareCoordinationProjectionCommit,
} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import { executeCoordinationTodoClaim } from "../../loopx/control_plane/coordination/todo_claim.ts";
import { executeCoordinationTodoCreate } from "../../loopx/control_plane/coordination/todo_create.ts";
import {executeCoordinationMonitorPoll} from "../../loopx/control_plane/coordination/todo_monitor_poll.ts";
import { executeCoordinationTodoUpdate } from "../../loopx/control_plane/coordination/todo_update.ts";
import { listLocalCoordinationTodos, LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA }
  from "../../loopx/control_plane/coordination/local_authority_runtime.ts";
import { sharedGoalWorkFacts } from "../../loopx/control_plane/goals/shared_goal_work.ts";
import {projectStandingDecisions} from "../../loopx/control_plane/todos/standing_decision.ts";
import {evaluateTodoResumeConditions} from "../../loopx/control_plane/todos/resume_condition.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {
  executeCoordinationTodoTerminalLifecycle,
} from "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";
import {
  PRODUCTION_SCALE_VALIDATION_DECLARATION,
  productionScaleCoordinationFixture,
} from "./production_scale_coordination_fixture.ts";
import {
  authorityProjectionFixture,
} from "./authority_projection_fixture.ts";

export interface AuthorityStoreConformanceFixture {
  store: AuthorityStore;
  contender: AuthorityStore;
}
export type AuthorityStoreConformanceFactory = (
  context: test.TestContext,
) => Promise<AuthorityStoreConformanceFixture>;

const TERMINAL_VALIDATION_DECLARATION = {
  validation_command: null,
  validation_command_argv: ["python3", "-c", "raise SystemExit(0)"],
  validation_label: "provider conformance validation",
  validation_timeout_seconds: 5,
};

function todoClaimProjection(goalId: string, native: boolean): Record<string, unknown> {
  const todos = [{
    schema_version: "todo_item_v0",
    todo_id: "todo-claim",
    role: "agent",
    status: "open",
    done: false,
    text: "Claim through the provider-neutral transaction",
    archive_state: "active",
    source_section: "Agent Todo",
    note: "preserve complete canonical record",
    required_write_scopes: ["loopx/control_plane/**"],
  }];
  if (native) {
    todos[0]!.schema_version = TODO_DOMAIN_ITEM_SCHEMA;
    Reflect.deleteProperty(todos[0]!, "source_section");
  }
  return authorityProjectionFixture(goalId, todos, [], native ? "native" : "legacy", {
    handoff_mode: "soft_claim",
  });
}

function todoTerminalProjection(goalId: string): Record<string, unknown> {
  const todos = [
    {
      schema_version: TODO_DOMAIN_ITEM_SCHEMA,
      todo_id: "todo-terminal",
      role: "agent",
      status: "open",
      done: false,
      text: "Finish the provider-neutral transaction family",
      archive_state: "active",
      task_class: "advancement_task",
      claimed_by: "agent-a",
      note: "preserve target metadata",
      completion_validation_required: true,
      completion_validation_sha256: canonicalAuthoritySha256(
        TERMINAL_VALIDATION_DECLARATION,
      ),
    },
    {
      schema_version: TODO_DOMAIN_ITEM_SCHEMA,
      todo_id: "todo-done-a",
      role: "agent",
      status: "done",
      done: true,
      text: "Older completed work",
      archive_state: "active",
      completed_at: "2026-09-01T00:00:00Z",
    },
    {
      schema_version: TODO_DOMAIN_ITEM_SCHEMA,
      todo_id: "todo-done-b",
      role: "agent",
      status: "done",
      done: true,
      text: "Newer completed work",
      archive_state: "active",
      completed_at: "2026-09-02T00:00:00Z",
    },
    {
      schema_version: TODO_DOMAIN_ITEM_SCHEMA,
      todo_id: "todo-user-decision",
      role: "user",
      status: "done",
      done: true,
      text: "Retain the standing provider decision",
      archive_state: "active",
      task_class: "user_gate",
      decision_scope: {kind: "direction", granularity: "goal", scope_key: goalId},
      decision_outcome: "approve",
      global_gate: true,
      goal_bound: true,
      completed_at: "2026-08-30T00:00:00Z",
    },
    {
      schema_version: TODO_DOMAIN_ITEM_SCHEMA,
      todo_id: "todo-user-ordinary",
      role: "user",
      status: "done",
      done: true,
      text: "Archive the ordinary completed user action",
      archive_state: "active",
      task_class: "user_action",
      goal_bound: true,
      completed_at: "2026-08-31T00:00:00Z",
    },
  ].sort((left, right) => left.todo_id.localeCompare(right.todo_id));
  const leases = [{
      schema_version: "task_lease_v0",
      goal_id: goalId,
      todo_id: "todo-terminal",
      owner: "agent-a",
      idempotency_key: "terminal-lease",
      write_scopes: ["loopx/control_plane/**"],
      acquire_ttl_seconds: 600,
      version: 1,
      lease_epoch: 1,
      acquired_at: "2026-09-07T05:50:00Z",
      updated_at: "2026-09-07T05:50:00Z",
      expires_at: "2026-09-07T06:10:00Z",
      status: "active",
    }];
  return authorityProjectionFixture(goalId, todos, leases, "native", {
    handoff_mode: "hard_lease",
  });
}

export function authorityStoreCommitFixture(
  expectedProviderRevision: string | null,
  operationId: string,
  authorityRevision: number,
  leaseEpoch: number,
): AuthorityStoreCommit {
  return {
    expected_provider_revision: expectedProviderRevision,
    operation_id: operationId,
    events: [{
      schema_version: "loopx_authority_event_v0",
      type: "todo_claimed",
      authority_revision: authorityRevision,
      lease_epoch: leaseEpoch,
    }],
    next_projection: {
      schema_version: "loopx_coordination_head_v1",
      authority_revision: authorityRevision,
      coordination: {
        leases: { "todo-a": { lease_epoch: leaseEpoch } },
      },
    },
    receipts: [{
      schema_version: "loopx_authority_receipt_v0",
      operation_id: operationId,
      accepted_authority_revision: authorityRevision,
      lease_epoch: leaseEpoch,
    }],
  };
}

export function registerAuthorityStoreConformance(
  providerName: string,
  factory: AuthorityStoreConformanceFactory,
): void {
  registerLeaseLifecycleConformance(providerName, factory);
  registerClaimTransferConformance(providerName, factory);
  registerLeaseAcquisitionConformance(providerName, factory);
  registerAuthorityScanConformance(providerName, factory);
  registerOwnershipObservationConformance(providerName, factory);
  registerNativePlanningUpdateConformance(providerName, factory);
  registerUserCompletionUpdateConformance(providerName, factory);
  registerMonitorConfigurationConformance(providerName, factory);
  registerLeasedMonitorConformance(providerName, factory);
  registerMonitorObservationUpdateConformance(providerName, factory);
  registerCoordinationReceiptConformance(providerName, factory);
  registerAuthoritySourceConformance(providerName, factory);
  registerHandoffModeConformance(providerName, factory);
  for (const native of [false, true]) test(`${providerName} conformance: standing revocation survives canonical ordering and archive (${native ? "native" : "legacy"})`, async (t) => {
    const {store} = await factory(t);
    const goal = "goal-standing";
    const records: Record<string, unknown>[] = [
      {todo_id: "todo_aaa_reject", decision_outcome: "reject", completed_at: "2026-09-10T02:00:00Z"},
      {todo_id: "todo_middle", decision_outcome: "approve", unblocks_todo_id: "todo_delivery"},
      {todo_id: "todo_zzz_approve", decision_outcome: "approve", completed_at: "2026-09-10T01:00:00Z"},
    ].map((item, i) => ({schema_version: native ? TODO_DOMAIN_ITEM_SCHEMA : TODO_ITEM_SCHEMA,
      text: "Synthetic decision", role: "user", status: "done", done: true, task_class: "user_gate",
      archive_state: "active", global_gate: true,
      decision_scope: {kind: "write_scope", granularity: "goal", scope_key: "release"},
      ...(!native ? {index: i + 1, source_section: "User Todo"} : {}), ...item}));
    const seeded = await store.commitAuthority({operation_id: "standing-fixture", expected_provider_revision: null,
      events: [], receipts: [], next_projection: {goal_id: goal, todos: records, leases: [],
        todo_read_model: {schema_version: native ? TODO_DOMAIN_READ_RECORD_SCHEMA : TODO_CANONICAL_READ_RECORD_SCHEMA,
          todo_count: records.length, records_sha256: canonicalAuthoritySha256(records),
          contract_fields: native ? [...TODO_DOMAIN_RECORD_CONTRACT.fields] : [...TODO_CANONICAL_READ_RECORD_FIELDS]}}});
    assert.equal(seeded.status, "applied");
    const before = await store.loadAuthority();
    assert.equal(before.status, "loaded");
    if (before.status !== "loaded") return;
    const decisions = projectStandingDecisions(before.head.todos as Record<string, unknown>[])!;
    assert.equal(decisions.active_count, 0);
    assert.equal((decisions.entries as Record<string, unknown>[])[0].source_todo_id, "todo_aaa_reject");
    const request = {goal_id: goal, role: "user" as const, max_active_done: 0,
      operation_id: "standing-archive", dry_run: false, now: new Date("2026-09-10T03:00:00Z")};
    const archived = await executeCoordinationTodoArchiveCompleted(store, request);
    assert.equal(archived.status, "applied", JSON.stringify(archived));
    assert.deepEqual(archived.moved_todo_ids, ["todo_middle"]);
    assert.equal(archived.retained_standing_decision_count, 2);
    const after = await store.loadAuthority();
    assert.equal(after.status, "loaded");
    if (after.status !== "loaded") return;
    assert.deepEqual(projectStandingDecisions(after.head.todos as Record<string, unknown>[]), decisions);
    assert.equal((await executeCoordinationTodoArchiveCompleted(store, request)).status, "replayed");
  });

  for (const native of [false, true]) test(`${providerName} conformance: lease-free legacy-mode Monitor observation and successor (${native ? "native" : "legacy"})`, async (t) => {
    const {store, contender} = await factory(t);
    const goal = "goal-monitor";
    const fixture = productionScaleCoordinationFixture(goal, native ? "native" : "legacy");
    const projection = structuredClone(fixture.projection);
    // A lease-free observation is legal in legacy mode. Hard mode now requires
    // current execution proof; its positive/negative cases have their own fixture.
    projection.handoff_mode = "legacy";
    const records = projection.todos as Record<string, unknown>[];
    const monitor = records.find(todo => todo.task_class === "continuous_monitor" && todo.status !== "done" &&
      !(projection.leases as Record<string, unknown>[]).some(lease => lease.todo_id === todo.todo_id));
    assert.ok(monitor, "complex fixture needs a lease-free Monitor");
    Object.assign(monitor, {target_key: "conformance-watch", cadence: "1h", material_change_generation: 4});
    for (const field of ["claimed_by", "bound_agent", "excluded_agents", "last_checked_at", "monitor_effect_id", "task_repository", "result_hash"]) delete monitor[field];
    // The fixture owns the compatibility conversion; this test only changes
    // the monitor-specific observation fields.
    projection.todo_read_model = coordinationTodoReadModel(
      records,
      (projection.todo_read_model as Record<string, unknown>).schema_version,
    );
    assert.equal((await store.commitAuthority({operation_id: "monitor-seed", expected_provider_revision: null,
      events: [], receipts: [], next_projection: projection})).status, "applied");
    const request = {goal_id: goal, operation_id: "monitor-effect", actor_agent_id: "agent-a",
      registered_agents: ["agent-a", "agent-b"], dry_run: false,
      observation: {todo_id: monitor.todo_id, generated_at: "2099-01-01T00:00:00Z",
        result_hash: "new-evidence", material_change: true},
      intent: {next_agent_todo: "Inspect the newly observed change", next_action_kind: "validate"}};
    const before = await store.loadAuthority();
    const invalid = await executeCoordinationMonitorPoll(store, {...request,
      intent: {...request.intent, next_claimed_by: "unregistered"}});
    assert.equal(invalid.status, "failed");
    assert.deepEqual(await store.loadAuthority(), before);
    const [first, second] = await Promise.all([executeCoordinationMonitorPoll(store, request),
      executeCoordinationMonitorPoll(contender, request)]);
    assert.ok([first, second].some(result => result.status === "applied"), JSON.stringify([first, second]));
    assert.equal((await executeCoordinationMonitorPoll(contender, request)).status, "replayed");
    const after = await store.loadAuthority();
    assert.equal(after.status, "loaded");
    if (after.status !== "loaded") return;
    const next = after.head.todos as Record<string, unknown>[];
    assert.equal(next.length, records.length + 1);
    assert.equal(next.find(todo => todo.todo_id === monitor.todo_id)?.material_change_generation, 5);
    assert.deepEqual(after.head.leases, projection.leases);
    const unrelated = next.filter(todo => todo.todo_id !== monitor.todo_id && records.some(old => old.todo_id === todo.todo_id));
    assert.deepEqual(unrelated, records.filter(todo => todo.todo_id !== monitor.todo_id));
    // Lose the acknowledgement, not the durable write; a new client recovers
    // both halves from the existing receipt without advancing generation again.
    const lostAck = new Proxy(store, {get(target, property) {
      if (property === "commitAuthority") return async (commit: AuthorityStoreCommit) => {
        await target.commitAuthority(commit); return {status: "conflict", reason: "synthetic lost acknowledgement"};
      };
      const value = Reflect.get(target, property); return typeof value === "function" ? value.bind(target) : value;
    }});
    const recovered = await executeCoordinationMonitorPoll(lostAck, {...request, operation_id: "monitor-next-effect",
      observation: {...request.observation, generated_at: "2099-01-01T01:00:00Z", material_change: false}, intent: {}});
    assert.equal(recovered.status, "recovered", JSON.stringify(recovered));
  });
  for (const native of [false, true]) test(`${providerName} conformance: governance reads one full Todo/lease snapshot (${native ? "native" : "legacy"})`, async (t) => {
    const {store} = await factory(t);
    const goal = "goal-governance";
    const fixture = productionScaleCoordinationFixture(goal, native ? "native" : "legacy");
    const projection = structuredClone(fixture.projection);
    const records = projection.todos as Record<string, unknown>[];
    const seeded = await store.commitAuthority({operation_id: "governance-fixture",
      expected_provider_revision: null, events: [], receipts: [], next_projection: projection});
    assert.equal(seeded.status, "applied");
    const request = {schema_version: LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA,
      runtime_root: "/synthetic-runtime", goal_id: goal};
    const plain = await listLocalCoordinationTodos(request, {createStore: () => store});
    assert.equal(plain.status, "loaded");
    assert.equal(Object.hasOwn(plain, "leases"), false); // Default-off response parity.
    assert.deepEqual(await listLocalCoordinationTodos({...request, include_leases: false},
      {createStore: () => store}), plain);
    assert.notEqual((await listLocalCoordinationTodos({...request, include_leases: "true"},
      {createStore: () => store})).status, "loaded");
    const load = store.loadAuthority.bind(store);
    let reads = 0;
    store.loadAuthority = async () => { reads++; return load(); };
    const full = await listLocalCoordinationTodos({...request, include_leases: true}, {createStore: () => store});
    store.loadAuthority = load;
    assert.equal(full.status, "loaded");
    assert.equal(reads, 1);
    assert.equal(full.provider_revision, plain.provider_revision);
    assert.deepEqual(full.todos, plain.todos);
    assert.equal((full.todos as unknown[]).length, fixture.expected_initial_todo_count);
    assert.equal((full.leases as unknown[]).length, fixture.expected_current_lease_count);
    const leases = new Map((full.leases as Record<string, unknown>[]).map((lease) => [lease.todo_id, lease]));
    const evaluation = evaluateTodoResumeConditions({schema_version: "todo_resume_evaluation_request_v0",
      items: full.todos, source_items: full.todos, kinds: ["todo_done", "monitor_changed"]});
    const conditions = new Map((evaluation.conditions as JsonObject[]).map(entry => [entry.todo_id, entry.condition as JsonObject]));
    const items = (full.todos as Record<string, unknown>[]).filter((item) => item.role === "agent")
      .map((item) => ({...item, lease: leases.get(item.todo_id) ?? null,
        ...(conditions.has(item.todo_id) ? {resume_ready: conditions.get(item.todo_id)!.satisfied === true} : {})}));
    const facts = sharedGoalWorkFacts(items, "agent-a", "2026-09-09T12:00:00Z");
    // The completed prerequisite satisfies the explicit wait. Evaluate from
    // the full provider snapshot before passing derived facts to governance.
    assert.deepEqual(facts.frontier_counts, {current_agent_claimed_advancement_count: 13,
      unclaimed_advancement_count: 0, other_agent_claimed_advancement_count: 24});
    assert.equal((facts.goal_todo_inventory as unknown[]).length, 48);
  });
  test(`${providerName} conformance: atomic transition, projection, and receipt`, async (t) => {
    const { store } = await factory(t);
    assert.deepEqual(await store.loadAuthority(), { status: "missing" });

    const applied = await store.commitAuthority(
      authorityStoreCommitFixture(null, "operation-a", 41, 7),
    );
    assert.equal(applied.status, "applied");
    if (applied.status !== "applied") return;
    assert.notEqual(applied.provider_revision, "41");
    assert.notEqual(applied.provider_revision, "7");
    assert.equal(applied.cursor, "1");

    const loaded = await store.loadAuthority();
    assert.equal(loaded.status, "loaded");
    if (loaded.status !== "loaded") return;
    assert.equal(loaded.head.authority_revision, 41);
    assert.deepEqual(loaded.head.coordination, {
      leases: { "todo-a": { lease_epoch: 7 } },
    });
    assert.equal(loaded.provider_revision, applied.provider_revision);
    assert.equal(loaded.cursor, "1");

    const receipt = await store.readReceipt("operation-a");
    assert.equal(receipt.status, "found");
    if (receipt.status === "found") {
      assert.equal(receipt.provider_revision, applied.provider_revision);
      assert.equal(receipt.receipts[0]?.accepted_authority_revision, 41);
      assert.equal(receipt.receipts[0]?.lease_epoch, 7);
    }
  });

  test(`${providerName} conformance: CAS admits one writer`, async (t) => {
    const { store, contender } = await factory(t);
    const results = await Promise.all([
      store.commitAuthority(authorityStoreCommitFixture(null, "operation-a", 1, 1)),
      contender.commitAuthority(authorityStoreCommitFixture(null, "operation-b", 1, 1)),
    ]);
    assert.deepEqual(results.map((result) => result.status).sort(), ["applied", "conflict"]);
    const applied = results.find((result) => result.status === "applied");
    const conflict = results.find((result) => result.status === "conflict");
    assert.ok(applied && applied.status === "applied");
    assert.ok(conflict && conflict.status === "conflict");
    assert.equal(conflict.conflict_kind, "provider_revision_mismatch");
    assert.equal(conflict.current_provider_revision, applied.provider_revision);
    assert.equal(conflict.current_cursor, "1");
  });

  test(`${providerName} conformance: historical replay and operation fencing`, async (t) => {
    const { store } = await factory(t);
    const first = await store.commitAuthority(
      authorityStoreCommitFixture(null, "operation-a", 1, 3),
    );
    assert.equal(first.status, "applied");
    if (first.status !== "applied") return;
    const second = await store.commitAuthority(
      authorityStoreCommitFixture(first.provider_revision, "operation-b", 2, 9),
    );
    assert.equal(second.status, "applied");
    if (second.status !== "applied") return;

    const historical = await store.readReceipt("operation-a");
    assert.equal(historical.status, "found");
    if (historical.status === "found") {
      assert.equal(historical.cursor, "1");
      assert.equal(historical.receipts[0]?.lease_epoch, 3);
    }
    const duplicate = await store.commitAuthority(
      authorityStoreCommitFixture(second.provider_revision, "operation-a", 3, 10),
    );
    assert.deepEqual(duplicate, {
      status: "conflict",
      conflict_kind: "operation_id_exists",
      current_provider_revision: second.provider_revision,
      current_cursor: "2",
    });
    const loaded = await store.loadAuthority();
    assert.equal(loaded.status, "loaded");
    if (loaded.status === "loaded") assert.equal(loaded.head.authority_revision, 2);
  });

  test(`${providerName} conformance: committed scan is ordered and isolated`, async (t) => {
    const { store } = await factory(t);
    const first = await store.commitAuthority(
      authorityStoreCommitFixture(null, "operation-a", 1, 1),
    );
    assert.equal(first.status, "applied");
    if (first.status !== "applied") return;
    await store.commitAuthority(
      authorityStoreCommitFixture(first.provider_revision, "operation-b", 2, 2),
    );

    const firstPage = await store.scanCommitted(null, 1);
    assert.equal(firstPage.status, "page");
    if (firstPage.status !== "page") return;
    assert.equal(firstPage.transactions[0]?.operation_id, "operation-a");
    assert.equal(firstPage.next_cursor, "1");
    assert.equal(firstPage.has_more, true);
    (firstPage.transactions[0]!.projection as { authority_revision: number })
      .authority_revision = 99;

    const secondPage = await store.scanCommitted("1", 1);
    assert.equal(secondPage.status, "page");
    if (secondPage.status === "page") {
      assert.equal(secondPage.transactions[0]?.operation_id, "operation-b");
      assert.equal(secondPage.next_cursor, "2");
      assert.equal(secondPage.has_more, false);
    }
    const loaded = await store.loadAuthority();
    assert.equal(loaded.status, "loaded");
    if (loaded.status === "loaded") assert.equal(loaded.head.authority_revision, 2);
    assert.equal((await store.scanCommitted("3", 1)).status, "failed");
    assert.equal((await store.scanCommitted(null, 0)).status, "failed");
  });

  test(`${providerName} conformance: malformed JSON fails before a write`, async (t) => {
    const { store } = await factory(t);
    const invalidNumber = authorityStoreCommitFixture(null, "operation-nan", 1, 1);
    invalidNumber.next_projection.authority_revision = Number.NaN;
    assert.equal((await store.commitAuthority(invalidNumber)).status, "failed");

    const invalidObject = authorityStoreCommitFixture(null, "operation-date", 1, 1);
    (invalidObject.next_projection as Record<string, unknown>).coordination = new Date();
    assert.equal((await store.commitAuthority(invalidObject)).status, "failed");
    assert.deepEqual(await store.loadAuthority(), { status: "missing" });
  });

  test(`${providerName} conformance: terminal lifecycle and archive share atomic authority`, async (t) => {
    const {store, contender} = await factory(t);
    const goalId = "goal-terminal";
    const initialized = await store.commitAuthority({
      expected_provider_revision: null,
      operation_id: "initialize-terminal",
      events: [],
      receipts: [],
      next_projection: todoTerminalProjection(goalId),
    });
    assert.equal(initialized.status, "applied");
    const successorIntent = {
      role: "agent",
      text: "Continue with the next provider-neutral transaction",
      task_class: "advancement_task",
    };
    const request = {
      goal_id: goalId,
      todo_id: "todo-terminal",
      expected_role: "agent" as const,
      command: "complete" as const,
      actor_agent_id: "agent-a",
      registered_agents: ["agent-a", "agent-b"],
      lifecycle_grants: [],
      authority_reason: null,
      decision_outcome: null,
      operation_id: "complete-terminal",
      lease_idempotency_key: "terminal-lease",
      lease_expected_version: 1,
      allow_user_gate_auto_acquire: false,
      requested_no_followup: false,
      requested_completion_turn_key: null,
      requested_completion_identity_source: null,
      linked_successor_todo_ids: [],
      successor_intents: [successorIntent, {role: "user", task_class: "user_gate",
        text: "Decide the completing agent's next step"}],
      note: "completed atomically",
      evidence: "focused provider conformance",
      reason: null,
      clear_claim: false,
      validation_declaration: TERMINAL_VALIDATION_DECLARATION,
      validation_receipt: null,
      completion_policy_request: {
        schema_version: "loopx_todo_completion_policy_request_v0",
        goal_id: goalId,
        agent_model: "peer_v1",
        claimed_by: "agent-a",
        registered_agents: ["agent-a", "agent-b"],
        next_claimed_by: "agent-b",
        next_agent_todo: successorIntent.text,
        next_continuation_policy: null,
        next_excluded_agents: [],
        self_merged: false,
        evidence: null,
        linked_successors: [],
      },
      dry_run: false,
      now: new Date("2026-09-07T06:00:00Z"),
    };
    const preview = await executeCoordinationTodoTerminalLifecycle(
      store, {...request, dry_run: true},
    );
    assert.equal(preview.status, "planned", JSON.stringify(preview));
    const validation = await executeCoordinationTodoTerminalLifecycle(store, request);
    assert.equal(validation.status, "execute_validation", JSON.stringify(validation));
    assert.equal(
      (validation.validation_effect as Record<string, unknown>)?.kind,
      "caller_validation",
    );
    const commitRequest = {
      ...request,
      validation_receipt: {
        schema_version: "issue_fix_validation_command_v0",
        command_label: "provider conformance validation",
        exit_code: 0,
        passed: true,
        status: "passed",
        summary: "provider conformance validation passed",
        stdout_captured: false as const,
        stderr_captured: false as const,
        local_path_captured: false as const,
      },
    };
    const dangling = await executeCoordinationTodoTerminalLifecycle(store, {
      ...commitRequest,
      operation_id: "complete-dangling-successor",
      linked_successor_todo_ids: ["todo-missing"],
      successor_intents: [],
    });
    assert.equal(dangling.status, "failed");
    assert.equal(dangling.reason_code, "todo_successor_not_found");
    const [first, second] = await Promise.all([
      executeCoordinationTodoTerminalLifecycle(store, commitRequest),
      executeCoordinationTodoTerminalLifecycle(contender, commitRequest),
    ]);
    assert.ok(
      [first.status, second.status].includes("applied"),
      JSON.stringify([first, second]),
    );
    assert.ok(
      [first.status, second.status].every((status) =>
        typeof status === "string" &&
        ["applied", "recovered", "replayed", "conflict"].includes(status)),
      JSON.stringify([first, second]),
    );
    const committedRevisions = [first, second]
      .filter((item) => item.status !== "conflict")
      .map((item) => item.provider_revision);
    assert.equal(new Set(committedRevisions).size, 1, JSON.stringify([first, second]));
    const replayed = await executeCoordinationTodoTerminalLifecycle(store, commitRequest);
    assert.equal(replayed.status, "replayed", JSON.stringify(replayed));
    assert.equal(replayed.changed, false);
    const proseReplay = await executeCoordinationTodoTerminalLifecycle(store, {
      ...commitRequest,
      note: "same operation, revised prose",
      evidence: "revised evidence does not create a new operation",
    });
    assert.equal(proseReplay.status, "replayed", JSON.stringify(proseReplay));
    const changedIntent = await executeCoordinationTodoTerminalLifecycle(store, {
      ...commitRequest,
      successor_intents: [{...successorIntent, text: "A genuinely different successor"}],
      completion_policy_request: {
        ...commitRequest.completion_policy_request,
        next_agent_todo: "A genuinely different successor",
      },
    });
    assert.equal(changedIntent.status, "failed", JSON.stringify(changedIntent));
    assert.equal(changedIntent.failure_kind, "decision_rejection");
    assert.equal(changedIntent.reason_code, "coordination_operation_identity_mismatch");

    const afterCompletion = await store.loadAuthority();
    assert.equal(afterCompletion.status, "loaded");
    if (afterCompletion.status !== "loaded") return;
    const completed = (afterCompletion.head.todos as Record<string, unknown>[])
      .find((todo) => todo.todo_id === "todo-terminal");
    const committedResult = first.status === "conflict" ? second : first;
    const generatedIds = committedResult.generated_successor_todo_ids as string[] | undefined;
    const generatedId = generatedIds?.[0];
    const created = (afterCompletion.head.todos as Record<string, unknown>[])
      .find((todo) => todo.todo_id === generatedId);
    assert.equal(completed?.status, "done");
    assert.equal(completed?.note, "completed atomically");
    assert.equal(completed?.evidence, "focused provider conformance");
    assert.deepEqual(completed?.successor_todo_ids, generatedIds);
    assert.equal(generatedIds?.length, 2);
    const userSuccessor = (afterCompletion.head.todos as Record<string, unknown>[])
      .find((todo) => todo.todo_id === generatedIds?.[1]);
    assert.equal(userSuccessor?.role, "user");
    assert.equal(userSuccessor?.blocks_agent, "agent-a");
    assert.equal(userSuccessor?.bound_agent, "agent-a");
    assert.notEqual(userSuccessor?.global_gate, true);
    assert.equal(created?.claimed_by, "agent-b");
    assert.equal(created?.created_by, "agent-a");
    const releasedLease = (afterCompletion.head.leases as Record<string, unknown>[])
      .find((lease) => lease.todo_id === "todo-terminal");
    assert.equal(releasedLease?.status, "released");

    const archiveRequest = {
      goal_id: goalId,
      role: "agent" as const,
      max_active_done: 1,
      operation_id: "archive-terminal",
      dry_run: false,
      now: new Date("2026-09-07T06:01:00Z"),
    };
    const archived = await executeCoordinationTodoArchiveCompleted(store, archiveRequest);
    assert.equal(archived.status, "applied", JSON.stringify(archived));
    assert.equal(archived.moved_count, 2);
    assert.deepEqual(archived.moved_todo_ids, ["todo-done-a", "todo-done-b"]);
    assert.equal((await executeCoordinationTodoArchiveCompleted(store, archiveRequest)).status,
      "replayed");
    const afterArchive = await store.loadAuthority();
    assert.equal(afterArchive.status, "loaded");
    if (afterArchive.status !== "loaded") return;
    const archivedIds = (afterArchive.head.todos as Record<string, unknown>[])
      .filter((todo) => todo.archive_state === "archive")
      .map((todo) => todo.todo_id);
    assert.deepEqual(archivedIds, ["todo-done-a", "todo-done-b"]);

    const userArchive = await executeCoordinationTodoArchiveCompleted(store, {
      ...archiveRequest,
      role: "user",
      max_active_done: 0,
      operation_id: "archive-user-terminal",
    });
    assert.equal(userArchive.status, "applied", JSON.stringify(userArchive));
    assert.deepEqual(userArchive.moved_todo_ids, ["todo-user-ordinary"]);
    assert.equal(userArchive.retained_standing_decision_count, 1);
    const afterUserArchive = await store.loadAuthority();
    assert.equal(afterUserArchive.status, "loaded");
    if (afterUserArchive.status !== "loaded") return;
    const standing = (afterUserArchive.head.todos as Record<string, unknown>[])
      .find((todo) => todo.todo_id === "todo-user-decision");
    assert.equal(standing?.archive_state, "active");

    const beforeNoChange = await store.loadAuthority();
    assert.equal(beforeNoChange.status, "loaded");
    if (beforeNoChange.status !== "loaded") return;
    const noChangeOperation = "archive-terminal-no-change";
    const noChange = await executeCoordinationTodoArchiveCompleted(store, {
      ...archiveRequest,
      operation_id: noChangeOperation,
    });
    assert.equal(noChange.status, "no_change", JSON.stringify(noChange));
    assert.equal(noChange.changed, false);
    assert.equal(noChange.moved_count, 0);
    assert.equal(noChange.provider_revision, beforeNoChange.provider_revision);
    assert.equal(noChange.cursor, beforeNoChange.cursor);
    assert.equal((await store.readReceipt(noChangeOperation)).status, "missing");
    const afterNoChange = await store.loadAuthority();
    assert.equal(afterNoChange.status, "loaded");
    if (afterNoChange.status !== "loaded") return;
    assert.equal(afterNoChange.provider_revision, beforeNoChange.provider_revision);
    assert.equal(afterNoChange.cursor, beforeNoChange.cursor);
  });

  test(`${providerName} conformance: archive binds the observed head and replays before current eligibility`, async (t) => {
    const {store} = await factory(t);
    const goalId = "goal-archive-retry";
    const seed = await store.commitAuthority({
      operation_id: "archive-retry-seed", expected_provider_revision: null,
      next_projection: todoTerminalProjection(goalId), events: [], receipts: [],
    });
    assert.equal(seed.status, "applied");
    if (seed.status !== "applied") return;
    const request = {goal_id: goalId, role: "agent" as const, max_active_done: 0,
      operation_id: "archive-retry", dry_run: false,
      expected_provider_revision: seed.provider_revision,
      now: new Date("2026-09-08T01:00:00Z")};
    const before = await store.loadAuthority();
    const stale = await executeCoordinationTodoArchiveCompleted(store, {
      ...request, expected_provider_revision: "stale-observed-revision",
    });
    assert.equal(stale.status, "conflict", JSON.stringify(stale));
    assert.equal(stale.conflict_kind, "provider_revision_mismatch");
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal((await store.readReceipt(request.operation_id)).status, "missing");

    const applied = await executeCoordinationTodoArchiveCompleted(store, request);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    assert.deepEqual(applied.moved_todo_ids, ["todo-done-a", "todo-done-b"]);
    const after = await store.loadAuthority();
    const replay = await executeCoordinationTodoArchiveCompleted(store, {
      ...request, now: new Date("2026-09-08T02:00:00Z"),
    });
    assert.equal(replay.status, "replayed", JSON.stringify(replay));
    assert.equal(replay.moved_count, 2);
    assert.deepEqual(replay.original_receipt, applied.original_receipt);
    assert.deepEqual(await store.loadAuthority(), after);
    const changedIntent = await executeCoordinationTodoArchiveCompleted(store, {
      ...request, expected_provider_revision: String(applied.provider_revision),
    });
    assert.equal(changedIntent.reason_code, "coordination_operation_identity_mismatch");
    assert.deepEqual(await store.loadAuthority(), after);
  });

  test(`${providerName} conformance: supersede preserves the legacy terminal continuation`, async (t) => {
    const {store} = await factory(t);
    const goalId = "goal-supersede";
    const initialized = await store.commitAuthority({
      expected_provider_revision: null,
      operation_id: "initialize-supersede",
      events: [],
      receipts: [],
      next_projection: todoClaimProjection(goalId, true),
    });
    assert.equal(initialized.status, "applied");
    const superseded = await executeCoordinationTodoTerminalLifecycle(store, {
      goal_id: goalId,
      todo_id: "todo-claim",
      expected_role: "agent",
      command: "supersede",
      actor_agent_id: "agent-a",
      registered_agents: ["agent-a", "agent-b"],
      lifecycle_grants: [],
      authority_reason: null,
      decision_outcome: null,
      lease_idempotency_key: null,
      lease_expected_version: null,
      operation_id: "supersede-terminal",
      allow_user_gate_auto_acquire: false,
      requested_no_followup: false,
      requested_completion_turn_key: null,
      requested_completion_identity_source: null,
      linked_successor_todo_ids: [],
      successor_intents: [],
      note: "superseded",
      evidence: null,
      reason: "the replacement owns the next action",
      clear_claim: false,
      validation_declaration: null,
      validation_receipt: null,
      completion_policy_request: null,
      dry_run: false,
      now: new Date("2026-09-07T06:02:00Z"),
    });
    assert.equal(superseded.status, "applied", JSON.stringify(superseded));
    const loaded = await store.loadAuthority();
    assert.equal(loaded.status, "loaded");
    if (loaded.status !== "loaded") return;
    const target = (loaded.head.todos as Record<string, unknown>[])
      .find((todo) => todo.todo_id === "todo-claim");
    assert.equal(target?.status, "done");
    assert.equal(target?.completion_continuation, "active_goal");
  });

  test(`${providerName} conformance: archive preserves legacy source order`, async (t) => {
    const {store} = await factory(t);
    const goalId = "goal-archive-order";
    const todos = [
      {
        schema_version: TODO_ITEM_SCHEMA,
        todo_id: "todo-a-newer",
        role: "agent",
        status: "done",
        done: true,
        text: "Newer completed compatibility Todo",
        archive_state: "active",
        source_section: "Agent Todo",
        index: 2,
      },
      {
        schema_version: TODO_ITEM_SCHEMA,
        todo_id: "todo-z-older",
        role: "agent",
        status: "done",
        done: true,
        text: "Older completed compatibility Todo",
        archive_state: "active",
        source_section: "Agent Todo",
        index: 1,
      },
    ].sort((left, right) => left.todo_id.localeCompare(right.todo_id));
    const initialized = await store.commitAuthority({
      expected_provider_revision: null,
      operation_id: "initialize-archive-order",
      events: [],
      receipts: [],
      next_projection: {
        goal_id: goalId,
        todos,
        leases: [],
        todo_read_model: {
          schema_version: TODO_CANONICAL_READ_RECORD_SCHEMA,
          todo_count: todos.length,
          records_sha256: createHash("sha256")
            .update(canonicalAuthorityBytes(todos))
            .digest("hex"),
          contract_fields: [...TODO_CANONICAL_READ_RECORD_FIELDS],
        },
      },
    });
    assert.equal(initialized.status, "applied");
    const archived = await executeCoordinationTodoArchiveCompleted(store, {
      goal_id: goalId,
      role: "agent",
      max_active_done: 1,
      operation_id: "archive-by-source-order",
      dry_run: false,
      now: new Date("2026-09-07T06:03:00Z"),
    });
    assert.equal(archived.status, "applied", JSON.stringify(archived));
    assert.deepEqual(archived.moved_todo_ids, ["todo-z-older"]);
  });

  for (const schema of ["legacy", "native"] as const) test(`${providerName} conformance: production-scale terminal lifecycle stays bounded (${schema})`, async (t) => {
    const {store} = await factory(t);
    const goalId = "goal-production-scale";
    const fixture = productionScaleCoordinationFixture(goalId, schema);
    assert.equal(fixture.projection.handoff_mode, "hard_lease");
    const initialized = await store.commitAuthority({
      expected_provider_revision: null,
      operation_id: "initialize-production-scale",
      events: [],
      receipts: [],
      next_projection: fixture.projection,
    });
    assert.equal(initialized.status, "applied");
    const common = {
      goal_id: goalId,
      expected_role: "agent" as const,
      registered_agents: fixture.registered_agents,
      lifecycle_grants: [],
      authority_reason: null,
      decision_outcome: null,
      lease_idempotency_key: null,
      lease_expected_version: null,
      allow_user_gate_auto_acquire: false,
      requested_completion_turn_key: null,
      requested_completion_identity_source: null,
      linked_successor_todo_ids: [],
      successor_intents: [],
      note: null,
      evidence: "synthetic production-scale conformance",
      reason: null,
      clear_claim: false,
      completion_policy_request: null,
      dry_run: false,
    };
    const completed = await executeCoordinationTodoTerminalLifecycle(store, {
      ...common,
      todo_id: fixture.completion_todo_id,
      command: "complete",
      actor_agent_id: "agent-a",
      lease_idempotency_key: fixture.completion_lease_idempotency_key,
      lease_expected_version: fixture.completion_lease_expected_version,
      operation_id: "complete-production-scale",
      requested_no_followup: true,
      validation_declaration: PRODUCTION_SCALE_VALIDATION_DECLARATION,
      validation_receipt: {
        schema_version: "issue_fix_validation_command_v0",
        command_label: "production-scale fixture validation",
        exit_code: 0,
        passed: true,
        status: "passed",
        summary: "synthetic production-scale validation passed",
        stdout_captured: false,
        stderr_captured: false,
        local_path_captured: false,
      },
      now: new Date("2026-09-07T07:00:00Z"),
    });
    assert.equal(completed.status, "applied", JSON.stringify(completed));
    const completedDecision = completed.terminal_decision as {
      lease_fence?: unknown;
      next_lease?: {status?: unknown};
    };
    assert.equal(completedDecision.lease_fence, "required");
    assert.equal(completedDecision.next_lease?.status, "released");
    const superseded = await executeCoordinationTodoTerminalLifecycle(store, {
      ...common,
      todo_id: fixture.supersede_todo_id,
      command: "supersede",
      actor_agent_id: "agent-b",
      lease_idempotency_key: fixture.supersede_lease_idempotency_key,
      lease_expected_version: fixture.supersede_lease_expected_version,
      operation_id: "supersede-production-scale",
      requested_no_followup: false,
      validation_declaration: null,
      validation_receipt: null,
      reason: "synthetic replacement owns the next action",
      now: new Date("2026-09-07T07:01:00Z"),
    });
    assert.equal(superseded.status, "applied", JSON.stringify(superseded));
    const supersededDecision = superseded.terminal_decision as {
      lease_fence?: unknown;
      next_lease?: {status?: unknown};
    };
    assert.equal(supersededDecision.lease_fence, "required");
    assert.equal(supersededDecision.next_lease?.status, "released");
    const agentArchive = await executeCoordinationTodoArchiveCompleted(store, {
      goal_id: goalId,
      role: "agent",
      max_active_done: 5,
      operation_id: "archive-production-scale-agent",
      dry_run: false,
      now: new Date("2026-09-07T07:02:00Z"),
    });
    assert.equal(agentArchive.status, "applied", JSON.stringify(agentArchive));
    assert.equal(
      agentArchive.moved_count,
      fixture.expected_agent_archive_count_after_terminals,
    );
    const userArchive = await executeCoordinationTodoArchiveCompleted(store, {
      goal_id: goalId,
      role: "user",
      max_active_done: 5,
      operation_id: "archive-production-scale-user",
      dry_run: false,
      now: new Date("2026-09-07T07:03:00Z"),
    });
    assert.equal(userArchive.status, "applied", JSON.stringify(userArchive));
    assert.equal(userArchive.moved_count, fixture.expected_user_archive_count);
    assert.equal(
      userArchive.retained_standing_decision_count,
      fixture.expected_standing_user_decision_count,
    );
    const loaded = await store.loadAuthority();
    assert.equal(loaded.status, "loaded");
    if (loaded.status !== "loaded") return;
    const todos = loaded.head.todos as Record<string, unknown>[];
    const leases = loaded.head.leases as Record<string, unknown>[];
    const standing = projectStandingDecisions(todos)!;
    assert.equal(standing.active_count, 1); // Four approvals, one scope/owner.
    assert.equal(
      standing.inactive_count,
      fixture.expected_inactive_standing_decision_count,
      "a recorded rejection stays a standing receipt without becoming authority",
    );
    for (const entry of standing.entries as JsonObject[]) {
      assert.equal(entry.active, entry.outcome === "approve",
        "only an explicit approval may activate a standing decision");
    }
    assert.equal(standing.conflict_count, undefined);
    assert.equal(todos.length, fixture.expected_initial_todo_count);
    assert.equal(leases.length, fixture.expected_current_lease_count);
    assert.equal(
      todos.filter((todo) => todo.archive_state === "active").length,
      fixture.expected_initial_todo_count -
        fixture.expected_agent_archive_count_after_terminals -
        fixture.expected_user_archive_count,
    );
  });

  for (const native of [false, true]) {
    test(`${providerName} conformance: native Todo create is atomic and replayable (${native ? "native" : "v0"})`, async (t) => {
      const {store, contender} = await factory(t);
      const goalId = "goal-claim";
      const projection = todoClaimProjection(goalId, native);
      const initialized = await store.commitAuthority({
        expected_provider_revision: null, operation_id: "init-create",
        events: [], receipts: [], next_projection: projection,
      });
      assert.equal(initialized.status, "applied");
      if (initialized.status !== "applied") return;
      const todo = {
        schema_version: TODO_DOMAIN_ITEM_SCHEMA,
        todo_id: "todo-created",
        role: "agent",
        status: "open",
        done: false,
        text: "Create through the provider-neutral transaction",
        archive_state: "active",
        task_class: "advancement_task",
        action_kind: "implement",
        claimed_by: "agent-a",
      };
      const request = {
        goal_id: goalId, todo, actor_agent_id: "agent-a",
        registered_agents: ["agent-a", "agent-b"], operation_id: "create-todo",
        dry_run: false, now: new Date("2026-09-05T06:00:00Z"),
      };
      const preview = await executeCoordinationTodoCreate(store, {...request, dry_run: true});
      assert.equal(preview.status, "planned");
      assert.equal((await store.loadAuthority()).status, "loaded");
      const [first, second] = await Promise.all([
        executeCoordinationTodoCreate(store, request),
        executeCoordinationTodoCreate(contender, {...request,
          operation_id: "create-todo-contender", todo: {...todo, text: "Competing create"}}),
      ]);
      assert.deepEqual(
        [first.status, second.status].sort((left, right) =>
          String(left).localeCompare(String(right))
        ),
        ["applied", "conflict"],
      );
      const applied = first.status === "applied" ? first : second;
      const replayRequest = first.status === "applied" ? request : {...request,
        operation_id: "create-todo-contender", todo: {...todo, text: "Competing create"}};
      assert.equal(applied.todo_id, todo.todo_id);
      assert.equal(applied.projection_delivery, "pending");
      assert.equal(applied.projection_source, "committed_authority_journal");
      const replayedCreate = await executeCoordinationTodoCreate(store, replayRequest);
      assert.equal(replayedCreate.status, "replayed");
      assert.equal(replayedCreate.projection_delivery, "pending");
      assert.equal((await executeCoordinationTodoCreate(store, {...replayRequest,
        todo: {...replayRequest.todo, note: "different intent"}})).status, "failed");
      assert.equal((await executeCoordinationTodoCreate(store, {...replayRequest,
        operation_id: "semantic-duplicate", todo: {...replayRequest.todo, todo_id: "todo-other"}})).status,
      "no_change");
      const conflictingDuplicate = await executeCoordinationTodoCreate(store, {...replayRequest,
        operation_id: "semantic-duplicate-conflict", todo: {...replayRequest.todo,
          todo_id: "todo-other", task_class: "continuous_monitor"}});
      assert.equal(conflictingDuplicate.status, "failed");
      assert.equal(conflictingDuplicate.reason_code, "todo_semantic_duplicate_conflict");
      const deferredSameText = {
        ...replayRequest.todo,
        todo_id: "todo-deferred-terminal",
        status: "deferred",
        done: true,
        text: "Repeat terminal work",
        ...(native ? {} : {schema_version: "todo_item_v0", source_section: "Agent Todo"}),
      };
      const loadedBeforeDeferred = await store.loadAuthority();
      assert.equal(loadedBeforeDeferred.status, "loaded");
      if (loadedBeforeDeferred.status !== "loaded") return;
      const deferredCommit = prepareCoordinationProjectionCommit({
        goal_id: goalId,
        operation_id: "seed-deferred-terminal",
        expected_provider_revision: loadedBeforeDeferred.provider_revision,
        projection: loadedBeforeDeferred.head,
        mutations: [{kind: "todo_upsert", todo: deferredSameText}],
      });
      assert.equal((await store.commitAuthority(deferredCommit)).status, "applied");
      const recreatedAfterDeferred = await executeCoordinationTodoCreate(store, {
        ...replayRequest,
        operation_id: "create-after-deferred-terminal",
        todo: {...replayRequest.todo, todo_id: "todo-after-deferred-terminal",
          text: "Repeat terminal work"},
      });
      assert.equal(recreatedAfterDeferred.status, "applied");
      const createdDeferred = await executeCoordinationTodoCreate(store, {
        ...replayRequest,
        operation_id: "create-deferred",
        todo: {...replayRequest.todo, todo_id: "todo-new-deferred",
          text: "Wait for the external dependency", status: "deferred", done: true,
          resume_when: "material_change"},
      });
      assert.equal(createdDeferred.status, "applied");
      const inconsistentTerminal = await executeCoordinationTodoCreate(store, {
        ...replayRequest,
        operation_id: "reject-inconsistent-terminal",
        todo: {...replayRequest.todo, todo_id: "todo-inconsistent-terminal",
          status: "done", done: false},
      });
      assert.equal(inconsistentTerminal.status, "failed");
      assert.equal(inconsistentTerminal.reason_code, "invalid_coordination_todo_create");
      const loaded = await store.loadAuthority();
      assert.equal(loaded.status, "loaded");
      if (loaded.status !== "loaded") return;
      const created = (loaded.head.todos as Record<string, unknown>[])
        .find((item) => item.todo_id === todo.todo_id)!;
      assert.equal(created.created_by, "agent-a");
      assert.equal(created.last_actor_agent_id, "agent-a");
      assert.equal(created.updated_at, "2026-09-05T06:00:00Z");
      assert.equal((loaded.head.todo_read_model as Record<string, unknown>).todo_count, 5);
      for (const invalid of [
        {...request, operation_id: "bad-actor", actor_agent_id: "agent-b"},
        {...request, operation_id: "bad-status", todo: {...todo, todo_id: "todo-done", status: "done", done: true}},
        {...request, operation_id: "bad-projection", todo: {...todo, todo_id: "todo-projection", source_section: "Agent Todo"}},
      ]) assert.equal((await executeCoordinationTodoCreate(store, invalid)).status, "failed");
    });
    test(`${providerName} conformance: Todo claim atomically acquires canonical ownership (${native ? "native" : "v0"})`, async (t) => {
      const {store} = await factory(t);
      const goalId = "goal-atomic-ownership";
      const projection = {
        ...todoClaimProjection(goalId, native),
        handoff_mode: "hard_lease",
      };
      assert.equal((await store.commitAuthority({
        expected_provider_revision: null,
        operation_id: "seed-atomic-ownership",
        events: [],
        receipts: [],
        next_projection: projection,
      })).status, "applied");
      const request = {
        goal_id: goalId,
        todo_id: "todo-claim",
        claimed_by: "agent-a",
        actor_agent_id: "agent-a",
        expected_role: "agent",
        registered_agents: ["agent-a", "agent-b"],
        operation_id: "claim-and-acquire",
        lease_request: {
          idempotency_key: "turn:atomic-ownership",
          expected_version: 0,
          ttl_seconds: 2_700,
        },
        dry_run: false,
        now: new Date("2026-09-05T04:30:00Z"),
      };
      const preview = await executeCoordinationTodoClaim(store, {...request, dry_run: true});
      assert.equal(preview.status, "planned");
      assert.equal(preview.todo_changed, true);
      assert.equal(preview.lease_changed, true);
      assert.equal((await store.readReceipt(request.operation_id)).status, "missing");

      const applied = await executeCoordinationTodoClaim(store, request);
      assert.equal(applied.status, "applied", JSON.stringify(applied));
      assert.equal(applied.todo_changed, true);
      assert.equal(applied.lease_changed, true);
      const loaded = await store.loadAuthority();
      assert.equal(loaded.status, "loaded");
      if (loaded.status !== "loaded") return;
      const claimedTodo = (loaded.head.todos as Record<string, unknown>[])[0]!;
      const lease = (loaded.head.leases as Record<string, unknown>[])[0]!;
      assert.equal(claimedTodo.claimed_by, "agent-a");
      assert.equal(lease.owner, "agent-a");
      assert.equal(lease.idempotency_key, "turn:atomic-ownership");
      assert.deepEqual(lease.write_scopes, ["loopx/control_plane/**"]);
      assert.equal((await executeCoordinationTodoClaim(store, request)).status, "replayed");

      const idempotent = await executeCoordinationTodoClaim(store, {
        ...request,
        operation_id: "claim-and-acquire-idempotent",
        lease_request: {...request.lease_request, expected_version: 1},
      });
      assert.equal(idempotent.status, "no_change", JSON.stringify(idempotent));
      assert.equal(idempotent.todo_changed, false);
      assert.equal(idempotent.lease_changed, false);
      assert.equal(idempotent.lease_idempotent, true);
      const afterIdempotent = await store.loadAuthority();
      assert.equal(afterIdempotent.status, "loaded");
      if (afterIdempotent.status !== "loaded") return;
      assert.deepEqual(afterIdempotent.head, loaded.head);
      assert.equal((await store.readReceipt("claim-and-acquire-idempotent")).status, "found");
    });
    test(`${providerName} conformance: competing ownership transactions cannot split claim and lease (${native ? "native" : "v0"})`, async (t) => {
      const {store, contender} = await factory(t);
      const goalId = "goal-competing-ownership";
      const projection = {
        ...todoClaimProjection(goalId, native),
        handoff_mode: "hard_lease",
      };
      assert.equal((await store.commitAuthority({
        expected_provider_revision: null,
        operation_id: "seed-competing-ownership",
        events: [],
        receipts: [],
        next_projection: projection,
      })).status, "applied");
      const request = (owner: "agent-a" | "agent-b") => ({
        goal_id: goalId,
        todo_id: "todo-claim",
        claimed_by: owner,
        actor_agent_id: owner,
        expected_role: "agent",
        registered_agents: ["agent-a", "agent-b"],
        operation_id: `claim-and-acquire:${owner}`,
        lease_request: {
          idempotency_key: `turn:competing-ownership:${owner}`,
          expected_version: 0,
          ttl_seconds: 2_700,
        },
        dry_run: false,
        now: new Date("2026-09-05T04:30:00Z"),
      });

      // Promise.all alone does not guarantee a CAS race: a late reader may
      // correctly reject the already-claimed Todo before reaching commit.
      // Hold the first two real reads so both transactions see the same head.
      let releaseReaders: () => void = () => {
        throw new Error("reader barrier was not initialized");
      };
      const ready = new Promise<void>((resolve) => { releaseReaders = resolve; });
      let readers = 0;
      const originals = [store, contender].map((backend) => {
        const load = backend.loadAuthority.bind(backend);
        backend.loadAuthority = async () => {
          backend.loadAuthority = load;
          const snapshot = await load();
          if (++readers === 2) releaseReaders();
          await ready;
          return snapshot;
        };
        return load;
      });
      const results = await Promise.all([
        executeCoordinationTodoClaim(store, request("agent-a")),
        executeCoordinationTodoClaim(contender, request("agent-b")),
      ]).finally(() => {
        [store, contender].forEach((backend, index) => {
          backend.loadAuthority = originals[index]!;
        });
      });
      assert.deepEqual(
        results.map((result) => result.status).sort(),
        ["applied", "conflict"],
      );
      const winnerIndex = results.findIndex((result) => result.status === "applied");
      assert.notEqual(winnerIndex, -1);
      const winner = winnerIndex === 0 ? "agent-a" : "agent-b";
      const loser = winner === "agent-a" ? "agent-b" : "agent-a";
      const loaded = await store.loadAuthority();
      assert.equal(loaded.status, "loaded");
      if (loaded.status !== "loaded") return;
      const claimedTodo = (loaded.head.todos as Record<string, unknown>[])[0]!;
      const leases = loaded.head.leases as Record<string, unknown>[];
      assert.equal(claimedTodo.claimed_by, winner);
      assert.equal(leases.length, 1);
      assert.equal(leases[0]!.owner, winner);
      assert.equal(leases[0]!.idempotency_key, `turn:competing-ownership:${winner}`);
      assert.equal((await store.readReceipt(`claim-and-acquire:${winner}`)).status, "found");
      assert.equal((await store.readReceipt(`claim-and-acquire:${loser}`)).status, "missing");
      assert.equal((await executeCoordinationTodoClaim(store, request(winner))).status, "replayed");
      const rejected = await executeCoordinationTodoClaim(store, request(loser));
      assert.equal(rejected.status, "failed");
      assert.equal(rejected.reason_code, "claim_owner_mismatch");
      assert.deepEqual(await store.loadAuthority(), loaded);
    });
    test(`${providerName} conformance: lease-fenced text/note update (${native ? "native" : "v0"})`, async (t) => {
      const {store, contender} = await factory(t);
      const goalId = "goal-claim";
      const projection = {...todoClaimProjection(goalId, native), handoff_mode: "hard_lease"};
      await store.commitAuthority({operation_id: "seed-update", expected_provider_revision: null,
        next_projection: projection, events: [], receipts: []});
      const initial = await store.loadAuthority();
      assert.equal(initial.status, "loaded");
      if (initial.status !== "loaded") return;
      const todo = (initial.head.todos as Record<string, unknown>[])[0]!;
      const lease = {todo_id: "todo-claim", owner: "agent-a", status: "active",
        idempotency_key: "execution-a", version: 4, lease_epoch: 2,
        expires_at: "2026-09-05T06:00:00Z"};
      await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goalId,
        operation_id: "seed-lease", expected_provider_revision: initial.provider_revision,
        projection: initial.head, mutations: [
          {kind: "todo_upsert", todo: {...todo, claimed_by: "agent-a"}},
          {kind: "lease_upsert", lease},
        ]}));
      const request = {goal_id: goalId, todo_id: "todo-claim", expected_role: "agent",
        actor_agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"],
        operation_id: "leased-edit", patch: {text: "Correct leased task", note: "Correction"},
        clear_fields: [], dry_run: false, now: new Date("2026-09-05T05:00:00Z"),
        lease_idempotency_key: "execution-a", lease_expected_version: 4};
      const before = await store.loadAuthority();
      for (const invalid of [
        {...request, actor_agent_id: "agent-b"},
        {...request, lease_idempotency_key: "old-execution"},
        {...request, lease_expected_version: 3},
        {...request, lease_idempotency_key: null, lease_expected_version: null},
        {...request, now: new Date("2026-09-05T06:00:00Z")},
      ]) {
        assert.equal((await executeCoordinationTodoUpdate(store, invalid)).status, "failed");
        assert.deepEqual(await store.loadAuthority(), before);
        assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
      }
      assert.equal((await executeCoordinationTodoUpdate(store, {...request, dry_run: true})).status, "planned");
      assert.deepEqual(await store.loadAuthority(), before);
      assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "applied");
      const after = await store.loadAuthority();
      assert.equal(after.status, "loaded");
      if (after.status !== "loaded") return;
      assert.deepEqual(after.head.leases, before.status === "loaded" ? before.head.leases : []);
      assert.equal((after.head.todos as Record<string, unknown>[])[0]!.note, "Correction");
      assert.equal((await executeCoordinationTodoUpdate(contender, {...request,
        now: new Date("2026-09-06T05:00:00Z")})).status, "replayed");
      for (const changed of [{patch: {text: "Different"}}, {lease_expected_version: 5},
        {lease_idempotency_key: "different"}]) {
        assert.equal((await executeCoordinationTodoUpdate(store, {...request, ...changed})).reason_code,
          "coordination_operation_identity_mismatch");
      }
      assert.deepEqual(await store.loadAuthority(), after);
      for (const fault of ["lost_response", "lease_transfer"] as const) {
        const fencedRequest = {...request, operation_id: fault, patch: {note: fault}};
        const intercepted: AuthorityStore = {
          storeIdentity: () => store.storeIdentity(), loadAuthority: () => store.loadAuthority(),
          readReceipt: (id) => store.readReceipt(id),
          scanCommitted: (cursor, limit) => store.scanCommitted(cursor, limit),
          commitAuthority: async (commit) => {
            if (fault === "lease_transfer") {
              const current = await contender.loadAuthority();
              assert.equal(current.status, "loaded");
              if (current.status !== "loaded") throw new Error("missing head");
              await contender.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goalId,
                operation_id: "transfer", expected_provider_revision: current.provider_revision,
                projection: current.head, mutations: [{kind: "lease_upsert",
                  lease: {...lease, owner: "agent-b", version: 5, lease_epoch: 3}}]}));
              return store.commitAuthority(commit);
            }
            assert.equal((await store.commitAuthority(commit)).status, "applied");
            return {status: "ambiguous", reason_code: "lost_response", reason: "response lost"};
          },
        };
        const outcome = await executeCoordinationTodoUpdate(intercepted, fencedRequest);
        assert.equal(outcome.status, fault === "lost_response" ? "recovered" : "conflict");
        assert.equal((await store.readReceipt(fault)).status,
          fault === "lost_response" ? "found" : "missing");
      }
      const transferred = await store.loadAuthority();
      assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "replayed");
      assert.equal((await executeCoordinationTodoUpdate(store, {...request,
        operation_id: "stale-after-transfer"})).status, "failed");
      assert.deepEqual(await store.loadAuthority(), transferred);
    });
    for (const fault of ["lease_replaced", "lost_response"] as const) {
      test(`${providerName} conformance: hard-lease claim ${fault} (${native ? "native" : "v0"})`, async (t) => {
        const {store, contender} = await factory(t);
        const goalId = "goal-claim";
        const projection = {...todoClaimProjection(goalId, native), handoff_mode: "hard_lease"};
        assert.equal((await store.commitAuthority({
          operation_id: "seed-hard-lease", expected_provider_revision: null,
          next_projection: projection, events: [], receipts: [],
        })).status, "applied");
        const request = {
          goal_id: goalId, todo_id: "todo-claim", claimed_by: "agent-a",
          actor_agent_id: "agent-a", expected_role: "agent",
          registered_agents: ["agent-a", "agent-b"], operation_id: "claim-hard-lease",
          dry_run: false, now: new Date("2026-09-05T04:30:00Z"),
        };
        // Claim does not mint execution authority: an absent lease rejects
        // without consuming the operation id or changing the provider head.
        const before = await store.loadAuthority();
        assert.equal((await executeCoordinationTodoClaim(store, request)).reason_code,
          "handoff_mode_requires_lease");
        assert.deepEqual(await store.loadAuthority(), before);
        assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
        assert.equal(before.status, "loaded");
        if (before.status !== "loaded") return;
        const lease = {todo_id: request.todo_id, owner: "agent-a", status: "active",
          lease_epoch: 1, expires_at: "2026-09-05T05:00:00Z"};
        assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({
          goal_id: goalId, operation_id: "acquire-canonical-lease",
          expected_provider_revision: before.provider_revision, projection: before.head,
          mutations: [{kind: "lease_upsert", lease}],
        }))).status, "applied");
        const leased = await store.loadAuthority();
        assert.equal(leased.status, "loaded");
        if (leased.status !== "loaded") return;
        const intercepted: AuthorityStore = {
          storeIdentity: () => store.storeIdentity(),
          loadAuthority: () => store.loadAuthority(),
          readReceipt: (id) => store.readReceipt(id),
          scanCommitted: (cursor, limit) => store.scanCommitted(cursor, limit),
          commitAuthority: async (commit) => {
            if (fault === "lease_replaced") {
              assert.equal((await contender.commitAuthority(prepareCoordinationProjectionCommit({
                goal_id: goalId, operation_id: "replace-lease-before-claim-commit",
                expected_provider_revision: leased.provider_revision, projection: leased.head,
                mutations: [{kind: "lease_upsert", lease: {...lease, owner: "agent-b", lease_epoch: 2}}],
              }))).status, "applied");
              return store.commitAuthority(commit);
            }
            assert.equal((await store.commitAuthority(commit)).status, "applied");
            return {status: "ambiguous", reason_code: "lost_response",
              reason: "commit response lost after persistence"};
          },
        };
        const result = await executeCoordinationTodoClaim(intercepted, request);
        assert.equal(result.status, fault === "lease_replaced" ? "conflict" : "recovered");
        assert.equal((await store.readReceipt(request.operation_id)).status,
          fault === "lease_replaced" ? "missing" : "found");
        const after = await store.loadAuthority();
        assert.equal(after.status, "loaded");
        if (after.status !== "loaded") return;
        const todo = (after.head.todos as Record<string, unknown>[])[0]!;
        if (fault === "lease_replaced") {
          assert.equal(todo.claimed_by, undefined);
          assert.deepEqual(after.head.leases, [{...lease, owner: "agent-b", lease_epoch: 2}]);
          assert.equal((await executeCoordinationTodoClaim(store, request)).reason_code,
            "handoff_mode_requires_lease");
        } else {
          assert.equal(todo.claimed_by, "agent-a");
          assert.deepEqual(after.head.leases, [lease]);
          // Historical acceptance is retry evidence, not a fresh grant after expiry.
          const expiredRequest = {...request, now: new Date("2026-09-05T06:00:00Z")};
          assert.equal((await executeCoordinationTodoClaim(store, expiredRequest)).status, "replayed");
          assert.equal((await executeCoordinationTodoClaim(store,
            {...expiredRequest, operation_id: "fresh-expired-claim"})).reason_code,
          "handoff_mode_requires_lease");
          assert.equal((await executeCoordinationTodoClaim(store,
            {...request, claimed_by: "agent-b", actor_agent_id: "agent-b"})).reason_code,
          "coordination_operation_identity_mismatch");
        }
        assert.deepEqual(await store.loadAuthority(), after);
      });
    }
    test(`${providerName} conformance: provider-neutral Todo update is atomic and replayable (${native ? "native" : "v0"})`, async (t) => {
      const {store, contender} = await factory(t);
      const goalId = "goal-claim";
      const initialized = await store.commitAuthority({
        expected_provider_revision: null, operation_id: "init-update",
        events: [], receipts: [], next_projection: todoClaimProjection(goalId, native),
      });
      assert.equal(initialized.status, "applied");
      const correction = {goal_id: goalId, todo_id: "todo-claim", expected_role: "agent",
        actor_agent_id: "agent-b", registered_agents: ["agent-a", "agent-b"],
        operation_id: "correct-unclaimed", patch: {text: "Correct unclaimed copy", note: "Correct note"},
        clear_fields: [], dry_run: false, now: new Date("2026-09-05T05:00:00Z")};
      assert.equal((await executeCoordinationTodoUpdate(store, correction)).status, "applied");
      assert.equal((await executeCoordinationTodoUpdate(contender, correction)).status, "replayed");
      const corrected = await store.loadAuthority();
      assert.equal(corrected.status, "loaded");
      if (corrected.status !== "loaded") return;
      const correctedTodo = (corrected.head.todos as Record<string, unknown>[])[0]!;
      assert.equal(correctedTodo.claimed_by, undefined);
      assert.equal(correctedTodo.note, "Correct note");
      assert.equal(correctedTodo.last_actor_agent_id, "agent-b");
      assert.equal((await executeCoordinationTodoClaim(store, {
        goal_id: goalId, todo_id: "todo-claim", claimed_by: "agent-a",
        actor_agent_id: "agent-a", expected_role: "agent",
        registered_agents: ["agent-a", "agent-b"], operation_id: "claim-before-update",
        dry_run: false, now: new Date("2026-09-05T05:15:00Z"),
      })).status, "applied");
      assert.equal((await executeCoordinationTodoUpdate(store, {...correction,
        operation_id: "correct-after-another-agent-claims"})).reason_code, "update_owner_mismatch");
      const request = {goal_id: goalId, todo_id: "todo-claim", expected_role: "agent",
        actor_agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"],
        operation_id: "update-native", patch: {text: "Updated provider-neutrally"},
        clear_fields: ["note"], dry_run: false,
        now: new Date("2026-09-05T05:30:00Z")};
      const preview = await executeCoordinationTodoUpdate(store, {...request, dry_run: true});
      assert.equal(preview.status, "planned");
      const [first, second] = await Promise.all([
        executeCoordinationTodoUpdate(store, request),
        executeCoordinationTodoUpdate(contender, {...request, operation_id: "update-contender",
          patch: {text: "Concurrent update"}}),
      ]);
      assert.deepEqual([String(first.status), String(second.status)].sort(
        (left, right) => left.localeCompare(right)),
        ["applied", "conflict"]);
      const appliedRequest = first.status === "applied" ? request : {...request,
        operation_id: "update-contender", patch: {text: "Concurrent update"}};
      assert.equal((await executeCoordinationTodoUpdate(store, appliedRequest)).status, "replayed");
      assert.equal((await executeCoordinationTodoUpdate(store, {...appliedRequest,
        patch: {text: "Changed intent"}})).reason_code,
      "coordination_operation_identity_mismatch");
      const loaded = await store.loadAuthority();
      assert.equal(loaded.status, "loaded");
      if (loaded.status !== "loaded") return;
      const updated = (loaded.head.todos as Record<string, unknown>[])[0]!;
      assert.equal(updated.text, appliedRequest.patch.text);
      assert.equal(updated.note, undefined);
      assert.equal(updated.claimed_by, "agent-a");
      assert.equal(updated.last_actor_agent_id, "agent-a");
      assert.equal((loaded.head.todo_read_model as Record<string, unknown>).todo_count, 1);
      for (const patch of [{excluded_agents: ["agent-b"]},
        {required_capabilities: ["network"]}, {continuation_policy: "no_followup"}]) {
        assert.equal((await executeCoordinationTodoUpdate(store, {...request,
          operation_id: `reject-patch-${Object.keys(patch)[0]}`, patch,
          clear_fields: []})).reason_code, "invalid_coordination_todo_update");
      }
      for (const field of ["excluded_agents", "required_capabilities", "continuation_policy"]) {
        assert.equal((await executeCoordinationTodoUpdate(store, {...request,
          operation_id: `reject-clear-${field}`, patch: {}, clear_fields: [field]})).reason_code,
        "invalid_coordination_todo_update");
      }
    });
    test(`${providerName} conformance: provider-neutral Todo claim transaction (${native ? "native" : "v0"})`, async (t) => {
      const { store } = await factory(t);
      const goalId = "goal-claim";
      const initialized = await store.commitAuthority({
        expected_provider_revision: null,
        operation_id: "initialize-claim",
        events: [{ schema_version: "loopx_authority_event_v0", type: "promoted" }],
        next_projection: todoClaimProjection(goalId, native),
        receipts: [],
      });
      assert.equal(initialized.status, "applied");

      const request = {
        goal_id: goalId,
        todo_id: "todo-claim",
        claimed_by: "agent-a",
        actor_agent_id: "agent-a",
        expected_role: "agent",
        registered_agents: ["agent-a", "agent-b"],
        operation_id: "claim-todo",
        dry_run: false,
        now: new Date("2026-09-05T04:30:00Z"),
      };
      const claimed = await executeCoordinationTodoClaim(store, request);
      assert.equal(claimed.status, "applied", JSON.stringify(claimed));
      assert.equal(claimed.projection_delivery, "pending");
      assert.equal(claimed.projection_source, "committed_authority_journal");

      const loaded = await store.loadAuthority();
      assert.equal(loaded.status, "loaded");
      if (loaded.status !== "loaded") return;
      const todo = (loaded.head.todos as Record<string, unknown>[])[0];
      assert.equal(todo?.claimed_by, "agent-a");
      assert.equal(todo?.note, "preserve complete canonical record");
      assert.equal((await store.readReceipt("claim-todo")).status, "found");
      const noChangeRequest = {...request, operation_id: "claim-already-owned"};
      const noChange = await executeCoordinationTodoClaim(store, noChangeRequest);
      assert.equal(noChange.status, "no_change", JSON.stringify(noChange));
      assert.equal(noChange.changed, false);
      const afterNoChange = await store.loadAuthority();
      assert.equal(afterNoChange.status, "loaded");
      if (afterNoChange.status !== "loaded") return;
      assert.deepEqual(afterNoChange.head, loaded.head);
      assert.notEqual(afterNoChange.provider_revision, loaded.provider_revision);
      assert.equal((await store.readReceipt(noChangeRequest.operation_id)).status, "found");
      const replayed = await executeCoordinationTodoClaim(store,
        {...noChangeRequest, registered_agents: []});
      assert.deepEqual(replayed, {...noChange, status: "replayed"});
      assert.deepEqual(await store.loadAuthority(), afterNoChange);
      const cleared = await store.commitAuthority(prepareCoordinationProjectionCommit({
        goal_id: goalId, operation_id: "clear-after-no-change",
        expected_provider_revision: afterNoChange.provider_revision,
        projection: afterNoChange.head,
        mutations: [{kind: "todo_upsert", todo: {...todo, claimed_by: null}}],
      }));
      assert.equal(cleared.status, "applied");
      const afterClear = await store.loadAuthority();
      assert.deepEqual(await executeCoordinationTodoClaim(store,
        {...noChangeRequest, registered_agents: []}), {...noChange, status: "replayed"});
      assert.deepEqual(await store.loadAuthority(), afterClear);
    });
  }
}
