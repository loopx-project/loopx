/** Explicit cross-agent, cross-session continuation over the current Todo.
 * This is a local workflow note, not a history, search or ownership protocol.
 * Phase 1: same-machine, different registered agents.
 *
 * Stage A supports two handoff styles:
 * - Legacy: rationale + source_refs (manual, compact)
 * - Rich context: work_summary + structured fields (approaches_tried,
 *   next_steps, files_touched, key_decisions, open_questions)
 * Both produce a typed continuation note validated by the shared
 * continuation_note.ts predicate. The rich form lets a source agent dump
 * complete working context so the target agent does not need to re-explain
 * what was done, what failed, and what comes next.
 */
import {stat} from "node:fs/promises";
import {isAbsolute, resolve, relative} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {canonicalAuthorityObject, canonicalAuthoritySha256, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {indexCoordinationProjection, validateCoordinationTodoReadModel} from "./coordination_projection.ts";
import {executeCoordinationTodoUpdate} from "./todo_update.ts";
import {decodeTaskLeaseProof} from "./task_lease_proof.ts";
import {continuationExecutionAuthority, sealLeasedContinuationAdoption} from "./continuation_adoption.ts";
import {executeCoordinationTodoClaim} from "./todo_claim.ts";
import {
  CONTINUATION_NOTE_MARKER,
  buildContinuationNote,
  computeContinuationTodoFacts,
  validateContinuationNote,
  validateRawContext,
  type ContinuationNoteContext,
  type ContinuationNoteValidation,
  CONTEXT_ROOT_KEYS,
} from "./continuation_note.ts";

const accepted = new Set(["applied", "replayed", "recovered", "no_change"]);
const reject = (reason_code: string, reason: string): JsonObject =>
  ({ok: false, status: "rejected", reason_code, reason});

function bounded(value: unknown, name: string, max: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > max) {
    throw new Error(`${name} must be non-empty text of at most ${max} characters`);
  }
  return value;
}

// Excludes claimed_by so handoff verification ignores the expected ownership transfer.
function stableFacts(todo: JsonObject): string {
  const copy = {...todo};
  for (const key of ["note", "updated_at", "last_actor_agent_id", "claimed_by"]) delete copy[key];
  return canonicalAuthoritySha256(copy);
}

async function availability(workspace: unknown, artifacts: unknown): Promise<JsonObject> {
  if (typeof workspace !== "string" || !isAbsolute(workspace)) throw new Error("workspace must be absolute");
  if (!Array.isArray(artifacts) || artifacts.length > 8) throw new Error("at most eight artifacts are supported");
  const inspect = async (path: string, directory: boolean) => {
    try { const info = await stat(path); return directory ? info.isDirectory() : info.isFile(); }
    catch (error) { if (["ENOENT", "ENOTDIR"].includes((error as NodeJS.ErrnoException).code ?? "")) return false; throw error; }
  };
  const workspaceAvailable = await inspect(workspace, true);
  const rows = [];
  for (const value of artifacts) {
    const ref = bounded(value, "artifact", 240);
    if (isAbsolute(ref) || relative(workspace, resolve(workspace, ref)).split(/[\\/]/u).includes("..")) {
      throw new Error("artifacts must be workspace-relative paths");
    }
    rows.push({ref, available: await inspect(resolve(workspace, ref), false)});
  }
  return {workspace_available: workspaceAvailable, artifacts: rows,
    ready: workspaceAvailable && rows.every(row => row.available)};
}

function buildContextFromInput(input: JsonObject): ContinuationNoteContext {
  // Two supported shapes:
  //   1. CLI path: context wrapped in input.context (dedicated field).
  //      Only context-only keys are allowed — operational keys (action,
  //      agent_id, goal_id, etc.) are REJECTED, not ignored. This is the
  //      security boundary: a context file cannot override command,
  //      identity, or authority metadata.
  //   2. Direct TypeScript callers: context fields passed flat (backward
  //      compat with unit tests). Request keys are allowed but only
  //      context keys are extracted.
  const contextKeys = CONTEXT_ROOT_KEYS;
  const requestKeys = new Set(["goal_id", "todo_id", "agent_id", "registered_agents", "action",
    "session_id", "operation_id", "expected_provider_revision", "workspace", "artifacts",
    "target_agent_id", "handoff_format", "runtime_root", "lease_proof"]);
  // Resolve the raw context source.
  let rawContext: JsonObject;
  if (input.context !== undefined) {
    // CLI path: context is in the dedicated field. Validate it is an object.
    if (typeof input.context !== "object" || input.context === null || Array.isArray(input.context)) {
      throw new Error("context must be a JSON object with context-only fields (work_summary, rationale, etc.)");
    }
    rawContext = input.context as JsonObject;
    validateRawContext(rawContext);
    return rawContext;
  } else {
    // Backward compat: flat fields. Allow both context and request keys.
    rawContext = input;
    for (const key of Object.keys(rawContext)) {
      if (!contextKeys.has(key) && !requestKeys.has(key)) {
        throw new Error(`unknown context field: ${key}`);
      }
    }
  }
  // Extract only the context fields.
  const context: JsonObject = {};
  for (const key of contextKeys) {
    if (key in rawContext) context[key] = rawContext[key];
  }
  // Validate the extracted context against the closed bounded schema. This
  // throws on the first violation — wrong type, bounds overflow, unknown
  // nested key, or missing required field — so the producer can never
  // silently sanitize bad input into a smaller but legal note.
  validateRawContext(context);
  // All fields are already validated — pass them through directly without
  // any second sanitization. The producer's output shape is guaranteed to
  // match what validateContinuationNote() will see later.
  return context;
}

export async function executeTodoContinuation(store: AuthorityStore, value: unknown): Promise<JsonObject> {
  const input = canonicalAuthorityObject(value, "continuation request");
  const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
  const todoId = requireAuthorityStoreId(input.todo_id, "Todo id");
  const agentId = requireAuthorityStoreId(input.agent_id, "agent id");
  const sessionId = bounded(input.session_id, "session id", 160);
  if (!["prepare", "inspect", "adopt"].includes(String(input.action))) throw new Error("invalid continuation action");
  if (!Array.isArray(input.registered_agents) || !input.registered_agents.includes(agentId)) {
    return reject("actor_not_registered", "Use the registered lane bound to this host session");
  }
  const registered = input.registered_agents.map(v => requireAuthorityStoreId(v, "registered agent"));
  const targetAgentId = input.target_agent_id != null
    ? requireAuthorityStoreId(input.target_agent_id, "target agent id") : null;
  if (targetAgentId != null && !registered.includes(targetAgentId)) {
    return reject("target_agent_not_registered", "Target agent must be registered for this goal");
  }
  const proof = decodeTaskLeaseProof(input.lease_proof);
  const head = await store.loadAuthority();
  if (head.status !== "loaded") return {ok: false, ...head};
  validateCoordinationTodoReadModel(head.head, goalId);
  const projection = indexCoordinationProjection(head.head, goalId);
  const todo = projection.todos.get(todoId);
  if (!todo) return reject("todo_not_found", "The stable Todo ID no longer exists; do not recreate it from text");
  if (todo.status !== "open" || todo.archive_state !== "active") return reject("todo_not_open", "Todo is no longer open and active");
  const leased = ![undefined, "legacy", "soft_claim"].includes(head.head.handoff_mode as string | undefined)
    || projection.leases.has(todoId);
  const authorityInput = {goal_id: goalId, todo_id: todoId, agent_id: agentId,
    registered_agents: registered, proof};
  const execution = leased ? continuationExecutionAuthority(head.head, authorityInput)
    : {allowed: true, reason_code: "lease_not_required"};
  const common = {goal_id: goalId, todo_id: todoId, expected_role: "agent",
    actor_agent_id: agentId, registered_agents: registered, dry_run: false, now: new Date()};
  if (input.action === "prepare") {
    if (todo.claimed_by !== agentId) return reject("continuation_owner_required", "Only the current Todo owner can prepare a continuation note");
    // A leased handoff requires execution authority. Ordinary note-edit admission
    // cannot replace this check; the update below pins the same source CAS.
    if (leased && !execution.allowed) return reject(execution.reason_code,
      "Preparation requires the current execution lease and admission; inspect the reported authority gap");
    const context = buildContextFromInput(input);
    const todoFacts = computeContinuationTodoFacts(todo);
    const noteObj = buildContinuationNote(context, sessionId, todoFacts);
    const note = JSON.stringify(noteObj);
    const result = await executeCoordinationTodoUpdate(store, {...common,
      operation_id: requireAuthorityStoreId(input.operation_id, "operation id"),
      expected_provider_revision: requireAuthorityStoreId(input.expected_provider_revision, "expected revision"),
      patch: {note}, clear_fields: [],
      lease_idempotency_key: proof?.idempotency_key, lease_expected_version: proof?.expected_version});
    const current = await store.loadAuthority();
    const verified = current.status === "loaded" &&
      indexCoordinationProjection(current.head, goalId).todos.get(todoId)?.note === note;
    return {ok: accepted.has(String(result.status)) && verified, action: "prepare", result,
      note_readback_verified: verified, goal_id: goalId, todo_id: todoId,
      provider_revision: current.status === "loaded" ? current.provider_revision : null};
  }
  const environment = await availability(input.workspace, input.artifacts ?? []);
  const noteValidation: ContinuationNoteValidation = validateContinuationNote(todo.note, computeContinuationTodoFacts(todo));
  const validNote = noteValidation.valid;
  const note = noteValidation.note;
  // Build a digest-friendly summary. The CLI can render this as a readable
  // handoff context for the target agent.
  const digest = validNote ? {
    work_summary: typeof note!.work_summary === "string" ? note!.work_summary : undefined,
    rationale: typeof note!.rationale === "string" ? note!.rationale : undefined,
    approaches_tried: Array.isArray(note!.approaches_tried) ? note!.approaches_tried : undefined,
    next_steps: Array.isArray(note!.next_steps) ? note!.next_steps : undefined,
    files_touched: Array.isArray(note!.files_touched) ? note!.files_touched : undefined,
    key_decisions: Array.isArray(note!.key_decisions) ? note!.key_decisions : undefined,
    open_questions: Array.isArray(note!.open_questions) ? note!.open_questions : undefined,
    source_refs: Array.isArray(note!.source_refs) ? note!.source_refs : undefined,
  } : undefined;
  const packet: JsonObject = {ok: true, action: "inspect", goal_id: goalId, todo_id: todoId,
    provider_revision: head.provider_revision, todo_status: todo.status, claimed_by: todo.claimed_by,
    note_state: validNote ? "current" : note?.kind === CONTINUATION_NOTE_MARKER ? "stale" : "missing",
    availability: environment, decision_rationale: validNote ? note!.rationale : null,
    evidence_refs: [`todo:${todoId}`, `revision:${head.provider_revision}`, ...(validNote && Array.isArray(note!.source_refs) ? note!.source_refs as string[] : [])],
    next_step: leased && !execution.allowed
      ? "Resolve the execution_authority gap; use task-lease transfer --transfer-claim when changing owner"
      : validNote && environment.ready === true ? todo.text : "Restore workspace/artifacts and ask the source session to prepare a current decision note",
    ...(leased ? {execution_authority: execution} : {}),
    can_adopt: validNote && environment.ready === true && note!.source_session !== sessionId &&
      (!leased || (execution.allowed && (targetAgentId === null || targetAgentId === agentId))),
    digest};
  if (input.action === "inspect") return packet;
  if (leased && (todo.claimed_by !== agentId || (targetAgentId !== null && targetAgentId !== agentId))) {
    return {...packet, ...reject("continuation_transfer_required",
      "The source must use task-lease transfer --transfer-claim; the receiver then adopts with its own current lease proof")};
  }
  if (leased && !execution.allowed) return {...packet, ...reject(execution.reason_code,
    "Adoption requires the current execution lease and admission; inspect the reported authority gap")};
  if (!packet.can_adopt) return {...packet, ...reject("continuation_not_ready", "Inspect and repair the reported note/session/availability gap before adopting")};
  const adoptOwnerId = targetAgentId ?? agentId;
  const preClaimFacts = stableFacts(todo);
  // Construct a handoff transfer grant binding source owner, target owner,
  // todo id, exact revision, and current continuation-note fingerprint. The
  // note fingerprint is computed by the shared validateContinuationNote so the
  // final claim authority reuses the exact same canonicalization. An invalid
  // note yields empty noteFacts, which the final decision will reject.
  const transferGrant = adoptOwnerId !== todo.claimed_by ? {
    schema_version: "todo_transfer_grant_v0" as const,
    source_agent_id: todo.claimed_by as string,
    target_agent_id: adoptOwnerId,
    todo_id: todoId,
    expected_revision: requireAuthorityStoreId(input.expected_provider_revision, "expected revision"),
    continuation_note_facts: noteValidation.noteFacts,
  } : undefined;
  const result = leased
    ? await sealLeasedContinuationAdoption(store, {...authorityInput, session_id: sessionId,
        operation_id: requireAuthorityStoreId(input.operation_id, "operation id"),
        expected_provider_revision: requireAuthorityStoreId(input.expected_provider_revision, "expected revision"),
        note_facts: noteValidation.noteFacts})
    : await executeCoordinationTodoClaim(store, {...common, claimed_by: adoptOwnerId,
    operation_id: requireAuthorityStoreId(input.operation_id, "operation id"),
    expected_provider_revision: requireAuthorityStoreId(input.expected_provider_revision, "expected revision"),
    transfer_grant: transferGrant});
  // Historical receipt replay alone never proves current authority.
  const current = await store.loadAuthority();
  const currentTodo = current.status === "loaded" ? indexCoordinationProjection(current.head, goalId).todos.get(todoId) : undefined;
  const verified = currentTodo?.status === "open" && currentTodo.archive_state === "active" &&
    currentTodo.claimed_by === adoptOwnerId && currentTodo.note === todo.note && stableFacts(currentTodo) === preClaimFacts &&
    current.status === "loaded" && current.provider_revision === result.provider_revision &&
    (!leased || continuationExecutionAuthority(current.head, authorityInput).allowed);
  return {...packet, action: "adopt", ok: accepted.has(String(result.status)) && verified,
    ...(leased ? {adoption: result} : {claim: result}), current_authority_verified: verified, target_agent_id: adoptOwnerId,
    provider_revision: current.status === "loaded" ? current.provider_revision : null};
}
