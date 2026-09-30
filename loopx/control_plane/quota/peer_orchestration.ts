/** Peer activation admission only; native children and bound delegation have separate gates. */
import type { JsonObject } from "../effect_program.ts";
import { jsonObject, requireJsonObject } from "../runtime_decode.ts";

type ActivationState = "ready" | "blocked";
// Admission is an allow-list. `executing` is backed by a live Turn lane or
// delegation worker; `bound`/`launchable` are durable work without a process.
// `unknown` (holder on another host, unreadable record, expired lease with
// nothing live) and every unlisted or missing state fail closed as not active.
const activeStates = new Set(["running", "monitoring", "executing", "bound", "launchable"]);
const rows = (value: unknown): JsonObject[] => Array.isArray(value)
  ? value.flatMap(item => { const row = jsonObject(item); return row ? [row] : []; }) : [];

export function projectPeerOrchestration(value: unknown): JsonObject | null {
  const input = requireJsonObject(value, "peer orchestration");
  const coordinator = String(input.agent_id ?? "");
  const registered = new Set(Array.isArray(input.registered_agents) ? input.registered_agents : []);
  const activation = Array.isArray(input.available_capabilities)
    && input.available_capabilities.includes("peer_agent_activation");
  const runtime = new Map(rows(input.agents).map(row => [row.agent_id, row]));
  // A peer can own several open tasks. Never let the first display row hide
  // another canonical task, or describe a claimed candidate as a session binding.
  const candidates = rows(input.items).filter(row => row.done !== true
    && ["", "open"].includes(String(row.status ?? "").trim().toLowerCase())
    && row.task_class === "advancement_task" && row.claimed_by
    && row.claimed_by !== coordinator && registered.has(row.claimed_by)
    && typeof row.todo_id === "string" && row.todo_id.length > 0);
  const unique = new Map(candidates.map(row => [JSON.stringify([row.claimed_by, row.todo_id]), row]));
  const eligible: JsonObject[] = [], blocked: JsonObject[] = [];
  for (const row of [...unique.values()].sort((a, b) =>
    String(a.claimed_by).localeCompare(String(b.claimed_by))
    || String(a.todo_id).localeCompare(String(b.todo_id)))) {
    const lane: JsonObject = {
      agent_id: row.claimed_by, todo_id: row.todo_id,
      priority: row.priority ?? null, task_class: row.task_class,
      action_kind: row.action_kind ?? null,
      title: String(row.title ?? row.text ?? "").trim(),
      resume_when: row.resume_when ?? null, resume_ready: row.resume_ready ?? null,
    };
    const reasons: string[] = [];
    if (!activation) reasons.push("peer_agent_activation_unavailable");
    const peer = runtime.get(row.claimed_by);
    if (!peer) reasons.push("peer_liveness_unavailable");
    else if (peer.stale_claim_hint) reasons.push("peer_runtime_stale");
    else if (!activeStates.has(String(peer.state))) reasons.push("peer_runtime_not_active");
    if (lane.resume_when && lane.resume_ready !== true) reasons.push("peer_lane_not_resume_ready");
    if (reasons.length) blocked.push({ ...lane, reason_codes: reasons });
    else eligible.push(lane);
  }
  if (!eligible.length && !blocked.length) return null;
  const state: ActivationState = eligible.length ? "ready" : "blocked";
  return {
    schema_version: "task_orchestration_contract_v1", mode: "task_scoped_peer",
    coordinator_agent_id: coordinator,
    execution_scope: "peer_agent_activation", task_selection: "canonical_claimed_candidates",
    execution_state: state, activation_required: eligible.length > 0,
    activation_allowed: activation, required_capability: "peer_agent_activation",
    eligible_peer_lanes: eligible, blocked_peer_lanes: blocked,
    retry_policy: "material_peer_state_change_only",
    terminal_outcome: state === "blocked" ? "blocked" : null,
    writeback_owner: "task_coordinator",
    coordinator_obligation: eligible.length
      ? "Activate or resume eligible peer tasks; multiple tasks may share a peer. Review returned evidence before writeback."
      : "Peer activation is blocked for these tasks; retry this entrypoint only after its gates change. This does not assess native children or bound delegation; inspect their own admission before use.",
  };
}
