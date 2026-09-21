/** Complete advancement frontier identity and long-chain checkpoint policy.
 * Python supplies normalized legacy facts and the exact v0 serialization codec;
 * selection, completeness, hashing, thresholds and ACK authority live here.
 */
import { createHash } from "node:crypto";
import {inflateSync} from "node:zlib";
import type { JsonObject } from "../effect_program.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { parseTodoTimestampMicros } from "../runtime_timestamp.ts";
import { normalizeTodoAgent, stripPythonWhitespace } from "../coordination/todo_agents.ts";
import { AuthorityStoreProtocolError } from "../coordination/authority_store_codec.ts";

const REVISION = "todo_frontier_revision_v0";
const INDEX = "todo_frontier_revision_index_v0";
const TRIGGER = "long_todo_chain";
type Checkpoint = { complete: false } | {
  complete: true; frontier_revision: string; frontier_updated_at: string;
  frontier_owned_identity: string | null;
};
type Row = {
  id: string; claim: string | null; excluded: string[];
  updated: string; serialized: string; advancement: boolean;
};
type LongChainObservation = {
  trigger_count: number;
  count_kind: "selectable_advancement_todos" | "selectable_open_todos" |
    "claimed_advancement_todos" | "claimed_open_todos";
  selectable_open_count: number; selectable_advancement_count: number;
  current_agent_claimed_open_count: number;
  current_agent_claimed_advancement_count: number; unclaimed_advancement_count: number;
  threshold: 15 | 20; agent_id: string | null;
  frontier_revision: string | null; frontier_revision_complete: boolean;
  frontier_owned_identity: string | null;
};
type AckDecision = {acknowledged: boolean; rearmed_after_obligation_id: string | null};
type SuccessorBinding = {kind: "exact"; todo_id: string} |
  {kind: "predecessor"; todo_id: string; frontier_revision: string; obligation_identity_revision: string};
type TriggerCheckpoint = {
  kind: string; frontier_revision: string; frontier_owned_identity?: string;
};
const object = (value: unknown): JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
const text = (value: unknown): string => typeof value === "string" ? stripPythonWhitespace(value) : "";
function agentId(value: unknown): string | null {
  try { return normalizeTodoAgent(value, "agent_id"); }
  catch (error) {
    if (error instanceof AuthorityStoreProtocolError) return null;
    throw error;
  }
}
const strings = (value: unknown): string[] => Array.isArray(value)
  ? value.filter((item): item is string => typeof item === "string").map(item => item.trim()).filter(Boolean) : [];
const count = (value: unknown): number => {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? Math.max(0, Math.trunc(number)) : 0;
};

/** One receipt shape for observation, successor and semantic-writeback paths.
 * Historical revision-only checkpoints remain valid. An owned identity cannot
 * stand alone or confer the long-chain matching rule on another trigger kind.
 */
function triggerCheckpoint(value: unknown): TriggerCheckpoint | null {
  const row = object(value), kind = text(row.kind), revision = text(row.frontier_revision);
  if (!kind || !revision || row.frontier_revision_complete === false) return null;
  const owned = kind === TRIGGER ? text(row.frontier_owned_identity) : "";
  return {kind, frontier_revision: revision, ...(owned ? {frontier_owned_identity: owned} : {})};
}

function triggerCheckpoints(value: unknown): TriggerCheckpoint[] {
  return (Array.isArray(value) ? value : []).map(triggerCheckpoint).filter(row => row !== null);
}

function decodeRows(value: unknown): Row[] | null {
  if (value == null) return null;
  if (!Array.isArray(value)) {
    const encoded = requireJsonObject(value, "frontier rows transport");
    if (encoded.encoding !== "deflate-base64-json-v0" || typeof encoded.data !== "string" ||
        encoded.data.length > 2 * 1024 * 1024 || !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded.data)) {
      throw new EffectRuntimeRequestError("invalid frontier rows transport");
    }
    try {
      value = JSON.parse(inflateSync(Buffer.from(encoded.data, "base64"), {
        maxOutputLength: 64 * 1024 * 1024,
      }).toString("utf8"));
    } catch {
      throw new EffectRuntimeRequestError("frontier rows must be valid compressed JSON within 64 MiB");
    }
  }
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError("frontier source must be an array");
  return value.map(raw => {
    const row = requireJsonObject(raw, "frontier row");
    if (typeof row.serialized !== "string" || typeof row.advancement !== "boolean") {
      throw new EffectRuntimeRequestError("frontier row codec facts are missing");
    }
    return {id: text(row.id), claim: text(row.claim) || null,
      excluded: strings(row.excluded), updated: text(row.updated),
      serialized: row.serialized, advancement: row.advancement};
  });
}

function checkpoint(rows: Row[] | null, agent: string | null, unclaimedOnly = false): Checkpoint {
  if (rows === null) return {complete: false};
  const selected = rows.filter(row => row.advancement &&
    (!unclaimedOnly || row.claim === null) &&
    (!agent || ((row.claim === null || row.claim === agent) && !row.excluded.includes(agent))));
  if (selected.length === 0) return {complete: false};
  const ids = new Set<string>();
  let latest: bigint | null = null;
  let updated = "";
  for (const row of selected) {
    const instant = parseTodoTimestampMicros(row.updated);
    if (!row.id || ids.has(row.id) || instant === null) return {complete: false};
    ids.add(row.id);
    if (latest === null || instant > latest) { latest = instant; updated = row.updated; }
  }
  selected.sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const digest = createHash("sha256").update(`[${selected.map(row => row.serialized).join(",")}]`).digest("hex");
  // The selectable set above also contains rows nobody has claimed yet, so any
  // other lane that claims or edits one of them moves the revision. That is a
  // real change to the measured chain but not to this agent's own work basis,
  // so the ACK fence also carries an identity over the rows this agent owns.
  const owned = agent === null ? [] : selected.filter(row => row.claim === agent);
  const ownedDigest = owned.length === 0 ? null : createHash("sha256")
    .update(`[${owned.map(row => row.serialized).join(",")}]`).digest("hex").slice(0, 24);
  return {complete: true, frontier_revision: `${REVISION}:${digest.slice(0, 24)}`,
    frontier_updated_at: updated,
    frontier_owned_identity: ownedDigest === null ? null : `${REVISION}:owned:${ownedDigest}`};
}

function readIndex(value: unknown, agent: string | null): Checkpoint | null {
  if (value === null || value === undefined || typeof value !== "object" || Array.isArray(value)) return null;
  const index = object(value);
  if (index.schema_version !== INDEX) return {complete: false};
  let raw = index.all;
  if (agent) {
    if (!Array.isArray(index.by_agent)) return {complete: false};
    const matches = index.by_agent.filter(row => agentId(object(row).agent_id) === agent);
    if (matches.length > 1) return {complete: false};
    raw = matches[0] ?? index.unclaimed;
  }
  const entry = object(raw);
  const revision = text(entry.frontier_revision), updated = text(entry.frontier_updated_at);
  if (entry.complete !== true || !revision || parseTodoTimestampMicros(updated) === null) return {complete: false};
  return {complete: true, frontier_revision: revision, frontier_updated_at: updated,
    frontier_owned_identity: text(entry.frontier_owned_identity) || null};
}

function successorCheckpoints(request: JsonObject, agent: string | null): JsonObject | null {
  const indexed = readIndex(request.index, agent);
  const rows = decodeRows(request.rows);
  const source = indexed ?? checkpoint(rows, agent);
  if (!source.complete) return null;
  const latest = parseTodoTimestampMicros(source.frontier_updated_at)!;
  const candidates = (Array.isArray(request.candidates) ? request.candidates : []).map(object)
    .filter(row => {
      const updated = parseTodoTimestampMicros(text(row.updated_at));
      return text(row.todo_id) && updated !== null && updated >= latest;
    });
  const bindings: SuccessorBinding[] = candidates.filter(row => row.origin_obligation_id === request.obligation_id)
    .map(row => ({kind: "exact", todo_id: text(row.todo_id)}));
  const triggers = Array.isArray(request.triggers) ? request.triggers : [];
  const trigger = object(triggers[0]);
  // A new successor changes the revision it was created to settle. Reconstruct
  // only a unique fresh insertion, using a complete source matching the index.
  // The existing obligation-id owner still verifies the predecessor revision.
  const priorAdvancement = count(agent === null ? trigger.selectable_advancement_count
    : trigger.current_agent_claimed_advancement_count) - 1;
  const priorOpen = count(agent === null ? trigger.selectable_open_count
    : trigger.current_agent_claimed_open_count) - 1;
  if (bindings.length === 0 && candidates.length === 1 && triggers.length === 1 &&
      trigger.kind === TRIGGER && trigger.frontier_revision === source.frontier_revision && (priorAdvancement >= 15 || priorOpen >= 20 && priorAdvancement > 0)) {
    const completeSource = indexed === null ? source : checkpoint(rows, agent);
    if (completeSource.complete && completeSource.frontier_revision === source.frontier_revision) {
      const candidate = candidates[0];
      const prior = checkpoint(rows === null ? null : rows.filter(row => row.id !== candidate.todo_id), agent);
      if (prior.complete && prior.frontier_revision !== source.frontier_revision) {
        bindings.push({kind: "predecessor", todo_id: text(candidate.todo_id), frontier_revision: prior.frontier_revision,
          obligation_identity_revision: prior.frontier_owned_identity ?? prior.frontier_revision});
      }
    }
  }
  return {trigger_checkpoints: [
    ...triggerCheckpoints(request.triggers).filter(row => row.kind !== TRIGGER),
    triggerCheckpoint({kind: TRIGGER, ...source}),
  ], bindings};
}

export function projectAdvancementFrontier(value: unknown): JsonObject {
  const request = requireJsonObject(value, "frontier revision request");
  if (request.schema_version !== "todo_frontier_revision_request_v0") throw new EffectRuntimeRequestError("frontier revision schema mismatch");
  const agent = agentId(request.agent_id);
  if (request.operation === "trigger_checkpoints") {
    return {trigger_checkpoints: triggerCheckpoints(request.triggers)};
  }
  if (request.operation === "successor_checkpoints") {
    return {source_checkpoint: successorCheckpoints(request, agent)};
  }
  if (request.operation === "read") return {checkpoint: readIndex(request.index, agent)};
  const rows = decodeRows(request.rows);
  if (request.operation === "select") return {checkpoint: checkpoint(rows, agent)};
  if (request.operation !== "index") throw new EffectRuntimeRequestError("unsupported frontier revision operation");
  // An excluded agent can have no claimed rows. It still needs its own lane;
  // falling back to the global unclaimed checkpoint would include excluded work.
  const agents = [...new Set((rows ?? []).filter(row => row.advancement)
    .flatMap(row => [...(row.claim ? [row.claim] : []), ...row.excluded]))].sort();
  return {index: {schema_version: INDEX, all: checkpoint(rows, null),
    unclaimed: checkpoint(rows, null, true),
    by_agent: agents.map(agent_id => ({agent_id, ...checkpoint(rows, agent_id)}))}};
}

function classifyAck(observation: LongChainObservation, value: unknown): AckDecision {
  const ack = object(value), delta = object(ack.semantic_delta);
  const id = text(delta.obligation_id);
  const rejected = {acknowledged: false, rearmed_after_obligation_id: null};
  if (ack.recorded !== true || delta.accepted !== true ||
      !strings(delta.trigger_kinds).includes(TRIGGER) || !/^replan-[a-f0-9]{16}$/.test(id) ||
      observation.frontier_revision_complete !== true || !text(observation.frontier_revision)) return rejected;
  const matches = Array.isArray(delta.trigger_checkpoints) && delta.trigger_checkpoints.some(raw => {
    const row = triggerCheckpoint(raw);
    if (row === null || row.kind !== TRIGGER) return false;
    if (row.frontier_revision === observation.frontier_revision) return true;
    // Another lane claiming or editing an unclaimed row moves the revision but
    // leaves this agent's own selectable rows untouched; that is not new
    // evidence about this agent's chain, so it must not re-arm the obligation.
    const recorded = text(row.frontier_owned_identity);
    return Boolean(recorded) && recorded === text(observation.frontier_owned_identity);
  });
  return {acknowledged: matches, rearmed_after_obligation_id: matches ? null : id};
}

export function evaluateLongTodoChain(value: unknown): JsonObject {
  const request = requireJsonObject(value, "long chain request");
  if (request.schema_version !== "long_todo_chain_request_v0") throw new EffectRuntimeRequestError("long chain schema mismatch");
  if (request.operation !== "observe") throw new EffectRuntimeRequestError("unsupported long chain operation");
  const summary = object(request.summary), frontier = object(request.frontier_counts);
  const agent = agentId(request.agent_id);
  const current = count(frontier.current_agent_claimed_advancement_count);
  const unclaimed = count(frontier.unclaimed_advancement_count);
  const advancement = current + unclaimed;
  const open = Math.max(advancement, request.summary == null ? count(object(request.agent_counts).open) :
    count(summary.current_agent_claimed_open_count) + count(summary.unclaimed_open_count));
  const claimedOpen = Math.max(current, count(summary.current_agent_claimed_open_count));
  // A lane replans commitments it owns. Shared candidates remain selectable,
  // but must not impose a chain obligation with no owned ACK fence.
  const measuredAdvancement = agent === null ? advancement : current;
  const measuredOpen = agent === null ? open : claimedOpen;
  const threshold = measuredAdvancement >= 15 ? 15 : measuredOpen >= 20 && measuredAdvancement > 0 ? 20 : null;
  if (threshold === null) return {observation: null, decision: null};
  const revision = readIndex(summary.advancement_frontier_revision_index, agent)
    ?? checkpoint(decodeRows(request.rows), agent);
  const observation: LongChainObservation = {trigger_count: threshold === 15 ? measuredAdvancement : measuredOpen,
    count_kind: agent === null
      ? threshold === 15 ? "selectable_advancement_todos" : "selectable_open_todos"
      : threshold === 15 ? "claimed_advancement_todos" : "claimed_open_todos",
    selectable_open_count: open, selectable_advancement_count: advancement,
    current_agent_claimed_open_count: claimedOpen,
    current_agent_claimed_advancement_count: current, unclaimed_advancement_count: unclaimed,
    threshold, agent_id: agent, frontier_revision: revision.complete ? revision.frontier_revision : null,
    frontier_revision_complete: revision.complete,
    frontier_owned_identity: revision.complete ? revision.frontier_owned_identity : null};
  const {frontier_revision, frontier_revision_complete, frontier_owned_identity, ...counts} = observation;
  const receipt = triggerCheckpoint({kind: TRIGGER, frontier_revision,
    frontier_revision_complete, frontier_owned_identity});
  return {observation: {...observation, trigger: {...counts, ...receipt,
    ...(revision.complete ? {obligation_identity_revision:
      revision.frontier_owned_identity ?? revision.frontier_revision} : {})}},
    decision: classifyAck(observation, request.ack)};
}
