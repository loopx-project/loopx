/** Host-recorded identity is an observation, never authentication or a grant. */
import {createHash} from "node:crypto";
import {open, realpath} from "node:fs/promises";
import {isAbsolute, relative} from "node:path";

type UnknownIdentity = {status: "unavailable"; reason: string};
type RecordedIdentity = {status: "runtime_reported"; model: string; provider: string;
  reasoning_effort: string | null; observation_id: string; active_turn_verified: boolean};
export type ExecutionIdentity = UnknownIdentity | RecordedIdentity;
const unavailable = (reason: string): UnknownIdentity => ({status: "unavailable", reason});
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const providers = new Map<string, string>([["openai", "OpenAI"], ["anthropic", "Anthropic"], ["google", "Google"]]);
// Only a provider's ordinary model name is publishable. Endpoint/router labels
// remain unavailable; do not guess a provider from a model prefix or configuration.
const publicModel = (value: unknown): value is string => typeof value === "string"
  && /^[A-Za-z0-9][A-Za-z0-9 ._-]{0,95}$/.test(value);

/** Read its latest recorded Turn identity, without claiming execution/authority. */
export async function readCodexExecutionIdentity(params: Record<string, unknown>): Promise<ExecutionIdentity> {
  const {home, path, thread_id: thread} = params;
  if (typeof home !== "string" || typeof path !== "string" || typeof thread !== "string" || !thread)
    return unavailable("session_not_bound");
  try {
    const root = await realpath(home), source = await realpath(path);
    const child = relative(root, source);
    if (!child || child === ".." || child.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) || isAbsolute(child))
      return unavailable("source_outside_home");
    const file = await open(source, "r");
    try {
      const start = await file.stat();
      // The opening record contains instructions/tools, so frame it as a full
      // bounded line. None of that content is retained or returned.
      const header = Buffer.alloc(Math.min(start.size, 2 * 1024 * 1024));
      const read = await file.read(header, 0, header.length, 0);
      const opening = header.subarray(0, read.bytesRead), lf = opening.indexOf(10);
      if (lf < 0) return unavailable("session_header_incomplete_or_oversized");
      const meta: unknown = JSON.parse(opening.subarray(0, lf).toString("utf8"));
      if (!object(meta) || meta.type !== "session_meta" || !object(meta.payload)
        || (meta.payload.id ?? meta.payload.session_id) !== thread) return unavailable("session_identity_mismatch");
      const sessionProvider = meta.payload.model_provider;
      // Freeze the extent. Discard a trailing incomplete line and the first
      // partial line at a nonzero offset; never splice content into metadata.
      const offset = Math.max(0, start.size - 8 * 1024 * 1024);
      const tail = Buffer.alloc(start.size - offset);
      const count = await file.read(tail, 0, tail.length, offset);
      if (count.bytesRead !== tail.length) return unavailable("source_changed_during_read");
      let lines = tail.toString("utf8").split("\n");
      lines.pop();
      if (offset) lines.shift();
      let context: Record<string, unknown> | undefined;
      const project = (active_turn_verified: boolean): ExecutionIdentity => {
        if (!context || typeof context.turn_id !== "string" || !context.turn_id)
          return unavailable("turn_identity_unavailable");
        const providerId = context.model_provider ?? sessionProvider;
        const provider = typeof providerId === "string" ? providers.get(providerId) : undefined;
        if (!provider) return unavailable("provider_not_publicly_identified");
        if (!publicModel(context.model)) return unavailable("model_not_publicly_identified");
        const effort = context.effort ?? context.reasoning_effort ?? null;
        if (effort !== null && (typeof effort !== "string" || !/^[a-z]{1,16}$/.test(effort)))
          return unavailable("effort_unrecognized");
        const observation_id = createHash("sha256").update(JSON.stringify([
          thread, context.turn_id, context.model, provider, effort,
        ])).digest("hex");
        return {status: "runtime_reported", model: context.model, provider,
          reasoning_effort: effort, observation_id, active_turn_verified};
      };
      for (const line of lines.reverse()) {
        if (!line.trim()) continue;
        // Parse only metadata envelopes, never project conversational content.
        const record: unknown = JSON.parse(line);
        if (!object(record) || !object(record.payload)) continue;
        const payload = record.payload;
        if (record.type === "turn_context" && !context) context = payload;
        if (record.type !== "event_msg") continue;
        if (["task_complete", "task_completed", "turn_aborted"].includes(String(payload.type)))
          return unavailable("no_active_turn");
        if (payload.type !== "task_started") continue;
        if (!context || typeof payload.turn_id !== "string" || !payload.turn_id
          || context.turn_id !== payload.turn_id) return unavailable("turn_identity_unavailable");
        const end = await file.stat();
        if (end.ino !== start.ino || end.size < start.size) return unavailable("source_changed_during_read");
        return project(true);
      }
      // Long Turns can push their opening event outside this bounded read.
      // The latest context still records its model, but it cannot prove liveness.
      return project(false);
    } finally { await file.close(); }
  } catch { return unavailable("host_record_unavailable"); }
}

/** A saved declaration may not impersonate observed execution or a prior Turn. */
export function matchExecutionDeclaration(params: Record<string, unknown>): {errors: string[]} {
  const {observation, declaration, current} = params;
  const errors: string[] = [];
  if (!object(declaration) || declaration.actor_kind !== "model_agent") return {errors};
  const effective = object(current) && current.status === "runtime_reported" ? current : observation;
  if (!object(effective) || effective.status !== "runtime_reported") {
    if (declaration.declaration_source === "runtime_reported") errors.push("runtime_observation_missing");
    return {errors};
  }
  if (!publicModel(effective.model) || ![...providers.values()].includes(String(effective.provider))
    || typeof effective.observation_id !== "string" || !/^[a-f0-9]{64}$/.test(effective.observation_id)
    || !(effective.reasoning_effort === null || typeof effective.reasoning_effort === "string"
      && /^[a-z]{1,16}$/.test(effective.reasoning_effort))) return {errors: ["runtime_observation_invalid"]};
  if (declaration.declaration_source !== "runtime_reported") errors.push("runtime_observation_requires_runtime_declaration");
  for (const [declared, observed] of [["declared_model", "model"], ["declared_provider", "provider"],
    ["declared_reasoning_effort", "reasoning_effort"], ["execution_observation_id", "observation_id"]]) {
    if (declaration[declared] !== effective[observed]) errors.push(`runtime_mismatch:${declared}`);
  }
  // CLI checks reread the active host; pure/offline checking has no new host I/O.
  if (object(observation) && observation.status === "runtime_reported" && current !== undefined
    && (!object(current) || current.status !== "runtime_reported"
      || ["observation_id", "model", "provider", "reasoning_effort"].some(key => current[key] !== observation[key])))
    errors.push("runtime_observation_changed");
  return {errors};
}
