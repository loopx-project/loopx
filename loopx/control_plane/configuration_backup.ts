/** Configuration checkpoint IO. Configuration owners retain validation and activation. */
import {lstat, mkdir, mkdtemp, readFile, rm} from "node:fs/promises";
import {dirname, isAbsolute, join, resolve} from "node:path";
import type {JsonObject} from "./effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "./runtime_decode.ts";
import {authorityUnicodeCompare, canonicalAuthorityJson, canonicalAuthoritySha256, hasExactAuthorityKeys, isAuthorityJsonObject} from "./coordination/authority_store_codec.ts";
import {durableWriteJson, publishNewDirectory} from "./effect_runtime_io.ts";
import {EffectRuntimeConflictError, EffectRuntimeRequestError} from "./effect_runtime_errors.ts";

const SCHEMA = "loopx_configuration_backup_v0";

function content(value: unknown): JsonObject {
  const data = requireJsonObject(canonicalAuthorityJson(value), "configuration backup data");
  if (!hasExactAuthorityKeys(data, ["machine_configuration", "goals"])) throw new EffectRuntimeRequestError("configuration backup data fields are invalid");
  if (data.machine_configuration !== null) requireJsonObject(data.machine_configuration, "machine configuration");
  if (!Array.isArray(data.goals)) throw new EffectRuntimeRequestError("configuration backup goals must be an array");
  const identities = new Set<string>();
  for (const item of data.goals) {
    const entry = requireJsonObject(item, "configuration backup Goal");
    if (!hasExactAuthorityKeys(entry, ["goal_id", "goal_configuration"])) throw new EffectRuntimeRequestError("configuration backup Goal fields are invalid");
    const id = requireNonEmptyString(entry.goal_id, "configuration backup Goal id");
    const goal = requireJsonObject(entry.goal_configuration, "Goal configuration");
    if (identities.has(id) || goal.id !== id) throw new EffectRuntimeRequestError("configuration backup Goal identity is ambiguous");
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
    throw new EffectRuntimeRequestError("configuration backup envelope is invalid");
  }
  const data = content(backup.data);
  const {sha256, ...body} = backup;
  if (sha256 !== canonicalAuthoritySha256(body)) throw new EffectRuntimeRequestError("configuration backup digest mismatch");
  return {ok: true, schema_version: SCHEMA, sha256, goal_count: (data.goals as unknown[]).length,
    machine_configuration_present: data.machine_configuration !== null,
    privacy_certified: false, activation_performed: false, readback_verified: true};
}

/** An inert operator comparison, not an adoption plan. Goal cohorts may differ;
 * presence in only one snapshot does not establish live creation or deletion. */
export function compareConfigurationBackups(value: unknown): JsonObject {
  const request = requireJsonObject(value, "configuration backup comparison");
  const before = requireJsonObject(request.before, "before configuration backup");
  const after = requireJsonObject(request.after, "after configuration backup");
  const beforeVerification = verifyConfigurationBackup(before);
  const afterVerification = verifyConfigurationBackup(after);
  const left = content(before.data), right = content(after.data);
  const goals = (data: JsonObject) => new Map((data.goals as JsonObject[]).map(goal =>
    [goal.goal_id as string, goal.goal_configuration]));
  const leftGoals = goals(left), rightGoals = goals(right);
  const beforeIds = [...leftGoals.keys()].sort(authorityUnicodeCompare);
  const afterIds = [...rightGoals.keys()].sort(authorityUnicodeCompare);
  const changes: JsonObject[] = [];
  const segment = (key: string) => key.replace(/~/g, "~0").replace(/\//g, "~1");
  const compare = (a: unknown, b: unknown, path: string, aPresent: boolean, bPresent: boolean): void => {
    if (aPresent && bPresent) {
      if (isAuthorityJsonObject(a) && isAuthorityJsonObject(b)) {
        for (const key of [...new Set([...Object.keys(a), ...Object.keys(b)])].sort(authorityUnicodeCompare)) {
          compare(a[key], b[key], `${path}/${segment(key)}`, Object.hasOwn(a, key), Object.hasOwn(b, key));
        }
        return;
      }
      // Arrays retain order and are compared as a whole; no inferred item identity.
      if (JSON.stringify(a) === JSON.stringify(b)) return;
    }
    changes.push({path, before_present: aPresent, after_present: bPresent,
      ...(aPresent ? {before: a} : {}), ...(bPresent ? {after: b} : {})});
  };
  compare(left.machine_configuration, right.machine_configuration, "/machine_configuration", true, true);
  for (const id of [...new Set([...beforeIds, ...afterIds])].sort(authorityUnicodeCompare)) {
    compare(leftGoals.get(id), rightGoals.get(id), `/goals/${segment(id)}`, leftGoals.has(id), rightGoals.has(id));
  }
  return {ok: true, status: "compared", before_sha256: beforeVerification.sha256, after_sha256: afterVerification.sha256,
    before_goal_ids: beforeIds, after_goal_ids: afterIds,
    only_in_before: beforeIds.filter(id => !rightGoals.has(id)), only_in_after: afterIds.filter(id => !leftGoals.has(id)),
    before_machine_configuration_present: beforeVerification.machine_configuration_present,
    after_machine_configuration_present: afterVerification.machine_configuration_present,
    change_count: changes.length, changes, written: false, activation_performed: false, privacy_certified: false};
}

async function requirePhysicalParent(path: string): Promise<void> {
  // Reject pre-existing symlink ancestors; do not silently follow another runtime.
  for (let current = path; ; current = dirname(current)) {
    const stat = await lstat(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new EffectRuntimeRequestError("configuration restore parent must be a physical directory");
    if (dirname(current) === current) break;
  }
}

export async function restoreConfigurationBackup(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "configuration backup restore");
  const backup = requireJsonObject(request.backup, "configuration backup");
  const verification = verifyConfigurationBackup(backup);
  if (request.expected_sha256 !== verification.sha256) throw new EffectRuntimeRequestError("reviewed configuration backup digest changed");
  const destination = requireNonEmptyString(request.destination, "configuration restore destination");
  if (!isAbsolute(destination) || resolve(destination) !== destination) throw new EffectRuntimeRequestError("configuration restore requires a normalized absolute destination");
  const parent = dirname(destination);
  await requirePhysicalParent(parent);
  try { await lstat(destination); throw new EffectRuntimeConflictError("configuration restore destination already exists"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  if (request.execute !== true && request.execute !== false) throw new EffectRuntimeRequestError("configuration restore execute must be a boolean");
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
    await publishNewDirectory(staging, destination);
    return receipt;
  } finally { await rm(staging, {recursive: true, force: true}); }
}

export async function configurationBackupOperation(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "configuration backup operation");
  if (request.action === "capture") return captureConfigurationBackup(request);
  if (request.action === "verify") return verifyConfigurationBackup(request.backup);
  if (request.action === "compare") return compareConfigurationBackups(request);
  if (request.action === "restore") return restoreConfigurationBackup(request);
  throw new EffectRuntimeRequestError("unsupported configuration backup operation");
}
