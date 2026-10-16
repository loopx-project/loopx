/** Selected Goal bytes inside the draft encrypted custody profile. The native
 * archive still owns Task/history semantics; this owner never recreates them.
 * Decryption grants no admission, Host authority or credential adoption. */
import {lstat} from "node:fs/promises";
import {isAbsolute, join, relative, sep} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys} from "../coordination/authority_store_codec.ts";
import {verifyAuthorityArchive} from "../coordination/authority_archive.ts";
import {PREPARATION_SCHEMA, decodePlannedTransferMaterial, type ProtectedGoalMaterial} from "./planned_transfer.ts";
import {inspectTransferFile, requirePhysicalTransferPath, withVerifiedTransferContainer,
  validatePlannedTransferRecipient, writeTransferContainer, type TransferContainerFile} from "./planned_transfer_container.ts";

const PAYLOAD_SCHEMA = "loopx_planned_transfer_payload_v0";
function reject(message: string): never { throw new EffectRuntimeConflictError(message); }
function same(a: unknown, b: unknown): boolean { return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b)); }
function beneath(root: string, path: string): boolean {
  const suffix = relative(root, path);
  return suffix === "" || (!isAbsolute(suffix) && suffix !== ".." && !suffix.startsWith(`..${sep}`));
}
function version(value: unknown): string {
  if (typeof value !== "string" || !value.trim() || value.length > 128) reject("Transfer package version is unavailable");
  return value;
}
export interface VerifiedGoalTransferPayload {
  preparation: JsonObject; context: JsonObject; archive: string; artifactSha256: string;
  materials: ProtectedGoalMaterial[]; sourceVersion: string;
}

/** Called only within source preparation's maintenance/context locks, after its
 * original registry, native revision, complete archive and source fence check.
 * Materials are an explicit reviewed selection, not a completeness/privacy
 * certification. No machine root, registry inventory or Git metadata is swept. */
export async function reviewProtectedGoalTransferInputs(raw: unknown, context: JsonObject, packageVersion: unknown) {
  const transport = requireJsonObject(raw, "protected transfer request");
  if (!hasExactAuthorityKeys(transport, ["destination", "recipient", "materials"]) || !Array.isArray(transport.materials)) {
    reject("Protected transfer requires an explicit recipient, destination and reviewed materials");
  }
  const recipient = validatePlannedTransferRecipient(transport.recipient);
  const destination = await requirePhysicalTransferPath(transport.destination), sourceVersion = version(packageVersion);
  const goal = requireJsonObject(context.goal, "original Goal");
  const project = await requirePhysicalTransferPath(goal.repo), runtime = await requirePhysicalTransferPath(context.runtime_root);
  const registry = await requirePhysicalTransferPath(context.registry_path);
  const files: TransferContainerFile[] = [];
  const materials: JsonObject[] = [], seen = new Set<string>();
  for (const [index, rawMaterial] of transport.materials.entries()) {
    const material = requireJsonObject(rawMaterial, "reviewed Goal material");
    if (!hasExactAuthorityKeys(material, ["path", "sha256"]) || !BARE_SHA256_PATTERN.test(String(material.sha256))) reject("Goal material requires its reviewed content digest");
    const path = await requirePhysicalTransferPath(material.path), suffix = relative(project, path);
    if (!beneath(project, path) || path === project || beneath(runtime, path) || path === registry ||
        beneath(join(project, ".git"), path) || seen.has(path)) reject("Transfer material must be unique workspace input outside runtime, registry and Git metadata");
    if ((await lstat(path)).nlink !== 1) reject("Transfer material must not alias another file through a hard link");
    const facts = await inspectTransferFile(path);
    if (facts.sha256 !== material.sha256) reject("Reviewed Goal material changed; review its current bytes before exporting");
    seen.add(path); files.push({path, ...facts});
    materials.push({id: (index + 2).toString(16).padStart(8, "0"), source_relative_path: suffix,
      ...facts});
  }
  return {recipient, destination, sourceVersion, files, materials};
}

export async function writeProtectedGoalTransfer(reviewed: Awaited<ReturnType<typeof reviewProtectedGoalTransferInputs>>,
  preparation: JsonObject, context: JsonObject, keyRecordPath: string): Promise<JsonObject> {
  if (preparation.original_registration_verified !== true) reject("Protected transfer requires the qualified original registered source");
  const archive = String(preparation.archive);
  const metadata = {schema_version: PAYLOAD_SCHEMA, source_package_version: reviewed.sourceVersion,
    source_preparation: preparation, source_context: context, materials: reviewed.materials};
  const result = await writeTransferContainer(reviewed.destination, reviewed.recipient, metadata,
    [{path: archive, ...await inspectTransferFile(archive)}, ...reviewed.files], keyRecordPath);
  return {...result, material_count: reviewed.materials.length, privacy_certified: false,
    host_drain_qualified: false, portable_migration_qualified: false};
}

/** Authenticate every byte before inspecting the native archive or exposing
 * temporary material paths to the stopped-stage owner. The caller must await
 * this scope; plaintext is removed when it exits, including refusal/retry. */
export async function withVerifiedGoalTransferPayload<T>(value: JsonObject,
  consume: (payload: VerifiedGoalTransferPayload) => Promise<T>): Promise<T> {
  if (!hasExactAuthorityKeys(value, ["directory", "private_key_path", "expected_artifact_sha256", "target_package_version"]) ||
      !BARE_SHA256_PATTERN.test(String(value.expected_artifact_sha256))) reject("Protected transfer requires its reviewed artifact digest");
  const targetVersion = version(value.target_package_version);
  const directory = await requirePhysicalTransferPath(value.directory), key = await requirePhysicalTransferPath(value.private_key_path);
  return withVerifiedTransferContainer(directory, key, value.expected_artifact_sha256, async verified => {
    const data = verified.metadata;
    if (!hasExactAuthorityKeys(data, ["schema_version", "source_package_version", "source_preparation", "source_context", "materials"]) ||
        data.schema_version !== PAYLOAD_SCHEMA || version(data.source_package_version) !== targetVersion) {
      reject("This draft transfer profile requires the same source and destination package version; use a qualified compatibility path");
    }
    const preparation = requireJsonObject(data.source_preparation, "original transfer preparation");
    const context = requireJsonObject(data.source_context, "original transfer context");
    const goal = requireJsonObject(context.goal, "original Goal");
    if (preparation.schema_version !== PREPARATION_SCHEMA || preparation.original_registration_verified !== true ||
        preparation.authority_write_fenced !== true || preparation.execution_authority_granted !== false ||
        preparation.goal_id !== goal.id || preparation.source_context_sha256 !== canonicalAuthoritySha256(context)) reject("Original transfer source context is inconsistent");
    if (!hasExactAuthorityKeys(context, ["goal", "state_text", "runtime_root", "registry_path"]) ||
        typeof context.state_text !== "string" || !context.state_text.trim() || goal.goal_instance_id !== undefined) reject("Original Goal context requires its qualified lifecycle");
    const archive = verified.files.get("00000001")!;
    const summary = await verifyAuthorityArchive(archive);
    if (!same({...summary}, preparation.authority_archive) || summary.goal_id !== goal.id) reject("Protected transfer differs from its prepared original authority");
    if (!Array.isArray(data.materials) || data.materials.length !== verified.members.length - 1) reject("Protected transfer material inventory differs");
    const materials: ProtectedGoalMaterial[] = [], seen = new Set<string>();
    for (const [index, rawMaterial] of data.materials.entries()) {
      const material = decodePlannedTransferMaterial(rawMaterial, index), member = verified.members[index + 1]!;
      if (seen.has(material.source_relative_path) || material.id !== member.id || material.sha256 !== member.sha256 || material.bytes !== member.bytes) reject("Protected material paths or original content identity changed");
      seen.add(material.source_relative_path);
      materials.push({...material, path: verified.files.get(member.id)!});
    }
    return consume({preparation, context, archive, materials, artifactSha256: verified.envelopeSha256, sourceVersion: targetVersion});
  });
}
