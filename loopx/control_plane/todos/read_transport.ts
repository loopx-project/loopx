/** Python transport only; native callers consume readTodo in process. */
import {createHash} from "node:crypto";
import {readTodo, TODO_READ_REQUEST, TODO_READ_RESULT} from "./read_projection.ts";
import type {JsonObject} from "../effect_program.ts";
import {MAX_LOCAL_SNAPSHOT_BYTES, openPrivateSnapshotSource} from "../effect_runtime_snapshot.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";

const completeBytes = MAX_LOCAL_SNAPSHOT_BYTES - 2 * 1024 * 1024;
const pieceBytes = Math.floor(completeBytes / 4) * 3;

async function sourceFacts(value: unknown): Promise<JsonObject> {
  const ref = requireJsonObject(value, "Todo fact source reference");
  const digest = requireNonEmptyString(ref.sha256, "Todo fact source digest"), size = ref.byte_count;
  if (!BARE_SHA256_PATTERN.test(digest) || typeof size !== "number" || !Number.isSafeInteger(size) || size <= 0) {
    throw new EffectRuntimeRequestError("invalid complete Todo fact source");
  }
  const file = await openPrivateSnapshotSource(ref.path);
  try {
    const before = await file.stat();
    if (before.size !== size) throw new Error("Todo source size changed");
    const bytes = await file.readFile(), after = await file.stat();
    if (bytes.length !== size || after.size !== size || before.ino !== after.ino || before.dev !== after.dev ||
        createHash("sha256").update(bytes).digest("hex") !== digest) throw new Error("Todo source identity changed");
    return requireJsonObject(JSON.parse(new TextDecoder("utf-8", {fatal: true, ignoreBOM: true}).decode(bytes)), "Todo source facts");
  } finally {await file.close();}
}

export async function readTodoRuntime(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "Todo read request");
  if (request.schema_version !== TODO_READ_REQUEST) throw new EffectRuntimeRequestError("Todo read request schema mismatch");
  let input = request;
  if (request.facts_ref !== undefined) {
    try {input = await sourceFacts(request.facts_ref);}
    catch {throw new EffectRuntimeRequestError("complete Todo fact source unavailable or invalid; repeat the original read");}
    if (input.facts_ref !== undefined || input.schema_version !== TODO_READ_REQUEST) throw new EffectRuntimeRequestError("invalid Todo fact source envelope");
  }
  const page = request.result_page == null ? null : requireJsonObject(request.result_page, "Todo result page");
  const observed = page === null ? new Date().toISOString() : requireNonEmptyString(page.observed_at, "original read observation");
  const clock = Date.parse(observed);
  if (!Number.isFinite(clock) || clock > Date.now()) throw new EffectRuntimeRequestError("invalid original Todo read observation");
  let result = await readTodo(input, observed);
  if (result.rejection || (result.canonical && !result.fields && !result.payload)) return result;
  if (input.kind === "group") {
    const {items, evaluations, orchestration, ...projection} = result;
    result = {...projection, selected_count: (items as unknown[]).length,
      ...(input.include_full_source === true ? {items, evaluations} : {}),
      ...(input.include_task_orchestration_authority === true ? {orchestration} : {})};
  }
  const bytes = Buffer.from(JSON.stringify(result), "utf8");
  if (page === null && bytes.length <= completeBytes) return result;
  const digest = createHash("sha256").update(bytes).digest("hex");
  const offset = page === null ? 0 : page.offset;
  if (typeof offset !== "number" || !Number.isSafeInteger(offset) || offset < 0 || offset >= bytes.length || offset % pieceBytes !== 0 ||
      (page !== null && page.sha256 !== digest)) {
    return {schema_version: TODO_READ_RESULT, canonical: {status: "failed", reason_code: "canonical_snapshot_changed",
      reason: "Todo read result changed; restart the complete read, never append a newer page",
      source_authority: "unknown_fail_closed", decision_read_from_provider: false, legacy_fallback_used: false}};
  }
  const end = Math.min(bytes.length, offset + pieceBytes);
  return {schema_version: TODO_READ_RESULT, result_piece: {offset, byte_count: bytes.length, sha256: digest,
    data: bytes.subarray(offset, end).toString("base64"),
    next: end === bytes.length ? null : {offset: end, sha256: digest, observed_at: observed}}};
}
