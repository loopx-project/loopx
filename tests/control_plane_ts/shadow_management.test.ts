import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, readdir, mkdir, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import { canonicalAuthoritySha256 } from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {
  bootstrapManagedShadow, rollbackManagedShadow, readShadowManagementState,
  readShadowBootstrapSourcePath, requireShadowPrimaryWriteAllowed, shadowMaintenanceLockPath,
  ShadowManagementError,
} from "../../loopx/control_plane/coordination/shadow_management.ts";

const primary = {
  withPrimaryLocks: async <T>(fn: () => Promise<T>) => await fn(),
  verifySourceSnapshot: async () => {},
};

function bootstrap(root: string, operationId = "bootstrap:initial") {
  return {
    runtime_root: root, goal_id: "goal-a", operation_id: operationId,
    source_version: "state:1", source_snapshot: { state_path: join(root, "state.md") },
    projection: { schema_version: "loopx_coordination_shadow_projection_v0", goal_id: "goal-a", todos: [], leases: [] },
  };
}

test("existing-only identity reads do not materialize a missing store", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-management-existing-"));
  const store = new FileAuthorityStore(join(root, "missing"), "goal-a", { existingOnly: true });
  assert.equal((await store.storeIdentity()).status, "unavailable");
  assert.equal((await store.loadAuthority()).status, "missing");
  assert.deepEqual(await readdir(root), []);
});

test("runtime-root aliases survive restart and retain legacy shadow bindings", async () => {
  const parent = await mkdtemp(join(tmpdir(), "loopx-management-root-alias-"));
  const root = join(parent, "runtime");
  const alias = join(parent, "runtime-alias");
  await mkdir(root);
  await symlink(root, alias, process.platform === "win32" ? "junction" : "dir");
  const applied = await bootstrapManagedShadow(bootstrap(alias), primary);
  assert.equal(applied.status, "applied", JSON.stringify(applied));
  assert.deepEqual(await requireShadowPrimaryWriteAllowed(root, "goal-a"), {
    capture_profile: applied.capture_profile,
    capture_lineage_id: applied.capture_lineage_id,
    source_root_digest: applied.source_root_digest,
    store_identity: applied.store_identity,
    bootstrap_operation_id: applied.bootstrap_operation_id,
    bootstrap_provider_revision: applied.bootstrap_provider_revision,
  });

  // Releases before canonical-root hashing persisted the lexical alias. Keep
  // those active lineages readable only when their immutable manifest proves
  // that the old path resolves to this exact runtime root.
  const statePath = join(shadowMaintenanceLockPath(root, "goal-a"), "..", "state.json");
  const state = JSON.parse(await readFile(statePath, "utf8"));
  const operations = join(shadowMaintenanceLockPath(root, "goal-a"), "..", "operations");
  const [operation] = await readdir(operations);
  const manifestPath = join(operations, operation, "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const legacyDigest = `sha256:${createHash("sha256").update(resolve(alias), "utf8").digest("hex")}`;
  manifest.source_root_digest = legacyDigest;
  state.source_root_digest = legacyDigest;
  state.binding.source_root_digest = legacyDigest;
  state.result.source_root_digest = legacyDigest;
  state.operation.manifest_digest = `sha256:${canonicalAuthoritySha256(manifest)}`;
  await writeFile(manifestPath, JSON.stringify(manifest));
  await writeFile(statePath, JSON.stringify(state));
  const legacyBinding = await requireShadowPrimaryWriteAllowed(root, "goal-a");
  assert.equal(legacyBinding?.source_root_digest, legacyDigest);
  assert.ok(legacyBinding);
  assert.equal(await readShadowBootstrapSourcePath(root, "goal-a", legacyBinding), join(alias, "state.md"));
});

async function killAt(kind: "bootstrap" | "rollback", request: object, phase: string): Promise<void> {
  const worker = fileURLToPath(new URL("./fixtures/shadow_management_crash_worker.ts", import.meta.url));
  const child = spawn(process.execPath, ["--no-warnings", "--experimental-strip-types", worker, kind, JSON.stringify(request), phase], { stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  let stderr = "";
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => { child.kill("SIGKILL"); reject(new Error(`crash barrier timed out: ${output} ${stderr}`)); }, 10000);
    child.stderr.on("data", (data) => { stderr += String(data); });
    child.stdout.on("data", (data) => {
      output += String(data);
      if (output.includes(`ready:${phase}\n`)) child.kill("SIGKILL");
    });
    child.on("error", (error) => { clearTimeout(timer); reject(error); });
    child.on("exit", (_code, signal) => {
      clearTimeout(timer);
      if (signal !== "SIGKILL" || !output.includes(`ready:${phase}\n`)) reject(new Error(`worker exited before ${phase}: ${output} ${stderr}`));
      else resolve();
    });
  });
}

test("bootstrap retries an orphan manifest after real SIGKILL without replacing its lineage", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-management-bootstrap-orphan-"));
  const request = bootstrap(root);
  await killAt("bootstrap", request, "bootstrap_manifest_orphan");
  assert.equal(await readShadowManagementState(root, "goal-a"), null);
  const directory = join(shadowMaintenanceLockPath(root, "goal-a"), "..", "operations");
  const [operation] = await readdir(directory);
  const path = join(directory, operation, "manifest.json");
  const bytes = await readFile(path);
  const manifest = JSON.parse(bytes.toString("utf8"));
  const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a", { existingOnly: true });
  assert.equal((await store.storeIdentity()).status, "unavailable");
  assert.equal((await store.loadAuthority()).status, "missing");
  const retried = await bootstrapManagedShadow(request, primary);
  assert.equal(retried.status, "applied", JSON.stringify(retried));
  assert.equal(retried.capture_lineage_id, manifest.capture_lineage_id);
  assert.deepEqual(await readFile(path), bytes);
  const replayed = await bootstrapManagedShadow(request, primary);
  assert.equal(replayed.status, "replayed");
  assert.equal(replayed.capture_lineage_id, manifest.capture_lineage_id);
  const page = await store.scanCommitted(null, 10);
  assert.equal(page.status, "page");
  if (page.status === "page") assert.equal(page.transactions.length, 1);
});

test("rollback retries an orphan manifest after real SIGKILL and archives the exact target", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-management-rollback-orphan-"));
  const seed = await bootstrapManagedShadow(bootstrap(root), primary);
  assert.equal(seed.status, "applied");
  const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a");
  const other = new FileAuthorityStore(store.directory, "goal-b");
  await other.commitAuthority({ expected_provider_revision: null, operation_id: "other-goal", events: [], next_projection: { goal_id: "goal-b" }, receipts: [] });
  const candidateBytes = await readFile(store.path);
  const identityBytes = await readFile(store.identityPath);
  const otherBytes = await readFile(other.path);
  const pending = join(root, "authority-shadow", "outbox", "goal-a", "todos");
  await mkdir(pending, { recursive: true });
  await writeFile(join(pending, "entry.prepared.json"), "pending source before intent");
  await writeFile(join(pending, "drain-cursor.json"), "{malformed retained");
  const request = { runtime_root: root, goal_id: "goal-a", operation_id: "rollback:orphan", expected_provider_revision: seed.provider_revision };
  await killAt("rollback", request, "rollback_manifest_orphan");
  assert.equal((await readShadowManagementState(root, "goal-a"))?.status, "active");
  assert.deepEqual(await readFile(store.path), candidateBytes);
  assert.equal(await readFile(join(pending, "entry.prepared.json"), "utf8"), "pending source before intent");
  const directory = join(shadowMaintenanceLockPath(root, "goal-a"), "..", "operations");
  const manifests = await Promise.all((await readdir(directory)).map(async (operation) => {
    const path = join(directory, operation, "manifest.json");
    const bytes = await readFile(path);
    return { path, bytes, value: JSON.parse(bytes.toString("utf8")) };
  }));
  const orphan = manifests.find((item) => item.value.kind === "rollback");
  assert.ok(orphan);
  const retried = await rollbackManagedShadow(request, primary);
  assert.equal(retried.status, "applied", JSON.stringify(retried));
  assert.deepEqual(await readFile(orphan.path), orphan.bytes);
  assert.deepEqual(await readFile(String(retried.candidate_archive_path)), candidateBytes);
  assert.equal(await readFile(join(String(retried.outbox_archive_path), "todos", "entry.prepared.json"), "utf8"), "pending source before intent");
  assert.equal(await readFile(join(String(retried.outbox_archive_path), "todos", "drain-cursor.json"), "utf8"), "{malformed retained");
  assert.deepEqual(await readFile(store.identityPath), identityBytes);
  assert.deepEqual(await readFile(other.path), otherBytes);
  assert.equal((await rollbackManagedShadow(request, primary)).status, "replayed");
  assert.equal(await requireShadowPrimaryWriteAllowed(root, "goal-a"), null);
});

for (const phase of ["bootstrap_prepared", "bootstrap_candidate_committed", "bootstrap_outbox_ready", "bootstrap_complete"]) {
  test(`bootstrap recovers a real SIGKILL at ${phase}`, async () => {
    const root = await mkdtemp(join(tmpdir(), "loopx-management-bootstrap-kill-"));
    const request = bootstrap(root);
    await killAt("bootstrap", request, phase);
    const before = await readShadowManagementState(root, "goal-a");
    if (phase !== "bootstrap_complete") await assert.rejects(requireShadowPrimaryWriteAllowed(root, "goal-a"), { code: "shadow_management_in_progress" });
    const recovered = await bootstrapManagedShadow(request, primary);
    assert.equal(recovered.status, phase === "bootstrap_complete" ? "replayed" : "recovered");
    assert.equal(recovered.cursor, "1");
    assert.equal((await readShadowManagementState(root, "goal-a"))?.binding?.capture_lineage_id, recovered.capture_lineage_id);
    assert.equal(before?.operation.operation_id, request.operation_id);
    const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a", { existingOnly: true });
    const page = await store.scanCommitted(null, 10);
    assert.equal(page.status, "page");
    if (page.status === "page") assert.equal(page.transactions.length, 1);
  });
}

for (const phase of ["rollback_prepared", "rollback_candidate_renamed", "rollback_candidate_archived", "rollback_outbox_renamed", "rollback_outbox_archived", "rollback_complete"]) {
  test(`rollback preserves all bytes after real SIGKILL at ${phase}`, async () => {
    const root = await mkdtemp(join(tmpdir(), "loopx-management-rollback-kill-"));
    const seed = await bootstrapManagedShadow(bootstrap(root), primary);
    assert.equal(seed.status, "applied");
    const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a");
    const other = new FileAuthorityStore(store.directory, "goal-b");
    await other.commitAuthority({ expected_provider_revision: null, operation_id: "other-goal", events: [], next_projection: { goal_id: "goal-b" }, receipts: [] });
    const candidateBytes = await readFile(store.path);
    const otherBytes = await readFile(other.path);
    const identityBytes = await readFile(store.identityPath);
    const pending = join(root, "authority-shadow", "outbox", "goal-a", "todos");
    await mkdir(pending, { recursive: true });
    await writeFile(join(pending, "entry.prepared.json"), "exact pending source");
    await writeFile(join(pending, "drain-cursor.json"), "{malformed retained");
    const request = { runtime_root: root, goal_id: "goal-a", operation_id: "rollback:kill", expected_provider_revision: seed.provider_revision };
    await killAt("rollback", request, phase);
    if (phase !== "rollback_complete") await assert.rejects(requireShadowPrimaryWriteAllowed(root, "goal-a"), { code: "shadow_management_in_progress" });
    const recovered = await rollbackManagedShadow(request, primary);
    assert.equal(recovered.status, phase === "rollback_complete" ? "replayed" : "recovered", JSON.stringify(recovered));
    assert.deepEqual(await readFile(String(recovered.candidate_archive_path)), candidateBytes);
    assert.equal(await readFile(join(String(recovered.outbox_archive_path), "todos", "entry.prepared.json"), "utf8"), "exact pending source");
    assert.equal(await readFile(join(String(recovered.outbox_archive_path), "todos", "drain-cursor.json"), "utf8"), "{malformed retained");
    assert.deepEqual(await readFile(other.path), otherBytes);
    assert.deepEqual(await readFile(store.identityPath), identityBytes);
    assert.equal(await requireShadowPrimaryWriteAllowed(root, "goal-a"), null);
  });
}

for (const phase of ["bootstrap_prepared", "bootstrap_candidate_committed", "bootstrap_outbox_ready"]) {
  test(`pending bootstrap can be explicitly aborted at ${phase}`, async () => {
    const root = await mkdtemp(join(tmpdir(), "loopx-management-abort-"));
    const request = bootstrap(root);
    await killAt("bootstrap", request, phase);
    const changed = await bootstrapManagedShadow(request, { ...primary, verifySourceSnapshot: async () => { throw new ShadowManagementError("source_changed_retry"); } });
    assert.equal(changed.reason_code, "source_changed_retry");
    const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a", { existingOnly: true });
    const loaded = await store.loadAuthority();
    const abort = { runtime_root: root, goal_id: "goal-a", operation_id: "rollback:abort", expected_provider_revision: null, expected_bootstrap_operation_id: request.operation_id };
    const stopped = await rollbackManagedShadow(abort, primary);
    assert.equal(stopped.status, "applied", JSON.stringify(stopped));
    assert.equal(await requireShadowPrimaryWriteAllowed(root, "goal-a"), null);
    const delayed = await bootstrapManagedShadow(request, primary);
    assert.equal(delayed.reason_code, "bootstrap_aborted");
    assert.equal((await readShadowManagementState(root, "goal-a"))?.status, "inactive");
    const again = await bootstrapManagedShadow(bootstrap(root, "bootstrap:after-abort"), primary);
    assert.equal(again.status, "applied");
  });
}

test("rollback archives pending bytes and rebootstrap cannot reuse the old lineage", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-management-rollback-"));
  const applied = await bootstrapManagedShadow(bootstrap(root), primary);
  assert.equal(applied.status, "applied");
  const binding = await requireShadowPrimaryWriteAllowed(root, "goal-a");
  assert.ok(binding?.capture_lineage_id);
  const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a");
  const identity = await readFile(store.identityPath, "utf8");
  const bytes = await readFile(store.path);
  const outbox = join(root, "authority-shadow", "outbox", "goal-a", "todos");
  await mkdir(outbox, { recursive: true });
  await writeFile(join(outbox, "pending.prepared.json"), "retained pending bytes");
  await writeFile(join(outbox, "drain-cursor.json"), "{broken cursor");
  const input = { runtime_root: root, goal_id: "goal-a", operation_id: "rollback:one", expected_provider_revision: applied.provider_revision, expected_bootstrap_operation_id: null };
  const result = await rollbackManagedShadow(input, primary);
  assert.equal(result.status, "applied");
  assert.equal((await readShadowManagementState(root, "goal-a"))?.status, "inactive");
  assert.equal(await requireShadowPrimaryWriteAllowed(root, "goal-a"), null);
  assert.equal(await readFile(store.identityPath, "utf8"), identity);
  assert.deepEqual(await readFile(String(result.candidate_archive_path)), bytes);
  assert.equal(await readFile(join(String(result.outbox_archive_path), "todos", "drain-cursor.json"), "utf8"), "{broken cursor");
  assert.equal((await rollbackManagedShadow(input, primary)).status, "replayed");
  assert.equal((await rollbackManagedShadow({ ...input, projection: { changed: true }, source_snapshot: { changed: true } }, primary)).status, "replayed");
  const next = await bootstrapManagedShadow(bootstrap(root, "bootstrap:after-rollback"), primary);
  assert.equal(next.status, "applied");
  assert.notEqual(next.capture_lineage_id, applied.capture_lineage_id);
  assert.notEqual(next.provider_revision, applied.provider_revision);
  assert.equal((await bootstrapManagedShadow(bootstrap(root), primary)).status, "replayed");
  assert.equal((await requireShadowPrimaryWriteAllowed(root, "goal-a"))?.capture_lineage_id, next.capture_lineage_id);
  const nextBytes = await readFile(store.path);
  const historical = await rollbackManagedShadow({ ...input, projection: { newer: true }, source_snapshot: { newer: true } }, primary);
  assert.equal(historical.status, "replayed");
  assert.equal(historical.current_capture_lineage_id, next.capture_lineage_id);
  assert.deepEqual(await readFile(store.path), nextBytes);
  assert.ok(!shadowMaintenanceLockPath(root, "goal-a").includes("/outbox/"));
});

test("rollback refuses a different valid candidate lineage without changing files", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-management-wrong-lineage-"));
  await bootstrapManagedShadow(bootstrap(root), primary);
  const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a");
  const loaded = await store.loadAuthority();
  assert.equal(loaded.status, "loaded");
  if (loaded.status !== "loaded") return;
  const committed = await store.commitAuthority({ expected_provider_revision: loaded.provider_revision, operation_id: "foreign-lineage", events: [], next_projection: { ...loaded.head, capture_lineage_id: "foreign" }, receipts: [] });
  assert.equal(committed.status, "applied");
  if (committed.status !== "applied") return;
  const before = await readFile(store.path);
  const rejected = await rollbackManagedShadow({ runtime_root: root, goal_id: "goal-a", operation_id: "rollback:foreign", expected_provider_revision: committed.provider_revision }, primary);
  assert.equal(rejected.reason_code, "rollback_candidate_identity_mismatch");
  assert.deepEqual(await readFile(store.path), before);
  assert.equal((await readShadowManagementState(root, "goal-a"))?.status, "active");
});

test("management request digest rejects reuse of a completed operation", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-management-request-digest-"));
  const request = bootstrap(root);
  assert.equal((await bootstrapManagedShadow(request, primary)).status, "applied");
  const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a");
  const before = await readFile(store.path);
  const rejected = await bootstrapManagedShadow({ ...request, source_version: "state:changed" }, primary);
  assert.equal(rejected.reason_code, "management_operation_identity_mismatch");
  assert.deepEqual(await readFile(store.path), before);
});

test("management manifest hash rejects changed pending bootstrap evidence", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-management-manifest-hash-"));
  const request = bootstrap(root);
  await killAt("bootstrap", request, "bootstrap_prepared");
  const directory = join(shadowMaintenanceLockPath(root, "goal-a"), "..", "operations");
  const [operation] = await readdir(directory);
  const path = join(directory, operation, "manifest.json");
  const manifest = JSON.parse(await readFile(path, "utf8"));
  await writeFile(path, JSON.stringify({ ...manifest, capture_lineage_id: "replaced-lineage" }));
  const before = await readFile(path);
  const rejected = await bootstrapManagedShadow(request, primary);
  assert.equal(rejected.reason_code, "shadow_management_manifest_invalid");
  assert.deepEqual(await readFile(path), before);
  assert.equal((await new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a", { existingOnly: true }).loadAuthority()).status, "missing");
});

test("management phase validation rejects an impossible terminal phase", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-management-phase-"));
  assert.equal((await bootstrapManagedShadow(bootstrap(root), primary)).status, "applied");
  const path = join(shadowMaintenanceLockPath(root, "goal-a"), "..", "state.json");
  const state = JSON.parse(await readFile(path, "utf8"));
  state.operation.phase = "prepared";
  await writeFile(path, JSON.stringify(state));
  const before = await readFile(path);
  await assert.rejects(requireShadowPrimaryWriteAllowed(root, "goal-a"), { code: "shadow_management_state_invalid" });
  assert.deepEqual(await readFile(path), before);
});

test("management goal binding rejects copied state from another goal", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-management-goal-binding-"));
  assert.equal((await bootstrapManagedShadow(bootstrap(root), primary)).status, "applied");
  const source = join(shadowMaintenanceLockPath(root, "goal-a"), "..", "state.json");
  const target = join(shadowMaintenanceLockPath(root, "goal-b"), "..", "state.json");
  await mkdir(join(target, ".."), { recursive: true });
  await writeFile(target, await readFile(source));
  const before = await readFile(source);
  await assert.rejects(requireShadowPrimaryWriteAllowed(root, "goal-b"), { code: "shadow_management_state_invalid" });
  assert.deepEqual(await readFile(source), before);
  assert.equal((await readShadowManagementState(root, "goal-a"))?.status, "active");
});

test("bootstrap source lookup is bound to active immutable management evidence", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-management-source-binding-"));
  const request = bootstrap(root);
  await bootstrapManagedShadow(request, primary);
  const binding = await requireShadowPrimaryWriteAllowed(root, "goal-a");
  assert.ok(binding);
  assert.equal(await readShadowBootstrapSourcePath(root, "goal-a", binding), join(root, "state.md"));
  await assert.rejects(readShadowBootstrapSourcePath(root, "goal-a", { ...binding, capture_lineage_id: "foreign" }), { code: "stale_generation" });
  const directory = join(shadowMaintenanceLockPath(root, "goal-a"), "..", "operations");
  const [operation] = await readdir(directory);
  const path = join(directory, operation, "manifest.json");
  const manifest = JSON.parse(await readFile(path, "utf8"));
  manifest.request.source_snapshot.state_path = join(root, "unbound.md");
  await writeFile(path, JSON.stringify(manifest));
  await assert.rejects(readShadowBootstrapSourcePath(root, "goal-a", binding), { code: "shadow_management_manifest_invalid" });
});

test("bootstrap source lookup is existing-only and rejects missing state", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-management-source-missing-"));
  const binding = { capture_profile: "file_outbox_v1", capture_lineage_id: "missing", source_root_digest: "sha256:" + "0".repeat(64),
    store_identity: "file:" + "0".repeat(32), bootstrap_operation_id: "none", bootstrap_provider_revision: "file:1:" + "0".repeat(24) };
  await assert.rejects(readShadowBootstrapSourcePath(root, "goal-a", binding), { code: "bootstrap_required" });
  assert.deepEqual(await readdir(root), []);
});
