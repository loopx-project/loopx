import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, requireStringLiteral } from "../runtime_decode.ts";

type Observation =
  | Readonly<{ state: "idle" | "turn_open" | "archived" }>
  | Readonly<{ state: "unavailable"; reason: string }>;

function observation(value: unknown): Observation {
  const row = requireJsonObject(value, "peer host observation");
  const state = requireStringLiteral(row.state,
    ["idle", "turn_open", "archived", "unavailable"], "peer host observation state");
  return state === "unavailable" ? { state, reason: requireStringLiteral(row.reason,
    ["host_observer_unavailable", "host_observation_failed", "thread_not_found",
      "store_unavailable", "record_unrecognized", "no_turn_marker", "unsupported_host",
      "route_candidate_withheld"], "unavailable peer host reason") } : { state };
}

/** Choose a locator, not an executor or permission grant. Unknown is never retired. */
export function selectObservedPeerHostRoute(params: JsonObject): JsonObject {
  if (!Array.isArray(params.observations) || !params.observations.length
      || params.observations.length > 32) {
    throw new EffectRuntimeRequestError("peer host observations must contain 1..32 candidates");
  }
  const rows = params.observations.map(observation);
  const remaining = rows.flatMap((row, index) => row.state === "archived" ? [] : [index]);
  if (remaining.length > 1) {
    return { status: "ambiguous", reason: "multiple_binding_candidates", selected_index: null };
  }
  if (!remaining.length) {
    return { status: "unavailable", reason: "host_thread_archived", selected_index: null };
  }
  const index = remaining[0];
  const row = rows[index];
  return row.state === "unavailable"
    ? { status: "unavailable", reason: row.reason, selected_index: null }
    : { status: "resolved", reason: null, selected_index: index };
}
