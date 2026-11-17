import {createHash} from "node:crypto";
import {planTodoPriority} from "../todos/priority.ts";
import type { JsonObject } from "../effect_program.ts";
import {AGENT_TODO_TASK_CLASSES as AGENT_TASK_CLASSES, USER_TODO_TASK_CLASSES as USER_TASK_CLASSES, userTodoScopeConflict} from "../todos/authoring_scope.ts";
import {
  AuthorityStoreProtocolError,
  canonicalAuthorityObject,
  optionalAuthorityBoolean,
  optionalAuthorityString,
  requireAuthorityLiteral,
  requireAuthorityStoreId,
} from "./authority_store_codec.ts";
import {
  TODO_CANONICAL_READ_RECORD_SCHEMA,
  TODO_DOMAIN_ITEM_SCHEMA,
  canonicalTodoDomainRecord,
} from "./coordination_state_contract.ts";
import {
  compactPythonWhitespace,
  normalizeRegisteredTodoAgents,
  normalizeTodoAgent,
  optionalRegisteredTodoAgent,
  stripPythonWhitespace,
} from "./todo_agents.ts";
import {canonicalTodoRecord, materializeTodoRecordForSchema} from "./todo_presentation.ts";

export const TODO_SUCCESSOR_DERIVATION_REQUEST_SCHEMA =
  "loopx_todo_successor_derivation_request_v0";
export const TODO_SUCCESSOR_DERIVATION_RESULT_SCHEMA =
  "loopx_todo_successor_derivation_result_v0";

const TERMINAL_COMMANDS = ["complete", "supersede"] as const;
const TODO_ROLES = ["agent", "user"] as const;
const CONTINUATION_POLICIES = new Set([
  "independent_handoff",
  "same_agent_non_delivery",
]);

type TerminalCommand = typeof TERMINAL_COMMANDS[number];
type TodoRole = typeof TODO_ROLES[number];

export interface TodoSuccessorDerivationInput {
  readonly schema_version: typeof TODO_SUCCESSOR_DERIVATION_REQUEST_SCHEMA;
  readonly command: TerminalCommand;
  readonly predecessor: JsonObject;
  readonly registered_agents: readonly string[];
  readonly actor_agent_id: string | null;
  readonly completion_policy: JsonObject | null;
  readonly successor_intents: readonly JsonObject[];
}

interface NormalizedSuccessorIntent extends JsonObject {
  role: TodoRole;
  text: string;
  task_class: string;
}

function optionalStringArray(value: unknown, label: string): string[] {
  if (value === null || value === undefined) return [];
  if (!Array.isArray(value)) {
    throw new AuthorityStoreProtocolError(`${label} must be an array`);
  }
  const values = value.map((item, index) => {
    if (typeof item !== "string" || compactPythonWhitespace(item).length === 0) {
      throw new AuthorityStoreProtocolError(`${label}[${index}] must be a string`);
    }
    return compactPythonWhitespace(item);
  });
  if (new Set(values).size !== values.length) {
    throw new AuthorityStoreProtocolError(`${label} must contain unique values`);
  }
  return values;
}

function optionalRegisteredAgents(
  value: unknown,
  label: string,
  registeredAgents: readonly string[],
): string[] {
  return optionalStringArray(value, label).map((raw, index) => {
    const agent = normalizeTodoAgent(raw, `${label}[${index}]`);
    if (!registeredAgents.includes(agent)) {
      throw new AuthorityStoreProtocolError(`${label}[${index}] is not registered`);
    }
    return agent;
  });
}

function normalizeIntent(
  value: unknown,
  index: number,
  registeredAgents: readonly string[],
): NormalizedSuccessorIntent {
  const raw = canonicalAuthorityObject(value, `successor_intents[${index}]`);
  const role = requireAuthorityLiteral(raw.role, TODO_ROLES, `successor_intents[${index}].role`);
  const rawTaskClass = optionalAuthorityString(
    raw.task_class,
    `successor_intents[${index}].task_class`,
  );
  const taskClass = rawTaskClass === null
    ? (role === "agent" ? "advancement_task" : "")
    : stripPythonWhitespace(rawTaskClass).toLowerCase();
  if (role === "agent" && !AGENT_TASK_CLASSES.has(taskClass)) {
    throw new AuthorityStoreProtocolError(
      "Agent successor task_class must be advancement_task, continuous_monitor, or blocker",
    );
  }
  if (role === "user" && !USER_TASK_CLASSES.has(taskClass)) {
    throw new AuthorityStoreProtocolError(
      "User successor task_class must be user_action or user_gate",
    );
  }
  const claimedBy = optionalRegisteredTodoAgent(
    raw.claimed_by,
    `successor_intents[${index}].claimed_by`,
    registeredAgents,
  );
  const excludedAgents = optionalRegisteredAgents(
    raw.excluded_agents,
    `successor_intents[${index}].excluded_agents`,
    registeredAgents,
  );
  if (claimedBy !== null && excludedAgents.includes(claimedBy)) {
    throw new AuthorityStoreProtocolError(
      "successor claimed_by cannot also appear in excluded_agents",
    );
  }
  const rawContinuationPolicy = optionalAuthorityString(
    raw.continuation_policy,
    `successor_intents[${index}].continuation_policy`,
  );
  const continuationPolicy = rawContinuationPolicy === null
    ? null
    : stripPythonWhitespace(rawContinuationPolicy).toLowerCase();
  if (continuationPolicy !== null && !CONTINUATION_POLICIES.has(continuationPolicy)) {
    throw new AuthorityStoreProtocolError(
      "successor continuation_policy is unsupported",
    );
  }
  const requiredCapabilities = optionalStringArray(
    raw.required_capabilities,
    `successor_intents[${index}].required_capabilities`,
  );
  const rawActionKind = optionalAuthorityString(
    raw.action_kind,
    `successor_intents[${index}].action_kind`,
  );
  const actionKind = rawActionKind === null
    ? null
    : stripPythonWhitespace(rawActionKind).toLowerCase();
  if (actionKind !== null && !/^[a-z][a-z0-9_-]{0,63}$/u.test(actionKind)) {
    throw new AuthorityStoreProtocolError(
      "successor action_kind must be a public-safe token",
    );
  }
  return {
    ...raw,
    role,
    text: optionalAuthorityString(raw.text, `successor_intents[${index}].text`) ?? "",
    task_class: taskClass,
    ...(claimedBy === null ? {} : {claimed_by: claimedBy}),
    excluded_agents: excludedAgents,
    required_capabilities: requiredCapabilities,
    ...(actionKind === null ? {} : {action_kind: actionKind}),
    ...(continuationPolicy === null ? {} : {continuation_policy: continuationPolicy}),
  };
}

function compactOptionalField(
  target: JsonObject,
  key: string,
  value: unknown,
): void {
  if (value !== null && value !== undefined && value !== "" &&
      (!Array.isArray(value) || value.length > 0)) {
    target[key] = value;
  }
}

function completionPolicyAgent(
  policy: JsonObject,
  field: "effective_claimed_by" | "effective_next_claimed_by",
  registeredAgents: readonly string[],
): string | null {
  return optionalRegisteredTodoAgent(policy[field], `completion_policy.${field}`, registeredAgents);
}

/**
 * Derive terminal successor proposals from caller intent and predecessor facts.
 *
 * This function owns priority, capability/binding, exclusion, and predecessor
 * relation inheritance for both canonical transactions and the legacy facade.
 * It deliberately does not assign provider- or Markdown-specific identities.
 */
export function deriveCoordinationTodoSuccessorProposals(
  rawInput: TodoSuccessorDerivationInput,
): JsonObject[] {
  if (rawInput.schema_version !== TODO_SUCCESSOR_DERIVATION_REQUEST_SCHEMA) {
    throw new AuthorityStoreProtocolError("Todo successor derivation schema mismatch");
  }
  const command = requireAuthorityLiteral(rawInput.command, TERMINAL_COMMANDS, "command");
  if (!Array.isArray(rawInput.successor_intents)) {
    throw new AuthorityStoreProtocolError("successor_intents must be an array");
  }
  if (rawInput.successor_intents.length === 0) {
    return [];
  }
  const predecessor = canonicalAuthorityObject(rawInput.predecessor, "predecessor");
  const predecessorId = requireAuthorityStoreId(predecessor.todo_id, "predecessor.todo_id");
  const registeredAgents = normalizeRegisteredTodoAgents(rawInput.registered_agents);
  const actor = optionalRegisteredTodoAgent(
    rawInput.actor_agent_id,
    "actor_agent_id",
    registeredAgents,
  );
  const intents = rawInput.successor_intents.map((intent, index) =>
    normalizeIntent(intent, index, registeredAgents));
  for (const role of TODO_ROLES) {
    if (intents.filter((intent) => intent.role === role).length > 1) {
      throw new AuthorityStoreProtocolError(
        `terminal lifecycle permits at most one generated ${role} successor`,
      );
    }
  }
  const completionPolicy = rawInput.completion_policy === null
    ? null
    : canonicalAuthorityObject(rawInput.completion_policy, "completion_policy");
  if (command === "complete" && completionPolicy === null && intents.length > 0) {
    throw new AuthorityStoreProtocolError(
      "complete successor derivation requires the committed completion policy",
    );
  }
  if (command === "supersede" && completionPolicy !== null) {
    throw new AuthorityStoreProtocolError(
      "supersede successor derivation must not carry a completion policy",
    );
  }

  const proposals: JsonObject[] = [];
  for (const intent of intents) {
    const proposal: JsonObject = {
      role: intent.role,
      text: String(planTodoPriority(predecessor, {text: intent.text}).text),
      task_class: intent.task_class,
    };
    compactOptionalField(proposal, "created_by", actor);
    if (intent.role === "agent") {
      let claimedBy = optionalRegisteredTodoAgent(
        intent.claimed_by,
        "successor_intent.claimed_by",
        registeredAgents,
      );
      let excludedAgents = optionalRegisteredAgents(
        intent.excluded_agents,
        "successor_intent.excluded_agents",
        registeredAgents,
      );
      if (completionPolicy !== null) {
        claimedBy = completionPolicyAgent(
          completionPolicy,
          "effective_next_claimed_by",
          registeredAgents,
        );
        excludedAgents = optionalRegisteredAgents(
          completionPolicy.effective_next_excluded_agents,
          "completion_policy.effective_next_excluded_agents",
          registeredAgents,
        );
      } else if (claimedBy === null &&
          intent.continuation_policy === "same_agent_non_delivery") {
        claimedBy = optionalRegisteredTodoAgent(
          predecessor.claimed_by,
          "predecessor.claimed_by",
          registeredAgents,
        );
      }
      if (claimedBy !== null && excludedAgents.includes(claimedBy)) {
        throw new AuthorityStoreProtocolError(
          "derived successor claimed_by cannot also appear in excluded_agents",
        );
      }
      compactOptionalField(proposal, "action_kind", intent.action_kind);
      compactOptionalField(
        proposal,
        "capability_binding_ref",
        predecessor.capability_binding_ref,
      );
      compactOptionalField(proposal, "task_repository", intent.task_repository);
      compactOptionalField(
        proposal,
        "required_capabilities",
        intent.required_capabilities,
      );
      compactOptionalField(
        proposal,
        "continuation_policy",
        intent.continuation_policy,
      );
      compactOptionalField(proposal, "claimed_by", claimedBy);
      proposal.excluded_agents = excludedAgents;
      compactOptionalField(
        proposal,
        "unblocks_todo_id",
        command === "complete" ? predecessorId : predecessor.unblocks_todo_id,
      );
    } else {
      let boundAgent: string | null = null;
      if (command === "complete") {
        if (registeredAgents.length > 1 && completionPolicy !== null) {
          boundAgent = completionPolicyAgent(
            completionPolicy,
            "effective_claimed_by",
            registeredAgents,
          );
          if (boundAgent === null) {
            throw new AuthorityStoreProtocolError(
              "multi-agent completion requires an effective completing Agent for its User successor",
            );
          }
        }
      } else {
        boundAgent = optionalRegisteredTodoAgent(
          predecessor.bound_agent ?? predecessor.blocks_agent ??
            predecessor.claimed_by ?? intents.find((candidate) =>
              candidate.role === "agent")?.claimed_by,
          "supersede user successor binding",
          registeredAgents,
        );
        if (registeredAgents.length > 1 && boundAgent === null) {
          throw new AuthorityStoreProtocolError(
            "multi-agent supersede requires an inherited or requested User successor binding",
          );
        }
      }
      compactOptionalField(proposal, "bound_agent", boundAgent);
      if (intent.task_class === "user_gate") {
        compactOptionalField(proposal, "blocks_agent", boundAgent);
        proposal.action_kind = "gate";
      }
    }
    proposals.push(proposal);
  }
  return proposals;
}

/** Pure effect-runtime wire handler used by the legacy Python facade. */
export function evaluateCoordinationTodoSuccessorDerivation(
  value: unknown,
): JsonObject {
  try {
    const input = canonicalAuthorityObject(value, "Todo successor derivation request");
    const proposals = deriveCoordinationTodoSuccessorProposals(
      input as unknown as TodoSuccessorDerivationInput,
    );
    return {
      schema_version: TODO_SUCCESSOR_DERIVATION_RESULT_SCHEMA,
      status: "derived",
      successors: proposals,
    };
  } catch (error) {
    return {
      schema_version: TODO_SUCCESSOR_DERIVATION_RESULT_SCHEMA,
      status: "failed",
      reason_code: "invalid_todo_successor_derivation",
      reason: error instanceof Error ? error.message : "invalid Todo successor derivation",
    };
  }
}

function validateSuccessorSemantics(
  successors: readonly JsonObject[],
  registeredAgents: readonly string[],
): void {
  for (const role of TODO_ROLES) {
    if (successors.filter((successor) => successor.role === role).length > 1) {
      throw new AuthorityStoreProtocolError(
        `terminal lifecycle permits at most one generated ${role} successor`,
      );
    }
  }
  for (const successor of successors) {
    const role = requireAuthorityLiteral(successor.role, TODO_ROLES, "successor.role");
    const taskClass = optionalAuthorityString(successor.task_class, "successor.task_class");
    const claimedBy = optionalRegisteredTodoAgent(successor.claimed_by, "successor.claimed_by", registeredAgents);
    const boundAgent = optionalRegisteredTodoAgent(successor.bound_agent, "successor.bound_agent", registeredAgents);
    const blocksAgent = optionalRegisteredTodoAgent(successor.blocks_agent, "successor.blocks_agent", registeredAgents);
    const goalBound = optionalAuthorityBoolean(successor.goal_bound, "successor.goal_bound");
    const globalGate = optionalAuthorityBoolean(successor.global_gate, "successor.global_gate");
    if (role === "agent") {
      if (USER_TASK_CLASSES.has(taskClass ?? "") || boundAgent !== null ||
          blocksAgent !== null || goalBound === true || globalGate === true) {
        throw new AuthorityStoreProtocolError(
          "generated Agent successor carries user-only task or binding semantics",
        );
      }
      continue;
    }
    if (!USER_TASK_CLASSES.has(taskClass ?? "")) {
      throw new AuthorityStoreProtocolError(
        "generated User successor requires task_class user_action or user_gate",
      );
    }
    if (claimedBy !== null) {
      throw new AuthorityStoreProtocolError(
        "generated User successor cannot carry claimed_by ownership",
      );
    }
    const scopeConflict = userTodoScopeConflict(taskClass, {
      bound_agent: boundAgent, goal_bound: goalBound, blocks_agent: blocksAgent, global_gate: globalGate,
    }, registeredAgents.length);
    if (scopeConflict === "binding_conflict") {
      throw new AuthorityStoreProtocolError("generated User successor cannot be both agent-bound and goal-bound");
    }
    if (taskClass === "user_action" && (blocksAgent !== null || globalGate === true)) {
      throw new AuthorityStoreProtocolError("generated user_action successor cannot carry blocking gate scope");
    }
    if (scopeConflict) {
      throw new AuthorityStoreProtocolError({
        gate_scope_conflict: "goal-wide User gate successor requires goal_bound and no Agent binding",
        global_binding_conflict: "goal-wide User gate successor requires goal_bound and no Agent binding",
        agent_binding_conflict: "Agent-scoped User gate successor must bind to its blocks_agent",
        gate_scope_missing: "multi-agent User gate successor requires an explicit blocking scope",
        binding_missing: "multi-agent User successor requires an explicit Agent or Goal binding",
      }[scopeConflict]);
    }
  }
}

function generatedSuccessorId(predecessorId: string, successor: JsonObject, offset: number): string {
  const role = String(successor.role);
  const section = role === "agent" ? "Agent Todo" : "User Todo";
  const identity = [role, section, `${predecessorId}:${offset}`, compactPythonWhitespace(String(successor.text ?? ""))].join("|");
  return `todo_${createHash("sha1").update(identity, "utf8").digest("hex").slice(0, 12)}`;
}

function materializeSuccessorProposals(predecessorId: string, proposals: readonly JsonObject[]): JsonObject[] {
  const successors = proposals.map((proposal, index) => canonicalTodoDomainRecord({
    ...proposal,
    schema_version: TODO_DOMAIN_ITEM_SCHEMA,
    todo_id: generatedSuccessorId(predecessorId, proposal, index + 1),
    status: "open",
    done: false,
    archive_state: "active",
  }, `derived successors[${index}]`));
  const successorIds = successors.map((successor) => String(successor.todo_id));
  if (new Set(successorIds).size !== successorIds.length) {
    throw new AuthorityStoreProtocolError("derived successors must contain unique Todo ids");
  }
  return successors;
}

function successorCandidate(
  todo: JsonObject,
  actor: string | null,
  now: Date,
  domainReadModel: boolean,
  completionPolicy: JsonObject | null,
): JsonObject {
  const policyOwned: JsonObject = todo.role === "agent" && completionPolicy !== null ? {
    ...(completionPolicy.effective_next_claimed_by === null ? {} : {claimed_by: completionPolicy.effective_next_claimed_by}),
    excluded_agents: completionPolicy.effective_next_excluded_agents,
  } : {};
  const candidate: JsonObject = {
    ...todo,
    ...policyOwned,
    schema_version: TODO_DOMAIN_ITEM_SCHEMA,
    created_by: todo.created_by ?? actor,
    last_actor_agent_id: actor,
    updated_at: now.toISOString().replace(/\.\d{3}Z$/u, "Z"),
  };
  if (todo.role === "agent" && completionPolicy !== null && completionPolicy.effective_next_claimed_by === null) {
    delete candidate.claimed_by;
  }
  const created = canonicalTodoDomainRecord(candidate, "terminal successor");
  return domainReadModel ? created : materializeTodoRecordForSchema(
    created,
    TODO_CANONICAL_READ_RECORD_SCHEMA,
    "terminal successor compatibility record",
  );
}

function semanticDuplicate(
  todos: Iterable<JsonObject>,
  successor: JsonObject,
  retiringTodoId: string | null = null,
): JsonObject | undefined {
  for (const todo of todos) {
    if (todo.todo_id !== retiringTodoId && todo.role === successor.role &&
        todo.archive_state === "active" && todo.status !== "done" &&
        todo.status !== "deferred" && todo.text === successor.text) return todo;
  }
  return undefined;
}

export type TerminalSuccessorsPreparation =
  | {
      readonly status: "ready";
      readonly successorCandidates: JsonObject[];
      readonly successorIds: string[];
      readonly generatedSuccessorIds: string[];
    }
  | {
      readonly status: "failed";
      readonly failureKind: "decision_rejection" | "protocol_failure";
      readonly reasonCode: string;
      readonly reason: string;
      readonly detail?: JsonObject;
    };

export function prepareCoordinationTodoTerminalSuccessors(params: {
  readonly command: TerminalCommand;
  readonly todoId: string;
  readonly todo: JsonObject;
  readonly todos: ReadonlyMap<string, JsonObject>;
  readonly registeredAgents: readonly string[];
  readonly actorAgentId: string | null;
  readonly linkedSuccessorTodoIds: readonly string[];
  readonly successorIntents: readonly JsonObject[];
  readonly completionPolicy: JsonObject | null;
  readonly completionPolicyRequest: JsonObject | null;
  readonly domainReadModel: boolean;
  readonly now: Date;
}): TerminalSuccessorsPreparation {
  const {
    command, todoId, todo, todos, registeredAgents, actorAgentId, linkedSuccessorTodoIds,
    successorIntents, completionPolicy, completionPolicyRequest, domainReadModel, now,
  } = params;
  const fail = (
    reasonCode: string,
    reason: string,
    detail?: JsonObject,
    failureKind: "decision_rejection" | "protocol_failure" = "protocol_failure",
  ): TerminalSuccessorsPreparation => ({
    status: "failed",
    failureKind,
    reasonCode,
    reason,
    ...(detail === undefined ? {} : {detail}),
  });
  let successors: JsonObject[];
  try {
    const proposals = deriveCoordinationTodoSuccessorProposals({
      schema_version: TODO_SUCCESSOR_DERIVATION_REQUEST_SCHEMA,
      command,
      predecessor: todo,
      registered_agents: registeredAgents,
      actor_agent_id: actorAgentId,
      completion_policy: completionPolicy,
      successor_intents: successorIntents,
    });
    successors = materializeSuccessorProposals(todoId, proposals);
    validateSuccessorSemantics(successors, registeredAgents);
  } catch (error) {
    return fail(
      "invalid_todo_successor_derivation",
      error instanceof Error ? error.message : "invalid Todo successor derivation",
      undefined,
      "decision_rejection",
    );
  }
  const generatedSuccessorIds = successors.map((successor) => String(successor.todo_id));
  const successorIds = [...new Set([...linkedSuccessorTodoIds, ...generatedSuccessorIds])];
  if (successorIds.includes(todoId)) {
    return fail("todo_successor_cycle", "Todo cannot name itself as a successor");
  }
  if (completionPolicy !== null) {
    const agentSuccessorIntents = successorIntents.filter((successor) => successor.role === "agent");
    if (agentSuccessorIntents.length > 1) {
      return fail(
        "completion_policy_successor_ambiguity",
        "completion policy currently permits at most one generated Agent successor",
      );
    }
    const requestedNextAgentTodo = completionPolicyRequest?.next_agent_todo;
    if ((requestedNextAgentTodo === null || requestedNextAgentTodo === undefined) !==
          (agentSuccessorIntents.length === 0) ||
        (agentSuccessorIntents.length === 1 && requestedNextAgentTodo !== agentSuccessorIntents[0]!.text)) {
      return fail(
        "completion_policy_successor_mismatch",
        "completion policy next_agent_todo must identify the generated Agent successor",
      );
    }
  }
  const userSuccessor = successors.find((successor) => successor.role === "user");
  if (command === "complete" && registeredAgents.length > 1 && userSuccessor !== undefined) {
    const completingAgent = completionPolicy?.effective_claimed_by;
    if (typeof completingAgent !== "string" || userSuccessor.bound_agent !== completingAgent ||
        (userSuccessor.task_class === "user_gate" && userSuccessor.blocks_agent !== completingAgent)) {
      return fail(
        "completion_user_successor_binding_mismatch",
        "multi-agent completion must bind its User successor to the effective completing Agent",
      );
    }
  }
  const successorCandidates: JsonObject[] = [];
  for (const successor of successors) {
    const successorId = String(successor.todo_id);
    if (todos.has(successorId)) {
      return fail("todo_already_exists", "terminal successor Todo id already exists", {todo_id: successorId});
    }
    const duplicate = semanticDuplicate(todos.values(), successor, todoId);
    if (duplicate !== undefined) {
      return fail(
        "todo_semantic_duplicate_conflict",
        "an active Todo with the same role/text already exists",
        {todo_id: duplicate.todo_id},
      );
    }
    successorCandidates.push(successorCandidate(successor, actorAgentId, now, domainReadModel, completionPolicy));
  }
  return {status: "ready", successorCandidates, successorIds, generatedSuccessorIds};
}
