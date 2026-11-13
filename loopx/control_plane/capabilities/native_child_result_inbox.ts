/** Durable, owner-private delivery for host-native child results.
 *
 * The public rollout log keeps only typed lifecycle receipts. Result text stays
 * in this local private store and is projected to the parent on a later Turn.
 */
import {createHash} from "node:crypto";
import {constants} from "node:fs";
import {open, readdir} from "node:fs/promises";
import {isAbsolute, join, toNamespacedPath} from "node:path";

import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {atomicWriteJson, atomicWriteText, withFileMutationLock} from "../effect_runtime_io.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString, requireStringLiteral} from "../runtime_decode.ts";

export const NATIVE_CHILD_RESULT_ENTRY_SCHEMA = "native_child_result_entry_v0";
export const NATIVE_CHILD_RESULT_INBOX_SCHEMA = "native_child_result_inbox_v0";

const ID = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$/u;
const RESULT_ID = /^ncr_[0-9a-f]{32}$/u;
const OUTCOMES = ["completed", "failed", "cancelled"] as const;
const DISPOSITIONS = ["accepted", "deferred", "rejected"] as const;
const MAX_CONTENT_BYTES = 4 * 1024 * 1024;
const INLINE_CONTENT_BYTES = 32 * 1024;
const MAX_INLINE_PAGE_BYTES = 128 * 1024;
const MAX_SCAN = 2_000;
const PAGE_SIZE = 20;

function fail(message: string): never { throw new EffectRuntimeRequestError(message); }
function opaque(value: unknown, label: string): string {
  const result = requireNonEmptyString(value, label);
  if (!ID.test(result)) fail(`${label} is invalid`);
  return result;
}
function instant(value: unknown, label: string): string {
  const result = requireNonEmptyString(value, label);
  if (result.length > 80 || !/^\d{4}-\d{2}-\d{2}T[^\s]{1,64}$/u.test(result)) fail(`${label} is invalid`);
  return result;
}
function goalRef(value: unknown, goalId: string): JsonObject | null {
  if (value === null || value === undefined) return null;
  const ref = requireJsonObject(value, "Goal reference");
  if (Object.keys(ref).sort().join(",") !== "goal_id,goal_instance_id"
      || ref.goal_id !== goalId) fail("Goal reference is invalid");
  return {goal_id: goalId, goal_instance_id: opaque(ref.goal_instance_id, "Goal instance")};
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const row = jsonObject(value);
  if (row) return `{${Object.keys(row).sort().map(key => `${JSON.stringify(key)}:${canonical(row[key])}`).join(",")}}`;
  return JSON.stringify(value ?? null);
}
function digest(value: unknown): string { return createHash("sha256").update(canonical(value)).digest("hex"); }
function scope(params: JsonObject): {root: string; goalId: string; agentId: string; goalRef: JsonObject | null} {
  const runtime = requireNonEmptyString(params.runtime_root, "runtime root");
  if (!isAbsolute(runtime)) fail("native child result runtime root must be absolute");
  const goalId = opaque(params.goal_id, "Goal"), agentId = opaque(params.agent_id, "Agent");
  const ref = goalRef(params.goal_ref, goalId);
  const key = digest({goal_ref: ref, goal_id: ref ? null : goalId, agent_id: agentId});
  return {root: toNamespacedPath(join(runtime, ".local", "native-child-result-inbox", key)),
    goalId, agentId, goalRef: ref};
}
function resultIdentity(source: ReturnType<typeof scope>, turn: string, operation: string): string {
  return `ncr_${digest({goal_ref: source.goalRef, goal_id: source.goalRef ? null : source.goalId,
    agent_id: source.agentId, turn_instance_id: turn, operation_id: operation}).slice(0, 32)}`;
}
function metadataPath(root: string, id: string): string { return join(root, `${id}.json`); }
function contentPath(root: string, id: string): string { return join(root, `${id}.txt`); }
async function readJson(path: string): Promise<JsonObject> {
  const handle = await open(path, constants.O_RDONLY | (constants.O_NONBLOCK ?? 0)
    | (constants.O_NOFOLLOW ?? 0));
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.size > 128_000) fail("native child result metadata is invalid");
    const text = await handle.readFile("utf8");
    return requireJsonObject(JSON.parse(text), "native child result metadata");
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof TypeError) fail("native child result metadata is invalid");
    throw error;
  } finally { await handle.close(); }
}
function identityMatches(row: JsonObject, source: ReturnType<typeof scope>): boolean {
  return row.schema_version === NATIVE_CHILD_RESULT_ENTRY_SCHEMA
    && row.goal_id === source.goalId && row.agent_id === source.agentId
    && canonical(row.goal_ref ?? null) === canonical(source.goalRef);
}
async function readEntry(source: ReturnType<typeof scope>, id: string): Promise<JsonObject> {
  if (!RESULT_ID.test(id)) fail("native child result id is invalid");
  const row = await readJson(metadataPath(source.root, id));
  if (!identityMatches(row, source) || row.result_id !== id) fail("native child result scope mismatch");
  const expected = ["acknowledgement", "agent_id", "content_bytes", "content_sha256", "created_at",
    "delivery", "goal_id", "goal_ref", "operation_id", "outcome", "result_id", "schema_version",
    "turn_instance_id"];
  if (Object.keys(row).sort().join(",") !== expected.join(",")) fail("native child result metadata is invalid");
  opaque(row.turn_instance_id, "Turn");
  opaque(row.operation_id, "operation");
  requireStringLiteral(row.outcome, [...OUTCOMES], "native child result outcome");
  if (typeof row.content_sha256 !== "string" || !/^sha256:[0-9a-f]{64}$/u.test(row.content_sha256)
      || !Number.isSafeInteger(row.content_bytes) || Number(row.content_bytes) < 0
      || Number(row.content_bytes) > MAX_CONTENT_BYTES) fail("native child result content identity is invalid");
  instant(row.created_at, "result creation time");
  const delivery = row.delivery === null ? null : jsonObject(row.delivery);
  if (row.delivery !== null && (!delivery
      || Object.keys(delivery).sort().join(",") !== "delivery_count,first_delivered_at,last_delivered_at"
      || !Number.isSafeInteger(delivery.delivery_count) || Number(delivery.delivery_count) < 1
      || Number(delivery.delivery_count) > 1_000_000)) fail("native child result delivery is invalid");
  if (delivery) {
    instant(delivery.first_delivered_at, "first delivery time");
    instant(delivery.last_delivered_at, "last delivery time");
  }
  const acknowledgement = row.acknowledgement === null ? null : jsonObject(row.acknowledgement);
  if (row.acknowledgement !== null && (!acknowledgement
      || Object.keys(acknowledgement).sort().join(",") !== "acknowledged_at,disposition")) {
    fail("native child result acknowledgement is invalid");
  }
  if (acknowledgement) {
    if (!delivery) fail("native child result acknowledgement has no delivery");
    instant(acknowledgement.acknowledged_at, "acknowledgement time");
    requireStringLiteral(acknowledgement.disposition, [...DISPOSITIONS], "native child result disposition");
  }
  return row;
}
async function readContent(path: string): Promise<string> {
  const handle = await open(path, constants.O_RDONLY | (constants.O_NONBLOCK ?? 0)
    | (constants.O_NOFOLLOW ?? 0));
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.size > MAX_CONTENT_BYTES) fail("native child result content is invalid");
    return await handle.readFile("utf8");
  } finally { await handle.close(); }
}
async function ingest(params: JsonObject): Promise<JsonObject> {
  const source = scope(params);
  const turn = opaque(params.turn_instance_id, "Turn"), operation = opaque(params.operation_id, "operation");
  const outcome = requireStringLiteral(params.outcome, [...OUTCOMES], "native child result outcome");
  const observedAt = instant(params.observed_at, "result observation time");
  if (typeof params.text !== "string") fail("native child result text is required");
  const text = params.text;
  if (/\p{Surrogate}/u.test(text)) fail("native child result text encoding is invalid");
  const bytes = Buffer.byteLength(text, "utf8");
  if (bytes > MAX_CONTENT_BYTES) fail("native child result text is too large");
  const id = resultIdentity(source, turn, operation), path = metadataPath(source.root, id);
  const contentSha256 = `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;
  return withFileMutationLock(path, async () => {
    let existing: JsonObject | null = null;
    try { existing = await readEntry(source, id); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    const identity: JsonObject = {schema_version: NATIVE_CHILD_RESULT_ENTRY_SCHEMA, result_id: id,
      goal_id: source.goalId, agent_id: source.agentId, goal_ref: source.goalRef,
      turn_instance_id: turn, operation_id: operation, outcome,
      content_sha256: contentSha256, content_bytes: bytes};
    if (existing) {
      if (Object.entries(identity).some(([key, value]) => canonical(existing![key]) !== canonical(value))) {
        fail("native child result identity already has conflicting content");
      }
      return {schema_version: NATIVE_CHILD_RESULT_INBOX_SCHEMA, ok: true, result_id: id,
        created: false, replay: true};
    }
    await atomicWriteText(contentPath(source.root, id), text);
    await atomicWriteJson(path, {...identity, created_at: observedAt, delivery: null, acknowledgement: null});
    return {schema_version: NATIVE_CHILD_RESULT_INBOX_SCHEMA, ok: true, result_id: id,
      created: true, replay: false};
  });
}
async function listEntries(params: JsonObject): Promise<JsonObject> {
  const source = scope(params);
  let names: string[] = [];
  try { names = (await readdir(source.root)).filter(name => /^ncr_[0-9a-f]{32}\.json$/u.test(name)).sort(); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  const scanComplete = names.length <= MAX_SCAN;
  names = names.slice(0, MAX_SCAN);
  const rows: JsonObject[] = [], unavailable: string[] = [];
  for (const name of names) {
    const id = name.slice(0, -5);
    try {
      const row = await readEntry(source, id);
      if (row.acknowledgement === null) rows.push(row);
    } catch { unavailable.push(id); }
  }
  rows.sort((left, right) => Number(left.delivery !== null) - Number(right.delivery !== null)
    || String(left.created_at).localeCompare(String(right.created_at)) || String(left.result_id).localeCompare(String(right.result_id)));
  const items: JsonObject[] = [];
  let inlineBytes = 0;
  let inspected = 0;
  for (const row of rows) {
    if (items.length >= PAGE_SIZE) break;
    inspected += 1;
    const file = contentPath(source.root, String(row.result_id));
    const base: JsonObject = Object.fromEntries(["result_id", "turn_instance_id", "operation_id", "outcome",
      "content_sha256", "content_bytes", "created_at", "delivery"].map(key => [key, row[key]]));
    const size = Number(row.content_bytes);
    try {
      if (Number.isSafeInteger(size) && size >= 0 && size <= INLINE_CONTENT_BYTES
          && inlineBytes + size <= MAX_INLINE_PAGE_BYTES) {
        const text = await readContent(file);
        if (`sha256:${createHash("sha256").update(text, "utf8").digest("hex")}` !== row.content_sha256) {
          unavailable.push(String(row.result_id)); continue;
        }
        base.text = text; inlineBytes += size;
      } else {
        const text = await readContent(file);
        if (Buffer.byteLength(text, "utf8") !== size
            || `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}` !== row.content_sha256) {
          unavailable.push(String(row.result_id)); continue;
        }
        base.content_file = file;
      }
    } catch {
        unavailable.push(String(row.result_id)); continue;
    }
    items.push(base);
  }
  return {schema_version: NATIVE_CHILD_RESULT_INBOX_SCHEMA, ok: true, items,
    has_more: !scanComplete || inspected < rows.length, pending_count: rows.length,
    scan_complete: scanComplete,
    unavailable_count: unavailable.length};
}
async function markDelivered(params: JsonObject): Promise<JsonObject> {
  const source = scope(params), deliveredAt = instant(params.observed_at, "delivery time");
  if (!Array.isArray(params.result_ids) || params.result_ids.length > PAGE_SIZE) fail("bounded native child result ids are required");
  if (new Set(params.result_ids.map(id => requireNonEmptyString(id, "native child result id"))).size
      !== params.result_ids.length) fail("native child result ids must be unique");
  for (const raw of params.result_ids) {
    const id = requireNonEmptyString(raw, "native child result id"), path = metadataPath(source.root, id);
    await withFileMutationLock(path, async () => {
      const row = await readEntry(source, id), delivery = jsonObject(row.delivery);
      await atomicWriteJson(path, {...row, delivery: {first_delivered_at: delivery?.first_delivered_at ?? deliveredAt,
        last_delivered_at: deliveredAt, delivery_count: Math.min(Number(delivery?.delivery_count ?? 0) + 1, 1_000_000)}});
    });
  }
  return {schema_version: NATIVE_CHILD_RESULT_INBOX_SCHEMA, ok: true, delivered_count: params.result_ids.length};
}
async function acknowledge(params: JsonObject): Promise<JsonObject> {
  const source = scope(params), id = requireNonEmptyString(params.result_id, "native child result id");
  const turn = opaque(params.turn_instance_id, "Turn");
  const acknowledgedAt = instant(params.observed_at, "acknowledgement time");
  const disposition = requireStringLiteral(params.disposition, [...DISPOSITIONS], "native child result disposition");
  const path = metadataPath(source.root, id);
  return withFileMutationLock(path, async () => {
    const row = await readEntry(source, id), prior = jsonObject(row.acknowledgement);
    if (row.turn_instance_id !== turn) fail("native child result Turn identity mismatch");
    if (!jsonObject(row.delivery)) fail("native child result must be delivered before acknowledgement");
    if (prior && prior.disposition !== disposition) fail("native child result already has a conflicting acknowledgement");
    if (!prior) await atomicWriteJson(path, {...row, acknowledgement: {acknowledged_at: acknowledgedAt, disposition}});
    return {schema_version: NATIVE_CHILD_RESULT_INBOX_SCHEMA, ok: true, result_id: id,
      disposition, replay: prior !== null};
  });
}

export async function nativeChildResultInbox(params: JsonObject): Promise<JsonObject> {
  const operation = requireStringLiteral(params.operation,
    ["ingest", "list", "mark_delivered", "acknowledge"], "native child result inbox operation");
  if (operation === "ingest") return ingest(params);
  if (operation === "list") return listEntries(params);
  if (operation === "mark_delivered") return markDelivered(params);
  return acknowledge(params);
}
