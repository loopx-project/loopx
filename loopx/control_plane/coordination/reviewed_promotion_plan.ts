/** Portable operator input, not a grant or a second migration-state store. */
import type { JsonObject } from "../effect_program.ts";
import { requireJsonObject, requireBoolean } from "../runtime_decode.ts";
import { canonicalAuthorityObject, requireAuthorityStoreId } from "./authority_store_codec.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";

export const REVIEWED_PROMOTION_PLAN_SCHEMA = "loopx_reviewed_coordination_promotion_v0";
export const REVIEWED_PROMOTION_OPERATION_SCHEMA = "loopx_reviewed_coordination_promotion_operation_v0";
export const REVIEWED_PROMOTION_OPERATION_RESULT_SCHEMA =
  "loopx_reviewed_coordination_promotion_operation_result_v0";
export type ReviewedPromotionAction = "apply" | "recover";

export function promotionPlanDigest(value: unknown): string {
  if (typeof value !== "string" || !BARE_SHA256_PATTERN.test(value)) {
    throw new TypeError("reviewed promotion plan digest must be a lowercase SHA-256");
  }
  return value;
}

export function reviewedPromotionPlan(request: unknown, digest: string): JsonObject {
  return {
    schema_version: REVIEWED_PROMOTION_PLAN_SCHEMA,
    promotion_plan_sha256: promotionPlanDigest(digest),
    request: canonicalAuthorityObject(request, "reviewed promotion request"),
  };
}

interface ReviewedPromotionIdentity {
  runtime_root: string;
  goal_id: string;
  execute: boolean;
  request: JsonObject;
  expected_plan_sha256: string;
}
export type ReviewedPromotionOperation = ReviewedPromotionIdentity &
  (
    | { action: "apply"; projection: JsonObject; source_snapshot: JsonObject }
    | { action: "recover"; projection?: never; source_snapshot?: never }
  );

function exactKeys(row: JsonObject, required: string[], optional: string[], label: string): void {
  if (
    required.some((key) => !(key in row)) ||
    Object.keys(row).some((key) => ![...required, ...optional].includes(key))
  ) {
    throw new TypeError(`${label} has unsupported or missing fields`);
  }
}

/** Accept the exact envelope or an unchanged public CLI preview response.
 * Never recursively search arbitrary JSON for a plausible plan. */
export function decodeReviewedPromotionOperation(value: unknown): ReviewedPromotionOperation {
  const input = requireJsonObject(value, "reviewed promotion operation");
  exactKeys(
    input,
    ["schema_version", "action", "runtime_root", "goal_id", "execute", "reviewed_plan"],
    ["projection", "source_snapshot"],
    "reviewed promotion operation",
  );
  if (input.schema_version !== REVIEWED_PROMOTION_OPERATION_SCHEMA)
    throw new TypeError("reviewed promotion operation schema mismatch");
  if (input.action !== "apply" && input.action !== "recover")
    throw new TypeError("reviewed promotion action must be apply or recover");
  const raw = requireJsonObject(input.reviewed_plan, "reviewed promotion input");
  let envelope = raw;
  if (raw.schema_version === "loopx_coordination_shadow_admin_v0") {
    if (raw.action !== "promote" || raw.ok !== true || raw.goal_id !== input.goal_id)
      throw new TypeError("reviewed CLI preview does not match this Goal");
    const promotion = requireJsonObject(raw.promotion, "promotion preview");
    const plan = requireJsonObject(promotion.plan, "promotion plan");
    envelope = requireJsonObject(plan.reviewed_plan, "reviewed promotion plan");
  }
  exactKeys(envelope, ["schema_version", "promotion_plan_sha256", "request"], [], "reviewed promotion plan");
  if (envelope.schema_version !== REVIEWED_PROMOTION_PLAN_SCHEMA)
    throw new TypeError("reviewed promotion plan schema mismatch");
  const request = canonicalAuthorityObject(envelope.request, "reviewed promotion request");
  const goal = requireAuthorityStoreId(input.goal_id, "goal id");
  const root = requireAuthorityStoreId(input.runtime_root, "runtime root");
  if (request.goal_id !== goal || request.runtime_root !== root)
    throw new TypeError("reviewed promotion plan addresses a different Goal or runtime root");
  if ("execute" in request) throw new TypeError("reviewed promotion plan cannot carry execution authority");
  const projection =
    input.projection === undefined
      ? undefined
      : canonicalAuthorityObject(input.projection, "current projection");
  const snapshot =
    input.source_snapshot === undefined
      ? undefined
      : canonicalAuthorityObject(input.source_snapshot, "current source snapshot");
  if (input.action === "recover" && (projection !== undefined || snapshot !== undefined))
    throw new TypeError("promotion recovery cannot use a new legacy source");
  const identity = {
    runtime_root: root,
    goal_id: goal,
    execute: requireBoolean(input.execute, "execute"),
    request,
    expected_plan_sha256: promotionPlanDigest(envelope.promotion_plan_sha256),
  };
  if (input.action === "recover") return { ...identity, action: "recover" };
  if (projection === undefined || snapshot === undefined)
    throw new TypeError("reviewed promotion apply requires a fresh source observation");
  return { ...identity, action: "apply", projection, source_snapshot: snapshot };
}
