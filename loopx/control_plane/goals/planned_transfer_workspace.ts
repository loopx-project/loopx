/** Reviewed material placement for the stopped Goal transfer. This owns only
 * selected workspace files, never Goal admission or authority. Credential policy
 * remains with its owner; selected bytes are not privacy-certified here. */
import {constants} from "node:fs";
import {copyFile, mkdir, open, readFile} from "node:fs/promises";
import {dirname, isAbsolute, join, relative, resolve, sep} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {durableWriteJson} from "../effect_runtime_io.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys} from "../coordination/authority_store_codec.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {inspectTransferFile, requirePhysicalTransferPath} from "./planned_transfer_container.ts";
import type {ProtectedGoalMaterial} from "./planned_transfer.ts";

interface WorkspaceMaterial {source: ProtectedGoalMaterial; destination: string; relativePath: string}
export interface GoalTransferWorkspacePlan {materials: WorkspaceMaterial[]; destinations: JsonObject[]}
type MaterialPresence = "missing" | "matched" | "occupied";
interface MaterialObservation extends JsonObject {id: string; relative_path: string; presence: MaterialPresence; sha256: string | null; bytes: number | null}
interface WorkspaceReview extends JsonObject {preview_sha256: string; ready: boolean; materials: MaterialObservation[]}
function reject(message: string): never { throw new EffectRuntimeConflictError(message); }
function same(a: unknown, b: unknown): boolean { return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b)); }
function beneath(root: string, path: string): boolean {
  const suffix = relative(root, path);
  return suffix === "" || (!isAbsolute(suffix) && suffix !== ".." && !suffix.startsWith(`..${sep}`));
}
async function optionalRecord(path: string): Promise<JsonObject | null> {
  await requirePhysicalTransferPath(path);
  try { return requireJsonObject(JSON.parse(await readFile(path, "utf8")), "original material placement"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
}
async function retainRecord(path: string, value: JsonObject): Promise<void> {
  const prior = await optionalRecord(path);
  if (prior) { if (!same(prior, value)) reject("Material placement belongs to another original operation"); return; }
  await mkdir(dirname(path), {recursive: true, mode: 0o700});
  await durableWriteJson(path, value);
  if (!same(await optionalRecord(path), value)) reject("Material placement receipt readback failed");
}

/** A null mapping keeps original relative names. An explicit mapping must
 * account for every selected member; no basename guessing or silent omission.
 * Reserved control state stays with its existing owner, outside this operation. */
export function planGoalTransferWorkspace(project: string, runtime: string,
  materials: ProtectedGoalMaterial[], raw: unknown): GoalTransferWorkspacePlan {
  if (process.platform === "win32") reject("Workspace transfer requires the local POSIX profile");
  const mapping = raw === null ? Object.fromEntries(materials.map(row => [row.id, row.source_relative_path])) : requireJsonObject(raw, "material destinations");
  if (!hasExactAuthorityKeys(mapping, materials.map(row => row.id))) reject("Review a destination for every selected material");
  const planned = materials.map(source => {
    const destination = mapping[source.id];
    if (typeof destination !== "string" || !destination || destination.includes("\0") || isAbsolute(destination) ||
        destination.split(sep).some(part => part === "" || part === "." || part === "..")) reject("Material destination must be a normalized relative workspace path");
    const path = resolve(project, destination);
    if (!beneath(project, path) || path === project || beneath(runtime, path) ||
        beneath(join(project, ".git"), path) || beneath(join(project, ".loopx"), path)) reject("Material destination overlaps runtime, registry or repository control state");
    return {source, destination: path, relativePath: destination};
  });
  const paths = new Set(planned.map(row => row.destination));
  if (paths.size !== planned.length) reject("Material destinations collide");
  for (const path of paths) {
    for (let parent = dirname(path); parent !== project; parent = dirname(parent)) {
      if (paths.has(parent)) reject("Material destinations contain one another");
    }
  }
  return {materials: planned, destinations: planned.map(row => ({id: row.source.id, relative_path: row.relativePath}))};
}

export async function reviewGoalTransferWorkspace(plan: GoalTransferWorkspacePlan, artifact: string,
  target: JsonObject, witness: JsonObject): Promise<WorkspaceReview> {
  const materials: MaterialObservation[] = [];
  for (const row of plan.materials) {
    await requirePhysicalTransferPath(row.destination);
    let observed: Awaited<ReturnType<typeof inspectTransferFile>> | null = null;
    try { observed = await inspectTransferFile(row.destination); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    const presence: MaterialPresence = !observed ? "missing"
      : observed.sha256 === row.source.sha256 && observed.bytes === row.source.bytes ? "matched" : "occupied";
    materials.push({id: row.source.id, relative_path: row.relativePath, presence,
      sha256: observed?.sha256 ?? null, bytes: observed?.bytes ?? null});
  }
  const facts = {schema_version: "loopx_planned_transfer_workspace_preview_v0", artifact_sha256: artifact,
    target, registry_witness: witness, materials};
  return {...facts, ready: materials.every(row => row.presence !== "occupied"), preview_sha256: canonicalAuthoritySha256(facts)};
}

/** A retained review binds the original operation, not a fresh confirmation on
 * each retry. Publishing a file is preceded by an intent. A missing file after
 * that intent is ambiguous and held, never recreated over a later deletion. */
export async function verifyGoalTransferWorkspaceReview(plan: GoalTransferWorkspacePlan, review: WorkspaceReview,
  directory: string, identity: JsonObject, expected: unknown, completed: boolean): Promise<void> {
  if (typeof expected !== "string" || !BARE_SHA256_PATTERN.test(expected)) reject("Workspace material placement requires its reviewed target preview");
  const original = await optionalRecord(join(directory, "workspace-review.json"));
  const binding = {identity_sha256: canonicalAuthoritySha256(identity), preview_sha256: expected};
  if (original ? !same(original, binding) : review.preview_sha256 !== expected) reject("Destination material preview changed; review the current target before staging");
  for (const [index, row] of plan.materials.entries()) {
    const observed = review.materials[index]!;
    if (observed.presence === "occupied") reject("Destination workspace material changed or is occupied; preserve it");
    const intent = await optionalRecord(join(directory, "workspace-materials", `${row.source.id}.json`));
    if (completed && !intent) reject("Completed workspace material intent is missing; recover the original stage");
    if (intent && !same(intent, materialIntent(row))) reject("Destination material intent differs from the original operation");
    if (intent && observed.presence === "missing") reject("Previously attempted workspace material is missing; recover it without repeating publication");
  }
}
function materialIntent(row: WorkspaceMaterial): JsonObject {
  return {id: row.source.id, destination: row.destination, sha256: row.source.sha256, bytes: row.source.bytes};
}
export async function readGoalTransferWorkspaceReview(directory: string, identity: JsonObject): Promise<string | null> {
  const original = await optionalRecord(join(directory, "workspace-review.json"));
  if (!original) return null;
  if (!hasExactAuthorityKeys(original, ["identity_sha256", "preview_sha256"]) ||
      original.identity_sha256 !== canonicalAuthoritySha256(identity) || !BARE_SHA256_PATTERN.test(String(original.preview_sha256))) reject("Original destination review changed; recover its existing operation");
  return String(original.preview_sha256);
}
export async function retainGoalTransferWorkspace(plan: GoalTransferWorkspacePlan, directory: string,
  identity: JsonObject, expected: string): Promise<void> {
  await retainRecord(join(directory, "workspace-review.json"), {identity_sha256: canonicalAuthoritySha256(identity), preview_sha256: expected});
  for (const row of plan.materials) {
    await requirePhysicalTransferPath(row.destination);
    let observed: Awaited<ReturnType<typeof inspectTransferFile>> | null = null;
    try { observed = await inspectTransferFile(row.destination); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    const path = join(directory, "workspace-materials", `${row.source.id}.json`);
    const intent = await optionalRecord(path);
    if (intent && !same(intent, materialIntent(row))) reject("Destination material intent changed");
    if (intent && !observed) reject("Previously attempted workspace material is missing; preserve the original operation");
    if (observed && (observed.sha256 !== row.source.sha256 || observed.bytes !== row.source.bytes)) reject("Destination workspace material changed; preserve it");
    await retainRecord(path, materialIntent(row));
    if (!observed) {
      const input = await inspectTransferFile(row.source.path);
      if (input.sha256 !== row.source.sha256 || input.bytes !== row.source.bytes) reject("Authenticated workspace material changed");
      await mkdir(dirname(row.destination), {recursive: true, mode: 0o700});
      await requirePhysicalTransferPath(row.destination);
      await copyFile(row.source.path, row.destination, constants.COPYFILE_EXCL);
      const handle = await open(row.destination, constants.O_RDONLY | constants.O_NOFOLLOW);
      try { await handle.sync(); } finally { await handle.close(); }
      const parent = await open(dirname(row.destination), constants.O_RDONLY);
      try { await parent.sync(); } finally { await parent.close(); }
    }
    const final = await inspectTransferFile(row.destination);
    if (final.sha256 !== row.source.sha256 || final.bytes !== row.source.bytes) reject("Destination workspace material readback failed");
  }
}
