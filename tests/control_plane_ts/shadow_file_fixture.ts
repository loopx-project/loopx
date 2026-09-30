import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { TestContext } from "node:test";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { canonicalAuthorityBytes, canonicalAuthoritySha256 } from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import { TODO_CANONICAL_READ_RECORD_FIELDS } from "../../loopx/control_plane/coordination/coordination_projection.ts";
import * as schemas from "../../loopx/control_plane/coordination/coordination_state_contract.generated.ts";
import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import { localAuthorityShadowPartitionDigest } from "../../loopx/control_plane/coordination/local_authority_shadow.ts";
import { outboxEntryIdentity } from "../../loopx/control_plane/coordination/local_authority_shadow_outbox.ts";
import { bootstrapCoordinationRuntimeShadow } from "../../loopx/control_plane/coordination/runtime_shadow.ts";
import { requireShadowCaptureBinding } from "../../loopx/control_plane/coordination/shadow_management.ts";

export function sha(value: Uint8Array | string): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}
export function todo(id = "todo_one", status = "open"): JsonObject {
  return { schema_version: "todo_item_v0", todo_id: id, role: "agent", status, done: status === "done",
    text: "Qualify file shadow", archive_state: "active", source_section: "Agent Todo" };
}
export function projection(todos: JsonObject[] = [], leases: JsonObject[] = [], handoff = "hard_lease"): JsonObject {
  return { schema_version: schemas.LOCAL_AUTHORITY_SHADOW_TRANSACTION_PROJECTION_SCHEMA, goal_id: "goal-a",
    source_authority: "legacy_markdown_and_task_lease", handoff_mode: handoff, todos, leases,
    todo_read_model: { schema_version: "loopx_todo_canonical_read_record_v0", todo_count: todos.length,
      records_sha256: canonicalAuthoritySha256(todos), contract_fields: [...TODO_CANONICAL_READ_RECORD_FIELDS] },
    partitions: { todos: null, leases: null } };
}
export interface ShadowFixture {
  root: string;
  statePath: string;
  store: FileAuthorityStore;
  baseline: JsonObject;
  goalRef?: JsonObject | null;
}
export async function sourceRequest(f: ShadowFixture, head: JsonObject): Promise<JsonObject> {
  const registryPath = join(f.root, "registry.json");
  let registryBytes: Buffer;
  try { registryBytes = await readFile(registryPath); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    registryBytes = Buffer.from(JSON.stringify({
      ...(f.goalRef == null ? {} : {profile_id: "source_session_v1"}),
      goals: [{
        id: "goal-a",
        ...(f.goalRef == null ? {} : {goal_instance_id: f.goalRef.goal_instance_id}),
        coordination: {registered_agents: ["agent-a", "agent-b"]},
      }],
    }));
    await writeFile(registryPath, registryBytes);
  }
  const agents = JSON.parse(registryBytes.toString("utf8")).goals[0].coordination.registered_agents;
  const directory = join(f.root, "goals", "goal-a", "task-leases");
  let names: string[];
  try { names = (await readdir(directory)).filter((name) => /^[A-Za-z0-9_.-]+\.json$/.test(name)).sort(); } catch { names = []; }
  const inventory = [];
  for (const name of names) inventory.push({ name, bytes_sha256: sha(await readFile(join(directory, name))) });
  return { runtime_root: f.root, goal_id: "goal-a", projection: head,
    source_snapshot: { state_path: f.statePath, registered_runtime_root: f.root, registered_state_path: f.statePath,
      state_bytes_sha256: sha(await readFile(f.statePath)),
      lease_inventory: inventory, projection_sha256: canonicalAuthoritySha256(head), evidence_files: [], registry_source: {path: registryPath,
        sha256: sha(registryBytes).slice(7), registered_agents: agents} } };
}
export async function fixture(
  t: TestContext,
  goalRef: JsonObject | null = null,
): Promise<ShadowFixture> {
  const root = await mkdtemp(join(tmpdir(), "loopx-file-outbox-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const statePath = join(root, "ACTIVE_GOAL_STATE.md");
  await writeFile(statePath, "---\ngoal_id: goal-a\nhandoff_mode: hard_lease\n---\n\n## Agent Todo\n\n");
  const f = {
    root,
    statePath,
    store: new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a"),
    baseline: projection(),
    goalRef,
  };
  const boot = await bootstrapCoordinationRuntimeShadow({ ...await sourceRequest(f, f.baseline),
    schema_version: goalRef === null
      ? schemas.COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA
      : schemas.COORDINATION_RUNTIME_SHADOW_EXACT_BOOTSTRAP_REQUEST_SCHEMA,
    operation_id: "bootstrap:test:first", source_version: "source:initial",
    ...(goalRef === null ? {} : {goal_ref: goalRef}) });
  if (boot.status !== "applied") throw new Error(`fixture bootstrap failed: ${JSON.stringify(boot)}`);
  return f;
}
export async function pendingEntry(f: ShadowFixture, seq: number, part: JsonObject, options: {
  partition?: "todos" | "leases"; resolution?: string; writeClass?: string; marker?: boolean;
  previousPartitionProjection?: JsonObject;
} = {}): Promise<JsonObject> {
  const partition = options.partition ?? "todos";
  const binding = await requireShadowCaptureBinding(f.root, "goal-a");
  const previous = await readFile(f.statePath);
  const prior = await f.store.loadAuthority();
  if (prior.status !== "loaded") throw new Error("fixture must have a baseline");
  const previousPartition = partition === "todos" ? { handoff_mode: prior.head.handoff_mode, todos: prior.head.todos } : { leases: prior.head.leases };
  const sourceBytes = Buffer.from(`primary transaction ${partition}:${seq}\n`);
  if (partition === "todos" && options.resolution !== "abandoned") await writeFile(f.statePath, sourceBytes);
  const source = { kind: partition === "todos" ? "markdown_active_state" : "task_lease_record",
    previous_partition_digest: localAuthorityShadowPartitionDigest(
      partition,
      options.previousPartitionProjection ?? previousPartition,
    ),
    previous_bytes_digest: sha(previous), bytes_digest: sha(sourceBytes), lease: null, event_id: null };
  const entryId = outboxEntryIdentity("goal-a", partition, seq, source.bytes_digest, binding.capture_lineage_id, binding.source_root_digest);
  const preparedAt = `2026-09-06T00:00:${String(seq).padStart(2, "0")}.000Z`;
  const committedAt = preparedAt;
  const directory = join(f.root, "authority-shadow", "outbox", "goal-a", partition);
  await mkdir(directory, { recursive: true });
  const stem = `${String(seq).padStart(10, "0")}-${entryId}`;
  const writer = { runtime: partition === "todos" ? "python" : "typescript", write_class: options.writeClass ?? "todo_add", operation_id: null };
  const recordedProjection = partition === "leases" ? { leases: (part.leases as JsonObject[]).map((record) => ({ file_stem: record.todo_id, record })) } : part;
  const prepared = { schema_version: schemas.LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA, goal_id: "goal-a",
    capture_lineage_id: binding.capture_lineage_id, entry_id: entryId, partition, seq, writer, source,
    source_root_digest: binding.source_root_digest, projection: recordedProjection,
    partition_digest: partition === "todos" ? localAuthorityShadowPartitionDigest(partition, part) : null, prepared_at: preparedAt };
  const preparedBytes = `${JSON.stringify(prepared, null, 2)}\n`;
  await writeFile(join(directory, `${stem}.prepared.json`), preparedBytes);
  const marker = options.marker ?? true;
  const markerBytes = `${JSON.stringify({ schema_version: schemas.LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA,
    capture_lineage_id: binding.capture_lineage_id, entry_id: entryId, committed_at: committedAt }, null, 2)}\n`;
  if (marker) await writeFile(join(directory, `${stem}.committed.json`), markerBytes);
  const resolution = options.resolution ?? "committed";
  const noOp = resolution === "abandoned" || resolution === "unproved";
  return { runtime_root: f.root, goal_id: "goal-a",
    ...(f.goalRef == null ? {} : {goal_ref: f.goalRef}),
    entry: { capture_lineage_id: binding.capture_lineage_id, entry_id: entryId, partition, seq, writer, source,
      source_root_digest: binding.source_root_digest, prepared_at: preparedAt, committed_at: marker ? committedAt : null,
      prepared_sha256: sha(preparedBytes), committed_sha256: marker ? sha(markerBytes) : null, resolution },
    partition_projection: noOp ? null : part,
    partition_digest: noOp ? null : localAuthorityShadowPartitionDigest(partition, part) };
}
export async function settleFiles(f: ShadowFixture, request: JsonObject, result: JsonObject, previousDigest: string | null = null): Promise<void> {
  const entry = request.entry as JsonObject;
  const directory = join(f.root, "authority-shadow", "outbox", "goal-a", String(entry.partition));
  const stem = `${String(entry.seq).padStart(10, "0")}-${entry.entry_id}`;
  await writeFile(join(directory, "drain-cursor.json"), JSON.stringify({
    schema_version: schemas.LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA, partition: entry.partition,
    last_seq: entry.seq, last_entry_id: entry.entry_id, last_partition_digest: request.partition_digest ?? previousDigest,
    last_cursor: result.cursor, last_provider_revision: result.provider_revision,
    updated_at: "2026-09-06T00:00:00Z",
  }));
  await rm(join(directory, `${stem}.prepared.json`));
  await rm(join(directory, `${stem}.committed.json`), { force: true });
}

/** Production delivery sends identity and byte witnesses, not a second projection. */
export function entrySelection(request: JsonObject): JsonObject {
  const entry = request.entry as JsonObject;
  const goalRef = request.goal_ref ?? null;
  return {schema_version: goalRef === null
      ? schemas.LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_REQUEST_SCHEMA
      : schemas.LOCAL_AUTHORITY_SHADOW_EXACT_COMMIT_ENTRY_REQUEST_SCHEMA,
    runtime_root: request.runtime_root, goal_id: request.goal_id,
    ...(goalRef === null ? {} : {goal_ref: goalRef}),
    ...Object.fromEntries(["partition", "seq", "entry_id", "capture_lineage_id", "prepared_sha256", "committed_sha256"]
      .map(key => [key, entry[key]]))};
}
