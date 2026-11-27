import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { normalizeTodoAgent } from "../coordination/todo_agents.ts";
import {
  optionalNonEmptyString,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
} from "../runtime_decode.ts";
import {
  decodeCallerLanes,
  decodePeerFrontiers,
  projectPeerCoordination,
  type CollaborationOpportunity,
  type CollaborationOpportunityKind,
} from "./peer_coordination.ts";
import { sharedGoalWorkFacts } from "./shared_goal_work.ts";

export const PEER_COORDINATION_DIGEST_REQUEST_SCHEMA_VERSION = "peer_coordination_digest_request_v0";
export const PEER_COORDINATION_DIGEST_PROJECTION_SCHEMA_VERSION = "peer_coordination_digest_projection_v0";
export const PEER_COORDINATION_DIGEST_SCHEMA_VERSION = "peer_digest_v0";
export const PEER_COORDINATION_DIGEST_MAX_BYTES = 300;
export const PEER_COORDINATION_DIGEST_MIN_BYTES = 128;
export const PEER_COORDINATION_DIGEST_FIELD_OVERHEAD_BYTES = Buffer.byteLength(
  ',"peer_coordination_digest":',
  "utf8",
);

const COMPACT_KIND: Partial<Record<CollaborationOpportunityKind, string>> = {
  shared_target_collision_risk: "collision",
  peer_verification_needed: "verify",
  peer_unleased_lane_attention: "unleased",
};

const GOAL_ID_SAFE_PATTERN = /^[^\\/\s]+$/;

function serializedDeltaBytes(digest: JsonObject): number {
  return PEER_COORDINATION_DIGEST_FIELD_OVERHEAD_BYTES + Buffer.byteLength(JSON.stringify(digest), "utf8");
}

function makeDigestEnvelope(items: readonly JsonObject[], omittedCount: number): JsonObject {
  const digest: JsonObject = {
    schema: PEER_COORDINATION_DIGEST_SCHEMA_VERSION,
    read_only: true,
    grants_authority: false,
    items: [...items],
  };
  if (omittedCount > 0) {
    digest.omitted = omittedCount;
  }
  return digest;
}

function compactDigestItem(
  opp: CollaborationOpportunity,
  compactKind: string,
  includeMine: boolean,
): JsonObject {
  const item: JsonObject = {
    id: opp.opportunity_id,
    kind: compactKind,
    peer: opp.peer_agent_id,
    todo: opp.peer_todo_id,
  };
  if (includeMine && opp.caller_todo_id !== null) {
    item.mine = opp.caller_todo_id;
  }
  return item;
}

/**
 * Project a conditional, default-on, cursor-deduplicated peer coordination digest
 * bounded strictly to <= max_bytes (default 300 B including serialized field overhead).
 *
 * Returns `digest: null` (0 B serialized delta) when:
 * - fewer than 2 registered agents share the Goal,
 * - no qualifying high-signal opportunities exist for the caller's state (busy vs. idle), or
 * - all qualifying opportunities were already surfaced in `seen_opportunity_ids`.
 */
export function projectPeerCoordinationDigest(value: unknown): JsonObject {
  const request = requireJsonObject(value, "peer_coordination_digest request");
  const schema = requireNonEmptyString(
    request.schema_version,
    "peer_coordination_digest.schema_version",
  );
  if (schema !== PEER_COORDINATION_DIGEST_REQUEST_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `unsupported peer_coordination_digest.schema_version: ${schema}`,
    );
  }
  const goalId = requireNonEmptyString(request.goal_id, "peer_coordination_digest.goal_id");
  if (!GOAL_ID_SAFE_PATTERN.test(goalId) || goalId.includes("..")) {
    throw new EffectRuntimeRequestError(
      "peer_coordination_digest.goal_id must be a safe single-segment goal id",
    );
  }
  let agentId: string;
  try {
    agentId = normalizeTodoAgent(request.agent_id, "peer_coordination_digest.agent_id");
  } catch {
    throw new EffectRuntimeRequestError(
      "peer_coordination_digest.agent_id must be a public-safe agent id",
    );
  }
  const registeredAgentCount = requireInteger(
    request.registered_agent_count,
    "peer_coordination_digest.registered_agent_count",
  );
  if (registeredAgentCount < 0) {
    throw new EffectRuntimeRequestError(
      "peer_coordination_digest.registered_agent_count must be non-negative",
    );
  }
  const seenOpportunityIds = request.seen_opportunity_ids === undefined
    ? []
    : requireStringArray(
      request.seen_opportunity_ids,
      "peer_coordination_digest.seen_opportunity_ids",
    );
  const rawMaxBytes = request.max_bytes === undefined
    ? PEER_COORDINATION_DIGEST_MAX_BYTES
    : requireInteger(request.max_bytes, "peer_coordination_digest.max_bytes");
  const maxBytes = Math.min(
    PEER_COORDINATION_DIGEST_MAX_BYTES,
    Math.max(PEER_COORDINATION_DIGEST_MIN_BYTES, rawMaxBytes),
  );
  const observedAt = optionalNonEmptyString(
    request.observed_at,
    "peer_coordination_digest.observed_at",
  ) ?? new Date().toISOString();

  if (registeredAgentCount < 2) {
    return {
      schema_version: PEER_COORDINATION_DIGEST_PROJECTION_SCHEMA_VERSION,
      goal_id: goalId,
      agent_id: agentId,
      digest: null,
      active_opportunity_ids: [],
      new_opportunity_ids: [],
      serialized_delta_bytes: 0,
    };
  }

  const workFacts = sharedGoalWorkFacts(request.work_items, agentId, observedAt);
  const callerLanes = decodeCallerLanes(workFacts.caller_lanes);
  const peerFrontiers = decodePeerFrontiers(workFacts.peer_frontiers);
  const coordination = projectPeerCoordination(callerLanes, peerFrontiers);

  const callerIdle = callerLanes.length === 0;
  const qualifying = coordination.collaboration_opportunities.filter((opp) => {
    if (callerIdle) {
      return (
        opp.kind === "peer_verification_needed"
        || opp.kind === "peer_unleased_lane_attention"
      );
    }
    return (
      opp.kind === "shared_target_collision_risk"
      || opp.kind === "peer_verification_needed"
    );
  });

  const activeOpportunityIds = Array.from(
    new Set(qualifying.map((opp) => opp.opportunity_id)),
  ).sort((a, b) => a.localeCompare(b));

  const seenSet = new Set(seenOpportunityIds);
  const newOpportunities = qualifying.filter(
    (opp) => !seenSet.has(opp.opportunity_id),
  );

  if (newOpportunities.length === 0) {
    return {
      schema_version: PEER_COORDINATION_DIGEST_PROJECTION_SCHEMA_VERSION,
      goal_id: goalId,
      agent_id: agentId,
      digest: null,
      active_opportunity_ids: activeOpportunityIds,
      new_opportunity_ids: [],
      serialized_delta_bytes: 0,
    };
  }

  const packed: JsonObject[] = [];
  for (let index = 0; index < newOpportunities.length; index += 1) {
    const opp = newOpportunities[index];
    const compactKind = COMPACT_KIND[opp.kind];
    if (!compactKind) continue;
    const remainingAfter = newOpportunities.length - (index + 1);
    const fullItem = compactDigestItem(opp, compactKind, true);
    const fullCandidate = makeDigestEnvelope([...packed, fullItem], remainingAfter);
    if (serializedDeltaBytes(fullCandidate) <= maxBytes) {
      packed.push(fullItem);
      continue;
    }
    if (opp.caller_todo_id !== null) {
      const strippedItem = compactDigestItem(opp, compactKind, false);
      const strippedCandidate = makeDigestEnvelope([...packed, strippedItem], remainingAfter);
      if (serializedDeltaBytes(strippedCandidate) <= maxBytes) {
        packed.push(strippedItem);
        continue;
      }
    }
    break;
  }

  const omittedCount = newOpportunities.length - packed.length;
  const digest = makeDigestEnvelope(packed, omittedCount);
  const serializedBytes = serializedDeltaBytes(digest);
  if (serializedBytes > maxBytes) {
    throw new EffectRuntimeRequestError(
      `peer_coordination_digest minimum envelope exceeds max_bytes (${serializedBytes} > ${maxBytes})`,
    );
  }

  return {
    schema_version: PEER_COORDINATION_DIGEST_PROJECTION_SCHEMA_VERSION,
    goal_id: goalId,
    agent_id: agentId,
    digest,
    active_opportunity_ids: activeOpportunityIds,
    new_opportunity_ids: newOpportunities.map((opp) => opp.opportunity_id),
    serialized_delta_bytes: serializedBytes,
  };
}
