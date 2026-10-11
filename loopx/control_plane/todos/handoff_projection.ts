/** Existing handoff_note_v0 read semantics. No queue, grant or completion effect. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {compactPythonWhitespace, stripPythonWhitespace} from "../coordination/todo_agents.ts";

// This is the historical display credential exclusion, not a state classifier.
// Python's Unicode word boundaries and IGNORECASE include dotted/dotless I.
const CREDENTIAL = /(?<![\p{L}\p{N}_])(?:bearer +[^ ]+|authorization *:|(?:ak|sk|api[_-]?key|access[_-]?key(?:[_-]?id)?|secret(?:[_-]?key)?|token|password)(?![\p{L}\p{N}_]) *[:=] *[^ ]+)/iu;

function compact(value: unknown, limit: number): string | null {
  if (typeof value !== "string") throw new EffectRuntimeRequestError("handoff text fact must be a string");
  const text = compactPythonWhitespace(value);
  if (!text || CREDENTIAL.test(text.replace(/[\u0130\u0131]/gu, "i"))) return null;
  const points = Array.from(text);
  return points.length <= limit ? text : stripPythonWhitespace(points.slice(0, limit - 1).join("")) + "...";
}

function nested(source: JsonObject): JsonObject {
  const primary = requireJsonObject(source.handoff ?? {truthy: false, fields: null}, "handoff source");
  const secondary = requireJsonObject(source.handoff_note ?? {truthy: false, fields: null}, "handoff note source");
  const chosen = primary.truthy ? primary : secondary;
  return chosen.fields === null ? {} : requireJsonObject(chosen.fields, "handoff fields");
}

function first(source: JsonObject, keys: readonly string[], limit: number): string | null {
  const fields = nested(source), texts = requireJsonObject(source.texts ?? {}, "handoff text facts");
  for (const key of keys) {
    const value = Object.hasOwn(fields, key) ? fields[key] : texts[key] ?? "";
    const text = compact(value, limit);
    if (text) return text;
  }
  return null;
}

function project(source: JsonObject): JsonObject {
  const handoff = nested(source), texts = requireJsonObject(source.texts ?? {}, "handoff text facts");
  const meta = requireJsonObject(source.metadata ?? {}, "handoff normalized metadata");
  const hint = first(source, ["continuation_hint", "suggested_next_action", "note", "reason"], 280);
  const successors = (meta.successor_todo_ids ?? []) as string[], excluded = (meta.excluded_agents ?? []) as string[];
  if (!Array.isArray(successors) || !Array.isArray(excluded)) {
    throw new EffectRuntimeRequestError("handoff metadata lists must be arrays");
  }
  if (!Object.keys(handoff).length && !successors.length && !excluded.length && !meta.unblocks_todo_id && !meta.superseded_by) {
    return {note: null, continuation_hint: hint};
  }
  const nestedMeta = requireJsonObject(source.nested_metadata ?? {}, "handoff nested metadata");
  const primary = requireJsonObject(source.handoff ?? {truthy: false, fields: null}, "handoff source");
  const chosenMeta = requireJsonObject((primary.truthy ? nestedMeta.handoff : nestedMeta.handoff_note) ?? {}, "chosen handoff metadata");
  const refsFact = requireJsonObject(chosenMeta.evidence_refs ?? {}, "nested evidence references");
  const outerRefs = requireJsonObject(source.evidence_refs ?? {}, "evidence references");
  const rawRefs = (refsFact.truthy ? refsFact : outerRefs).values ?? [];
  if (!Array.isArray(rawRefs)) throw new EffectRuntimeRequestError("handoff evidence facts must be an array");
  const refs: string[] = [];
  for (const raw of rawRefs) {
    const ref = compact(raw, 180);
    if (ref && !refs.includes(ref)) refs.push(ref);
  }
  if (meta.todo_id && source.has_evidence) refs.push(`todo:${meta.todo_id}:evidence`);
  if (meta.todo_id && source.has_note) refs.push(`todo:${meta.todo_id}:note`);
  const kind = compact(texts.latest_event_kind ?? "", 80);
  if (kind && meta.todo_id) refs.push(`rollout_event:${kind}:${meta.todo_id}`);
  const decisions = [...(meta.required_decision_scopes ?? []) as JsonObject[]];
  const single = meta.decision_scope as JsonObject | null;
  if (single && !decisions.some(row => row.kind === single.kind && row.granularity === single.granularity && row.scope_key === single.scope_key)) {
    decisions.push(single);
  }
  const note: JsonObject = {
    schema_version: "handoff_note_v0", handoff_id: meta.todo_id
      ? `handoff_${String(meta.todo_id).slice(5)}` : source.legacy_id,
    todo_id: meta.todo_id, goal_id: compact(source.goal_id || texts.goal_id || "", 180),
    from_agent: chosenMeta.from_agent, to_agent: chosenMeta.to_agent || meta.claimed_by,
    intent: first(source, ["intent"], 80) || meta.action_kind || compact(texts.task_class ?? "", 80) || "continue",
    summary: first(source, ["summary", "note", "reason", "evidence", "title", "text"], 280),
    evidence_refs: refs.slice(0, 6), unresolved_decisions: decisions,
    blocked_on: first(source, ["blocked_on"], 180) || meta.resume_when ||
      (meta.unblocks_todo_id ? `todo:${meta.unblocks_todo_id}` : meta.superseded_by ? `todo:${meta.superseded_by}` : null),
    suggested_next_action: first(source, ["suggested_next_action", "title", "text"], 260),
    source: compact(source.source || texts.source || "", 120),
    successor_todo_ids: successors, unblocks_todo_id: meta.unblocks_todo_id, excluded_agents: excluded,
  };
  return {continuation_hint: hint, note: Object.fromEntries(Object.entries(note).filter(([, value]) =>
    value !== null && value !== undefined && value !== "" && (!Array.isArray(value) || value.length > 0) &&
    (typeof value !== "object" || Array.isArray(value) || Object.keys(value).length > 0)))};
}

/** Co-deployed transport over common normalized metadata, ordered one-to-one. */
export function projectTodoHandoffContext(value: unknown, followups?: unknown): JsonObject[] {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError("handoff sources must be an array");
  const sources = value.map(row => requireJsonObject(row, "handoff context source"));
  if (followups === undefined) return sources.map(project);
  if (!Array.isArray(followups) || followups.length !== sources.length) {
    throw new EffectRuntimeRequestError("handoff followup cardinality mismatch");
  }
  return sources.map((source, index) => {
    const result = project(source);
    if (followups[index] === null) return result;
    const next = requireJsonObject(followups[index], "handoff followup source");
    if (result.note) {
      const note = requireJsonObject(result.note, "handoff read model");
      const meta = requireJsonObject(next.nested_metadata ?? {}, "handoff nested metadata");
      const previous = requireJsonObject(meta.handoff_note ?? {}, "handoff note metadata");
      // Index presentation first compacts the source, then labels it. Preserve
      // that sequence inside this one batch, before historical audit merging.
      return project({...next, handoff_note: {truthy: true, fields: note},
        nested_metadata: {...meta, handoff_note: {...previous,
          from_agent: note.from_agent || previous.from_agent, to_agent: note.to_agent,
          evidence_refs: {truthy: Array.isArray(note.evidence_refs) && note.evidence_refs.length > 0,
            values: note.evidence_refs ?? []}}}});
    }
    return project(next);
  });
}
