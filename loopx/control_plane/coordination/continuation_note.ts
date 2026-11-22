/** Shared continuation-note validation for the explicit cross-agent handoff flow.
 * This is the single source of truth for whether a note is a valid prepared
 * continuation note. Both the handoff helper (todo_continuation.ts) and the
 * final claim authority (todo_claim.ts) use this to prevent arbitrary JSON
 * notes from being accepted as handoff intent.
 *
 * The note schema supports two shapes:
 * - Legacy: rationale + source_refs (backward compatible)
 * - Rich: work_summary + structured context (approaches_tried, next_steps, etc.)
 * A note is valid if it carries the marker, current todo_facts, a source_session,
 * and at least one of (work_summary, rationale).
 */
import type { JsonObject } from "../effect_program.ts";
import { canonicalAuthorityObject, canonicalAuthoritySha256 } from "./authority_store_codec.ts";

export const CONTINUATION_NOTE_MARKER = "loopx-explicit-continuation";

// Any changed execution fact expires the note. Audit-only writes do not.
export function computeContinuationTodoFacts(todo: JsonObject): string {
  const copy = { ...todo };
  for (const key of ["note", "updated_at", "last_actor_agent_id"]) delete copy[key];
  return canonicalAuthoritySha256(copy);
}

export interface ContinuationNoteApproachTried {
  readonly approach: string;
  readonly outcome: "success" | "partial" | "failed";
  readonly reason: string;
}

export interface ContinuationNoteFileTouched {
  readonly path: string;
  readonly action: "read" | "edited" | "created" | "deleted";
  readonly summary?: string;
}

export interface ContinuationNoteDecision {
  readonly decision: string;
  readonly rationale: string;
}

// Input shape for constructing a rich continuation note.
export interface ContinuationNoteContext {
  readonly work_summary?: string;
  readonly approaches_tried?: readonly ContinuationNoteApproachTried[];
  readonly next_steps?: readonly string[];
  readonly files_touched?: readonly ContinuationNoteFileTouched[];
  readonly key_decisions?: readonly ContinuationNoteDecision[];
  readonly open_questions?: readonly string[];
  // Legacy fields (still supported for backward compat):
  readonly rationale?: string;
  readonly source_refs?: readonly string[];
}

export interface ContinuationNoteValidation {
  readonly valid: boolean;
  readonly note: JsonObject | null;
  readonly noteFacts: string;
}

// Allowed keys for the raw --from-context input (before producer adds
// kind/source_session/todo_facts). This is the public input contract.
export const CONTEXT_ROOT_KEYS: ReadonlySet<string> = new Set([
  "work_summary",
  "rationale",
  "source_refs",
  "approaches_tried",
  "next_steps",
  "files_touched",
  "key_decisions",
  "open_questions",
]);

// Exact closed bounded schema for continuation notes. This is the single
// source of truth shared by both the producer (buildContextFromInput) and the
// final claim authority (validateContinuationNote). Every constraint here
// MUST match what the producer can generate — otherwise the final authority
// accepts notes that no producer can create.
//
// Root-level allowed keys:
const NOTE_ROOT_KEYS = new Set([
  "kind", "source_session", "todo_facts", ...CONTEXT_ROOT_KEYS,
]);

// Per-field max lengths. Must match buildContextFromInput bounds exactly.
const FIELD_MAX = {
  source_session: 160,
  work_summary: 2000,
  rationale: 600,
  source_ref: 180,
  approach: 300,
  reason: 300,
  next_step: 300,
  path: 240,
  file_summary: 200,
  decision: 300,
  decision_rationale: 300,
  open_question: 300,
} as const;

// Nested object allowed keys.
const APPROACH_KEYS = new Set(["approach", "outcome", "reason"]);
const FILE_KEYS = new Set(["path", "action", "summary"]);
const DECISION_KEYS = new Set(["decision", "rationale"]);

function boundedString(value: unknown, _name: string, max: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}

function hasOnlyKeys(obj: Record<string, unknown>, allowed: ReadonlySet<string>): boolean {
  return Object.keys(obj).every(k => allowed.has(k));
}

function isApproachTried(value: unknown): value is ContinuationNoteApproachTried {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (!hasOnlyKeys(v, APPROACH_KEYS)) return false;
  return boundedString(v.approach, "approach", FIELD_MAX.approach) &&
    ["success", "partial", "failed"].includes(String(v.outcome)) &&
    boundedString(v.reason, "reason", FIELD_MAX.reason);
}

function isFileTouched(value: unknown): value is ContinuationNoteFileTouched {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (!hasOnlyKeys(v, FILE_KEYS)) return false;
  return boundedString(v.path, "path", FIELD_MAX.path) &&
    ["read", "edited", "created", "deleted"].includes(String(v.action)) &&
    (v.summary === undefined || boundedString(v.summary, "summary", FIELD_MAX.file_summary));
}

function isDecision(value: unknown): value is ContinuationNoteDecision {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (!hasOnlyKeys(v, DECISION_KEYS)) return false;
  return boundedString(v.decision, "decision", FIELD_MAX.decision) &&
    boundedString(v.rationale, "decision_rationale", FIELD_MAX.decision_rationale);
}

// Validate the raw --from-context input against the closed bounded schema.
// Throws on the first violation: unknown root key, wrong type, or bounds
// overflow. This runs at the earliest boundary — before any field reaches
// the persisted note — so the producer can never silently sanitize bad
// input into a smaller but legal note.
export function validateRawContext(input: JsonObject): asserts input is JsonObject & ContinuationNoteContext {
  if (!hasOnlyKeys(input, CONTEXT_ROOT_KEYS)) {
    const extra = Object.keys(input).filter(k => !CONTEXT_ROOT_KEYS.has(k));
    throw new Error(`unknown context field: ${extra.join(", ")}`);
  }
  if (input.work_summary !== undefined) {
    if (typeof input.work_summary !== "string" || !input.work_summary.trim() ||
        input.work_summary.length > FIELD_MAX.work_summary) {
      throw new Error(`work_summary must be non-empty text of at most ${FIELD_MAX.work_summary} characters`);
    }
  }
  if (input.rationale !== undefined) {
    if (typeof input.rationale !== "string" || !input.rationale.trim() ||
        input.rationale.length > FIELD_MAX.rationale) {
      throw new Error(`rationale must be non-empty text of at most ${FIELD_MAX.rationale} characters`);
    }
  }
  if (input.source_refs !== undefined) {
    if (!Array.isArray(input.source_refs) || input.source_refs.length > 20 ||
        !input.source_refs.every(v => boundedString(v, "source_ref", FIELD_MAX.source_ref))) {
      throw new Error(`source_refs must be an array of at most 20 non-empty strings of at most ${FIELD_MAX.source_ref} characters`);
    }
  }
  if (input.approaches_tried !== undefined) {
    if (!Array.isArray(input.approaches_tried) || input.approaches_tried.length > 20 ||
        !input.approaches_tried.every(isApproachTried)) {
      throw new Error("approaches_tried must be an array of at most 20 objects with approach/outcome/reason");
    }
  }
  if (input.next_steps !== undefined) {
    if (!Array.isArray(input.next_steps) || input.next_steps.length > 20 ||
        !input.next_steps.every(v => boundedString(v, "next_step", FIELD_MAX.next_step))) {
      throw new Error(`next_steps must be an array of at most 20 non-empty strings of at most ${FIELD_MAX.next_step} characters`);
    }
  }
  if (input.files_touched !== undefined) {
    if (!Array.isArray(input.files_touched) || input.files_touched.length > 50 ||
        !input.files_touched.every(isFileTouched)) {
      throw new Error("files_touched must be an array of at most 50 objects with path/action[/summary]");
    }
  }
  if (input.key_decisions !== undefined) {
    if (!Array.isArray(input.key_decisions) || input.key_decisions.length > 20 ||
        !input.key_decisions.every(isDecision)) {
      throw new Error("key_decisions must be an array of at most 20 objects with decision/rationale");
    }
  }
  if (input.open_questions !== undefined) {
    if (!Array.isArray(input.open_questions) || input.open_questions.length > 20 ||
        !input.open_questions.every(v => boundedString(v, "open_question", FIELD_MAX.open_question))) {
      throw new Error(`open_questions must be an array of at most 20 non-empty strings of at most ${FIELD_MAX.open_question} characters`);
    }
  }
  if (!input.work_summary && !input.rationale) {
    throw new Error("provide at least one of: work_summary (rich context) or rationale (legacy)");
  }
}

export function validateContinuationNote(
  rawNote: unknown,
  currentTodoFacts: string,
): ContinuationNoteValidation {
  let parsed: JsonObject | null = null;
  try {
    if (typeof rawNote === "string" && rawNote.length > 0) {
      parsed = canonicalAuthorityObject(JSON.parse(rawNote), "continuation note");
    }
  } catch {
    return { valid: false, note: null, noteFacts: "" };
  }
  if (parsed === null) {
    return { valid: false, note: null, noteFacts: "" };
  }
  // Core invariant: marker, current todo_facts, source session (non-empty, bounded).
  if (parsed.kind !== CONTINUATION_NOTE_MARKER || parsed.todo_facts !== currentTodoFacts ||
      !boundedString(parsed.source_session, "source_session", FIELD_MAX.source_session)) {
    return { valid: false, note: parsed, noteFacts: "" };
  }
  // Must have at least one of: work_summary (rich) or rationale (legacy).
  const hasSummary = boundedString(parsed.work_summary, "work_summary", FIELD_MAX.work_summary);
  const hasRationale = boundedString(parsed.rationale, "rationale", FIELD_MAX.rationale);
  if (!hasSummary && !hasRationale) {
    return { valid: false, note: parsed, noteFacts: "" };
  }
  // Reject unknown root keys — the schema is closed.
  if (!hasOnlyKeys(parsed as Record<string, unknown>, NOTE_ROOT_KEYS)) {
    return { valid: false, note: parsed, noteFacts: "" };
  }
  // Validate rich fields when present.
  if (parsed.approaches_tried !== undefined) {
    if (!Array.isArray(parsed.approaches_tried) || parsed.approaches_tried.length > 20 ||
        !parsed.approaches_tried.every(isApproachTried)) {
      return { valid: false, note: parsed, noteFacts: "" };
    }
  }
  if (parsed.next_steps !== undefined) {
    if (!Array.isArray(parsed.next_steps) || parsed.next_steps.length > 20 ||
        !parsed.next_steps.every(v => boundedString(v, "next_step", FIELD_MAX.next_step))) {
      return { valid: false, note: parsed, noteFacts: "" };
    }
  }
  if (parsed.files_touched !== undefined) {
    if (!Array.isArray(parsed.files_touched) || parsed.files_touched.length > 50 ||
        !parsed.files_touched.every(isFileTouched)) {
      return { valid: false, note: parsed, noteFacts: "" };
    }
  }
  if (parsed.key_decisions !== undefined) {
    if (!Array.isArray(parsed.key_decisions) || parsed.key_decisions.length > 20 ||
        !parsed.key_decisions.every(isDecision)) {
      return { valid: false, note: parsed, noteFacts: "" };
    }
  }
  if (parsed.open_questions !== undefined) {
    if (!Array.isArray(parsed.open_questions) || parsed.open_questions.length > 20 ||
        !parsed.open_questions.every(v => boundedString(v, "open_question", FIELD_MAX.open_question))) {
      return { valid: false, note: parsed, noteFacts: "" };
    }
  }
  if (parsed.source_refs !== undefined) {
    if (!Array.isArray(parsed.source_refs) || parsed.source_refs.length > 20 ||
        !parsed.source_refs.every(v => boundedString(v, "source_ref", FIELD_MAX.source_ref))) {
      return { valid: false, note: parsed, noteFacts: "" };
    }
  }
  return {
    valid: true,
    note: parsed,
    noteFacts: canonicalAuthoritySha256(parsed),
  };
}

// Build the canonical continuation note object from a context input.
// Caller is responsible for setting source_session and todo_facts.
export function buildContinuationNote(
  context: ContinuationNoteContext,
  sourceSession: string,
  todoFacts: string,
): JsonObject {
  const note: JsonObject = {
    kind: CONTINUATION_NOTE_MARKER,
    source_session: sourceSession,
    todo_facts: todoFacts,
  };
  if (context.work_summary) note.work_summary = context.work_summary;
  if (context.rationale) note.rationale = context.rationale;
  if (context.source_refs) note.source_refs = context.source_refs;
  if (context.approaches_tried) note.approaches_tried = context.approaches_tried;
  if (context.next_steps) note.next_steps = context.next_steps;
  if (context.files_touched) note.files_touched = context.files_touched;
  if (context.key_decisions) note.key_decisions = context.key_decisions;
  if (context.open_questions) note.open_questions = context.open_questions;
  return note;
}

/** Only an authorized claim-transfer planner may carry a current note forward.
 * Keep owner binding in the fingerprint: arbitrary claim edits still stale it.
 * Never refresh a note whose work facts had already changed before transfer. */
export function carryContinuationAcrossClaimTransfer(before: JsonObject, after: JsonObject): JsonObject {
  const validation = validateContinuationNote(before.note, computeContinuationTodoFacts(before));
  if (!validation.valid) return after;
  const expected = {...before, claimed_by: after.claimed_by};
  if (computeContinuationTodoFacts(expected) !== computeContinuationTodoFacts(after)) return after;
  return {...after, note: JSON.stringify({...validation.note,
    todo_facts: computeContinuationTodoFacts(after)})};
}
