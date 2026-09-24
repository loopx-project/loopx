import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { isBoundedBlockedRetry } from "../quota/blocked_retry.ts";
import {
  DELIVERY_BOUNDARIES,
  type DeliveryBoundary,
} from "../turn_driver/delivery_continuity.ts";
import {
  decodeOptionalDeliveryOutcome,
  isMaterialDeliveryOutcome,
  type DeliveryOutcome,
} from "../work_items/delivery_outcome.ts";

export const VISION_REFRESH_REQUEST_SCHEMA = "loopx_vision_refresh_request_v0";
export const VISION_REFRESH_PREPARED_SCHEMA_VERSION =
  "vision_refresh_prepared_v0";
export const VISION_CHECKPOINT_REQUEST_SCHEMA = VISION_REFRESH_REQUEST_SCHEMA;
export const VISION_CHECKPOINT_SCHEMA_VERSION = "vision_checkpoint_v0";

const GOAL_VISION_REPLAN_SCHEMA_VERSION = "goal_vision_replan_contract_v0";
const GOAL_PATH_DELTA_SCHEMA_VERSION = "goal_path_delta_v0";
const GOAL_VISION_BUDGET_ERROR = "vision_budget_exceeded";
// Direction and evidence-linked path changes share one bounded packet.
const GOAL_VISION_TOTAL_LIMIT = 1_800;
const GOAL_VISION_ADVANCEMENT_POLICIES = ["as_needed", "repeat_until_closed"] as const;
const VISION_UNCHANGED_REASON_LIMIT = 240;
const VISION_BUDGET_SUGGESTION_LIMIT = 96;

const GOAL_VISION_FIELD_LIMITS = {
  vision_summary: 420,
  role_scope: 280,
  acceptance_summary: 420,
  advancement_policy: 32,
  replan_trigger_summary: 240,
  dreaming_policy: 240,
  last_patch_summary: 240,
} as const;
const GOAL_VISION_DURABLE_FIELDS = [
  "vision_summary",
  "role_scope",
  "acceptance_summary",
  "advancement_policy",
] as const;
const GOAL_PATH_DELTA_OUTCOMES = [
  "ask_human",
  "continue",
  "no_change",
  "replan",
  "stop",
  "wait",
] as const;
const GOAL_PATH_DELTA_SCALAR_LIMITS = {
  prior_assumption: 320,
  observed_reality: 320,
  reentry_condition: 180,
} as const;
const GOAL_PATH_DELTA_LIST_LIMITS = {
  retained: [3, 120],
  changed: [3, 120],
  stopped: [3, 120],
  unresolved_questions: [2, 140],
  evidence_refs: [4, 140],
} as const;

/** Authoring hints share the validator's limits; they grant no transition authority. */
export function visionAuthoringContract(): JsonObject {
  return {
    schema_version: GOAL_VISION_REPLAN_SCHEMA_VERSION,
    fields: {state: "lifecycle token", vision_patch: {...GOAL_VISION_FIELD_LIMITS}},
    common_states: ["vision_patch_proposed", "vision_closed", "no_followup"],
    advancement_policies: [...GOAL_VISION_ADVANCEMENT_POLICIES],
    minimal_example: {schema_version: GOAL_VISION_REPLAN_SCHEMA_VERSION, state: "vision_patch_proposed", vision_patch: {
      vision_summary: "Scoped outcome", acceptance_summary: "Verified evidence and remaining gap",
    }},
    authoring_hint: "Fields are optional, not a checklist. Keep the whole decision compact; total includes path_delta. Do not copy the delivery evidence report into every field.",
    total_text_limit: GOAL_VISION_TOTAL_LIMIT,
    unchanged_reason_limit: VISION_UNCHANGED_REASON_LIMIT,
    path_delta: {
      schema_version: GOAL_PATH_DELTA_SCHEMA_VERSION,
      outcomes: [...GOAL_PATH_DELTA_OUTCOMES],
      required: ["outcome", "prior_assumption", "observed_reality"],
      require_any: ["retained", "changed", "stopped"],
      scalar_limits: {...GOAL_PATH_DELTA_SCALAR_LIMITS},
      list_limits: Object.fromEntries(Object.entries(GOAL_PATH_DELTA_LIST_LIMITS).map(
        ([field, [maxItems, maxChars]]) => [field, {item_type: "string", max_items: maxItems, max_item_chars: maxChars}],
      )),
    },
    rule: "Compare acceptance with evidence. vision_closed closes a stage, not the Goal; no_followup requires no remaining scoped work. A changed mainline needs path_delta; respect the live replan contract.",
  };
}
// Bounded typed fallback declarations survive prepare unchanged so the
// declared direction cannot disappear behind later read-model compaction.
const VISION_FALLBACK_DECLARATION_ENTRY_LIMIT = 4;
const VISION_FALLBACK_DECLARATION_ID_LIMIT = 120;
const VISION_FALLBACK_DECLARATION_FIELDS = [
  "target_todo_id",
  "successor_todo_id",
] as const;
const GOAL_VISION_STATE_ALIASES: Readonly<Record<string, string>> = {
  closed: "vision_closed",
  satisfied: "vision_closed",
  vision_satisfied: "vision_closed",
  vision_retired: "retired",
  vision_superseded: "superseded",
  vision_no_followup: "no_followup",
  closed_no_followup: "no_followup",
  no_follow_up: "no_followup",
};
// Mirrors loopx/public_safe_text.py. Both runtimes are pinned to the shared
// corpus in tests/fixtures/public_safe_text_corpus.json: ordinary governance
// prose such as "needs owner authorization" must pass, while the header,
// assignment, and quoted-JSON key credential shapes must be rejected.
const AUTHORIZATION_CREDENTIAL_SHAPE = /\bAuthorization["']?\s*[:=]/i;
const BASIC_CREDENTIAL_VALUE =
  /[Bb]asic\s+(?=[A-Za-z0-9+/=]*[a-z])(?=[A-Za-z0-9+/=]*[A-Z])[A-Za-z0-9+/=]{16,}/;
const PRIVATE_TEXT_PATTERNS = [
  /\/Users\//,
  /\/ext_data\//,
  /lark[o]ffice/i, // Equivalent matcher avoids matching its own policy source.
  /docs\.internal/i,
  /\bt-20\d{12}-[a-z0-9]+\b/,
  /\bBearer\b/i,
  AUTHORIZATION_CREDENTIAL_SHAPE,
  BASIC_CREDENTIAL_VALUE,
  /\btoken\s*=/i,
  /\bpassword\b/i,
  /\bsecret\b/i,
] as const;

interface VisionRefreshPrepareRequest {
  phase: "prepare";
  goal_id: string;
  agent_id: string | null;
  agent_vision_packet: JsonObject;
  existing_agent_vision: JsonObject | null;
  merge_patch: boolean;
  require_path_delta_for_durable_change: boolean;
}

interface VisionRefreshFinalizeRequest {
  phase: "finalize";
  agent_id: string | null;
  agent_vision: JsonObject | null;
  existing_agent_vision: JsonObject | null;
  vision_unchanged_reason: string | null;
  delivery_outcome: DeliveryOutcome | null;
  active_state_next_action_would_update: boolean;
  delivery_boundary: DeliveryBoundary;
  todo_id: string | null;
  completion_todo_id: string | null;
  autonomous_replan_recorded: boolean;
  blocked_retry: JsonObject | null;
}

export type VisionCheckpointDecision =
  | "patched"
  | "unchanged_with_reason"
  | "missing_required"
  | "not_required";

interface ExistingVisionContinuityBasis extends JsonObject {
  kind: "existing_vision_unchanged";
  vision_generated_at: string;
}

function requiredObject(value: unknown, label: string): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new EffectRuntimeRequestError(`${label} must be an object`);
  }
  return value as JsonObject;
}

function optionalObject(value: unknown, label: string): JsonObject | null {
  if (value === null || value === undefined) return null;
  return requiredObject(value, label);
}

function optionalString(value: unknown, label: string): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string" || !value.trim()) {
    throw new EffectRuntimeRequestError(`${label} must be a non-empty string or null`);
  }
  return value.trim();
}

function requiredBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new EffectRuntimeRequestError(`${label} must be a boolean`);
  }
  return value;
}

function requiredString(value: unknown, label: string): string {
  const decoded = optionalString(value, label);
  if (decoded === null) {
    throw new EffectRuntimeRequestError(`${label} must be a non-empty string`);
  }
  return decoded;
}

// v0 accepted JSON values through Python's str(value or ""). Preserve that
// compatibility while the existing protocol remains versioned as v0.
function pythonStringLiteral(value: string): string {
  const doubleQuoted = JSON.stringify(value);
  if (value.includes("'") && !value.includes('"')) return doubleQuoted;
  const content = doubleQuoted
    .slice(1, -1)
    .replaceAll('\\"', '"')
    .replaceAll("'", "\\'")
    .replaceAll("\\b", "\\x08")
    .replaceAll("\\f", "\\x0c");
  return `'${content}'`;
}

function pythonJsonRepr(value: unknown): string {
  if (value === null || value === undefined) return "None";
  if (typeof value === "string") return pythonStringLiteral(value);
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) {
    return `[${value.map(pythonJsonRepr).join(", ")}]`;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).map(
      ([key, item]) => `${pythonStringLiteral(key)}: ${pythonJsonRepr(item)}`,
    );
    return `{${entries.join(", ")}}`;
  }
  return String(value);
}

function pythonTruthyJsonValue(value: unknown): boolean {
  if (value === null || value === undefined || value === false || value === "") {
    return false;
  }
  if (typeof value === "number") return value !== 0 && !Number.isNaN(value);
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

function compactText(value: unknown): string {
  const compatible = pythonTruthyJsonValue(value) ? pythonJsonRepr(value) : "";
  const text = typeof value === "string" ? value : compatible;
  return text.trim().split(/\s+/).filter(Boolean).join(" ");
}

function publicSafeTextGuidance(label: string): string {
  const normalized = label.toLowerCase().replaceAll("-", "_");
  if (normalized.includes("next_action") || normalized.includes("recommended_action")) {
    return "this field is usually local-control state; use validate_local_control_text for private project routing, or a public-safe alias only on exported surfaces";
  }
  if (normalized.includes("todo")) {
    return "use a compact public-safe todo alias or summary here; keep raw local paths, private URLs, task bodies, and logs in evidence/private payloads";
  }
  if (normalized.includes("evidence")) {
    return "use a compact public-safe evidence pointer here; keep raw logs, local paths, and private URLs in private payloads";
  }
  return "use a public-safe summary or alias here; keep raw local paths, private URLs, and raw evidence in private payloads";
}

function validatePublicSafeText(label: string, value: string): void {
  for (const pattern of PRIVATE_TEXT_PATTERNS) {
    if (pattern.test(value)) {
      throw new EffectRuntimeRequestError(
        `${label} contains a private-looking value; ${publicSafeTextGuidance(label)}`,
      );
    }
  }
}

function suggestedCompactText(text: string, limit: number): string {
  if (text.length <= limit) return text;
  if (limit <= 3) return text.slice(0, limit);
  return `${text.slice(0, limit - 3).trimEnd()}...`;
}

function visionBudgetError(
  field: string,
  used: number,
  limit: number,
  suggestion: string | null = null,
): EffectRuntimeRequestError {
  let message = `${GOAL_VISION_BUDGET_ERROR}: ${field} uses ${used} chars; limit is ${limit}`;
  message += suggestion === null
    ? "; shorten one or more vision fields before retrying"
    : `; suggested compact value: ${JSON.stringify(suggestion)}`;
  return new EffectRuntimeRequestError(message, GOAL_VISION_BUDGET_ERROR);
}

function boundedPublicText(field: string, value: unknown, limit: number): string {
  const text = compactText(value);
  validatePublicSafeText(`agent_vision.${field}`, text);
  if (text.length > limit) {
    throw visionBudgetError(
      field,
      text.length,
      limit,
      suggestedCompactText(
        text,
        Math.min(limit, VISION_BUDGET_SUGGESTION_LIMIT),
      ),
    );
  }
  return text;
}

function packetText(
  packet: JsonObject,
  field: string,
  limit: number,
): string | null {
  const value = packet[field];
  if (value === null || value === undefined) return null;
  return boundedPublicText(field, value, limit) || null;
}

function normalizeGoalVisionState(value: unknown): string {
  const state = compactText(value || "vision_patch_proposed")
    .toLowerCase()
    .replaceAll("-", "_");
  if (!/^[a-z][a-z0-9_]{0,79}$/.test(state)) {
    throw new EffectRuntimeRequestError(
      "agent_vision.state must be a lower snake_case lifecycle token such as vision_patch_proposed or vision_closed",
    );
  }
  return GOAL_VISION_STATE_ALIASES[state] ?? state;
}

function normalizeAdvancementPolicy(value: unknown): string {
  const candidate = compactText(value).toLowerCase().replaceAll("-", "_");
  if (!GOAL_VISION_ADVANCEMENT_POLICIES.some(policy => policy === candidate)) {
    throw new EffectRuntimeRequestError(
      "agent_vision.advancement_policy must be one of: as_needed, repeat_until_closed",
    );
  }
  return candidate;
}

function normalizeGoalPathDelta(
  value: unknown,
): [JsonObject | null, Record<string, number>] {
  if (value === null || value === undefined) return [null, {}];
  const source = requiredObject(value, "agent_vision.path_delta");
  if (source.schema_version !== undefined && source.schema_version !== GOAL_PATH_DELTA_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `agent_vision.path_delta.schema_version must be ${GOAL_PATH_DELTA_SCHEMA_VERSION}`,
    );
  }
  const outcome = compactText(source.outcome).toLowerCase().replaceAll("-", "_");
  if (!(GOAL_PATH_DELTA_OUTCOMES as readonly string[]).includes(outcome)) {
    throw new EffectRuntimeRequestError(
      `agent_vision.path_delta.outcome must be one of: ${GOAL_PATH_DELTA_OUTCOMES.join(", ")}`,
    );
  }

  const normalized: JsonObject = {
    schema_version: GOAL_PATH_DELTA_SCHEMA_VERSION,
    outcome,
  };
  const fieldUsage: Record<string, number> = {
    "path_delta.outcome": outcome.length,
  };
  for (const [field, limit] of Object.entries(GOAL_PATH_DELTA_SCALAR_LIMITS)) {
    const rawValue = source[field];
    if (rawValue === null || rawValue === undefined) continue;
    const text = boundedPublicText(`path_delta.${field}`, rawValue, limit);
    if (!text) continue;
    normalized[field] = text;
    fieldUsage[`path_delta.${field}`] = text.length;
  }

  for (const [field, limits] of Object.entries(GOAL_PATH_DELTA_LIST_LIMITS)) {
    const rawItems = source[field];
    if (rawItems === null || rawItems === undefined) continue;
    if (!Array.isArray(rawItems)) {
      throw new EffectRuntimeRequestError(
        `agent_vision.path_delta.${field} must be a JSON array`,
      );
    }
    const [maxItems, itemLimit] = limits;
    if (rawItems.length > maxItems) {
      throw new EffectRuntimeRequestError(
        `agent_vision.path_delta.${field} has ${rawItems.length} items; limit is ${maxItems}`,
      );
    }
    const items: string[] = [];
    rawItems.forEach((item, index) => {
      const text = boundedPublicText(
        `path_delta.${field}[${index}]`,
        item,
        itemLimit,
      );
      if (!text) return;
      items.push(text);
      fieldUsage[`path_delta.${field}[${index}]`] = text.length;
    });
    if (items.length > 0) normalized[field] = items;
  }

  if (!("prior_assumption" in normalized) || !("observed_reality" in normalized)) {
    throw new EffectRuntimeRequestError(
      "agent_vision.path_delta requires prior_assumption and observed_reality",
    );
  }
  if (!["retained", "changed", "stopped"].some((field) => normalized[field])) {
    throw new EffectRuntimeRequestError(
      "agent_vision.path_delta requires at least one retained, changed, or stopped item",
    );
  }
  return [normalized, fieldUsage];
}

function normalizeFallbackDeclarations(
  value: unknown,
): [JsonObject[], Record<string, number>] | null {
  if (value === null || value === undefined) return null;
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError(
      "agent_vision.fallback_declarations must be a JSON array",
    );
  }
  if (value.length === 0) return null;
  if (value.length > VISION_FALLBACK_DECLARATION_ENTRY_LIMIT) {
    throw new EffectRuntimeRequestError(
      `agent_vision.fallback_declarations has ${value.length} items; limit is ${VISION_FALLBACK_DECLARATION_ENTRY_LIMIT}`,
    );
  }
  const declarations: JsonObject[] = [];
  const seenIds = new Set<string>();
  const fieldUsage: Record<string, number> = {};
  value.forEach((raw, index) => {
    const entry = requiredObject(
      raw,
      `agent_vision.fallback_declarations[${index}]`,
    );
    const declarationId = boundedPublicText(
      `fallback_declarations[${index}].declaration_id`,
      entry.declaration_id ?? null,
      VISION_FALLBACK_DECLARATION_ID_LIMIT,
    );
    if (!declarationId) {
      throw new EffectRuntimeRequestError(
        `agent_vision.fallback_declarations[${index}] requires a non-empty declaration_id`,
      );
    }
    if (seenIds.has(declarationId)) {
      throw new EffectRuntimeRequestError(
        `agent_vision.fallback_declarations repeats declaration_id ${JSON.stringify(declarationId)}`,
      );
    }
    seenIds.add(declarationId);
    const declaration: JsonObject = { declaration_id: declarationId };
    fieldUsage[`fallback_declarations[${index}].declaration_id`] =
      declarationId.length;
    for (const field of VISION_FALLBACK_DECLARATION_FIELDS) {
      const text = boundedPublicText(
        `fallback_declarations[${index}].${field}`,
        entry[field],
        VISION_FALLBACK_DECLARATION_ID_LIMIT,
      );
      if (!text) continue;
      declaration[field] = text;
      fieldUsage[`fallback_declarations[${index}].${field}`] = text.length;
    }
    declarations.push(declaration);
  });
  return [declarations, fieldUsage];
}

function decodePrepareRequest(request: JsonObject): VisionRefreshPrepareRequest {
  return {
    phase: "prepare",
    goal_id: requiredString(request.goal_id, "goal_id"),
    agent_id: optionalString(request.agent_id, "agent_id"),
    agent_vision_packet: requiredObject(
      request.agent_vision_packet,
      "agent_vision_packet",
    ),
    existing_agent_vision: optionalObject(
      request.existing_agent_vision,
      "existing_agent_vision",
    ),
    merge_patch: requiredBoolean(request.merge_patch, "merge_patch"),
    require_path_delta_for_durable_change: requiredBoolean(
      request.require_path_delta_for_durable_change,
      "require_path_delta_for_durable_change",
    ),
  };
}

function prepareVisionRefresh(request: VisionRefreshPrepareRequest): JsonObject {
  const packet = request.agent_vision_packet;
  // Validate authoring before merge/compaction can silently discard a declared
  // protocol. Ordinary extension metadata is not classified by overlapping keys.
  for (const [container, prefix] of [[packet, "agent_vision"], [packet.vision_patch, "agent_vision.vision_patch"]] as const) {
    if (typeof container !== "object" || container === null || Array.isArray(container)) continue;
    for (const [field, value] of Object.entries(container)) {
      if (prefix === "agent_vision" && field === "path_delta") continue;
      if (field === GOAL_PATH_DELTA_SCHEMA_VERSION ||
          (prefix === "agent_vision.vision_patch" && field === "path_delta") ||
          (typeof value === "object" && value !== null && !Array.isArray(value) &&
           (value as JsonObject).schema_version === GOAL_PATH_DELTA_SCHEMA_VERSION)) {
        throw new EffectRuntimeRequestError(
          `${prefix}.${field} must be supplied as agent_vision.path_delta; ${GOAL_PATH_DELTA_SCHEMA_VERSION} is the schema_version, not the enclosing field`,
        );
      }
    }
  }
  const existing = request.existing_agent_vision ?? {};
  const updatePacket: JsonObject = { ...packet };
  if (request.merge_patch && Object.keys(existing).length > 0) {
    const existingPatch = typeof existing.vision_patch === "object" &&
        existing.vision_patch !== null && !Array.isArray(existing.vision_patch)
      ? existing.vision_patch as JsonObject
      : {};
    const incomingPatch = typeof packet.vision_patch === "object" &&
        packet.vision_patch !== null && !Array.isArray(packet.vision_patch)
      ? packet.vision_patch as JsonObject
      : packet;
    updatePacket.vision_patch = { ...existingPatch, ...incomingPatch };
    if (!compactText(packet.state)) updatePacket.state = existing.state;
  }

  const source = typeof updatePacket.vision_patch === "object" &&
      updatePacket.vision_patch !== null && !Array.isArray(updatePacket.vision_patch)
    ? updatePacket.vision_patch as JsonObject
    : updatePacket;
  const packetGoalId = compactText(updatePacket.goal_id);
  if (packetGoalId && packetGoalId !== request.goal_id) {
    throw new EffectRuntimeRequestError(
      `agent_vision goal_id ${JSON.stringify(packetGoalId)} does not match ${JSON.stringify(request.goal_id)}`,
    );
  }
  const packetAgentId = compactText(updatePacket.agent_id);
  if (request.agent_id !== null && packetAgentId && packetAgentId !== request.agent_id) {
    throw new EffectRuntimeRequestError(
      `agent_vision agent_id ${JSON.stringify(packetAgentId)} does not match ${JSON.stringify(request.agent_id)}`,
    );
  }
  const resolvedAgentId = request.agent_id ?? (packetAgentId || null);
  if (resolvedAgentId === null) {
    throw new EffectRuntimeRequestError(
      "agent_vision requires agent_id from packet or --agent-id",
    );
  }
  validatePublicSafeText("agent_vision.agent_id", resolvedAgentId);

  const visionPatch: JsonObject = {};
  const fieldUsage: Record<string, number> = {};
  for (const [field, limit] of Object.entries(GOAL_VISION_FIELD_LIMITS)) {
    let text = packetText(source, field, limit);
    if (text === null) continue;
    if (field === "advancement_policy") text = normalizeAdvancementPolicy(text);
    visionPatch[field] = text;
    fieldUsage[field] = text.length;
  }
  if (Object.keys(visionPatch).length === 0) {
    throw new EffectRuntimeRequestError(
      "agent_vision must include at least one bounded vision field",
    );
  }

  const [pathDelta, pathDeltaUsage] = normalizeGoalPathDelta(updatePacket.path_delta);
  Object.assign(fieldUsage, pathDeltaUsage);
  const [fallbackDeclarations, fallbackUsage] = normalizeFallbackDeclarations(
    updatePacket.fallback_declarations,
  ) ?? [null, {}];
  Object.assign(fieldUsage, fallbackUsage);
  const totalUsage = Object.values(fieldUsage).reduce((total, used) => total + used, 0);
  if (totalUsage > GOAL_VISION_TOTAL_LIMIT) {
    throw visionBudgetError(
      "total_agent_vision",
      totalUsage,
      GOAL_VISION_TOTAL_LIMIT,
    );
  }

  const todoDelta: string[] = [];
  if (Array.isArray(updatePacket.todo_delta)) {
    for (const item of updatePacket.todo_delta.slice(0, 8)) {
      const text = boundedPublicText("todo_delta", item, 80);
      if (text) todoDelta.push(text);
    }
  }
  const rawValidation = typeof updatePacket.validation === "object" &&
      updatePacket.validation !== null && !Array.isArray(updatePacket.validation)
    ? updatePacket.validation as JsonObject
    : {};
  const validation: JsonObject = {
    ...rawValidation,
    budget_checked: true,
    budget_status: "ok",
    write_correctness_checked: Boolean(rawValidation.write_correctness_checked),
  };

  const fieldLimits: JsonObject = {
    ...GOAL_VISION_FIELD_LIMITS,
    "path_delta.outcome": 32,
  };
  for (const [field, limit] of Object.entries(GOAL_PATH_DELTA_SCALAR_LIMITS)) {
    fieldLimits[`path_delta.${field}`] = limit;
  }
  for (const [field, [, itemLimit]] of Object.entries(GOAL_PATH_DELTA_LIST_LIMITS)) {
    fieldLimits[`path_delta.${field}[]`] = itemLimit;
  }
  fieldLimits["fallback_declarations"] = VISION_FALLBACK_DECLARATION_ENTRY_LIMIT;
  fieldLimits["fallback_declarations[]"] = VISION_FALLBACK_DECLARATION_ID_LIMIT;

  const agentVision: JsonObject = {
    schema_version: GOAL_VISION_REPLAN_SCHEMA_VERSION,
    goal_id: request.goal_id,
    agent_id: resolvedAgentId,
    state: normalizeGoalVisionState(updatePacket.state),
    vision_patch: visionPatch,
    todo_delta: todoDelta,
    vision_budget: {
      schema_version: "goal_vision_budget_v0",
      status: "ok",
      field_limits: fieldLimits,
      field_usage: fieldUsage,
      total_limit: GOAL_VISION_TOTAL_LIMIT,
      total_usage: totalUsage,
    },
    validation,
  };
  if (pathDelta !== null) agentVision.path_delta = pathDelta;
  if (fallbackDeclarations !== null) {
    agentVision.fallback_declarations = fallbackDeclarations;
  }

  if (
    request.require_path_delta_for_durable_change &&
    Object.keys(existing).length > 0
  ) {
    const existingPatch = typeof existing.vision_patch === "object" &&
        existing.vision_patch !== null && !Array.isArray(existing.vision_patch)
      ? existing.vision_patch as JsonObject
      : {};
    const changedFields = GOAL_VISION_DURABLE_FIELDS.filter(
      (field) => existingPatch[field] !== visionPatch[field],
    );
    if (changedFields.length > 0 && pathDelta?.outcome !== "replan") {
      throw new EffectRuntimeRequestError(
        `autonomous agent vision replan changes durable fields ${changedFields.join(", ")}; provide path_delta with schema_version=goal_path_delta_v0 and outcome=replan so the mainline change is explicit`,
      );
    }
  }

  return {
    schema_version: VISION_REFRESH_PREPARED_SCHEMA_VERSION,
    agent_vision: agentVision,
  };
}

function existingVisionContinuityBasis(
  vision: JsonObject | null,
): ExistingVisionContinuityBasis | null {
  if (vision === null) return null;
  const generatedAt = vision.generated_at;
  if (typeof generatedAt !== "string" || !generatedAt.trim()) return null;
  return {
    kind: "existing_vision_unchanged",
    vision_generated_at: generatedAt.trim(),
  };
}

function deliveryBoundary(value: unknown): DeliveryBoundary {
  if (value === null || value === undefined || value === "") {
    return "semantic_closeout";
  }
  if (DELIVERY_BOUNDARIES.some((candidate) => candidate === value)) {
    return value as DeliveryBoundary;
  }
  throw new EffectRuntimeRequestError("delivery_boundary is unsupported");
}

export function normalizeVisionUnchangedReason(value: unknown): string | null {
  const unchanged = compactText(value);
  if (!unchanged) return null;
  validatePublicSafeText("vision_unchanged_reason", unchanged);
  if (unchanged.length > VISION_UNCHANGED_REASON_LIMIT) {
    throw new EffectRuntimeRequestError(
      `vision_unchanged_reason exceeds ${VISION_UNCHANGED_REASON_LIMIT} chars`,
    );
  }
  return unchanged;
}

export function decodeVisionCheckpointRequest(
  value: unknown,
): VisionRefreshFinalizeRequest {
  const request = requiredObject(value, "goal.vision_checkpoint params");
  if (request.schema_version !== VISION_REFRESH_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Vision refresh request schema mismatch");
  }
  if (request.phase !== "finalize") {
    throw new EffectRuntimeRequestError("Vision refresh phase is unsupported");
  }
  return {
    phase: "finalize",
    agent_id: optionalString(request.agent_id, "agent_id"),
    agent_vision: optionalObject(request.agent_vision, "agent_vision"),
    existing_agent_vision: optionalObject(
      request.existing_agent_vision,
      "existing_agent_vision",
    ),
    vision_unchanged_reason: normalizeVisionUnchangedReason(
      request.vision_unchanged_reason,
    ),
    delivery_outcome: decodeOptionalDeliveryOutcome(request.delivery_outcome),
    active_state_next_action_would_update: requiredBoolean(
      request.active_state_next_action_would_update,
      "active_state_next_action_would_update",
    ),
    delivery_boundary: deliveryBoundary(request.delivery_boundary),
    todo_id: optionalString(request.todo_id, "todo_id"),
    completion_todo_id: optionalString(
      request.completion_todo_id,
      "completion_todo_id",
    ),
    autonomous_replan_recorded: requiredBoolean(
      request.autonomous_replan_recorded,
      "autonomous_replan_recorded",
    ),
    blocked_retry: optionalObject(request.blocked_retry, "blocked_retry"),
  };
}

function validateInFlightBoundary(request: VisionRefreshFinalizeRequest): void {
  if (request.delivery_boundary !== "in_flight_continuation") return;
  if (request.delivery_outcome !== "outcome_progress") {
    throw new EffectRuntimeRequestError(
      "in_flight_continuation requires delivery_outcome=outcome_progress",
    );
  }
  if (request.agent_id === null || request.todo_id === null) {
    throw new EffectRuntimeRequestError(
      "in_flight_continuation requires an agent-bound Todo settlement",
    );
  }
  if (request.completion_todo_id !== null) {
    throw new EffectRuntimeRequestError(
      "in_flight_continuation conflicts with Todo completion closeout",
    );
  }
  if (request.active_state_next_action_would_update) {
    throw new EffectRuntimeRequestError(
      "in_flight_continuation conflicts with a durable Next Action update",
    );
  }
  if (request.autonomous_replan_recorded) {
    throw new EffectRuntimeRequestError(
      "in_flight_continuation conflicts with autonomous replan writeback",
    );
  }
}

export function buildVisionCheckpoint(value: unknown): JsonObject {
  const envelope = requiredObject(value, "goal.vision_checkpoint params");
  if (envelope.schema_version !== VISION_REFRESH_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Vision refresh request schema mismatch");
  }
  if (envelope.phase === "prepare") {
    return prepareVisionRefresh(decodePrepareRequest(envelope));
  }
  const request = decodeVisionCheckpointRequest(value);
  validateInFlightBoundary(request);
  if (request.blocked_retry !== null && (
    request.delivery_outcome !== "outcome_gap" ||
    request.delivery_boundary !== "semantic_closeout" ||
    request.todo_id === null ||
    request.completion_todo_id !== null ||
    !isBoundedBlockedRetry(request.blocked_retry, request.todo_id)
  )) {
    throw new EffectRuntimeRequestError("blocked retry does not bind a typed outcome-gap Todo closeout");
  }
  const triggers: JsonObject[] = [];
  if (
    isMaterialDeliveryOutcome(request.delivery_outcome) &&
    request.delivery_boundary === "semantic_closeout" &&
    request.blocked_retry === null
  ) {
    triggers.push({
      kind: "material_delivery_outcome",
      delivery_outcome: request.delivery_outcome,
    });
  } else if (request.delivery_boundary === "in_flight_continuation") {
    triggers.push({
      kind: "in_flight_continuation",
      todo_id: request.todo_id,
    });
  }
  if (request.active_state_next_action_would_update) {
    triggers.push({ kind: "durable_next_action_update" });
  }

  const unchanged = request.vision_unchanged_reason;
  const requiredTriggers = triggers.filter((trigger) =>
    trigger.kind !== "in_flight_continuation"
  );
  const required = requiredTriggers.length > 0 || unchanged !== null;
  let decision: VisionCheckpointDecision;
  let satisfied: boolean;
  if (request.agent_vision !== null) {
    decision = "patched";
    satisfied = true;
  } else if (unchanged !== null && request.existing_agent_vision !== null) {
    decision = "unchanged_with_reason";
    satisfied = true;
  } else if (unchanged !== null || required) {
    decision = "missing_required";
    satisfied = false;
  } else {
    decision = "not_required";
    satisfied = true;
  }

  const checkpoint: JsonObject = {
    schema_version: VISION_CHECKPOINT_SCHEMA_VERSION,
    agent_id: request.agent_id,
    required,
    satisfied,
    decision,
    triggers,
    delivery_boundary: request.delivery_boundary,
  };
  if (request.agent_vision !== null) {
    checkpoint.agent_vision_state = request.agent_vision.state;
  }
  if (unchanged !== null && request.existing_agent_vision !== null) {
    checkpoint.unchanged_reason = unchanged;
    checkpoint.agent_vision_state = request.existing_agent_vision.state;
    const continuityBasis = existingVisionContinuityBasis(
      request.existing_agent_vision,
    );
    if (continuityBasis !== null) {
      checkpoint.continuity_basis = continuityBasis;
    }
  } else if (unchanged !== null) {
    checkpoint.missing_baseline = true;
    checkpoint.rejected_unchanged_reason = unchanged;
  }
  if (!satisfied) {
    checkpoint.required_resolution = ["write_vision_patch"];
    if (checkpoint.missing_baseline !== true) {
      (checkpoint.required_resolution as string[]).push(
        "record_unchanged_reason",
      );
    }
  }
  return checkpoint;
}
