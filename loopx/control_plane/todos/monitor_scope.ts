import { createHash } from "node:crypto";

import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";

export const MONITOR_SCOPE_GENERATION_LIMIT = 128;
const MONITOR_SCOPE_PATTERN = /^[a-z\d][a-z\d_.:/-]{0,95}$/u;
const SCOPE_DIGEST_PATTERN = /^[a-f\d]{24}$/u;

export function normalizeMonitorChangeScope(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim().toLowerCase();
  return MONITOR_SCOPE_PATTERN.test(normalized) ? normalized : null;
}

export function requireMonitorChangeScope(value: unknown, label = "material_change_scope"): string {
  const normalized = normalizeMonitorChangeScope(value);
  if (normalized === null) {
    throw new EffectRuntimeRequestError(
      `${label} must be a lowercase public-safe token of at most 96 characters`,
    );
  }
  return normalized;
}

export function monitorChangeScopeDigest(scope: string): string {
  return createHash("sha256").update(scope, "utf8").digest("hex").slice(0, 24);
}

interface MonitorScopeState {
  generation: number;
  resultDigest: string | null;
}

function parseMonitorScopeStates(value: unknown): Map<string, MonitorScopeState> {
  if (value === null || value === undefined || value === "") return new Map();
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError("material_change_scope_generations must be a string");
  }
  const result = new Map<string, MonitorScopeState>();
  for (const entry of value.split(",")) {
    const [digest, generationText, resultDigest, ...extra] = entry.split(":");
    const generation = Number(generationText);
    if (extra.length > 0 || !SCOPE_DIGEST_PATTERN.test(digest ?? "") ||
        !/^\d+$/u.test(generationText ?? "") || !Number.isSafeInteger(generation) ||
        (resultDigest !== undefined && !SCOPE_DIGEST_PATTERN.test(resultDigest))) {
      throw new EffectRuntimeRequestError("material_change_scope_generations is malformed");
    }
    if (result.has(digest!)) {
      throw new EffectRuntimeRequestError("material_change_scope_generations contains a duplicate scope");
    }
    result.set(digest!, { generation, resultDigest: resultDigest ?? null });
  }
  if (result.size > MONITOR_SCOPE_GENERATION_LIMIT) {
    throw new EffectRuntimeRequestError(
      `material_change_scope_generations exceeds ${MONITOR_SCOPE_GENERATION_LIMIT} retained scopes`,
    );
  }
  return result;
}

function serializeMonitorScopeStates(states: Map<string, MonitorScopeState>): string {
  return [...states.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([digest, state]) =>
      state.resultDigest === null
        ? `${digest}:${state.generation}`
        : `${digest}:${state.generation}:${state.resultDigest}`,
    )
    .join(",");
}

export function parseMonitorScopeGenerations(value: unknown): Map<string, number> {
  const result = new Map<string, number>();
  for (const [digest, state] of parseMonitorScopeStates(value)) {
    result.set(digest, state.generation);
  }
  return result;
}

export function serializeMonitorScopeGenerations(generations: Map<string, number>): string {
  return [...generations.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([digest, generation]) => `${digest}:${generation}`)
    .join(",");
}

export function monitorScopeGeneration(value: unknown, scope: string): number {
  return parseMonitorScopeStates(value).get(monitorChangeScopeDigest(scope))?.generation ?? 0;
}

export function monitorScopeResultAdvances(
  value: unknown,
  scope: string,
  resultHash: string,
  previousScope: unknown,
  previousHash: string,
): boolean {
  const state = parseMonitorScopeStates(value).get(monitorChangeScopeDigest(scope));
  if (!state) return true;
  if (state.resultDigest !== null) {
    return state.resultDigest !== monitorChangeScopeDigest(resultHash);
  }
  return normalizeMonitorChangeScope(previousScope) === scope
    ? resultHash !== previousHash
    : true;
}

export function advanceMonitorScopeGeneration(
  value: unknown,
  scope: string | null,
  generation: number,
  advances: boolean,
  resultHash: string | null = null,
): JsonObject {
  const states = parseMonitorScopeStates(value);
  if (scope !== null && advances) {
    const digest = monitorChangeScopeDigest(scope);
    if (!states.has(digest) && states.size >= MONITOR_SCOPE_GENERATION_LIMIT) {
      throw new EffectRuntimeRequestError(
        `Monitor already retains ${MONITOR_SCOPE_GENERATION_LIMIT} scoped transitions; create an independent Monitor before adding another scope`,
      );
    }
    const resultDigest =
      typeof resultHash === "string" && resultHash.length > 0
        ? monitorChangeScopeDigest(resultHash)
        : null;
    states.set(digest, { generation, resultDigest });
  }
  return {
    encoded: serializeMonitorScopeStates(states),
    scope_generation:
      scope === null ? null : states.get(monitorChangeScopeDigest(scope))?.generation ?? 0,
  };
}
