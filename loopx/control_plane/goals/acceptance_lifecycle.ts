import type {JsonObject} from "../effect_program.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject} from "../coordination/authority_store_codec.ts";
import {parseExactGoalRef} from "./goal_instance_identity.ts";

export const GOAL_ACCEPTANCE_LIFECYCLE_SCHEMA = "loopx_goal_acceptance_lifecycle_v0";

export interface WireExactGoalRef extends JsonObject {
  goal_id: string;
  goal_instance_id: string;
}

export interface GoalAcceptanceLifecycle extends JsonObject {
  schema_version: typeof GOAL_ACCEPTANCE_LIFECYCLE_SCHEMA;
  state: "active" | "retiring";
  goal_ref: WireExactGoalRef;
}

export type GoalAcceptanceLifecycleTransition =
  | Readonly<{kind: "bind_existing"; goal_ref: WireExactGoalRef}>
  | Readonly<{kind: "retire"; goal_ref: WireExactGoalRef}>
  | Readonly<{
      kind: "activate_successor";
      retired_goal_ref: WireExactGoalRef;
      goal_ref: WireExactGoalRef;
    }>;

function requireLifecycle(condition: unknown, message: string): asserts condition {
  if (!condition) throw new AuthorityStoreProtocolError(message);
}

export function parseWireExactGoalRef(value: unknown, label: string): WireExactGoalRef {
  const parsed = parseExactGoalRef(value);
  requireLifecycle(parsed.kind === "parsed", `${label} must be an exact Goal reference`);
  return {
    goal_id: parsed.value.goalId.value,
    goal_instance_id: parsed.value.goalInstanceId.value,
  };
}

export function sameExactGoalRef(left: WireExactGoalRef, right: WireExactGoalRef): boolean {
  return left.goal_id === right.goal_id && left.goal_instance_id === right.goal_instance_id;
}

export function readGoalAcceptanceLifecycle(head: JsonObject, goalId: string): GoalAcceptanceLifecycle | null {
  if (!Object.hasOwn(head, "goal_acceptance_lifecycle")) return null;
  const raw = canonicalAuthorityObject(head.goal_acceptance_lifecycle, "goal acceptance lifecycle");
  requireLifecycle(
    Object.keys(raw).length === 3
      && Object.hasOwn(raw, "schema_version")
      && Object.hasOwn(raw, "state")
      && Object.hasOwn(raw, "goal_ref"),
    "goal acceptance lifecycle fields are missing or unsupported",
  );
  requireLifecycle(raw.schema_version === GOAL_ACCEPTANCE_LIFECYCLE_SCHEMA,
    "unsupported goal acceptance lifecycle version");
  requireLifecycle(raw.state === "active" || raw.state === "retiring",
    "invalid goal acceptance lifecycle state");
  const goalRef = parseWireExactGoalRef(raw.goal_ref, "goal acceptance lifecycle goal_ref");
  requireLifecycle(goalRef.goal_id === goalId && head.goal_id === goalId,
    "goal acceptance lifecycle Goal identity mismatch");
  return {
    schema_version: GOAL_ACCEPTANCE_LIFECYCLE_SCHEMA,
    state: raw.state,
    goal_ref: goalRef,
  };
}

export function parseGoalAcceptanceLifecycleTransition(value: unknown): GoalAcceptanceLifecycleTransition {
  const raw = canonicalAuthorityObject(value, "goal acceptance lifecycle transition");
  if (raw.kind === "bind_existing" || raw.kind === "retire") {
    requireLifecycle(Object.keys(raw).length === 2 && Object.hasOwn(raw, "goal_ref"),
      "goal acceptance lifecycle transition fields are missing or unsupported");
    return {kind: raw.kind, goal_ref: parseWireExactGoalRef(raw.goal_ref, "transition goal_ref")};
  }
  requireLifecycle(raw.kind === "activate_successor",
    "unsupported goal acceptance lifecycle transition");
  requireLifecycle(Object.keys(raw).length === 3
    && Object.hasOwn(raw, "retired_goal_ref") && Object.hasOwn(raw, "goal_ref"),
  "goal acceptance lifecycle transition fields are missing or unsupported");
  const retiredGoalRef = parseWireExactGoalRef(raw.retired_goal_ref, "transition retired_goal_ref");
  const goalRef = parseWireExactGoalRef(raw.goal_ref, "transition goal_ref");
  requireLifecycle(retiredGoalRef.goal_id === goalRef.goal_id,
    "goal acceptance successor must preserve the Goal alias");
  requireLifecycle(!sameExactGoalRef(retiredGoalRef, goalRef),
    "goal acceptance successor must use a new Goal instance");
  return {kind: "activate_successor", retired_goal_ref: retiredGoalRef, goal_ref: goalRef};
}
