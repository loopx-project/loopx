import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireBoolean,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
} from "../runtime_decode.ts";

import type { JsonObject } from "../effect_program.ts";
import {
  ACTION_PORTFOLIO_PLANNING_PACKET_REQUEST_SCHEMA,
  ACTION_PORTFOLIO_PLANNING_PACKET_RESULT_SCHEMA,
  ACTION_PORTFOLIO_SELECTION_REQUEST_SCHEMA,
  ACTION_PORTFOLIO_SELECTION_RESULT_SCHEMA,
} from "../coordination/coordination_state_contract.generated.ts";
import {
  decodeTodoPlanningInventory,
  projectTodoPlanningInventory,
  projectTodoPlanningInventoryDetail,
} from "./planning_inventory.ts";
import {
  PLANNING_HORIZON_REQUEST_SCHEMA_VERSION,
  projectQuotaPlanningHorizon,
} from "./planning_horizon.ts";
import { EffectiveAction } from "../quota/effective_action.generated.ts";

export const ACTION_PORTFOLIO_SCHEMA_VERSION = "quota_action_portfolio_v2";
export const ACTION_PORTFOLIO_REQUEST_SCHEMA_VERSION =
  "quota_action_portfolio_request_v1";
export const ACTION_SELECTION_QUALIFICATION_SCHEMA_VERSION =
  ACTION_PORTFOLIO_SELECTION_RESULT_SCHEMA;
export const ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION =
  ACTION_PORTFOLIO_SELECTION_REQUEST_SCHEMA;
export const RETAINED_ACTION_SELECTION_REENTRY_REQUEST_SCHEMA_VERSION =
  "retained_action_selection_reentry_request_v0";
export const RETAINED_ACTION_SELECTION_REENTRY_SCHEMA_VERSION =
  "retained_action_selection_reentry_v0";
export const QUOTA_PLANNING_PACKET_SCHEMA_VERSION =
  ACTION_PORTFOLIO_PLANNING_PACKET_RESULT_SCHEMA;
export const QUOTA_PLANNING_PACKET_REQUEST_SCHEMA_VERSION =
  ACTION_PORTFOLIO_PLANNING_PACKET_REQUEST_SCHEMA;

const MAX_ALTERNATIVE_ACTIONS = 3;
const DEFAULT_MAX_ALTERNATIVE_ACTIONS = 2;

interface ActionCandidate extends JsonObject {
  todo_id: string;
  text: string;
  required_capabilities?: string[];
  required_write_scopes?: string[];
}

function actionCandidate(value: unknown, label: string): ActionCandidate {
  const raw = requireJsonObject(value, label);
  const candidate: ActionCandidate = {
    todo_id: requireNonEmptyString(raw.todo_id, `${label}.todo_id`),
    text: requireNonEmptyString(raw.text, `${label}.text`),
  };
  for (const field of [
    "priority",
    "status",
    "task_class",
    "action_kind",
    "claimed_by",
    "source",
    "selected_by",
    "availability_reason",
    "next_due_at",
    "continuation_hint",
  ] as const) {
    const normalized = optionalNonEmptyString(raw[field], `${label}.${field}`);
    if (normalized !== null) candidate[field] = normalized;
  }
  if (raw.index !== null && raw.index !== undefined) {
    candidate.index = requireInteger(raw.index, `${label}.index`);
  }
  for (const field of [
    "required_capabilities",
    "required_write_scopes",
  ] as const) {
    if (raw[field] !== null && raw[field] !== undefined) {
      candidate[field] = requireStringArray(raw[field], `${label}.${field}`);
    }
  }
  if (raw.claim_required_before_work === true) {
    candidate.claim_required_before_work = true;
  }
  return candidate;
}

function candidateIdentity(candidate: ActionCandidate): string {
  return candidate.todo_id || candidate.text;
}

function requireRunnableAdvancement(
  candidate: ActionCandidate,
  label: string,
): void {
  if (candidate.status !== "open") {
    throw new EffectRuntimeRequestError(`${label}.status must be open`);
  }
  if (candidate.task_class !== "advancement_task") {
    throw new EffectRuntimeRequestError(`${label}.task_class must be advancement_task`);
  }
}

function primaryProjection(candidate: ActionCandidate): JsonObject {
  return { todo_id: candidate.todo_id };
}

function suggestedActionProjection(candidate: ActionCandidate): JsonObject {
  const projected: JsonObject = {
    todo_id: candidate.todo_id,
    text: candidate.text,
  };
  for (const field of ["priority", "action_kind", "continuation_hint"] as const) {
    if (candidate[field] !== undefined) projected[field] = candidate[field];
  }
  for (const field of [
    "required_capabilities",
    "required_write_scopes",
  ] as const) {
    if (candidate[field] !== undefined) projected[field] = candidate[field];
  }
  if (candidate.claim_required_before_work === true) {
    projected.claim_required_before_work = true;
  }
  return projected;
}

function allowedActionProjection(
  candidate: ActionCandidate,
  selectionRole: "recommended" | "alternative",
): JsonObject {
  return {
    ...suggestedActionProjection(candidate),
    selection_role: selectionRole,
  };
}

function unavailableProjection(candidate: ActionCandidate): JsonObject {
  const projected: JsonObject = {
    todo_id: candidate.todo_id,
    text: candidate.text,
    availability_reason: candidate.availability_reason,
  };
  for (const field of [
    "priority",
    "status",
    "task_class",
    "action_kind",
    "next_due_at",
  ] as const) {
    if (candidate[field] !== undefined) projected[field] = candidate[field];
  }
  return projected;
}

/**
 * Build the bounded, model-facing action set for one quota decision.
 *
 * Python owns repository/status projection and supplies only candidates that
 * already passed agent-scope and capability gates. TypeScript owns the stable
 * portfolio semantics: validation, identity de-duplication, ordering, and the
 * fallback trigger exposed to hosts and models.
 */
function projectQuotaActionPortfolioV1(
  request: JsonObject,
  requirePlanningChoice = false,
): JsonObject | null {
  if (request.schema_version !== ACTION_PORTFOLIO_REQUEST_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `action_portfolio_request.schema_version must be ${ACTION_PORTFOLIO_REQUEST_SCHEMA_VERSION}`,
    );
  }
  const inventory = decodeTodoPlanningInventory(request.planning_inventory);
  const primaryValue = inventory.items.find(
    (item) => item.todo_id === inventory.selected_todo_id,
  );
  if (!primaryValue) {
    throw new EffectRuntimeRequestError(
      "action_portfolio_request.planning_inventory must include selected_todo_id",
    );
  }
  const primary = actionCandidate(primaryValue, "action_portfolio_request.primary");
  requireRunnableAdvancement(primary, "action_portfolio_request.primary");
  const maximum = request.max_alternative_actions === undefined
    ? DEFAULT_MAX_ALTERNATIVE_ACTIONS
    : requireInteger(
      request.max_alternative_actions,
      "action_portfolio_request.max_alternative_actions",
    );
  if (maximum < 1 || maximum > MAX_ALTERNATIVE_ACTIONS) {
    throw new EffectRuntimeRequestError(
      `action_portfolio_request.max_alternative_actions must be between 1 and ${MAX_ALTERNATIVE_ACTIONS}`,
    );
  }

  const rawCandidates = inventory.items.filter((item) => item.runnable_candidate);
  const seen = new Set<string>([candidateIdentity(primary)]);
  const alternativeActions: ActionCandidate[] = [];
  for (const [index, rawCandidate] of rawCandidates.entries()) {
    const candidate = actionCandidate(
      rawCandidate,
      `action_portfolio_request.candidates[${index}]`,
    );
    requireRunnableAdvancement(
      candidate,
      `action_portfolio_request.candidates[${index}]`,
    );
    const identity = candidateIdentity(candidate);
    if (seen.has(identity)) continue;
    seen.add(identity);
    alternativeActions.push(candidate);
    if (alternativeActions.length >= maximum) break;
  }

  const rawUnavailable = inventory.items.filter(
    (item) => item.unavailable_higher_priority,
  );
  const unavailableHigherPriority: ActionCandidate[] = [];
  const seenUnavailable = new Set<string>();
  for (const [index, rawCandidate] of rawUnavailable.entries()) {
    const candidate = actionCandidate(
      rawCandidate,
      `action_portfolio_request.unavailable_higher_priority[${index}]`,
    );
    if (!candidate.availability_reason) {
      throw new EffectRuntimeRequestError(
        `action_portfolio_request.unavailable_higher_priority[${index}].availability_reason must be a non-empty string`,
      );
    }
    const identity = candidateIdentity(candidate);
    if (identity === candidateIdentity(primary) || seenUnavailable.has(identity)) {
      continue;
    }
    seenUnavailable.add(identity);
    unavailableHigherPriority.push(candidate);
    if (unavailableHigherPriority.length >= MAX_ALTERNATIVE_ACTIONS) break;
  }

  if (!requirePlanningChoice && alternativeActions.length === 0 && unavailableHigherPriority.length === 0) {
    return null;
  }
  const requiresExplicitTurnBinding = requirePlanningChoice || alternativeActions.length > 0;
  return {
    schema_version: ACTION_PORTFOLIO_SCHEMA_VERSION,
    primary: primaryProjection(primary),
    selection_policy: {
      decision_owner: "agent",
      mode: requiresExplicitTurnBinding
        ? "explicit_turn_binding"
        : "recommended_only",
      recommendation_role: requiresExplicitTurnBinding
        ? "default_not_binding"
        : "only_runnable_action",
      requires_explicit_turn_binding: requiresExplicitTurnBinding,
      direct_delivery_before_selection: !requiresExplicitTurnBinding,
      max_alternative_actions: maximum,
      candidate_scope: "current_authoritative_eligible_todos",
      suggestions_exhaustive: false,
    },
    suggested_actions: [
      allowedActionProjection(primary, "recommended"),
      ...alternativeActions.map((candidate) =>
        allowedActionProjection(candidate, "alternative")
      ),
    ],
    unavailable_higher_priority: unavailableHigherPriority.map(
      unavailableProjection,
    ),
  };
}

function projectQuotaPlanningPacket(request: JsonObject): JsonObject {
  const projectionEnabled = requireBoolean(
    request.projection_enabled,
    "planning_packet_request.projection_enabled",
  );
  const includeDetail = requireBoolean(
    request.include_detail,
    "planning_packet_request.include_detail",
  );
  if (!Array.isArray(request.acceptance_gaps)) {
    throw new EffectRuntimeRequestError(
      "planning_packet_request.acceptance_gaps must be an array",
    );
  }
  const inventory = projectTodoPlanningInventory(
    requireJsonObject(
      request.planning_inventory_request,
      "planning_packet_request.planning_inventory_request",
    ),
  );
  // A required replan still preempts delivery. Before any explicit choice,
  // however, its recommended Todo is not a causal settlement identity.
  // Unscoped diagnostic reads have no Turn receipt to bind or recover.
  let requirePlanningChoice = false;
  if (request.replan_selection_context !== undefined) {
    const context = requireJsonObject(request.replan_selection_context, "replan_selection_context");
    const hasTurnIdentity = requireBoolean(context.has_turn_identity, "replan_selection_context.has_turn_identity");
    const shouldRun = requireBoolean(context.should_run, "replan_selection_context.should_run");
    const receiptBound = requireBoolean(context.receipt_bound, "replan_selection_context.receipt_bound");
    const selectionRequested = requireBoolean(context.selection_requested, "replan_selection_context.selection_requested");
    const monitorOnly = requireBoolean(context.monitor_only, "replan_selection_context.monitor_only");
    requirePlanningChoice = hasTurnIdentity && shouldRun && !receiptBound && !selectionRequested && !monitorOnly;
  }
  const projected: JsonObject = {
    schema_version: QUOTA_PLANNING_PACKET_SCHEMA_VERSION,
  };
  if (projectionEnabled || requirePlanningChoice) {
    const portfolio = projectQuotaActionPortfolioV1({
      schema_version: ACTION_PORTFOLIO_REQUEST_SCHEMA_VERSION,
      planning_inventory: inventory,
      max_alternative_actions: DEFAULT_MAX_ALTERNATIVE_ACTIONS,
    }, requirePlanningChoice);
    if (portfolio !== null) projected.action_portfolio = portfolio;
    const horizon = projectQuotaPlanningHorizon({
      schema_version: PLANNING_HORIZON_REQUEST_SCHEMA_VERSION,
      planning_inventory: inventory,
      acceptance_gaps: request.acceptance_gaps,
    });
    if (horizon !== null) projected.planning_horizon = horizon;
  }
  if (includeDetail) {
    projected.agent_todo_planning_inventory =
      projectTodoPlanningInventoryDetail(inventory);
  }
  return projected;
}

/**
 * Project either the legacy action portfolio or the aggregate quota planning
 * packet through the existing registered runtime operation.
 */
export function projectQuotaActionPortfolio(value: unknown): JsonObject | null {
  const request = requireJsonObject(value, "action_portfolio_request");
  if (request.schema_version === QUOTA_PLANNING_PACKET_REQUEST_SCHEMA_VERSION) {
    return projectQuotaPlanningPacket(request);
  }
  return projectQuotaActionPortfolioV1(request);
}

/**
 * Qualify an agent's provisional Todo choice against the current hard lane.
 *
 * A same-turn `--todo-id` is not a receipt binding yet.  Python projects the
 * current authoritative candidate and the current arbitration result; this
 * reducer alone decides whether that pending choice may become the settlement
 * candidate.  Committed receipt replay remains a separate state.
 */
type ActionSelectionQualification = JsonObject & (
  | {state: "qualified"; selected_todo: JsonObject; recovery_action?: never}
  | {state: "deferred" | "rejected"; recovery_action: "reenter_guard_without_selection"; selected_todo?: never}
);

export function qualifyActionSelection(value: unknown): ActionSelectionQualification {
  const request = requireJsonObject(value, "action_selection_qualification_request");
  if (
    request.schema_version !==
      ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION
  ) {
    throw new Error(
      `action_selection_qualification_request.schema_version must be ${ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION}`,
    );
  }
  const requestedTodoId = requireNonEmptyString(
    request.requested_todo_id,
    "action_selection_qualification_request.requested_todo_id",
  );
  const rawCandidate = request.candidate;
  const preemptions = Array.isArray(request.delivery_preemptions)
    ? request.delivery_preemptions.map((item, index) =>
      requireNonEmptyString(
        item,
        `action_selection_qualification_request.delivery_preemptions[${index}]`,
      )
    )
    : [];
  const shouldRun = request.should_run === true;
  const normalDeliveryAllowed = request.normal_delivery_allowed === true;
  if (rawCandidate === null || rawCandidate === undefined) {
    const requestedTaskClass = request.requested_task_class === null ||
        request.requested_task_class === undefined
      ? null
      : requireNonEmptyString(
        request.requested_task_class,
        "action_selection_qualification_request.requested_task_class",
      );
    return {
      schema_version: ACTION_SELECTION_QUALIFICATION_SCHEMA_VERSION,
      state: "rejected",
      recovery_action: "reenter_guard_without_selection",
      requested_todo_id: requestedTodoId,
      reason: requestedTaskClass === "continuous_monitor"
        ? "auxiliary_monitor_not_selectable_in_advancement_lane"
        : "candidate_not_currently_eligible",
    };
  }
  const candidate = actionCandidate(
    rawCandidate,
    "action_selection_qualification_request.candidate",
  );
  requireRunnableAdvancement(
    candidate,
    "action_selection_qualification_request.candidate",
  );
  if (candidate.todo_id !== requestedTodoId) {
    throw new Error(
      "action_selection_qualification_request.candidate.todo_id must match requested_todo_id",
    );
  }
  if (!shouldRun || !normalDeliveryAllowed || preemptions.length > 0) {
    return {
      schema_version: ACTION_SELECTION_QUALIFICATION_SCHEMA_VERSION,
      state: "deferred",
      recovery_action: "reenter_guard_without_selection",
      requested_todo_id: requestedTodoId,
      reason: preemptions[0] ?? "current_delivery_gate",
      delivery_preemptions: preemptions,
      // This is permission to refresh admission, never permission to deliver.
      // Other hard lanes must remain visible to the caller without an inline
      // retry. The CLI must durably retain the choice before taking this hop.
      ...(shouldRun && !normalDeliveryAllowed &&
          preemptions.includes("autonomous_replan") &&
          preemptions.every((reason) =>
            reason === "autonomous_replan" || reason === "delivery_not_allowed"
          ) ? { inline_reentry_allowed: true } : {}),
    };
  }
  return {
    schema_version: ACTION_SELECTION_QUALIFICATION_SCHEMA_VERSION,
    state: "qualified",
    requested_todo_id: requestedTodoId,
    selected_todo: {
      ...suggestedActionProjection(candidate),
      selection_binding: "pending_action_selection",
    },
  };
}

/**
 * Reconcile a previously explicit, still-unbound Todo choice on guard reentry.
 *
 * A projected default is recommendation authority only.  It may inherit the
 * retained choice when the identities agree, but it may never replace that
 * choice.  A hard autonomous replan is different: it is the current control
 * lane and therefore receives its own replan settlement identity while the
 * Todo choice remains deferred for a fresh Turn.
 */
export function reconcileRetainedActionSelection(value: unknown): JsonObject {
  const request = requireJsonObject(
    value,
    "retained_action_selection_reentry_request",
  );
  if (
    request.schema_version !==
      RETAINED_ACTION_SELECTION_REENTRY_REQUEST_SCHEMA_VERSION
  ) {
    throw new EffectRuntimeRequestError(
      `retained_action_selection_reentry_request.schema_version must be ${RETAINED_ACTION_SELECTION_REENTRY_REQUEST_SCHEMA_VERSION}`,
    );
  }
  const retainedTodoId = requireNonEmptyString(
    request.retained_todo_id,
    "retained_action_selection_reentry_request.retained_todo_id",
  );
  const projectedTodoId = optionalNonEmptyString(
    request.projected_todo_id,
    "retained_action_selection_reentry_request.projected_todo_id",
  );
  const effectiveAction = requireNonEmptyString(
    request.effective_action,
    "retained_action_selection_reentry_request.effective_action",
  );
  const replanObligationId = optionalNonEmptyString(
    request.replan_obligation_id,
    "retained_action_selection_reentry_request.replan_obligation_id",
  );

  if (projectedTodoId === retainedTodoId) {
    return {
      schema_version: RETAINED_ACTION_SELECTION_REENTRY_SCHEMA_VERSION,
      disposition: "preserve_retained_todo",
      retained_todo_id: retainedTodoId,
      projected_todo_id: projectedTodoId,
      clear_fields: ["action_portfolio"],
    };
  }
  if (
    effectiveAction === "autonomous_replan_required" &&
    replanObligationId !== null
  ) {
    return {
      schema_version: RETAINED_ACTION_SELECTION_REENTRY_SCHEMA_VERSION,
      disposition: "bind_autonomous_replan",
      retained_todo_id: retainedTodoId,
      projected_todo_id: projectedTodoId,
      replan_obligation_id: replanObligationId,
      continuation: "fresh_turn_after_replan_closeout",
      clear_fields: ["action_portfolio"],
    };
  }
  return {
    schema_version: RETAINED_ACTION_SELECTION_REENTRY_SCHEMA_VERSION,
    disposition: "require_explicit_selection",
    retained_todo_id: retainedTodoId,
    projected_todo_id: projectedTodoId,
    reason: "projected_default_differs_from_retained_explicit_choice",
    projection: {
      decision_patch: {
        ok: false,
        decision: "skip",
        should_run: false,
        effective_action: EffectiveAction.QUOTA_SKIP,
        normal_delivery_allowed: false,
        recovery_delivery_allowed: false,
        self_repair_allowed: false,
        state: "action_selection_required",
        reason:
          "the current projected default differs from the explicit Todo retained by this Turn",
        recommended_action:
          "rerun quota should-run with the same --turn-instance-id and an explicit eligible --todo-id",
      },
      execution_obligation_patch: {
        must_attempt_work: false,
        delivery_allowed: false,
        reason:
          "rerun quota should-run with the same --turn-instance-id and an explicit eligible --todo-id",
      },
      clear_fields: ["selected_todo", "todo_id", "agent_lane_next_action"],
    },
  };
}
