/** Original work selected by canonical completion or a strict R index.
 * Private content is prepared by the IO adapter; content alone grants nothing. */
import {createHash} from "node:crypto";
import {execFileSync} from "node:child_process";
import {lstatSync, readFileSync} from "node:fs";
import {isAbsolute, join} from "node:path";
import {fileURLToPath} from "node:url";
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {goalPathSegment} from "../rollout_receipt_log.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {indexCoordinationProjection, validateCoordinationTodoReadModel} from "../coordination/coordination_projection.ts";
import {openRuntimeAuthorityStore, requireLocalAuthorityRuntimeRoot,
  type LocalAuthorityProviderDependencies} from "../coordination/local_authority_provider.ts";
import {evaluateResultWorkBinding, originalWorkReceiptDigest} from "./checkpoint_read_context.ts";

function unknown(message: string): never {
  throw new EffectRuntimeRequestError(message, "checkpoint_commit_unknown");
}
/** The hashed read identity is only a locator. The canonical digest selects content. */
export function workReceiptWitnessPath(root: string, identity: JsonObject, readId: unknown): string {
  const read = requireNonEmptyString(readId, "original work read identity");
  const effect = requireNonEmptyString(identity.effect_id, "original work effect identity");
  const directory = join(root, "goals", goalPathSegment(identity.goal_id), "checkpoint-contexts", "work-witnesses");
  const entry = lstatSync(directory);
  if (!entry.isDirectory() || entry.isSymbolicLink()) unknown("Original work witness directory changed");
  return join(directory, createHash("sha256").update(effect + ":work-witness:" + read).digest("hex") + ".json");
}
export function readWorkReceiptWitness(root: string, identity: JsonObject, digest: unknown, readId: unknown): JsonObject {
  if (typeof digest !== "string" || !BARE_SHA256_PATTERN.test(digest)) unknown("Original work digest is unavailable");
  const path = workReceiptWitnessPath(root, identity, readId), entry = lstatSync(path);
  if (!entry.isFile() || entry.isSymbolicLink()) unknown("Original work witness is not a regular private file");
  const receipt = requireJsonObject(JSON.parse(readFileSync(path, "utf8")), "original work witness");
  if (receipt.result_only !== true || receipt.read_context_id !== readId || originalWorkReceiptDigest(receipt) !== digest ||
      evaluateResultWorkBinding({phase: "restore", identity, receipt}).ok !== true) {
    unknown("Original work witness identity, basis or digest changed");
  }
  return receipt;
}
/** Windows DACL enforcement stays in the existing OS-specific private IO owner.
 * The executable and package root are runtime-owned; no caller command is run. */
export function writeWindowsWorkReceipt(path: string, receipt: JsonObject): void {
  const python = process.env.LOOPX_EFFECT_RUNTIME_PYTHON_BIN;
  if (!python || !isAbsolute(python)) unknown("Private Windows work receipt IO is unavailable");
  execFileSync(python, ["-P", "-m", "loopx.control_plane.runtime.document_io", "--write-private-json", path], {
    input: JSON.stringify(receipt), encoding: "utf8", timeout: 5000, windowsHide: true, maxBuffer: 4096,
    env: {...process.env, PYTHONPATH: fileURLToPath(new URL("../../../", import.meta.url))},
  });
}

async function originalCompletionAnchor(root: string, identity: JsonObject,
  dependencies: LocalAuthorityProviderDependencies,
): Promise<JsonObject | null> {
  if (typeof identity.todo_id !== "string") return null;
  const goal = goalPathSegment(identity.goal_id);
  const store = await openRuntimeAuthorityStore(root, goal, dependencies, {existingOnly: true});
  const head = await store.loadAuthority();
  if (head.status !== "loaded") unknown("Canonical original completion is unavailable");
  validateCoordinationTodoReadModel(head.head, goal);
  const todo = indexCoordinationProjection(head.head, goal).todos.get(identity.todo_id);
  if (!todo) unknown("Original Todo is unavailable");
  if (todo.status !== "done") return null;
  if (![identity.effect_id, identity.turn_instance_id].includes(String(todo.completion_turn_key))) unknown("Original completion belongs to another Turn");
  const {completionTurnOperationId, COORDINATION_TODO_TERMINAL_LIFECYCLE_RECEIPT_SCHEMA} = await import("../coordination/todo_terminal_lifecycle.ts");
  const operation = completionTurnOperationId({goal_id: goal, todo_id: identity.todo_id, requested_completion_turn_key: String(todo.completion_turn_key)}, false);
  const saved = await store.readReceipt(operation);
  if (saved.status !== "found" || saved.receipts.length !== 1) unknown("Original completion receipt is unavailable");
  const anchor = saved.receipts[0];
  if (anchor.schema_version !== COORDINATION_TODO_TERMINAL_LIFECYCLE_RECEIPT_SCHEMA || anchor.operation_id !== operation ||
      anchor.goal_id !== goal || anchor.todo_id !== identity.todo_id || anchor.command !== "complete") unknown("Original completion receipt identity changed");
  const storeIdentity = await store.storeIdentity();
  if (storeIdentity.status !== "available") unknown("Original work provider identity is unavailable");
  return {anchor, store_identity: storeIdentity.store_identity};
}
/** Runs before the checkpoint provider fence. The final existing basis check
 * still owns CAS; no provider transaction nests this immutable receipt IO. */
export async function verifyCompletedWorkReceipt(root: string, identity: JsonObject, receipt: JsonObject,
  dependencies: LocalAuthorityProviderDependencies,
): Promise<void> {
  const selected = await originalCompletionAnchor(root, identity, dependencies);
  if (selected === null) return;
  const anchor = requireJsonObject(selected.anchor, "original completion anchor");
  // An intact legacy read retains its existing path. Missing legacy commitments
  // cannot restore lost content or enroll a replacement work witness.
  if (anchor.result_work_receipt_sha256 === undefined) return;
  const source = requireJsonObject(requireJsonObject(receipt.work_basis, "work basis").source, "work source");
  if (originalWorkReceiptDigest(receipt) !== anchor.result_work_receipt_sha256 ||
      receipt.read_context_id !== anchor.delivery_read_context_id || source.store_identity !== selected.store_identity) {
    unknown("Primary work differs from its original completion commitment");
  }
}

/** Read authority only; the adapter holds source locks and privately persists
 * the exact selected bytes without minting a token or rerunning validation. */
export async function recoverOriginalWorkReceipt(request: JsonObject,
  dependencies: LocalAuthorityProviderDependencies,
): Promise<JsonObject> {
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const identity = requireJsonObject(request.identity, "original work identity"), goal = goalPathSegment(identity.goal_id);
  const {readCheckpointHistoryRows, inspectCheckpointReplayFromRows} = await import("./checkpoint_commit.ts");
  const rows = readCheckpointHistoryRows(root, goal);
  const indexed = rows.filter(row => jsonObject(row.settlement_identity)?.effect_id === identity.effect_id && row.result_work_context != null);
  const replayIndex = request.replay_index_sha256;
  if (replayIndex !== undefined && (indexed.length !== 1 ||
      replayIndex !== canonicalAuthoritySha256(indexed[0]))) {
    unknown("Original result index no longer matches the accepted direction");
  }
  let receipt: JsonObject;
  if (indexed.length) {
    if (indexed.length !== 1 || canonicalAuthoritySha256(indexed[0].settlement_identity) !== canonicalAuthoritySha256(identity)) unknown("Original result index is ambiguous");
    const result = inspectCheckpointReplayFromRows({runtime_root: root, goal_id: goal, prior: indexed[0], include_retained_binding: true}, rows);
    receipt = requireJsonObject(result.retained_work_receipt, "indexed original work receipt");
  } else {
    const selected = await originalCompletionAnchor(root, identity, dependencies);
    if (selected === null) unknown("Original completed Todo is unavailable");
    const anchor = requireJsonObject(selected.anchor, "original completion anchor");
    receipt = readWorkReceiptWitness(root, identity, anchor.result_work_receipt_sha256, anchor.delivery_read_context_id);
    const source = requireJsonObject(requireJsonObject(receipt.work_basis, "work basis").source, "work source");
    if (source.store_identity !== selected.store_identity) unknown("Original work provider incarnation changed");
  }
  if (Object.hasOwn(request, "goal_ref") && canonicalAuthoritySha256(jsonObject(jsonObject(receipt.work_basis)?.source)?.goal_ref ?? null) !== canonicalAuthoritySha256(request.goal_ref)) unknown("Original work belongs to another Goal instance");
  if (replayIndex !== undefined) {
    const original = indexed[0];
    return {ok: true, replay_result: {
      classification: original.classification,
      json_path: original.json_path,
      markdown_path: original.markdown_path,
      result_work_context: original.result_work_context,
      result_artifact_digests: original.result_artifact_digests,
    }};
  }
  return {ok: true, receipt};
}
