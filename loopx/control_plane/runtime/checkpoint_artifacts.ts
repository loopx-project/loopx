/** Inert artifact capture for an already-owned source barrier.
 * Source stability and byte integrity are not research-point or execution authority.
 * Native sessions, canonical state adoption and evaluator opportunities retain owners.
 */
import {createHash} from "node:crypto";
import {createReadStream} from "node:fs";
import {copyFile, lstat, mkdir, mkdtemp, open, readFile, readdir, rm} from "node:fs/promises";
import {dirname, isAbsolute, join, resolve, sep} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {canonicalAuthoritySha256, hasExactAuthorityKeys} from "../coordination/authority_store_codec.ts";
import {snapshotSqliteDatabase} from "../coordination/sqlite_backup.ts";
import {durableWriteJson, publishNewDirectory} from "../effect_runtime_io.ts";
import {settlementIdentity} from "../effect_program.ts";
import {normalizeHeartbeatReplanObligationId, normalizeHeartbeatTodoId} from "../quota/heartbeat_receipt_identity.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {EffectiveAction} from "../quota/effective_action.generated.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";

const SCHEMA = "loopx_checkpoint_artifact_set_v0";
type Component = {id: string; source_path: string};
type Member = {path: string; kind: "file" | "directory"; mode: number; size?: number; sha256?: string};

function absolute(value: unknown, label: string): string {
  const path = requireNonEmptyString(value, label);
  if (!isAbsolute(path) || resolve(path) !== path) throw new Error(`${label} must be normalized and absolute`);
  return path;
}
async function physical(path: string): Promise<void> {
  for (let current = path;; current = dirname(current)) {
    const entry = await lstat(current);
    if (entry.isSymbolicLink()) throw new Error("checkpoint path must not contain symlinks");
    if (current !== path && !entry.isDirectory()) throw new Error("checkpoint ancestor must be a directory");
    if (dirname(current) === current) break;
  }
}
function components(value: unknown): Component[] {
  if (!Array.isArray(value) || value.length === 0) throw new Error("checkpoint requires explicit components");
  const ids = new Set<string>();
  const result = value.map(raw => {
    const entry = requireJsonObject(raw, "checkpoint component");
    if (!hasExactAuthorityKeys(entry, ["id", "source_path"])) throw new Error("invalid checkpoint component fields");
    const id = requireNonEmptyString(entry.id, "checkpoint component id");
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(id) || ids.has(id)) throw new Error("checkpoint component identity is invalid or duplicated");
    ids.add(id);
    return {id, source_path: absolute(entry.source_path, "checkpoint component source")};
  });
  for (const a of result) for (const b of result) {
    if (a !== b && (a.source_path === b.source_path || a.source_path.startsWith(b.source_path + sep))) {
      throw new Error("checkpoint component sources overlap");
    }
  }
  return result;
}
async function digest(path: string): Promise<string> {
  const hash = createHash("sha256");
  for await (const block of createReadStream(path)) hash.update(block);
  return hash.digest("hex");
}
async function members(root: string, sourceObservation: boolean): Promise<Member[]> {
  await physical(root);
  const output: Member[] = [];
  async function visit(path: string, name: string): Promise<void> {
    const stat = await lstat(path);
    if (stat.isSymbolicLink()) throw new Error("checkpoint source contains a symlink");
    // SQLite's reader index and an empty WAL contain no committed data. A
    // read-only backup can create either on a closed WAL database. Exclude
    // only these recognized sidecars, not a nonempty WAL or an explicitly
    // requested standalone file. Artifact verification excludes nothing.
    if (sourceObservation && name !== "" && stat.isFile() &&
        (await sqliteSidecar(path, ["-shm"]) ||
         (stat.size === 0 && await sqliteSidecar(path, ["-wal"])))) return;
    if (stat.isDirectory()) {
      output.push({path: name, kind: "directory", mode: stat.mode & 0o777});
      for (const child of (await readdir(path)).sort()) await visit(join(path, child), name ? `${name}/${child}` : child);
    } else if (stat.isFile()) {
      output.push({path: name, kind: "file", mode: stat.mode & 0o777, size: stat.size, sha256: await digest(path)});
    } else throw new Error("checkpoint source contains an unsupported file type");
  }
  await visit(root, "");
  if ((await lstat(root)).isFile() && await sqliteFile(root)) {
    for (const suffix of sourceObservation ? ["-wal", "-journal"] : ["-wal", "-shm", "-journal"]) {
      try {await visit(root + suffix, suffix);}
      catch (error) {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;}
    }
  }
  return output;
}
async function witness(entries: Component[], sourceObservation = true): Promise<JsonObject> {
  const result: JsonObject = {};
  for (const entry of entries) result[entry.id] = await members(entry.source_path, sourceObservation);
  return result;
}
function unchanged(a: unknown, b: unknown, message = "checkpoint source moved; retry at a new owned barrier"): void {
  if (canonicalAuthoritySha256(a) !== canonicalAuthoritySha256(b)) throw new EffectRuntimeConflictError(message);
}
async function absentDestination(path: string): Promise<void> {
  await physical(dirname(path));
  try {await lstat(path); throw new Error("checkpoint destination already exists");}
  catch (error) {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;}
}
async function sqliteFile(path: string): Promise<boolean> {
  const handle = await open(path, "r");
  try {
    const bytes = Buffer.alloc(16);
    const {bytesRead} = await handle.read(bytes, 0, 16, 0);
    return bytesRead === 16 && bytes.equals(Buffer.from("SQLite format 3\0"));
  } finally {await handle.close();}
}
async function sqliteSidecar(path: string, suffixes: string[]): Promise<boolean> {
  const suffix = suffixes.find(s => path.endsWith(s));
  if (!suffix) return false;
  const database = path.slice(0, -suffix.length);
  try {return (await lstat(database)).isFile() && await sqliteFile(database);}
  catch (error) {if ((error as NodeJS.ErrnoException).code === "ENOENT") return false; throw error;}
}
async function copyComponent(source: string, destination: string, snapshotDatabases = true): Promise<void> {
  const stat = await lstat(source);
  if (stat.isSymbolicLink()) throw new Error("checkpoint source became a symlink");
  if (stat.isDirectory()) {
    await mkdir(destination, {mode: 0o700});
    const children = (await readdir(source)).sort();
    const databases = new Set<string>();
    for (const child of snapshotDatabases ? children : []) {
      const path = join(source, child);
      if ((await lstat(path)).isFile()) {
        if (await sqliteFile(path)) databases.add(child);
      }
    }
    for (const child of children) {
      if (["-wal", "-shm", "-journal"].some(suffix => child.endsWith(suffix) && databases.has(child.slice(0, -suffix.length)))) continue;
      await copyComponent(join(source, child), join(destination, child), snapshotDatabases);
    }
  } else if (stat.isFile()) {
    // SQLite's existing physical snapshot owner seals committed WAL contents.
    if (snapshotDatabases && await sqliteFile(source)) await snapshotSqliteDatabase(source, destination);
    else await copyFile(source, destination);
    // copyFile preserves a read-only participant's mode. Syncing its bytes
    // needs no write access; r+ fails for an ordinary non-root controller.
    const handle = await open(destination, "r");
    try {await handle.sync();} finally {await handle.close();}
  } else throw new Error("checkpoint source contains an unsupported file type");
}
function manifestBody(raw: unknown): JsonObject {
  const value = requireJsonObject(raw, "checkpoint manifest");
  if (!hasExactAuthorityKeys(value, ["schema_version", "point_metadata", "components", "source_witness", "artifact_witness", "research_point_qualified", "execution_authority_granted", "privacy_certified", "sha256"]) ||
      value.schema_version !== SCHEMA || value.research_point_qualified !== false ||
      value.execution_authority_granted !== false || value.privacy_certified !== false) throw new Error("checkpoint manifest fields are invalid");
  const {sha256, ...body} = value;
  if (sha256 !== canonicalAuthoritySha256(body)) throw new Error("checkpoint manifest digest mismatch");
  components(value.components);
  requireJsonObject(value.point_metadata, "checkpoint point metadata");
  requireJsonObject(value.source_witness, "checkpoint source witness");
  requireJsonObject(value.artifact_witness, "checkpoint artifact witness");
  return value;
}
async function verify(root: string, expected: unknown): Promise<JsonObject> {
  await physical(root);
  await physical(join(root, "checkpoint.json"));
  const value = manifestBody(JSON.parse(await readFile(join(root, "checkpoint.json"), "utf8")));
  if (value.sha256 !== expected) throw new Error("checkpoint reviewed digest mismatch");
  const entries = components(value.components);
  const actual = await witness(entries.map(c => ({id: c.id, source_path: join(root, "components", c.id)})), false);
  unchanged(value.artifact_witness, actual, "checkpoint artifact members differ from the reviewed capture");
  if ((await readdir(root)).sort().join(",") !== "checkpoint.json,components" ||
      (await readdir(join(root, "components"))).sort().join(",") !== entries.map(c => c.id).sort().join(",")) throw new Error("checkpoint has unexpected members");
  return {ok: true, sha256: value.sha256, integrity_verified: true, research_point_qualified: false,
    execution_authority_granted: false, privacy_certified: false};
}
export async function checkpointArtifacts(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "checkpoint artifacts request");
  if (request.action === "restore_capture") {
    if (!hasExactAuthorityKeys(request, ["action", "checkpoint_sha256", "goal_id", "agent_id", "current_obligation_id", "review",
        ...(request.include_review_basis === undefined ? [] : ["include_review_basis"])]) ||
        (request.include_review_basis !== undefined && typeof request.include_review_basis !== "boolean")) {
      throw new Error("checkpoint restored capture fields are invalid");
    }
    const sha = requireNonEmptyString(request.checkpoint_sha256, "restored checkpoint digest");
    if (!BARE_SHA256_PATTERN.test(sha)) throw new Error("invalid restored checkpoint digest");
    const goal = requireNonEmptyString(request.goal_id, "checkpoint destination Goal");
    const agent = requireNonEmptyString(request.agent_id, "checkpoint destination Agent");
    if (request.current_obligation_id !== null && !normalizeHeartbeatReplanObligationId(request.current_obligation_id)) {
      throw new Error("restored checkpoint current replan identity is invalid");
    }
    const review = jsonObject(request.review), identity = jsonObject(review?.identity);
    const progress = jsonObject(review?.progress), run = jsonObject(review?.writeback_run);
    const guard = jsonObject(review?.semantic_replan_guard), ack = jsonObject(run?.autonomous_replan_ack);
    const delta = jsonObject(ack?.semantic_delta), path = jsonObject(jsonObject(run?.agent_vision)?.path_delta);
    const id = normalizeHeartbeatReplanObligationId(delta?.obligation_id);
    const todo = normalizeHeartbeatTodoId(identity?.todo_id);
    const boundReplan = normalizeHeartbeatReplanObligationId(identity?.replan_obligation_id);
    // The adapter must obtain these records from the existing canonical
    // settlement owner. Copied source runs, slot debit alone, a neutral ACK or
    // the still-pending decision being captured cannot release this barrier.
    const accepted = !!identity && !!run && identity.goal_id === goal && identity.agent_id === agent &&
      run.goal_id === goal && run.agent_id === agent && run.turn_instance_id === identity.turn_instance_id &&
      normalizeHeartbeatTodoId(run.todo_id) === todo &&
      normalizeHeartbeatReplanObligationId(run.replan_obligation_id) === boundReplan &&
      typeof identity.turn_instance_id === "string" && identity.turn_instance_id.length > 0 &&
      identity.effect_id === settlementIdentity({goal_id: goal, agent_id: agent, todo_id: todo,
        replan_obligation_id: boundReplan, turn_instance_id: identity.turn_instance_id}).effect_id &&
      progress?.state === "settled" && ack?.schema_version === "autonomous_replan_ack_v0" && ack.recorded === true &&
      delta?.schema_version === "replan_semantic_delta_v0" && delta.accepted === true && !!id &&
      guard?.selected_obligation_id === id && request.current_obligation_id !== id &&
      Array.isArray(path?.evidence_refs) && path.evidence_refs.includes(`checkpoint:${sha}`);
    return {capture_allowed: accepted, reason: accepted ? "restored_replan_completed" : "restored_replan_pending",
      ...(accepted ? {review_obligation_id: id} : {}),
      ...(accepted && request.include_review_basis === true ? {review_basis: {
        identity, obligation_id: id,
        completed_decision: {agent_vision: run!.agent_vision, autonomous_replan_ack: ack},
        decision_sha256: canonicalAuthoritySha256({agent_vision: run!.agent_vision, autonomous_replan_ack: ack}),
      }} : {}),
      execution_authority_granted: false};
  }
  if (request.action === "select_replan") {
    if (!hasExactAuthorityKeys(request, ["action", "quota", "goal_id", "agent_id", "turn_instance_id"])) {
      throw new Error("checkpoint replan selection fields are invalid");
    }
    const quota = requireJsonObject(request.quota, "checkpoint quota readback");
    const goal = requireNonEmptyString(request.goal_id, "checkpoint goal");
    const agent = requireNonEmptyString(request.agent_id, "checkpoint agent");
    const turn = requireNonEmptyString(request.turn_instance_id, "checkpoint Turn");
    if (quota.ok !== true || quota.goal_id !== goal) throw new Error("checkpoint requires a matching current quota readback");
    if (quota.effective_action !== EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED) {
      return {capture: false, reason: "no_fresh_replan", execution_authority_granted: false};
    }
    const packet = requireJsonObject(quota.replan_action_packet, "checkpoint replan packet");
    const id = normalizeHeartbeatReplanObligationId(packet.obligation_id);
    const receipt = requireJsonObject(quota.heartbeat_receipt, "checkpoint heartbeat receipt");
    // The replan owner marks receipt-owned follow-through with settlement_only.
    // These packets carry the original writer's response, not a new research node.
    const settlementOnly = packet.settlement_only === true;
    if (packet.schema_version !== "replan_action_packet_v0" ||
        typeof packet.decision !== "string" || packet.decision.trim() === "" ||
        (!settlementOnly && packet.decision !== "replan_required") ||
        !id || (receipt.status !== "committed" && receipt.status !== "replayed") || receipt.turn_instance_id !== turn) {
      throw new Error("checkpoint replan does not match the current admitted identity");
    }
    const semanticReplan = normalizeHeartbeatReplanObligationId(receipt.semantic_replan_obligation_id);
    const closeoutPending = receipt.closeout_required === true;
    if (!closeoutPending && !settlementOnly && receipt.settlement_identity === undefined && receipt.settlement_binding_owed === true) {
      // The original guard owns the semantic node before the Agent chooses
      // work. An inert pre-decision capture does not invent that later work's
      // causal settlement identity or grant permission to execute it.
      const actor = requireJsonObject(quota.agent_identity, "checkpoint source Agent");
      requireNonEmptyString(receipt.event_id, "checkpoint committed guard event");
      if (actor.agent_id !== agent || actor.registered !== true || semanticReplan !== id) {
        throw new Error("checkpoint replan does not match the current admitted identity");
      }
      return {capture: true, obligation_id: id, execution_authority_granted: false};
    }
    const identity = requireJsonObject(receipt.settlement_identity, "checkpoint settlement identity");
    const todo = normalizeHeartbeatTodoId(identity.todo_id);
    const boundReplan = normalizeHeartbeatReplanObligationId(identity.replan_obligation_id);
    if ((todo !== null && boundReplan !== null) ||
        identity.goal_id !== goal || identity.agent_id !== agent || identity.turn_instance_id !== turn ||
        identity.effect_id !== settlementIdentity({goal_id: goal, agent_id: agent, todo_id: todo,
          replan_obligation_id: boundReplan, turn_instance_id: turn}).effect_id) {
      throw new Error("checkpoint replan does not match the current admitted identity");
    }
    // A replay can expose the next obligation while this Turn still owes its
    // original Todo settlement. Release that response to its writer; it is not
    // a new capture boundary and must not interrupt the pending closeout.
    if (closeoutPending) return {capture: false, reason: "turn_closeout_pending", execution_authority_granted: false};
    if (settlementOnly) {
      if (semanticReplan !== id || (boundReplan !== null && boundReplan !== id)) {
        throw new Error("checkpoint replan does not match the current admitted identity");
      }
      return {capture: false, reason: "turn_closeout_pending", execution_authority_granted: false};
    }
    if (todo ? boundReplan !== null || semanticReplan !== id : boundReplan !== id) {
      throw new Error("checkpoint replan does not match the current admitted identity");
    }
    return {capture: true, obligation_id: id, settlement_effect_id: identity.effect_id,
      execution_authority_granted: false};
  }
  if (request.action === "read_basis") {
    if (!hasExactAuthorityKeys(request, ["action", "root", "expected_sha256"])) throw new Error("checkpoint basis fields are invalid");
    const root = absolute(request.root, "checkpoint root");
    await physical(join(root, "checkpoint.json"));
    const manifest = manifestBody(JSON.parse(await readFile(join(root, "checkpoint.json"), "utf8")));
    if (manifest.sha256 !== request.expected_sha256) throw new Error("checkpoint reviewed digest mismatch");
    // This reads a digest-bound declaration after admission's full member
    // verification. It does not claim current integrity of every component.
    return {point_metadata: manifest.point_metadata, components: manifest.components,
      integrity_verified: false, execution_authority_granted: false};
  }
  const fields = request.action === "probe" ? ["action", "components"] :
    request.action === "verify" ? ["action", "root", "expected_sha256"] :
    request.action === "capture" ? ["action", "components", "destination", "point_metadata", "expected_source_sha256"] :
    request.action === "materialize" ? ["action", "root", "expected_sha256", "destination"] : null;
  if (fields === null) throw new Error("unsupported checkpoint artifact operation");
  if (!hasExactAuthorityKeys(request, [...fields, ...(request.deadline_epoch_ms === undefined ? [] : ["deadline_epoch_ms"])])) {
    throw new Error("checkpoint request fields are invalid");
  }
  const deadline = request.deadline_epoch_ms;
  if (deadline !== undefined && (typeof deadline !== "number" || !Number.isFinite(deadline))) throw new Error("checkpoint deadline must be finite");
  function withinDeadline(): void {
    if (typeof deadline === "number" && Date.now() >= deadline) throw new Error("checkpoint capture window has closed");
  }
  withinDeadline();
  if (request.action === "probe") {
    const sources = components(request.components);
    const observed = await witness(sources);
    return {source_witness: observed, source_sha256: canonicalAuthoritySha256(observed)};
  }
  if (request.action === "verify") return verify(absolute(request.root, "checkpoint root"), request.expected_sha256);
  if (request.action !== "capture" && request.action !== "materialize") throw new Error("unsupported checkpoint artifact operation");
  const destination = absolute(request.destination, "checkpoint destination");
  await absentDestination(destination);
  const staging = await mkdtemp(join(dirname(destination), ".loopx-checkpoint-"));
  try {
    if (request.action === "materialize") {
      const root = absolute(request.root, "checkpoint root");
      const verification = await verify(root, request.expected_sha256);
      await copyComponent(root, join(staging, "unit"), false);
      await verify(join(staging, "unit"), request.expected_sha256);
      withinDeadline();
      await publishNewDirectory(join(staging, "unit"), destination);
      return {...verification, materialized: true, activation_performed: false};
    }
    const sources = components(request.components);
    if (sources.some(c => destination === c.source_path || destination.startsWith(c.source_path + sep) || c.source_path.startsWith(destination + sep))) throw new Error("checkpoint output overlaps its source");
    const before = await witness(sources);
    if (request.expected_source_sha256 !== canonicalAuthoritySha256(before)) throw new EffectRuntimeConflictError("checkpoint source moved before capture");
    await mkdir(join(staging, "components"), {mode: 0o700});
    for (const c of sources) await copyComponent(c.source_path, join(staging, "components", c.id));
    unchanged(before, await witness(sources));
    const artifacts = await witness(sources.map(c => ({id: c.id, source_path: join(staging, "components", c.id)})), false);
    // Byte-copy members must match the reviewed source, including ABA movement
    // that could otherwise escape a before/after comparison. SQLite is sealed
    // by its existing snapshot owner and has a separate physical witness.
    for (const c of sources) {
      for (const member of before[c.id] as Member[]) {
        if (member.kind !== "file") continue;
        const source = (await lstat(c.source_path)).isDirectory() ? join(c.source_path, member.path) : c.source_path + member.path;
        if (await sqliteFile(source) || (member.path !== "" && await sqliteSidecar(source, ["-wal", "-shm", "-journal"]))) continue;
        const copied = (artifacts[c.id] as Member[]).find(m => m.path === member.path && m.kind === "file");
        if (!copied || copied.sha256 !== member.sha256 || copied.size !== member.size || copied.mode !== member.mode) {
          throw new Error("checkpoint copied bytes do not match the reviewed source");
        }
      }
    }
    const body: JsonObject = {schema_version: SCHEMA, components: sources, point_metadata: requireJsonObject(request.point_metadata, "checkpoint point metadata"),
      source_witness: before, artifact_witness: artifacts, research_point_qualified: false,
      execution_authority_granted: false, privacy_certified: false};
    const manifest = {...body, sha256: canonicalAuthoritySha256(body)};
    await durableWriteJson(join(staging, "checkpoint.json"), manifest);
    await verify(staging, manifest.sha256);
    unchanged(before, await witness(sources));
    withinDeadline();
    await publishNewDirectory(staging, destination);
    return {...await verify(destination, manifest.sha256), source_stability_verified: true};
  } finally {await rm(staging, {recursive: true, force: true});}
}
