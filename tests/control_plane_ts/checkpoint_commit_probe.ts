/** Isolated process barriers around the production persistence methods. No
 * runtime test flag or alternate store implementation enters product code. */
import {existsSync, readFileSync, writeFileSync} from "node:fs";
import fs from "node:fs";
import {syncBuiltinESMExports} from "node:module";
import {join} from "node:path";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import {openLocalAuthorityStore} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

let raw = "";
for await (const chunk of process.stdin) raw += chunk;
const input = JSON.parse(raw) as {mode: string; barrier: string; provider: string;
  method: string; params: JsonObject; repeat?: boolean; fault?: string; provider_direct?: boolean};
if (input.fault?.startsWith("after_")) {
  const descriptors = new Map<number, string>();
  const open = fs.openSync;
  const flush = fs.fsyncSync;
  fs.openSync = ((path, ...args) => {
    const fd = open(path, ...args);
    descriptors.set(fd, String(path));
    return fd;
  }) as typeof fs.openSync;
  fs.fsyncSync = fd => {
    flush(fd);
    const path = descriptors.get(fd) ?? "";
    const suffix = input.fault === "after_json" ? ".json" : input.fault === "after_markdown" ? ".md" : "index.jsonl";
    if (path.includes("runs") && path.endsWith(suffix)) process.exit(86);
  };
  syncBuiltinESMExports();
}
if (input.fault === "before_artifacts") {
  const rename = fs.renameSync;
  fs.renameSync = (from, to) => {
    rename(from, to);
    if (String(to).includes("checkpoint-contexts") &&
        JSON.parse(readFileSync(to, "utf8")).commit_attempt != null) process.exit(86);
  };
  syncBuiltinESMExports();
}
const signal = (name: string, value: unknown = true) => writeFileSync(join(input.barrier, name), JSON.stringify(value));
const pause = () => {
  const deadline = Date.now() + 20000;
  const buffer = new Int32Array(new SharedArrayBuffer(4));
  while (!existsSync(join(input.barrier, "release"))) {
    if (Date.now() >= deadline) throw new Error("checkpoint test barrier timed out");
    Atomics.wait(buffer, 0, 0, 10);
  }
};
const prototype = input.provider === "sqlite" ? SqliteAuthorityStore.prototype : FileAuthorityStore.prototype;
const commitCheckpoint = async (params: JsonObject): Promise<JsonObject> =>
  await (await import("../../loopx/control_plane/goals/checkpoint_commit.ts")).commitCheckpoint(params);
if (input.mode === "checkpoint") {
  const original = prototype.withCheckpointHead;
  prototype.withCheckpointHead = function(save) {
    return original.call(this as SqliteAuthorityStore & FileAuthorityStore, (head, identity) => {
      signal("head-read", {provider_revision: head.provider_revision, pid: process.pid});
      pause();
      if (input.fault === "throw") throw new Error("synthetic checkpoint save failure");
      const result = save(head, identity);
      signal("checkpoint-saved", result);
      return result;
    });
  };
} else if (input.mode === "writer") {
  const original = prototype.commitAuthority;
  prototype.commitAuthority = async function(commit) {
    const started = performance.now();
    signal("writer-entered");
    const result = await original.call(this as SqliteAuthorityStore & FileAuthorityStore, commit);
    signal("provider-result", {...result, elapsed_ms: performance.now() - started});
    return result;
  };
}
try {
  async function writer(): Promise<unknown> {
    if (!input.provider_direct) return await dispatchEffectRuntimeMethod(
      createEffectRuntimeHandlers({fingerprint: "checkpoint-test", requestShutdown() {}}), input.method, input.params);
    // Deliberately exercise the exported provider-neutral transaction directly.
    // The ordinary CLI wrapper ALREADY holds M; do not claim this lower-level
    // test reproduces a race through that protected CLI path.
    const p = input.params;
    const store = await openLocalAuthorityStore(String(p.runtime_root), String(p.goal_id));
    return {...await executeCoordinationTodoUpdate(store, {
      goal_id: String(p.goal_id), todo_id: String(p.todo_id), expected_role: null,
      actor_agent_id: String(p.actor_agent_id), registered_agents: p.registered_agents as string[],
      operation_id: String(p.operation_id), lease_idempotency_key: String(p.lease_idempotency_key),
      lease_expected_version: Number(p.lease_expected_version), patch: p.patch as JsonObject,
      clear_fields: [], dry_run: false, now: new Date(String(p.observed_at)),
    }), source_authority: input.provider + "_v0", decision_read_from_provider: true, legacy_fallback_used: false};
  }
  const result = input.mode === "checkpoint" ? await commitCheckpoint(input.params)
    : await writer();
  if (input.repeat) {
    const replay = await commitCheckpoint(input.params);
    signal("runtime-replay", replay);
  }
  process.stdout.write(JSON.stringify(result));
} catch (error) {
  process.stdout.write(JSON.stringify({error: error instanceof Error ? error.message : String(error),
    error_code: (error as {code?: string}).code}));
  process.exitCode = 1;
}
