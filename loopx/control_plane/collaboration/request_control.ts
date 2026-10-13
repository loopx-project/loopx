/** Revision and reserved-effect facts in the existing exact request record.
 * The host holds the original request lock and authenticates source/receipt IO.
 * This owner never runs a Goal effect or treats a timeout as its terminal result.
 */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {parseExactGoalRef} from "../goals/goal_instance_identity.ts";
import {normalizeCollaborationBrief, planCollaborationRequestPublication, receiverDecision, sameRequest, type ReceiverDecision} from "./semantic_request.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {REQUEST_CONTROL_SCHEMA, REQUEST_AMENDMENT_RESULT_SCHEMA, REQUEST_AMENDMENT_OUTCOMES,
  type RequestAmendmentOutcome} from "./request_control_contract.ts";
type Revision = {revision: number; brief: JsonObject; reason: string; created_at: string;
  read_at: string | null; assessment: {decision: ReceiverDecision; reason: string; assessed_at: string} | null};
type Reservation = {binding: JsonObject; digest: string; terminal: JsonObject | null};
type Control = {schema_version: typeof REQUEST_CONTROL_SCHEMA; request_sha256: string;
  revisions: Revision[]; cancellation: JsonObject | null; reservations: Reservation[];
  operations: {operation_id: string; request_sha256: string; result: JsonObject}[]};
const MAX_OPERATIONS = 64, MAX_REVISIONS = 8, MAX_RESERVATIONS = 16;
const OP_FIELDS = {
  inspect: [], revise: ["expected_revision", "expected_digest", "brief", "reason"],
  cancel: ["expected_revision", "expected_digest", "reason"],
  read: ["revision", "revision_digest"], assess: ["revision", "revision_digest", "decision", "reason"],
  reserve: ["expected_revision", "expected_digest"], settle: ["reservation_digest"],
} as const;
function ensure(value: unknown, message: string, code = "invalid_request"): asserts value {
  if (!value) throw new EffectRuntimeRequestError(message, code);
}
function obj(value: unknown, name: string): JsonObject {return requireJsonObject(value, name);}
function keys(value: JsonObject, fields: readonly string[]): void {
  ensure(Object.keys(value).length === fields.length && fields.every(k => Object.hasOwn(value, k)), "request control fields are invalid");
}
function text(value: unknown, name: string, max = 2000): string {
  ensure(typeof value === "string" && value.trim() === value && value.length > 0 && value.length <= max && !value.includes("\0"), `${name} must be bounded nonempty text`);
  return value;
}
function id(value: unknown, name: string): string {
  const result = text(value, name, 160);
  ensure(/^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(result), `${name} must be a safe identifier`); return result;
}
function digest(value: unknown, name: string): string {
  ensure(typeof value === "string" && BARE_SHA256_PATTERN.test(value), `${name} must be SHA-256`); return value;
}
function integer(value: unknown, name: string): number {
  ensure(Number.isSafeInteger(value) && Number(value) > 0, `${name} must be a positive integer`); return Number(value);
}
function timestamp(value: unknown): string {
  const result = text(value, "request observation time", 64);
  ensure(Number.isFinite(Date.parse(result)), "request observation time is invalid"); return result;
}
function array(value: unknown, max: number): unknown[] {ensure(Array.isArray(value) && value.length <= max, "request control capacity exceeded"); return value;}
function same(a: unknown, b: unknown): boolean {return canonicalAuthoritySha256(a) === canonicalAuthoritySha256(b);}
function recoverableRecordBytes(entry: JsonObject, state: Control): number {
  const retained = structuredClone(state);
  const active = retained.reservations.find(row => row.terminal === null);
  if (retained.cancellation === null) {
    // JSON escaping can cost six bytes per permitted UTF-16 code unit, even
    // though ordinary CJK text costs three. Account for the declared input
    // bounds, not an assumed short English cancellation message.
    retained.cancellation = {operation_id: "c".repeat(160), reason: "\u0001".repeat(2000), requested_at: "0".repeat(64)};
    retained.operations.push({operation_id: "c".repeat(160), request_sha256: "f".repeat(64),
      result: {operation: "cancel", revision: retained.revisions.length, reserved_effect_pending: active !== undefined}});
  }
  if (active) {
    const terminal = {operation_id: active.binding.operation_id, reservation_digest: active.digest, outcome: "committed",
      goal_ref: active.binding.goal_ref, goal_changed: true, goal_revision: Number.MAX_SAFE_INTEGER,
      todo_id: "t".repeat(160), receipt_sha256: "f".repeat(64)};
    active.terminal = terminal;
    retained.operations.push({operation_id: "s".repeat(160), request_sha256: "f".repeat(64), result: {operation: "settle", terminal}});
  }
  return Buffer.byteLength(JSON.stringify({...entry, request_control: retained}), "utf8");
}
function goalRef(value: unknown): JsonObject {
  const ref = obj(value, "request Goal reference"); keys(ref, ["goal_id", "goal_instance_id"]);
  ensure(parseExactGoalRef(ref).kind === "parsed", "request needs an exact Goal instance"); return ref;
}
export function requestOriginalDigest(entry: JsonObject): string {
  return canonicalAuthoritySha256(Object.fromEntries(Object.entries(entry).filter(([key]) =>
    !["instruction", "delivered_at", "source_channel", "request_control"].includes(key))));
}
function initial(entry: JsonObject): Control {
  ensure(entry.schema_version === "loopx_manager_context_entry_v2", "request control requires the exact-instance source profile");
  const ref = goalRef(entry.goal_ref);
  ensure(ref.goal_id === entry.goal_id && !Object.hasOwn(entry, "execution_binding_id"), "request profile cannot control an independently bound host execution");
  digest(entry.request_id, "request_id"); id(entry.agent_id, "receiver Agent"); text(entry.source_id, "source_id", 512);
  return {schema_version: REQUEST_CONTROL_SCHEMA, request_sha256: requestOriginalDigest(entry),
    revisions: [{revision: 1, brief: normalizeCollaborationBrief(entry.brief), reason: "Original request",
      created_at: timestamp(entry.delivered_at), read_at: null, assessment: null}],
    cancellation: null, reservations: [], operations: []};
}
export function requestRevisionDigest(entry: JsonObject, revision: Revision): string {
  return canonicalAuthoritySha256({request_sha256: requestOriginalDigest(entry), revision: revision.revision,
    brief: revision.brief, reason: revision.reason});
}
export function normalizeRequestBasis(value: unknown): JsonObject {
  const row = obj(value, "source request basis"); keys(row, ["request_id", "request_revision", "request_digest"]);
  return {request_id: digest(row.request_id, "source request id"), request_revision: integer(row.request_revision, "source request revision"),
    request_digest: digest(row.request_digest, "source request digest")};
}
function binding(value: unknown, entry: JsonObject, revisions: Revision[], epoch: number): JsonObject {
  const row = obj(value, "request amendment binding");
  keys(row, ["request_id", "request_revision", "request_digest", "receiver_agent_id", "goal_ref", "attempt_epoch",
    "authority_source_sha256", "proposal_id", "proposal_digest", "base_goal_revision", "base_intent_digest", "base_work_digest", "operation_id"]);
  const rev = revisions[integer(row.request_revision, "request revision") - 1];
  ensure(rev && row.request_id === entry.request_id && row.receiver_agent_id === entry.agent_id
    && row.request_digest === requestRevisionDigest(entry, rev) && same(goalRef(row.goal_ref), entry.goal_ref)
    && row.attempt_epoch === epoch, "request amendment binding identity mismatch");
  for (const key of ["authority_source_sha256", "proposal_digest", "base_intent_digest", "base_work_digest"]) digest(row[key], key);
  id(row.proposal_id, "proposal_id"); id(row.operation_id, "effect operation_id"); integer(row.base_goal_revision, "base Goal revision");
  return row;
}
export function requestTerminalSummary(value: unknown, reservation: Reservation): JsonObject {
  const row = obj(value, "Goal terminal receipt");
  keys(row, ["operation_id", "reservation_digest", "outcome", "goal_ref", "goal_changed", "goal_revision", "todo_id", "receipt_sha256"]);
  ensure(row.operation_id === reservation.binding.operation_id && row.reservation_digest === reservation.digest
    && same(row.goal_ref, reservation.binding.goal_ref)
    && REQUEST_AMENDMENT_OUTCOMES.includes(row.outcome as RequestAmendmentOutcome), "Goal terminal receipt does not match the reserved operation");
  digest(row.receipt_sha256, "Goal terminal receipt digest");
  ensure(row.goal_changed === (row.outcome === "committed"), "Goal terminal receipt has inconsistent effect facts");
  if (row.goal_changed) {
    ensure(integer(row.goal_revision, "resulting Goal revision") === Number(reservation.binding.base_goal_revision) + 1,
      "committed Goal revision does not follow the reserved basis");
    id(row.todo_id, "created Todo");
  } else ensure(row.goal_revision === null && row.todo_id === null, "no-effect terminal receipt cannot claim Goal work");
  return row;
}
export function readRequestControl(entry: JsonObject): Control {
  const original = initial(entry);
  if (!Object.hasOwn(entry, "request_control")) return original;
  const raw = obj(entry.request_control, "request_control");
  keys(raw, ["schema_version", "request_sha256", "revisions", "cancellation", "reservations", "operations"]);
  ensure(raw.schema_version === REQUEST_CONTROL_SCHEMA && raw.request_sha256 === original.request_sha256, "request control source identity changed");
  const revisions: Revision[] = array(raw.revisions, MAX_REVISIONS).map((value, i) => {
    const row = obj(value, "request revision"); keys(row, ["revision", "brief", "reason", "created_at", "read_at", "assessment"]);
    ensure(row.revision === i + 1, "request revisions must be complete and ordered");
    const brief = normalizeCollaborationBrief(row.brief);
    ensure(same(brief, row.brief), "request revision brief is not canonical");
    let assessment: Revision["assessment"] = null;
    if (row.assessment !== null) {
      const a = obj(row.assessment, "request assessment"); keys(a, ["decision", "reason", "assessed_at"]);
      const decision = receiverDecision(a.decision); ensure(decision !== null, "request assessment is invalid");
      assessment = {decision, reason: text(a.reason, "assessment reason"), assessed_at: timestamp(a.assessed_at)};
    }
    return {revision: i + 1, brief, reason: text(row.reason, "revision reason"), created_at: timestamp(row.created_at),
      read_at: row.read_at === null ? null : timestamp(row.read_at), assessment};
  });
  ensure(revisions.length > 0 && same(revisions[0].brief, original.revisions[0].brief)
    && revisions[0].reason === original.revisions[0].reason && revisions[0].created_at === original.revisions[0].created_at,
  "request control must preserve the original declaration");
  const reservations: Reservation[] = array(raw.reservations, MAX_RESERVATIONS).map((value, i) => {
    const row = obj(value, "request reservation"); keys(row, ["binding", "digest", "terminal"]);
    const bound = binding(row.binding, entry, revisions, i + 1);
    ensure(row.digest === canonicalAuthoritySha256(bound), "request reservation digest changed");
    const result: Reservation = {binding: bound, digest: String(row.digest), terminal: null};
    if (row.terminal !== null) result.terminal = requestTerminalSummary(row.terminal, result);
    return result;
  });
  ensure(reservations.filter(r => r.terminal === null).length <= 1
    && reservations.slice(0, -1).every(r => r.terminal !== null)
    && new Set(reservations.map(r => r.binding.operation_id)).size === reservations.length, "request has conflicting effect reservations");
  let cancellation: JsonObject | null = null;
  if (raw.cancellation !== null) {
    const c = obj(raw.cancellation, "request cancellation"); keys(c, ["operation_id", "reason", "requested_at"]);
    cancellation = {operation_id: id(c.operation_id, "cancellation operation"), reason: text(c.reason, "cancellation reason"), requested_at: timestamp(c.requested_at)};
  }
  const operations = array(raw.operations, MAX_OPERATIONS).map(value => {
    const row = obj(value, "request operation receipt"); keys(row, ["operation_id", "request_sha256", "result"]);
    return {operation_id: id(row.operation_id, "request operation"), request_sha256: digest(row.request_sha256, "request operation digest"), result: obj(row.result, "request operation result")};
  });
  ensure(new Set(operations.map(r => r.operation_id)).size === operations.length, "duplicate request operation receipt");
  return {schema_version: REQUEST_CONTROL_SCHEMA, request_sha256: original.request_sha256, revisions, cancellation, reservations, operations};
}
function assessmentObservation(entry: JsonObject, revision: Revision, legacy: unknown): {value: Revision["assessment"]; unavailable: boolean} {
  if (revision.assessment !== null || revision.revision !== 1 || legacy === null) return {value: revision.assessment, unavailable: false};
  try {
    const row = obj(legacy, "original receiver assessment"), decision = receiverDecision(row.decision);
    ensure(sameRequest(row, entry, false) && decision !== null, "original receiver assessment conflicts with the request");
    return {value: {decision, reason: text(row.reason, "original assessment reason"), assessed_at: timestamp(row.decided_at)}, unavailable: false};
  } catch (error) {
    if (!(error instanceof EffectRuntimeRequestError)) throw error;
    return {value: null, unavailable: true};
  }
}
export function requestRevisionReceiptContext(entry: JsonObject, requested: unknown, legacy: unknown = null): JsonObject {
  const state = readRequestControl(entry), number = integer(requested ?? 1, "result request revision");
  const revision = state.revisions[number - 1];
  ensure(revision, "result refers to an unknown request revision");
  return {revision: number, revision_digest: requestRevisionDigest(entry, revision), current_revision: state.revisions.length,
    assessment: assessmentObservation(entry, revision, legacy).value, read_at: revision.read_at, brief: revision.brief};
}
/** Host receipt for the content actually supplied by the existing inbox read.
 * Legacy consumers without a revision marker supplied the immutable original.
 */
export function recordControlledRequestRead(params: JsonObject): JsonObject {
  const entry = obj(params.entry, "read request"), state = readRequestControl(entry);
  const revision = state.revisions[integer(params.revision ?? 1, "supplied request revision") - 1];
  ensure(revision, "supplied request revision is unavailable");
  const basis = requestRevisionDigest(entry, revision);
  ensure(params.revision_digest == null ? revision.revision === 1 : params.revision_digest === basis,
    "supplied request digest does not match its revision");
  const plan = planRequestControl({entry, input: {operation: "read", operation_id: "read:" + basis,
    revision: revision.revision, revision_digest: basis}, source: {actor: "receiver", actor_agent_id: params.actor_agent_id,
    observed_at: params.observed_at, legacy_assessment: params.legacy_assessment ?? null,
    legacy_assessment_unavailable: params.legacy_assessment_unavailable === true}});
  return {...plan, readback: {supplied_revision: revision.revision, revision_digest: basis,
    current_revision_at_record: state.revisions.length, model_comprehension_verified: false}};
}
export function recordControlledRequestAssessment(params: JsonObject): JsonObject {
  const entry = obj(params.entry, "assessed request"), state = readRequestControl(entry);
  const revision = state.revisions[integer(params.revision ?? 1, "assessed request revision") - 1];
  ensure(revision, "assessed request revision is unavailable");
  const basis = requestRevisionDigest(entry, revision);
  ensure(params.revision_digest == null ? revision.revision === 1 : params.revision_digest === basis,
    "assessed request digest does not match its revision");
  const decision = receiverDecision(params.decision), reason = text(params.reason, "assessment reason");
  ensure(decision !== null && params.actor_agent_id === entry.agent_id, "request assessment actor or decision is invalid");
  const recorded = assessmentObservation(entry, revision, params.legacy_assessment ?? null).value;
  if (recorded !== null) {
    ensure(recorded.decision === decision && recorded.reason === reason, "request revision already has a different assessment");
    return {changed: false, replayed: true, entry, result: {revision: revision.revision, decision},
      view: requestControlView(entry, params.legacy_assessment ?? null, params.legacy_assessment_unavailable === true,
        params.legacy_read ?? null, params.legacy_read_unavailable === true)};
  }
  return planRequestControl({entry, input: {operation: "assess", operation_id: "assess:" + canonicalAuthoritySha256({basis, decision, reason}),
    revision: revision.revision, revision_digest: basis, decision, reason}, source: {actor: "receiver", actor_agent_id: params.actor_agent_id,
    goal_active: params.goal_active, observed_at: params.observed_at, legacy_assessment: params.legacy_assessment ?? null,
    legacy_assessment_unavailable: params.legacy_assessment_unavailable === true,
    legacy_read: params.legacy_read ?? null, legacy_read_unavailable: params.legacy_read_unavailable === true}});
}
export function requestControlView(entry: JsonObject, legacyAssessment: unknown = null, legacyUnavailable = false,
    legacyRead: unknown = null, legacyReadUnavailable = false): JsonObject {
  const state = readRequestControl(entry), current = state.revisions.at(-1)!;
  const active = state.reservations.find(r => r.terminal === null) ?? null;
  const correctionPending = active !== null && Number(active.binding.request_revision) < current.revision;
  const observed = assessmentObservation(entry, current, legacyAssessment), recorded = observed.value;
  let readAt = current.read_at, readUnavailable = false;
  if (readAt === null && current.revision === 1) {
    if (legacyRead !== null) {
      const value = typeof legacyRead === "object" && !Array.isArray(legacyRead) ? legacyRead as JsonObject : null;
      if (sameRequest(value, entry, false) && value.kind === "receiver_cli_read" && typeof value.read_at === "string") readAt = value.read_at;
      else readUnavailable = true;
    }
    readUnavailable ||= legacyReadUnavailable;
  }
  const instruction = active
    ? `Recover original effect ${active.binding.operation_id} before another effect for this request. ${state.cancellation || correctionPending ? "The source control is pending its terminal receipt, not proof of no change." : "A timeout does not release the reservation."}`
    : state.cancellation ? "This request is cancelled. Retain historical results and committed Goal changes; do not start new work for this request."
    : `Use request revision ${current.revision} and its exact digest for assessment and request-derived effects. Return results with request_revision=${current.revision}; earlier observations remain bound to their original revisions. Request context grants no new Goal or execution permission.`;
  return {schema_version: REQUEST_CONTROL_SCHEMA, request_id: entry.request_id, goal_ref: entry.goal_ref,
    receiver_agent_id: entry.agent_id, revision: current.revision, control_version: state.operations.length,
    revision_digest: requestRevisionDigest(entry, current),
    brief: current.brief, reason: current.reason, read_at: readAt,
    read_readback: readAt !== null ? "recorded" : readUnavailable ? "unavailable" : "not_recorded",
    assessment: recorded,
    assessment_readback: recorded !== null ? "recorded" : current.revision === 1 && (legacyUnavailable || observed.unavailable) ? "unavailable" : "not_recorded",
    control_state: state.cancellation ? (active ? "cancellation_pending" : "cancelled") : (active ? "effect_pending" : "open"),
    cancellation: state.cancellation, active_reservation: active,
    recovery_operation: active === null ? null : state.cancellation || correctionPending ? "abort_request" : "read_receipt",
    receiver_instruction: instruction,
    terminal_effects: state.reservations.filter(r => r.terminal !== null),
    execution_authority_granted: false};
}

export function planRequestControl(params: JsonObject): JsonObject {
  const entry = obj(params.entry, "request entry"), input = obj(params.input, "request operation");
  const source = obj(params.source, "authenticated request source");
  const project = (value: JsonObject) => requestControlView(value, source.legacy_assessment ?? null,
    source.legacy_assessment_unavailable === true, source.legacy_read ?? null, source.legacy_read_unavailable === true);
  const operation = String(input.operation);
  ensure(Object.hasOwn(OP_FIELDS, operation), "unsupported request control operation");
  const op = operation as keyof typeof OP_FIELDS;
  keys(input, op === "inspect" ? ["operation"] : ["operation", "operation_id", ...OP_FIELDS[op]]);
  const state = readRequestControl(entry);
  let requestedBasis: JsonObject | null = null;
  if (source.requested_basis != null) {
    requestedBasis = normalizeRequestBasis(source.requested_basis);
    const revision = state.revisions[Number(requestedBasis.request_revision) - 1];
    ensure(revision && requestedBasis.request_id === entry.request_id && requestedBasis.request_digest === requestRevisionDigest(entry, revision),
      "source request basis does not identify a retained revision");
  }
  if (op === "inspect") return {changed: false, verified_request_basis: requestedBasis,
    view: project(entry)};
  const operationId = id(input.operation_id, "request operation_id");
  const requestSha = canonicalAuthoritySha256({input,
    ...(op === "settle" ? {} : {actor: source.actor, actor_agent_id: source.actor_agent_id ?? null}),
    ...(op === "reserve" ? {amendment: source.amendment} : {}), ...(op === "settle" ? {terminal: source.terminal} : {})});
  ensure(source.actor === "source" || source.actor === "receiver", "request actor is not authenticated");
  if (source.actor === "receiver") ensure(source.actor_agent_id === entry.agent_id, "request receiver identity mismatch");
  else ensure(entry.source_kind === "peer" ? source.actor_agent_id === entry.source_agent_id : source.actor_agent_id === null,
    "request source actor identity mismatch");
  const original = state.operations.find(row => row.operation_id === operationId);
  if (original) {
    ensure(original.request_sha256 === requestSha, "request operation identity conflict", "request_operation_conflict");
    return {changed: false, replayed: true, result: original.result, entry,
      view: project(entry)};
  }
  const now = timestamp(source.observed_at), current = state.revisions.at(-1)!;
  const active = state.reservations.find(r => r.terminal === null);
  if (op === "revise" || op === "assess" || op === "reserve") ensure(source.goal_active === true,
    "inactive Goal permits request cancellation and recovery, not new request work");
  let result: JsonObject;
  if (op === "revise" || op === "cancel" || op === "reserve") {
    ensure(input.expected_revision === current.revision && input.expected_digest === requestRevisionDigest(entry, current),
      "request basis changed; inspect the original request before retrying", "request_basis_changed");
  }
  if (op === "revise" || op === "cancel") {
    ensure(source.actor === "source", "only the authenticated source may correct or cancel this request");
    ensure(state.cancellation === null, "request cancellation already recorded; recover its original operation");
    if (op === "revise") {
      ensure(state.revisions.length < MAX_REVISIONS, "request revision capacity exceeded");
      state.revisions.push({revision: current.revision + 1, brief: normalizeCollaborationBrief(input.brief),
        reason: text(input.reason, "correction reason"), created_at: now, read_at: null, assessment: null});
    } else state.cancellation = {operation_id: operationId, reason: text(input.reason, "cancellation reason"), requested_at: now};
    result = {operation: op, revision: state.revisions.length, reserved_effect_pending: !!active};
  } else if (op === "read" || op === "assess") {
    ensure(source.actor === "receiver", "only this request receiver may record consumption");
    const revision = state.revisions[integer(input.revision, "observed revision") - 1];
    ensure(revision && input.revision_digest === requestRevisionDigest(entry, revision), "observed request revision identity mismatch");
    if (op === "read") revision.read_at ??= now;
    else {
      ensure(revision === current && state.cancellation === null, "a stale or cancelled request cannot receive a new assessment");
      const decision = receiverDecision(input.decision); ensure(decision !== null, "receiver decision is invalid");
      const value = {decision, reason: text(input.reason, "assessment reason"), assessed_at: now};
      ensure(revision.assessment === null, "request revision already assessed; recover its original operation");
      revision.assessment = value;
    }
    result = {operation: op, revision: revision.revision};
  } else if (op === "reserve") {
    ensure(source.actor === "receiver" && state.cancellation === null && !active, "request cannot reserve another effect");
    ensure(assessmentObservation(entry, current, source.legacy_assessment ?? null).value?.decision === "adopt", "receiver must adopt this exact request revision before reserving an effect");
    ensure(state.reservations.length < MAX_RESERVATIONS, "request effect capacity exceeded");
    const amendment = obj(source.amendment, "source-resolved amendment");
    const bound = binding({...amendment, request_id: entry.request_id, request_revision: current.revision,
      request_digest: requestRevisionDigest(entry, current), receiver_agent_id: entry.agent_id,
      goal_ref: entry.goal_ref, attempt_epoch: state.reservations.length + 1}, entry, state.revisions, state.reservations.length + 1);
    ensure(!state.reservations.some(r => r.binding.operation_id === bound.operation_id), "effect operation already reserved; recover its original request receipt");
    const reservation: Reservation = {binding: bound, digest: canonicalAuthoritySha256(bound), terminal: null};
    state.reservations.push(reservation); result = {operation: op, reservation};
  } else {
    const reserved = state.reservations.find(r => r.digest === input.reservation_digest);
    ensure(reserved, "original request reservation is unavailable");
    const terminal = requestTerminalSummary(source.terminal, reserved);
    ensure(reserved.terminal === null || same(reserved.terminal, terminal), "request terminal result conflicts with its original Goal receipt");
    reserved.terminal = terminal; result = {operation: op, terminal};
  }
  // Leave room for a cancellation and its terminal linkage. Neither a full
  // history nor an oversized correction may strand an admitted effect.
  const limit = op === "settle" ? MAX_OPERATIONS : op === "cancel" ? MAX_OPERATIONS - 1 : MAX_OPERATIONS - 2;
  ensure(state.operations.length < limit, "request operation capacity reached; preserve and recover existing operations");
  state.operations.push({operation_id: operationId, request_sha256: requestSha, result});
  const updated = {...entry, request_control: state};
  const bytes = op === "settle" ? Buffer.byteLength(JSON.stringify(updated), "utf8") : recoverableRecordBytes(entry, state);
  ensure(bytes <= 128000, "request record capacity reached; preserve cancellation and terminal recovery space");
  readRequestControl(updated);
  return {changed: true, replayed: false, result, entry: updated,
    view: project(updated)};
}

/** Internal lifecycle state is not part of the original sender's request.
 * Validate it through its owner before excluding it from replay comparison.
 */
export function planControlledRequestPublication(params: JsonObject): JsonObject {
  const request = obj(params.request, "request");
  ensure(!Object.hasOwn(request, "request_control"), "request control cannot be supplied by an ingress caller");
  const entry = params.entry === null ? null : obj(params.entry, "entry");
  if (entry === null || !Object.hasOwn(entry, "request_control")) return planCollaborationRequestPublication(params);
  readRequestControl(entry);
  const original = {...entry}; delete original.request_control;
  const plan = planCollaborationRequestPublication({...params, entry: original});
  return {...plan, entry};
}

/** Current request readback and historical Goal outcome are independent facts. */
export function requestAmendmentReadback(params: JsonObject): JsonObject {
  const entry = obj(params.entry, "request source snapshot"), state = readRequestControl(entry);
  const reservation = state.reservations.find(row => row.digest === params.reservation_digest);
  ensure(reservation, "request effect recovery requires its original reservation");
  const terminal = params.terminal === null ? null : requestTerminalSummary(params.terminal, reservation);
  const current = params.source_current === true;
  const linked = current && terminal !== null && reservation.terminal !== null && same(reservation.terminal, terminal);
  return {schema_version: REQUEST_AMENDMENT_RESULT_SCHEMA, ok: linked, request_id: entry.request_id,
    goal_ref: entry.goal_ref, effect: terminal, request_linked: linked,
    request_control: current ? requestControlView(entry, params.legacy_assessment ?? null, params.legacy_assessment_unavailable === true,
      params.legacy_read ?? null, params.legacy_read_unavailable === true) : null,
    recovery_required: !linked, effect_operation_id: reservation.binding.operation_id,
    execution_authority_granted: false};
}
