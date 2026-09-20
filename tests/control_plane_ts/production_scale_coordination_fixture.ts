import {readFileSync} from "node:fs";

import {authorityUnicodeCompare, canonicalAuthoritySha256} from
  "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {
  TODO_ITEM_SCHEMA,
} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {
  authorityProjectionFixture,
  projectionFixtureAsSchema,
  type AuthorityProjectionSchema,
} from "./authority_projection_fixture.ts";

const envelope = JSON.parse(readFileSync(new URL(
  "../fixtures/control_plane/coordination_production_scale_v0.json",
  import.meta.url,
), "utf8")) as {
  schema_version: string;
  agent_status_counts: Record<string, number>;
  user_status_counts: Record<string, number>;
  agent_status_order: string[];
  user_status_order: string[];
  current_lease_count: number;
  retired_lease_count: number;
  standing_user_decision_count: number;
  rejected_standing_decision_count: number;
  scoped_without_outcome_count: number;
  linked_decision_count: number;
  completion_target_index: number;
  supersede_target_index: number;
  history: {
    commit_count: number;
    parity_commit_count: number;
    scan_page_size: number;
    expected_scan_pages: number;
    observation_source: string;
    projection_scope: {agent_todos: number; user_todos: number; leases: number};
  };
  provider_matrix: {
    default: "file";
    local_profiles: string[];
    service_profiles: string[];
    service_requires_factory: true;
  };
  lease_lifecycle: {owner: string; receiver: string; execution_key: string;
    receiver_execution_key: string; version: number; lease_epoch: number; now: string};
  lease_acquisition: {execution_key: string; next_execution_key: string; write_scopes: string[]; ttl_seconds: number;
    conflict_todo_id: string; conflict_write_scopes: string[]};
  semantic_cases: Record<string, Record<string, unknown>>;
  presentation_cases: Record<string, Record<string, unknown>>;
  update_cases: Record<string, Record<string, unknown>>;
};

export const PRODUCTION_SCALE_FIXTURE_SCHEMA =
  "loopx_coordination_production_scale_fixture_v0";

/**
 * One long retained history over the production-scale projection.
 *
 * The projection dimension proves that one transaction reduces a full
 * production-scale Todo/lease state. This dimension keeps that same state and
 * appends a deterministic observation history on top of it, so a provider is
 * exercised with many retained transactions instead of a handful. It stays
 * provider-neutral on purpose: no field here names a storage window, page
 * layout or checkpoint interval, because those belong to the provider.
 */
export interface ProductionScaleHistoryPlan {
  readonly commit_count: number;
  /**
   * Cross-provider prefix. The journal providers rewrite their complete
   * retained document on every commit, so the parity smoke stays on a bounded
   * prefix while the embedded provider runs the full plan.
   */
  readonly parity_commit_count: number;
  readonly scan_page_size: number;
  readonly expected_scan_pages: number;
  readonly observation_source: "continuous_monitor";
  /**
   * Record counts of the retained-history projection.
   *
   * The history projection keeps the production-scale record families and
   * per-record shapes, and bounds how many records one commit has to reduce.
   * Reducing the full production-scale projection costs roughly half a second
   * per domain mutation, which would make hundreds of retained transactions a
   * multi-minute smoke. Projection scale stays covered by the single-transaction
   * conformance and parity cases over the full fixture.
   */
  readonly projection_scope: {readonly agent_todos: number; readonly user_todos: number;
    readonly leases: number};
}
export const PRODUCTION_SCALE_VALIDATION_DECLARATION = {
  validation_command: null,
  validation_command_argv: ["python3", "-c", "raise SystemExit(0)"],
  validation_label: "production-scale fixture validation",
  validation_timeout_seconds: 5,
};

export interface ProductionScaleCoordinationFixture {
  readonly projection: Record<string, unknown>;
  readonly registered_agents: readonly string[];
  readonly completion_todo_id: string;
  readonly supersede_todo_id: string;
  readonly completion_lease_idempotency_key: string;
  readonly completion_lease_expected_version: number;
  readonly supersede_lease_idempotency_key: string;
  readonly supersede_lease_expected_version: number;
  readonly expected_initial_todo_count: number;
  readonly expected_current_lease_count: number;
  readonly expected_agent_archive_count_after_terminals: number;
  readonly expected_user_archive_count: number;
  readonly expected_standing_user_decision_count: number;
  /**
   * Standing receipts whose recorded outcome is not `approve`.
   *
   * Authority collapses per decision identity, so every rejection sharing one
   * scope produces a single inactive entry rather than one per Todo.
   */
  readonly expected_inactive_standing_decision_count: number;
  readonly semantic_cases: Readonly<Record<string, Record<string, unknown>>>;
  readonly presentation_cases: Readonly<Record<string, Record<string, unknown>>>;
  readonly update_cases: Readonly<Record<string, Record<string, unknown>>>;
  readonly provider_matrix: Readonly<{
    default: "file";
    local_profiles: readonly string[];
    service_profiles: readonly string[];
    service_requires_factory: true;
  }>;
}

function statusSeries(
  counts: Record<string, number>,
  order: readonly string[],
  role: string,
): string[] {
  const keys = Object.keys(counts).sort();
  const orderedKeys = [...order].sort();
  if (keys.length !== orderedKeys.length || keys.some((key, index) => key !== orderedKeys[index])) {
    throw new Error(`${role} production fixture status order does not cover its counts`);
  }
  return order.flatMap(status => {
    const count = counts[status];
    if (!Number.isSafeInteger(count) || count < 0) {
      throw new Error(`${role} production fixture count is not a non-negative safe integer`);
    }
    return Array.from({length: count}, () => status);
  });
}

function todoId(role: "agent" | "user", index: number): string {
  return `todo_fixture_${role}_${String(index).padStart(3, "0")}`;
}

function observedAt(index: number): string {
  return new Date(Date.UTC(2025, 0, 1, 0, index)).toISOString().replace(/\.\d{3}Z$/u, "Z");
}

function todoRecords(
  goalId: string,
  role: "agent" | "user",
  counts: Record<string, number>,
  order: readonly string[],
): Record<string, unknown>[] {
  return statusSeries(counts, order, role).map((status, index) => {
    const done = status === "done" || status === "deferred";
    const record: Record<string, unknown> = {
      schema_version: TODO_ITEM_SCHEMA,
      todo_id: todoId(role, index),
      role,
      status,
      done,
      text: `Synthetic ${role} Todo ${String(index).padStart(3, "0")}`,
      archive_state: "active",
      source_section: role === "agent" ? "Agent Todo" : "User Todo",
      index: index + 1,
      task_class: role === "agent"
        ? index % 4 === 0 ? "continuous_monitor" : "advancement_task"
        : index % 3 === 0 ? "user_gate" : "user_action",
      ...(done ? {updated_at: observedAt(index), completed_at: observedAt(index)} : {}),
      ...(status === "deferred" ? {resume_when: "material_change"} : {}),
    };
    if (role === "agent" && status !== "done" && status !== "deferred") {
      record.claimed_by = index % 2 === 0 ? "agent-a" : "agent-b";
    }
    if (role === "agent" && record.task_class === "advancement_task") {
      // Full requirement declarations survive unrelated transitions and archive;
      // editing a declaration must not change an existing execution grant.
      Object.assign(record, {action_kind: "implement", task_domain: "code",
        task_repository: "git:github.com/example/project",
        required_write_scopes: ["src/**", "tests/**"], required_capabilities: ["code_review"],
        target_capabilities: ["delivery"], explore_result_node_refs: [`Node:fixture-${index}`]});
    }
    if (record.task_class === "continuous_monitor") {
      // Durable mixed-source observation shapes: bounded and watch-only,
      // untouched and previously changed, with cadence and retained generation.
      Object.assign(record, {target_key: `synthetic-watch-${index}`, cadence: "1h",
        last_checked_at: observedAt(index), next_due_at: "2025-02-01T00:00:00Z",
        result_hash: `synthetic-result-${index}`, material_change_generation: index % 3,
        consecutive_no_change: String(index % 5), material_change: String(index % 3 === 0),
        ...(index % 8 === 0 ? {watch_only: "true"} : {max_no_change_before_replan: "5"})});
    }
    if (role === "agent" && status === "done" && index < 3) {
      record.successor_todo_ids = [todoId("agent", envelope.completion_target_index + index)];
      record.completion_continuation = "successor";
    }
    if (role === "user" && index < envelope.standing_user_decision_count) {
      record.task_class = "user_gate";
      record.decision_scope = {kind: "direction", granularity: "goal", scope_key: goalId};
      record.decision_outcome = "approve";
      record.global_gate = true;
      record.goal_bound = true;
    }
    // Long-lived histories include scoped gates without an explicit outcome
    // and exact-action approvals. Neither is reusable standing authority.
    const partialEnd = envelope.standing_user_decision_count + envelope.scoped_without_outcome_count;
    if (role === "user" && index >= envelope.standing_user_decision_count &&
        index < partialEnd + envelope.linked_decision_count) {
      record.task_class = "user_gate";
      record.blocks_agent = "agent-a";
      record.decision_scope = {kind: "direction", granularity: "goal", scope_key: goalId};
      if (index >= partialEnd) {
        record.decision_outcome = "approve";
        record.unblocks_todo_id = todoId("agent", envelope.completion_target_index);
      }
    }
    // An explicit rejection is a recorded decision, not absent authority: the
    // same broad goal is refused under a second decision kind. It stays a
    // standing receipt while its outcome keeps it inactive, so a provider
    // cannot present "no active approval" as "no decision was made".
    const rejectedStart = partialEnd + envelope.linked_decision_count;
    if (role === "user" && index >= rejectedStart &&
        index < rejectedStart + envelope.rejected_standing_decision_count) {
      record.task_class = "user_gate";
      record.decision_scope = {kind: "write_scope", granularity: "goal", scope_key: goalId};
      record.decision_outcome = "reject";
      record.global_gate = true;
      record.goal_bound = true;
    }
    return record;
  });
}

export function productionScaleCoordinationFixture(
  goalId: string,
  schema: AuthorityProjectionSchema = "legacy",
): ProductionScaleCoordinationFixture {
  if (envelope.schema_version !== PRODUCTION_SCALE_FIXTURE_SCHEMA) {
    throw new Error("production-scale fixture envelope schema mismatch");
  }
  const agents = todoRecords(
    goalId, "agent", envelope.agent_status_counts, envelope.agent_status_order,
  );
  const users = todoRecords(
    goalId, "user", envelope.user_status_counts, envelope.user_status_order,
  );
  const archiveDependent = [...agents].reverse().find(item => item.status === "open")!;
  archiveDependent.task_class = "advancement_task";
  archiveDependent.resume_when = `todo_done:${todoId("agent", 3)}`;
  const completionTodo = agents[envelope.completion_target_index]!;
  const supersedeTodo = agents[envelope.supersede_target_index]!;
  completionTodo.task_class = "advancement_task";
  completionTodo.claimed_by = "agent-a";
  completionTodo.completion_validation_required = true;
  completionTodo.completion_validation_sha256 = canonicalAuthoritySha256(
    PRODUCTION_SCALE_VALIDATION_DECLARATION,
  );
  supersedeTodo.task_class = "advancement_task";
  supersedeTodo.claimed_by = "agent-b";
  const todos = [...agents, ...users]
    .sort((left, right) => authorityUnicodeCompare(String(left.todo_id), String(right.todo_id)));
  const leasedIds = [
    String(completionTodo.todo_id),
    String(supersedeTodo.todo_id),
    ...agents.map((todo) => String(todo.todo_id)),
  ]
    .filter((value, index, values) => values.indexOf(value) === index)
    .slice(0, envelope.current_lease_count);
  const leases = leasedIds.map((leasedTodoId, index) => ({
    schema_version: "task_lease_v0",
    goal_id: goalId,
    todo_id: leasedTodoId,
    owner: leasedTodoId === completionTodo.todo_id ? "agent-a" : "agent-b",
    idempotency_key: `fixture-lease-${index}`,
    write_scopes: ["loopx/control_plane/**"],
    version: index + 1,
    lease_epoch: index + 1,
    acquired_at: observedAt(index),
    updated_at: observedAt(index),
    expires_at: index < 2 ? "2027-01-01T00:00:00Z" : observedAt(index + 1),
    status: index < 2 ? "active" : "released",
  })).sort((left, right) => authorityUnicodeCompare(left.todo_id, right.todo_id));
  const completionLease = leases.find((lease) => lease.todo_id === completionTodo.todo_id)!;
  const supersedeLease = leases.find((lease) => lease.todo_id === supersedeTodo.todo_id)!;
  const legacyProjection = authorityProjectionFixture(
    goalId,
    todos as Record<string, unknown>[],
    leases as Record<string, unknown>[],
    "legacy",
    {source_authority: "synthetic_production_scale_fixture", handoff_mode: "hard_lease"},
  );
  const expectedAgentDone = agents.filter(todo => todo.status === "done").length;
  const expectedUserDone = users.filter(todo => todo.status === "done").length;
  const expectedStanding = users.filter(todo =>
    todo.task_class === "user_gate" && todo.decision_outcome === "approve" &&
    todo.global_gate === true && todo.goal_bound === true,
  ).length;
  const rejectedStanding = users.filter(todo =>
    todo.task_class === "user_gate" && todo.decision_outcome === "reject" &&
    todo.global_gate === true && todo.goal_bound === true,
  );
  const expectedInactiveStanding = new Set(rejectedStanding.map(todo => {
    const scope = todo.decision_scope as {kind: string; granularity: string; scope_key: string};
    return JSON.stringify([scope.kind, scope.granularity, scope.scope_key, "global"]);
  })).size;
  return {
    projection: schema === "legacy"
      ? legacyProjection
      : projectionFixtureAsSchema(legacyProjection, schema),
    registered_agents: ["agent-a", "agent-b"],
    completion_todo_id: String(completionTodo.todo_id),
    supersede_todo_id: String(supersedeTodo.todo_id),
    completion_lease_idempotency_key: completionLease.idempotency_key,
    completion_lease_expected_version: completionLease.version,
    supersede_lease_idempotency_key: supersedeLease.idempotency_key,
    supersede_lease_expected_version: supersedeLease.version,
    expected_initial_todo_count: todos.length,
    expected_current_lease_count: leases.length,
    expected_agent_archive_count_after_terminals: expectedAgentDone + 2 - 5,
    // Archive keeps every standing receipt, approved or rejected, so each
    // rejection leaves one fewer movable completed row behind.
    expected_user_archive_count: Math.min(
      expectedUserDone - expectedStanding - rejectedStanding.length,
      expectedUserDone - 5,
    ),
    expected_standing_user_decision_count: expectedStanding + rejectedStanding.length,
    expected_inactive_standing_decision_count: expectedInactiveStanding,
    semantic_cases: envelope.semantic_cases,
    presentation_cases: envelope.presentation_cases,
    update_cases: envelope.update_cases,
    provider_matrix: {
      default: envelope.provider_matrix.default,
      local_profiles: [...envelope.provider_matrix.local_profiles],
      service_profiles: [...envelope.provider_matrix.service_profiles],
      service_requires_factory: envelope.provider_matrix.service_requires_factory,
    },
  };
}

export const PRODUCTION_SCALE_RETIRED_LEASE_COUNT = envelope.retired_lease_count;
export const PRODUCTION_SCALE_REJECTED_DECISION_COUNT = envelope.rejected_standing_decision_count;

function requireSafeCount(value: number, label: string): number {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new Error(`production fixture ${label} is not a positive safe integer`);
  }
  return value;
}

/**
 * Checked while the module loads, so a drifted history envelope fails every
 * consumer instead of quietly shrinking a provider's retained history.
 */
export const PRODUCTION_SCALE_HISTORY: ProductionScaleHistoryPlan = (() => {
  const plan = envelope.history;
  if (plan === undefined || plan.observation_source !== "continuous_monitor") {
    throw new Error("production fixture history observation source is unsupported");
  }
  const commit_count = requireSafeCount(plan.commit_count, "history commit count");
  const scan_page_size = requireSafeCount(plan.scan_page_size, "history scan page size");
  const parity_commit_count = requireSafeCount(plan.parity_commit_count, "history parity commit count");
  if (parity_commit_count > commit_count) {
    throw new Error("production fixture history parity prefix exceeds its commit count");
  }
  if (plan.expected_scan_pages !== Math.ceil(commit_count / scan_page_size)) {
    throw new Error("production fixture history scan page count does not cover its commits");
  }
  const scope = plan.projection_scope;
  if (scope === undefined) throw new Error("production fixture history projection scope is missing");
  const projection_scope = {
    agent_todos: requireSafeCount(scope.agent_todos, "history agent Todos"),
    user_todos: requireSafeCount(scope.user_todos, "history user Todos"),
    leases: requireSafeCount(scope.leases, "history leases"),
  };
  if (projection_scope.agent_todos > envelope.agent_status_counts.done +
      envelope.agent_status_counts.open + envelope.agent_status_counts.blocked +
      envelope.agent_status_counts.deferred ||
      projection_scope.user_todos > envelope.user_status_counts.done +
      envelope.user_status_counts.open + envelope.user_status_counts.deferred ||
      projection_scope.leases > envelope.current_lease_count) {
    throw new Error("production fixture history projection scope exceeds its record families");
  }
  return {commit_count, parity_commit_count, scan_page_size, expected_scan_pages: plan.expected_scan_pages,
    observation_source: plan.observation_source, projection_scope};
})();

export interface ProductionScaleHistoryProjection {
  readonly projection: Record<string, unknown>;
  readonly todo_ids: readonly string[];
  readonly monitor_ids: readonly string[];
}

/**
 * The retained-history projection: production-scale record families and record
 * shapes at a size that keeps hundreds of retained transactions affordable.
 */
export function productionScaleHistoryProjection(
  goalId: string,
  schema: AuthorityProjectionSchema = "legacy",
): ProductionScaleHistoryProjection {
  const full = productionScaleCoordinationFixture(goalId, "legacy");
  const scope = PRODUCTION_SCALE_HISTORY.projection_scope;
  const allTodos = full.projection.todos as Record<string, unknown>[];
  const agentTodos = allTodos.filter(todo => todo.role === "agent");
  // Retained history is dominated by live observations, so the slice keeps the
  // open monitors first and fills the remaining record budget with the other
  // agent Todos in fixture order.
  const openMonitors = agentTodos.filter(todo =>
    todo.status === "open" && todo.task_class === "continuous_monitor");
  const todos = [...openMonitors, ...agentTodos.filter(todo => !openMonitors.includes(todo))]
    .slice(0, scope.agent_todos)
    .concat(allTodos.filter(todo => todo.role === "user").slice(0, scope.user_todos));
  const todoIds = new Set(todos.map(todo => String(todo.todo_id)));
  const leases = (full.projection.leases as Record<string, unknown>[])
    .filter(lease => todoIds.has(String(lease.todo_id))).slice(0, scope.leases);
  const projection = authorityProjectionFixture(goalId, todos, leases, "legacy",
    {source_authority: "synthetic_production_scale_history_fixture", handoff_mode: "hard_lease"});
  const monitor_ids = todos.filter(todo => todo.task_class === "continuous_monitor")
    .map(todo => String(todo.todo_id));
  if (monitor_ids.length === 0) {
    throw new Error("production fixture history projection needs monitor Todos");
  }
  return {projection: schema === "legacy" ? projection : projectionFixtureAsSchema(projection, schema),
    todo_ids: todos.map(todo => String(todo.todo_id)).sort(authorityUnicodeCompare),
    monitor_ids: monitor_ids.sort(authorityUnicodeCompare)};
}

export interface ProductionScaleObservationStep {
  readonly operation_id: string;
  readonly todo_id: string;
  readonly mutation: {kind: "todo_upsert"; todo: Record<string, unknown>};
}

/**
 * One deterministic monitor observation over an existing projection.
 *
 * Every step re-reads the current projection, so callers can apply the same
 * sequence to any provider and compare the resulting histories exactly. The
 * mutation is an ordinary domain update: no storage-specific field is touched.
 */
export function productionScaleObservationStep(
  projection: Record<string, unknown>,
  index: number,
): ProductionScaleObservationStep {
  if (!Number.isSafeInteger(index) || index < 0) {
    throw new Error("production fixture history step index is invalid");
  }
  const todos = projection.todos;
  if (!Array.isArray(todos)) throw new Error("production fixture history needs a projection");
  const monitors = (todos as Record<string, unknown>[])
    .filter(todo => todo.task_class === "continuous_monitor")
    .sort((left, right) => authorityUnicodeCompare(String(left.todo_id), String(right.todo_id)));
  if (monitors.length === 0) throw new Error("production fixture history needs monitor Todos");
  const monitor = monitors[index % monitors.length]!;
  const generation = Number(monitor.material_change_generation ?? 0) + (index % 3 === 0 ? 1 : 0);
  const todo: Record<string, unknown> = {...monitor, last_checked_at: observedAt(index),
    result_hash: `history-observation-${index}`, material_change_generation: generation,
    consecutive_no_change: String(index % 5)};
  if (index % 6 === 0) {
    Object.assign(todo, {material_change: "true", last_actor_agent_id: "agent-a",
      reason: `history observation ${index}`});
  }
  return {operation_id: `history-observation-${String(index).padStart(4, "0")}`,
    todo_id: String(monitor.todo_id), mutation: {kind: "todo_upsert", todo}};
}

/** Exercise execution handover inside the same mixed-status, decision and
 * historical-lease population. The ordinary fixture's counts stay unchanged. */
export function productionScaleLeaseLifecycleFixture(goalId: string,
  schema: AuthorityProjectionSchema = "native") {
  const fixture = productionScaleCoordinationFixture(goalId, schema);
  const scenario = envelope.lease_lifecycle;
  const todos = fixture.projection.todos as Record<string, unknown>[];
  const leases = fixture.projection.leases as Record<string, unknown>[];
  const target = fixture.completion_todo_id;
  const projection = authorityProjectionFixture(goalId,
    todos.map(todo => todo.todo_id === target ? {...todo, claimed_by: null} : todo),
    leases.map(lease => lease.todo_id === target ? {...lease, owner: scenario.owner,
      idempotency_key: scenario.execution_key, version: scenario.version,
      lease_epoch: scenario.lease_epoch} : lease), schema,
    {source_authority: "synthetic_production_scale_fixture", handoff_mode: "hard_lease"});
  return {projection, target, scenario, registered_agents: fixture.registered_agents};
}

/** Claimed handover retains the complete mixed-status work graph. */
export function productionScaleClaimTransferFixture(goalId: string,
  schema: AuthorityProjectionSchema = "native") {
  const fixture = productionScaleLeaseLifecycleFixture(goalId, schema);
  const target = (fixture.projection.todos as Record<string, unknown>[]).find(todo => todo.todo_id === fixture.target)!;
  target.claimed_by = fixture.scenario.owner;
  return {...fixture, projection: authorityProjectionFixture(goalId,
    fixture.projection.todos as Record<string, unknown>[], fixture.projection.leases as Record<string, unknown>[],
    schema, {handoff_mode: "hard_lease"})};
}

/** Start without a target lease, with a live peer beyond the bounded display. */
export function productionScaleLeaseAcquisitionFixture(goalId: string,
  schema: AuthorityProjectionSchema = "native") {
  const fixture = productionScaleLeaseLifecycleFixture(goalId, schema);
  const scenario = envelope.lease_acquisition;
  const todos = fixture.projection.todos as Record<string, unknown>[];
  const leases = fixture.projection.leases as Record<string, unknown>[];
  const target = todos.find(todo => todo.todo_id === fixture.target)!;
  const oldLease = leases.find(lease => lease.todo_id === fixture.target)!;
  const projection = authorityProjectionFixture(goalId,
    [...todos, {...target, todo_id: scenario.conflict_todo_id, text: "Independent scope holder beyond display limits"}],
    [...leases.filter(lease => lease.todo_id !== fixture.target), {...oldLease,
      todo_id: scenario.conflict_todo_id, owner: "agent-b", idempotency_key: "scope-holder",
      write_scopes: ["independent-work/**"]}], schema,
    {source_authority: "synthetic_production_scale_fixture", handoff_mode: "hard_lease"});
  return {...fixture, projection, acquisition: scenario};
}

/** A leased Monitor in the complete mixed work graph, with prior observations
 * and a waiting dependent. Only these target rows differ from the base fixture. */
export function productionScaleLeasedMonitorFixture(goalId: string,
  schema: AuthorityProjectionSchema = "native") {
  const fixture = productionScaleCoordinationFixture(goalId, schema);
  const target = fixture.completion_todo_id;
  const rows = fixture.projection.todos as Record<string, unknown>[];
  const dependent = [...rows].reverse().find(todo => todo.role === "agent" && todo.status === "open" && todo.todo_id !== target)!;
  const todos = rows.map(todo => todo.todo_id === target ? {...todo,
    task_class: "continuous_monitor", target_key: "leased-public-watch", cadence: "1h",
    material_change_generation: 4, consecutive_no_change: "2", result_hash: "previous-evidence",
    last_checked_at: "2026-09-01T00:00:00Z", next_due_at: "2026-09-01T01:00:00Z"} :
    todo.todo_id === dependent.todo_id ? {...todo, resume_when: `monitor_changed:${target}`,
      resume_monitor_generation: 4} : todo);
  const projection = authorityProjectionFixture(goalId, todos,
    fixture.projection.leases as Record<string, unknown>[], schema,
    {source_authority: "synthetic_production_scale_fixture", handoff_mode: "hard_lease"});
  return {projection, target, dependent: String(dependent.todo_id),
    actor: "agent-a", registered_agents: fixture.registered_agents,
    now: new Date("2026-09-01T01:00:00Z"),
    proof: {idempotency_key: fixture.completion_lease_idempotency_key,
      expected_version: fixture.completion_lease_expected_version}};
}

/** A completed watch and its waiting dependent within the full mixed graph. */
export function productionScaleCompletedMonitorFixture(goalId: string,
  schema: AuthorityProjectionSchema = "native") {
  const fixture = productionScaleLeasedMonitorFixture(goalId, schema);
  const todos = (fixture.projection.todos as Record<string, unknown>[]).map(todo =>
    todo.todo_id === fixture.target ? {...todo, status: "done", done: true,
      completed_at: "2026-09-01T00:30:00Z", no_followup: true,
      completion_continuation: "no_followup", completion_recovery: "same_turn_terminal_closeout",
      completion_turn_key: "retired-cycle", watch_only: "true"} : todo);
  return {...fixture, projection: authorityProjectionFixture(goalId, todos,
    (fixture.projection.leases as Record<string, unknown>[]).filter(lease => lease.todo_id !== fixture.target),
    schema, {source_authority: "synthetic_production_scale_fixture", handoff_mode: "legacy"})};
}
