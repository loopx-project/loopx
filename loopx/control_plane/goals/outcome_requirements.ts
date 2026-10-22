/** Supporting outcomes retain planning requirements; existing acceptance owns
 * verification. Refinement cannot rewrite root intent, grant work or attest success. */
import type {JsonObject} from "../effect_program.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {indexCoordinationProjectionTodos} from "../coordination/coordination_projection.ts";
import {readGoalAcceptanceLifecycle, sameExactGoalRef, parseWireExactGoalRef, type WireExactGoalRef} from "./acceptance_lifecycle.ts";

import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {goalAcceptanceTodoDigest} from "./acceptance_work_basis.ts";

export const GOAL_OUTCOMES_SCHEMA = "loopx_goal_outcomes_v0";
interface Requirement extends JsonObject {id: string; description: string}
interface ReviewItem extends JsonObject {id: string; conclusion: "met" | "unmet" | "uncertain"; evidence_refs: string[]; note: string}
interface OutcomeReview extends JsonObject {basis_digest: string; items: ReviewItem[]}
interface Outcome extends JsonObject {
  id: string; owner_agent_id: string; objective: string; revision: number;
  requirements: Requirement[]; todo_ids: string[];
  sources: {ref: string; summary: string}[];
  review?: OutcomeReview;
}
interface OutcomeState extends JsonObject {
  schema_version: typeof GOAL_OUTCOMES_SCHEMA; goal_id: string;
  goal_ref: WireExactGoalRef | null; revision: number; outcomes: Outcome[];
}
/** Only fixed rule text, validator-owned labels and measured byte counts belong in public detail.
 * Caller values and arbitrary provider/IO errors must never use this marker. */
export class GoalOutcomeValidationError extends AuthorityStoreProtocolError {}

function requireValue(condition: unknown, reason: string): asserts condition {
  if (!condition) throw new GoalOutcomeValidationError(reason);
}
function text(value: unknown, label: string, max = 512): string {
  requireValue(typeof value === "string" && value === value.trim() && !!value && value.length <= max && !value.includes("\0"), `${label} must be bounded nonempty text`);
  return value;
}
function id(value: unknown, label: string): string {
  const result = text(value, label, 128);
  requireValue(/^[A-Za-z0-9][A-Za-z0-9_.:@-]*$/.test(result), `${label} must be an opaque identifier`);
  return result;
}
function keys(row: JsonObject, required: string[], optional: string[] = []): void {
  requireValue(required.every(key => Object.hasOwn(row, key)) && Object.keys(row).every(key => [...required, ...optional].includes(key)), "outcome fields are missing or unsupported");
}
function list(value: unknown, label: string, max: number): unknown[] {
  requireValue(Array.isArray(value) && value.length <= max, `${label} exceeds its supported capacity`);
  return value;
}
function unique(values: string[], label: string): void {requireValue(new Set(values).size === values.length, `${label} contains duplicate ids`);}
function requirements(value: unknown): Requirement[] {
  const result = list(value, "requirements", 32).map(value => {
    const row = canonicalAuthorityObject(value, "requirement"); keys(row, ["id", "description"]);
    return {id: id(row.id, "requirement id"), description: text(row.description, "requirement description")};
  });
  unique(result.map(row => row.id), "requirements"); return result;
}
function revision(value: unknown): number {
  requireValue(Number.isSafeInteger(value) && Number(value) > 0, "outcome revision is invalid"); return Number(value);
}
function source(value: unknown): {ref: string; summary: string} {
  const row = canonicalAuthorityObject(value, "outcome source"); keys(row, ["ref", "summary"]);
  return {ref: id(row.ref, "source ref"), summary: text(row.summary, "source summary")};
}
function reviewItems(value: unknown): ReviewItem[] {
  const items = list(value, "review items", 32).map(value => {
    const row = canonicalAuthorityObject(value, "Agent review"); keys(row, ["id", "conclusion", "evidence_refs", "note"]);
    requireValue(["met", "unmet", "uncertain"].includes(String(row.conclusion)), "review conclusion must be met, unmet or uncertain");
    const evidence = list(row.evidence_refs, "review evidence", 8).map(ref => text(ref, "evidence reference", 1024));
    requireValue(row.conclusion !== "met" || evidence.length > 0, "a met self-assessment requires evidence references");
    return {id: id(row.id, "review requirement"), conclusion: row.conclusion as ReviewItem["conclusion"], evidence_refs: evidence, note: text(row.note, "review reasoning")};
  });
  unique(items.map(row => row.id), "review items"); return items;
}
function reviewBasis(outcome: Outcome, work: ReadonlyMap<string, JsonObject>): string {
  return canonicalAuthoritySha256({objective: outcome.objective, requirements: outcome.requirements,
    work: outcome.todo_ids.map(key => ({todo_id: key, digest: work.has(key) ? goalAcceptanceTodoDigest(work.get(key)!) : null}))});
}
function readState(head: JsonObject, goalId: string): OutcomeState | null {
  if (head.goal_outcomes === undefined) return null;
  const row = canonicalAuthorityObject(head.goal_outcomes, "Goal outcomes");
  keys(row, ["schema_version", "goal_id", "goal_ref", "revision", "outcomes"]);
  requireValue(row.schema_version === GOAL_OUTCOMES_SCHEMA && row.goal_id === goalId, "outcome source Goal mismatch");
  const goalRef = row.goal_ref === null ? null : parseWireExactGoalRef(row.goal_ref, "outcome Goal ref");
  requireValue(goalRef === null || goalRef.goal_id === goalId, "outcome lifetime Goal mismatch");
  const outcomes = list(row.outcomes, "outcomes", 24).map(value => {
    const o = canonicalAuthorityObject(value, "outcome");
    keys(o, ["id", "owner_agent_id", "objective", "revision", "requirements", "todo_ids", "sources"], ["review"]);
    let review: OutcomeReview | undefined;
    if (o.review !== undefined) {
      const row = canonicalAuthorityObject(o.review, "outcome review"); keys(row, ["basis_digest", "items"]);
      requireValue(typeof row.basis_digest === "string" && BARE_SHA256_PATTERN.test(row.basis_digest), "review basis digest is invalid");
      review = {basis_digest: row.basis_digest, items: reviewItems(row.items)};
    }
    const todoIds = list(o.todo_ids, "outcome work", 64).map(v => id(v, "Todo id")); unique(todoIds, "outcome work");
    const sources = list(o.sources, "outcome sources", 64).map(source); unique(sources.map(s => s.ref), "sources");
    const reqs = requirements(o.requirements); requireValue(reqs.length && sources.length, "outcome requirements and source are required");
    return {id: id(o.id, "outcome id"), owner_agent_id: id(o.owner_agent_id, "outcome Agent"), objective: text(o.objective, "outcome objective", 1024),
      revision: revision(o.revision), requirements: reqs, todo_ids: todoIds, sources, ...(review ? {review} : {})};
  });
  unique(outcomes.map(o => o.id), "outcomes");
  return {schema_version: GOAL_OUTCOMES_SCHEMA, goal_id: goalId, goal_ref: goalRef, revision: revision(row.revision), outcomes};
}

/** The existing lifecycle owner calls this only while binding a legacy source.
 * Keep its declarations intact; a successor must never adopt a predecessor's
 * outcomes merely because it reuses the same Goal alias. No receipt is changed. */
export function bindGoalOutcomesStateOwner(head: JsonObject, goalId: string, goalRef: WireExactGoalRef): OutcomeState | null {
  const previous = readState(head, goalId);
  requireValue(readGoalAcceptanceLifecycle(head, goalId) === null, "outcome source already has a Goal lifetime");
  const exact = parseWireExactGoalRef(goalRef, "outcome Goal ref");
  requireValue(exact.goal_id === goalId, "outcome lifetime Goal mismatch");
  requireValue(!previous || previous.goal_ref === null || sameExactGoalRef(previous.goal_ref, exact),
    "outcome Goal lifetime changed; reconcile retained declarations");
  return previous === null ? null : {...previous, goal_ref: exact};
}

/** Same stored snapshot, two independently bounded envelopes. */
function outcomeBytes(state: OutcomeState): {declaration: number; review: number} {
  const declaration = {...state, outcomes: state.outcomes.map(({review: _review, ...outcome}) => outcome)};
  const declarationBytes = Buffer.byteLength(JSON.stringify(declaration), "utf8");
  return {declaration: declarationBytes, review: Buffer.byteLength(JSON.stringify(state), "utf8") - declarationBytes};
}

export function refineGoalOutcome(head: JsonObject, goalId: string, goalRef: WireExactGoalRef | null,
  actor: string | null, value: unknown): OutcomeState {
  let previous = readState(head, goalId);
  const lifecycle = readGoalAcceptanceLifecycle(head, goalId);
  requireValue(!lifecycle || lifecycle.state === "active" && goalRef && sameExactGoalRef(lifecycle.goal_ref, goalRef), "outcome Goal lifetime is unavailable or changed");
  if (previous && canonicalAuthoritySha256(previous.goal_ref) !== canonicalAuthoritySha256(goalRef)) {
    if (previous.goal_ref === null && lifecycle === null && goalRef !== null) {
      previous = bindGoalOutcomesStateOwner(head, goalId, goalRef);
    } else {
      // Only a completed canonical lifetime transition permits fresh state.
      // Old exact operation receipts remain with the authority journal, and
      // new declarations cannot silently inherit the retired requirements.
      requireValue(lifecycle !== null && previous.goal_ref !== null && goalRef !== null,
        "outcome Goal lifetime changed; reconcile retained declarations");
      previous = null;
    }
  }
  const patch = canonicalAuthorityObject(value, "outcome refinement");
  keys(patch, ["outcome_id"], ["source", "owner_agent_id", "objective", "requirements", "todo_ids", "review"]);
  const outcomeId = id(patch.outcome_id, "outcome id");
  const old = previous?.outcomes.find(o => o.id === outcomeId);
  const owner = id(patch.owner_agent_id === undefined ? old?.owner_agent_id : patch.owner_agent_id, "outcome Agent");
  requireValue(actor === null || actor === owner, "Agents can refine only their own outcomes");
  const objective = text(patch.objective === undefined ? old?.objective : patch.objective, "outcome objective", 1024);
  requireValue(!old || old.owner_agent_id === owner && old.objective === objective, "refinement cannot replace the original outcome objective or owner");
  const todos = indexCoordinationProjectionTodos(head, goalId).todos;
  const links = list(patch.todo_ids === undefined ? [] : patch.todo_ids, "outcome work", 64).map(v => id(v, "Todo id")); unique(links, "outcome work");
  for (const key of links) {
    const todo = todos.get(key);
    requireValue(todo && todo.role === "agent" && (todo.task_class == null || todo.task_class === "advancement_task"), "outcomes link existing Agent advancement Todos only");
    requireValue(actor === null || !todo.claimed_by || todo.claimed_by === actor, "an Agent cannot attach another Agent's claimed work");
  }
  const reqs = new Map((old?.requirements ?? []).map(row => [row.id, row]));
  const additions = requirements(patch.requirements === undefined ? [] : patch.requirements);
  const declaresRequirements = !old || additions.some(row => !reqs.has(row.id));
  for (const row of additions) {
    requireValue(!reqs.has(row.id) || reqs.get(row.id)!.description === row.description, "an existing requirement cannot be overwritten; reconcile conflicting intent explicitly");
    reqs.set(row.id, row);
  }
  const sources = new Map((old?.sources ?? []).map(row => [row.ref, row]));
  // Declaration origins survive progress reviews. Operation receipts and the
  // review's evidence references retain the new observation, not another origin.
  const origin = Object.hasOwn(patch, "source") ? source(patch.source) : null;
  requireValue(origin !== null || !declaresRequirements, "new outcomes or requirements require source provenance; review evidence belongs in review.evidence_refs");
  if (origin !== null) {
    requireValue(!sources.has(origin.ref) || sources.get(origin.ref)!.summary === origin.summary, "source reference already identifies another refinement");
    requireValue(sources.has(origin.ref) || sources.size < 64,
      "outcome source capacity reached; review/work updates may omit source and retain evidence in review.evidence_refs; new declarations still require provenance");
    sources.set(origin.ref, origin);
  }
  const outcome: Outcome = {id: outcomeId, owner_agent_id: owner, objective, revision: (old?.revision ?? 0) + 1,
    requirements: [...reqs.values()], todo_ids: [...new Set([...(old?.todo_ids ?? []), ...links])], sources: [...sources.values()],
    ...(old?.review ? {review: old.review} : {})};
  if (patch.review !== undefined) {
    const items = reviewItems(patch.review);
    requireValue(items.length === outcome.requirements.length && items.every(row => reqs.has(row.id)), "review must address every retained requirement exactly once");
    outcome.review = {basis_digest: reviewBasis(outcome, todos), items};
  }
  const state: OutcomeState = {schema_version: GOAL_OUTCOMES_SCHEMA, goal_id: goalId, goal_ref: goalRef, revision: (previous?.revision ?? 0) + 1,
    outcomes: [...(previous?.outcomes.filter(o => o.id !== outcomeId) ?? []), outcome]};
  // Preserve declaration/review limits; diagnostics count the failing
  // envelope, not the combined snapshot or an individual outcome/source list.
  const candidate = outcomeBytes(state);
  if (candidate.declaration > 131072 || candidate.review > 131072) {
    const retained = previous === null ? {declaration: 0, review: 0} : outcomeBytes(previous);
    const budget = candidate.declaration > 131072 ? "declaration" : "review";
    const recovery = budget === "review"
      ? "Compact only proposed review notes/evidence references without losing conclusions or provenance; keep full evidence in its owning material. "
      : "Preserve retained requirements/origins; keep supporting detail in its owning material. Shortening reviews cannot reduce this declaration budget. ";
    throw new GoalOutcomeValidationError(
      `outcome ${budget} capacity exceeded: candidate ${candidate[budget]} UTF-8 bytes, retained ${retained[budget]}, limit 131072; ` +
      "this budget covers all outcomes in the Goal, not the source count; declaration and review budgets are independent. " + recovery +
      "Do not remove requirements/origins or create a replacement outcome to bypass the limit. If no lossless update fits, report the capacity blocker.");
  }
  return readState({...head, goal_outcomes: state}, goalId)!;
}

/** Resolve check authoring from one current requirement source. Review/source
 * bookkeeping does not invalidate an otherwise unchanged prepared check. */
export function goalOutcomeCheckBasis(head: JsonObject, goalId: string, outcomeId: unknown): JsonObject {
  const state = readState(head, goalId), lifecycle = readGoalAcceptanceLifecycle(head, goalId);
  requireValue(state && (lifecycle === null ? state.goal_ref === null
    : lifecycle.state === "active" && state.goal_ref !== null && sameExactGoalRef(lifecycle.goal_ref, state.goal_ref)),
    "check source outcome lifetime is unavailable or changed");
  const outcome = state.outcomes.find(o => o.id === id(outcomeId, "outcome id"));
  requireValue(outcome, "check source outcome does not exist");
  return {outcome_id: outcome.id, objective: outcome.objective, requirements: outcome.requirements, todo_ids: outcome.todo_ids};
}

/** Only the existing acceptance projection supplies check results. A matched
 * description alone is not evidence; work coverage and freshness also matter. */
export function projectGoalOutcomes(head: JsonObject, goalId: string, acceptance: JsonObject): JsonObject | null {
  const state = readState(head, goalId); if (!state) return null;
  const lifecycle = readGoalAcceptanceLifecycle(head, goalId);
  const current = lifecycle === null ? state.goal_ref === null
    : lifecycle.state === "active" && state.goal_ref !== null && sameExactGoalRef(lifecycle.goal_ref, state.goal_ref);
  const work = indexCoordinationProjectionTodos(head, goalId).todos;
  return observeOutcomeRequirements({schema_version: GOAL_OUTCOMES_SCHEMA, revision: state.revision, current_lifetime: current,
    outcomes: state.outcomes.map(o => ({...o, review_basis_current: !!o.review && o.review.basis_digest === reviewBasis(o, work),
      // The source checkpoint includes current declared work and its completed
      // execution identity. Display/source bookkeeping does not create work.
      basis_digest: canonicalAuthoritySha256({goal_id: goalId, goal_ref: state.goal_ref,
        outcome_id: o.id, owner_agent_id: o.owner_agent_id, work_basis: reviewBasis(o, work),
        completions: o.todo_ids.map(key => ({todo_id: key, ...Object.fromEntries(
          ["completion_turn_key", "completion_receipt_id", "completion_validation_sha256", "completion_result"]
            .filter(field => work.get(key)?.[field] !== undefined).map(field => [field, work.get(key)![field]]))}))}),
      work: o.todo_ids.map(key => ({todo_id: key, status: work.get(key)?.status ?? "missing"}))})),
    execution_authority_granted: false, goal_completion_assessed: false}, acceptance);
}

/** Re-use after the host observes oracle freshness; raw stored evidence cannot
 * become a current passing result without those observations. No state writes. */
export function observeOutcomeRequirements(declaration: JsonObject, acceptance: JsonObject,
  oracleObservations: JsonObject[] = []): JsonObject {
  const current = declaration.current_lifetime === true;
  const allCriteria = (acceptance.criteria ?? []) as JsonObject[], tasks = (acceptance.tasks ?? []) as JsonObject[];
  const outcomes = (declaration.outcomes as (Outcome & {basis_digest?: string; review_basis_current: boolean; work: {todo_id: string; status: string}[]})[]).map(o => ({...o,
    requirements: o.requirements.map(r => {
      const criteria = acceptance.outcome_id === undefined || acceptance.outcome_id === o.id ? allCriteria : [];
      const criterion = criteria.find(c => c.id === r.id && c.description === r.description);
      const covered = o.todo_ids.length > 0 && o.todo_ids.every(key => tasks.some(t => t.todo_id === key && t.state === "ready" && (t.criterion_ids as string[]).includes(r.id)));
      const oracle = criterion?.oracle as JsonObject | undefined;
      const receipt = acceptance.verification as JsonObject | null | undefined;
      const ownResult = (receipt?.results as JsonObject[] | undefined)?.find(result => result.criterion_id === r.id);
      const passed = acceptance.status === "accepted" || acceptance.status === "failed" && receipt?.todo_id === null
        && receipt.contract_revision === acceptance.revision && receipt.contract_digest === acceptance.digest && ownResult?.passed === true;
      const checked = current && criterion && covered && passed && (!oracle || oracle.state === "current");
      const review = o.review?.items.find(item => item.id === r.id) ?? r.agent_review as (ReviewItem & {basis_current?: boolean}) | undefined;
      const reviewCurrent = current && (o.review_basis_current ?? review?.basis_current === true);
      const configured = criteria.some(c => c.id === r.id);
      return {...r, ...(review ? {agent_review: {...review, basis_current: reviewCurrent, independently_verified: false}} : {}),
        planning_satisfied: !!checked || (!configured && reviewCurrent && review?.conclusion === "met"),
        status: checked ? "accepted" : current && criterion && covered && acceptance.status !== "accepted" ? acceptance.status : "unverified"};
    }),
  }));
  const uncovered = current ? outcomes.flatMap(outcome => {
    const unmet = outcome.requirements.filter(r => !r.planning_satisfied).map(r => r.id);
    if (!unmet.length || outcome.work.some(w => !["done", "missing"].includes(String(w.status)))) return [];
    const criteria = acceptance.outcome_id === undefined || acceptance.outcome_id === outcome.id ? allCriteria
      .filter(criterion => outcome.requirements.some(requirement => requirement.id === criterion.id)) : [];
    const receipt = acceptance.verification as JsonObject | null | undefined;
    const basis = {outcome_id: outcome.id, basis_digest: outcome.basis_digest ?? null, work: outcome.work,
      requirements: outcome.requirements.map(({id, status, planning_satisfied, agent_review}) => ({id, status, planning_satisfied,
        ...(agent_review ? {agent_review: {conclusion: agent_review.conclusion,
          evidence_refs: [...new Set(agent_review.evidence_refs)].sort(), basis_current: agent_review.basis_current}} : {})})),
      checks: criteria.length ? {revision: acceptance.revision ?? null, digest: acceptance.digest ?? null, status: acceptance.status ?? null,
        criteria, verification: receipt ? {contract_revision: receipt.contract_revision ?? null, contract_digest: receipt.contract_digest ?? null,
          todo_id: receipt.todo_id ?? null, results: (receipt.results as JsonObject[] ?? []).filter(result => criteria.some(c => c.id === result.criterion_id))
            .map(result => ({...result, ...(result.oracle_evidence ? {oracle_evidence: Object.fromEntries(
              Object.entries(result.oracle_evidence as JsonObject).filter(([key]) => key !== "checked_at"))} : {})}))} : null,
        observations: oracleObservations.filter(row => criteria.some(c => c.id === row.criterion_id))} : null};
    return [{outcome, unmet, basis}];
  }) : [];
  // One scoped source checkpoint covers the complete uncovered set, including
  // outcomes beyond the three detailed replan triggers. This remains an ACK
  // over canonical evidence, not another ledger or an acceptance verdict.
  const revisions = new Map([...new Set(uncovered.map(row => row.outcome.owner_agent_id))].map(agent => [agent,
    canonicalAuthoritySha256(uncovered.filter(row => row.outcome.owner_agent_id === agent)
      .sort((left, right) => left.outcome.id < right.outcome.id ? -1 : left.outcome.id > right.outcome.id ? 1 : 0)
      .map(row => row.basis))]));
  const gaps = uncovered.map(({outcome, unmet}) => ({kind: "goal_outcome_uncovered", source: "goal_acceptance_contract", agent_id: outcome.owner_agent_id,
      outcome_id: outcome.id, requirement_ids: unmet, frontier_revision: revisions.get(outcome.owner_agent_id)!,
      replan_trigger_summary: `Outcome ${outcome.id} still has unmet requirements without active linked work.`,
      acceptance_summary: outcome.objective, vision_todo_ids: [],
      resolution_hint: "Read goal-acceptance inspect; preserve its original objective and cumulative requirements. " +
        "Link concrete existing or successor work with goal-acceptance refine, or record the actual acceptance/owner blocker. " +
        "Default review records evidence and unmet requirements; configured checks remain authoritative. Todo completion alone cannot settle requirements."}));
  return {...declaration, outcomes: outcomes.map(({review: _review, review_basis_current: _basis, ...outcome}) => outcome), acceptance_gaps: gaps};
}
