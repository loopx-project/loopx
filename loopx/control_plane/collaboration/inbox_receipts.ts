import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireBoolean, requireJsonObject, requireStringLiteral } from "../runtime_decode.ts";
import { parseExactGoalRef } from "../goals/goal_instance_identity.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

type Observation =
  | Readonly<{ state: "absent" | "unavailable" }>
  | Readonly<{ state: "read"; value: JsonObject | null }>;
type Decision = "adopt" | "defer" | "reject" | "no_change";
type Warning = "decision_unreadable_or_conflicting" | "conclusion_unreadable_or_conflicting"
  | "decision_missing_for_conclusion";
type InboxState = "pending" | "awaiting_conclusion" | "settled" | "receipt_unavailable";

/** Advice for the receiving Agent, not a new task lifecycle or admission gate. */
function receiverFollowthrough(value: unknown): JsonObject {
  const params = requireJsonObject(value, "receiver followthrough observation");
  const kind = requireStringLiteral(params.kind,
    ["pending", "awaiting_conclusion", "settled", "receipt_unavailable"], "inbox receipt state");
  const decision = params.recorded_decision === null ? null : requireStringLiteral(
    params.recorded_decision, ["adopt", "defer", "reject", "no_change"], "recorded receiver decision");
  const linked = params.linked_todos;
  if (!Array.isArray(linked) || linked.length > 16) {
    throw new EffectRuntimeRequestError("receiver followthrough needs at most 16 linked Todos");
  }
  const work = linked.map((value) => requireJsonObject(value, "linked Todo"));
  const refs = params.evidence_refs;
  if (!Array.isArray(refs) || refs.length > 16 || refs.some(
    (ref) => typeof ref !== "string" || !ENVELOPED_SHA256_PATTERN.test(ref))) {
    throw new EffectRuntimeRequestError("receiver evidence references need at most 16 opaque SHA256 identifiers");
  }
  const evidenceUnavailable = requireBoolean(params.evidence_unavailable, "receiver evidence availability");
  // Only explicit request links can establish its work. A busy Agent, a
  // matching title, and an unrelated rolling review task are not such links.
  const unresolved = work.some((todo) => todo.status !== "done");
  const step = kind === "receipt_unavailable" || evidenceUnavailable ? "recover_evidence"
    : decision === null ? "assess_request"
    : decision === "adopt" && (unresolved || work.length === 0) ? "review_request_work"
    : "return_answer";
  return {
    step,
    assessment_required: decision === null,
    recorded_decision: decision,
    linked_todos: work,
    evidence_refs: refs,
    answer_owed: kind !== "settled",
    request_completion: "not_established_by_receipts_or_todo_status",
  };
}

export function projectReceiverFollowthrough(params: JsonObject): JsonObject {
  if (!Array.isArray(params.observations) || params.observations.length > 20) {
    throw new EffectRuntimeRequestError("receiver followthrough batch must contain at most 20 requests");
  }
  return { items: params.observations.map(receiverFollowthrough) };
}

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
