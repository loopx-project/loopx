import { createHash } from "node:crypto";
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { normalizeTodoAgent } from "../coordination/todo_agents.ts";
import { optionalNonEmptyString, requireBoolean, requireJsonObject } from "../runtime_decode.ts";
import { normalizeTodoRepository } from "../todos/work_requirements.ts";
import {
  comparisonWriteScopes,
  schedulingScopePairOverlaps,
  schedulingScopeRoot,
} from "../work_items/task_lease_acquire_decision.ts";
import { leaseWriteRepository } from "../work_items/task_lease_repository.ts";
import { normalizeWriteScopes } from "../work_items/write_scope_codec.ts";

export const MAX_PEER_FRONTIER_ITEMS = 12;
export const MAX_COLLABORATION_OPPORTUNITIES = 8;

export const COLLABORATION_OPPORTUNITY_KINDS = [
  "shared_target_collision_risk",
  "peer_verification_needed",
  "peer_unleased_lane_attention",
  "shared_domain_synergy",
] as const;

export const COLLABORATION_ADVISORY_ACTIONS = [
  "deconflict_before_edit",
  "offer_independent_verification",
  "inspect_peer_status_before_duplicating",
  "align_with_peer_deliverable",
] as const;

export type CollaborationOpportunityKind = (typeof COLLABORATION_OPPORTUNITY_KINDS)[number];
export type CollaborationAdvisoryAction = (typeof COLLABORATION_ADVISORY_ACTIONS)[number];

const TODO_ID_PATTERN = /^todo_[a-z0-9_-]{3,64}$/;
const PRIORITY_RANK: Record<string, number> = { P0: 0, P1: 1, P2: 2, P3: 3, P4: 4 };
const VERIFIER_ACTION_KINDS = new Set(["verify", "review", "benchmark", "evaluate", "audit"]);
const OPPORTUNITY_KIND_RANK: Record<CollaborationOpportunityKind, number> = {
  shared_target_collision_risk: 0,
  peer_verification_needed: 1,
  peer_unleased_lane_attention: 2,
  shared_domain_synergy: 3,
};

export interface CallerLaneSummary extends JsonObject {
  todo_id: string;
  priority: string | null;
  action_kind: string | null;
  task_domain: string | null;
  task_repository: string | null;
  target_key: string | null;
  required_write_scopes: string[];
  completion_validation_required: boolean;
  lease_active: boolean | null;
}

export interface PeerFrontierItem extends CallerLaneSummary {
  agent_id: string;
}

export interface CollaborationOpportunity extends JsonObject {
  opportunity_id: string;
  kind: CollaborationOpportunityKind;
  peer_agent_id: string;
  peer_todo_id: string;
  caller_todo_id: string | null;
  matched_on: string[];
  advisory_action: CollaborationAdvisoryAction;
  grants_authority: false;
}

export interface PeerCoordinationProjection {
  peer_frontiers: PeerFrontierItem[];
  collaboration_opportunities: CollaborationOpportunity[];
}

function optionalLowerToken(value: unknown, label: string): string | null {
  const token = optionalNonEmptyString(value, label);
  return token === null ? null : token.trim().toLowerCase() || null;
}

function optionalPriority(value: unknown, label: string): string | null {
  const token = optionalNonEmptyString(value, label);
  if (token === null) return null;
  const upper = token.trim().toUpperCase();
  if (!(upper in PRIORITY_RANK)) {
    throw new EffectRuntimeRequestError(`${label} must be P0..P4 or null`);
  }
  return upper;
}

function optionalLeaseActive(value: unknown, label: string): boolean | null {
  if (value === null || value === undefined) return null;
  return requireBoolean(value, label);
}

function decodeLaneBase(value: unknown, label: string): CallerLaneSummary {
  const raw = requireJsonObject(value, label);
  const todoId = (optionalNonEmptyString(raw.todo_id, `${label}.todo_id`) ?? "").trim().toLowerCase();
  if (!TODO_ID_PATTERN.test(todoId)) {
    throw new EffectRuntimeRequestError(`${label}.todo_id must be a valid Todo id`);
  }
  return {
    todo_id: todoId,
    priority: optionalPriority(raw.priority, `${label}.priority`),
    action_kind: optionalLowerToken(raw.action_kind, `${label}.action_kind`),
    task_domain: optionalLowerToken(raw.task_domain, `${label}.task_domain`),
    task_repository: normalizeTodoRepository(raw.task_repository, `${label}.task_repository`),
    target_key: optionalNonEmptyString(raw.target_key, `${label}.target_key`),
    required_write_scopes: normalizeWriteScopes(raw.required_write_scopes ?? []),
    completion_validation_required: raw.completion_validation_required === undefined
      ? false
      : requireBoolean(raw.completion_validation_required, `${label}.completion_validation_required`),
    lease_active: optionalLeaseActive(raw.lease_active, `${label}.lease_active`),
  };
}

export function decodeCallerLanes(value: unknown): CallerLaneSummary[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError("shared_goal_alignment.caller_lanes must be an array");
  return value.map((item, idx) => decodeLaneBase(item, `shared_goal_alignment.caller_lanes[${idx}]`));
}

export function decodePeerFrontiers(value: unknown): PeerFrontierItem[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError("shared_goal_alignment.peer_frontiers must be an array");
  return value.map((item, idx) => {
    const label = `shared_goal_alignment.peer_frontiers[${idx}]`;
    const base = decodeLaneBase(item, label);
    const raw = item as Record<string, unknown>;
    let agentId: string;
    try {
      agentId = normalizeTodoAgent(raw.agent_id, `${label}.agent_id`);
    } catch {
      throw new EffectRuntimeRequestError(`${label}.agent_id must be a public-safe agent id`);
    }
    return { ...base, agent_id: agentId };
  });
}

function priorityRank(priority: string | null): number {
  return priority !== null && priority in PRIORITY_RANK ? PRIORITY_RANK[priority] : 50;
}

function buildOpportunityId(
  kind: CollaborationOpportunityKind,
  peerAgentId: string,
  peerTodoId: string,
  callerTodoId: string | null,
  matchedOn: readonly string[],
): string {
  const payload = JSON.stringify([kind, peerAgentId, peerTodoId, callerTodoId ?? "", ...matchedOn]);
  return `opp_${createHash("sha256").update(payload, "utf8").digest("hex").slice(0, 16)}`;
}

function precomputedScopesOverlap(left: readonly string[], right: readonly string[]): boolean {
  if (left.length === 0 || right.length === 0) return false;
  return left.some((first) => right.some((second) => schedulingScopePairOverlaps(first, second)));
}

interface IndexedCaller {
  readonly idx: number;
  readonly lane: CallerLaneSummary;
  readonly repoLower: string | null;
  readonly isVerifier: boolean;
  readonly scopes: readonly string[];
  readonly scopeRoots: readonly string[][];
}

interface IndexedPeer {
  readonly lane: PeerFrontierItem;
  readonly priorityRank: number;
  readonly repoLower: string | null;
  readonly scopes: readonly string[];
  readonly scopeRoots: readonly string[][];
}

interface ScopeTrieNode {
  exactOrPrefix: IndexedCaller[];
  descendants: IndexedCaller[];
  children: Map<string, ScopeTrieNode>;
}

function createScopeTrieNode(): ScopeTrieNode {
  return { exactOrPrefix: [], descendants: [], children: new Map() };
}

function splitScopeRootSegments(scope: string): string[] {
  if (scope === "*" || scope === "**" || scope === "./" || scope === ".") return [];
  const root = schedulingScopeRoot(scope);
  return root && root !== "." ? root.split("/") : [];
}

function pushToMapArray<K, V>(map: Map<K, V[]>, key: K, value: V): void {
  const existing = map.get(key);
  if (existing !== undefined) existing.push(value);
  else map.set(key, [value]);
}

function buildCallerIndex(sortedCallers: readonly CallerLaneSummary[]) {
  const byRepo = new Map<string, IndexedCaller[]>();
  const globalRepoCallers: IndexedCaller[] = [];
  const byTarget = new Map<string, IndexedCaller[]>();
  const byDomain = new Map<string, IndexedCaller[]>();
  const verifiers: IndexedCaller[] = [];
  const scopeTrie = createScopeTrieNode();
  const globalScopeCallers: IndexedCaller[] = [];

  const callers: IndexedCaller[] = sortedCallers.map((lane, idx) => {
    const repoLower = leaseWriteRepository(lane.task_repository)?.toLowerCase() ?? null;
    const scopes = lane.required_write_scopes.length > 0
      ? comparisonWriteScopes(lane.required_write_scopes)
      : [];
    const scopeRoots = scopes.map(splitScopeRootSegments);
    const entry: IndexedCaller = {
      idx,
      lane,
      repoLower,
      isVerifier: Boolean(lane.action_kind && VERIFIER_ACTION_KINDS.has(lane.action_kind)),
      scopes,
      scopeRoots,
    };
    if (repoLower === null) globalRepoCallers.push(entry);
    else pushToMapArray(byRepo, repoLower, entry);
    if (lane.target_key) pushToMapArray(byTarget, lane.target_key, entry);
    if (lane.task_domain) pushToMapArray(byDomain, lane.task_domain, entry);
    if (entry.isVerifier) verifiers.push(entry);
    if (scopes.length > 0) {
      let addedGlobal = false;
      for (const parts of scopeRoots) {
        if (parts.length === 0) {
          if (!addedGlobal) {
            globalScopeCallers.push(entry);
            addedGlobal = true;
          }
          continue;
        }
        let node = scopeTrie;
        for (const seg of parts) {
          node.descendants.push(entry);
          let child = node.children.get(seg);
          if (child === undefined) {
            child = createScopeTrieNode();
            node.children.set(seg, child);
          }
          node = child;
        }
        node.exactOrPrefix.push(entry);
      }
    }
    return entry;
  });

  return { callers, byRepo, globalRepoCallers, byTarget, byDomain, verifiers, scopeTrie, globalScopeCallers };
}

function repoCompatible(callerRepoLower: string | null, peerRepoLower: string | null): boolean {
  return callerRepoLower === null || peerRepoLower === null || callerRepoLower === peerRepoLower;
}

function scopeCandidateCallers(
  index: ReturnType<typeof buildCallerIndex>,
  peer: IndexedPeer,
): IndexedCaller[] {
  if (peer.scopes.length === 0) return [];
  const seen = new Set<IndexedCaller>();
  const add = (list: readonly IndexedCaller[]) => {
    for (const c of list) {
      if (repoCompatible(c.repoLower, peer.repoLower)) seen.add(c);
    }
  };
  add(index.globalScopeCallers);
  for (const parts of peer.scopeRoots) {
    if (parts.length === 0) {
      // Rootless wildcard peer can overlap every caller that declares a scope.
      return index.callers.filter((c) => c.scopes.length > 0 && repoCompatible(c.repoLower, peer.repoLower));
    }
    let node: ScopeTrieNode | undefined = index.scopeTrie;
    for (const seg of parts) {
      if (node === undefined) break;
      add(node.exactOrPrefix);
      node = node.children.get(seg);
    }
    if (node !== undefined) {
      add(node.exactOrPrefix);
      add(node.descendants);
    }
  }
  return [...seen];
}

function compareBuiltOpportunities(
  a: CollaborationOpportunity,
  b: CollaborationOpportunity,
  peerRankByTodo: ReadonlyMap<string, number>,
): number {
  return OPPORTUNITY_KIND_RANK[a.kind] - OPPORTUNITY_KIND_RANK[b.kind]
    || (peerRankByTodo.get(a.peer_todo_id) ?? 50) - (peerRankByTodo.get(b.peer_todo_id) ?? 50)
    || a.peer_todo_id.localeCompare(b.peer_todo_id)
    || (a.caller_todo_id ?? "").localeCompare(b.caller_todo_id ?? "")
    || a.opportunity_id.localeCompare(b.opportunity_id);
}

function collectBoundedOpportunities(
  peersInOppOrder: readonly IndexedPeer[],
  index: ReturnType<typeof buildCallerIndex>,
  peerRankByTodo: ReadonlyMap<string, number>,
  limit: number,
): CollaborationOpportunity[] {
  const winners: CollaborationOpportunity[] = [];
  const kindsInRankOrder: readonly CollaborationOpportunityKind[] = [
    "shared_target_collision_risk",
    "peer_verification_needed",
    "peer_unleased_lane_attention",
    "shared_domain_synergy",
  ];

  for (const kind of kindsInRankOrder) {
    const remaining = limit - winners.length;
    if (remaining <= 0) break;

    const bucket: CollaborationOpportunity[] = [];
    const seenInBucket = new Set<string>();
    let cutoff: { rank: number; todoId: string } | null = null;

    for (const peer of peersInOppOrder) {
      if (cutoff !== null && (peer.priorityRank > cutoff.rank || peer.lane.todo_id !== cutoff.todoId)) {
        break;
      }

      if (kind === "peer_verification_needed" && !peer.lane.completion_validation_required) continue;
      if (kind === "peer_unleased_lane_attention"
        && (peer.lane.lease_active !== false || (peer.lane.priority !== "P0" && peer.lane.priority !== "P1"))) continue;
      if (kind === "shared_domain_synergy" && !peer.lane.task_domain) continue;

      let candidateCallers: IndexedCaller[];
      if (kind === "shared_target_collision_risk") {
        const merged = new Set<IndexedCaller>();
        if (peer.lane.target_key) {
          for (const c of index.byTarget.get(peer.lane.target_key) ?? []) {
            if (repoCompatible(c.repoLower, peer.repoLower)) merged.add(c);
          }
        }
        for (const c of scopeCandidateCallers(index, peer)) {
          merged.add(c);
        }
        candidateCallers = [...merged].sort((a, b) => a.idx - b.idx);
      } else if (kind === "peer_verification_needed") {
        const merged = new Set<IndexedCaller>();
        for (const c of index.verifiers) {
          if (repoCompatible(c.repoLower, peer.repoLower)) merged.add(c);
        }
        if (peer.lane.task_domain) {
          for (const c of index.byDomain.get(peer.lane.task_domain) ?? []) {
            if (repoCompatible(c.repoLower, peer.repoLower)) merged.add(c);
          }
        }
        if (peer.repoLower !== null) {
          for (const c of index.byRepo.get(peer.repoLower) ?? []) {
            merged.add(c);
          }
        }
        candidateCallers = [...merged].sort((a, b) => a.idx - b.idx);
      } else if (kind === "peer_unleased_lane_attention") {
        const merged = new Set<IndexedCaller>();
        if (peer.lane.target_key) {
          for (const c of index.byTarget.get(peer.lane.target_key) ?? []) {
            if (repoCompatible(c.repoLower, peer.repoLower)) merged.add(c);
          }
        }
        if (peer.lane.task_domain) {
          for (const c of index.byDomain.get(peer.lane.task_domain) ?? []) {
            if (repoCompatible(c.repoLower, peer.repoLower)) merged.add(c);
          }
        }
        candidateCallers = [...merged].sort((a, b) => a.idx - b.idx);
      } else {
        candidateCallers = [];
        for (const c of index.byDomain.get(peer.lane.task_domain!) ?? []) {
          if (repoCompatible(c.repoLower, peer.repoLower)) candidateCallers.push(c);
        }
      }

      for (const caller of candidateCallers) {
        const dedupKey = `${peer.lane.agent_id}|${peer.lane.todo_id}|${caller.lane.todo_id}`;
        if (seenInBucket.has(dedupKey)) continue;

        const sameTarget = Boolean(
          caller.lane.target_key && peer.lane.target_key && caller.lane.target_key === peer.lane.target_key,
        );
        const sameDomain = Boolean(
          caller.lane.task_domain && peer.lane.task_domain && caller.lane.task_domain === peer.lane.task_domain,
        );
        const sameRepo = Boolean(caller.repoLower && peer.repoLower && caller.repoLower === peer.repoLower);

        if (kind === "shared_target_collision_risk") {
          const signals: string[] = [];
          if (sameTarget) signals.push("target_key");
          if (precomputedScopesOverlap(caller.scopes, peer.scopes)) signals.push("required_write_scopes");
          if (signals.length === 0) continue;
          seenInBucket.add(dedupKey);
          bucket.push(buildOpportunity(kind, "deconflict_before_edit", peer.lane, caller.lane.todo_id, signals));
        } else if (kind === "peer_verification_needed") {
          if (!(caller.isVerifier || sameDomain || sameRepo)) continue;
          const signals = ["completion_validation_required"];
          if (caller.isVerifier) signals.push("caller_verifier_action_kind");
          if (sameDomain) signals.push("task_domain");
          if (sameRepo) signals.push("task_repository");
          seenInBucket.add(dedupKey);
          bucket.push(buildOpportunity(kind, "offer_independent_verification", peer.lane, caller.lane.todo_id, signals));
        } else if (kind === "peer_unleased_lane_attention") {
          if (!(sameTarget || sameDomain)) continue;
          const signals = ["lease_inactive"];
          if (sameTarget) signals.push("target_key");
          if (sameDomain) signals.push("task_domain");
          seenInBucket.add(dedupKey);
          bucket.push(buildOpportunity(kind, "inspect_peer_status_before_duplicating", peer.lane, caller.lane.todo_id, signals));
        } else {
          if (sameTarget || precomputedScopesOverlap(caller.scopes, peer.scopes)) continue;
          const signals = ["task_domain"];
          if (sameRepo) signals.push("task_repository");
          seenInBucket.add(dedupKey);
          bucket.push(buildOpportunity(kind, "align_with_peer_deliverable", peer.lane, caller.lane.todo_id, signals));
        }
      }

      if (bucket.length >= remaining && cutoff === null) {
        cutoff = { rank: peer.priorityRank, todoId: peer.lane.todo_id };
      }
    }

    if (bucket.length > 0) {
      bucket.sort((a, b) => compareBuiltOpportunities(a, b, peerRankByTodo));
      winners.push(...bucket.slice(0, remaining));
    }
  }
  return winners;
}

function buildOpportunity(
  kind: CollaborationOpportunityKind,
  advisoryAction: CollaborationAdvisoryAction,
  peer: PeerFrontierItem,
  callerTodoId: string | null,
  matchedOn: string[],
): CollaborationOpportunity {
  return {
    opportunity_id: buildOpportunityId(kind, peer.agent_id, peer.todo_id, callerTodoId, matchedOn),
    kind,
    peer_agent_id: peer.agent_id,
    peer_todo_id: peer.todo_id,
    caller_todo_id: callerTodoId,
    matched_on: matchedOn,
    advisory_action: advisoryAction,
    grants_authority: false,
  };
}

/** Deterministically project bounded same-Goal peer frontiers and coordination opportunities. */
export function projectPeerCoordination(
  callerLanes: readonly CallerLaneSummary[],
  peerLanes: readonly PeerFrontierItem[],
): PeerCoordinationProjection {
  const sortedCallers = [...callerLanes].sort((a, b) => a.todo_id.localeCompare(b.todo_id));
  const sortedPeers = [...peerLanes].sort(
    (a, b) => priorityRank(a.priority) - priorityRank(b.priority)
      || a.agent_id.localeCompare(b.agent_id)
      || a.todo_id.localeCompare(b.todo_id),
  );
  const peerRankByTodo = new Map<string, number>();
  for (const lane of sortedPeers) {
    peerRankByTodo.set(lane.todo_id, priorityRank(lane.priority));
  }
  const indexedPeers: IndexedPeer[] = sortedPeers.map((lane) => {
    const repoLower = leaseWriteRepository(lane.task_repository)?.toLowerCase() ?? null;
    const scopes = lane.required_write_scopes.length > 0
      ? comparisonWriteScopes(lane.required_write_scopes)
      : [];
    return {
      lane,
      priorityRank: peerRankByTodo.get(lane.todo_id) ?? 50,
      repoLower,
      scopes,
      scopeRoots: scopes.map(splitScopeRootSegments),
    };
  });
  const peersInOppOrder = [...indexedPeers].sort(
    (a, b) => a.priorityRank - b.priorityRank
      || a.lane.todo_id.localeCompare(b.lane.todo_id)
      || a.lane.agent_id.localeCompare(b.lane.agent_id),
  );

  if (sortedCallers.length === 0) {
    const rawOpps: CollaborationOpportunity[] = [];
    const seen = new Set<string>();
    const addIdleBucket = (kind: "peer_verification_needed" | "peer_unleased_lane_attention") => {
      let cutoff: { rank: number; todoId: string } | null = null;
      const needed = MAX_COLLABORATION_OPPORTUNITIES - rawOpps.length;
      if (needed <= 0) return;
      let added = 0;
      for (const peer of peersInOppOrder) {
        if (cutoff !== null && (peer.priorityRank > cutoff.rank || peer.lane.todo_id !== cutoff.todoId)) break;
        if (kind === "peer_verification_needed" && peer.lane.completion_validation_required) {
          const key = `${kind}|${peer.lane.agent_id}|${peer.lane.todo_id}`;
          if (!seen.has(key)) {
            seen.add(key);
            rawOpps.push(buildOpportunity(
              kind,
              "offer_independent_verification",
              peer.lane,
              null,
              ["completion_validation_required", "idle_caller_discovery"],
            ));
            added += 1;
          }
        } else if (kind === "peer_unleased_lane_attention"
          && peer.lane.lease_active === false && (peer.lane.priority === "P0" || peer.lane.priority === "P1")) {
          const key = `${kind}|${peer.lane.agent_id}|${peer.lane.todo_id}`;
          if (!seen.has(key)) {
            seen.add(key);
            rawOpps.push(buildOpportunity(
              kind,
              "inspect_peer_status_before_duplicating",
              peer.lane,
              null,
              ["lease_inactive", "idle_caller_discovery"],
            ));
            added += 1;
          }
        }
        if (added >= needed && cutoff === null) {
          cutoff = { rank: peer.priorityRank, todoId: peer.lane.todo_id };
        }
      }
    };
    addIdleBucket("peer_verification_needed");
    addIdleBucket("peer_unleased_lane_attention");
    rawOpps.sort((a, b) => compareBuiltOpportunities(a, b, peerRankByTodo));
    return {
      peer_frontiers: sortedPeers.slice(0, MAX_PEER_FRONTIER_ITEMS),
      collaboration_opportunities: rawOpps.slice(0, MAX_COLLABORATION_OPPORTUNITIES),
    };
  }

  const callerIndex = buildCallerIndex(sortedCallers);
  const built = collectBoundedOpportunities(
    peersInOppOrder,
    callerIndex,
    peerRankByTodo,
    MAX_COLLABORATION_OPPORTUNITIES,
  );
  return {
    peer_frontiers: sortedPeers.slice(0, MAX_PEER_FRONTIER_ITEMS),
    collaboration_opportunities: built.slice(0, MAX_COLLABORATION_OPPORTUNITIES),
  };
}
