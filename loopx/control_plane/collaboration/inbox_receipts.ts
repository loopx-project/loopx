import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireBoolean, requireJsonObject, requireStringLiteral } from "../runtime_decode.ts";
import { parseExactGoalRef } from "../goals/goal_instance_identity.ts";

type Observation =
  | Readonly<{ state: "absent" | "unavailable" }>
  | Readonly<{ state: "read"; value: JsonObject | null }>;
type Decision = "adopt" | "defer" | "reject" | "no_change";
type Warning = "decision_unreadable_or_conflicting" | "conclusion_unreadable_or_conflicting"
  | "decision_missing_for_conclusion";
type InboxState = "pending" | "awaiting_conclusion" | "settled" | "receipt_unavailable";

function observation(value: unknown): Observation {
  const row = requireJsonObject(value, "receipt observation");
  const state = requireStringLiteral(row.state, ["absent", "unavailable", "read"], "receipt observation state");
  return state === "read" ? { state, value: jsonObject(row.value) } : { state };
}

function sameGoalRef(left: unknown, right: unknown): boolean {
  if (left === undefined && right === undefined) return true;
  const a = parseExactGoalRef(left);
  const b = parseExactGoalRef(right);
  return a.kind === "parsed" && b.kind === "parsed"
    && a.value.goalId.value === b.value.goalId.value
    && a.value.goalInstanceId.value === b.value.goalInstanceId.value;
}

export function sameRequest(receipt: JsonObject | null, request: JsonObject, source: boolean): receipt is JsonObject {
  return receipt !== null && ["request_id", "goal_id", "agent_id"].every((key) => receipt[key] === request[key])
    && sameGoalRef(receipt.goal_ref, request.goal_ref)
    && (!source || receipt.source_id === request.source_id);
}

export function receiverDecision(value: unknown): Decision | null {
  switch (value) {
    case "adopt": case "defer": case "reject": case "no_change": return value;
    default: return null;
  }
}

function inspect(value: unknown): JsonObject {
  const row = requireJsonObject(value, "inbox observation");
  const request = requireJsonObject(row.request, "inbox request");
  const routePresent = requireBoolean(row.route_present, "return route presence");
  const decision = observation(row.decision);
  const conclusion = observation(row.conclusion);
  const recorded = decision.state === "read" && sameRequest(decision.value, request, false)
    ? receiverDecision(decision.value.decision) : null;
  const warnings: Warning[] = [];
  if (decision.state !== "absent" && !recorded) warnings.push("decision_unreadable_or_conflicting");
  if (conclusion.state !== "absent") {
    const result = conclusion.state === "read" ? conclusion.value : null;
    if (!sameRequest(result, request, true) || result.phase !== "conclusion"
        || typeof result.text !== "string" || !result.text.trim()
        || Array.from(result.text).length > 20000
        || !receiverDecision(result.decision) || (recorded !== null && result.decision !== recorded)) {
      warnings.push("conclusion_unreadable_or_conflicting");
    }
    if (decision.state === "absent") warnings.push("decision_missing_for_conclusion");
  }
  // This is receipt/readback state only. Settled means a receiver reply exists
  // (or a legacy ACK owes no return), never accepted work or verified delivery.
  const kind: InboxState = warnings.length ? "receipt_unavailable"
    : !recorded ? "pending"
    : conclusion.state === "read" || !routePresent ? "settled" : "awaiting_conclusion";
  return { kind, recorded_decision: recorded, warnings };
}

/** One bounded read-model batch; no store writes, execution or authority. */
export function inspectCollaborationInboxReceipts(params: JsonObject): JsonObject {
  if (!Array.isArray(params.observations) || params.observations.length > 128) {
    throw new EffectRuntimeRequestError("inbox receipt observation batch must contain at most 128 requests");
  }
  return { items: params.observations.map(inspect) };
}
