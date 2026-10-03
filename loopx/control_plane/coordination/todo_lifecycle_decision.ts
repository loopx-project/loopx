import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  requireBoolean,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
  requireStringLiteral,
} from "../runtime_decode.ts";
import { normalizeRegisteredTodoAgents, normalizeTodoAgent } from "./todo_agents.ts";
import {normalizeTodoRepository} from "../todos/work_requirements.ts";
import {leaseWriteRepository, leaseRepositoryRejection} from "../work_items/task_lease_repository.ts";

export const COORDINATION_TODO_TERMINAL_DECISION_REQUEST_SCHEMA =
  "loopx_coordination_todo_terminal_decision_request_v0";
export const COORDINATION_TODO_TERMINAL_DECISION_RESULT_SCHEMA =
  "loopx_coordination_todo_terminal_decision_result_v0";

const COMMANDS = ["complete", "supersede"] as const;
const MUTATION_COMMANDS = ["claim", "update"] as const;
export const COORDINATION_TODO_MUTATION_DECISION_REQUEST_SCHEMA =
  "loopx_coordination_todo_mutation_decision_request_v0";
export const COORDINATION_TODO_MUTATION_DECISION_RESULT_SCHEMA =
  "loopx_coordination_todo_mutation_decision_result_v0";
export const COORDINATION_TERMINAL_FENCE_REQUEST_SCHEMA =
  "loopx_coordination_terminal_fence_request_v0";
export const COORDINATION_TERMINAL_FENCE_RESULT_SCHEMA =
  "loopx_coordination_terminal_fence_result_v0";
import {HANDOFF_MODES, type HandoffMode} from "./handoff_mode_policy.ts";
const OUTCOMES = ["approve", "reject", "cancel"] as const;
const AUTHORITY_ACTIONS = ["complete", "reassign", "supersede", "update"] as const;
const EXECUTOR_RECLAIM_ACTION = "reclaim";

type LifecycleCommand = typeof COMMANDS[number] | typeof MUTATION_COMMANDS[number];
type DecisionOutcome = typeof OUTCOMES[number];

interface DecisionScope extends JsonObject {
  readonly kind: string;
  readonly granularity: string;
  readonly scope_key: string;
}

interface TodoFact extends JsonObject {
  readonly task_repository?: string | null;
  readonly todo_id: string;
  readonly status: string;
  readonly role: "user" | "agent";
  readonly task_class: string | null;
  readonly claimed_by: string | null;
  readonly excluded_agents: readonly string[];
  readonly bound_agent: string | null;
  readonly blocks_agent: string | null;
  readonly decision_scope: DecisionScope | null;
  readonly required_decision_scopes: readonly DecisionScope[];
  readonly unblocks_todo_id: string | null;
}

interface LeaseFact extends JsonObject {
  readonly write_repository?: string | null;
  readonly present: boolean;
  readonly active: boolean;
  readonly status: string | null;
  readonly owner: string | null;
  readonly idempotency_key: string | null;
  readonly version: number;
  readonly lease_epoch: number;
  readonly write_scopes: readonly string[];
  readonly acquire_ttl_seconds: number | null;
}

interface LifecycleGrant extends JsonObject {
  readonly agent_id: string;
  readonly actions: readonly string[];
  readonly requires_reason: boolean;
}

interface LifecycleDecisionRequest {
  readonly command: LifecycleCommand;
  readonly handoff_mode: HandoffMode;
  readonly registered_agents: readonly string[];
  readonly lifecycle_grants: readonly LifecycleGrant[];
  readonly todo: TodoFact;
  readonly decision_target: TodoFact | null;
  readonly lease: LeaseFact | null;
  readonly actor_agent_id: string | null;
  readonly authority_action: string;
  readonly authority_reason: string | null;
  readonly decision_outcome: DecisionOutcome | null;
  readonly lease_idempotency_key: string | null;
  readonly lease_expected_version: number | null;
  readonly allow_user_gate_auto_acquire: boolean;
  readonly requested_claimed_by: string | null;
  readonly clear_claim: boolean;
  readonly ownership_mutation: boolean;
}

export interface CoordinationTodoTerminalDecisionResult extends JsonObject {
  readonly schema_version: typeof COORDINATION_TODO_TERMINAL_DECISION_RESULT_SCHEMA;
  readonly outcome: "apply" | "no_change" | "conflict" | "rejected";
  readonly code: string;
  readonly authority_mode: string | null;
  readonly ownership_gate: "not_required" | "require_holder" | "delegated_override";
  readonly lease_fence: "not_required" | "required" | "auto_acquire" | "delegated_override";
  readonly idempotent: boolean;
  readonly next_todo_status: "done" | null;
  readonly next_lease: LeaseFact | null;
}

function optionalString(value: unknown, label: string): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value;
}

function optionalAgent(value: unknown, label: string): string | null {
  const candidate = optionalString(value, label);
  return candidate === null ? null : normalizeTodoAgent(candidate, label);
}

function optionalNonNegativeInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined) return null;
  const candidate = requireInteger(value, label);
  if (candidate < 0) {
    throw new EffectRuntimeRequestError(`${label} must be a non-negative integer or null`);
  }
  return candidate;
}

function decisionScope(value: unknown, label: string): DecisionScope | null {
  if (value === null || value === undefined) return null;
  const scope = requireJsonObject(value, label);
  return {
    kind: requireNonEmptyString(scope.kind, `${label}.kind`),
    granularity: requireNonEmptyString(scope.granularity, `${label}.granularity`),
    scope_key: requireNonEmptyString(scope.scope_key, `${label}.scope_key`),
    ...(typeof scope.schema_version === "string"
      ? { schema_version: scope.schema_version }
      : {}),
    ...(typeof scope.decision_id === "string"
      ? { decision_id: scope.decision_id }
      : {}),
  };
}

function decisionScopes(value: unknown, label: string): DecisionScope[] {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError(`${label} must be an array`);
  }
  return value.map((scope, index) => decisionScope(scope, `${label}[${index}]`)!);
}

function todoFact(value: unknown, label: string): TodoFact {
  const todo = requireJsonObject(value, label);
  const role = requireStringLiteral(todo.role, ["user", "agent"] as const, `${label}.role`);
  return {
    todo_id: requireNonEmptyString(todo.todo_id, `${label}.todo_id`),
    task_repository: normalizeTodoRepository(todo.task_repository),
    status: requireNonEmptyString(todo.status, `${label}.status`),
    role,
    task_class: optionalString(todo.task_class, `${label}.task_class`),
    claimed_by: optionalAgent(todo.claimed_by, `${label}.claimed_by`),
    excluded_agents: normalizeRegisteredTodoAgents(
      requireStringArray(todo.excluded_agents ?? [], `${label}.excluded_agents`),
    ),
    bound_agent: optionalAgent(todo.bound_agent, `${label}.bound_agent`),
    blocks_agent: optionalAgent(todo.blocks_agent, `${label}.blocks_agent`),
    decision_scope: decisionScope(todo.decision_scope, `${label}.decision_scope`),
    required_decision_scopes: decisionScopes(
      todo.required_decision_scopes ?? [],
      `${label}.required_decision_scopes`,
    ),
    unblocks_todo_id: optionalString(todo.unblocks_todo_id, `${label}.unblocks_todo_id`),
  };
}

function leaseFact(value: unknown): LeaseFact | null {
  if (value === null || value === undefined) return null;
  const lease = requireJsonObject(value, "lease");
  const version = optionalNonNegativeInteger(lease.version, "lease.version");
  const epoch = optionalNonNegativeInteger(lease.lease_epoch, "lease.lease_epoch");
  if (version === null || epoch === null) {
    throw new EffectRuntimeRequestError("lease version and lease_epoch are required");
  }
  return {
    present: requireBoolean(lease.present, "lease.present"),
    active: requireBoolean(lease.active, "lease.active"),
    status: optionalString(lease.status, "lease.status"),
    owner: optionalAgent(lease.owner, "lease.owner"),
    idempotency_key: optionalString(lease.idempotency_key, "lease.idempotency_key"),
    version,
    lease_epoch: epoch,
    write_scopes: requireStringArray(lease.write_scopes ?? [], "lease.write_scopes"),
    ...(lease.write_repository == null ? {} : {write_repository: leaseWriteRepository(lease.write_repository)}),
    acquire_ttl_seconds: optionalNonNegativeInteger(
      lease.acquire_ttl_seconds,
      "lease.acquire_ttl_seconds",
    ),
  };
}

function lifecycleGrants(
  value: unknown,
  registeredAgents: readonly string[],
): LifecycleGrant[] {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError("lifecycle_grants must be an array");
  }
  const seen = new Set<string>();
  return value.map((raw, index) => {
    const grant = requireJsonObject(raw, `lifecycle_grants[${index}]`);
    const agentId = normalizeTodoAgent(grant.agent_id, `lifecycle_grants[${index}].agent_id`);
    if (!registeredAgents.includes(agentId)) {
      throw new EffectRuntimeRequestError(
        `todo lifecycle authority agent_id='${agentId}' must already be registered`,
      );
    }
    if (seen.has(agentId)) {
      throw new EffectRuntimeRequestError(`duplicate todo lifecycle authority grant for '${agentId}'`);
    }
    seen.add(agentId);
    const actions = requireStringArray(grant.actions, `lifecycle_grants[${index}].actions`)
      .map((action) => action.trim().toLowerCase());
    if (actions.length === 0 || actions.some((action) =>
      !AUTHORITY_ACTIONS.some((candidate) => candidate === action))) {
      throw new EffectRuntimeRequestError(
        "todo lifecycle authority actions must contain supported actions",
      );
    }
    return {
      agent_id: agentId,
      actions: [...new Set(actions)],
      requires_reason: requireBoolean(
        grant.requires_reason,
        `lifecycle_grants[${index}].requires_reason`,
      ),
    };
  });
}

function executorReclaimGrant(request: JsonObject, actor: string | null): LifecycleGrant[] {
  // This is the existing executor's ephemeral, clock-authorized clear-claim
  // intent, not a configurable public lifecycle grant. The executor checks
  // expiry/grace under CAS; this pure decision still owns actor eligibility.
  const grants = request.lifecycle_grants;
  if (request.command !== "update" || request.clear_claim !== true ||
      request.ownership_mutation !== true || request.requested_claimed_by != null ||
      actor === null || !Array.isArray(grants) || grants.length !== 1) {
    throw new EffectRuntimeRequestError("executor reclaim requires one standing clear-claim grant");
  }
  const grant = requireJsonObject(grants[0], "executor reclaim grant");
  const agent = normalizeTodoAgent(grant.agent_id, "executor reclaim grant.agent_id");
  const actions = requireStringArray(grant.actions, "executor reclaim grant.actions");
  if (agent !== actor || actions.length !== 1 || actions[0] !== EXECUTOR_RECLAIM_ACTION ||
      grant.requires_reason !== false) {
    throw new EffectRuntimeRequestError("executor reclaim grant must match its actor and action");
  }
  // Do not require registration here: authority() must return the established
  // typed actor rejection before considering this synthesized delegation.
  return [{agent_id: agent, actions: [EXECUTOR_RECLAIM_ACTION], requires_reason: false}];
}

function decodeRequest(value: unknown, kind: "terminal" | "mutation" = "terminal"): LifecycleDecisionRequest {
  const request = requireJsonObject(value, "Todo terminal decision request");
  requireStringLiteral(
    request.schema_version,
    [kind === "terminal" ? COORDINATION_TODO_TERMINAL_DECISION_REQUEST_SCHEMA
      : COORDINATION_TODO_MUTATION_DECISION_REQUEST_SCHEMA],
    "schema_version",
  );
  const registeredAgents = normalizeRegisteredTodoAgents(
    requireStringArray(request.registered_agents, "registered_agents"),
  );
  const actor = optionalAgent(request.actor_agent_id, "actor_agent_id");
  const internalReclaim = kind === "mutation" && request.authority_action === EXECUTOR_RECLAIM_ACTION;
  const outcome = kind === "terminal" ? optionalString(request.decision_outcome, "decision_outcome") : null;
  return {
    command: requireStringLiteral(request.command,
      kind === "terminal" ? COMMANDS : MUTATION_COMMANDS, "command"),
    handoff_mode: requireStringLiteral(request.handoff_mode, HANDOFF_MODES, "handoff_mode"),
    registered_agents: registeredAgents,
    lifecycle_grants: internalReclaim ? executorReclaimGrant(request, actor)
      : lifecycleGrants(request.lifecycle_grants ?? [], registeredAgents),
    todo: todoFact(request.todo, "todo"),
    decision_target: kind !== "terminal" || request.decision_target === null || request.decision_target === undefined
      ? null
      : todoFact(request.decision_target, "decision_target"),
    lease: leaseFact(request.lease),
    actor_agent_id: actor,
    authority_action: requireStringLiteral(
      request.authority_action,
      kind === "terminal" ? AUTHORITY_ACTIONS
        : [...AUTHORITY_ACTIONS, "claim", EXECUTOR_RECLAIM_ACTION],
      "authority_action",
    ),
    authority_reason: optionalString(request.authority_reason, "authority_reason"),
    decision_outcome: outcome === null
      ? null
      : requireStringLiteral(outcome, OUTCOMES, "decision_outcome"),
    lease_idempotency_key: kind === "terminal" ? optionalString(
      request.lease_idempotency_key,
      "lease_idempotency_key",
    ) : null,
    lease_expected_version: kind === "terminal" ? optionalNonNegativeInteger(
      request.lease_expected_version,
      "lease_expected_version",
    ) : null,
    allow_user_gate_auto_acquire: kind === "terminal" ? requireBoolean(
      request.allow_user_gate_auto_acquire,
      "allow_user_gate_auto_acquire",
    ) : false,
    requested_claimed_by: optionalAgent(request.requested_claimed_by, "requested_claimed_by"),
    clear_claim: kind === "mutation" ? requireBoolean(request.clear_claim, "clear_claim") : false,
    ownership_mutation: kind === "mutation"
      ? requireBoolean(request.ownership_mutation, "ownership_mutation") : false,
  };
}

function scopeKey(scope: DecisionScope): string {
  return `${scope.kind}\u0000${scope.granularity}\u0000${scope.scope_key}`;
}

function exactUserGateOverride(request: LifecycleDecisionRequest): boolean {
  const { todo, decision_target: target } = request;
  return request.command === "complete" && target !== null && todo.role === "user" &&
    todo.task_class === "user_gate" && request.decision_outcome !== null &&
    todo.decision_scope !== null && todo.unblocks_todo_id === target.todo_id &&
    target.required_decision_scopes.some((scope) =>
      scopeKey(scope) === scopeKey(todo.decision_scope!));
}

function result(
  outcome: CoordinationTodoTerminalDecisionResult["outcome"],
  code: string,
  options: Partial<Pick<CoordinationTodoTerminalDecisionResult,
    "authority_mode" | "ownership_gate" | "lease_fence" | "idempotent" |
    "next_todo_status" | "next_lease">> = {},
): CoordinationTodoTerminalDecisionResult {
  return {
    schema_version: COORDINATION_TODO_TERMINAL_DECISION_RESULT_SCHEMA,
    outcome,
    code,
    authority_mode: options.authority_mode ?? null,
    ownership_gate: options.ownership_gate ?? "not_required",
    lease_fence: options.lease_fence ?? "not_required",
    idempotent: options.idempotent ?? false,
    next_todo_status: options.next_todo_status ?? null,
    next_lease: options.next_lease ?? null,
  };
}

/** Registered-actor restrictions, independent of single-agent compatibility or
 * delegated authority. Native edits and multi-agent lifecycle admission share it. */
export function registeredTodoMutationRejection(raw: JsonObject, actor: string | null,
  registered: readonly string[]): string | null {
  const todo = todoFact(raw, "todo");
  if (actor === null) return "actor_required";
  if (!registered.includes(actor)) return "actor_not_registered";
  if (todo.excluded_agents.includes(actor)) return "actor_excluded";
  const boundAgent = todo.bound_agent ?? (todo.role === "user" ? todo.blocks_agent : null);
  if (boundAgent !== null && boundAgent !== actor) return "bound_agent_mismatch";
  if (todo.claimed_by !== null && todo.claimed_by !== actor) return "claim_owner_mismatch";
  return null;
}

function authority(request: LifecycleDecisionRequest):
  | { mode: string; ownershipGate: CoordinationTodoTerminalDecisionResult["ownership_gate"] }
  | CoordinationTodoTerminalDecisionResult {
  const { todo, actor_agent_id: actor, registered_agents: registered } = request;
  if (registered.length <= 1) {
    if (actor !== null) {
      const rejection = registeredTodoMutationRejection(todo, actor, registered);
      if (rejection !== null) return result("rejected", rejection);
    } else if (todo.claimed_by !== null || todo.bound_agent !== null ||
        todo.blocks_agent !== null || todo.excluded_agents.length > 0) {
      // Actorless compatibility is limited to genuinely unowned Todo records;
      // ownership, binding, and exclusion facts require an accountable actor.
      return result("rejected", "actor_required");
    }
    return { mode: "single_agent_compatibility", ownershipGate: "not_required" };
  }
  if (exactUserGateOverride(request)) {
    return { mode: "exact_user_gate_decision_scope_override", ownershipGate: "not_required" };
  }
  const rejection = registeredTodoMutationRejection(todo, actor, registered);
  if (rejection !== null && rejection !== "claim_owner_mismatch") return result("rejected", rejection);
  if (rejection === "claim_owner_mismatch") {
    const grant = request.lifecycle_grants.find((candidate) => candidate.agent_id === actor);
    if (grant === undefined) return result("rejected", "claim_owner_mismatch");
    if (!grant.actions.includes(request.authority_action)) {
      return result("rejected", "delegation_action_not_granted");
    }
    if (grant.requires_reason && !String(request.authority_reason ?? "").trim()) {
      return result("rejected", "delegation_reason_required");
    }
    return { mode: "delegated_orchestration_override", ownershipGate: "not_required" };
  }
  if (request.command === "claim" && request.requested_claimed_by !== actor) {
    return result("rejected", "claim_actor_mismatch");
  }
  return { mode: "registered_peer_actor", ownershipGate: "not_required" };
}

type FenceRequest = Pick<LifecycleDecisionRequest,
  "todo" | "lease" | "registered_agents" | "handoff_mode" | "actor_agent_id" |
  "lease_idempotency_key" | "lease_expected_version" | "allow_user_gate_auto_acquire"> & {
    readonly command?: LifecycleCommand;
  };

function ownerIdentityEligible(request: FenceRequest, owner: string | null): boolean {
  const todo = request.todo;
  return owner !== null &&
    request.registered_agents.includes(owner) && !todo.excluded_agents.includes(owner) &&
    (todo.claimed_by === null || todo.claimed_by === owner);
}

function ownerEligible(request: FenceRequest, owner: string | null): boolean {
  return request.todo.status === "open" && ownerIdentityEligible(request, owner);
}

function terminalFence(
  request: FenceRequest,
  authorityMode: string | null,
  requireActiveWhenFenceSupplied = true,
): CoordinationTodoTerminalDecisionResult {
  const lease = request.lease;
  const timeActive = lease !== null && lease.present && lease.active;
  const effective = timeActive && ownerEligible(request, lease.owner);
  const explicitFence = request.lease_idempotency_key !== null ||
    request.lease_expected_version !== null;
  const delegated = authorityMode === "delegated_orchestration_override";
  // User actions cannot claim execution leases. Closing an ordinary reminder
  // by its exact registered bound actor is instead a provider-CAS lifecycle
  // edit. Never bypass an active holder or an explicitly supplied fence, and
  // never mint a gate lease, execution claim or broader decision authority.
  if (request.command === "complete" && request.handoff_mode === "hard_lease" &&
      request.todo.role === "user" && request.todo.task_class === "user_action" &&
      request.todo.bound_agent !== null && request.todo.bound_agent === request.actor_agent_id &&
      !delegated && !timeActive && !explicitFence &&
      ownerIdentityEligible(request, request.actor_agent_id)) {
    return result("apply", "terminal_fence_not_required", {
      authority_mode: authorityMode, lease_fence: "not_required",
      next_lease: lease?.present && lease.status !== "released"
        ? {...lease, active: false, status: "released"} : null,
    });
  }
  // Deferred work cannot acquire a lease. Superseding a retired wait is a
  // terminal lifecycle edit, not execution, and the provider CAS retires any
  // expired lease lineage together with the Todo transition.
  if (request.command === "supersede" && request.handoff_mode === "hard_lease" &&
      request.todo.role === "agent" && request.todo.status === "deferred" &&
      !delegated && !timeActive && !explicitFence &&
      ownerIdentityEligible(request, request.actor_agent_id)) {
    return result("apply", "terminal_fence_not_required", {
      authority_mode: authorityMode,
      lease_fence: "not_required",
      next_lease: lease?.present && lease.status !== "released"
        ? {...lease, active: false, status: "released"} : null,
    });
  }
  const autoAcquire = request.handoff_mode === "hard_lease" && !delegated &&
    request.allow_user_gate_auto_acquire && request.todo.role === "user" &&
    request.todo.task_class === "user_gate";
  if (autoAcquire && !effective && !timeActive) {
    if (!ownerEligible(request, request.actor_agent_id)) {
      return result("rejected", "handoff_mode_requires_lease", {
        authority_mode: authorityMode,
        lease_fence: "auto_acquire",
      });
    }
    const version = (lease?.present ? lease.version : 0) + 1;
    const epoch = (lease?.lease_epoch ?? 0) + 1;
    return result("apply", "terminal_fence_verified", {
      authority_mode: authorityMode,
      lease_fence: "auto_acquire",
      next_lease: {
        present: true,
        active: false,
        status: "released",
        owner: request.actor_agent_id,
        idempotency_key: request.lease_idempotency_key ?? `auto-${request.todo.todo_id}`,
        version,
        lease_epoch: epoch,
        write_scopes: [],
        acquire_ttl_seconds: 2700,
      },
    });
  }
  if (!effective) {
    if (request.handoff_mode === "hard_lease" && !delegated) {
      return result("rejected", timeActive
        ? "handoff_mode_lease_claim_divergence"
        : "handoff_mode_requires_lease", {
        authority_mode: authorityMode,
        lease_fence: "required",
      });
    }
    if (explicitFence && requireActiveWhenFenceSupplied) {
      return result("rejected", "lease_not_active", { authority_mode: authorityMode });
    }
    return result("apply", "terminal_fence_not_required", {
      authority_mode: authorityMode,
      lease_fence: delegated && request.handoff_mode === "hard_lease"
        ? "delegated_override"
        : "not_required",
    });
  }
  const repositoryRejection = leaseRepositoryRejection(request.todo, lease);
  if (repositoryRejection !== null) return result("rejected", repositoryRejection, {
    authority_mode: authorityMode, lease_fence: "required",
  });
  if (request.lease_idempotency_key === null) {
    return result("rejected", "lease_fence_required", {
      authority_mode: authorityMode,
      lease_fence: "required",
    });
  }
  if (lease!.owner !== request.actor_agent_id ||
      lease!.idempotency_key !== request.lease_idempotency_key) {
    return result("rejected", "lease_cas_mismatch", {
      authority_mode: authorityMode,
      lease_fence: "required",
    });
  }
  if (request.lease_expected_version === null) {
    return result("rejected", "version_required", {
      authority_mode: authorityMode,
      lease_fence: "required",
    });
  }
  if (lease!.version !== request.lease_expected_version) {
    return result("conflict", "version_mismatch", {
      authority_mode: authorityMode,
      lease_fence: "required",
    });
  }
  return result("apply", "terminal_fence_verified", {
    authority_mode: authorityMode,
    lease_fence: "required",
    next_lease: { ...lease!, active: false, status: "released" },
  });
}

/** One semantic owner for complete/supersede authority and the terminal lease fence. */
export function evaluateCoordinationTodoTerminalDecision(
  value: unknown,
): CoordinationTodoTerminalDecisionResult {
  const request = decodeRequest(value);
  const authorityResult = authority(request);
  if ("outcome" in authorityResult) return authorityResult;
  if (request.todo.status === "done") {
    return result("no_change", "terminal_replay", {
      authority_mode: authorityResult.mode,
      ownership_gate: authorityResult.ownershipGate,
      idempotent: true,
    });
  }
  const fence = terminalFence(request, authorityResult.mode);
  return fence.outcome === "apply"
    ? { ...fence, code: "terminal_transition", next_todo_status: "done" }
    : fence;
}

function ownershipGate(mode: HandoffMode, mutation: boolean, authorityMode: string | null):
    CoordinationTodoTerminalDecisionResult["ownership_gate"] {
  if (!mutation || mode !== "hard_lease") return "not_required";
  return authorityMode === "delegated_orchestration_override" ? "delegated_override" : "require_holder";
}

export function evaluateTodoOwnershipGate(value: unknown): JsonObject {
  const input = requireJsonObject(value, "Todo ownership gate");
  return { ownership_gate: ownershipGate(
    requireStringLiteral(input.handoff_mode, HANDOFF_MODES, "handoff_mode"),
    requireBoolean(input.ownership_mutation, "ownership_mutation"),
    optionalString(input.authority_mode, "authority_mode"),
  ) };
}

/** Claim/update admission only: this neither edits arbitrary fields nor releases a lease. */
export function evaluateCoordinationTodoMutationDecision(value: unknown): JsonObject {
  const request = decodeRequest(value, "mutation");
  const decided = authority(request);
  if ("outcome" in decided) {
    return { ...decided, schema_version: COORDINATION_TODO_MUTATION_DECISION_RESULT_SCHEMA };
  }
  const gate = ownershipGate(request.handoff_mode, request.ownership_mutation, decided.mode);
  const lease = request.lease;
  const denied = gate === "require_holder" &&
    (lease === null || !lease.present || !lease.active || lease.owner !== request.actor_agent_id);
  return {
    ...result(denied ? "rejected" : "apply", denied ? "handoff_mode_requires_lease" : "todo_transition", {
      authority_mode: decided.mode, ownership_gate: gate,
    }),
    schema_version: COORDINATION_TODO_MUTATION_DECISION_RESULT_SCHEMA,
    next_todo_status: denied ? null : request.todo.status,
    next_todo_claimed_by: denied ? null : request.command === "claim"
      ? request.requested_claimed_by
      : request.ownership_mutation
        ? request.clear_claim ? null : request.requested_claimed_by
        : request.todo.claimed_by,
  };
}

/** Preauthorized lease fence; authority was checked by the caller under its state lock. */
export function evaluateCoordinationTerminalFence(value: unknown): JsonObject {
  const input = requireJsonObject(value, "terminal fence request");
  requireStringLiteral(input.schema_version, [COORDINATION_TERMINAL_FENCE_REQUEST_SCHEMA], "schema_version");
  const request: FenceRequest = {
    todo: todoFact(input.todo, "todo"),
    lease: leaseFact(input.lease),
    registered_agents: normalizeRegisteredTodoAgents(requireStringArray(input.registered_agents, "registered_agents")),
    handoff_mode: requireStringLiteral(input.handoff_mode, HANDOFF_MODES, "handoff_mode"),
    actor_agent_id: optionalAgent(input.actor_agent_id, "actor_agent_id"),
    lease_idempotency_key: optionalString(input.lease_idempotency_key, "lease_idempotency_key"),
    lease_expected_version: optionalNonNegativeInteger(input.lease_expected_version, "lease_expected_version"),
    allow_user_gate_auto_acquire: requireBoolean(input.allow_user_gate_auto_acquire, "allow_user_gate_auto_acquire"),
  };
  const delegated = requireBoolean(input.delegated_authority, "delegated_authority");
  const fence = terminalFence(request, delegated ? "delegated_orchestration_override" : null,
    requireBoolean(input.require_active_when_fence_supplied, "require_active_when_fence_supplied"));
  return { ...fence, schema_version: COORDINATION_TERMINAL_FENCE_RESULT_SCHEMA, authority_mode: null };
}
