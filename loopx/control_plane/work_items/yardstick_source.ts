/** Server-owned canonical event-prefix capture and recovery. Browser readers
 * share the pure decision/capture normalizers without importing Node hashing. */
import {createHash} from "node:crypto";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, type JsonObject} from "../runtime_decode.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {currentWorkYardstickFinding, normalizeWorkYardstickCapture,
  normalizeWorkYardstickDecision, normalizeWorkYardstickSourceSnapshot,
  workYardstickTodos} from "./yardstick_decision.ts";

/** The adapter supplies strictly validated canonical events, never attachment fields.
 * Event ids bind the complete event contents through the existing log validator. */
function committedSourceSnapshot(params: JsonObject, binding: JsonObject) {
  if (!Array.isArray(params.source_events)) return undefined;
  const events = params.source_events as JsonObject[];
  if (events.some(row => row.goal_id !== binding.goal_id || typeof row.event_id !== "string")) {
    throw new EffectRuntimeRequestError("yardstick source snapshot cannot cross its canonical Goal");
  }
  // Old committed payloads have no recorded prefix. Recovery cannot invent one.
  if (params.source_snapshot === null) return undefined;
  const recorded = params.source_snapshot === undefined ? undefined : normalizeWorkYardstickSourceSnapshot(params.source_snapshot);
  const count = recorded?.event_count ?? events.length;
  const digest = createHash("sha256").update(JSON.stringify(events.slice(0, count).map(row => row.event_id))).digest("hex");
  if (count > events.length || recorded && digest !== recorded.event_ids_sha256) {
    throw new EffectRuntimeRequestError("yardstick source snapshot changed; retain the original evidence before retrying");
  }
  return {event_count: count, event_ids_sha256: digest};
}
/** Select only current captures; the transport replays their already-read log prefixes. */
export function projectWorkYardstickSourceRequests(params: JsonObject): JsonObject {
  const findings = (params.findings ?? []) as JsonObject[];
  const current = workYardstickTodos(params).map(todo => {
    const linked = new Set(todo.explore_result_node_refs as unknown[] ?? []);
    return currentWorkYardstickFinding(findings.filter(row => linked.has(row.node_id)), {...params, todo_id: todo.todo_id});
  }).filter((source): source is JsonObject => source !== undefined
    && (params.result_node == null || source.node_id === params.result_node)).slice(0, 3);
  return {sources: current.flatMap(source => {
    const capture = normalizeWorkYardstickCapture({capture: source.work_yardstick, evidence_refs: source.evidence_refs,
      goal_id: params.goal_id, agent_id: params.agent_id});
    if (!capture.source_snapshot || !capture.decision.counterevidence_refs.length) return [];
    committedSourceSnapshot({...params, source_snapshot: capture.source_snapshot}, capture.binding);
    return [{source_finding_id: source.finding_id, source_snapshot: capture.source_snapshot}];
  })};
}
export function validateWorkYardstickContinuation(params: JsonObject): JsonObject {
  if (params.enabled !== true) throw new EffectRuntimeRequestError("work yardstick capture requires explicit yardstick_enabled configuration");
  const binding = requireJsonObject(params.binding, "work binding");
  const decision = normalizeWorkYardstickDecision(params.decision, params.evidence_refs);
  const current = currentWorkYardstickFinding((params.findings ?? []) as JsonObject[], binding);
  // Only the settlement adapter supplies the original timestamp of an exact
  // committed replay. Preserve a strictly older missing capture as history;
  // it cannot displace the current choice or admit a newer stale predecessor.
  const replayTime = typeof params.replay_committed_at === "string"
    ? parseTodoTimestampMicros(params.replay_committed_at) : null;
  const currentTime = current ? parseTodoTimestampMicros(String(current.last_updated_at ?? "")) : null;
  const historicalReplay = replayTime !== null && currentTime !== null && replayTime < currentTime;
  if (decision.prior_finding_id !== (current?.finding_id ?? null) && !historicalReplay) {
    throw new EffectRuntimeRequestError("yardstick basis changed; read the current same-Todo decision before revising it");
  }
  const snapshot = committedSourceSnapshot(params, binding);
  return {decision, ...(snapshot === undefined ? {} : {source_snapshot: snapshot})};
}

