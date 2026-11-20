/** Single-owner scope-budget and large-feature diagnostic for dev and PR review.
 *
 * Pure TypeScript control-plane decision owner with zero host or Git IO.
 * Surfaces non-blocking proportionality diagnostics on large additions,
 * high commit/merge churn, or multi-round review loops while exempting
 * net-subtraction cleanups and allowing cohesive large features.
 */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireInteger, requireJsonObject} from "../runtime_decode.ts";

export const CHANGE_SCOPE_BUDGET_SCHEMA_VERSION =
  "change_scope_budget_assessment_v0";

export const CHANGE_SCOPE_BUDGET_STATUSES = [
  "within_budget",
  "net_subtraction",
  "elevated_scope",
  "large_scope",
] as const;
export type ChangeScopeBudgetStatus =
  (typeof CHANGE_SCOPE_BUDGET_STATUSES)[number];

const ALLOWED_INPUT_FIELDS = new Set([
  "changed_files",
  "additions",
  "deletions",
  "commit_count",
  "merge_commit_count",
  "request_changes_rounds",
]);

function nonNegativeInteger(value: unknown, label: string): number {
  const parsed = requireInteger(value, label);
  if (!Number.isSafeInteger(parsed) || parsed < 0) {
    throw new EffectRuntimeRequestError(
      `${label} must be a non-negative integer`,
    );
  }
  return parsed;
}

function optionalNullableNonNegativeInteger(
  value: unknown,
  label: string,
): number | null {
  if (value === undefined || value === null) return null;
  return nonNegativeInteger(value, label);
}

export function assessChangeScopeBudget(input: JsonObject): JsonObject {
  const raw = requireJsonObject(input, "change scope budget input");
  for (const key of Object.keys(raw)) {
    if (!ALLOWED_INPUT_FIELDS.has(key)) {
      throw new EffectRuntimeRequestError(
        `change scope budget input.${key} is unsupported`,
      );
    }
  }
  const changedFiles = nonNegativeInteger(raw.changed_files, "changed_files");
  const additions = nonNegativeInteger(raw.additions, "additions");
  const deletions = nonNegativeInteger(raw.deletions, "deletions");
  const commitCount = optionalNullableNonNegativeInteger(
    raw.commit_count,
    "commit_count",
  );
  const mergeCommitCount = optionalNullableNonNegativeInteger(
    raw.merge_commit_count,
    "merge_commit_count",
  );
  const requestChangesRounds = optionalNullableNonNegativeInteger(
    raw.request_changes_rounds,
    "request_changes_rounds",
  );

  const triggeredSignals: string[] = [];
  let status: ChangeScopeBudgetStatus;

  if (deletions > additions && additions <= 1500) {
    status = "net_subtraction";
  } else {
    const rounds = requestChangesRounds ?? 0;
    const commits = commitCount ?? 0;
    const merges = mergeCommitCount ?? 0;

    if (additions >= 2500 && additions > deletions && changedFiles >= 35) {
      triggeredSignals.push("excessive_addition_surface");
    }
    if (rounds >= 5 && additions >= 1200 && additions >= deletions) {
      triggeredSignals.push("review_ping_pong_spiral");
    }
    if (commits >= 40 && additions >= 1200 && additions >= deletions) {
      triggeredSignals.push("excessive_commit_churn");
    }
    if (merges >= 3 && additions >= 800) {
      triggeredSignals.push("repeated_mainline_merge_commits");
    }

    if (triggeredSignals.length > 0) {
      status = "large_scope";
    } else {
      if (
        (additions >= 1000 && additions > deletions) ||
        (changedFiles >= 20 && additions >= deletions && additions >= 400)
      ) {
        triggeredSignals.push("elevated_addition_surface");
      }
      if (rounds >= 3 && additions >= 400) {
        triggeredSignals.push("elevated_review_rounds");
      }
      if (commits >= 20 && additions >= 400) {
        triggeredSignals.push("elevated_commit_count");
      }
      if (merges >= 2) {
        triggeredSignals.push("repeated_mainline_merge_commits");
      }
      status =
        triggeredSignals.length > 0 ? "elevated_scope" : "within_budget";
    }
  }

  const remediation =
    status === "large_scope"
      ? "verify_goal_proportionality"
      : status === "elevated_scope"
        ? "prefer_smaller_slice"
        : "none";

  return {
    schema_version: CHANGE_SCOPE_BUDGET_SCHEMA_VERSION,
    status,
    changed_files: changedFiles,
    additions,
    deletions,
    net_additions: additions - deletions,
    commit_count: commitCount,
    merge_commit_count: mergeCommitCount,
    request_changes_rounds: requestChangesRounds,
    triggered_signals: triggeredSignals,
    remediation,
  };
}
