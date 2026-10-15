import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireBoolean, requireJsonObject, requireStringLiteral } from "../runtime_decode.ts";
import {receiverDecision, sameRequest} from "./semantic_request.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";
import {requestControlView} from "./request_control.ts";
import {collaborationResultObservations} from "./result_publication.ts";

type Observation =
  | Readonly<{ state: "absent" | "unavailable" }>
  | Readonly<{ state: "read"; value: JsonObject | null }>;
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


function inspect(value: unknown): JsonObject {
  const row = requireJsonObject(value, "inbox observation");
  const request = requireJsonObject(row.request, "inbox request");
  const routePresent = requireBoolean(row.route_present, "return route presence");
  const decision = observation(row.decision);
  if (Object.hasOwn(request, "request_control")) {
    let view: JsonObject | null = null;
    let readback: JsonObject | null = null;
    try {
      const legacy = decision.state === "read" ? decision.value : null;
      const priorRead = observation(row.read);
      view = requestControlView(request, legacy, decision.state === "unavailable",
        priorRead.state === "read" ? priorRead.value : null, priorRead.state === "unavailable");
      const recorded = receiverDecision((view.assessment as JsonObject | null)?.decision);
      const readAt = view.read_at, readUnavailable = view.read_readback === "unavailable";
      const a = view.assessment as JsonObject | null;
      readback = {request_revision: view.revision, request_revision_digest: view.revision_digest, brief: view.brief,
        read: {status: readUnavailable ? "unavailable" : readAt !== null ? "supplied_to_receiver" : recorded ? "decision_exists_read_receipt_missing" : "not_recorded", at: readAt},
        decision: {status: recorded ?? (view.assessment_readback === "unavailable" ? "unavailable" : "not_recorded"),
          at: a?.assessed_at ?? null, reason: a?.reason ?? null},
        card_read_status: readUnavailable ? "unavailable" : readAt !== null ? "supplied" : "pending",
        card_decision: recorded ?? (view.assessment_readback === "unavailable" ? "unavailable" : "pending")};
      const results = collaborationResultObservations(request, row.results, recorded, legacy);
      const answered = results.some(result => result.value.phase === "conclusion"
        && (result.value.request_revision ?? 1) === view!.revision);
      const warnings: Warning[] = view.assessment_readback === "unavailable" ? ["decision_unreadable_or_conflicting"] : [];
      const kind: InboxState = warnings.length ? "receipt_unavailable"
        : view.control_state === "cancelled" ? "settled"
        : view.active_reservation !== null ? "awaiting_conclusion"
        : !recorded ? "pending" : answered ? "settled" : "awaiting_conclusion";
      return {kind, recorded_decision: recorded, warnings, request_control_view: view, readback,
        result_revisions: results.map(result => ({result_key: result.key, request_revision: result.value.request_revision ?? 1}))};
    } catch (error) {
      if (!(error instanceof EffectRuntimeRequestError)) throw error;
      return {kind: "receipt_unavailable", recorded_decision: null,
        warnings: ["conclusion_unreadable_or_conflicting"], request_control_view: view, readback, result_revisions: null};
    }
  }
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
