import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { assertNever, jsonObject } from "../runtime_decode.ts";
import {
  parseExactGoalRef,
  type ExactGoalRef,
} from "./goal_instance_identity.ts";
import { SOURCE_SESSION_PROFILE_ID } from "./source_session_lifetime.ts";

type WireGoalRef = Readonly<{
  goal_id: string;
  goal_instance_id: string;
}>;

type SessionFact =
  | Readonly<{ kind: "legacy"; sessionId: string; updatedAt: string }>
  | Readonly<{
      kind: "exact";
      sessionId: string;
      goalRef: ExactGoalRef;
      updatedAt: string;
    }>;

type TurnFact = Readonly<{
  goalRef: ExactGoalRef | null;
  admittedGoalRef: ExactGoalRef | null;
}>;

type SelectionFacts = Readonly<{
  operation: "select";
  profileId: string | null;
  currentGoalRef: ExactGoalRef | null;
  candidates: readonly SessionFact[];
}>;

type AdmissionFacts = Readonly<{
  operation: "admit" | "claim" | "replay_claim" | "complete";
  profileId: string | null;
  currentGoalRef: ExactGoalRef | null;
  session: SessionFact;
  turn: TurnFact | null;
}>;

type LifecycleFacts = SelectionFacts | AdmissionFacts;

export type ChatSessionLifecycleDecision =
  | Readonly<{ kind: "legacy" }>
  | Readonly<{ kind: "create"; goal_ref: WireGoalRef }>
  | Readonly<{
      kind: "reuse";
      session_id: string;
      goal_ref: WireGoalRef;
    }>
  | Readonly<{ kind: "allow_current"; goal_ref: WireGoalRef }>
  | Readonly<{ kind: "allow_historical"; goal_ref: WireGoalRef }>
  | Readonly<{
      kind: "reject";
      code:
        | "goal_instance_id_missing"
        | "stale_goal_instance"
        | "turn_goal_instance_mismatch"
        | "turn_not_admitted";
    }>;

function requiredObject(value: unknown, label: string): JsonObject {
  const result = jsonObject(value);
  if (!result) {
    throw new EffectRuntimeRequestError(`${label} must be an object`);
  }
  return result;
}

function requiredString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new EffectRuntimeRequestError(`${label} must be a non-empty string`);
  }
  return value;
}

function optionalProfile(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  return requiredString(value, "profile_id");
}

function exactGoalRef(value: unknown, label: string): ExactGoalRef {
  const parsed = parseExactGoalRef(value);
  if (parsed.kind === "invalid") {
    throw new EffectRuntimeRequestError(`${label} ${parsed.issue}`);
  }
  return parsed.value;
}

function optionalGoalRef(value: unknown, label: string): ExactGoalRef | null {
  return value === null ? null : exactGoalRef(value, label);
}

function wireGoalRef(value: ExactGoalRef): WireGoalRef {
  return {
    goal_id: value.goalId.value,
    goal_instance_id: value.goalInstanceId.value,
  };
}

function goalRefsEqual(left: ExactGoalRef, right: ExactGoalRef): boolean {
  return left.goalId.value === right.goalId.value
    && left.goalInstanceId.value === right.goalInstanceId.value;
}

function sessionFact(value: unknown, label: string): SessionFact {
  const raw = requiredObject(value, label);
  const sessionId = requiredString(raw.session_id, `${label}.session_id`);
  const updatedAt = requiredString(raw.updated_at, `${label}.updated_at`);
  if (raw.goal_instance_id === null || raw.goal_instance_id === undefined) {
    return { kind: "legacy", sessionId, updatedAt };
  }
  return {
    kind: "exact",
    sessionId,
    updatedAt,
    goalRef: exactGoalRef(
      {
        goal_id: raw.goal_id,
        goal_instance_id: raw.goal_instance_id,
      },
      `${label}.goal_ref`,
    ),
  };
}

function turnFact(value: unknown): TurnFact | null {
  if (value === null) return null;
  const raw = requiredObject(value, "turn");
  const goalRef = raw.goal_instance_id === null
    || raw.goal_instance_id === undefined
    ? null
    : exactGoalRef(
      {
        goal_id: raw.goal_id,
        goal_instance_id: raw.goal_instance_id,
      },
      "turn.goal_ref",
    );
  const admittedGoalRef = raw.admitted_goal_instance_id === null
    || raw.admitted_goal_instance_id === undefined
    ? null
    : exactGoalRef(
      {
        goal_id: raw.goal_id,
        goal_instance_id: raw.admitted_goal_instance_id,
      },
      "turn.admitted_goal_ref",
    );
  return { goalRef, admittedGoalRef };
}

function decodeFacts(value: unknown): LifecycleFacts {
  const raw = requiredObject(value, "Chat session lifecycle facts");
  const operation = requiredString(raw.operation, "operation");
  const profileId = optionalProfile(raw.profile_id);
  const currentGoalRef = optionalGoalRef(
    raw.current_goal_ref ?? null,
    "current_goal_ref",
  );
  if (operation === "select") {
    if (!Array.isArray(raw.candidates)) {
      throw new EffectRuntimeRequestError("candidates must be an array");
    }
    return {
      operation,
      profileId,
      currentGoalRef,
      candidates: raw.candidates.map((candidate, index) =>
        sessionFact(candidate, `candidates[${index}]`)
      ),
    };
  }
  if (
    operation === "admit"
    || operation === "claim"
    || operation === "replay_claim"
    || operation === "complete"
  ) {
    return {
      operation,
      profileId,
      currentGoalRef,
      session: sessionFact(raw.session, "session"),
      turn: turnFact(raw.turn ?? null),
    };
  }
  throw new EffectRuntimeRequestError("unsupported Chat session lifecycle operation");
}

function requireExactSession(
  session: SessionFact,
): ExactGoalRef | ChatSessionLifecycleDecision {
  return session.kind === "exact"
    ? session.goalRef
    : { kind: "reject", code: "goal_instance_id_missing" };
}

function decideSelection(
  facts: SelectionFacts,
): ChatSessionLifecycleDecision {
  if (facts.currentGoalRef === null) {
    return { kind: "reject", code: "goal_instance_id_missing" };
  }
  const currentGoalRef = facts.currentGoalRef;
  const current = facts.candidates
    .filter((candidate): candidate is Extract<SessionFact, { kind: "exact" }> =>
      candidate.kind === "exact"
      && goalRefsEqual(candidate.goalRef, currentGoalRef)
    )
    .sort((left, right) =>
      right.updatedAt.localeCompare(left.updatedAt)
      || right.sessionId.localeCompare(left.sessionId)
    )[0];
  return current
    ? {
        kind: "reuse",
        session_id: current.sessionId,
        goal_ref: wireGoalRef(currentGoalRef),
      }
    : { kind: "create", goal_ref: wireGoalRef(currentGoalRef) };
}

function decideAdmission(
  facts: AdmissionFacts,
): ChatSessionLifecycleDecision {
  if (facts.currentGoalRef === null) {
    return { kind: "reject", code: "goal_instance_id_missing" };
  }
  const sessionGoalRef = requireExactSession(facts.session);
  if (!("kind" in sessionGoalRef) || sessionGoalRef.kind !== "goal_ref") {
    return sessionGoalRef;
  }
  if (facts.operation === "admit" || facts.operation === "claim") {
    return goalRefsEqual(sessionGoalRef, facts.currentGoalRef)
      ? { kind: "allow_current", goal_ref: wireGoalRef(sessionGoalRef) }
      : { kind: "reject", code: "stale_goal_instance" };
  }
  const turn = facts.turn;
  if (turn === null || turn.goalRef === null) {
    return { kind: "reject", code: "turn_goal_instance_mismatch" };
  }
  if (!goalRefsEqual(turn.goalRef, sessionGoalRef)) {
    return { kind: "reject", code: "turn_goal_instance_mismatch" };
  }
  if (
    turn.admittedGoalRef === null
    || !goalRefsEqual(turn.admittedGoalRef, sessionGoalRef)
  ) {
    return { kind: "reject", code: "turn_not_admitted" };
  }
  return goalRefsEqual(sessionGoalRef, facts.currentGoalRef)
    ? { kind: "allow_current", goal_ref: wireGoalRef(sessionGoalRef) }
    : { kind: "allow_historical", goal_ref: wireGoalRef(sessionGoalRef) };
}

export function decideChatSessionLifecycle(
  value: unknown,
): ChatSessionLifecycleDecision {
  const facts = decodeFacts(value);
  if (facts.profileId !== SOURCE_SESSION_PROFILE_ID) {
    return { kind: "legacy" };
  }
  switch (facts.operation) {
    case "select":
      return decideSelection(facts);
    case "admit":
    case "claim":
    case "replay_claim":
    case "complete":
      return decideAdmission(facts);
    default:
      return assertNever(facts, "unsupported Chat session lifecycle facts");
  }
}
