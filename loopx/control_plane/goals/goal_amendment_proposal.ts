import { createHash } from "node:crypto";

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
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

/**
 * Governed goal amendment proposal admission contract (RFC
 * shared-goal-alignment-and-governed-amendment-v0, Stage 2 — proposal only).
 *
 * The reducer performs RFC §5 step 2 (admit): strict schema decode, actor
 * identity shape, a closed `amendment_class` enum, bounded evidence pointers,
 * and the relation between the proposal's base basis and the current derived
 * goal basis. Admission validates and retains a proposal; it never approves,
 * commits, or applies one. The answer always carries `canonical_effect:
 * "none"` — a proposal has no canonical effect by construction (RFC §3.4).
 *
 * Basis binding: the proposal binds to the derived basis with BOTH its
 * `base_state_event_basis_sequence` and its `base_source_basis_digest`. An
 * equal sequence with a different digest is a `needs_rebase` admission with
 * a `base_source_basis_digest_mismatch` fact — never a silently fresh
 * admission. The basis facts follow Stage 1's downgraded naming: they are an
 * event-log-derived source projection basis, not a canonical intent
 * revision/digest (no typed shared_goal_intent_v0 source exists yet).
 *
 * Admitted statuses are fact tokens only: `admitted`, or `needs_rebase` when
 * the proposal's base is behind the derived head or its digest mismatches
 * (RFC §7: a stale base is never silently merged). A schema violation fails
 * closed as a request rejection — the equivalent `rejected_schema` outcome
 * never yields a record. There is no `approved` status and no commit path in
 * this contract; governed commit belongs to Stage 3+ behind the
 * `GoalAmendmentAuthority`.
 *
 * The derived goal basis facts arrive from the Python adapter via the Stage 1
 * alignment projection (`state_event_log` head = last append sequence). When
 * no event log exists the basis is `markdown_active_state` before promotion
 * or `canonical_todo_snapshot` afterwards, both with sequence 0 —
 * the only sequence the markdown producer can emit — and a proposal binding
 * that real 0 is admitted as unverifiable instead of being forced to
 * fabricate a positive sequence.
 *
 * The proposal declares its own `base_revision_basis` — the type of the
 * basis it was actually produced against — so sequence producibility is
 * validated against the *claimed* basis, never inferred from the current
 * derived basis: 0 is only producible under a non-event basis, and a
 * positive sequence is only producible under `state_event_log` (a fabricated
 * value under either claim fails closed as a request rejection). When a
 * Goal's basis evolves from markdown to a typed event log, a proposal still
 * carrying its real markdown base (sequence 0 + the markdown source digest)
 * is NOT a fabricated history: it is retained as an explicit
 * `needs_rebase` reconciliation outcome with the
 * `base_revision_basis_superseded` fact (RFC §7: never silently merged,
 * never silently dropped), while a proposal claiming `state_event_log` with
 * sequence 0 — an append that can never have existed — stays a request
 * rejection, and an event-log proposal ahead of a markdown derived head
 * stays the future rejection.
 *
 * Causal binding (RFC §5 "impact scope"): the request also carries typed
 * inventories the Python authority derived at admission time — the goal's
 * open, required replan obligations (with explicit agent-lane bindings) and
 * the goal's actionable open Todos. The proposal's `replan_obligation_id`
 * must resolve to an inventory entry of the same goal whose lane includes
 * the proposer, and every `affected_todo_ids` entry must resolve to an open
 * Todo of that goal. An invalid reference is a request rejection — fail
 * closed, nothing retained — never a new admission outcome, because a
 * proposal without a valid causal target is untrustworthy input for Stage 3
 * settlement rather than a base that merely needs a rebase.
 */

export const GOAL_AMENDMENT_PROPOSAL_REQUEST_SCHEMA_VERSION =
  "goal_amendment_proposal_request_v0";
export const GOAL_AMENDMENT_PROPOSAL_SCHEMA_VERSION =
  "goal_amendment_proposal_v0";
export const GOAL_AMENDMENT_PROPOSAL_ADMISSION_SCHEMA_VERSION =
  "goal_amendment_proposal_admission_v0";

export const GOAL_AMENDMENT_CLASSES = [
  "lane_route",
  "shared_work_graph",
  "shared_acceptance",
  "protected_authority",
] as const;
export type GoalAmendmentClass = (typeof GOAL_AMENDMENT_CLASSES)[number];

export const GOAL_AMENDMENT_PROPOSAL_ADMISSIONS = [
  "admitted",
  "needs_rebase",
] as const;
export type GoalAmendmentAdmission =
  (typeof GOAL_AMENDMENT_PROPOSAL_ADMISSIONS)[number];

export const GOAL_AMENDMENT_PROPOSAL_ADMISSION_FACTS = [
  "base_state_event_basis_sequence_behind_derived_head",
  "base_source_basis_digest_mismatch",
  "base_source_basis_unverifiable",
  "base_revision_basis_superseded",
] as const;
export type GoalAmendmentAdmissionFact =
  (typeof GOAL_AMENDMENT_PROPOSAL_ADMISSION_FACTS)[number];

export const REVISION_BASIS_VALUES = [
  "state_event_log",
  "markdown_active_state",
  "canonical_todo_snapshot",
] as const;
export type AmendmentRevisionBasis = (typeof REVISION_BASIS_VALUES)[number];

/** Bounded evidence and intent-statement budgets (RFC §5.2 admission). */
export const MAX_EVIDENCE_REFS = 8;
export const MAX_EVIDENCE_REF_LENGTH = 200;
export const MAX_INTENT_STATEMENTS = 16;
export const MAX_INTENT_STATEMENT_LENGTH = 500;
export const MAX_AFFECTED_TODO_IDS = 16;

const PROPOSAL_ID_PATTERN = /^gap_[a-z0-9_-]{3,64}$/;
// Mirrors TODO_REPLAN_OBLIGATION_ID_PATTERN / normalize_todo_replan_obligation_id
// (loopx/control_plane/todos/contract.py): replan obligation ids are
// "replan-" + 16 lowercase hex chars, e.g. "replan-fe2d75e84da47ac3".
const REPLAN_OBLIGATION_ID_PATTERN = /^replan-[a-f0-9]{16}$/;
const SOURCE_BASIS_DIGEST_PATTERN = ENVELOPED_SHA256_PATTERN;
const AGENT_ID_PATTERN = /^[a-z][a-z0-9_.:@-]{0,79}$/;
const TODO_ID_PATTERN = /^todo_[a-z0-9_-]{3,64}$/;

export interface GoalAmendmentProposal extends JsonObject {
  schema_version: typeof GOAL_AMENDMENT_PROPOSAL_SCHEMA_VERSION;
  proposal_id: string;
  goal_id: string;
  proposer_agent_id: string;
  amendment_class: GoalAmendmentClass;
  base_revision_basis: AmendmentRevisionBasis;
  base_state_event_basis_sequence: number;
  base_source_basis_digest: string;
  retained: string[];
  changed: string[];
  stopped: string[];
  evidence_refs: string[];
  affected_todo_ids: string[];
  replan_obligation_id: string;
}

export interface DerivedGoalBasisFacts extends JsonObject {
  state_event_basis_sequence: number;
  revision_basis: AmendmentRevisionBasis;
  source_basis_digest: string;
}

/**
 * One open, required replan obligation of the goal being amended, with its
 * agent-lane binding folded into explicit `bound_agent_ids` by the Python
 * authority (`autonomous_replan_scope_decision`): agent-scoped obligations
 * carry their owners, unscoped goal-level ones carry their deterministic
 * peer assignment. An empty array means the obligation imposes no lane
 * constraint. The reducer only compares these typed facts; it never
 * re-derives scope.
 */
export interface OpenReplanObligationFacts extends JsonObject {
  schema_version: string;
  obligation_id: string;
  goal_id: string;
  required: true;
  bound_agent_ids: string[];
}

/**
 * One actionable open Todo of the goal being amended. `claimed_by` and
 * `bound_agent` are diagnostic facts only: admission checks existence,
 * openness, and goal membership — not proposer ownership (shared
 * amendments legitimately affect peer-claimed work; lease disposition
 * belongs to the Stage 3 commit step).
 */
export interface GoalTodoInventoryFacts extends JsonObject {
  todo_id: string;
  status: string;
  task_class: string | null;
  claimed_by: string | null;
  bound_agent: string | null;
}

export interface GoalAmendmentProposalRequest extends JsonObject {
  schema_version: typeof GOAL_AMENDMENT_PROPOSAL_REQUEST_SCHEMA_VERSION;
  proposal: GoalAmendmentProposal;
  derived_basis: DerivedGoalBasisFacts;
  open_replan_obligations: OpenReplanObligationFacts[];
  goal_todo_inventory: GoalTodoInventoryFacts[];
}

export interface GoalAmendmentProposalAdmission extends JsonObject {
  schema_version: typeof GOAL_AMENDMENT_PROPOSAL_ADMISSION_SCHEMA_VERSION;
  proposal_id: string;
  goal_id: string;
  proposer_agent_id: string;
  amendment_class: GoalAmendmentClass;
  base_revision_basis: AmendmentRevisionBasis;
  proposal_digest: string;
  base_state_event_basis_sequence: number;
  base_source_basis_digest: string;
  retained: string[];
  changed: string[];
  stopped: string[];
  evidence_refs: string[];
  affected_todo_ids: string[];
  replan_obligation_id: string;
  admission: GoalAmendmentAdmission;
  admission_facts: GoalAmendmentAdmissionFact[];
  canonical_effect: "none";
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

function sourceBasisDigest(value: unknown, label: string): string {
  const digest = requireNonEmptyString(value, label);
  if (!SOURCE_BASIS_DIGEST_PATTERN.test(digest)) {
    throw new EffectRuntimeRequestError(
      `${label} must be a sha256:<hex> digest`,
    );
  }
  return digest;
}

function boundedStatementArray(
  value: unknown,
  label: string,
  options: { minimum: number },
): string[] {
  const statements = requireStringArray(value, label).map((item, index) => {
    const statement = item.trim();
    if (!statement) {
      throw new EffectRuntimeRequestError(
        `${label}[${index}] must be a non-empty statement`,
      );
    }
    if (statement.length > MAX_INTENT_STATEMENT_LENGTH) {
      throw new EffectRuntimeRequestError(
        `${label}[${index}] exceeds ${MAX_INTENT_STATEMENT_LENGTH} characters`,
      );
    }
    return statement;
  });
  if (statements.length < options.minimum) {
    throw new EffectRuntimeRequestError(
      `${label} requires at least ${options.minimum} statement(s)`,
    );
  }
  if (statements.length > MAX_INTENT_STATEMENTS) {
    throw new EffectRuntimeRequestError(
      `${label} exceeds ${MAX_INTENT_STATEMENTS} statements`,
    );
  }
  return statements;
}

function decodeEvidenceRefs(value: unknown): string[] {
  const refs = requireStringArray(
    value,
    "goal_amendment_proposal.evidence_refs",
  ).map((item, index) => {
    const pointer = item.trim();
    if (!pointer) {
      throw new EffectRuntimeRequestError(
        `goal_amendment_proposal.evidence_refs[${index}] must be a non-empty pointer`,
      );
    }
    if (pointer.length > MAX_EVIDENCE_REF_LENGTH) {
      throw new EffectRuntimeRequestError(
        `goal_amendment_proposal.evidence_refs[${index}] exceeds ${MAX_EVIDENCE_REF_LENGTH} characters`,
      );
    }
    return pointer;
  });
  if (refs.length < 1) {
    throw new EffectRuntimeRequestError(
      "goal_amendment_proposal.evidence_refs requires at least one pointer",
    );
  }
  if (refs.length > MAX_EVIDENCE_REFS) {
    throw new EffectRuntimeRequestError(
      `goal_amendment_proposal.evidence_refs exceeds ${MAX_EVIDENCE_REFS} pointers`,
    );
  }
  requireNoDuplicateValues(
    refs,
    "goal_amendment_proposal.evidence_refs",
    "pointer",
  );
  return refs;
}

function decodeAffectedTodoIds(value: unknown): string[] {
  if (value === null || value === undefined) {
    throw new EffectRuntimeRequestError(
      "goal_amendment_proposal.affected_todo_ids is required",
    );
  }
  const todoIds = requireStringArray(
    value,
    "goal_amendment_proposal.affected_todo_ids",
  ).map((item, index) => {
    const todoId = item.trim().toLowerCase();
    if (!TODO_ID_PATTERN.test(todoId)) {
      throw new EffectRuntimeRequestError(
        `goal_amendment_proposal.affected_todo_ids[${index}] must be a valid Todo id`,
      );
    }
    return todoId;
  });
  if (todoIds.length > MAX_AFFECTED_TODO_IDS) {
    throw new EffectRuntimeRequestError(
      `goal_amendment_proposal.affected_todo_ids exceeds ${MAX_AFFECTED_TODO_IDS} ids`,
    );
  }
  requireNoDuplicateValues(
    todoIds,
    "goal_amendment_proposal.affected_todo_ids",
    "todo_id",
  );
  return todoIds;
}

function requireNoDuplicateValues(
  values: string[],
  label: string,
  kind: string,
): void {
  const seen = new Set<string>();
  for (const value of values) {
    if (seen.has(value)) {
      throw new EffectRuntimeRequestError(
        `${label} contains a duplicate ${kind}: ${value}`,
      );
    }
    seen.add(value);
  }
}

function decodeProposal(value: unknown): GoalAmendmentProposal {
  const proposal = requireJsonObject(value, "goal_amendment_proposal");
  if (proposal.schema_version !== GOAL_AMENDMENT_PROPOSAL_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      "goal amendment proposal schema mismatch",
    );
  }
  const proposalId = requireNonEmptyString(
    proposal.proposal_id,
    "goal_amendment_proposal.proposal_id",
  ).trim().toLowerCase();
  if (!PROPOSAL_ID_PATTERN.test(proposalId)) {
    throw new EffectRuntimeRequestError(
      "goal_amendment_proposal.proposal_id must match gap_<slug>",
    );
  }
  const replanObligationId = requireNonEmptyString(
    proposal.replan_obligation_id,
    "goal_amendment_proposal.replan_obligation_id",
  ).trim();
  if (!REPLAN_OBLIGATION_ID_PATTERN.test(replanObligationId)) {
    throw new EffectRuntimeRequestError(
      "goal_amendment_proposal.replan_obligation_id must match replan-<16 lowercase hex> (normalize_todo_replan_obligation_id)",
    );
  }
  return {
    schema_version: GOAL_AMENDMENT_PROPOSAL_SCHEMA_VERSION,
    proposal_id: proposalId,
    goal_id: requireNonEmptyString(
      proposal.goal_id,
      "goal_amendment_proposal.goal_id",
    ).trim(),
    proposer_agent_id: agentId(
      proposal.proposer_agent_id,
      "goal_amendment_proposal.proposer_agent_id",
    ),
    amendment_class: requireStringLiteral(
      proposal.amendment_class,
      GOAL_AMENDMENT_CLASSES,
      "goal_amendment_proposal.amendment_class",
      "amendment class is unsupported",
    ),
    base_revision_basis: requireStringLiteral(
      proposal.base_revision_basis,
      REVISION_BASIS_VALUES,
      "goal_amendment_proposal.base_revision_basis",
      "amendment base revision_basis is unsupported",
    ),
    base_state_event_basis_sequence: nonNegativeInteger(
      proposal.base_state_event_basis_sequence,
      "goal_amendment_proposal.base_state_event_basis_sequence",
    ),
    base_source_basis_digest: sourceBasisDigest(
      proposal.base_source_basis_digest,
      "goal_amendment_proposal.base_source_basis_digest",
    ),
    retained: boundedStatementArray(proposal.retained, "goal_amendment_proposal.retained", {
      minimum: 1,
    }),
    changed: boundedStatementArray(proposal.changed, "goal_amendment_proposal.changed", {
      minimum: 1,
    }),
    stopped: boundedStatementArray(proposal.stopped, "goal_amendment_proposal.stopped", {
      minimum: 0,
    }),
    evidence_refs: decodeEvidenceRefs(proposal.evidence_refs),
    affected_todo_ids: decodeAffectedTodoIds(proposal.affected_todo_ids),
    replan_obligation_id: replanObligationId,
  };
}

function nonNegativeInteger(value: unknown, label: string): number {
  const decoded = requireInteger(value, label);
  if (decoded < 0) {
    throw new EffectRuntimeRequestError(`${label} must be a non-negative integer`);
  }
  return decoded;
}

function requireProducibleBaseSequence(
  proposal: GoalAmendmentProposal,
): void {
  // The proposal's base sequence must be a value the Stage 1 producer can
  // actually emit for the basis the proposal DECLARES it was produced
  // against (`base_revision_basis`) — never a decoded integer the caller
  // invented, and never a type inferred from the goal's *current* derived
  // basis. A markdown base always projects state_event_basis_sequence=0, so
  // 0 is the only real markdown base and a fabricated positive sequence is
  // a request rejection, not an unverifiable admission. An event-log base
  // stays strictly positive: append sequences start at 1, so 0 cannot exist
  // under a claimed state_event_log base and is rejected instead of
  // silently becoming a "behind the head" needs_rebase retention. Validating
  // against the claimed basis is what keeps a REAL markdown base (0)
  // admissible after the goal's derived basis has since evolved to a typed
  // event log — that transition is admissionOutcome's superseded branch,
  // not a fabricated history (review round 8).
  if (
    proposal.base_revision_basis !== "state_event_log" &&
    proposal.base_state_event_basis_sequence !== 0
  ) {
    throw new EffectRuntimeRequestError(
      `goal_amendment_proposal.base_state_event_basis_sequence must be 0 when the proposal\'s base_revision_basis is ${proposal.base_revision_basis} (no event append sequence; do not fabricate one)`,
    );
  }
  if (
    proposal.base_revision_basis === "state_event_log" &&
    proposal.base_state_event_basis_sequence < 1
  ) {
    throw new EffectRuntimeRequestError(
      "goal_amendment_proposal.base_state_event_basis_sequence must be a positive integer when the proposal's base_revision_basis is state_event_log",
    );
  }
}

function decodeDerivedBasis(value: unknown): DerivedGoalBasisFacts {
  const raw = requireJsonObject(value, "goal_amendment_proposal.derived_basis");
  const revisionBasis = requireStringLiteral(
    raw.revision_basis,
    REVISION_BASIS_VALUES,
    "goal_amendment_proposal.derived_basis.revision_basis",
    "goal amendment derived revision_basis is unsupported",
  );
  const basisSequence = requireInteger(
    raw.state_event_basis_sequence,
    "goal_amendment_proposal.derived_basis.state_event_basis_sequence",
  );
  if (basisSequence < 0) {
    throw new EffectRuntimeRequestError(
      "goal_amendment_proposal.derived_basis.state_event_basis_sequence must be non-negative",
    );
  }
  if (revisionBasis === "state_event_log" && basisSequence < 1) {
    throw new EffectRuntimeRequestError(
      "goal_amendment_proposal.derived_basis.state_event_basis_sequence must be a positive event append sequence when revision_basis is state_event_log",
    );
  }
  if (revisionBasis !== "state_event_log" && basisSequence !== 0) {
    throw new EffectRuntimeRequestError(
      `goal_amendment_proposal.derived_basis.state_event_basis_sequence must be 0 when revision_basis is ${revisionBasis}`,
    );
  }
  return {
    state_event_basis_sequence: basisSequence,
    revision_basis: revisionBasis,
    source_basis_digest: sourceBasisDigest(
      raw.source_basis_digest,
      "goal_amendment_proposal.derived_basis.source_basis_digest",
    ),
  };
}

function decodeOpenReplanObligations(
  value: unknown,
): OpenReplanObligationFacts[] {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError(
      "goal_amendment_proposal_request.open_replan_obligations must be an array",
    );
  }
  const obligations = value.map((item, index) => {
    const raw = requireJsonObject(
      item,
      `goal_amendment_proposal_request.open_replan_obligations[${index}]`,
    );
    const schemaVersion = requireNonEmptyString(
      raw.schema_version,
      `goal_amendment_proposal_request.open_replan_obligations[${index}].schema_version`,
    );
    const obligationId = requireNonEmptyString(
      raw.obligation_id,
      `goal_amendment_proposal_request.open_replan_obligations[${index}].obligation_id`,
    ).trim();
    if (!REPLAN_OBLIGATION_ID_PATTERN.test(obligationId)) {
      throw new EffectRuntimeRequestError(
        `goal_amendment_proposal_request.open_replan_obligations[${index}].obligation_id must match replan-<16 lowercase hex> (normalize_todo_replan_obligation_id)`,
      );
    }
    const goalId = requireNonEmptyString(
      raw.goal_id,
      `goal_amendment_proposal_request.open_replan_obligations[${index}].goal_id`,
    ).trim();
    // The inventory is an admission-time snapshot of *open* obligations: a
    // closed/settled one (required=false) must never reach the reducer. The
    // literal-true guard defends against a Python-side filtering regression.
    const required = requireBoolean(
      raw.required,
      `goal_amendment_proposal_request.open_replan_obligations[${index}].required`,
    );
    if (required !== true) {
      throw new EffectRuntimeRequestError(
        `goal_amendment_proposal_request.open_replan_obligations[${index}].required must be true for listed obligations (closed obligations are not admissible causal chains)`,
      );
    }
    const boundAgentIds = requireStringArray(
      raw.bound_agent_ids,
      `goal_amendment_proposal_request.open_replan_obligations[${index}].bound_agent_ids`,
    ).map((candidate, agentIndex) => {
      const boundAgentId = candidate.trim().toLowerCase();
      if (!AGENT_ID_PATTERN.test(boundAgentId)) {
        throw new EffectRuntimeRequestError(
          `goal_amendment_proposal_request.open_replan_obligations[${index}].bound_agent_ids[${agentIndex}] must be a public-safe agent id`,
        );
      }
      return boundAgentId;
    });
    return {
      schema_version: schemaVersion,
      obligation_id: obligationId,
      goal_id: goalId,
      required: true as const,
      bound_agent_ids: boundAgentIds,
    };
  });
  requireNoDuplicateValues(
    obligations.map((entry) => entry.obligation_id),
    "goal_amendment_proposal_request.open_replan_obligations",
    "obligation_id",
  );
  return obligations;
}

function decodeGoalTodoInventory(value: unknown): GoalTodoInventoryFacts[] {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError(
      "goal_amendment_proposal_request.goal_todo_inventory must be an array",
    );
  }
  const entries = value.map((item, index) => {
    const raw = requireJsonObject(
      item,
      `goal_amendment_proposal_request.goal_todo_inventory[${index}]`,
    );
    const todoId = requireNonEmptyString(
      raw.todo_id,
      `goal_amendment_proposal_request.goal_todo_inventory[${index}].todo_id`,
    ).trim().toLowerCase();
    if (!TODO_ID_PATTERN.test(todoId)) {
      throw new EffectRuntimeRequestError(
        `goal_amendment_proposal_request.goal_todo_inventory[${index}].todo_id must be a valid Todo id`,
      );
    }
    return {
      todo_id: todoId,
      status: optionalNonEmptyString(
        raw.status,
        `goal_amendment_proposal_request.goal_todo_inventory[${index}].status`,
      ) ?? "",
      task_class: optionalNonEmptyString(
        raw.task_class,
        `goal_amendment_proposal_request.goal_todo_inventory[${index}].task_class`,
      ),
      claimed_by: optionalNonEmptyString(
        raw.claimed_by,
        `goal_amendment_proposal_request.goal_todo_inventory[${index}].claimed_by`,
      ),
      bound_agent: optionalNonEmptyString(
        raw.bound_agent,
        `goal_amendment_proposal_request.goal_todo_inventory[${index}].bound_agent`,
      ),
    };
  });
  requireNoDuplicateValues(
    entries.map((entry) => entry.todo_id),
    "goal_amendment_proposal_request.goal_todo_inventory",
    "todo_id",
  );
  return entries;
}

function requireCausalBinding(
  proposal: GoalAmendmentProposal,
  request: GoalAmendmentProposalRequest,
): void {
  // RFC §5 admit step "impact scope": the proposal's causal chain must
  // resolve against the authoritative inventories the Python adapter
  // derived at admission time. An invalid reference is a request
  // rejection (fail closed, nothing retained) — never a new admission
  // outcome and never a silently admitted proposal.
  const obligations = new Map(
    request.open_replan_obligations.map((entry) => [entry.obligation_id, entry]),
  );
  const linked = obligations.get(proposal.replan_obligation_id);
  if (!linked) {
    throw new EffectRuntimeRequestError(
      `goal_amendment_proposal.replan_obligation_id does not match an open replan obligation of goal ${proposal.goal_id}: ${proposal.replan_obligation_id}`,
    );
  }
  if (linked.goal_id !== proposal.goal_id) {
    // Defensive: the Python adapter builds a per-goal inventory, so this
    // branch should be unreachable in production — it exists so a future
    // adapter regression cannot silently admit a cross-goal causal chain.
    throw new EffectRuntimeRequestError(
      `goal_amendment_proposal.replan_obligation_id belongs to another goal: ${linked.goal_id}`,
    );
  }
  if (
    linked.bound_agent_ids.length > 0 &&
    !linked.bound_agent_ids.includes(proposal.proposer_agent_id)
  ) {
    throw new EffectRuntimeRequestError(
      `goal_amendment_proposal.replan_obligation_id is bound to another agent lane: ${proposal.replan_obligation_id}`,
    );
  }
  const openTodoIds = new Set(
    request.goal_todo_inventory.map((entry) => entry.todo_id),
  );
  for (const todoId of proposal.affected_todo_ids) {
    if (!openTodoIds.has(todoId)) {
      throw new EffectRuntimeRequestError(
        `goal_amendment_proposal.affected_todo_ids references a todo that is not open on goal ${proposal.goal_id}: ${todoId}`,
      );
    }
  }
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, child]) => [key, stableValue(child)]),
  );
}

function canonicalDigest(value: unknown): string {
  // Same recipe as the Python adapter's _canonical_digest and
  // governed_capability.ts: sorted keys, compact separators, no ASCII
  // escaping, so the digest is identical across both runtimes.
  const encoded = JSON.stringify(stableValue(value));
  if (encoded === undefined) {
    throw new EffectRuntimeRequestError(
      "goal amendment proposal is not JSON-compatible",
    );
  }
  return `sha256:${createHash("sha256").update(encoded, "utf8").digest("hex")}`;
}

function admissionOutcome(
  proposal: GoalAmendmentProposal,
  derived: DerivedGoalBasisFacts,
): { admission: GoalAmendmentAdmission; facts: GoalAmendmentAdmissionFact[] } {
  if (proposal.base_state_event_basis_sequence > derived.state_event_basis_sequence) {
    // Checked across basis types too: an event-log proposal's positive
    // sequence is ahead of a markdown derived head (0), which is not a real
    // producer transition and stays the future rejection.
    throw new EffectRuntimeRequestError(
      "goal amendment proposal base_state_event_basis_sequence is ahead of the derived state event basis head",
    );
  }
  if (
    proposal.base_revision_basis !== "state_event_log" &&
    derived.revision_basis === "state_event_log"
  ) {
    // A real markdown base that has since been superseded by the goal's
    // first state events: the sequence 0 and markdown digest were produced
    // by the Stage 1 projection of the then event-less goal, so this is a
    // rebase-required base transition — an explicit, read-back
    // reconciliation outcome — never a fabricated history and never a
    // silently fresh admission (RFC §7).
    return {
      admission: "needs_rebase",
      facts: ["base_revision_basis_superseded"],
    };
  }
  if (derived.revision_basis === "canonical_todo_snapshot") {
    const facts: GoalAmendmentAdmissionFact[] = [];
    if (proposal.base_revision_basis !== derived.revision_basis) facts.push("base_revision_basis_superseded");
    if (proposal.base_source_basis_digest !== derived.source_basis_digest) facts.push("base_source_basis_digest_mismatch");
    return facts.length ? {admission: "needs_rebase", facts}
      : {admission: "admitted", facts: ["base_source_basis_unverifiable"]};
  }
  if (derived.revision_basis === "markdown_active_state") {
    // No event log to compare against: report unverifiable, never a
    // fabricated stale verdict (same policy as Stage 1).
    return {
      admission: "admitted",
      facts: ["base_source_basis_unverifiable"],
    };
  }
  const facts: GoalAmendmentAdmissionFact[] = [];
  if (proposal.base_state_event_basis_sequence < derived.state_event_basis_sequence) {
    // Stale bases stay admitted as retained facts with a needs_rebase
    // marker; admission never silently drops or merges them (RFC §7).
    facts.push("base_state_event_basis_sequence_behind_derived_head");
  }
  if (proposal.base_source_basis_digest !== derived.source_basis_digest) {
    // The proposal must actually bind to the source basis it claims: a
    // different basis identity — even at an equal sequence — is a
    // rebase-required mismatch, never a fresh admission.
    facts.push("base_source_basis_digest_mismatch");
  }
  if (facts.length > 0) {
    return { admission: "needs_rebase", facts };
  }
  return { admission: "admitted", facts };
}

export function decodeGoalAmendmentProposalRequest(
  value: unknown,
): GoalAmendmentProposalRequest {
  const request = requireJsonObject(value, "goal amendment proposal request");
  if (
    request.schema_version !== GOAL_AMENDMENT_PROPOSAL_REQUEST_SCHEMA_VERSION
  ) {
    throw new EffectRuntimeRequestError(
      "goal amendment proposal request schema mismatch",
    );
  }
  if (request.work_items !== undefined && request.goal_todo_inventory !== undefined) {
    throw new EffectRuntimeRequestError("work_items cannot mix with preselected amendment inventory");
  }
  const proposal = decodeProposal(request.proposal);
  return {
    schema_version: GOAL_AMENDMENT_PROPOSAL_REQUEST_SCHEMA_VERSION,
    proposal,
    derived_basis: decodeDerivedBasis(request.derived_basis),
    open_replan_obligations: decodeOpenReplanObligations(
      request.open_replan_obligations,
    ),
    goal_todo_inventory: decodeGoalTodoInventory(request.work_items === undefined
      ? request.goal_todo_inventory : sharedGoalWorkFacts(request.work_items, proposal.proposer_agent_id, request.observed_at).goal_todo_inventory),
  };
}

/** Validate one proposal and emit its non-authoritative admission record. */
export function admitGoalAmendmentProposal(
  value: unknown,
): GoalAmendmentProposalAdmission {
  const request = decodeGoalAmendmentProposalRequest(value);
  const { proposal } = request;
  requireProducibleBaseSequence(proposal);
  requireCausalBinding(proposal, request);
  const outcome = admissionOutcome(proposal, request.derived_basis);
  return {
    schema_version: GOAL_AMENDMENT_PROPOSAL_ADMISSION_SCHEMA_VERSION,
    proposal_id: proposal.proposal_id,
    goal_id: proposal.goal_id,
    proposer_agent_id: proposal.proposer_agent_id,
    amendment_class: proposal.amendment_class,
    base_revision_basis: proposal.base_revision_basis,
    proposal_digest: canonicalDigest(proposal),
    base_state_event_basis_sequence: proposal.base_state_event_basis_sequence,
    base_source_basis_digest: proposal.base_source_basis_digest,
    retained: proposal.retained,
    changed: proposal.changed,
    stopped: proposal.stopped,
    evidence_refs: proposal.evidence_refs,
    affected_todo_ids: proposal.affected_todo_ids,
    replan_obligation_id: proposal.replan_obligation_id,
    admission: outcome.admission,
    admission_facts: outcome.facts,
    canonical_effect: "none",
  };
}
