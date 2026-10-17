/** Goal transfer composition for original closed settlement content.
 *
 * Journal and quota owners interpret their own bytes. This private carrier
 * keeps original receipts/paths intact; it never installs Host/session records,
 * active accounting, or target execution authority. Destination admission must
 * consume this history rather than interpret an empty live run index as zero.
 */
import {constants} from "node:fs";
import {copyFile, lstat, mkdir, mkdtemp, open, readdir, rm, rmdir} from "node:fs/promises";
import {dirname, isAbsolute, join, resolve} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {durableWriteJson, publishNewDirectory} from "../effect_runtime_io.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys, requireAuthorityStoreId} from "../coordination/authority_store_codec.ts";
import {readGoalTurnJournalsForTransfer} from "../turn_driver/turn_journal_query.ts";
import {readGoalQuotaHistoryForTransfer} from "../quota/accounting_artifact_transaction.ts";
import {inspectTransferFile, requirePhysicalTransferPath, type TransferContainerFile} from "./planned_transfer_container.ts";

const HISTORY_SCHEMA = "loopx_planned_transfer_runtime_history_v0";
export interface GoalTransferHistoryFile extends JsonObject {relative_path: string; sha256: string; bytes: number}
export interface GoalTransferHistory extends JsonObject {
  schema_version: typeof HISTORY_SCHEMA; goal_id: string; source_runtime_root: string;
  turn_history: JsonObject; quota_history: JsonObject; files: GoalTransferHistoryFile[];
}
export interface ProtectedGoalTransferHistory {manifest: GoalTransferHistory; files: TransferContainerFile[]}
function reject(message: string): never {throw new EffectRuntimeConflictError(message);}
function same(a: unknown, b: unknown): boolean {return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b));}

export async function readGoalTransferHistoryJson(path: string): Promise<JsonObject> {
  await requirePhysicalTransferPath(path);
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.size > 16 * 1024 * 1024) reject("Runtime history requires bounded regular metadata");
    const bytes = await handle.readFile();
    if (bytes.length > 16 * 1024 * 1024) reject("Runtime history metadata grew beyond its local capacity");
    return requireJsonObject(JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes)), "runtime history metadata");
  } finally {await handle.close();}
}

/** These are addresses already selected by their owners, not a runtime sweep.
 * The closed set is local to this private transfer carrier; runtime semantics
 * remain with the original journal and accounting vocabularies. */
export function decodeGoalTransferHistory(raw: unknown): GoalTransferHistory {
  const value = requireJsonObject(raw, "original runtime history");
  if (!hasExactAuthorityKeys(value, ["schema_version", "goal_id", "source_runtime_root", "turn_history", "quota_history", "files"]) ||
      value.schema_version !== HISTORY_SCHEMA || typeof value.source_runtime_root !== "string" ||
      !isAbsolute(value.source_runtime_root) || resolve(value.source_runtime_root) !== value.source_runtime_root ||
      value.source_runtime_root.includes("\0") || !Array.isArray(value.files) || value.files.length > 20_000) reject("Original runtime history is invalid or unsupported");
  const goal = requireAuthorityStoreId(value.goal_id, "original runtime Goal");
  if (/[\\/\0]/u.test(goal) || goal === "." || goal === "..") reject("Original runtime Goal must be one path segment");
  const turns = requireJsonObject(value.turn_history, "original Turn history"), quota = requireJsonObject(value.quota_history, "original quota history");
  if (turns.closed !== true || turns.turn_hosts_closed !== true || quota.closed !== true || !Array.isArray(turns.files) || !Array.isArray(quota.files)) reject("Original settlement history must be closed by its source owners");
  let previous = "";
  for (const rawFile of value.files) {
    const file = requireJsonObject(rawFile, "original runtime file");
    if (!hasExactAuthorityKeys(file, ["relative_path", "sha256", "bytes"]) || typeof file.relative_path !== "string" ||
        !( /^turns\/[a-f0-9]{64}\.json$/u.test(file.relative_path) ||
           /^runs\/(?:index\.jsonl|\.transactions\/quota-(?:spend|void)\/[a-f0-9]{24}\.json|[0-9A-Za-z-]+\.(?:json|md))$/u.test(file.relative_path)) ||
        file.relative_path <= previous || !BARE_SHA256_PATTERN.test(String(file.sha256)) ||
        typeof file.bytes !== "number" || !Number.isSafeInteger(file.bytes) || file.bytes < 0 || file.bytes > 128 * 1024 * 1024) reject("Original runtime file address or digest is invalid");
    previous = file.relative_path;
  }
  const owned = [...turns.files, ...quota.files].sort((a, b) => String((a as JsonObject).relative_path) < String((b as JsonObject).relative_path) ? -1 : 1);
  if (!same(owned, value.files)) reject("Runtime content differs from its owning inventories");
  return value as GoalTransferHistory;
}

export function goalTransferHistory(root: string, goal: string, turns: JsonObject, quota: JsonObject): GoalTransferHistory {
  return decodeGoalTransferHistory({schema_version: HISTORY_SCHEMA, goal_id: goal, source_runtime_root: root,
    turn_history: turns, quota_history: quota,
    files: [...turns.files as JsonObject[], ...quota.files as JsonObject[]].sort((a, b) => String(a.relative_path) < String(b.relative_path) ? -1 : 1)});
}

export async function goalTransferHistoryInputs(history: GoalTransferHistory): Promise<TransferContainerFile[]> {
  const result: TransferContainerFile[] = [];
  for (const file of history.files) {
    const path = join(history.source_runtime_root, "goals", history.goal_id, file.relative_path);
    const observed = await inspectTransferFile(path);
    if (!same(observed, {sha256: file.sha256, bytes: file.bytes})) reject("Original runtime history changed before export");
    result.push({path, ...observed});
  }
  return result;
}

async function physicalFiles(directory: string, allowedDirectories: ReadonlySet<string>, suffix = ""): Promise<string[]> {
  const result: string[] = [];
  for (const entry of await readdir(join(directory, suffix), {withFileTypes: true})) {
    const path = suffix ? `${suffix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      if (!allowedDirectories.has(path)) reject("Retained runtime history contains unsupported later content");
      result.push(...await physicalFiles(directory, allowedDirectories, path));
    }
    else if (entry.isFile()) result.push(path);
    else reject("Retained runtime history contains a linked or unsupported entry");
  }
  return result.sort();
}

/** Reinterpret the original content on the target. The quota owner validates
 * source addresses but opens only corresponding retained local bytes; no PID
 * is probed and no historical receipt or source path is rewritten. */
export async function inspectGoalTransferHistory(directory: string, raw: unknown): Promise<JsonObject> {
  const history = decodeGoalTransferHistory(raw);
  await requirePhysicalTransferPath(directory);
  if (!(await lstat(directory)).isDirectory()) reject("Retained runtime history is unavailable");
  const manifest = await readGoalTransferHistoryJson(join(directory, "manifest.json"));
  if (!same(manifest, history)) reject("Retained runtime history manifest changed");
  const expected = ["manifest.json", ...history.files.map(file => `goals/${history.goal_id}/${file.relative_path}`)].sort();
  const allowedDirectories = new Set<string>();
  for (const path of expected) {
    for (let parent = dirname(path); parent !== "."; parent = dirname(parent)) allowedDirectories.add(parent);
  }
  if (!same(await physicalFiles(directory, allowedDirectories), expected)) reject("Retained runtime history is missing or contains later content; recover its original operation");
  for (const file of history.files) {
    const observed = await inspectTransferFile(join(directory, "goals", history.goal_id, file.relative_path));
    if (!same(observed, {sha256: file.sha256, bytes: file.bytes})) reject("Retained runtime history changed; preserve later work");
  }
  const turns = await readGoalTurnJournalsForTransfer(directory, history.goal_id);
  if (!same(turns, {turns: history.turn_history.turns, files: history.turn_history.files, closed: true})) reject("Original Turn history did not pass its journal owner at the destination");
  const quota = await readGoalQuotaHistoryForTransfer(history.source_runtime_root, history.goal_id, directory);
  if (!same(quota, history.quota_history)) reject("Original quota history did not pass its accounting owner at the destination");
  return {history_sha256: canonicalAuthoritySha256(history), turn_history: turns, quota_history: quota,
    historical_only: true, runtime_accounting_adopted: false, execution_authority_granted: false};
}

async function syncDirectory(path: string): Promise<void> {
  const handle = await open(path, constants.O_RDONLY);
  try {await handle.sync();} finally {await handle.close();}
}

/** Publish one complete immutable history, or read back the same operation.
 * No missing/modified completed history is recreated from inbound bytes. The
 * only unfinished publication we may remove is our exact empty reservation,
 * bound to the retained intent and original pending transfer under maintenance. */
export async function retainGoalTransferHistory(operationDirectory: string, input: ProtectedGoalTransferHistory,
  pending: boolean, completed: boolean, execute: boolean): Promise<JsonObject | null> {
  const history = decodeGoalTransferHistory(input.manifest), directory = join(operationDirectory, "runtime-history");
  if (input.files.length !== history.files.length) reject("Authenticated runtime content inventory differs");
  await requirePhysicalTransferPath(directory);
  let exists = false;
  try {await lstat(directory); exists = true;} catch (error) {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;}
  const intentPath = join(operationDirectory, "runtime-history-intent.json");
  const intent = {history_sha256: canonicalAuthoritySha256(history)};
  let originalIntent: unknown = null;
  try {originalIntent = await readGoalTransferHistoryJson(intentPath);} catch (error) {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;}
  if (originalIntent !== null && !same(originalIntent, intent)) reject("Original runtime history publication changed");
  let emptyReservation = false;
  if (exists) {
    if (!pending || !same(originalIntent, intent)) reject("Destination runtime history is occupied");
    emptyReservation = !completed && (await readdir(directory)).length === 0;
    if (!emptyReservation) return inspectGoalTransferHistory(directory, history);
  }
  if (completed) reject("Completed runtime history is missing; recover the original operation");
  if (!execute) return null;
  await durableWriteJson(intentPath, intent);
  const temporary = await mkdtemp(join(operationDirectory, ".runtime-history-"));
  try {
    const directories = new Set<string>([temporary]);
    for (const [index, file] of history.files.entries()) {
      const source = input.files[index]!, target = join(temporary, "goals", history.goal_id, file.relative_path);
      if (source.sha256 !== file.sha256 || source.bytes !== file.bytes) reject("Authenticated runtime content identity differs");
      await requirePhysicalTransferPath(source.path);
      await mkdir(dirname(target), {recursive: true, mode: 0o700});
      await copyFile(source.path, target, constants.COPYFILE_EXCL);
      const handle = await open(target, constants.O_RDONLY | constants.O_NOFOLLOW);
      try {await handle.sync();} finally {await handle.close();}
      for (let parent = dirname(target); parent !== temporary; parent = dirname(parent)) directories.add(parent);
    }
    await durableWriteJson(join(temporary, "manifest.json"), history);
    const observed = await inspectGoalTransferHistory(temporary, history);
    for (const path of [...directories].sort((a, b) => b.length - a.length)) await syncDirectory(path);
    if (emptyReservation) await rmdir(directory);
    await publishNewDirectory(temporary, directory);
    await syncDirectory(operationDirectory);
    await inspectGoalTransferHistory(directory, history);
    return observed;
  } finally {await rm(temporary, {recursive: true, force: true});}
}
