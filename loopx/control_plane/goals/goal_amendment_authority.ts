/** Governed work-graph amendments. Only the trusted source adapter may supply
 * lifecycle/actor facts; provider persistence alone grants no amendment power. */
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreHead} from "../coordination/authority_store.ts";
import {canonicalAuthorityObject, canonicalAuthoritySha256, AuthorityStoreProtocolError} from "../coordination/authority_store_codec.ts";
import {CoordinationCommandReceipt, commandReceiptResult} from "../coordination/command_receipt.ts";
import {indexCoordinationProjection, prepareCoordinationProjectionCommit, validateCoordinationTodoReadModel} from "../coordination/coordination_projection.ts";
import {executeCoordinationTodoClaim, type CoordinationTodoClaimLeaseRequest} from "../coordination/todo_claim.ts";
import {planCoordinationTodoCreate} from "../coordination/todo_create.ts";
import {TODO_DOMAIN_ITEM_SCHEMA} from "../coordination/coordination_state_contract.ts";
import {parseExactGoalRef} from "./goal_instance_identity.ts";
import {sourceGoalMutationReadiness} from "./source_session_lifetime.ts";
import {openLocalAuthorityStore, localAuthorityOpenFailure} from "../coordination/local_authority_provider.ts";
import {withCanonicalWriter} from "../coordination/local_authority_write.ts";
import {isAbsolute} from "node:path";
import {parseQuotaAccountingOwner, withBorrowedQuotaAccountingOwner} from "../quota/source_admission.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {registryAuthoritySourceCheck, uncheckedAuthoritySource, type AuthoritySourceCheck} from "../coordination/authority_source.ts";
import {readGoalAcceptanceLifecycle, sameExactGoalRef} from "./acceptance_lifecycle.ts";

export const SHARED_GOAL_INTENT_SCHEMA = "shared_goal_intent_v0";
export const GOAL_AMENDMENT_OPERATION_SCHEMA = "goal_amendment_operation_v0";
export const GOAL_AMENDMENT_RESULT_SCHEMA = "goal_amendment_result_v0";
export const GOAL_AMENDMENT_PROPOSAL_SCHEMA = "goal_work_graph_amendment_v0";
const LIMITS = {templates: 16, proposals: 64, created: 256, peers: 128, bytes: 256 * 1024};
const OPERATION_FIELDS = {inspect: [], configure: ["root_intent", "policy", "expected_provider_revision"],
    propose: ["proposal_id", "template_id", "base_goal_revision", "base_intent_digest", "base_work_digest", "evidence_refs", "replan_obligation_id"],
    commit: ["proposal_id", "proposal_digest"], adopt: ["base_goal_revision", "base_intent_digest", "base_work_digest"], receipt: ["receipt_operation_id"], claim: ["todo_id", "expected_provider_revision", "base_goal_revision", "base_intent_digest", "base_work_digest", "lease_request"]} as const;
type Operation = keyof typeof OPERATION_FIELDS;
type GoalRef = {goal_id: string; goal_instance_id: string};
interface Template extends JsonObject {id: string; todo: JsonObject; digest: string}
interface RootIntent extends JsonObject {
  objective: string; non_goals: string[]; acceptance: string[];
  permission_scope: {write_scopes: string[]; capabilities: string[]};
  stop_conditions: string[]; terminal_conditions: string[];
}
interface Policy extends JsonObject {
  eligible_agents: string[]; templates: Template[]; max_created_todos: number; out_of_policy: "reject";
}
interface Proposal extends JsonObject {
  schema_version: typeof GOAL_AMENDMENT_PROPOSAL_SCHEMA;
  proposal_id: string; proposer_agent_id: string; goal_ref: GoalRef;
  base_goal_revision: number; base_intent_digest: string; base_work_digest: string;
  template_id: string; template_digest: string; evidence_refs: string[];
  replan_obligation_id: string | null; digest: string;
}
interface IntentState extends JsonObject {
  schema_version: typeof SHARED_GOAL_INTENT_SCHEMA; goal_ref: GoalRef;
  root_intent: RootIntent; policy: Policy; intent_digest: string; goal_revision: number;
  proposals: Proposal[]; created_todo_ids: string[];
  adoptions: Record<string, {goal_revision: number; intent_digest: string}>;
}
class GoalAmendmentRebase extends AuthorityStoreProtocolError {
  readonly reasonCode = "goal_amendment_needs_rebase";
}
function requireValue(condition: unknown, reason: string): asserts condition {
  if (!condition) {
    if (reason === "goal_amendment_needs_rebase") throw new GoalAmendmentRebase(reason);
    throw new AuthorityStoreProtocolError(reason);
  }
}
function object(value: unknown, label: string): JsonObject { return canonicalAuthorityObject(value, label); }
function keys(row: JsonObject, required: string[], optional: string[] = []): void {
  requireValue(required.every(key => Object.hasOwn(row, key)) && Object.keys(row).every(key => required.includes(key) || optional.includes(key)),
    "goal amendment fields are missing or unsupported");
}
function text(value: unknown, label: string, max = 4096): string {
  requireValue(typeof value === "string" && value.trim() === value && !!value && value.length <= max && !value.includes("\0"), `${label} must be nonempty bounded text`);
  return value;
}
function id(value: unknown, label: string): string {
  const result = text(value, label, 128);
  requireValue(/^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(result), `${label} must be a safe identifier`);
  return result;
}
function integer(value: unknown, label: string, min = 0, max = Number.MAX_SAFE_INTEGER): number {
  requireValue(Number.isSafeInteger(value) && Number(value) >= min && Number(value) <= max, `${label} is outside its supported range`);
  return Number(value);
}
function list(value: unknown, label: string, max: number): unknown[] {
  requireValue(Array.isArray(value) && value.length <= max, `${label} must be a bounded list`);
  return value;
}
function strings(value: unknown, label: string, max = 32): string[] {
  const values = list(value, label, max).map(v => text(v, label, 512));
  requireValue(new Set(values).size === values.length, `${label} must be unique`);
  return values;
}
function evidenceRefs(value: unknown): string[] {
  const refs = strings(value, "evidence_refs", 8);
  requireValue(refs.length && refs.every(item => /^(?:evidence:[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}|sha256:[a-f0-9]{64})$/.test(item)),
    "evidence refs must be opaque evidence identifiers or SHA-256 digests; retain paths, URLs and raw evidence in their owning store");
  return refs;
}
function ref(value: unknown): GoalRef {
  const row = object(value, "goal_ref"); keys(row, ["goal_id", "goal_instance_id"]);
  const parsed = parseExactGoalRef(row);
  requireValue(parsed.kind === "parsed", "goal_ref must identify an exact source Goal instance");
  return {goal_id: parsed.value.goalId.value, goal_instance_id: parsed.value.goalInstanceId.value};
}
function same(left: unknown, right: unknown): boolean {return canonicalAuthoritySha256(left) === canonicalAuthoritySha256(right);}
function bounded(value: unknown): void {requireValue(Buffer.byteLength(JSON.stringify(value), "utf8") <= LIMITS.bytes, "goal amendment capacity exceeded");}
function rootIntent(value: unknown): RootIntent {
  const raw = object(value, "root_intent");
  keys(raw, ["objective", "non_goals", "acceptance", "permission_scope", "stop_conditions", "terminal_conditions"]);
  const permissions = object(raw.permission_scope, "permission_scope"); keys(permissions, ["write_scopes", "capabilities"]);
  const result: RootIntent = {objective: text(raw.objective, "objective"), non_goals: strings(raw.non_goals, "non_goals"),
    acceptance: strings(raw.acceptance, "acceptance"), stop_conditions: strings(raw.stop_conditions, "stop_conditions"),
    terminal_conditions: strings(raw.terminal_conditions, "terminal_conditions"), permission_scope: {
      write_scopes: strings(permissions.write_scopes, "write_scopes"), capabilities: strings(permissions.capabilities, "capabilities"),
    }};
  requireValue(result.acceptance.length && result.stop_conditions.length && result.terminal_conditions.length,
    "root acceptance, stop and terminal conditions must be explicit");
  return result;
}
function policy(value: unknown, root: RootIntent): Policy {
  const raw = object(value, "amendment policy"); keys(raw, ["eligible_agents", "templates", "max_created_todos", "out_of_policy"]);
  requireValue(raw.out_of_policy === "reject", "the first amendment policy must explicitly reject out-of-policy operations");
  const eligible = strings(raw.eligible_agents, "eligible_agents", LIMITS.peers).map(v => id(v, "eligible Agent"));
  requireValue(eligible.length, "amendment policy needs eligible registered Agents");
  const templates = list(raw.templates, "templates", LIMITS.templates).map(value => {
    const t = object(value, "template"); keys(t, ["id", "todo"], ["digest"]);
    const todo = object(t.todo, "template Todo");
    keys(todo, ["text", "action_kind", "target_key", "required_write_scopes", "required_capabilities"], ["task_repository"]);
    text(todo.text, "template text"); id(todo.action_kind, "action_kind"); text(todo.target_key, "target_key", 128);
    if (todo.task_repository !== undefined) text(todo.task_repository, "task_repository", 512);
    const writes = strings(todo.required_write_scopes, "template write scopes"), capabilities = strings(todo.required_capabilities, "template capabilities");
    requireValue(writes.every(v => root.permission_scope.write_scopes.includes(v)) && capabilities.every(v => root.permission_scope.capabilities.includes(v)),
      "template cannot exceed the root permission envelope");
    const normalized = {...todo, required_write_scopes: writes, required_capabilities: capabilities};
    const digest = canonicalAuthoritySha256(normalized);
    requireValue(t.digest === undefined || t.digest === digest, "template digest mismatch");
    return {id: id(t.id, "template id"), todo: normalized, digest};
  });
  requireValue(templates.length && new Set(templates.map(t => t.id)).size === templates.length, "template ids must be nonempty and unique");
  return {out_of_policy: "reject", eligible_agents: eligible, templates, max_created_todos: integer(raw.max_created_todos, "max_created_todos", 1, LIMITS.created)};
}
function readIntent(head: JsonObject): IntentState | null {
  if (head.shared_goal_intent === undefined) return null;
  const raw = object(head.shared_goal_intent, "shared Goal intent");
  keys(raw, ["schema_version", "goal_ref", "root_intent", "policy", "intent_digest", "goal_revision", "proposals", "created_todo_ids", "adoptions"]);
  requireValue(raw.schema_version === SHARED_GOAL_INTENT_SCHEMA, "shared Goal intent schema unsupported");
  const goalRef = ref(raw.goal_ref), root = rootIntent(raw.root_intent), rules = policy(raw.policy, root);
  const revision = integer(raw.goal_revision, "Goal revision", 1);
  requireValue(goalRef.goal_id === head.goal_id, "shared Goal intent belongs to another Goal");
  requireValue(raw.intent_digest === canonicalAuthoritySha256({goal_ref: goalRef, root_intent: root, policy: rules}), "shared Goal intent digest mismatch");
  const proposals = list(raw.proposals, "proposals", LIMITS.proposals).map(v => {
    const p = object(v, "stored proposal");
    keys(p, ["schema_version", "proposal_id", "proposer_agent_id", "goal_ref", "base_goal_revision", "base_intent_digest", "base_work_digest",
      "template_id", "template_digest", "evidence_refs", "replan_obligation_id", "digest"]);
    const {digest, ...content} = p;
    requireValue(digest === canonicalAuthoritySha256(content) && p.schema_version === GOAL_AMENDMENT_PROPOSAL_SCHEMA && same(p.goal_ref, goalRef), "stored amendment proposal is corrupt");
    id(p.proposal_id, "proposal id");
    requireValue(rules.eligible_agents.includes(id(p.proposer_agent_id, "proposal Agent")), "stored proposal actor is outside the root policy");
    integer(p.base_goal_revision, "proposal revision", 1, revision);
    requireValue(p.base_intent_digest === raw.intent_digest && /^[a-f0-9]{64}$/.test(text(p.base_work_digest, "proposal work digest")), "stored proposal basis is invalid");
    requireValue(rules.templates.some(t => t.id === p.template_id && t.digest === p.template_digest), "stored proposal template is invalid");
    evidenceRefs(p.evidence_refs);
    requireValue(/^replan-[a-f0-9]{16}$/.test(text(p.replan_obligation_id, "proposal obligation")), "stored proposal obligation is invalid");
    return p as Proposal;
  });
  requireValue(new Set(proposals.map(p => p.proposal_id)).size === proposals.length, "duplicate amendment proposal ids");
  const adoptions = object(raw.adoptions, "peer adoptions");
  requireValue(Object.keys(adoptions).length <= LIMITS.peers, "peer adoption capacity exceeded");
  for (const [agent, value] of Object.entries(adoptions)) {
    id(agent, "adopting Agent"); const a = object(value, "adoption"); keys(a, ["goal_revision", "intent_digest"]);
    requireValue(rules.eligible_agents.includes(agent), "stored adoption actor is outside the root policy");
    integer(a.goal_revision, "adoption revision", 1, revision); requireValue(a.intent_digest === raw.intent_digest, "adoption intent digest mismatch");
  }
  const result = {...raw, goal_ref: goalRef, root_intent: root, policy: rules, proposals,
    goal_revision: revision, created_todo_ids: strings(raw.created_todo_ids, "created Todos", LIMITS.created), adoptions} as IntentState;
  requireValue(result.goal_revision === result.created_todo_ids.length + 1 && result.created_todo_ids.length <= result.policy.max_created_todos, "Goal revision and created-work history disagree");
  requireValue(result.created_todo_ids.every((todoId, index) => proposals.some(p =>
    amendmentTodoId(goalRef, p.proposal_id) === todoId && p.base_goal_revision === index + 1)), "created work is missing its ordered proposal lineage");
  bounded(result); return result;
}
function failure(reason_code: string, reason: string): JsonObject & {schema_version: typeof GOAL_AMENDMENT_RESULT_SCHEMA} {
  return {ok: false, schema_version: GOAL_AMENDMENT_RESULT_SCHEMA, status: "failed", changed: false, reason_code, reason, execution_authority_granted: false};
}
function amendmentTodoId(goalRef: GoalRef, proposalId: string): string {
  return "todo_" + createHash("sha256").update(canonicalAuthoritySha256({goal_ref: goalRef, proposal_id: proposalId})).digest("hex").slice(0, 24);
}
function workDigest(head: JsonObject): string {
  const rows = indexCoordinationProjection(head, String(head.goal_id));
  return canonicalAuthoritySha256({todos: [...rows.todos].sort(([a], [b]) => a < b ? -1 : 1), leases: [...rows.leases].sort(([a], [b]) => a < b ? -1 : 1)});
}
function view(head: AuthorityStoreHead, goalRef: GoalRef, agents: string[], source: JsonObject): JsonObject {
  const state = readIntent(head.head);
  return {schema_version: GOAL_AMENDMENT_RESULT_SCHEMA, status: "loaded", goal_ref: goalRef, provider_revision: head.provider_revision,
    source_commitments: source.root_commitments, work_digest: workDigest(head.head), intent: state, work_items: [...indexCoordinationProjection(head.head, goalRef.goal_id).todos.values()], execution_authority_granted: false,
    peer_adoption: agents.map(agent => ({agent_id: agent, based_on_goal_revision: state?.adoptions[agent]?.goal_revision ?? null,
      current: !!state && state.adoptions[agent]?.goal_revision === state.goal_revision && same(state.goal_ref, goalRef)})),
    current_lifetime: same(goalRef, source.current_goal_ref) && (state === null || same(state.goal_ref, goalRef)),
    mutation_readiness: sourceGoalMutationReadiness({...source, requested_goal_ref: goalRef})};
}
function command(request: JsonObject, op: Operation) {
  const content = Object.fromEntries(Object.entries(request).filter(([key]) => !["runtime_root", "source", "observed_at", "dry_run"].includes(key)));
  return new CoordinationCommandReceipt({result_schema: GOAL_AMENDMENT_RESULT_SCHEMA,
    identity: {schema_version: GOAL_AMENDMENT_OPERATION_SCHEMA, goal_id: String(ref(request.goal_ref).goal_id),
      operation_id: id(request.operation_id, "operation_id"), request_sha256: canonicalAuthoritySha256({op, ...content})},
    failure, decode: commandReceiptResult});
}
function requireOpenObligation(source: JsonObject, obligation: unknown, actor: string, goalRef: GoalRef): void {
  const rows = list(source.open_replan_obligations, "source replan inventory", 128).map(v => object(v, "source obligation"));
  requireValue(rows.some(row => row.obligation_id === obligation && row.goal_id === goalRef.goal_id && row.required === true
    && Array.isArray(row.bound_agent_ids) && row.bound_agent_ids.includes(actor)), "amendment requires a current source-owned replan obligation for its proposer");
}
function currentBasis(request: JsonObject, state: IntentState, head: JsonObject): void {
  requireValue(request.base_goal_revision === state.goal_revision && request.base_intent_digest === state.intent_digest && request.base_work_digest === workDigest(head),
    "goal_amendment_needs_rebase");
}

/** The source adapter holds the exact Goal lifecycle and registry guards until
 * this call returns. No source-supplied boolean alone can replace that contract. */
export async function executeGoalAmendment(store: AuthorityStore, value: unknown, sourceCurrent: AuthoritySourceCheck = uncheckedAuthoritySource): Promise<JsonObject> {
  const request = object(value, "Goal amendment request");
  const result = await executeAmendmentOperation(store, request, sourceCurrent);
  // Success is operation-specific: a missing receipt is a completed historical
  // lookup, but missing authority cannot successfully inspect or perform work.
  // Transports must not maintain their own, diverging status classifications.
  const ok = request.operation === "inspect" ? result.status === "loaded"
    : request.operation === "receipt" ? result.status === "found" || result.status === "missing"
    : ["planned", "applied", "recovered", "replayed", "no_change"].includes(String(result.status));
  return {...result, ok, ...(!ok && result.status === "missing" ? {
    reason_code: "goal_amendment_source_unavailable",
    reason: "Canonical Goal authority is missing; restore its original storage before retrying.",
  } : {})};
}

async function executeAmendmentOperation(store: AuthorityStore, value: unknown, sourceCurrent: AuthoritySourceCheck): Promise<JsonObject> {
  const request = object(value, "Goal amendment request"); bounded(request);
  const op = request.operation as Operation;
  requireValue(Object.hasOwn(OPERATION_FIELDS, op), "unsupported Goal amendment operation");
  keys(request, ["operation", "goal_ref", "actor_agent_id", "source", ...OPERATION_FIELDS[op]], ["runtime_root", "observed_at", "dry_run", "operation_id"]);
  const goalRef = ref(request.goal_ref), source = object(request.source, "source-owned facts");
  keys(source, ["profile_id", "current_goal_ref", "gate", "registered_agents", "open_replan_obligations", "goal_status", "root_commitments"], ["registry_source", "accounting_admission"]);
  const agents = strings(source.registered_agents, "registered Agents", LIMITS.peers).map(v => id(v, "Agent"));
  const actor = request.actor_agent_id === null ? null : id(request.actor_agent_id, "actor Agent");
  requireValue(actor === null || agents.includes(actor), "amendment actor is not currently registered");
  if (op === "receipt") {
    const receipt = await store.readReceipt(id(request.receipt_operation_id, "receipt operation"));
    if (receipt.status === "found") requireValue(receipt.receipts.length === 1 && receipt.receipts[0].schema_version === GOAL_AMENDMENT_OPERATION_SCHEMA
      && same(object(receipt.receipts[0].result, "amendment receipt").goal_ref, goalRef), "historical amendment receipt belongs to another Goal instance or operation");
    return {schema_version: GOAL_AMENDMENT_RESULT_SCHEMA, ...receipt, historical_only: true, execution_authority_granted: false};
  }
  const receipt = op === "inspect" || op === "claim" ? null : command(request, op);
  if (receipt && request.dry_run !== true) {
    const replay = await receipt.read(store);
    if (replay !== null) return {...replay, historical_only: true, execution_authority_granted: false};
  }
  if (!await sourceCurrent()) return failure("authority_source_changed", "Source Goal registration changed; inspect before retrying");
  const readiness = sourceGoalMutationReadiness({...source, requested_goal_ref: goalRef});
  if (op !== "inspect" && readiness.kind === "reject") return failure(readiness.code, "Goal lifetime does not admit this mutation");
  const observation = receipt ? await receipt.observe(store) : {kind: "authority" as const, authority: await store.loadAuthority()};
  if (observation.kind === "receipt") return {...observation.result, historical_only: true, execution_authority_granted: false};
  const loaded = observation.authority;
  if (loaded.status !== "loaded") return {schema_version: GOAL_AMENDMENT_RESULT_SCHEMA, ...loaded, execution_authority_granted: false};
  validateCoordinationTodoReadModel(loaded.head, goalRef.goal_id);
  const lifecycle = readGoalAcceptanceLifecycle(loaded.head, goalRef.goal_id);
  // A timed-out caller releases its source locks while the RPC can still be
  // queued. Recreation serializes this canonical retirement fence under the
  // same writer lock, so delayed old-instance requests cannot pass cached facts.
  if (op !== "inspect" && lifecycle !== null &&
      (lifecycle.state !== "active" || !sameExactGoalRef(lifecycle.goal_ref, goalRef))) {
    return failure("stale_goal_instance", "The canonical Goal lifetime is retiring or has changed");
  }
  const state = readIntent(loaded.head);
  if (op === "inspect") return view(loaded, goalRef, agents, source);
  if (op === "claim") {
    requireValue(state !== null && same(state.goal_ref, goalRef) && actor !== null, "current exact Goal intent is unavailable");
    requireValue(state.created_todo_ids.includes(String(request.todo_id)), "claim must target work created by this Goal amendment owner");
    const operationId = id(request.operation_id, "claim operation");
    const prior = await store.readReceipt(operationId);
    if (prior.status !== "found") {
      currentBasis(request, state, loaded.head);
      requireValue(state.adoptions[actor]?.goal_revision === state.goal_revision, "Agent must adopt the current Goal basis before claiming new work");
    }
    const origin = state.proposals.find(p => amendmentTodoId(goalRef, p.proposal_id) === request.todo_id);
    requireValue(origin, "amendment-created work is missing its original proposal");
    const binding = origin.proposer_agent_id === actor ? origin.replan_obligation_id : null;
    const existingBinding = indexCoordinationProjection(loaded.head, goalRef.goal_id).todos.get(String(request.todo_id))?.replan_obligation_id;
    // Consume an open obligation only when materializing its binding. Once
    // bound, ordinary claim/lease continuation must not reopen settled replan.
    if (binding !== null && existingBinding !== binding && prior.status !== "found") {
      requireOpenObligation(source, binding, actor, goalRef);
    }
    const lease = request.lease_request === null ? null : object(request.lease_request, "lease_request") as unknown as CoordinationTodoClaimLeaseRequest;
    const result = await executeCoordinationTodoClaim(store, {goal_id: goalRef.goal_id, todo_id: id(request.todo_id, "todo_id"),
      claimed_by: actor, actor_agent_id: actor, expected_role: "agent", registered_agents: agents,
      operation_id: operationId, expected_provider_revision: text(request.expected_provider_revision, "expected_provider_revision"),
      dry_run: request.dry_run === true, now: new Date(text(request.observed_at, "observed_at", 64)), lease_request: lease,
      ...(binding === null ? {} : {replan_obligation_id: binding})}, sourceCurrent);
    return {...result, schema_version: GOAL_AMENDMENT_RESULT_SCHEMA, goal_ref: goalRef,
      claim_receipt: result.original_receipt, execution_authority_granted: false};
  }

  let next = structuredClone(loaded.head), result: JsonObject;
  const now = text(request.observed_at, "observed_at", 64);
  requireValue(Number.isFinite(Date.parse(now)), "observed_at must be a timestamp");
  if (op === "configure") {
    requireValue(actor === null, "only the trusted Goal owner can initialize root intent");
    requireValue(state === null, "root intent is immutable; use lifecycle retirement, not replacement");
    requireValue(loaded.provider_revision === request.expected_provider_revision, "goal_amendment_needs_rebase");
    const root = rootIntent(request.root_intent), rules = policy(request.policy, root);
    const original = object(source.root_commitments, "source-owned original Goal commitments");
    keys(original, ["objective", "non_goals", "acceptance", "stop_condition"]);
    requireValue(root.objective === original.objective && same(root.non_goals, original.non_goals) && same(root.acceptance, original.acceptance)
      && same(root.stop_conditions, [original.stop_condition]), "root initialization must preserve the original source Goal commitments");
    requireValue(rules.eligible_agents.every(v => agents.includes(v)), "policy Agent is not registered");
    const configured: IntentState = {schema_version: SHARED_GOAL_INTENT_SCHEMA, goal_ref: goalRef, root_intent: root, policy: rules,
      intent_digest: canonicalAuthoritySha256({goal_ref: goalRef, root_intent: root, policy: rules}), goal_revision: 1,
      proposals: [], created_todo_ids: [], adoptions: {}};
    next.shared_goal_intent = configured; result = {changed: true, goal_ref: goalRef, goal_revision: 1, intent_digest: configured.intent_digest};
  } else {
    requireValue(state !== null && same(state.goal_ref, goalRef), "current exact Goal intent is unavailable");
    requireValue(actor !== null && state.policy.eligible_agents.includes(actor), "actor is outside the amendment policy");
    const updated = structuredClone(state); next.shared_goal_intent = updated;
    if (op === "adopt") {
      currentBasis(request, state, loaded.head);
      Object.defineProperty(updated.adoptions, actor, {value: {goal_revision: state.goal_revision, intent_digest: state.intent_digest}, enumerable: true, writable: true, configurable: true});
      result = {changed: true, goal_ref: goalRef, agent_id: actor, based_on_goal_revision: state.goal_revision, intent_digest: state.intent_digest};
    } else if (op === "propose") {
      currentBasis(request, state, loaded.head);
      requireValue(state.adoptions[actor]?.goal_revision === state.goal_revision, "Agent must adopt the current Goal basis");
      requireValue(state.proposals.length < LIMITS.proposals, "amendment proposal capacity exceeded");
      const template = state.policy.templates.find(t => t.id === request.template_id);
      requireValue(template, "unknown amendment template");
      const proposalId = id(request.proposal_id, "proposal_id");
      requireValue(!state.proposals.some(p => p.proposal_id === proposalId), "proposal identity already exists; replay its original operation");
      const obligation = text(request.replan_obligation_id, "replan obligation", 64);
      requireValue(/^replan-[a-f0-9]{16}$/.test(obligation), "invalid replan obligation id");
      requireOpenObligation(source, obligation, actor, goalRef);
      const evidence = evidenceRefs(request.evidence_refs);
      const content = {schema_version: GOAL_AMENDMENT_PROPOSAL_SCHEMA as typeof GOAL_AMENDMENT_PROPOSAL_SCHEMA, proposal_id: proposalId, proposer_agent_id: actor, goal_ref: goalRef,
        base_goal_revision: state.goal_revision, base_intent_digest: state.intent_digest, base_work_digest: workDigest(loaded.head),
        template_id: template.id, template_digest: template.digest, evidence_refs: evidence, replan_obligation_id: obligation};
      const proposal: Proposal = {...content, digest: canonicalAuthoritySha256(content)};
      updated.proposals.push(proposal); result = {changed: true, goal_ref: goalRef, canonical_effect: "none", proposal};
    } else {
      const proposal = state.proposals.find(p => p.proposal_id === request.proposal_id);
      requireValue(proposal && proposal.digest === request.proposal_digest && proposal.proposer_agent_id === actor, "amendment proposal identity mismatch");
      currentBasis(proposal, state, loaded.head);
      requireOpenObligation(source, proposal.replan_obligation_id, actor, goalRef);
      requireValue(state.adoptions[actor]?.goal_revision === state.goal_revision, "Agent must adopt the current Goal basis");
      requireValue(state.created_todo_ids.length < state.policy.max_created_todos, "amendment creation capacity exceeded");
      const template = state.policy.templates.find(t => t.id === proposal.template_id && t.digest === proposal.template_digest);
      requireValue(template, "amendment template changed");
      const todoId = amendmentTodoId(goalRef, proposal.proposal_id);
      const index = indexCoordinationProjection(loaded.head, goalRef.goal_id);
      const plan = planCoordinationTodoCreate({goal_id: goalRef.goal_id, operation_id: String(request.operation_id), actor_agent_id: actor,
        registered_agents: agents, dry_run: true, now: new Date(now), todo: {...template.todo, schema_version: TODO_DOMAIN_ITEM_SCHEMA,
          todo_id: todoId, role: "agent", task_class: "advancement_task", status: "open", done: false, archive_state: "active"}},
        index.todos, object(loaded.head.todo_read_model, "Todo read model").schema_version);
      requireValue(plan.status === "planned", "amendment Todo is already satisfied or conflicts; revise the proposal");
      const commit = prepareCoordinationProjectionCommit({goal_id: goalRef.goal_id, operation_id: String(request.operation_id),
        expected_provider_revision: loaded.provider_revision, projection: loaded.head,
        mutations: [{kind: "todo_upsert", todo: object(plan.todo, "planned Todo")}]});
      updated.goal_revision++; updated.created_todo_ids.push(todoId); next = {...commit.next_projection, shared_goal_intent: updated};
      result = {changed: true, goal_ref: goalRef, canonical_effect: "committed", proposal_id: proposal.proposal_id, proposal_digest: proposal.digest,
        prior_goal_revision: state.goal_revision, goal_revision: updated.goal_revision, intent_digest: state.intent_digest,
        todo_id: todoId, todo: plan.todo, lease_disposition: "unaffected", lease_basis_digest: canonicalAuthoritySha256([...index.leases]),
        replan_obligation_id: proposal.replan_obligation_id, replan_settlement: proposal.replan_obligation_id === null ? "not_required" : "pending",
        peer_adoption: "pending"};
    }
  }
  if (!await sourceCurrent()) return failure("authority_source_changed", "Source Goal registration changed before commit");
  bounded(next.shared_goal_intent);
  const stored = {...receipt!.contract.identity, result: {...result, execution_authority_granted: false}};
  if (request.dry_run === true) return {schema_version: GOAL_AMENDMENT_RESULT_SCHEMA, status: "planned", ...result,
    changed: false, canonical_effect: "none", dry_run: true, execution_authority_granted: false};
  return {...await receipt!.commit(store, {expected_provider_revision: loaded.provider_revision, operation_id: String(request.operation_id),
    next_projection: next, events: [{schema_version: GOAL_AMENDMENT_OPERATION_SCHEMA, operation: op, goal_ref: goalRef, operation_id: request.operation_id}], receipts: [stored]}),
    execution_authority_granted: false};
}

export async function goalAmendmentLocal(value: unknown): Promise<JsonObject> {
  const request = object(value, "Goal amendment request");
  const root = text(request.runtime_root, "runtime_root"); requireValue(isAbsolute(root), "runtime_root must be absolute");
  const goal = ref(request.goal_ref);
  try {
    const source = object(request.source, "source");
    const observationOwner = parseQuotaAccountingOwner({runtimeRoot: root, goalId: goal.goal_id,
      goalRefValue: request.operation === "inspect" || request.operation === "receipt" ? source.current_goal_ref : goal,
      sourceAdmissionValue: source.accounting_admission});
    return await withBorrowedQuotaAccountingOwner(observationOwner, async () =>
      withCanonicalWriter(root, goal.goal_id, request.operation === "inspect" || request.operation === "receipt" || request.dry_run === true,
        async () => executeGoalAmendment(await openLocalAuthorityStore(root, goal.goal_id), request,
          registryAuthoritySourceCheck({registry_source: source.registry_source}, true))));
  } catch (error) {
    if (error instanceof EffectRuntimeConflictError) return failure(error.code, error.message);
    if (error instanceof GoalAmendmentRebase) return failure(error.reasonCode, "Goal basis changed; inspect and preview the updated basis before retrying");
    if (error instanceof AuthorityStoreProtocolError) return failure("goal_amendment_rejected", error.message);
    const failed = localAuthorityOpenFailure(error);
    if (Object.keys(failed).length) return {...failure("goal_amendment_source_unavailable", "Goal authority is unavailable"), ...failed};
    throw error;
  }
}
