import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { access, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { canonicalAuthoritySha256 } from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import { TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA } from "../../loopx/control_plane/coordination/coordination_state_contract.generated.ts";
import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import { engageLegacyCoordinationWriterFence } from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import { authorityProjectionFixture } from "./authority_projection_fixture.ts";

const entrypoint = fileURLToPath(
  new URL(
    "../../loopx/control_plane/work_items/task_lease_acquire_cli.ts",
    import.meta.url,
  ),
);

function runCli(input: unknown) {
  const child = spawnSync(
    process.execPath,
    ["--no-warnings", "--experimental-strip-types", entrypoint],
    {
      input: typeof input === "string" ? input : JSON.stringify(input),
      encoding: "utf8",
    },
  );
  assert.equal(child.error, undefined, child.error?.message);
  const lines = child.stdout.trim().split(/\r?\n/u).filter(Boolean);
  assert.equal(lines.length, 1, child.stdout);
  return {
    status: child.status,
    stderr: child.stderr,
    value: JSON.parse(lines[0]) as Record<string, unknown>,
  };
}

async function tempRequest(t: TestContext) {
  const root = await mkdtemp(join(tmpdir(), "loopx-task-lease-cli-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const runtimeRoot = join(root, "runtime");
  const goalId = "goal-cli";
  const todoId = "todo_cli_native";
  const todo = {
    todo_id: todoId,
    role: "agent",
    status: "open",
    done: false,
    text: "Acquire the canonical CLI lease",
    archive_state: "active",
    claimed_by: null,
    excluded_agents: [],
  };
  const projection = authorityProjectionFixture(goalId, [todo], [], "native", {
    handoff_mode: "hard_lease",
  });
  const store = new FileAuthorityStore(
    join(runtimeRoot, "authority", "file-v0"),
    goalId,
  );
  const seeded = await store.commitAuthority({
    operation_id: "seed",
    expected_provider_revision: null,
    events: [],
    receipts: [],
    next_projection: projection,
  });
  assert.equal(seeded.status, "applied");
  if (seeded.status !== "applied") throw new Error("fixture initialization failed");

  const statePath = join(root, "ACTIVE_GOAL_STATE.md");
  const stateContent = "# Synthetic task lease source\n";
  await writeFile(statePath, stateContent, "utf8");
  const fenced = await engageLegacyCoordinationWriterFence({
    schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
    runtime_root: runtimeRoot,
    goal_id: goalId,
    state_path: statePath,
    fence: {
      schema_version: "loopx_legacy_coordination_writer_fence_v0",
      state: "engaged",
      goal_id: goalId,
      fence_id: "task-lease-cli-fixture",
      source_version: "state:1",
      source_projection_sha256: canonicalAuthoritySha256(projection),
      expected_shadow_provider_revision: seeded.provider_revision,
    },
  });
  assert.equal(fenced.status, "applied");

  const input = {
    schema_version: TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA,
    runtime_root: runtimeRoot,
    goal_id: "goal-cli",
    todo_id: todoId,
    owner: "agent-cli",
    idempotency_key: "turn-cli",
    ttl_seconds: 120,
    write_scopes: ["docs/**"],
    expected_version: null,
    authority: {
      handoff_mode: "hard_lease",
      registered_agent_candidates: [["agent-cli"]],
      todos: [todo],
      todo_projection_error: null,
      source_receipts: [{
        source_id: "state",
        path: statePath,
        state: "file",
        sha256: createHash("sha256").update(stateContent).digest("hex"),
      }],
    },
  };
  return { input, runtimeRoot, store };
}

test("native task-lease command writes and replays one JSON envelope", async (t) => {
  const { input, runtimeRoot, store } = await tempRequest(t);
  const first = runCli(input);
  assert.equal(first.status, 0);
  assert.equal(first.value.ok, true);
  assert.equal(first.value.acquired, true);
  assert.equal(first.value.status, "applied");
  assert.equal(first.value.source_authority, "file_v0");
  assert.equal(first.value.lease_path, undefined);

  const committed = await store.loadAuthority();
  assert.equal(committed.status, "loaded");
  if (committed.status !== "loaded") throw new Error("canonical lease was not stored");
  const leases = committed.head.leases as Record<string, unknown>[];
  assert.equal(leases.length, 1);
  assert.equal(leases[0].todo_id, "todo_cli_native");
  assert.equal(leases[0].owner, "agent-cli");
  assert.equal(leases[0].version, 1);

  const replay = runCli(input);
  assert.equal(replay.status, 0);
  assert.equal(replay.value.status, "replayed");
  assert.equal(replay.value.idempotent, true);
  assert.equal(replay.value.acquired, false);
  assert.equal(
    (replay.value.settlement as Record<string, unknown>).effect_id,
    (first.value.settlement as Record<string, unknown>).effect_id,
  );
  assert.deepEqual(replay.value.original_receipt, first.value.original_receipt);
  assert.deepEqual(await store.loadAuthority(), committed);
  const history = await store.scanCommitted(null, 10);
  assert.equal(history.status, "page");
  if (history.status === "page") assert.equal(history.transactions.length, 2);
  await assert.rejects(access(join(
    runtimeRoot,
    "goals",
    "goal-cli",
    "task-leases",
    "todo_cli_native.json",
  )), { code: "ENOENT" });
});

test("native task-lease command returns one typed malformed-input envelope", () => {
  const malformed = runCli("not-json");
  assert.notEqual(malformed.status, 0);
  assert.equal(malformed.value.schema_version, "task_lease_v0");
  assert.equal(malformed.value.action, "acquire");
  assert.equal(malformed.value.error_code, "invalid_json");
});
