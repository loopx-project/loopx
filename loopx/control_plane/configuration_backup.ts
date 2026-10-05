/** Configuration checkpoint IO. Configuration owners retain validation and activation. */
import {lstat, mkdir, mkdtemp, readFile, rename, rm, rmdir} from "node:fs/promises";
import {dirname, isAbsolute, join, resolve} from "node:path";
import type {JsonObject} from "./effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "./runtime_decode.ts";
import {canonicalAuthorityJson, canonicalAuthoritySha256, hasExactAuthorityKeys} from "./coordination/authority_store_codec.ts";
import {durableWriteJson} from "./effect_runtime_io.ts";

const SCHEMA = "loopx_configuration_backup_v0";

function content(value: unknown): JsonObject {
  const data = requireJsonObject(canonicalAuthorityJson(value), "configuration backup data");
  if (!hasExactAuthorityKeys(data, ["machine_configuration", "goals"])) throw new Error("configuration backup data fields are invalid");
  if (data.machine_configuration !== null) requireJsonObject(data.machine_configuration, "machine configuration");
  if (!Array.isArray(data.goals)) throw new Error("configuration backup goals must be an array");
  const identities = new Set<string>();
  for (const item of data.goals) {
    const entry = requireJsonObject(item, "configuration backup Goal");
    if (!hasExactAuthorityKeys(entry, ["goal_id", "goal_configuration"])) throw new Error("configuration backup Goal fields are invalid");
    const id = requireNonEmptyString(entry.goal_id, "configuration backup Goal id");
    const goal = requireJsonObject(entry.goal_configuration, "Goal configuration");
    if (identities.has(id) || goal.id !== id) throw new Error("configuration backup Goal identity is ambiguous");
    identities.add(id);
  }
  return data;
}

export function captureConfigurationBackup(value: unknown): JsonObject {
  const request = requireJsonObject(value, "configuration backup capture");
  const body: JsonObject = {schema_version: SCHEMA, privacy_certified: false,
    activation_performed: false, data: content(request.data)};
  return {...body, sha256: canonicalAuthoritySha256(body)};
}

export function verifyConfigurationBackup(value: unknown): JsonObject {
  const backup = requireJsonObject(canonicalAuthorityJson(value), "configuration backup");
  if (!hasExactAuthorityKeys(backup, ["schema_version", "privacy_certified", "activation_performed", "data", "sha256"])
    || backup.schema_version !== SCHEMA || backup.privacy_certified !== false || backup.activation_performed !== false) {
    throw new Error("configuration backup envelope is invalid");
  }
  const data = content(backup.data);
  const {sha256, ...body} = backup;
  if (sha256 !== canonicalAuthoritySha256(body)) throw new Error("configuration backup digest mismatch");
  return {ok: true, schema_version: SCHEMA, sha256, goal_count: (data.goals as unknown[]).length,
    machine_configuration_present: data.machine_configuration !== null,
    privacy_certified: false, activation_performed: false, readback_verified: true};
}

async function requirePhysicalParent(path: string): Promise<void> {
  // Reject pre-existing symlink ancestors; do not silently follow another runtime.
  for (let current = path; ; current = dirname(current)) {
    const stat = await lstat(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error("configuration restore parent must be a physical directory");
    if (dirname(current) === current) break;
  }
}

export async function restoreConfigurationBackup(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "configuration backup restore");
  const backup = requireJsonObject(request.backup, "configuration backup");
  const verification = verifyConfigurationBackup(backup);
  if (request.expected_sha256 !== verification.sha256) throw new Error("reviewed configuration backup digest changed");
  const destination = requireNonEmptyString(request.destination, "configuration restore destination");
  if (!isAbsolute(destination) || resolve(destination) !== destination) throw new Error("configuration restore requires a normalized absolute destination");
  const parent = dirname(destination);
  await requirePhysicalParent(parent);
  try { await lstat(destination); throw new Error("configuration restore destination already exists"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  if (request.execute !== true && request.execute !== false) throw new Error("configuration restore execute must be a boolean");
  if (!request.execute) return {...verification, status: "preview", written: false};
  const staging = await mkdtemp(join(parent, ".loopx-configuration-"));
  try {
    const data = content(backup.data);
    const files: Array<[string, unknown]> = [["configuration-backup.json", backup]];
    if (data.machine_configuration !== null) files.push(["machine/configuration.json", data.machine_configuration]);
    for (const raw of data.goals as JsonObject[]) {
      files.push([`goals/${canonicalAuthoritySha256(raw.goal_id)}.json`, raw.goal_configuration]);
    }
    for (const [name, payload] of files) {
      const path = join(staging, name);
      await mkdir(dirname(path), {recursive: true, mode: 0o700});
      await durableWriteJson(path, requireJsonObject(payload, "configuration checkpoint file"));
      if (canonicalAuthoritySha256(JSON.parse(await readFile(path, "utf8"))) !== canonicalAuthoritySha256(payload)) {
        throw new Error("configuration restore readback mismatch");
      }
    }
    const receipt = {...verification, status: "restored", written: true,
      live_configuration_changed: false, configuration_files: files.map(([name]) => name)};
    await durableWriteJson(join(staging, "restore-receipt.json"), receipt);
    // Exclusive reservation prevents a competing restore from being overwritten.
    await mkdir(destination, {mode: 0o700});
    try { await rename(staging, destination); }
    catch (error) { await rmdir(destination); throw error; }
    return receipt;
  } finally { await rm(staging, {recursive: true, force: true}); }
}

export async function configurationBackupOperation(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "configuration backup operation");
  if (request.action === "capture") return captureConfigurationBackup(request);
  if (request.action === "verify") return verifyConfigurationBackup(request.backup);
  if (request.action === "restore") return restoreConfigurationBackup(request);
  throw new Error("unsupported configuration backup operation");
}
