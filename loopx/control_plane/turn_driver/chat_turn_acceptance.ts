import {createHash} from "node:crypto";

import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {
  requireBoolean,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringLiteral,
} from "../runtime_decode.ts";

export const CHAT_TURN_ACCEPTANCE_REQUEST_SCHEMA =
  "loopx_chat_turn_acceptance_request_v0";
export const CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA =
  "loopx_chat_turn_acceptance_result_v0";
export const CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA =
  "loopx_chat_turn_acceptance_v0";

const TURN_STATUSES = [
  "queued",
  "starting",
  "running",
  "completing",
  "interrupting",
  "completed",
  "interrupted",
  "timed_out",
  "failed",
] as const;
const TERMINAL_TURN_STATUSES = new Set<TurnStatus>([
  "completed",
  "interrupted",
  "timed_out",
  "failed",
]);
const OPAQUE_ID = /^[A-Za-z0-9._-]{1,160}$/;
const SHA256 = /^sha256:[0-9a-f]{64}$/;
const EMPTY_OBJECT_SHA256 = sha256("{}");

type OpaqueId = string & {readonly __brand: "OpaqueId"};
type Sha256 = string & {readonly __brand: "Sha256"};
type TurnStatus = (typeof TURN_STATUSES)[number];

interface RequestFacts {
  readonly sessionId: OpaqueId;
  readonly clientTurnId: OpaqueId;
  readonly executionMessageSha256: Sha256;
  readonly displayMessageSha256: Sha256;
  readonly attachmentsSha256: Sha256;
  readonly origin: OpaqueId;
  readonly loopxExecution: boolean;
  readonly loopxRequestSha256: Sha256;
}

interface CandidateFacts {
  readonly turnId: OpaqueId;
  readonly messageId: OpaqueId;
  readonly acceptedAt: string;
}

interface SessionFacts {
  readonly status: string;
  readonly activeTurnId: OpaqueId | null;
}

interface ActiveTurnFacts {
  readonly turnId: OpaqueId;
  readonly status: TurnStatus;
}

interface AcceptanceCapsuleFacts {
  readonly requestSha256: Sha256;
  readonly messageId: OpaqueId;
  readonly displayMessageSha256: Sha256;
  readonly attachmentsSha256: Sha256;
}

interface MatchingTurnFacts {
  readonly turnId: OpaqueId;
  readonly clientTurnId: OpaqueId;
  readonly status: TurnStatus;
  readonly executionMessageSha256: Sha256;
  readonly origin: OpaqueId;
  readonly loopxExecution: boolean;
  readonly loopxRequestSha256: Sha256;
  readonly acceptance: AcceptanceCapsuleFacts | null;
}

type TranscriptFacts =
  | {readonly kind: "absent"}
  | {
      readonly kind: "single";
      readonly messageId: OpaqueId;
      readonly displayMessageSha256: Sha256;
      readonly attachmentsSha256: Sha256;
      readonly origin: OpaqueId;
    }
  | {readonly kind: "ambiguous"; readonly count: number};

type QueuedEventFacts =
  | {readonly kind: "absent"}
  | {readonly kind: "single"; readonly payloadSha256: Sha256}
  | {readonly kind: "ambiguous"; readonly count: number};

type PreparedTurnFacts =
  | {readonly kind: "absent"}
  | {
      readonly kind: "single";
      readonly turnId: OpaqueId;
      readonly clientTurnId: OpaqueId;
    }
  | {readonly kind: "ambiguous"; readonly count: number};

interface AcceptanceFacts {
  readonly request: RequestFacts;
  readonly candidate: CandidateFacts;
  readonly session: SessionFacts | null;
  readonly activeTurn: ActiveTurnFacts | null;
  readonly matchingTurn: MatchingTurnFacts | null;
  readonly transcript: TranscriptFacts;
  readonly queuedEvent: QueuedEventFacts;
  readonly preparedTurn: PreparedTurnFacts;
}

export type ChatTurnAcceptanceRejectionCode =
  | "session_not_found"
  | "session_closed"
  | "request_conflict"
  | "active_turn_conflict"
  | "original_request_unavailable"
  | "durable_state_conflict";

type ChatTurnDispatch =
  | {readonly kind: "required"}
  | {
      readonly kind: "not_required";
      readonly reason:
        | "already_started"
        | "completion_in_progress"
        | "terminal";
    };

interface AcceptanceWrites {
  readonly prepare_turn: boolean;
  readonly activate_session: boolean;
  readonly append_message: boolean;
  readonly append_queued_event: boolean;
  readonly settle_turn: boolean;
}

export type ChatTurnAcceptancePlan =
  | {
      readonly schema_version: typeof CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA;
      readonly kind: "accepted";
      readonly disposition: "created" | "repaired" | "replayed";
      readonly turn_id: string;
      readonly message_id: string;
      readonly request_sha256: string;
      readonly created: boolean;
      readonly writes: AcceptanceWrites;
      readonly dispatch: ChatTurnDispatch;
    }
  | {
      readonly schema_version: typeof CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA;
      readonly kind: "rejected";
      readonly code: ChatTurnAcceptanceRejectionCode;
      readonly active_turn_id?: string;
    };

function sha256(value: string): Sha256 {
  return `sha256:${createHash("sha256").update(value).digest("hex")}` as Sha256;
}

function opaqueId(value: unknown, label: string): OpaqueId {
  const candidate = requireNonEmptyString(value, label);
  if (!OPAQUE_ID.test(candidate)) {
    throw new EffectRuntimeRequestError(`${label} must be a compact opaque id`);
  }
  return candidate as OpaqueId;
}

function digest(value: unknown, label: string): Sha256 {
  const candidate = requireNonEmptyString(value, label);
  if (!SHA256.test(candidate)) {
    throw new EffectRuntimeRequestError(`${label} must be a SHA-256 digest`);
  }
  return candidate as Sha256;
}

function optionalOpaqueId(value: unknown, label: string): OpaqueId | null {
  if (value === null || value === undefined) return null;
  return opaqueId(value, label);
}

function turnStatus(value: unknown, label: string): TurnStatus {
  return requireStringLiteral(value, TURN_STATUSES, label);
}

function decodeRequest(value: unknown): RequestFacts {
  const request = requireJsonObject(value, "request");
  return {
    sessionId: opaqueId(request.session_id, "request.session_id"),
    clientTurnId: opaqueId(
      request.client_turn_id,
      "request.client_turn_id",
    ),
    executionMessageSha256: digest(
      request.execution_message_sha256,
      "request.execution_message_sha256",
    ),
    displayMessageSha256: digest(
      request.display_message_sha256,
      "request.display_message_sha256",
    ),
    attachmentsSha256: digest(
      request.attachments_sha256,
      "request.attachments_sha256",
    ),
    origin: opaqueId(request.origin, "request.origin"),
    loopxExecution: requireBoolean(
      request.loopx_execution,
      "request.loopx_execution",
    ),
    loopxRequestSha256: digest(
      request.loopx_request_sha256,
      "request.loopx_request_sha256",
    ),
  };
}

function decodeCandidate(value: unknown): CandidateFacts {
  const candidate = requireJsonObject(value, "candidate");
  return {
    turnId: opaqueId(candidate.turn_id, "candidate.turn_id"),
    messageId: opaqueId(candidate.message_id, "candidate.message_id"),
    acceptedAt: requireNonEmptyString(
      candidate.accepted_at,
      "candidate.accepted_at",
    ),
  };
}

function decodeSession(value: unknown): SessionFacts | null {
  if (value === null || value === undefined) return null;
  const session = requireJsonObject(value, "session");
  return {
    status: requireNonEmptyString(session.status, "session.status"),
    activeTurnId: optionalOpaqueId(
      session.active_turn_id,
      "session.active_turn_id",
    ),
  };
}

function decodeActiveTurn(value: unknown): ActiveTurnFacts | null {
  if (value === null || value === undefined) return null;
  const turn = requireJsonObject(value, "active_turn");
  return {
    turnId: opaqueId(turn.turn_id, "active_turn.turn_id"),
    status: turnStatus(turn.status, "active_turn.status"),
  };
}

function decodeAcceptance(value: unknown): AcceptanceCapsuleFacts | null {
  if (value === null || value === undefined) return null;
  const acceptance = requireJsonObject(value, "matching_turn.acceptance");
  requireStringLiteral(
    acceptance.schema_version,
    [CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA] as const,
    "matching_turn.acceptance.schema_version",
  );
  requireStringLiteral(
    acceptance.phase,
    ["prepared"] as const,
    "matching_turn.acceptance.phase",
  );
  return {
    requestSha256: digest(
      acceptance.request_sha256,
      "matching_turn.acceptance.request_sha256",
    ),
    messageId: opaqueId(
      acceptance.message_id,
      "matching_turn.acceptance.message_id",
    ),
    displayMessageSha256: digest(
      acceptance.display_message_sha256,
      "matching_turn.acceptance.display_message_sha256",
    ),
    attachmentsSha256: digest(
      acceptance.attachments_sha256,
      "matching_turn.acceptance.attachments_sha256",
    ),
  };
}

function decodeMatchingTurn(value: unknown): MatchingTurnFacts | null {
  if (value === null || value === undefined) return null;
  const turn = requireJsonObject(value, "matching_turn");
  return {
    turnId: opaqueId(turn.turn_id, "matching_turn.turn_id"),
    clientTurnId: opaqueId(
      turn.client_turn_id,
      "matching_turn.client_turn_id",
    ),
    status: turnStatus(turn.status, "matching_turn.status"),
    executionMessageSha256: digest(
      turn.execution_message_sha256,
      "matching_turn.execution_message_sha256",
    ),
    origin: opaqueId(turn.origin, "matching_turn.origin"),
    loopxExecution: requireBoolean(
      turn.loopx_execution,
      "matching_turn.loopx_execution",
    ),
    loopxRequestSha256: digest(
      turn.loopx_request_sha256,
      "matching_turn.loopx_request_sha256",
    ),
    acceptance: decodeAcceptance(turn.acceptance),
  };
}

function observedCount(value: JsonObject, label: string): number {
  const count = requireInteger(value.count, `${label}.count`);
  if (count < 0) {
    throw new EffectRuntimeRequestError(`${label}.count must not be negative`);
  }
  return count;
}

function decodeTranscript(value: unknown): TranscriptFacts {
  const transcript = requireJsonObject(value, "transcript");
  const count = observedCount(transcript, "transcript");
  if (count === 0) return {kind: "absent"};
  if (count > 1) return {kind: "ambiguous", count};
  return {
    kind: "single",
    messageId: opaqueId(transcript.message_id, "transcript.message_id"),
    displayMessageSha256: digest(
      transcript.display_message_sha256,
      "transcript.display_message_sha256",
    ),
    attachmentsSha256: digest(
      transcript.attachments_sha256,
      "transcript.attachments_sha256",
    ),
    origin: opaqueId(transcript.origin, "transcript.origin"),
  };
}

function decodeQueuedEvent(value: unknown): QueuedEventFacts {
  const event = requireJsonObject(value, "queued_event");
  const count = observedCount(event, "queued_event");
  if (count === 0) return {kind: "absent"};
  if (count > 1) return {kind: "ambiguous", count};
  return {
    kind: "single",
    payloadSha256: digest(
      event.payload_sha256,
      "queued_event.payload_sha256",
    ),
  };
}

function decodePreparedTurn(value: unknown): PreparedTurnFacts {
  const prepared = requireJsonObject(value, "prepared_turn");
  const count = observedCount(prepared, "prepared_turn");
  if (count === 0) return {kind: "absent"};
  if (count > 1) return {kind: "ambiguous", count};
  return {
    kind: "single",
    turnId: opaqueId(prepared.turn_id, "prepared_turn.turn_id"),
    clientTurnId: opaqueId(
      prepared.client_turn_id,
      "prepared_turn.client_turn_id",
    ),
  };
}

function decodeFacts(input: JsonObject): AcceptanceFacts {
  requireStringLiteral(
    input.schema_version,
    [CHAT_TURN_ACCEPTANCE_REQUEST_SCHEMA] as const,
    "schema_version",
  );
  return {
    request: decodeRequest(input.request),
    candidate: decodeCandidate(input.candidate),
    session: decodeSession(input.session),
    activeTurn: decodeActiveTurn(input.active_turn),
    matchingTurn: decodeMatchingTurn(input.matching_turn),
    transcript: decodeTranscript(input.transcript),
    queuedEvent: decodeQueuedEvent(input.queued_event),
    preparedTurn: decodePreparedTurn(input.prepared_turn),
  };
}

function requestIdentity(
  request: RequestFacts,
  displayMessageSha256 = request.displayMessageSha256,
  attachmentsSha256 = request.attachmentsSha256,
): Sha256 {
  return sha256(JSON.stringify({
    schema_version: "loopx_chat_turn_request_identity_v0",
    session_id: request.sessionId,
    client_turn_id: request.clientTurnId,
    execution_message_sha256: request.executionMessageSha256,
    display_message_sha256: displayMessageSha256,
    attachments_sha256: attachmentsSha256,
    origin: request.origin,
    loopx_execution: request.loopxExecution,
    loopx_request_sha256: request.loopxRequestSha256,
  }));
}

function storedRequestIdentity(
  request: RequestFacts,
  turn: MatchingTurnFacts,
  displayMessageSha256: Sha256,
  attachmentsSha256: Sha256,
): Sha256 {
  return requestIdentity(
    {
      sessionId: request.sessionId,
      clientTurnId: turn.clientTurnId,
      executionMessageSha256: turn.executionMessageSha256,
      displayMessageSha256,
      attachmentsSha256,
      origin: turn.origin,
      loopxExecution: turn.loopxExecution,
      loopxRequestSha256: turn.loopxRequestSha256,
    },
    displayMessageSha256,
    attachmentsSha256,
  );
}

function rejected(
  code: ChatTurnAcceptanceRejectionCode,
  activeTurnId?: OpaqueId,
): ChatTurnAcceptancePlan {
  return {
    schema_version: CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA,
    kind: "rejected",
    code,
    ...(activeTurnId ? {active_turn_id: activeTurnId} : {}),
  };
}

function dispatchFor(status: TurnStatus): ChatTurnDispatch {
  if (status === "queued") return {kind: "required"};
  if (status === "starting" || status === "running") {
    return {kind: "not_required", reason: "already_started"};
  }
  if (status === "completing" || status === "interrupting") {
    return {kind: "not_required", reason: "completion_in_progress"};
  }
  return {kind: "not_required", reason: "terminal"};
}

function activeObservationIsConsistent(
  session: SessionFacts,
  activeTurn: ActiveTurnFacts | null,
): boolean {
  return session.activeTurnId === null
    ? activeTurn === null
    : activeTurn !== null && activeTurn.turnId === session.activeTurnId;
}

function activeTurnBlocks(
  activeTurn: ActiveTurnFacts | null,
  acceptedTurnId: OpaqueId,
): OpaqueId | null {
  if (
    activeTurn === null ||
    activeTurn.turnId === acceptedTurnId ||
    TERMINAL_TURN_STATUSES.has(activeTurn.status)
  ) {
    return null;
  }
  return activeTurn.turnId;
}

function acceptedPlan(input: {
  disposition: "created" | "repaired" | "replayed";
  turnId: OpaqueId;
  messageId: OpaqueId;
  requestSha256: Sha256;
  writes: AcceptanceWrites;
  dispatch: ChatTurnDispatch;
}): ChatTurnAcceptancePlan {
  return {
    schema_version: CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA,
    kind: "accepted",
    disposition: input.disposition,
    turn_id: input.turnId,
    message_id: input.messageId,
    request_sha256: input.requestSha256,
    created: input.disposition === "created",
    writes: input.writes,
    dispatch: input.dispatch,
  };
}

function planNewAcceptance(
  facts: AcceptanceFacts,
  session: SessionFacts,
  requestSha256: Sha256,
): ChatTurnAcceptancePlan {
  if (
    facts.transcript.kind !== "absent" ||
    facts.queuedEvent.kind !== "absent"
  ) {
    return rejected("durable_state_conflict");
  }
  if (facts.preparedTurn.kind === "ambiguous") {
    return rejected("durable_state_conflict");
  }
  if (facts.preparedTurn.kind === "single") {
    return rejected("active_turn_conflict", facts.preparedTurn.turnId);
  }
  const blockingTurnId = activeTurnBlocks(
    facts.activeTurn,
    facts.candidate.turnId,
  );
  if (blockingTurnId !== null) {
    return rejected("active_turn_conflict", blockingTurnId);
  }
  return acceptedPlan({
    disposition: "created",
    turnId: facts.candidate.turnId,
    messageId: facts.candidate.messageId,
    requestSha256,
    writes: {
      prepare_turn: true,
      activate_session:
        session.status !== "busy" ||
        session.activeTurnId !== facts.candidate.turnId,
      append_message: true,
      append_queued_event: true,
      settle_turn: true,
    },
    dispatch: {kind: "required"},
  });
}

function planPreparedAcceptance(
  facts: AcceptanceFacts,
  session: SessionFacts,
  turn: MatchingTurnFacts,
  requestSha256: Sha256,
): ChatTurnAcceptancePlan {
  const capsule = turn.acceptance;
  if (
    capsule === null ||
    turn.status !== "queued" ||
    facts.preparedTurn.kind !== "single" ||
    facts.preparedTurn.turnId !== turn.turnId ||
    facts.preparedTurn.clientTurnId !== turn.clientTurnId
  ) {
    return rejected("durable_state_conflict");
  }
  const storedIdentity = storedRequestIdentity(
    facts.request,
    turn,
    capsule.displayMessageSha256,
    capsule.attachmentsSha256,
  );
  if (storedIdentity !== capsule.requestSha256) {
    return rejected("durable_state_conflict");
  }
  if (requestSha256 !== capsule.requestSha256) {
    return rejected("request_conflict");
  }
  const blockingTurnId = activeTurnBlocks(facts.activeTurn, turn.turnId);
  if (blockingTurnId !== null) {
    return rejected("active_turn_conflict", blockingTurnId);
  }
  if (facts.transcript.kind === "ambiguous") {
    return rejected("durable_state_conflict");
  }
  if (
    facts.transcript.kind === "single" &&
    (
      facts.transcript.messageId !== capsule.messageId ||
      facts.transcript.displayMessageSha256 !==
        capsule.displayMessageSha256 ||
      facts.transcript.attachmentsSha256 !== capsule.attachmentsSha256 ||
      facts.transcript.origin !== turn.origin
    )
  ) {
    return rejected("durable_state_conflict");
  }
  if (
    facts.queuedEvent.kind === "ambiguous" ||
    (
      facts.queuedEvent.kind === "single" &&
      facts.queuedEvent.payloadSha256 !== EMPTY_OBJECT_SHA256
    )
  ) {
    return rejected("durable_state_conflict");
  }
  const sessionOwnsPreparedTurn =
    session.status === "busy" && session.activeTurnId === turn.turnId;
  if (
    (
      facts.transcript.kind === "single" ||
      facts.queuedEvent.kind === "single"
    ) &&
    !sessionOwnsPreparedTurn
  ) {
    return rejected("durable_state_conflict");
  }
  if (
    facts.queuedEvent.kind === "single" &&
    facts.transcript.kind !== "single"
  ) {
    return rejected("durable_state_conflict");
  }
  return acceptedPlan({
    disposition: "repaired",
    turnId: turn.turnId,
    messageId: capsule.messageId,
    requestSha256,
    writes: {
      prepare_turn: false,
      activate_session: !sessionOwnsPreparedTurn,
      append_message: facts.transcript.kind === "absent",
      append_queued_event: facts.queuedEvent.kind === "absent",
      settle_turn: true,
    },
    dispatch: {kind: "required"},
  });
}

function planSettledReplay(
  facts: AcceptanceFacts,
  session: SessionFacts,
  turn: MatchingTurnFacts,
  requestSha256: Sha256,
): ChatTurnAcceptancePlan {
  if (facts.transcript.kind === "absent") {
    return rejected("original_request_unavailable");
  }
  if (facts.transcript.kind === "ambiguous") {
    return rejected("durable_state_conflict");
  }
  const storedIdentity = storedRequestIdentity(
    facts.request,
    turn,
    facts.transcript.displayMessageSha256,
    facts.transcript.attachmentsSha256,
  );
  if (
    requestSha256 !== storedIdentity ||
    facts.transcript.origin !== turn.origin
  ) {
    return rejected("request_conflict");
  }
  if (
    facts.queuedEvent.kind !== "single" ||
    facts.queuedEvent.payloadSha256 !== EMPTY_OBJECT_SHA256
  ) {
    return rejected("durable_state_conflict");
  }
  const dispatch = dispatchFor(turn.status);
  if (dispatch.kind === "required") {
    const blockingTurnId = activeTurnBlocks(facts.activeTurn, turn.turnId);
    if (blockingTurnId !== null) {
      return rejected("active_turn_conflict", blockingTurnId);
    }
    if (session.activeTurnId !== turn.turnId || session.status !== "busy") {
      return rejected("durable_state_conflict");
    }
  } else if (
    dispatch.reason === "already_started" &&
    session.activeTurnId !== turn.turnId
  ) {
    return rejected("durable_state_conflict");
  }
  return acceptedPlan({
    disposition: "replayed",
    turnId: turn.turnId,
    messageId: facts.transcript.messageId,
    requestSha256,
    writes: {
      prepare_turn: false,
      activate_session: false,
      append_message: false,
      append_queued_event: false,
      settle_turn: false,
    },
    dispatch,
  });
}

export function planChatTurnAcceptance(
  input: JsonObject,
): ChatTurnAcceptancePlan {
  const facts = decodeFacts(input);
  if (facts.session === null) return rejected("session_not_found");
  if (facts.session.status === "closed") return rejected("session_closed");
  if (!activeObservationIsConsistent(facts.session, facts.activeTurn)) {
    return rejected("durable_state_conflict");
  }

  const requestSha256 = requestIdentity(facts.request);
  const matchingTurn = facts.matchingTurn;
  if (matchingTurn === null) {
    return planNewAcceptance(facts, facts.session, requestSha256);
  }
  if (matchingTurn.clientTurnId !== facts.request.clientTurnId) {
    return rejected("durable_state_conflict");
  }
  if (matchingTurn.acceptance !== null) {
    return planPreparedAcceptance(
      facts,
      facts.session,
      matchingTurn,
      requestSha256,
    );
  }
  return planSettledReplay(
    facts,
    facts.session,
    matchingTurn,
    requestSha256,
  );
}
