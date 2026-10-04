/** Typed compatibility plan. Python owns source locks/capture; TS owns the change. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireStringLiteral} from "../runtime_decode.ts";
import {HANDOFF_MODES, decideHandoffMode} from "./handoff_mode_policy.ts";
import {handoffQuiescence, persistedHandoffMode, previousModeFields} from "./handoff_mode_facts.ts";

export const LEGACY_HANDOFF_PLAN_SCHEMA = "loopx_legacy_handoff_mode_plan_request_v0";
const RESULT_SCHEMA = "loopx_legacy_handoff_mode_plan_result_v0";

/** Edit only the generated scalar field. All other bytes, including CRLF and
 * Unicode line separators inside quoted metadata, remain untouched. A duplicate
 * key has no single writable owner, so do not silently patch only one copy. */
export function patchHandoffMode(text: string, requested: string): string {
  const opening = /^---[ \t]*\r?\n/.exec(text)?.[0];
  if (!opening) throw new Error("state_frontmatter_missing");
  const fields: {start: number; end: number; ending: string}[] = [];
  let close: number | undefined;
  // JavaScript multiline ^ also recognizes U+2028/U+2029. The generated
  // metadata protocol uses physical LF only, including inside JSON strings.
  for (let start = opening.length; start <= text.length;) {
    const newline = text.indexOf("\n", start);
    const end = newline < 0 ? text.length : newline + 1;
    const raw = text.slice(start, end);
    const line = raw.replace(/\r?\n$/, "");
    if (/^---[ \t]*$/.test(line)) { close = start; break; }
    if (/^[ \t]*handoff_mode[ \t]*:/.test(line)) {
      fields.push({start, end, ending: /\r?\n$/.exec(raw)?.[0] ?? ""});
    }
    if (newline < 0) break;
    start = end;
  }
  if (close === undefined) throw new Error("state_frontmatter_missing");
  if (fields.length > 1) throw new Error("handoff_mode_duplicate_field");
  const field = fields[0];
  if (field) return text.slice(0, field.start) + `handoff_mode: ${requested}${field.ending}` + text.slice(field.end);
  const newline = opening.endsWith("\r\n") ? "\r\n" : "\n";
  return text.slice(0, close) + `handoff_mode: ${requested}${newline}` + text.slice(close);
}

export function planLegacyHandoffMode(value: unknown): JsonObject {
  const input = requireJsonObject(value, "legacy handoff mode plan");
  if (input.schema_version !== LEGACY_HANDOFF_PLAN_SCHEMA) throw new Error("legacy handoff plan schema mismatch");
  const requested = requireStringLiteral(input.requested_mode, HANDOFF_MODES, "requested_mode");
  if (typeof input.frontmatter_text !== "string") throw new Error("frontmatter_text must be a string");
  const previous = persistedHandoffMode(input.previous_value);
  const fields = {...previousModeFields(previous), handoff_mode: requested};
  if (requested === "legacy") return {schema_version: RESULT_SCHEMA,
    ...decideHandoffMode(previous, requested, 0, 0), ...fields};
  // An identical valid mode grants no new behavior. Preserve the compatibility
  // no-op even when leases exist or the legacy state has no frontmatter.
  if (previous.kind === "valid" && previous.value === requested) {
    return {schema_version: RESULT_SCHEMA, outcome: "no_change", code: "handoff_mode_unchanged",
      changed: false, ...fields};
  }
  if (input.todos === null && input.leases === null) return {schema_version: RESULT_SCHEMA,
    outcome: "snapshot_required", ...fields, changed: false};
  if (!Array.isArray(input.todos) || !Array.isArray(input.leases)) throw new Error("complete Todo and lease facts required");
  let facts;
  try {
    facts = handoffQuiescence(input.todos.map(row => requireJsonObject(row, "Todo")),
      input.leases.map(row => requireJsonObject(row, "lease")), String(input.observed_at));
  } catch (error) {
    return {schema_version: RESULT_SCHEMA, outcome: "rejected", code: "invalid_handoff_mode_authority",
      ...fields, changed: false, reason: String(error)};
  }
  const plan = decideHandoffMode(previous, requested, facts.claimed_todos.length, facts.active_leases.length);
  if (plan.outcome === "rejected") return {schema_version: RESULT_SCHEMA, ...plan, ...fields,
    changed: false, ...facts};
  try {
    return {schema_version: RESULT_SCHEMA, ...plan, ...fields, changed: true,
      next_frontmatter_text: patchHandoffMode(input.frontmatter_text, requested)};
  } catch (error) {
    if (!(error instanceof Error) || !["state_frontmatter_missing", "handoff_mode_duplicate_field"].includes(error.message)) throw error;
    return {schema_version: RESULT_SCHEMA, outcome: "rejected", code: error.message, ...fields, changed: false};
  }
}
