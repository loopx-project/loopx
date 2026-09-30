/** Filesystem effects for native drain. Receipt proof, not a cursor or filename,
 * authorizes cleanup. All callers hold the shadow maintenance guard. */
import {spawn, type ChildProcessWithoutNullStreams} from "node:child_process";
import {createInterface} from "node:readline";
import {lstat, readFile, readdir, unlink} from "node:fs/promises";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import type {JsonObject} from "../effect_program.ts";
import {withFileMutationLock} from "../effect_runtime_io.ts";
import {EffectRuntimeLockTimeoutError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {ShadowLineageError} from "./local_authority_shadow.ts";
import {outboxEntryIdentity, OUTBOX_ENTRY_FILE_PATTERN} from "./local_authority_shadow_identity.ts";
import {sha256Digest, readOutboxCursor, outboxPartitionDirectory} from "./local_authority_shadow_outbox.ts";
import {legacyCoordinationTodoLockPath, taskLeaseLockPath} from "./legacy_writer_lock_paths.ts";
import {readShadowBootstrapSourcePath, type ShadowCaptureBinding} from "./shadow_management.ts";
import {LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA, LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA} from "./coordination_state_contract.generated.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

export type DrainPartition = "todos" | "leases";
export interface DrainEntry {
  entry_id: string; seq: number; prepared: boolean; capture_lineage_id: string | null;
  prepared_sha256: string | null; committed_sha256: string | null;
}
export interface DrainInventory {cursor: JsonObject | null; entries: DrainEntry[]; files: Map<string, [string, string][]>}
function ensure(value: unknown): asserts value {if (!value) throw new ShadowLineageError("outbox_file_invalid");}

export async function drainInventory(root: string, goal: string, partition: DrainPartition): Promise<DrainInventory> {
  const directory = outboxPartitionDirectory(root, goal, partition);
  const entries = new Map<number, DrainEntry>();
  const files = new Map<string, [string, string][]>();
  let names: string[];
  try { names = await readdir(directory); }
  catch (error) {if ((error as NodeJS.ErrnoException).code === "ENOENT") names = []; else throw error;}
  const records = new Map<string, JsonObject>();
  for (const name of names.sort()) {
    const path = join(directory, name), info = await lstat(path);
    ensure(info.isFile() && !info.isSymbolicLink());
    if (name === "drain-cursor.json") continue;
    const match = OUTBOX_ENTRY_FILE_PATTERN.exec(name);
    ensure(match !== null && Number(match[1]) > 0);
    const seq = Number(match[1]), id = match[2], phase = match[3];
    const entry = entries.get(seq) ?? {entry_id: id, seq, prepared: false, capture_lineage_id: null,
      prepared_sha256: null, committed_sha256: null};
    ensure(entry.entry_id === id);
    const bytes = await readFile(path), digest = sha256Digest(bytes);
    let record: JsonObject;
    try {record = requireJsonObject(JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes)), "outbox record");}
    catch {throw new ShadowLineageError("outbox_file_invalid");}
    ensure(record.entry_id === id && typeof record.capture_lineage_id === "string" && record.capture_lineage_id.length > 0);
    if (phase === "prepared") {
      const source = requireJsonObject(record.source, "source"), writer = requireJsonObject(record.writer, "writer");
      const ref = typeof source.bytes_digest === "string" && source.bytes_digest ? source.bytes_digest
        : typeof source.event_id === "string" && source.event_id ? `event:${source.event_id}`
        : typeof record.partition_digest === "string" && record.partition_digest ? `seed:${record.partition_digest}` : null;
      ensure(record.schema_version === LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA && record.seq === seq &&
        record.goal_id === goal && record.partition === partition &&
        ["python", "typescript"].includes(String(writer.runtime)) && typeof writer.write_class === "string" && writer.write_class.length > 0 &&
        ["markdown_active_state", "state_event_log", "task_lease_record"].includes(String(source.kind)) &&
        typeof record.source_root_digest === "string" && ENVELOPED_SHA256_PATTERN.test(record.source_root_digest) && ref !== null &&
        outboxEntryIdentity(goal, partition, seq, ref, record.capture_lineage_id, record.source_root_digest) === id);
      entry.prepared = true; entry.capture_lineage_id = record.capture_lineage_id; entry.prepared_sha256 = digest;
    } else {
      ensure(record.schema_version === LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA);
      entry.committed_sha256 = digest;
    }
    entries.set(seq, entry); records.set(`${id}:${phase}`, record);
    files.set(id, [...(files.get(id) ?? []), [path, digest]]);
  }
  for (const entry of entries.values()) {
    const marker = records.get(`${entry.entry_id}:committed`);
    ensure(!entry.prepared || marker === undefined || marker.capture_lineage_id === entry.capture_lineage_id);
  }
  return {cursor: await readOutboxCursor(directory, partition), entries: [...entries.values()].sort((a,b) => a.seq-b.seq), files};
}

export async function verifyDrainFiles(files: [string, string][]): Promise<void> {
  for (const [path, digest] of files) {
    const info = await lstat(path);
    if (!info.isFile() || info.isSymbolicLink() || sha256Digest(await readFile(path)) !== digest)
      throw new ShadowLineageError("outbox_file_changed");
  }
}
export async function reclaimDrainFiles(files: [string, string][], afterUnlink?: () => Promise<void>): Promise<number> {
  await verifyDrainFiles(files);
  // Prepared first deliberately permits committed-only residue after a crash.
  for (const [path] of [...files].sort(([a],[b]) => Number(b.endsWith(".prepared.json"))-Number(a.endsWith(".prepared.json")))) {
    await unlink(path); await afterUnlink?.();
  }
  return files.length;
}

/** One lazy OS-lock process per batch. It holds no locks between sections. */
export class DrainKernelLockHost {
  private readonly python: string;
  private child: ChildProcessWithoutNullStreams | null = null;
  private replies: AsyncIterator<string> | null = null;
  private exited: Promise<void> | null = null;
  constructor(python: string) {this.python = python;}

  private async command(line: string): Promise<string> {
    if (!this.child) {
      this.child = spawn(this.python, ["-m", "loopx.control_plane.coordination.shadow_lock_host"], {
        stdio: ["pipe", "pipe", "pipe"], env: {...process.env, PYTHONPATH: fileURLToPath(new URL("../../../", import.meta.url))},
      });
      this.exited = new Promise(resolve => this.child!.once("close", () => resolve()));
      this.child.on("error", () => {}); // The closed reply stream reports an unavailable host.
      this.child.stdin.on("error", () => {});
      this.child.stderr.resume();
      this.replies = createInterface({input: this.child.stdout})[Symbol.asyncIterator]();
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      this.child.stdin.write(line + "\n");
      const reply = await Promise.race([
        this.replies!.next().then(item => item.done ? "failed" : item.value),
        this.exited!.then(() => "failed"),
        new Promise<string>(resolve => {timer = setTimeout(() => resolve("failed"), 5000);}),
      ]);
      if (reply === "failed") throw new ShadowLineageError("shadow_lock_host_unavailable");
      return reply;
    } finally {clearTimeout(timer);}
  }

  async withLocks<T>(paths: string[], operation: () => Promise<T>): Promise<T> {
    const state = await this.command(JSON.stringify(paths));
    if (state !== "held") throw new ShadowLineageError(state === "busy" ? "primary_writer_busy" : "shadow_lock_host_unavailable");
    try {return await operation();}
    finally {
      if (await this.command("release") !== "released") throw new ShadowLineageError("shadow_lock_host_unavailable");
    }
  }

  async close(): Promise<void> {
    if (this.child) {
      this.child.stdin.end(); this.child.kill(); await this.exited;
    }
  }
}

/** Maintain the existing M → primary marker → kernel-lock order, with no wait
 * for a busy primary writer. Killing the TS process closes the pipe as well. */
export async function withDrainPrimary<T>(root: string, goal: string, partition: DrainPartition,
  binding: ShadowCaptureBinding, kernel: DrainKernelLockHost, operation: () => Promise<T>): Promise<T> {
  const paths = partition === "todos" ? [legacyCoordinationTodoLockPath(root, goal), await readShadowBootstrapSourcePath(root, goal, binding)]
    : [taskLeaseLockPath({runtime_root: root, goal_id: goal})];
  const lock = (i: number): Promise<T> => i === paths.length ? kernel.withLocks(paths, operation)
    : withFileMutationLock(paths[i], () => lock(i + 1), 0);
  try {return await lock(0);} catch (error) {
    if (error instanceof EffectRuntimeLockTimeoutError) throw new ShadowLineageError("primary_writer_busy");
    throw error;
  }
}
