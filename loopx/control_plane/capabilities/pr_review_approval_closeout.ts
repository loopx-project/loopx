/** PR-review-owned read model. Finding resolution and GitHub write authority
 * cannot be inferred from an approval, commit age, or this candidate list. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";

// GitHub owns these input states; closeout statuses are local to this read model.
type ReviewState = "PENDING" | "COMMENTED" | "APPROVED" | "CHANGES_REQUESTED" | "DISMISSED";
type Review = {id: number; login: string; state: ReviewState; head: string; time: number;
  submittedAt: string | null; url: string};
const states = new Set<ReviewState>(["PENDING", "COMMENTED", "APPROVED", "CHANGES_REQUESTED", "DISMISSED"]);
function fail(message: string): never { throw new EffectRuntimeRequestError(message); }
function oid(value: unknown): string {
  const result = requireNonEmptyString(value, "review commit").toLowerCase();
  return /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(result) ? result : fail("review commit must be a full SHA");
}

export function planPrReviewApprovalCloseout(value: unknown): JsonObject {
  const request = requireJsonObject(value, "approval closeout");
  const exact = requireNonEmptyString(request.expected_exact_head, "expected_exact_head");
  const match = /^([1-9][0-9]*)@([a-f0-9]{40}|[a-f0-9]{64})$/.exec(exact);
  if (!match) return fail("approval closeout requires NUMBER@full_HEAD_OID");
  const pr = requireJsonObject(request.pull_request, "pull_request");
  const holds: string[] = [];
  if (pr.number !== Number(match[1])) holds.push("pull_request_identity_changed");
  if (pr.state !== "OPEN") holds.push("pull_request_not_open");
  if (pr.headRefOid !== match[2] || request.readback_head !== match[2]) holds.push("head_changed");
  if (request.reviews_complete !== true) holds.push("review_source_incomplete");
  if (!Array.isArray(request.reviews)) return fail("reviews must be a complete array");
  const seen = new Set<number>();
  const reviews: Review[] = request.reviews.map(value => {
    const row = requireJsonObject(value, "review");
    const id = row.id;
    if (typeof id !== "number" || !Number.isSafeInteger(id) || id <= 0 || seen.has(id)) {
      return fail("review id must be unique and positive");
    }
    seen.add(id);
    const login = requireNonEmptyString(requireJsonObject(row.user, "review user").login, "reviewer login");
    const state = row.state as ReviewState;
    if (!states.has(state)) return fail("unknown GitHub review state");
    // Pending reviews have no submitted_at and cannot clear a submitted opinion.
    const parsed = state === "PENDING" ? new Date(0) : parseIsoTimestamp(requireNonEmptyString(row.submitted_at, "submitted_at"));
    if (parsed === null) return fail("submitted_at must be an ISO timestamp");
    const time = parsed.getTime();
    return {id, login, state, head: oid(row.commit_id), time,
      submittedAt: state === "PENDING" ? null : String(row.submitted_at),
      url: requireNonEmptyString(row.html_url, "review URL")};
  });
  if (!Array.isArray(request.review_conclusions)) return fail("review_conclusions must be a complete array");
  const reviewsById = new Map(reviews.map(review => [review.id, review]));
  const conclusions = new Map<number, JsonObject>();
  for (const value of request.review_conclusions) {
    const row = requireJsonObject(value, "review conclusion");
    const id = row.review_id;
    if (typeof id !== "number" || !seen.has(id) || conclusions.has(id)) {
      return fail("review conclusion must bind one unique review id");
    }
    const review = reviewsById.get(id)!;
    if (row.valid === true && (row.state !== review.state || row.reviewer !== review.login)) {
      return fail("review conclusion identity does not match history");
    }
    conclusions.set(id, row);
  }
  if (conclusions.size !== reviews.length) return fail("review conclusions are incomplete");
  const author = typeof pr.author === "object" && pr.author !== null
    ? requireNonEmptyString(requireJsonObject(pr.author, "PR author").login, "PR author login").toLowerCase() : null;
  const latest = new Map<string, Review>();
  for (const row of reviews.sort((a, b) => a.time - b.time || a.id - b.id)) {
    const conclusion = conclusions.get(row.id)!;
    const authorFallback = row.state === "COMMENTED" && row.login.toLowerCase() === author
      && conclusion.valid === true && ["APPROVE", "REQUEST_CHANGES"].includes(String(conclusion.verdict));
    if (row.state !== "PENDING" && (row.state !== "COMMENTED" || authorFallback)) latest.set(row.login.toLowerCase(), row);
  }
  const approvalReview = [...latest.values()].filter(row => row.head === match[2]
    && conclusions.get(row.id)!.valid === true && conclusions.get(row.id)!.verdict === "APPROVE"
    && (row.state === "APPROVED" || (row.state === "COMMENTED" && row.login.toLowerCase() === author)))
    .sort((a, b) => b.time - a.time || b.id - a.id)[0];
  if (!approvalReview) holds.push("exact_head_approval_missing");
  const blockers = [...latest.values()].filter(row => row.state === "CHANGES_REQUESTED")
    .sort((a, b) => a.id - b.id).map(row => ({review_id: row.id, reviewer: row.login,
      review_head: row.head, review_url: row.url, on_approved_head: row.head === match[2]}));
  if (request.reviews_complete === true && !blockers.length && pr.reviewDecision === "CHANGES_REQUESTED") {
    holds.push("aggregate_review_decision_conflict");
  }
  return {schema_version: "pull_request_review_approval_closeout_v0",
    repository: requireNonEmptyString(request.repository, "repository"), exact_head: exact,
    status: holds.length ? "hold" : blockers.length ? "verification_required" : "clear",
    hold_reasons: holds, blocking_reviews: blockers,
    observed_review_decision: pr.reviewDecision ?? null,
    approval_snapshot: {reviewer: approvalReview?.login ?? null, review_id: approvalReview?.id ?? null,
      state: approvalReview?.state ?? null,
      submitted_at: approvalReview?.submittedAt ?? null},
    dismissal_authorized: false, merge_authorized: false, github_write_performed: false};
}
