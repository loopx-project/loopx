import type { JsonObject } from "../effect_program.ts";
import { sharedGoalWorkFacts } from "./shared_goal_work.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireBoolean,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
  requireStringLiteral,
} from "../runtime_decode.ts";
import { BARE_SHA256_PATTERN, ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";
import type { AuthorityStoreSourceAuthority } from "../coordination/authority_store.ts";

/**
 * Read-only `shared_goal_alignment_v0` projection contract (RFC
 * shared-goal-alignment-and-governed-amendment-v0, Stage 1).
 *
 * The reducer is deterministic: every projected field is derived from the
 * typed facts in the request. Shared `Next Action` prose, agent vision, and
 * chat prose are never inputs. The source basis sequence, digest, and
 * frontier basis are decoded and passed through unchanged — the digest is
 * computed once on the Python side so both runtimes observe one value.
 *
 * Basis semantics: `source_basis` is an event-log-derived projection basis,
 * NOT a canonical intent revision. With no event log, canonical Todo reads
 * use `canonical_todo_snapshot`, sequence 0 and an unbound Agent frontier.
 * The separate `todo_basis` identifies the single Todo/lease snapshot. `state_event_basis_sequence` is the state
 * event log's append sequence (or 0 with the markdown fallback), and
 * `source_basis_digest` hashes goal status, registered agents, event-log
 * basis facts and the canonical Todo revision when promoted — the RFC §3.1 canonical intent envelope (objective,
 * non-goals, acceptance, permissions, terminal conditions) has no typed
 * storage yet, so no field here claims canonical intent identity. This
 * contract has no writer surface: it projects drift and conflict facts only
 * and always answers with `read_only: true`.
 */

export const SHARED_GOAL_ALIGNMENT_REQUEST_SCHEMA_VERSION =
  "shared_goal_alignment_request_v0";
export const SHARED_GOAL_ALIGNMENT_SCHEMA_VERSION = "shared_goal_alignment_v0";

export const SHARED_GOAL_ALIGNMENT_DRIFT_FACTS = [
  "frontier_basis_behind",
] as const;
export const SHARED_GOAL_ALIGNMENT_CONFLICT_FACTS = [
  "frontier_basis_unverifiable",
  "lease_owner_mismatch",
  "open_lane_replan_obligation",
  "peer_claimed_lane_conflict",
] as const;

export type SharedGoalAlignmentDriftFact =
  (typeof SHARED_GOAL_ALIGNMENT_DRIFT_FACTS)[number];
export type SharedGoalAlignmentConflictFact =
  (typeof SHARED_GOAL_ALIGNMENT_CONFLICT_FACTS)[number];

const REVISION_BASIS_VALUES = [
  "state_event_log",
  "markdown_active_state",
  "canonical_todo_snapshot",
] as const;
const BASIS_SOURCE_VALUES = ["state_event_log", "unbound"] as const;

const TODO_ID_PATTERN = /^todo_[a-z0-9_-]{3,64}$/;
const AGENT_ID_PATTERN = /^[a-z][a-z0-9_.:@-]{0,79}$/;
// Mirrors loopx.runtime.validate_goal_id_path_segment (and the loopx.history
// copy): a goal id is any non-empty, safe single filesystem path segment —
// not "." or "..", and never containing "/", "\", or whitespace. The
// repository Goal-ID contract does not require a "goal-" prefix; registered
// goal ids such as "loopx-meta" must decode.
const GOAL_ID_PATTERN = /^(?!\.\.?$)[^\s/\\]+$/;
const SOURCE_BASIS_DIGEST_PATTERN = ENVELOPED_SHA256_PATTERN;

export type RevisionBasis = (typeof REVISION_BASIS_VALUES)[number];
export type BasisSource = (typeof BASIS_SOURCE_VALUES)[number];

export interface SourceBasisFacts extends JsonObject {
  state_event_basis_sequence: number;
  source_basis_digest: string;
  revision_basis: RevisionBasis;
  state_updated_at: string | null;
  todo_basis?: JsonObject;
}

export interface FrontierBasisFacts extends JsonObject {
  based_on_state_event_sequence: number | null;
  basis_source: BasisSource;
  last_agent_event_id: string | null;
}

export interface FrontierCounts extends JsonObject {
  current_agent_claimed_advancement_count: number;
  unclaimed_advancement_count: number;
  other_agent_claimed_advancement_count: number;
}

export interface AlignmentClaimFacts extends JsonObject {
  todo_id: string;
  claimed_by: string;
  lease_epoch: number | null;
  lease_owner: string | null;
}

export interface UnclaimedEligibleFacts extends JsonObject {
  todo_id: string;
  task_class: "advancement_task";
  action_kind?: string;
}

export interface SharedGoalAlignmentRequest extends JsonObject {
  schema_version: typeof SHARED_GOAL_ALIGNMENT_REQUEST_SCHEMA_VERSION;
  goal_id: string;
  agent_id: string;
  source_basis: SourceBasisFacts;
  frontier_basis: FrontierBasisFacts;
  frontier_counts: FrontierCounts;
  claims: AlignmentClaimFacts[];
  unclaimed_eligible: UnclaimedEligibleFacts[];
  peer_claimed_bound_todo_ids: string[];
  open_lane_replan_obligation_required: boolean;
}

export interface SharedGoalAlignment extends JsonObject {
  schema_version: typeof SHARED_GOAL_ALIGNMENT_SCHEMA_VERSION;
  goal_id: string;
  agent_id: string;
  source_basis: SourceBasisFacts;
  frontier_basis: FrontierBasisFacts;
  frontier_counts: FrontierCounts;
  unclaimed_eligible_work: Array<{
    todo_id: string;
    claim_required_before_work: true;
  }>;
  drift_facts: SharedGoalAlignmentDriftFact[];
  conflict_facts: SharedGoalAlignmentConflictFact[];
  read_only: true;
}

function agentId(value: unknown, label: string): string {
  const decoded = requireNonEmptyString(value, label).trim().toLowerCase();
  if (!AGENT_ID_PATTERN.test(decoded)) {
    throw new EffectRuntimeRequestError(
      `${label} must be a public-safe agent id`,
    );
  }
  return decoded;
}

function todoId(value: unknown, label: string): string {
  const decoded = requireNonEmptyString(value, label).trim().toLowerCase();
  if (!TODO_ID_PATTERN.test(decoded)) {
    throw new EffectRuntimeRequestError(`${label} must be a valid Todo id`);
  }
  return decoded;
}

function nonNegativeInteger(value: unknown, label: string): number {
  const decoded = requireInteger(value, label);
  if (decoded < 0) {
    throw new EffectRuntimeRequestError(`${label} must be non-negative`);
  }
  return decoded;
}

function requireNoDuplicates(values: string[], label: string): void {
  const seen = new Set<string>();
  for (const value of values) {
    if (seen.has(value)) {
      throw new EffectRuntimeRequestError(
        `${label} contains a duplicate todo_id: ${value}`,
      );
    }
    seen.add(value);
  }
}

function decodeSourceBasis(value: unknown): SourceBasisFacts {
  const raw = requireJsonObject(value, "shared_goal_alignment.source_basis");
  const revisionBasis = requireStringLiteral(
    raw.revision_basis,
    REVISION_BASIS_VALUES,
    "shared_goal_alignment.source_basis.revision_basis",
    "shared_goal_alignment source_basis revision_basis is unsupported",
  );
  const basisSequence = nonNegativeInteger(
    raw.state_event_basis_sequence,
    "shared_goal_alignment.source_basis.state_event_basis_sequence",
  );
  if (revisionBasis === "state_event_log" && basisSequence < 1) {
    throw new EffectRuntimeRequestError(
      "shared_goal_alignment source_basis state_event_basis_sequence must be a positive event append sequence when revision_basis is state_event_log",
    );
  }
  if (revisionBasis !== "state_event_log" && basisSequence !== 0) {
    throw new EffectRuntimeRequestError(
      `shared_goal_alignment source_basis state_event_basis_sequence must be 0 when revision_basis is ${revisionBasis}`,
    );
  }
  const sourceBasisDigest = requireNonEmptyString(
    raw.source_basis_digest,
    "shared_goal_alignment.source_basis.source_basis_digest",
  );
  if (!SOURCE_BASIS_DIGEST_PATTERN.test(sourceBasisDigest)) {
    throw new EffectRuntimeRequestError(
      "shared_goal_alignment.source_basis.source_basis_digest must be a sha256:<hex> digest computed from typed source facts",
    );
  }
  let todoBasis: JsonObject | undefined;
  if (raw.todo_basis !== undefined) {
    const basis = requireJsonObject(raw.todo_basis, "todo_basis");
    // Decode the selected local source; this read-only view neither selects
    // a provider nor grants authority to an unsupported provider profile.
    const sourceAuthority = requireStringLiteral(
      basis.source_authority,
      ["file_v0", "sqlite_v0"] as const satisfies readonly AuthorityStoreSourceAuthority[],
      "todo_basis.source_authority",
      "invalid canonical Todo basis source authority",
    );
    if (typeof basis.records_sha256 !== "string" || !BARE_SHA256_PATTERN.test(basis.records_sha256)) {
      throw new EffectRuntimeRequestError("invalid canonical Todo basis");
    }
    todoBasis = {source_authority: sourceAuthority, records_sha256: basis.records_sha256,
      provider_revision: requireNonEmptyString(basis.provider_revision, "todo_basis.provider_revision")};
  }
  if (revisionBasis === "canonical_todo_snapshot" && todoBasis === undefined) {
    throw new EffectRuntimeRequestError("canonical_todo_snapshot requires a canonical Todo basis");
  }
  return {
    state_event_basis_sequence: basisSequence,
    source_basis_digest: sourceBasisDigest,
    revision_basis: revisionBasis,
    state_updated_at: optionalNonEmptyString(
      raw.state_updated_at,
      "shared_goal_alignment.source_basis.state_updated_at",
    ),
    ...(todoBasis === undefined ? {} : {todo_basis: todoBasis}),
  };
}

function decodeFrontierBasis(
  value: unknown,
  sourceBasis: SourceBasisFacts,
): FrontierBasisFacts {
  const raw = requireJsonObject(value, "shared_goal_alignment.frontier_basis");
  const basisSource = requireStringLiteral(
    raw.basis_source,
    BASIS_SOURCE_VALUES,
    "shared_goal_alignment.frontier_basis.basis_source",
    "shared_goal_alignment frontier basis_source is unsupported",
  );
  if (basisSource === "state_event_log") {
    if (sourceBasis.revision_basis !== "state_event_log") {
      throw new EffectRuntimeRequestError(
        "shared_goal_alignment frontier basis_source state_event_log cannot be compared against a markdown_active_state source basis",
      );
    }
    const basedOn = requireInteger(
      raw.based_on_state_event_sequence,
      "shared_goal_alignment.frontier_basis.based_on_state_event_sequence",
    );
    if (basedOn < 1) {
      throw new EffectRuntimeRequestError(
        "shared_goal_alignment.frontier_basis.based_on_state_event_sequence must be a positive event append sequence when basis_source is state_event_log",
      );
    }
    return {
      based_on_state_event_sequence: basedOn,
      basis_source: basisSource,
      last_agent_event_id: optionalNonEmptyString(
        raw.last_agent_event_id,
        "shared_goal_alignment.frontier_basis.last_agent_event_id",
      ),
    };
  }
  if (
    raw.based_on_state_event_sequence !== null &&
    raw.based_on_state_event_sequence !== undefined
  ) {
    throw new EffectRuntimeRequestError(
      "shared_goal_alignment.frontier_basis.based_on_state_event_sequence must be null when basis_source is unbound",
    );
  }
  if (
    raw.last_agent_event_id !== null &&
    raw.last_agent_event_id !== undefined
  ) {
    throw new EffectRuntimeRequestError(
      "shared_goal_alignment.frontier_basis.last_agent_event_id must be null when basis_source is unbound",
    );
  }
  return {
    based_on_state_event_sequence: null,
    basis_source: basisSource,
    last_agent_event_id: null,
  };
}

function decodeFrontierCounts(value: unknown): FrontierCounts {
  const raw = requireJsonObject(value, "shared_goal_alignment.frontier_counts");
  return {
    current_agent_claimed_advancement_count: nonNegativeInteger(
      raw.current_agent_claimed_advancement_count,
      "shared_goal_alignment.frontier_counts.current_agent_claimed_advancement_count",
    ),
    unclaimed_advancement_count: nonNegativeInteger(
      raw.unclaimed_advancement_count,
      "shared_goal_alignment.frontier_counts.unclaimed_advancement_count",
    ),
    other_agent_claimed_advancement_count: nonNegativeInteger(
      raw.other_agent_claimed_advancement_count,
      "shared_goal_alignment.frontier_counts.other_agent_claimed_advancement_count",
    ),
  };
}

function decodeClaims(
  value: unknown,
  agentIdValue: string,
): AlignmentClaimFacts[] {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError(
      "shared_goal_alignment.claims must be an array",
    );
  }
  const claims: AlignmentClaimFacts[] = value.map((item, index) => {
    const raw = requireJsonObject(
      item,
      `shared_goal_alignment.claims[${index}]`,
    );
    const claimedBy = agentId(
      raw.claimed_by,
      `shared_goal_alignment.claims[${index}].claimed_by`,
    );
    if (claimedBy !== agentIdValue) {
      throw new EffectRuntimeRequestError(
        `shared_goal_alignment.claims[${index}].claimed_by must match the projected agent's own claim`,
      );
    }
    const leaseEpoch =
      raw.lease_epoch === null || raw.lease_epoch === undefined
        ? null
        : requireInteger(
            raw.lease_epoch,
            `shared_goal_alignment.claims[${index}].lease_epoch`,
          );
    if (leaseEpoch !== null && leaseEpoch < 1) {
      throw new EffectRuntimeRequestError(
        `shared_goal_alignment.claims[${index}].lease_epoch must be a positive lease generation`,
      );
    }
    const leaseOwner =
      raw.lease_owner === null || raw.lease_owner === undefined
        ? null
        : agentId(
            raw.lease_owner,
            `shared_goal_alignment.claims[${index}].lease_owner`,
          );
    // Lease facts travel as a pair: an active lease must carry both a
    // positive generation and a valid owner. A half-present pair is corrupt
    // authority — accepting it would let the reducer project the broken
    // lease as conflict-free, so decode rejects it at the boundary.
    if ((leaseEpoch !== null) !== (leaseOwner !== null)) {
      throw new EffectRuntimeRequestError(
        `shared_goal_alignment.claims[${index}].lease_epoch and lease_owner must be both present for an active lease or both null without one`,
      );
    }
    return {
      todo_id: todoId(raw.todo_id, `shared_goal_alignment.claims[${index}].todo_id`),
      claimed_by: claimedBy,
      lease_epoch: leaseEpoch,
      lease_owner: leaseOwner,
    };
  });
  requireNoDuplicates(
    claims.map((claim) => claim.todo_id),
    "shared_goal_alignment.claims",
  );
  return claims;
}

function decodeUnclaimedEligible(
  value: unknown,
  claimedTodoIds: Set<string>,
): UnclaimedEligibleFacts[] {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError(
      "shared_goal_alignment.unclaimed_eligible must be an array",
    );
  }
  const items: UnclaimedEligibleFacts[] = value.map((item, index) => {
    const raw = requireJsonObject(
      item,
      `shared_goal_alignment.unclaimed_eligible[${index}]`,
    );
    const todoIdValue = todoId(
      raw.todo_id,
      `shared_goal_alignment.unclaimed_eligible[${index}].todo_id`,
    );
    if (claimedTodoIds.has(todoIdValue)) {
      throw new EffectRuntimeRequestError(
        `shared_goal_alignment.unclaimed_eligible[${index}].todo_id is already claimed by the projected agent`,
      );
    }
    const taskClass = requireNonEmptyString(
      raw.task_class,
      `shared_goal_alignment.unclaimed_eligible[${index}].task_class`,
    )
      .trim()
      .toLowerCase();
    if (taskClass !== "advancement_task") {
      throw new EffectRuntimeRequestError(
        `shared_goal_alignment.unclaimed_eligible[${index}].task_class must be advancement_task`,
      );
    }
    const actionKind = optionalNonEmptyString(
      raw.action_kind,
      `shared_goal_alignment.unclaimed_eligible[${index}].action_kind`,
    );
    const decoded: UnclaimedEligibleFacts = {
      todo_id: todoIdValue,
      task_class: "advancement_task",
    };
    if (actionKind !== null) decoded.action_kind = actionKind;
    return decoded;
  });
  requireNoDuplicates(
    items.map((item) => item.todo_id),
    "shared_goal_alignment.unclaimed_eligible",
  );
  return items;
}

function decodePeerClaimedBoundTodoIds(
  value: unknown,
  claimedTodoIds: Set<string>,
): string[] {
  const todoIds = requireStringArray(
    value,
    "shared_goal_alignment.peer_claimed_bound_todo_ids",
  ).map((item, index) =>
    todoId(item, `shared_goal_alignment.peer_claimed_bound_todo_ids[${index}]`),
  );
  for (const todoIdValue of todoIds) {
    if (claimedTodoIds.has(todoIdValue)) {
      throw new EffectRuntimeRequestError(
        "shared_goal_alignment.peer_claimed_bound_todo_ids cannot include a Todo already claimed by the projected agent",
      );
    }
  }
  requireNoDuplicates(
    todoIds,
    "shared_goal_alignment.peer_claimed_bound_todo_ids",
  );
  return todoIds;
}

function driftFacts(
  sourceBasis: SourceBasisFacts,
  frontier: FrontierBasisFacts,
): SharedGoalAlignmentDriftFact[] {
  const facts: SharedGoalAlignmentDriftFact[] = [];
  const comparable =
    sourceBasis.revision_basis === "state_event_log" &&
    frontier.basis_source === "state_event_log" &&
    frontier.based_on_state_event_sequence !== null;
  if (
    comparable &&
    frontier.based_on_state_event_sequence! < sourceBasis.state_event_basis_sequence
  ) {
    facts.push("frontier_basis_behind");
  }
  return facts;
}

function conflictFacts(
  sourceBasis: SourceBasisFacts,
  frontier: FrontierBasisFacts,
  claims: AlignmentClaimFacts[],
  peerClaimedBoundTodoIds: string[],
  openLaneReplanObligationRequired: boolean,
): SharedGoalAlignmentConflictFact[] {
  const facts: SharedGoalAlignmentConflictFact[] = [];
  if (
    frontier.basis_source === "unbound" ||
    sourceBasis.revision_basis === "markdown_active_state"
  ) {
    facts.push("frontier_basis_unverifiable");
  }
  if (
    claims.some(
      (claim) => claim.lease_owner !== null && claim.lease_owner !== claim.claimed_by,
    )
  ) {
    facts.push("lease_owner_mismatch");
  }
  if (openLaneReplanObligationRequired) {
    facts.push("open_lane_replan_obligation");
  }
  if (peerClaimedBoundTodoIds.length > 0) {
    facts.push("peer_claimed_lane_conflict");
  }
  return facts;
}

export function decodeSharedGoalAlignmentRequest(
  value: unknown,
): SharedGoalAlignmentRequest {
  let request = requireJsonObject(value, "shared_goal_alignment request");
  if (
    request.schema_version !== SHARED_GOAL_ALIGNMENT_REQUEST_SCHEMA_VERSION
  ) {
    throw new EffectRuntimeRequestError(
      "shared goal alignment request schema mismatch",
    );
  }
  const goalIdValue = requireNonEmptyString(
    request.goal_id,
    "shared_goal_alignment.goal_id",
  );
  if (!GOAL_ID_PATTERN.test(goalIdValue)) {
    throw new EffectRuntimeRequestError(
      "shared_goal_alignment.goal_id must be a safe single-segment goal id (no \"/\", \"\\\", whitespace, or path traversal)",
    );
  }
  const agentIdValue = agentId(request.agent_id, "shared_goal_alignment.agent_id");
  if (request.work_items !== undefined) {
    for (const key of ["claims", "frontier_counts", "unclaimed_eligible", "peer_claimed_bound_todo_ids"]) {
      if (request[key] !== undefined) throw new EffectRuntimeRequestError("work_items cannot mix with preselected alignment facts");
    }
    request = {...request, ...sharedGoalWorkFacts(request.work_items, agentIdValue, request.observed_at)};
  }
  const sourceBasis = decodeSourceBasis(request.source_basis);
  const frontier = decodeFrontierBasis(request.frontier_basis, sourceBasis);
  const claims = decodeClaims(request.claims, agentIdValue);
  const claimedTodoIds = new Set(claims.map((claim) => claim.todo_id));
  const openLaneReplanObligationRequired = requireBoolean(
    request.open_lane_replan_obligation_required,
    "shared_goal_alignment.open_lane_replan_obligation_required",
  );
  return {
    schema_version: SHARED_GOAL_ALIGNMENT_REQUEST_SCHEMA_VERSION,
    goal_id: goalIdValue,
    agent_id: agentIdValue,
    source_basis: sourceBasis,
    frontier_basis: frontier,
    frontier_counts: decodeFrontierCounts(request.frontier_counts),
    claims,
    unclaimed_eligible: decodeUnclaimedEligible(
      request.unclaimed_eligible,
      claimedTodoIds,
    ),
    peer_claimed_bound_todo_ids: decodePeerClaimedBoundTodoIds(
      request.peer_claimed_bound_todo_ids,
      claimedTodoIds,
    ),
    open_lane_replan_obligation_required: openLaneReplanObligationRequired,
  };
}

/** Project one read-only per-Agent alignment view from typed facts. */
export function projectSharedGoalAlignment(value: unknown): SharedGoalAlignment {
  const request = decodeSharedGoalAlignmentRequest(value);
  return {
    schema_version: SHARED_GOAL_ALIGNMENT_SCHEMA_VERSION,
    goal_id: request.goal_id,
    agent_id: request.agent_id,
    source_basis: request.source_basis,
    frontier_basis: request.frontier_basis,
    frontier_counts: request.frontier_counts,
    unclaimed_eligible_work: request.unclaimed_eligible.map((item) => ({
      todo_id: item.todo_id,
      claim_required_before_work: true,
    })),
    drift_facts: driftFacts(request.source_basis, request.frontier_basis),
    conflict_facts: conflictFacts(
      request.source_basis,
      request.frontier_basis,
      request.claims,
      request.peer_claimed_bound_todo_ids,
      request.open_lane_replan_obligation_required,
    ),
    read_only: true,
  };
}
