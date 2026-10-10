import type { JsonObject } from "../effect_program.ts";
import {
  DELIVERY_WORKSPACE_CAUSALITY_REQUEST_SCHEMA,
  DELIVERY_WORKSPACE_CAUSALITY_RESULT_SCHEMA,
  DELIVERY_WORKSPACE_CAUSALITY_SCHEMA,
  DELIVERY_WORKSPACE_LEGACY_RECEIPT_EVIDENCE_SCHEMA,
  DELIVERY_WORKSPACE_RESOLUTION_SCHEMA,
  DELIVERY_WORKSPACE_SETTLEMENT_REQUIREMENT_SCHEMA,
} from "../coordination/coordination_state_contract.generated.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject as requiredObject } from "../runtime_decode.ts";
import { projectGoalWriteScopes } from "../todos/work_requirements.ts";

export const DELIVERY_WORKSPACE_CAUSALITY_SCHEMA_VERSION =
  DELIVERY_WORKSPACE_CAUSALITY_SCHEMA;
export { DELIVERY_WORKSPACE_CAUSALITY_REQUEST_SCHEMA, DELIVERY_WORKSPACE_CAUSALITY_RESULT_SCHEMA };
export const DELIVERY_WORKSPACE_RESOLUTION_SCHEMA_VERSION =
  DELIVERY_WORKSPACE_RESOLUTION_SCHEMA;
export const SETTLEMENT_WORKSPACE_REQUIREMENT_SCHEMA_VERSION =
  DELIVERY_WORKSPACE_SETTLEMENT_REQUIREMENT_SCHEMA;
export const LEGACY_SETTLEMENT_RECEIPT_EVIDENCE_SCHEMA_VERSION =
  DELIVERY_WORKSPACE_LEGACY_RECEIPT_EVIDENCE_SCHEMA;

export const DELIVERY_WORKSPACE_REQUIREMENTS = [
  "required",
  "not_required",
  "unknown",
] as const;

export type DeliveryWorkspaceRequirement =
  (typeof DELIVERY_WORKSPACE_REQUIREMENTS)[number];

export interface DeliveryWorkspaceCausality extends JsonObject {
  schema_version: typeof DELIVERY_WORKSPACE_CAUSALITY_SCHEMA_VERSION;
  todo_id: string;
  requirement: DeliveryWorkspaceRequirement;
  source: string;
  reason: string;
}

type DeliveryWorkspaceCausalityOperation =
  | "goal_write_scopes"
  | "classify"
  | "normalize"
  | "event_fields"
  | "missing_workspace"
  | "settlement_requirement"
  | "from_event";

export interface SettlementWorkspaceRequirement extends JsonObject {
  schema_version: typeof SETTLEMENT_WORKSPACE_REQUIREMENT_SCHEMA_VERSION;
  settlement_binding_kind: "todo" | "autonomous_replan";
  requirement: DeliveryWorkspaceRequirement;
  source: string;
  reason: string;
}

function hasCompleteLegacySettlementReceipts(value: unknown): boolean {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const evidence = value as JsonObject;
  if (
    evidence.schema_version !== LEGACY_SETTLEMENT_RECEIPT_EVIDENCE_SCHEMA_VERSION ||
    typeof evidence.settlement_effect_id !== "string" ||
    !evidence.settlement_effect_id.trim() ||
    evidence.delivery_workspace_present !== false ||
    !Array.isArray(evidence.receipts) ||
    evidence.receipts.length !== 2
  ) {
    return false;
  }
  const expectedKinds = new Set(["validation", "durable_writeback"]);
  for (const value of evidence.receipts) {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      return false;
    }
    const receipt = value as JsonObject;
    if (
      typeof receipt.step_kind !== "string" ||
      !expectedKinds.delete(receipt.step_kind) ||
      receipt.status !== "committed" ||
      receipt.effect_id !== evidence.settlement_effect_id
    ) {
      return false;
    }
  }
  return expectedKinds.size === 0;
}

export type DeliveryWorkspaceResolution =
  | {
      schema_version: typeof DELIVERY_WORKSPACE_RESOLUTION_SCHEMA_VERSION;
      todo_id: string;
      decision: "require_snapshot";
      requirement: "required";
      reason: "declared_workspace_snapshot_required";
    }
  | {
      schema_version: typeof DELIVERY_WORKSPACE_RESOLUTION_SCHEMA_VERSION;
      todo_id: string;
      decision: "omit_snapshot";
      requirement: "not_required";
      reason: "explicit_non_delivery_contract";
    }
  | {
      schema_version: typeof DELIVERY_WORKSPACE_RESOLUTION_SCHEMA_VERSION;
      todo_id: string;
      decision: "repair_contract";
      requirement: "unknown";
      reason: "todo_delivery_contract_not_explicit";
      accepted_resolutions: readonly [
        "declare_repository_or_write_contract",
        "declare_explicit_non_delivery_contract",
      ];
    };

function optionalObject(value: unknown, label: string): JsonObject | null {
  if (value === null || value === undefined) return null;
  return requiredObject(value, label);
}

function requiredString(value: unknown, label: string): string {
  if (typeof value !== "string") throw new EffectRuntimeRequestError(`${label} must be a string`);
  return value;
}

function optionalString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) return null;
  return requiredString(value, label);
}

function stringArray(value: unknown, label: string): readonly string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new EffectRuntimeRequestError(`${label} must be an array of strings`);
  }
  return value;
}

function operation(value: unknown): DeliveryWorkspaceCausalityOperation {
  if (
    value === "classify" || value === "normalize" ||
    value === "event_fields" || value === "missing_workspace" ||
    value === "settlement_requirement" ||
    value === "from_event" || value === "goal_write_scopes"
  ) return value;
  throw new EffectRuntimeRequestError("delivery workspace causality operation is unsupported");
}

export function resolveSettlementWorkspaceRequirement(
  value: unknown,
  settlementBindingKind: unknown,
  legacySettlementEvidence: unknown = null,
): SettlementWorkspaceRequirement {
  if (
    settlementBindingKind !== "todo" &&
    settlementBindingKind !== "autonomous_replan"
  ) {
    throw new EffectRuntimeRequestError(
      "settlement_binding_kind must be todo or autonomous_replan",
    );
  }
  const causality = normalizeDeliveryWorkspaceCausality(value, null);
  if (causality) {
    return {
      schema_version: SETTLEMENT_WORKSPACE_REQUIREMENT_SCHEMA_VERSION,
      settlement_binding_kind: settlementBindingKind,
      requirement: causality.requirement,
      source: causality.source,
      reason: causality.reason,
    };
  }
  if (settlementBindingKind === "autonomous_replan") {
    return {
      schema_version: SETTLEMENT_WORKSPACE_REQUIREMENT_SCHEMA_VERSION,
      settlement_binding_kind: settlementBindingKind,
      requirement: "not_required",
      source: "typed_settlement_identity",
      reason: "autonomous_replan_is_non_repository_control_plane_work",
    };
  }
  if (hasCompleteLegacySettlementReceipts(legacySettlementEvidence)) {
    return {
      schema_version: SETTLEMENT_WORKSPACE_REQUIREMENT_SCHEMA_VERSION,
      settlement_binding_kind: settlementBindingKind,
      requirement: "not_required",
      source: "legacy_settlement_receipts",
      reason: "pre_causality_settlement_already_committed",
    };
  }
  return {
    schema_version: SETTLEMENT_WORKSPACE_REQUIREMENT_SCHEMA_VERSION,
    settlement_binding_kind: settlementBindingKind,
    requirement: "unknown",
    source: "typed_settlement_identity",
    reason: "todo_delivery_contract_not_available",
  };
}

function requestObject(value: unknown): JsonObject {
  const request = requiredObject(value, "delivery workspace causality request");
  if (request.schema_version !== DELIVERY_WORKSPACE_CAUSALITY_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("delivery workspace causality request schema mismatch");
  }
  return request;
}

function result(values: JsonObject): JsonObject {
  return {
    schema_version: DELIVERY_WORKSPACE_CAUSALITY_RESULT_SCHEMA,
    ...values,
  };
}

export function classifyDeliveryWorkspaceCausality(
  value: unknown,
): DeliveryWorkspaceCausality | null {
  // Missing write metadata stays unknown. Only an explicit non-delivery
  // contract with no repository-write evidence can omit workspace validation.
  const contract = requiredObject(value, "normalized_todo_contract");
  const todoId = optionalString(contract.todo_id, "todo_id");
  if (!todoId) return null;
  const taskRepository = optionalString(
    contract.task_repository,
    "task_repository",
  );
  const writeScopes = stringArray(
    contract.required_write_scopes,
    "required_write_scopes",
  );
  const capabilities = stringArray(
    contract.required_capabilities,
    "required_capabilities",
  );
  const continuationPolicy = optionalString(
    contract.continuation_policy,
    "continuation_policy",
  );
  const source = requiredString(contract.source, "source");

  let requirement: DeliveryWorkspaceRequirement;
  let reason: string;
  if (
    taskRepository || writeScopes.length > 0 ||
    capabilities.includes("filesystem_write")
  ) {
    requirement = "required";
    reason = "declared_repository_or_write_contract";
  } else if (continuationPolicy === "same_agent_non_delivery") {
    requirement = "not_required";
    reason = "explicit_non_delivery_without_repository_writes";
  } else {
    requirement = "unknown";
    reason = "todo_write_contract_not_explicit";
  }
  return {
    schema_version: DELIVERY_WORKSPACE_CAUSALITY_SCHEMA_VERSION,
    todo_id: todoId,
    requirement,
    source,
    reason,
  };
}

export function resolveMissingDeliveryWorkspace(
  value: unknown,
): DeliveryWorkspaceResolution | null {
  const causality = normalizeDeliveryWorkspaceCausality(value, null);
  if (!causality) return null;
  const base = {
    schema_version: DELIVERY_WORKSPACE_RESOLUTION_SCHEMA_VERSION,
    todo_id: causality.todo_id,
  } as const;
  if (causality.requirement === "required") {
    return {
      ...base,
      decision: "require_snapshot",
      requirement: "required",
      reason: "declared_workspace_snapshot_required",
    };
  }
  if (causality.requirement === "not_required") {
    return {
      ...base,
      decision: "omit_snapshot",
      requirement: "not_required",
      reason: "explicit_non_delivery_contract",
    };
  }
  return {
    ...base,
    decision: "repair_contract",
    requirement: "unknown",
    reason: "todo_delivery_contract_not_explicit",
    accepted_resolutions: [
      "declare_repository_or_write_contract",
      "declare_explicit_non_delivery_contract",
    ],
  };
}

export function normalizeDeliveryWorkspaceCausality(
  value: unknown,
  expectedTodoId: string | null,
): DeliveryWorkspaceCausality | null {
  const candidate = optionalObject(value, "causality");
  if (
    candidate === null ||
    candidate.schema_version !== DELIVERY_WORKSPACE_CAUSALITY_SCHEMA_VERSION
  ) return null;
  const todoId = optionalString(candidate.todo_id, "causality.todo_id");
  if (!todoId || (expectedTodoId && todoId !== expectedTodoId)) return null;
  const rawRequirement = optionalString(
    candidate.requirement,
    "causality.requirement",
  );
  const requirement = DELIVERY_WORKSPACE_REQUIREMENTS.find(
    (item) => item === rawRequirement,
  );
  if (!requirement) return null;
  const source = optionalString(candidate.source, "causality.source")?.trim();
  const reason = optionalString(candidate.reason, "causality.reason")?.trim();
  if (!source || !reason) return null;
  return {
    schema_version: DELIVERY_WORKSPACE_CAUSALITY_SCHEMA_VERSION,
    todo_id: todoId,
    requirement,
    source,
    reason,
  };
}

export function deliveryWorkspaceCausalityEventFields(
  value: unknown,
): JsonObject {
  const causality = normalizeDeliveryWorkspaceCausality(value, null);
  if (!causality) return {};
  return {
    delivery_workspace_causality_schema_version: causality.schema_version,
    delivery_workspace_causality_todo_id: causality.todo_id,
    delivery_workspace_requirement: causality.requirement,
    delivery_workspace_causality_source: causality.source,
    delivery_workspace_causality_reason: causality.reason,
  };
}

export function evaluateDeliveryWorkspaceCausality(value: unknown): JsonObject {
  const request = requestObject(value);
  const selectedOperation = operation(request.operation);
  const expectedTodoId = optionalString(
    request.expected_todo_id,
    "expected_todo_id",
  );
  if (selectedOperation === "goal_write_scopes") {
    return result({ write_scope_projection: projectGoalWriteScopes(request) });
  }
  if (selectedOperation === "classify") {
    return result({
      causality: classifyDeliveryWorkspaceCausality(
        request.normalized_todo_contract,
      ),
    });
  }
  if (selectedOperation === "normalize") {
    return result({
      causality: normalizeDeliveryWorkspaceCausality(
        request.causality,
        expectedTodoId,
      ),
    });
  }
  if (selectedOperation === "event_fields") {
    return result({
      fields: deliveryWorkspaceCausalityEventFields(request.causality),
    });
  }
  if (selectedOperation === "missing_workspace") {
    return result({
      resolution: resolveMissingDeliveryWorkspace(request.causality),
    });
  }
  if (selectedOperation === "settlement_requirement") {
    return result({
      settlement_requirement: resolveSettlementWorkspaceRequirement(
        request.causality,
        request.settlement_binding_kind,
        request.legacy_settlement_evidence,
      ),
    });
  }
  const nested = normalizeDeliveryWorkspaceCausality(
    request.nested_causality,
    expectedTodoId,
  );
  return result({
    causality: (nested ?? normalizeDeliveryWorkspaceCausality(
      request.flat_causality,
      expectedTodoId,
    )),
  });
}
