import { createHash } from "node:crypto";
import { inflateSync } from "node:zlib";

import { authorityUnicodeCompare } from "../coordination/authority_store_codec.ts";
import { contextSourceIdentity } from "./diagnostic_projection.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { pythonString, requireJsonObject, type JsonObject } from "../runtime_decode.ts";

export const COMPLETE_TURN_DECISION_VERSION = "loopx_turn_envelope_v1";
// Sparse producer metadata is a different identity from the earlier
// experimental all-float binding; its saved contracts need their pinned reader.
export const COMPLETE_TURN_HASH_CODEC = "context_source_numbers_v2";

const EXECUTABLE_CLI_ARGS_MAX_ITEMS = 64;
const EXECUTABLE_CLI_ARGS_MAX_ITEM_CHARS = 512;
const EXECUTABLE_CLI_ARGS_MAX_TOTAL_CHARS = 2_048;

export function object(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? { ...(value as JsonObject) }
    : {};
}

export function pickNonNull(source: JsonObject, fields: readonly string[]): JsonObject {
  const picked: JsonObject = {};
  for (const field of fields) {
    if (source[field] !== null && source[field] !== undefined) picked[field] = source[field];
  }
  return picked;
}

export function pickOwn(source: JsonObject, fields: readonly string[]): JsonObject {
  const picked: JsonObject = {};
  for (const field of fields) {
    if (Object.hasOwn(source, field)) picked[field] = source[field];
  }
  return picked;
}

export function isNonEmptyFilePath(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && !value.includes("\0");
}

export function scalarString(value: unknown, label: string, fallback = ""): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean") {
    throw new EffectRuntimeRequestError(`${label} must be scalar-compatible`);
  }
  return pythonString(value);
}

export function integer(value: unknown, label: string): number {
  if (value === null || value === undefined || value === "") return 0;
  if (typeof value === "boolean") return Number(value);
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === "string" && /^[+-]?\d+$/u.test(value.trim())) return Number.parseInt(value, 10);
  throw new EffectRuntimeRequestError(`${label} must be integer-compatible`);
}

export function text(value: unknown, limit: number): string | null {
  if (value === null || value === undefined) return null;
  const compact = scalarString(value, "text value").trim().split(/\s+/u).join(" ");
  if (!compact) return null;
  return compact.length <= limit ? compact : `${compact.slice(0, Math.max(0, limit - 3)).trimEnd()}...`;
}

export function textList(value: unknown, limit: number, itemLimit = 240): string[] {
  if (!Array.isArray(value)) return [];
  const result: string[] = [];
  for (const item of value) {
    const rendered = text(item, itemLimit);
    if (!rendered || result.includes(rendered)) continue;
    result.push(rendered);
    if (result.length >= limit) break;
  }
  return result;
}

export function compactFields(
  value: unknown, fields: readonly string[], limits: Readonly<Record<string, number>> = {},
): JsonObject {
  const source = object(value);
  const compact: JsonObject = {};
  for (const field of fields) {
    const raw = source[field];
    if (raw === null || raw === undefined) continue;
    if (limits[field] !== undefined) {
      const rendered = text(raw, limits[field]);
      if (rendered) compact[field] = rendered;
    } else if (Array.isArray(raw)) {
      const values = textList(raw, 6, 140);
      if (values.length > 0) compact[field] = values;
    } else compact[field] = raw;
  }
  return compact;
}

export function executableCliArgs(value: unknown): string[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > EXECUTABLE_CLI_ARGS_MAX_ITEMS) return [];
  const result: string[] = [];
  let totalChars = 0;
  for (const item of value) {
    if (typeof item !== "string" || !item || item.length > EXECUTABLE_CLI_ARGS_MAX_ITEM_CHARS) return [];
    totalChars += item.length + 1;
    if (totalChars > EXECUTABLE_CLI_ARGS_MAX_TOTAL_CHARS) return [];
    result.push(item);
  }
  return result;
}

export function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (typeof value !== "object" || value === null) return value;
  const source = value as JsonObject;
  return Object.fromEntries(
    Object.keys(source).sort(authorityUnicodeCompare).map((key) => [key, canonicalValue(source[key])]),
  );
}

export function canonicalHash(value: unknown): string {
  return `sha256:${createHash("sha256").update(JSON.stringify(canonicalValue(value)), "utf8").digest("hex")}`;
}

export function completeSourceHash(value: JsonObject, identity = contextSourceIdentity(value, [])): string {
  return `sha256:${createHash("sha256").update(identity(value, []), "utf8").digest("hex")}`;
}

/** Complete references reuse the checked content binding. Historical captures
 * retain their old JSON hash; never rewrite a stored action or Turn key. */
export function decisionSourceHash(payload: JsonObject): string {
  if (payload.schema_version !== COMPLETE_TURN_DECISION_VERSION) return canonicalHash(payload);
  const digest = object(payload.contract_binding).content_hash;
  if (typeof digest !== "string") throw new EffectRuntimeRequestError("complete Turn decision content binding is missing");
  return digest;
}

export function shellQuote(value: string): string {
  return /^[A-Za-z0-9_@%+=:,./-]+$/u.test(value) ? value : `'${value.replaceAll("'", `'"'"'`)}'`;
}

export function commandPrefix(runtimeRoot: unknown, registry?: unknown): string {
  const runtimeRootText = scalarString(runtimeRoot, "quota payload runtime_root").trim();
  const registryText = scalarString(registry, "quota payload registry").trim();
  return "loopx" + (registryText ? ` --registry ${shellQuote(registryText)}` : "") +
    (runtimeRootText ? ` --runtime-root ${shellQuote(runtimeRootText)}` : "");
}

export function coldPath(
  payload: JsonObject, agentId: string | null, schedulerExecutionArgs: string, capturedDecisionPath?: string,
): JsonObject {
  const goalId = scalarString(payload.goal_id, "quota payload goal_id", "<goal-id>");
  const agentArg = agentId ? ` --agent-id ${agentId}` : "";
  const prefix = commandPrefix(payload.runtime_root, payload.registry);
  return {
    full_decision: capturedDecisionPath ? `cat -- ${shellQuote(capturedDecisionPath)}` : schedulerExecutionArgs
      ? `${prefix} --format json quota should-run --goal-id ${goalId}${agentArg}${schedulerExecutionArgs}`
      : "rerun the typed quota_guard from the current host packet",
    ...(capturedDecisionPath ? {
      captured_decision: {
        path: capturedDecisionPath,
        goal_id: payload.goal_id ?? null,
        agent_id: agentId,
        turn_instance_id: object(payload.heartbeat_receipt).turn_instance_id ?? null,
        source_hash_ref: "$.action_signature.source_decision_hash",
        instruction: "Read this saved observation for omitted context. Check ok, Goal/Agent/Turn and source hash before use. " +
          "It grants no fresh authority: selection, lease/workspace changes, cancellation or quota revalidation require " +
          "the current host guard and a new capture directory. Missing or invalid capture requires recovery, not blind guard replay.",
      },
    } : {}),
    todo_detail: `${prefix} --format json todo list --goal-id ${goalId}`,
    status_detail: `${prefix} --format json status --goal-id ${goalId}`,
  };
}

export function decodeTurnSource(request: JsonObject, inlineField: "payload" | "source" | "context"): JsonObject {
  const transport = requireJsonObject(request.source_transport, "Turn composition source transport");
  const { encoding, data } = transport;
  const maxSourceBytes = 1024 * 1024;
  if (Object.hasOwn(request, inlineField) || encoding !== "deflate-base64-json-v0" ||
      typeof data !== "string" || data.length > maxSourceBytes ||
      data.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(data)) {
    throw new EffectRuntimeRequestError("invalid Turn composition source transport");
  }
  let source: unknown;
  try {
    const compressed = Buffer.from(data, "base64");
    // Inflate may stop at the first stream and silently leave input unread.
    // The synchronous info result reports consumed bytes on supported Node versions.
    const { buffer, engine } = inflateSync(compressed,
      { info: true, maxOutputLength: maxSourceBytes }) as unknown as { buffer: Buffer; engine: { bytesWritten: number } };
    if (engine.bytesWritten !== compressed.length) throw new Error("trailing compressed source data");
    source = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(buffer));
  } catch {
    throw new EffectRuntimeRequestError("Turn composition source must be valid compressed JSON within 1 MiB");
  }
  return requireJsonObject(source, "complete Turn decision source");
}
