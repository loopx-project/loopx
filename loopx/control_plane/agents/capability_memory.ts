/** Host-local observations, never Goal configuration or an authorization grant. */
import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import {join} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {durableWriteJson, withFileMutationLock} from "../effect_runtime_io.ts";
import {requireBoolean, requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";
import {OBSERVABLE_RUNTIME_CAPABILITIES} from "./capability_gate.ts";

const SCHEMA = "agent_runtime_capabilities_v0";
const observable = (value: string) => OBSERVABLE_RUNTIME_CAPABILITIES.has(value);
const lexical = (left: string, right: string) => left.localeCompare(right);
const unique = (values: string[]) => [...new Set(values)].sort(lexical);
const sortedEntries = (value: JsonObject) =>
  Object.entries(value).sort(([left], [right]) => lexical(left, right));

export async function agentCapabilityMemory(request: JsonObject): Promise<JsonObject> {
  if (request.schema_version !== "agent_runtime_capability_request_v0") {
    throw new TypeError("agent runtime capability request schema mismatch");
  }
  const root = requireNonEmptyString(request.runtime_root, "runtime_root");
  const scope = {
    registry: requireNonEmptyString(request.registry, "registry"),
    goal_id: requireNonEmptyString(request.goal_id, "goal_id"),
    agent_id: requireNonEmptyString(request.agent_id, "agent_id"),
  };
  const legacyRegistries = unique(requireStringArray(request.legacy_registries ?? [], "legacy_registries"))
    .filter(registry => registry.length > 0 && registry !== scope.registry);
  const preserveUnavailable = request.preserve_unavailable === undefined
    ? false : requireBoolean(request.preserve_unavailable, "preserve_unavailable");
  const registered = requireStringArray(request.registered_agents, "registered_agents");
  if (!registered.includes(scope.agent_id)) throw new TypeError("agent must be registered for this Goal");
  const supplied = unique(requireStringArray(request.available ?? [], "available"));
  const available = supplied.filter(observable);
  const unavailable = unique(requireStringArray(request.unavailable ?? [], "unavailable"));
  const forget = unique(requireStringArray(request.forget ?? [], "forget"));
  if ([...unavailable, ...forget].some(c => !observable(c))) {
    throw new TypeError("only observable runtime capabilities can be marked unavailable or forgotten");
  }
  if (available.some(c => unavailable.includes(c) || forget.includes(c)) || unavailable.some(c => forget.includes(c))) {
    throw new TypeError("capability updates must not contradict each other");
  }
  const execute = requireBoolean(request.execute, "execute");
  const scopePath = (targetScope: typeof scope) => {
    const key = createHash("sha256").update(JSON.stringify(targetScope)).digest("hex");
    return join(root, "agent-runtime-capabilities", `${key}.json`);
  };
  const path = scopePath(scope);
  const readScope = async (targetScope: typeof scope): Promise<{exists: boolean; observations: JsonObject}> => {
    let bytes: string;
    try { bytes = await readFile(scopePath(targetScope), "utf8"); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return {exists: false, observations: {}};
      throw error;
    }
    const state = requireJsonObject(JSON.parse(bytes), "agent runtime capability state");
    if (state.schema_version !== SCHEMA || Object.entries(targetScope).some(([k, v]) => state[k] !== v)) {
      throw new TypeError("agent runtime capability state scope/schema mismatch");
    }
    const observations = requireJsonObject(state.observations, "observations");
    for (const [capability, value] of Object.entries(observations)) {
      if (!observable(capability) || (value !== "available" && value !== "unavailable")) {
        throw new TypeError("invalid agent runtime capability observation");
      }
    }
    return {exists: true, observations};
  };
  const read = async (): Promise<{canonicalExists: boolean; canonical: JsonObject; merged: JsonObject}> => {
    let merged: JsonObject = {};
    for (const registry of legacyRegistries) {
      const legacy = await readScope({...scope, registry});
      merged = {...merged, ...legacy.observations};
    }
    const canonical = await readScope(scope);
    return {
      canonicalExists: canonical.exists,
      canonical: canonical.observations,
      merged: {...merged, ...canonical.observations},
    };
  };
  const update = async (): Promise<JsonObject> => {
    const {canonicalExists, canonical, merged: previous} = await read();
    const next = {...previous};
    for (const c of available) {
      if (!preserveUnavailable || previous[c] !== "unavailable") next[c] = "available";
    }
    for (const c of unavailable) next[c] = "unavailable";
    for (const c of forget) delete next[c];
    const changed = JSON.stringify(sortedEntries(previous)) !== JSON.stringify(sortedEntries(next));
    const migrateCanonical = (!canonicalExists && Object.keys(next).length > 0)
      || JSON.stringify(sortedEntries(canonical)) !== JSON.stringify(sortedEntries(next));
    const shouldWrite = execute && (changed || (migrateCanonical && (available.length > 0 || unavailable.length > 0 || forget.length > 0)));
    if (shouldWrite) await durableWriteJson(path, {schema_version: SCHEMA, ...scope, observations: next});
    const project = (observations: JsonObject) => ({
      available: Object.keys(observations).filter(c => observations[c] === "available").sort(lexical),
      unavailable: Object.keys(observations).filter(c => observations[c] === "unavailable").sort(lexical),
    });
    return {
      schema_version: SCHEMA, goal_id: scope.goal_id, agent_id: scope.agent_id,
      scope: "host_registry_goal_agent", source: "agent_runtime_observation",
      ...project(execute ? next : previous),
      ...(changed && !execute ? {proposed: project(next)} : {}),
      invocation_only: supplied.filter(c => !observable(c)),
      written: shouldWrite, durable_grant_written: false,
    };
  };
  return execute && (available.length || unavailable.length || forget.length)
    ? withFileMutationLock(path, update) : update();
}
