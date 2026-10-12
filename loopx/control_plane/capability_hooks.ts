import type { JsonObject } from "./effect_program.ts";
import {
  CAPABILITY_HOOK_INTENT_SCHEMA,
  CAPABILITY_HOOK_INTERACTION_RESULT_SCHEMA,
  CAPABILITY_HOOK_POST_WRITEBACK_INPUT_SCHEMA,
  CAPABILITY_HOOK_POST_WRITEBACK_RECEIPT_SCHEMA,
  CAPABILITY_HOOK_POST_WRITEBACK_REGISTRATION_SCHEMA,
  CAPABILITY_HOOK_POST_WRITEBACK_RESULT_SCHEMA,
  CAPABILITY_HOOK_REGISTRATION_SCHEMA,
  CAPABILITY_HOOK_TURN_START_REGISTRATION_SCHEMA,
  CAPABILITY_HOOK_TURN_START_RESULT_SCHEMA,
} from "./coordination/coordination_state_contract.generated.ts";
import {
  requireBoolean,
  requireInteger,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
  requireStringArray,
} from "./runtime_decode.ts";
import { projectRepositoryDeliveryGate } from "./work_items/repository_delivery.ts";
import { projectPendingCapabilityIntent } from "./work_items/pending_capability_intent.ts";

import {exploreResultWritebackAffordance} from "./capabilities/explore_result_writeback.ts";

export const CAPABILITY_HOOK_REGISTRATION_SCHEMA_VERSION =
  CAPABILITY_HOOK_REGISTRATION_SCHEMA;
export const INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION =
  CAPABILITY_HOOK_INTERACTION_RESULT_SCHEMA;
export const TURN_START_HOOK_REGISTRATION_SCHEMA_VERSION =
  CAPABILITY_HOOK_TURN_START_REGISTRATION_SCHEMA;
export const TURN_START_HOOK_RESULT_SCHEMA_VERSION =
  CAPABILITY_HOOK_TURN_START_RESULT_SCHEMA;
export const POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION =
  CAPABILITY_HOOK_POST_WRITEBACK_REGISTRATION_SCHEMA;
export const POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION =
  CAPABILITY_HOOK_POST_WRITEBACK_INPUT_SCHEMA;
export const POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION =
  CAPABILITY_HOOK_POST_WRITEBACK_RESULT_SCHEMA;
export const POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION =
  CAPABILITY_HOOK_POST_WRITEBACK_RECEIPT_SCHEMA;
export const CAPABILITY_INTENT_SCHEMA_VERSION = CAPABILITY_HOOK_INTENT_SCHEMA;

const REGISTRATION_FIELDS = new Set([
  "schema_version",
  "hook_id",
  "capability_id",
  "phase",
  "projection_slots",
  "budget",
  "failure_policy",
  "requested_read_scope",
  "requested_write_scope",
]);
const BUDGET_FIELDS = new Set([
  "max_invocations_per_dispatch",
  "max_result_bytes",
]);
const RESULT_FIELDS = new Set([
  "schema_version",
  "hook_id",
  "capability_id",
  "phase",
  "status",
  "projection_slot",
  "payload",
]);
const TURN_START_REGISTRATION_FIELDS = new Set([
  "schema_version",
  "hook_id",
  "capability_id",
  "phase",
  "budget",
  "failure_policy",
  "requested_read_scope",
  "requested_write_scope",
  "required_read",
]);
const TURN_START_REQUIRED_READ_FIELDS = new Set([
  "kind",
  "command",
  "reason",
  "ordering",
]);
const TURN_START_RESULT_FIELDS = new Set([
  "schema_version",
  "hook_id",
  "capability_id",
  "phase",
  "status",
  "observation_count",
  "agent_read_required",
  "external_reads_performed",
  "external_writes_performed",
  "local_private_state_mutated",
  "private_content_returned",
  "provider_payload_returned",
  "error_code",
]);
const POST_WRITEBACK_REGISTRATION_FIELDS = new Set([
  "schema_version",
  "hook_id",
  "capability_id",
  "policy_version",
  "phase",
  "event_kinds",
  "intent_kinds",
  "budget",
  "failure_policy",
  "requested_read_scope",
  "requested_write_scope",
]);
const POST_WRITEBACK_BUDGET_FIELDS = new Set([
  "max_invocations_per_dispatch",
  "max_input_bytes",
  "max_result_bytes",
]);
const POST_WRITEBACK_INPUT_FIELDS = new Set([
  "schema_version",
  "receipt",
  "identity",
  "state_version",
  "projection",
]);
const POST_WRITEBACK_RECEIPT_FIELDS = new Set([
  "schema_version",
  "event_id",
  "event_kind",
  "status",
  "recorded_at",
  "durable",
]);
const POST_WRITEBACK_IDENTITY_FIELDS = new Set([
  "goal_id",
  "agent_id",
  "todo_id",
  "turn_instance_id",
  "effect_id",
]);
const POST_WRITEBACK_RESULT_FIELDS = new Set([
  "schema_version",
  "hook_id",
  "capability_id",
  "phase",
  "status",
  "intent",
]);
const CAPABILITY_INTENT_FIELDS = new Set([
  "schema_version",
  "intent_kind",
  "idempotency_key",
  "source_receipt_id",
  "payload",
  "requested_write_scope",
]);
const POST_WRITEBACK_SIDECAR_RECEIPT_FIELDS = new Set([
  "schema_version",
  "dispatch_id",
  "hook_id",
  "capability_id",
  "source_receipt_id",
  "status",
  "intent",
  "error_code",
  "attempt_count",
  "recorded_at",
]);
const TURN_START_WRITE_SCOPES = new Set([
  "agent_private_capability_memory",
  "owner_private_inbox",
  "owner_private_cursor",
  "provider_message_reaction",
]);
const TURN_START_STATUSES = new Set([
  "not_applicable",
  "observed",
  "empty",
  "partial",
  "unavailable",
  "failed",
]);
const TOKEN_RE = /^[a-z][a-z0-9_.:-]{2,95}$/;
const POLICY_VERSION_RE = /^[a-z0-9][a-z0-9_.:-]{1,95}$/;

function requireExactFields(
  value: JsonObject,
  expected: ReadonlySet<string>,
  label: string,
): void {
  const fields = Object.keys(value);
  if (
    fields.length !== expected.size ||
    fields.some((field) => !expected.has(field))
  ) {
    throw new Error(`${label} fields are invalid`);
  }
}

function boundedTokens(
  value: unknown,
  label: string,
  limit: number,
): string[] {
  const tokens = requireStringArray(value, label);
  if (
    tokens.length > limit ||
    new Set(tokens).size !== tokens.length ||
    tokens.some((token) => !TOKEN_RE.test(token))
  ) {
    throw new Error(`${label} contains invalid tokens`);
  }
  return tokens;
}

export function validateInteractionProjectionHookRegistration(
  value: unknown,
): JsonObject & {
  hook_id: string;
  capability_id: string;
  projection_slots: string[];
  max_result_bytes: number;
} {
  const registration = requiredObject(value, "capability hook registration");
  requireExactFields(
    registration,
    REGISTRATION_FIELDS,
    "capability hook registration",
  );
  if (
    registration.schema_version !==
      CAPABILITY_HOOK_REGISTRATION_SCHEMA_VERSION ||
    registration.phase !== "interaction_projection" ||
    registration.failure_policy !== "isolate"
  ) {
    throw new Error("capability hook registration contract is invalid");
  }
  const hookId = requiredString(registration.hook_id, "capability hook hook_id");
  const capabilityId = requiredString(
    registration.capability_id,
    "capability hook capability_id",
  );
  if (!TOKEN_RE.test(hookId) || !TOKEN_RE.test(capabilityId)) {
    throw new Error("capability hook identity is invalid");
  }
  const projectionSlots = boundedTokens(
    registration.projection_slots,
    "capability hook projection_slots",
    8,
  );
  if (projectionSlots.length === 0) {
    throw new Error("capability hook projection_slots cannot be empty");
  }
  boundedTokens(
    registration.requested_read_scope,
    "capability hook requested_read_scope",
    16,
  );
  const writeScope = boundedTokens(
    registration.requested_write_scope,
    "capability hook requested_write_scope",
    16,
  );
  if (writeScope.length > 0) {
    throw new Error("interaction projection hooks cannot request write scope");
  }
  const budget = requiredObject(registration.budget, "capability hook budget");
  requireExactFields(budget, BUDGET_FIELDS, "capability hook budget");
  const maxInvocations = requireInteger(
    budget.max_invocations_per_dispatch,
    "capability hook max_invocations_per_dispatch",
  );
  const maxResultBytes = requireInteger(
    budget.max_result_bytes,
    "capability hook max_result_bytes",
  );
  if (maxInvocations !== 1 || maxResultBytes < 1024 || maxResultBytes > 65_536) {
    throw new Error("capability hook budget is outside the admitted envelope");
  }
  return {
    ...registration,
    hook_id: hookId,
    capability_id: capabilityId,
    projection_slots: projectionSlots,
    max_result_bytes: maxResultBytes,
  };
}

export function validateInteractionProjectionHookInvocation(input: {
  registration: unknown;
  result: unknown;
}): JsonObject {
  const registration = validateInteractionProjectionHookRegistration(
    input.registration,
  );
  const result = requiredObject(input.result, "capability hook result");
  requireExactFields(result, RESULT_FIELDS, "capability hook result");
  if (
    result.schema_version !== INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION ||
    result.hook_id !== registration.hook_id ||
    result.capability_id !== registration.capability_id ||
    result.phase !== "interaction_projection"
  ) {
    throw new Error("capability hook result identity is invalid");
  }
  if (
    new TextEncoder().encode(JSON.stringify(result)).byteLength >
      registration.max_result_bytes
  ) {
    throw new Error("capability hook result exceeds its budget");
  }
  if (result.status === "not_applicable") {
    if (result.projection_slot !== null || result.payload !== null) {
      throw new Error("not-applicable capability hook result must be empty");
    }
    return {
      schema_version: INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
      hook_id: registration.hook_id,
      capability_id: registration.capability_id,
      phase: "interaction_projection",
      status: "not_applicable",
      projection_slot: null,
      projection: null,
    };
  }
  if (result.status !== "candidate") {
    throw new Error("capability hook result status is invalid");
  }
  const slot = requiredString(
    result.projection_slot,
    "capability hook projection_slot",
  );
  if (!registration.projection_slots.includes(slot)) {
    throw new Error("capability hook projection_slot is not registered");
  }
  let projection: JsonObject | null;
  switch (slot) {
    case "pending_capability_intent":
      projection = projectPendingCapabilityIntent(result.payload);
      break;
    case "explore_result_attachment":
      if (registration.capability_id !== "explore") {
        throw new Error("Explore attachment projection requires its owning capability");
      }
      // The capability supplies presence only; shared typed guidance cannot be
      // replaced by provider text or converted into a mandatory work gate.
      if (Object.keys(requiredObject(result.payload, "Explore attachment presence")).length) {
        throw new Error("Explore attachment presence must be empty");
      }
      projection = exploreResultWritebackAffordance();
      break;
    case "repository_delivery":
      projection = projectRepositoryDeliveryGate(result.payload);
      break;
    default:
      throw new Error("capability hook projection_slot is unsupported");
  }
  return {
    schema_version: INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
    hook_id: registration.hook_id,
    capability_id: registration.capability_id,
    phase: "interaction_projection",
    status: projection ? "projected" : "not_applicable",
    projection_slot: projection ? slot : null,
    projection,
  };
}

/** Optional per-read prompt allowance; never execution or effect authority. */
export function turnStartPromptBudgetBytes(value: unknown): number {
  if (value === undefined) return 0;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1 || value > 2_048) {
    throw new Error("turn-start prompt budget must be an integer from 1 to 2048 bytes");
  }
  return value;
}

export function validateTurnStartHookRegistration(
  value: unknown,
): JsonObject & {
  hook_id: string;
  capability_id: string;
  max_result_bytes: number;
  requested_write_scope: string[];
  required_read: JsonObject | null;
} {
  const registration = requiredObject(value, "turn-start hook registration");
  requireExactFields(
    registration,
    TURN_START_REGISTRATION_FIELDS,
    "turn-start hook registration",
  );
  if (
    registration.schema_version !== TURN_START_HOOK_REGISTRATION_SCHEMA_VERSION ||
    registration.phase !== "turn_start" ||
    registration.failure_policy !== "isolate"
  ) {
    throw new Error("turn-start hook registration contract is invalid");
  }
  const hookId = requiredString(registration.hook_id, "turn-start hook hook_id");
  const capabilityId = requiredString(
    registration.capability_id,
    "turn-start hook capability_id",
  );
  if (!TOKEN_RE.test(hookId) || !TOKEN_RE.test(capabilityId)) {
    throw new Error("turn-start hook identity is invalid");
  }
  boundedTokens(
    registration.requested_read_scope,
    "turn-start hook requested_read_scope",
    16,
  );
  const writeScope = boundedTokens(
    registration.requested_write_scope,
    "turn-start hook requested_write_scope",
    8,
  );
  if (writeScope.some((scope) => !TURN_START_WRITE_SCOPES.has(scope))) {
    throw new Error("turn-start hook requested_write_scope is not admitted");
  }
  const budget = requiredObject(registration.budget, "turn-start hook budget");
  requireExactFields(budget, BUDGET_FIELDS, "turn-start hook budget");
  const maxInvocations = requireInteger(
    budget.max_invocations_per_dispatch,
    "turn-start hook max_invocations_per_dispatch",
  );
  const maxResultBytes = requireInteger(
    budget.max_result_bytes,
    "turn-start hook max_result_bytes",
  );
  if (maxInvocations !== 1 || maxResultBytes < 1024 || maxResultBytes > 65_536) {
    throw new Error("turn-start hook budget is outside the admitted envelope");
  }
  let requiredRead: JsonObject | null = null;
  if (registration.required_read !== null) {
    const candidate = requiredObject(
      registration.required_read,
      "turn-start hook required_read",
    );
    const readFields = new Set(TURN_START_REQUIRED_READ_FIELDS);
    if ("prompt_budget_bytes" in candidate) readFields.add("prompt_budget_bytes");
    requireExactFields(candidate, readFields, "turn-start hook required_read");
    const promptBudget = turnStartPromptBudgetBytes(candidate.prompt_budget_bytes);
    const kind = requiredString(candidate.kind, "turn-start hook required_read kind");
    const command = requiredString(
      candidate.command,
      "turn-start hook required_read command",
    ).trim();
    const reason = requiredString(
      candidate.reason,
      "turn-start hook required_read reason",
    ).trim();
    const containsControlCharacter = /[\u0000-\u001f\u007f]/;
    if (
      !TOKEN_RE.test(kind) ||
      // Bound explicit registry/runtime routes too; two legitimate absolute
      // paths can exceed the old 360-byte display-oriented budget.
      new TextEncoder().encode(command).byteLength > 1024 ||
      reason.length > 240 ||
      containsControlCharacter.test(command) ||
      containsControlCharacter.test(reason)
    ) {
      throw new Error("turn-start hook required_read is outside the admitted envelope");
    }
    if (candidate.ordering !== "before_work") {
      throw new Error("turn-start hook required_read ordering is invalid");
    }
    requiredRead = { kind, command, reason, ordering: "before_work" };
    if (promptBudget) {
      requiredRead.prompt_budget_bytes = promptBudget;
      if (Buffer.byteLength(JSON.stringify(requiredRead), "utf8") > promptBudget) {
        throw new Error("turn-start required read exceeds its declared prompt budget");
      }
    }
  }
  return {
    ...registration,
    hook_id: hookId,
    capability_id: capabilityId,
    max_result_bytes: maxResultBytes,
    requested_write_scope: writeScope,
    required_read: requiredRead,
  };
}

export function validateTurnStartHookInvocation(input: {
  registration: unknown;
  result: unknown;
}): JsonObject {
  const registration = validateTurnStartHookRegistration(input.registration);
  const result = requiredObject(input.result, "turn-start hook result");
  requireExactFields(result, TURN_START_RESULT_FIELDS, "turn-start hook result");
  if (
    result.schema_version !== TURN_START_HOOK_RESULT_SCHEMA_VERSION ||
    result.hook_id !== registration.hook_id ||
    result.capability_id !== registration.capability_id ||
    result.phase !== "turn_start"
  ) {
    throw new Error("turn-start hook result identity is invalid");
  }
  if (
    new TextEncoder().encode(JSON.stringify(result)).byteLength >
      registration.max_result_bytes
  ) {
    throw new Error("turn-start hook result exceeds its budget");
  }
  const status = requiredString(result.status, "turn-start hook status");
  if (!TURN_START_STATUSES.has(status)) {
    throw new Error("turn-start hook result status is invalid");
  }
  const observationCount = requireInteger(
    result.observation_count,
    "turn-start hook observation_count",
  );
  if (observationCount < 0 || observationCount > 10_000) {
    throw new Error("turn-start hook observation_count is invalid");
  }
  for (const field of [
    "agent_read_required",
    "external_reads_performed",
    "external_writes_performed",
    "local_private_state_mutated",
    "private_content_returned",
    "provider_payload_returned",
  ]) {
    if (typeof result[field] !== "boolean") {
      throw new Error(`turn-start hook ${field} must be boolean`);
    }
  }
  if (result.private_content_returned || result.provider_payload_returned) {
    throw new Error("turn-start hook cannot return private provider content");
  }
  if (
    result.local_private_state_mutated &&
    registration.requested_write_scope.length === 0
  ) {
    throw new Error("turn-start hook mutated undeclared local-private state");
  }
  if (
    result.external_writes_performed &&
    !registration.requested_write_scope.includes("provider_message_reaction")
  ) {
    throw new Error("turn-start hook performed an undeclared external write");
  }
  const errorCode = result.error_code;
  if (errorCode !== null && (typeof errorCode !== "string" || !TOKEN_RE.test(errorCode))) {
    throw new Error("turn-start hook error_code is invalid");
  }
  if (status === "observed" && (
    observationCount === 0 ||
    errorCode !== null ||
    result.agent_read_required !== true
  )) {
    throw new Error("observed turn-start hook result is inconsistent");
  }
  if (status === "empty" && (
    observationCount !== 0 ||
    errorCode !== null ||
    result.agent_read_required
  )) {
    throw new Error("empty turn-start hook result is inconsistent");
  }
  if (status === "not_applicable" && (
    observationCount !== 0 ||
    result.agent_read_required ||
    result.external_reads_performed ||
    result.external_writes_performed ||
    result.local_private_state_mutated ||
    errorCode !== null
  )) {
    throw new Error("not-applicable turn-start hook result must be empty");
  }
  if (["partial", "unavailable", "failed"].includes(status) && errorCode === null) {
    throw new Error("failed turn-start hook result requires error_code");
  }
  if (status === "partial" && observationCount > 0 && !result.agent_read_required) {
    throw new Error("partial turn-start observations require Agent reading");
  }
  if (["unavailable", "failed"].includes(status) && result.agent_read_required) {
    throw new Error("unavailable turn-start hook cannot require unread evidence");
  }
  if (result.agent_read_required && registration.required_read === null) {
    throw new Error("turn-start hook required read route is missing");
  }
  return { ...result };
}

/** Transport failed context observations to the executor, not only diagnostics.
 * Missing context never means empty context or a grant to use a stale cache.
 */
export function projectTurnStartUnavailableContext(value: unknown): JsonObject | null {
  if (value === undefined || value === null) return null;
  const dispatch = requiredObject(value, "turn-start dispatch");
  const affected: JsonObject[] = [];
  for (const field of ["results", "failures"] as const) {
    const rows = dispatch[field] ?? [];
    if (!Array.isArray(rows)) throw new TypeError(`turn-start ${field} must be an array`);
    for (const raw of rows) {
      const row = requiredObject(raw, `turn-start ${field} entry`);
      const status = field === "failures" ? "failed" : row.status;
      if (!["partial", "unavailable", "failed"].includes(String(status))) continue;
      const identity: JsonObject = {};
      for (const key of ["hook_id", "capability_id", "error_code"] as const) {
        const token = requiredString(row[key], `turn-start ${key}`);
        if (token.length > 160 || !TOKEN_RE.test(token)) throw new TypeError(`invalid turn-start ${key}`);
        identity[key] = token;
      }
      affected.push({...identity, status});
    }
  }
  if (affected.length === 0) return null;
  return {
    affected_hooks: affected,
    cache_policy: "invalidate_affected_hook_context",
    dependent_action_policy: "hold_until_fresh_context",
    independent_work_policy: "preserve_existing_authority",
  };
}

export function validatePostWritebackHookRegistration(
  value: unknown,
): JsonObject & {
  hook_id: string;
  capability_id: string;
  policy_version: string;
  event_kinds: string[];
  intent_kinds: string[];
  requested_read_scope: string[];
  max_input_bytes: number;
  max_result_bytes: number;
} {
  const registration = requiredObject(value, "post-writeback hook registration");
  requireExactFields(
    registration,
    POST_WRITEBACK_REGISTRATION_FIELDS,
    "post-writeback hook registration",
  );
  if (
    registration.schema_version !== POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION ||
    registration.phase !== "post_writeback" ||
    registration.failure_policy !== "isolate"
  ) {
    throw new Error("post-writeback hook registration contract is invalid");
  }
  const hookId = requiredString(registration.hook_id, "post-writeback hook hook_id");
  const capabilityId = requiredString(
    registration.capability_id,
    "post-writeback hook capability_id",
  );
  const policyVersion = requiredString(
    registration.policy_version,
    "post-writeback hook policy_version",
  );
  if (
    !TOKEN_RE.test(hookId) ||
    !TOKEN_RE.test(capabilityId) ||
    !POLICY_VERSION_RE.test(policyVersion)
  ) {
    throw new Error("post-writeback hook identity is invalid");
  }
  const eventKinds = boundedTokens(
    registration.event_kinds,
    "post-writeback hook event_kinds",
    16,
  );
  const intentKinds = boundedTokens(
    registration.intent_kinds,
    "post-writeback hook intent_kinds",
    16,
  );
  const requestedReadScope = boundedTokens(
    registration.requested_read_scope,
    "post-writeback hook requested_read_scope",
    16,
  );
  if (eventKinds.length === 0 || intentKinds.length === 0) {
    throw new Error("post-writeback hook kinds cannot be empty");
  }
  const writeScope = boundedTokens(
    registration.requested_write_scope,
    "post-writeback hook requested_write_scope",
    8,
  );
  if (writeScope.length > 0) {
    throw new Error("post-writeback hooks cannot request write scope");
  }
  const budget = requiredObject(registration.budget, "post-writeback hook budget");
  requireExactFields(budget, POST_WRITEBACK_BUDGET_FIELDS, "post-writeback hook budget");
  const maxInvocations = requireInteger(
    budget.max_invocations_per_dispatch,
    "post-writeback hook max_invocations_per_dispatch",
  );
  const maxInputBytes = requireInteger(
    budget.max_input_bytes,
    "post-writeback hook max_input_bytes",
  );
  const maxResultBytes = requireInteger(
    budget.max_result_bytes,
    "post-writeback hook max_result_bytes",
  );
  if (
    maxInvocations !== 1 ||
    maxInputBytes < 1024 ||
    maxInputBytes > 262_144 ||
    maxResultBytes < 1024 ||
    maxResultBytes > 65_536
  ) {
    throw new Error("post-writeback hook budget is outside the admitted envelope");
  }
  return {
    ...registration,
    hook_id: hookId,
    capability_id: capabilityId,
    policy_version: policyVersion,
    event_kinds: eventKinds,
    intent_kinds: intentKinds,
    requested_read_scope: requestedReadScope,
    max_input_bytes: maxInputBytes,
    max_result_bytes: maxResultBytes,
  };
}

export function validatePostWritebackHookInput(input: {
  registration: unknown;
  hook_input: unknown;
}): JsonObject {
  const registration = validatePostWritebackHookRegistration(input.registration);
  const hookInput = requiredObject(input.hook_input, "post-writeback hook input");
  requireExactFields(hookInput, POST_WRITEBACK_INPUT_FIELDS, "post-writeback hook input");
  if (hookInput.schema_version !== POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION) {
    throw new Error("post-writeback hook input schema is invalid");
  }
  if (
    new TextEncoder().encode(JSON.stringify(hookInput)).byteLength >
      registration.max_input_bytes
  ) {
    throw new Error("post-writeback hook input exceeds its budget");
  }
  const receipt = requiredObject(hookInput.receipt, "post-writeback receipt");
  requireExactFields(receipt, POST_WRITEBACK_RECEIPT_FIELDS, "post-writeback receipt");
  const eventKind = requiredString(receipt.event_kind, "post-writeback receipt event_kind");
  if (
    !registration.event_kinds.includes(eventKind) ||
    requireBoolean(receipt.durable, "post-writeback receipt durable") !== true ||
    !["appended", "committed", "receipt_repaired"].includes(
      requiredString(receipt.status, "post-writeback receipt status"),
    )
  ) {
    throw new Error("post-writeback receipt is not an admitted durable event");
  }
  requiredString(receipt.schema_version, "post-writeback receipt schema_version");
  requiredString(receipt.event_id, "post-writeback receipt event_id");
  requiredString(receipt.recorded_at, "post-writeback receipt recorded_at");
  const identity = requiredObject(hookInput.identity, "post-writeback identity");
  requireExactFields(identity, POST_WRITEBACK_IDENTITY_FIELDS, "post-writeback identity");
  for (const field of POST_WRITEBACK_IDENTITY_FIELDS) {
    if (field === "todo_id") {
      if (identity[field] !== null) {
        requiredString(identity[field], `post-writeback identity ${field}`);
      }
      continue;
    }
    requiredString(identity[field], `post-writeback identity ${field}`);
  }
  requiredString(hookInput.state_version, "post-writeback state_version");
  const projection = requiredObject(hookInput.projection, "post-writeback projection");
  if (Object.keys(projection).some((field) => !registration.requested_read_scope.includes(field))) {
    throw new Error("post-writeback projection exceeds requested_read_scope");
  }
  return { ...hookInput, receipt, identity, projection };
}

export function validatePostWritebackHookInvocation(input: {
  registration: unknown;
  hook_input: unknown;
  result: unknown;
}): JsonObject {
  const registration = validatePostWritebackHookRegistration(input.registration);
  const hookInput = validatePostWritebackHookInput({
    registration: input.registration,
    hook_input: input.hook_input,
  });
  const result = requiredObject(input.result, "post-writeback hook result");
  requireExactFields(result, POST_WRITEBACK_RESULT_FIELDS, "post-writeback hook result");
  if (
    result.schema_version !== POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION ||
    result.hook_id !== registration.hook_id ||
    result.capability_id !== registration.capability_id ||
    result.phase !== "post_writeback"
  ) {
    throw new Error("post-writeback hook result identity is invalid");
  }
  if (
    new TextEncoder().encode(JSON.stringify(result)).byteLength >
      registration.max_result_bytes
  ) {
    throw new Error("post-writeback hook result exceeds its budget");
  }
  if (result.status === "not_applicable") {
    if (result.intent !== null) {
      throw new Error("not-applicable post-writeback hook result must be empty");
    }
    return { ...result };
  }
  if (result.status !== "intent") {
    throw new Error("post-writeback hook result status is invalid");
  }
  const intent = requiredObject(result.intent, "post-writeback capability intent");
  requireExactFields(intent, CAPABILITY_INTENT_FIELDS, "post-writeback capability intent");
  if (intent.schema_version !== CAPABILITY_INTENT_SCHEMA_VERSION) {
    throw new Error("post-writeback capability intent schema is invalid");
  }
  const intentKind = requiredString(intent.intent_kind, "capability intent intent_kind");
  if (!registration.intent_kinds.includes(intentKind)) {
    throw new Error("capability intent kind is not registered");
  }
  requiredString(intent.idempotency_key, "capability intent idempotency_key");
  if (intent.source_receipt_id !== (hookInput.receipt as JsonObject).event_id) {
    throw new Error("capability intent does not bind the durable source receipt");
  }
  requiredObject(intent.payload, "capability intent payload");
  const writeScope = boundedTokens(
    intent.requested_write_scope,
    "capability intent requested_write_scope",
    8,
  );
  if (writeScope.length > 0) {
    throw new Error("post-writeback capability intent cannot grant write scope");
  }
  return { ...result, intent: { ...intent, requested_write_scope: writeScope } };
}

export function validatePostWritebackHookReceipt(input: {
  registration: unknown;
  hook_input: unknown;
  receipt: unknown;
}): JsonObject {
  const registration = validatePostWritebackHookRegistration(input.registration);
  const hookInput = validatePostWritebackHookInput({
    registration: input.registration,
    hook_input: input.hook_input,
  });
  const receipt = requiredObject(input.receipt, "post-writeback sidecar receipt");
  requireExactFields(
    receipt,
    POST_WRITEBACK_SIDECAR_RECEIPT_FIELDS,
    "post-writeback sidecar receipt",
  );
  if (
    receipt.schema_version !== POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION ||
    receipt.hook_id !== registration.hook_id ||
    receipt.capability_id !== registration.capability_id ||
    !/^pwh_[0-9a-f]{64}$/.test(
      requiredString(receipt.dispatch_id, "post-writeback dispatch_id"),
    ) ||
    receipt.source_receipt_id !== (hookInput.receipt as JsonObject).event_id ||
    receipt.recorded_at !== (hookInput.receipt as JsonObject).recorded_at
  ) {
    throw new Error("post-writeback sidecar receipt identity is invalid");
  }
  const attemptCount = requireInteger(
    receipt.attempt_count,
    "post-writeback sidecar receipt attempt_count",
  );
  if (attemptCount < 1 || attemptCount > 10_000) {
    throw new Error("post-writeback sidecar receipt attempt_count is invalid");
  }
  const errorCode = receipt.error_code;
  if (
    errorCode !== null &&
    (typeof errorCode !== "string" || !TOKEN_RE.test(errorCode))
  ) {
    throw new Error("post-writeback sidecar receipt error_code is invalid");
  }
  if (receipt.status === "retryable_failure") {
    if (receipt.intent !== null || errorCode === null) {
      throw new Error("retryable sidecar receipt requires only error_code");
    }
    return { ...receipt, attempt_count: attemptCount };
  }
  if (receipt.status === "not_applicable") {
    if (receipt.intent !== null || errorCode !== null) {
      throw new Error("not-applicable sidecar receipt must be empty");
    }
    return { ...receipt, attempt_count: attemptCount };
  }
  if (receipt.status !== "intent_recorded" || errorCode !== null) {
    throw new Error("post-writeback sidecar receipt status is invalid");
  }
  const result = validatePostWritebackHookInvocation({
    registration: input.registration,
    hook_input: input.hook_input,
    result: {
      schema_version: POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
      hook_id: registration.hook_id,
      capability_id: registration.capability_id,
      phase: "post_writeback",
      status: "intent",
      intent: receipt.intent,
    },
  });
  return { ...receipt, intent: result.intent, attempt_count: attemptCount };
}
