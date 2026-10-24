/** Current operating objective under the retained Goal commitments. R4 owns
 * revisions and peer adoption; this does not amend permission or acceptance. */
import type {JsonObject} from "../effect_program.ts";
import {canonicalAuthorityObject, canonicalAuthoritySha256, AuthorityStoreProtocolError} from "../coordination/authority_store_codec.ts";
import {indexCoordinationProjectionTodos} from "../coordination/coordination_projection.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {readGoalAcceptanceLifecycle, sameExactGoalRef, parseWireExactGoalRef, type WireExactGoalRef} from "./acceptance_lifecycle.ts";
import {normalizeRegisteredTodoAgents} from "../coordination/todo_agents.ts";

export const GOAL_OBJECTIVE_SCHEMA = "goal_objective_revision_v0";
export const GOAL_OBJECTIVE_REVIEW_REQUIRED = "goal_objective_review_required";
interface Revision extends JsonObject {revision: number; objective: string; reason: string; source_ref: string; digest: string}
interface Adoption extends JsonObject {revision: number; digest: string; work_digest: string; review_summary: string}
export interface GoalObjectiveState extends JsonObject {
  schema_version: typeof GOAL_OBJECTIVE_SCHEMA; owner_goal_ref: WireExactGoalRef | null;
  original_objective: string; revisions: Revision[]; registered_agents: string[];
  adoptions: Record<string, Adoption>;
}
function requireValue(condition: unknown, message: string): asserts condition {
  if (!condition) throw new AuthorityStoreProtocolError(message);
}
function text(value: unknown, field: string, limit = 4096): string {
  requireValue(typeof value === "string" && !!value.trim() && value === value.trim() && value.length <= limit && !value.includes("\0"), `${field} must be nonempty bounded text`);
  return value;
}
function keys(row: JsonObject, names: string[]): void {
  requireValue(Object.keys(row).length === names.length && names.every(key => Object.hasOwn(row, key)), "objective revision fields are missing or unsupported");
}
function agents(value: unknown): string[] {
  requireValue(Array.isArray(value) && value.length <= 128, "registered Agents must be a bounded list");
  const result = value.map(v => text(v, "Agent", 128));
  // Retain historical ID forms while accepting the registry's @ aliases.
  requireValue(result.every(v => /^[A-Za-z0-9][A-Za-z0-9_.:@-]*$/.test(v)) && new Set(result).size === result.length, "Agent identities must be unique safe identifiers");
  return result;
}
export function readGoalObjective(head: JsonObject): GoalObjectiveState | null {
  if (head.goal_objective === undefined) return null;
  const raw = canonicalAuthorityObject(head.goal_objective, "Goal objective");
  keys(raw, ["schema_version", "owner_goal_ref", "original_objective", "revisions", "registered_agents", "adoptions"]);
  requireValue(raw.schema_version === GOAL_OBJECTIVE_SCHEMA, "unsupported Goal objective schema");
  const goalRef = raw.owner_goal_ref === null ? null : parseWireExactGoalRef(raw.owner_goal_ref, "objective owner");
  requireValue(goalRef === null || goalRef.goal_id === head.goal_id, "objective belongs to another Goal");
  const original = text(raw.original_objective, "original objective");
  requireValue(Array.isArray(raw.revisions) && raw.revisions.length > 0 && raw.revisions.length <= 64, "objective revision history is outside capacity");
  let previous = canonicalAuthoritySha256({owner_goal_ref: goalRef, original_objective: original});
  const revisions = raw.revisions.map((value, index) => {
    const row = canonicalAuthorityObject(value, "objective revision");
    keys(row, ["revision", "objective", "reason", "source_ref", "digest"]);
    requireValue(row.revision === index + 1, "objective revision history must be ordered and contiguous");
    const objective = text(row.objective, "objective"), reason = text(row.reason, "revision reason", 1024), source = text(row.source_ref, "source reference", 256);
    requireValue(/^[A-Za-z][A-Za-z0-9_-]*:[A-Za-z0-9_.:@-]+$/.test(source), "use an opaque source reference; keep raw evidence in its owner");
    const digest = canonicalAuthoritySha256({previous, revision: index + 1, objective, reason, source_ref: source});
    requireValue(row.digest === digest, "objective revision digest mismatch"); previous = digest;
    return {...row, objective, reason, source_ref: source} as Revision;
  });
  const roster = agents(raw.registered_agents), adopted = canonicalAuthorityObject(raw.adoptions, "objective adoptions");
  agents(Object.keys(adopted));
  for (const value of Object.values(adopted)) {
    const row = canonicalAuthorityObject(value, "objective adoption"); keys(row, ["revision", "digest", "work_digest", "review_summary"]);
    requireValue(Number.isSafeInteger(row.revision) && Number(row.revision) >= 1 && Number(row.revision) <= revisions.length, "invalid adopted objective revision");
    requireValue(revisions[Number(row.revision) - 1].digest === row.digest && BARE_SHA256_PATTERN.test(String(row.work_digest)), "adoption basis mismatch");
    text(row.review_summary, "work review", 1024);
  }
  requireValue(Buffer.byteLength(JSON.stringify(raw), "utf8") <= 256 * 1024, "objective revision capacity exceeded");
  return {...raw, owner_goal_ref: goalRef, original_objective: original, revisions, registered_agents: roster, adoptions: adopted} as GoalObjectiveState;
}
export function currentGoalObjective(head: JsonObject): GoalObjectiveState | null {
  const state = readGoalObjective(head), lifecycle = readGoalAcceptanceLifecycle(head, String(head.goal_id));
  return state && (lifecycle === null ? state.owner_goal_ref === null : lifecycle.state === "active" && state.owner_goal_ref !== null && sameExactGoalRef(state.owner_goal_ref, lifecycle.goal_ref)) ? state : null;
}
export function goalObjectiveWorkDigest(head: JsonObject, agent: string): string {
  const work = [...indexCoordinationProjectionTodos(head, String(head.goal_id)).todos.values()]
    .filter(row => row.claimed_by === agent && row.archive_state === "active" && row.status !== "done")
    .sort((a, b) => String(a.todo_id).localeCompare(String(b.todo_id)));
  return canonicalAuthoritySha256(work);
}
export function objectiveAdoptionRequired(state: GoalObjectiveState, agent: string): boolean {
  return state.adoptions[agent]?.digest !== state.revisions.at(-1)!.digest;
}
export function goalObjectiveWorkGuard(head: JsonObject, agent: string): JsonObject | null {
  const state = currentGoalObjective(head);
  if (!state || !objectiveAdoptionRequired(state, agent)) return null;
  return {allowed: false, reason_code: GOAL_OBJECTIVE_REVIEW_REQUIRED,
    reason: "The shared Goal objective changed. Review current work and adopt its exact revision before starting another execution. Existing Turn settlement and lease release remain available.",
    revision: state.revisions.length, digest: state.revisions.at(-1)!.digest,
    read_action: `loopx --format json goal-acceptance inspect --goal-id ${head.goal_id} --agent-id ${agent}`};
}
export function projectGoalObjective(head: JsonObject, registeredAgents?: unknown): JsonObject | null {
  requireValue(registeredAgents === undefined || Array.isArray(registeredAgents), "registered_agents must be an array");
  const currentPeers = registeredAgents === undefined ? [] : normalizeRegisteredTodoAgents(registeredAgents);
  const state = currentGoalObjective(head);
  if (!state) return null;
  const current = state.revisions.at(-1)!;
  const peers = [...new Set([...state.registered_agents, ...Object.keys(state.adoptions), ...currentPeers])];
  return {...current, original_objective: state.original_objective,
    peer_adoption: peers.map(agent => ({agent_id: agent, revision: state.adoptions[agent]?.revision ?? null, current: !objectiveAdoptionRequired(state, agent)})),
    acceptance_gaps: peers.filter(agent => objectiveAdoptionRequired(state, agent)).map(agent => ({
      kind: GOAL_OBJECTIVE_REVIEW_REQUIRED, source: "goal_acceptance_contract", agent_id: agent,
      frontier_revision: current.digest, acceptance_summary: current.objective,
      replan_trigger_summary: "The shared Goal objective changed; this Agent has not reviewed its current work against the new revision.",
      resolution_hint: "Inspect Goal acceptance, reconcile your current work using normal Todo/Vision operations, then record adopt-objective with the exact revision, digest, own work_digest and review_summary. Adoption is not acceptance or permission. Other peers adopt independently; do not wait for unanimous acknowledgement.",
    }))};
}
export function reviseGoalObjective(head: JsonObject, goalRef: WireExactGoalRef | null, original: unknown, roster: unknown, value: unknown): GoalObjectiveState {
  const document = canonicalAuthorityObject(value, "objective revision"); keys(document, ["objective", "reason", "source_ref"]);
  const prior = currentGoalObjective(head);
  const initial = prior ?? {schema_version: GOAL_OBJECTIVE_SCHEMA, owner_goal_ref: goalRef, original_objective: text(original, "recorded original objective"), revisions: [], registered_agents: [], adoptions: {}};
  requireValue(initial.revisions.length < 64, "objective revision capacity exceeded; retain history and use lifecycle retirement");
  requireValue(text(document.objective, "objective").replace(/\s+/g, " ") !== (initial.revisions.at(-1)?.objective ?? initial.original_objective).replace(/\s+/g, " "), "objective is unchanged; no revision is needed");
  const previous = initial.revisions.at(-1)?.digest ?? canonicalAuthoritySha256({owner_goal_ref: initial.owner_goal_ref, original_objective: initial.original_objective});
  const content = {revision: initial.revisions.length + 1, objective: document.objective, reason: document.reason, source_ref: document.source_ref};
  const next = {...initial, registered_agents: agents(roster), revisions: [...initial.revisions, {...content, digest: canonicalAuthoritySha256({previous, ...content})}]};
  return readGoalObjective({...head, goal_objective: next})!;
}
export function adoptGoalObjective(head: JsonObject, actor: string, value: unknown): GoalObjectiveState {
  const state = currentGoalObjective(head); requireValue(state, "no current objective revision to adopt");
  const document = canonicalAuthorityObject(value, "objective adoption"); keys(document, ["revision", "digest", "work_digest", "review_summary"]);
  const revision = state.revisions.at(-1)!;
  requireValue(document.revision === revision.revision && document.digest === revision.digest, "objective changed; inspect and review the current revision");
  requireValue(document.work_digest === goalObjectiveWorkDigest(head, actor), "own work changed; review it before adopting the objective");
  text(document.review_summary, "work review", 1024);
  // Work and peer writes do not invalidate adoption or replace its original review.
  if (!objectiveAdoptionRequired(state, actor)) return state;
  const next = structuredClone(state);
  Object.defineProperty(next.adoptions, actor, {value: document, enumerable: true, writable: true, configurable: true});
  return readGoalObjective({...head, goal_objective: next})!;
}
