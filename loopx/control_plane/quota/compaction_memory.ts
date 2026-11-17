/** Source-bound advisory working memory. Never edits work, reads or continuity. */
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";

export const COMPACTION_MEMORY_SCHEMA = "loopx_compaction_memory_v0";
const MAX_VIEW_BYTES = 12000;
const text = (value: unknown, bytes: number): string | undefined =>
  typeof value === "string" && value.trim() && Buffer.byteLength(value) <= bytes ? value : undefined;
const pointer = (path: (string | number)[]) => "/" + path.map(part => String(part).replaceAll("~", "~0").replaceAll("/", "~1")).join("/");

export function prepareCompactionMemory(request: JsonObject): JsonObject {
  const context = requireJsonObject(request.context, "memory source");
  const scope = requireJsonObject(request.scope, "memory scope");
  const binding: JsonObject = {};
  for (const key of ["goal_id", "agent_id", "session_id", "profile"])
    binding[key] = requireNonEmptyString(scope[key], `memory ${key}`);
  if (scope.turn_instance_id !== undefined)
    binding.turn_instance_id = requireNonEmptyString(scope.turn_instance_id, "original memory Turn");
  if (context.goal_id !== undefined && context.goal_id !== binding.goal_id)
    throw new Error("memory source belongs to a different Goal");
  const sourceAgent = context.agent_id ?? jsonObject(context.agent_identity)?.agent_id;
  if (sourceAgent !== undefined && sourceAgent !== binding.agent_id)
    throw new Error("memory source belongs to a different Agent");
  const source = requireJsonObject(request.source, "memory source reference");
  const recovery = requireJsonObject(request.recovery, "original recovery reference");
  const reference = (value: JsonObject): JsonObject => {
    const sha256 = requireNonEmptyString(value.sha256, "memory source digest");
    if (!/^[a-f0-9]{64}$/.test(sha256)) throw new Error("invalid memory source digest");
    return {path: requireNonEmptyString(value.path, "memory source path"), sha256};
  };
  const document: JsonObject = {
    schema_version: COMPACTION_MEMORY_SCHEMA, binding,
    source: reference(source), recovery: reference(recovery),
    coverage: [], frontier: [], needed_reads: [],
  };
  const coverage = document.coverage as string[];
  const frontier = document.frontier as JsonObject[];
  const reads = document.needed_reads as string[];
  let selected = jsonObject(context.selected_todo);
  let selectedPath: (string | number)[] = ["selected_todo"];
  const agent = jsonObject(jsonObject(context.interaction_contract)?.agent_channel);
  const work = jsonObject(agent?.work_context);
  const sources = Array.isArray(work?.sources) ? work.sources : [];
  for (const [index, value] of sources.entries()) {
    const row = jsonObject(value), content = jsonObject(row?.content);
    if (!row || !content || content.ok === false || content.read_complete === false) continue;
    const path = ["interaction_contract", "agent_channel", "work_context", "sources", index];
    if (["goal_state", "goal_acceptance"].includes(String(row.source))) reads.push(pointer(path));
    if (row.source === "selected_todo") {
      const todo = jsonObject(content.todo);
      if (todo && typeof todo.todo_id === "string" && todo.todo_id === selected?.todo_id) {
        selected = todo;
        selectedPath = [...path, "content", "todo"];
      }
    }
  }
  if (typeof selected?.todo_id === "string" && selected.todo_id) {
    const focus: JsonObject = {todo_id: selected.todo_id, ref: pointer(selectedPath)};
    const title = text(selected.title, 512) ?? text(selected.text, 512);
    if (title) {
      focus.title = title;
      coverage.push(pointer([...selectedPath, selected.title === title ? "title" : "text"]));
    }
    document.focus = focus;
    if (typeof selected.note === "string" && selected.note.trim()) {
      const ref = pointer([...selectedPath, "note"]), note = text(selected.note, 1024);
      frontier.push({ref, ...(note ? {note} : {})});
      if (note) coverage.push(ref);
    }
  }
  if (Array.isArray(agent?.required_reads) && agent.required_reads.length)
    reads.push("/interaction_contract/agent_channel/required_reads");
  // The complete capture remains the source for unknown or omitted material.
  // No note, missing field or empty navigation clears the original read contract.
  if (!document.focus && !reads.length) reads.push("");
  if (Buffer.byteLength(JSON.stringify(document)) > MAX_VIEW_BYTES)
    return {available: false};
  return {available: true, revision: canonicalAuthoritySha256(document), document};
}
