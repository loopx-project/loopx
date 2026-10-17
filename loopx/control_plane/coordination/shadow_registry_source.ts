/** Registration is a source of migration facts, not an authorization grant.
 * Hold its existing cross-runtime lock through source verification and cutover.
 * Recovery of an already fenced operation deliberately does not use this owner.
 */
import {isAbsolute, resolve} from "node:path";
import {requireJsonObject} from "../runtime_decode.ts";
import type {JsonObject} from "../effect_program.ts";
import {withFileMutationLock} from "../effect_runtime_io.ts";
import {EffectRuntimeLockTimeoutError} from "../effect_runtime_errors.ts";
import {canonicalAuthorityBytes, hasExactAuthorityKeys} from "./authority_store_codec.ts";
import {AUTHORITY_SOURCE_CHANGED, registryAuthoritySourceCheck} from "./authority_source.ts";
import {ShadowManagementError} from "./shadow_management.ts";
import {normalizeRegisteredTodoAgents} from "./todo_agents.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";

interface ShadowRegistrySource {
  path: string;
  sha256: string;
  registered_agents: string[];
}

function registrySource(snapshot: JsonObject): ShadowRegistrySource {
  const raw = snapshot.registry_source;
  if (raw === null || typeof raw !== "object" || Array.isArray(raw) ||
      !hasExactAuthorityKeys(raw as JsonObject, ["path", "sha256", "registered_agents"])) {
    throw new ShadowManagementError("source_registry_witness_required");
  }
  const value = raw as JsonObject;
  if (typeof value.path !== "string" || !isAbsolute(value.path) ||
      typeof value.sha256 !== "string" || !BARE_SHA256_PATTERN.test(value.sha256) ||
      !Array.isArray(value.registered_agents) ||
      resolve(value.path) === resolve(String(snapshot.state_path))) {
    throw new ShadowManagementError("source_registry_witness_invalid");
  }
  return {path: value.path, sha256: value.sha256,
    registered_agents: normalizeRegisteredTodoAgents(value.registered_agents as string[])};
}

export async function verifyShadowRegistrySource(snapshot: JsonObject): Promise<void> {
  const source = registrySource(snapshot);
  if (!await registryAuthoritySourceCheck({registry_source: {...source}}, true)()) {
    throw new ShadowManagementError("source_registry_changed_retry");
  }
}

export function requirePromotionRegisteredAgents(snapshot: JsonObject, agents: readonly string[]): void {
  const current = registrySource(snapshot).registered_agents;
  if (!canonicalAuthorityBytes(current).equals(canonicalAuthorityBytes(normalizeRegisteredTodoAgents([...agents])))) {
    throw new ShadowManagementError("promotion_registration_changed_retry");
  }
}

export async function withShadowRegistrySource<T>(snapshot: JsonObject, operation: () => Promise<T>): Promise<T> {
  const source = registrySource(snapshot);
  let acquired = false;
  try {
    // Existing registry administration can hold R before requesting a source
    // lock. We already hold source locks: never wait for R in the reverse order.
    // A busy registry releases our locks so its writer can finish and we retry.
    return await withFileMutationLock(source.path, async () => {
      acquired = true;
      await verifyShadowRegistrySource(snapshot);
      return await operation();
    }, 0);
  } catch (error) {
    if (!acquired && error instanceof EffectRuntimeLockTimeoutError) {
      throw new ShadowManagementError("source_registry_busy_retry");
    }
    throw error;
  }
}

/** Retain the already observed source bytes through a native canonical effect.
 * Nonwaiting acquisition follows the existing source-guard rule: release and
 * retry on contention rather than waiting in a reverse registry/writer order.
 * The native effect owns these guards even if its transport caller exits.
 */
export async function withRegistryAuthoritySources<T>(
  value: unknown, primaryValue: unknown, operation: () => Promise<T>,
): Promise<T> {
  if (!Array.isArray(value) || value.length < 1 || value.length > 2) {
    throw new TypeError("registry_sources must contain one or two source witnesses");
  }
  const primary = requireJsonObject(primaryValue, "registry_source");
  registryAuthoritySourceCheck({registry_source: primary}, true);
  const sources = value.map(raw => {
    const source = requireJsonObject(raw, "registry_sources entry");
    const check = registryAuthoritySourceCheck({registry_source: source}, true);
    return {path: source.path as string, sha256: source.sha256 as string, check};
  }).sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  if (new Set(sources.map(source => source.path)).size !== sources.length ||
      !sources.some(source => source.path === primary.path && source.sha256 === primary.sha256)) {
    throw new TypeError("registry_sources must be distinct and retain the primary registry_source");
  }
  let sourcesAcquired = false;
  const lock = async (index: number): Promise<T> => {
    if (index < sources.length) {
      return withFileMutationLock(sources[index]!.path, () => lock(index + 1), 0);
    }
    sourcesAcquired = true;
    for (const source of sources) {
      if (!await source.check()) throw new ShadowManagementError(AUTHORITY_SOURCE_CHANGED.code);
    }
    return operation();
  };
  try { return await lock(0); }
  catch (error) {
    if (!sourcesAcquired && error instanceof EffectRuntimeLockTimeoutError) {
      throw new ShadowManagementError("source_registry_busy_retry");
    }
    throw error;
  }
}
