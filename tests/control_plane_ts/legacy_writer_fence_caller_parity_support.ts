/**
 * Row executor for the fenced sibling-caller parity table.
 *
 * One implementation drives both the test and the fixture recorder: every row
 * builds an isolated workspace, applies one fence state, runs one native
 * task-lease caller, and reports the complete envelope together with an
 * exclusion-free effect snapshot of the runtime root. Expectations live in
 * tests/fixtures/control_plane/legacy_writer_fence_caller_parity_v0.json.
 */
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative, sep } from "node:path";

import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { atomicWriteJson } from "../../loopx/control_plane/effect_runtime_io.ts";
import {
  LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
  LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
} from "../../loopx/control_plane/coordination/coordination_state_contract.generated.ts";
import {
  engageLegacyCoordinationWriterFence,
  legacyCoordinationWriterFencePath,
} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import { executeTaskLeaseAcquire } from "../../loopx/control_plane/work_items/task_lease_acquire.ts";
import {
  executeTaskLeaseLifecycle,
  TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,
} from "../../loopx/control_plane/work_items/task_lease_lifecycle.ts";

export type FenceState = "absent" | "engaged" | "invalid" | "unreadable";
export type ParityCaller =
  | "acquire"
  | "renew"
  | "transfer"
  | "release"
  | "terminal_verify_auto_acquire"
  | "terminal_verify_auto_acquire_keyed"
  | "fence_close_release";

export interface ParityRow {
  id: string;
  caller: ParityCaller;
  fence_state: FenceState;
}

export interface EffectDiff {
  added: string[];
  removed: string[];
  changed: string[];
}

export interface Observation {
  envelope: JsonObject;
  effect: EffectDiff;
  /** Parsed JSON of every added or changed file, keyed by runtime-relative path. */
  artifacts: Record<string, JsonObject>;
  /** The identical request issued a second time; fence-close rows only. */
  retry: JsonObject | null;
  /** Runtime root of the workspace, for declared after-state checks. */
  runtime_root: string;
}

export const GOAL = "goal-a";
export const RUNTIME_ROOT_PLACEHOLDER = "{runtime_root}";
const FENCE_ID = "legacy-writer-fence:goal-a:state-1";
const PARITY_KEY = "lease:parity";
/** Fixed clock so lease timestamps in the fixture are literal. */
const FIXED_NOW = new Date("2026-09-07T00:00:00.000Z");
const CLOCK = { now: () => FIXED_NOW };

const FENCED_CALLERS: ParityCaller[] = [
  "acquire",
  "renew",
  "transfer",
  "release",
  "terminal_verify_auto_acquire",
  "fence_close_release",
];

export const TS_ROWS: ParityRow[] = [
  ...FENCED_CALLERS.flatMap((caller) =>
    (["engaged", "invalid", "unreadable"] as const).map((fence_state) => ({
      id: `ts-${caller}-${fence_state}`,
      caller,
      fence_state,
    }))
  ),
  { id: "ts-terminal_verify_auto_acquire_keyed-engaged", caller: "terminal_verify_auto_acquire_keyed", fence_state: "engaged" },
  { id: "ts-acquire-absent", caller: "acquire", fence_state: "absent" },
  { id: "ts-release-absent", caller: "release", fence_state: "absent" },
];

interface Workspace {
  root: string;
  runtimeRoot: string;
  statePath: string;
  authority: JsonObject;
  lockToken: string | null;
  fenceOperationId: string | null;
  leaseVersion: number;
  leaseEpoch: number;
}

async function snapshot(root: string): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  async function walk(dir: string): Promise<void> {
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) {
        await walk(path);
      } else {
        // Effect keys use the fixture's POSIX spelling on every host.
        out[relative(root, path).split(sep).join("/")] = createHash("sha256").update(await readFile(path)).digest("hex");
      }
    }
  }
  await walk(root);
  return out;
}

function diff(before: Record<string, string>, after: Record<string, string>): EffectDiff {
  return {
    added: Object.keys(after).filter((key) => !(key in before)).sort(),
    removed: Object.keys(before).filter((key) => !(key in after)).sort(),
    changed: Object.keys(after).filter((key) => key in before && before[key] !== after[key]).sort(),
  };
}

async function workspace(): Promise<Workspace> {
  const root = await mkdtemp(join(tmpdir(), "loopx-fence-parity-"));
  const runtimeRoot = join(root, "runtime");
  await mkdir(runtimeRoot, { recursive: true });
  const statePath = join(root, "ACTIVE_GOAL_STATE.md");
  await writeFile(statePath, "---\ngoal_id: goal-a\nhandoff_mode: hard_lease\n---\n\n## Agent Todo\n\n", "utf8");
  const authorityPath = join(root, "authority-source.json");
  await writeFile(authorityPath, "authority-v1", "utf8");
  const authority: JsonObject = {
    handoff_mode: "hard_lease",
    registered_agent_candidates: [["agent-a", "agent-b"]],
    todos: [
      { todo_id: "todo_abc", status: "open", claimed_by: "agent-a", role: "agent", task_class: "advancement_task" },
      { todo_id: "todo_gate", status: "open", claimed_by: null, role: "user", task_class: "user_gate" },
    ],
    todo_projection_error: null,
    source_receipts: [{
      source_id: "authority",
      path: authorityPath,
      state: "file",
      sha256: createHash("sha256").update("authority-v1").digest("hex"),
    }],
  };
  return { root, runtimeRoot, statePath, authority, lockToken: null, fenceOperationId: null, leaseVersion: 0, leaseEpoch: 0 };
}

function lifecycleRequest(ws: Workspace, operation: string, extra: JsonObject): JsonObject {
  return {
    schema_version: TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,
    operation,
    runtime_root: ws.runtimeRoot,
    goal_id: GOAL,
    todo_id: "todo_abc",
    owner: "agent-a",
    idempotency_key: PARITY_KEY,
    expected_version: ws.leaseVersion,
    new_owner: null,
    new_idempotency_key: null,
    authority: ws.authority,
    ...extra,
  };
}

async function acquireParityLease(ws: Workspace): Promise<void> {
  const acquired = await executeTaskLeaseAcquire({
    schema_version: "loopx_task_lease_acquire_native_v0",
    runtime_root: ws.runtimeRoot,
    goal_id: GOAL,
    todo_id: "todo_abc",
    owner: "agent-a",
    idempotency_key: PARITY_KEY,
    write_scopes: [],
    ttl_seconds: 600,
    expected_version: null,
    authority: ws.authority,
  }, CLOCK);
  if (acquired.ok !== true) throw new Error(`parity fixture acquire failed: ${JSON.stringify(acquired)}`);
  const lease = acquired.lease as JsonObject;
  ws.leaseVersion = lease.version as number;
  ws.leaseEpoch = lease.lease_epoch as number;
}

async function holdParityFence(ws: Workspace): Promise<void> {
  const verify = await executeTaskLeaseLifecycle(lifecycleRequest(ws, "terminal_verify", {}), CLOCK);
  const fence = verify.fence as JsonObject | undefined;
  if (verify.ok !== true || !fence) throw new Error(`parity fixture verify failed: ${JSON.stringify(verify)}`);
  ws.lockToken = String(fence.lock_token);
  ws.fenceOperationId = String(fence.fence_operation_id);
}

function fenceRecord(state: "engaged" | "disengaged"): JsonObject {
  return {
    schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
    state,
    goal_id: GOAL,
    fence_id: FENCE_ID,
    source_version: "state:1",
    source_projection_sha256: "a".repeat(64),
    expected_shadow_provider_revision: "file:1:aaaaaaaaaaaaaaaaaaaaaaaa",
  };
}

async function applyFence(ws: Workspace, state: FenceState, lockHeld: boolean): Promise<void> {
  const fencePath = legacyCoordinationWriterFencePath(ws.runtimeRoot, GOAL);
  if (state === "absent") return;
  if (state === "unreadable") {
    await mkdir(fencePath, { recursive: true });
    return;
  }
  if (state === "invalid") {
    await atomicWriteJson(fencePath, fenceRecord("disengaged"));
    return;
  }
  if (lockHeld) {
    // A held terminal fence owns the lease lock, so the locked engage path
    // cannot run; persist the exact record engagement would write.
    await atomicWriteJson(fencePath, fenceRecord("engaged"));
    return;
  }
  const engaged = await engageLegacyCoordinationWriterFence({
    schema_version: LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
    runtime_root: ws.runtimeRoot,
    goal_id: GOAL,
    state_path: ws.statePath,
    fence: fenceRecord("engaged"),
  });
  if (engaged.status !== "applied") throw new Error(`parity fixture engage failed: ${JSON.stringify(engaged)}`);
}

function callerRequest(ws: Workspace, caller: ParityCaller): { kind: "acquire" | "lifecycle"; request: JsonObject } {
  switch (caller) {
    case "acquire":
      return {
        kind: "acquire",
        request: {
          schema_version: "loopx_task_lease_acquire_native_v0",
          runtime_root: ws.runtimeRoot,
          goal_id: GOAL,
          todo_id: "todo_abc",
          owner: "agent-a",
          idempotency_key: "lease:parity-acquire",
          write_scopes: [],
          ttl_seconds: 600,
          expected_version: null,
          authority: ws.authority,
        },
      };
    case "renew":
      return { kind: "lifecycle", request: lifecycleRequest(ws, "renew", { ttl_seconds: 600 }) };
    case "transfer":
      return {
        kind: "lifecycle",
        request: lifecycleRequest(ws, "transfer", { ttl_seconds: 600, new_owner: "agent-b", new_idempotency_key: "lease:parity-transfer" }),
      };
    case "release":
      return { kind: "lifecycle", request: lifecycleRequest(ws, "release", {}) };
    case "terminal_verify_auto_acquire":
      return {
        kind: "lifecycle",
        request: lifecycleRequest(ws, "terminal_verify", {
          todo_id: "todo_gate",
          idempotency_key: null,
          expected_version: null,
          allow_user_gate_auto_acquire: true,
        }),
      };
    case "terminal_verify_auto_acquire_keyed":
      return {
        kind: "lifecycle",
        request: lifecycleRequest(ws, "terminal_verify", {
          todo_id: "todo_gate",
          idempotency_key: "turn-1",
          expected_version: null,
          allow_user_gate_auto_acquire: true,
        }),
      };
    case "fence_close_release":
      return {
        kind: "lifecycle",
        request: lifecycleRequest(ws, "fence_close", {
          idempotency_key: null,
          expected_version: null,
          lock_token: ws.lockToken,
          fence_operation_id: ws.fenceOperationId,
          committed: true,
          release_lease: true,
          fence_owner: "agent-a",
          fence_idempotency_key: PARITY_KEY,
          fence_expected_version: ws.leaseVersion,
          fence_expected_lease_epoch: ws.leaseEpoch,
          owner_pid: process.pid,
        }),
      };
  }
}

async function execute(ws: Workspace, caller: ParityCaller): Promise<JsonObject> {
  const { kind, request } = callerRequest(ws, caller);
  return kind === "acquire"
    ? await executeTaskLeaseAcquire(request, CLOCK) as JsonObject
    : await executeTaskLeaseLifecycle(request, CLOCK);
}

/**
 * Replace the temporary runtime root inside an envelope with a stable
 * placeholder, and spell the path under it with the fixture's POSIX separators.
 */
export function normalize(value: unknown, runtimeRoot: string): JsonObject {
  const text = JSON.stringify(value)
    .split(JSON.stringify(runtimeRoot).slice(1, -1)).join(RUNTIME_ROOT_PLACEHOLDER);
  return JSON.parse(text, (_key, field: unknown) =>
    typeof field === "string" && field.startsWith(RUNTIME_ROOT_PLACEHOLDER)
      ? field.split(sep).join("/")
      : field);
}

export async function observeRow(row: ParityRow): Promise<Observation> {
  const ws = await workspace();
  const needsLease = ["renew", "transfer", "release", "fence_close_release"].includes(row.caller);
  if (needsLease) await acquireParityLease(ws);
  const holdsFence = row.caller === "fence_close_release";
  if (holdsFence) await holdParityFence(ws);
  await applyFence(ws, row.fence_state, holdsFence);
  const before = await snapshot(ws.runtimeRoot);
  const envelope = await execute(ws, row.caller);
  const after = await snapshot(ws.runtimeRoot);
  const effect = diff(before, after);
  const artifacts: Record<string, JsonObject> = {};
  for (const path of [...effect.added, ...effect.changed]) {
    if (!path.endsWith(".json")) continue;
    artifacts[path] = normalize(JSON.parse(await readFile(join(ws.runtimeRoot, path), "utf8")), ws.runtimeRoot);
  }
  const retry = holdsFence ? normalize(await execute(ws, row.caller), ws.runtimeRoot) : null;
  return { envelope: normalize(envelope, ws.runtimeRoot), effect, artifacts, retry, runtime_root: ws.runtimeRoot };
}
