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

export function parseMonitorScopeGenerations(value: unknown): Map<string, number> {
  if (value === null || value === undefined || value === "") return new Map();
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError("material_change_scope_generations must be a string");
  }
  const result = new Map<string, number>();
  for (const entry of value.split(",")) {
    const [digest, generationText, ...extra] = entry.split(":");
    const generation = Number(generationText);
    if (extra.length > 0 || !SCOPE_DIGEST_PATTERN.test(digest ?? "") ||
        !/^\d+$/u.test(generationText ?? "") || !Number.isSafeInteger(generation)) {
      throw new EffectRuntimeRequestError("material_change_scope_generations is malformed");
    }
    if (result.has(digest!)) {
      throw new EffectRuntimeRequestError("material_change_scope_generations contains a duplicate scope");
    }
    result.set(digest!, generation);
  }
  if (result.size > MONITOR_SCOPE_GENERATION_LIMIT) {
    throw new EffectRuntimeRequestError(
      `material_change_scope_generations exceeds ${MONITOR_SCOPE_GENERATION_LIMIT} retained scopes`,
    );
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
  return parseMonitorScopeGenerations(value).get(monitorChangeScopeDigest(scope)) ?? 0;
}

export function advanceMonitorScopeGeneration(
  value: unknown,
  scope: string | null,
  generation: number,
  advances: boolean,
): JsonObject {
  const generations = parseMonitorScopeGenerations(value);
  if (scope !== null && advances) {
    const digest = monitorChangeScopeDigest(scope);
    if (!generations.has(digest) && generations.size >= MONITOR_SCOPE_GENERATION_LIMIT) {
      throw new EffectRuntimeRequestError(
        `Monitor already retains ${MONITOR_SCOPE_GENERATION_LIMIT} scoped transitions; create an independent Monitor before adding another scope`,
      );
    }
    generations.set(digest, generation);
  }
  return {
    encoded: serializeMonitorScopeGenerations(generations),
    scope_generation: scope === null ? null : generations.get(monitorChangeScopeDigest(scope)) ?? 0,
  };
}
