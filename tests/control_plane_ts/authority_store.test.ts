import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {
  AUTHORITY_STORE_PROVIDER_PROFILES,
  AUTHORITY_STORE_REQUIRED_GUARANTEES,
} from "../../loopx/control_plane/coordination/authority_store.ts";
import {
  authorityStoreCommitFixture as commit,
  registerAuthorityStoreConformance,
} from "./authority_store_conformance.ts";

async function fixture(t: test.TestContext, goalId = "goal-a") {
  const root = await mkdtemp(join(tmpdir(), "loopx-authority-store-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return { root, store: new FileAuthorityStore(root, goalId) };
}

registerAuthorityStoreConformance("file provider", async (t) => {
  const { root, store } = await fixture(t);
  return { store, contender: new FileAuthorityStore(root, "goal-a") };
});

test("file provider persists object keys in deterministic Unicode order", async (t) => {
  const { store } = await fixture(t);
  const ordered = commit(null, "operation-order", 1, 1);
  (ordered.next_projection as Record<string, unknown>).coordination = {
    "😀": true,
    "é": true,
    z: true,
    a: true,
  };

  assert.equal((await store.commitAuthority(ordered)).status, "applied");
  const persisted = JSON.parse(await readFile(store.path, "utf8"));
  assert.deepEqual(Object.keys(persisted.head.coordination), ["a", "z", "é", "😀"]);
});

test("provider profiles map one logical contract onto different backend primitives", () => {
  assert.equal(AUTHORITY_STORE_REQUIRED_GUARANTEES.length, 6);
  assert.deepEqual(Object.keys(AUTHORITY_STORE_PROVIDER_PROFILES), [
    "sqlite", "file", "nokv", "postgresql",
  ]);
  assert.equal(AUTHORITY_STORE_PROVIDER_PROFILES.file.stage, "stage1_implemented");
  assert.equal(
    AUTHORITY_STORE_PROVIDER_PROFILES.nokv.revision_primitive,
    "path_generation_compare_and_publish",
  );
  assert.equal(
    AUTHORITY_STORE_PROVIDER_PROFILES.nokv.store_lineage_mapping,
    "workbench_workspace_incarnation_id",
  );
  assert.ok(
    AUTHORITY_STORE_PROVIDER_PROFILES.nokv.qualification_holds.includes(
      "capacity_and_receipt_retention",
    ),
  );
  assert.equal(
    AUTHORITY_STORE_PROVIDER_PROFILES.postgresql.atomic_commit_mapping,
    "one_sql_transaction_over_head_events_and_receipts",
  );
  assert.equal(AUTHORITY_STORE_PROVIDER_PROFILES.postgresql.stage, "stage2b_candidate");
  assert.match(AUTHORITY_STORE_PROVIDER_PROFILES.postgresql.trust_boundary, /tenant_scoped/);
  assert.ok(
    AUTHORITY_STORE_PROVIDER_PROFILES.postgresql.qualification_holds.includes(
      "service_api_authentication_and_tenant_authorization",
    ),
  );
  assert.ok(
    AUTHORITY_STORE_PROVIDER_PROFILES.postgresql.qualification_holds.includes(
      "service_role_provisioning_and_audit_policy",
    ),
  );
  assert.ok(
    AUTHORITY_STORE_PROVIDER_PROFILES.postgresql.qualification_holds.includes(
      "retention_partitioning_and_measured_capacity",
    ),
  );
  assert.notDeepEqual(
    AUTHORITY_STORE_PROVIDER_PROFILES.file,
    AUTHORITY_STORE_PROVIDER_PROFILES.nokv,
  );
});

test("corrupt, cross-goal, or revision-divergent documents fail closed", async (t) => {
  const { store } = await fixture(t);
  const applied = await store.commitAuthority(commit(null, "operation-a", 1, 1));
  assert.equal(applied.status, "applied");
  const otherHandle = new FileAuthorityStore(store.directory, "goal-a");
  assert.deepEqual(await otherHandle.loadAuthority(), await store.loadAuthority());
  const original = JSON.parse(await readFile(store.path, "utf8"));

  // A same-length replacement must not inherit the verified journal simply
  // because its path or filesystem size is unchanged.
  await writeFile(store.path, JSON.stringify({ ...original, goal_id: "goal-b" }), "utf8");
  assert.equal((await store.loadAuthority()).status, "failed");

  await writeFile(store.path, JSON.stringify({ ...original, unexpected: true }), "utf8");
  assert.equal((await store.loadAuthority()).status, "failed");

  const changed = structuredClone(original);
  changed.committed[0].projection.authority_revision = 99;
  changed.head.authority_revision = 99;
  await writeFile(store.path, JSON.stringify(changed), "utf8");
  const divergent = await store.loadAuthority();
  assert.equal(divergent.status, "failed");
  if (divergent.status === "failed") assert.match(divergent.reason, /revision lineage/);
});

test("file verification is reused only for exact bytes and store identity", async (t) => {
  const { root, store } = await fixture(t);
  const applied = await store.commitAuthority(commit(null, "operation-a", 1, 1));
  assert.equal(applied.status, "applied");
  const validBytes = await readFile(store.path, "utf8");
  // A benign external rewrite forces one full validation; a second handle
  // reads the same proven bytes without validating the whole journal again.
  await writeFile(store.path, `${validBytes}\n`, "utf8");
  class CountingStore extends FileAuthorityStore {
    static validations = 0;
    protected override decodeStoredDocument(value: unknown, identity: string) {
      CountingStore.validations += 1;
      return super.decodeStoredDocument(value, identity);
    }
  }
  const first = new CountingStore(root, "goal-a");
  const second = new CountingStore(root, "goal-a");
  assert.equal((await first.loadAuthority()).status, "loaded");
  assert.equal((await second.loadAuthority()).status, "loaded");
  assert.equal(CountingStore.validations, 1);

  const originalIdentity = await readFile(store.identityPath, "utf8");
  await writeFile(store.identityPath, `file:${"f".repeat(32)}`, "utf8");
  assert.equal((await second.loadAuthority()).status, "failed");
  assert.equal(CountingStore.validations, 2);
  await writeFile(store.identityPath, originalIdentity, "utf8");

  await writeFile(store.path, validBytes.replace('"goal-a"', '"goal-b"'), "utf8");
  assert.equal((await second.loadAuthority()).status, "failed");
  assert.equal(CountingStore.validations, 3);
});

test("store identity is one durable directory lineage and restored bytes are fenced", async (t) => {
  const { root, store } = await fixture(t);
  const handles = Array.from({ length: 8 }, () => new FileAuthorityStore(root, "goal-a"));
  const identities = await Promise.all(handles.map((handle) => handle.storeIdentity()));
  assert.ok(identities.every((result) => result.status === "available"));
  const values = identities.flatMap((result) =>
    result.status === "available" ? [result.store_identity] : []
  );
  assert.equal(new Set(values).size, 1);
  assert.match(values[0]!, /^file:[0-9a-f]{32}$/);

  await store.commitAuthority(commit(null, "operation-a", 1, 1));
  await writeFile(store.identityPath, `file:${"a".repeat(32)}`, "ascii");
  const restored = await store.loadAuthority();
  assert.equal(restored.status, "failed");
  if (restored.status === "failed") assert.match(restored.reason, /lineage mismatch/);
});

test("proven missing is distinct from provider read unavailability", async (t) => {
  const { store } = await fixture(t);
  assert.deepEqual(await store.loadAuthority(), { status: "missing" });
  const identity = await store.storeIdentity();
  assert.equal(identity.status, "available");
  await mkdir(store.path);
  const unavailable = await store.loadAuthority();
  assert.equal(unavailable.status, "unavailable");
});

test("orphan temporary writes never become the visible authority head", async (t) => {
  const { store } = await fixture(t);
  await writeFile(`${store.path}.tmp-crashed-writer`, "{truncated", "utf8");
  assert.deepEqual(await store.loadAuthority(), { status: "missing" });
  const applied = await store.commitAuthority(commit(null, "operation-a", 1, 1));
  assert.equal(applied.status, "applied");
  const loaded = await store.loadAuthority();
  assert.equal(loaded.status, "loaded");
});

test("ambiguous file commits reconcile only from durable receipt readback", async (t) => {
  const { root } = await fixture(t);
  class FaultStore extends FileAuthorityStore {
    fault: "before" | "after" | null = null;

    protected override async replaceDurably(path: string, payload: Uint8Array): Promise<void> {
      if (path === this.path && this.fault === "before") {
        this.fault = null;
        throw new Error("injected before replace");
      }
      await super.replaceDurably(path, payload);
      if (path === this.path && this.fault === "after") {
        this.fault = null;
        throw new Error("injected after durable replace");
      }
    }
  }

  const store = new FaultStore(root, "goal-a");
  await store.storeIdentity();
  store.fault = "before";
  const unproved = await store.commitAuthority(commit(null, "operation-before", 1, 1));
  assert.equal(unproved.status, "ambiguous");
  assert.deepEqual(await store.readReceipt("operation-before"), { status: "missing" });
  assert.deepEqual(await store.loadAuthority(), { status: "missing" });

  store.fault = "after";
  const recoverable = await store.commitAuthority(commit(null, "operation-after", 1, 2));
  assert.equal(recoverable.status, "ambiguous");
  const receipt = await store.readReceipt("operation-after");
  assert.equal(receipt.status, "found");
  if (receipt.status === "found") assert.equal(receipt.receipts[0]?.lease_epoch, 2);
  const loaded = await store.loadAuthority();
  assert.equal(loaded.status, "loaded");
  if (loaded.status === "loaded") assert.equal(loaded.head.authority_revision, 1);
});
