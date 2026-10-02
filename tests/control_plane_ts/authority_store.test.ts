import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {
  AUTHORITY_STORE_PROVIDER_PROFILES,
  AUTHORITY_STORE_REQUIRED_GUARANTEES,
  readAuthorityReceipts,
} from "../../loopx/control_plane/coordination/authority_store.ts";
import {
  authorityStoreCommitFixture as commit,
  registerAuthorityStoreConformance,
} from "./authority_store_conformance.ts";
import {registerAuthorityOperationReplayConformance} from "./authority_operation_replay_conformance.ts";

async function fixture(t: test.TestContext, goalId = "goal-a") {
  const root = await mkdtemp(join(tmpdir(), "loopx-authority-store-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return { root, store: new FileAuthorityStore(root, goalId) };
}

registerAuthorityStoreConformance("file provider", async (t) => {
  const { root, store } = await fixture(t);
  return { store, contender: new FileAuthorityStore(root, "goal-a") };
}, "applied");

registerAuthorityOperationReplayConformance("file provider", async (t) => {
  const { root, store } = await fixture(t);
  return { store, contender: new FileAuthorityStore(root, "goal-a") };
});

test("file receipt batches retain caller order, original receipts and detached results", async t => {
  const {store} = await fixture(t);
  assert.equal(typeof store.readReceipts, "function");
  let revision: string | null = null;
  const expected = [];
  for (let i = 1; i <= 3; i++) {
    const request = commit(revision, `operation-${i}`, i, i);
    request.receipts = [{original: i, metadata: {absent: null, enabled: false}}];
    const result = await store.commitAuthority(request);
    assert.equal(result.status, "applied");
    if (result.status !== "applied") throw new Error("seed failed");
    revision = result.provider_revision;
    expected.push({status: "found", cursor: String(i), provider_revision: revision, receipts: request.receipts});
  }
  const before = await readFile(store.path);
  const ids = ["operation-3", "absent", "operation-1", "operation-3"];
  const result = await readAuthorityReceipts(store, ids);
  assert.deepEqual(result, {status: "receipts", results: [expected[2], {status: "missing"}, expected[0], expected[2]]});
  if (result.status !== "receipts" || result.results[0].status !== "found") throw new Error("batch failed");
  result.results[0].receipts[0].original = "caller edit";
  assert.deepEqual(result.results[3], expected[2], "duplicate results must not share mutable receipt bodies");
  assert.deepEqual(await store.readReceipt("operation-3"), expected[2]);
  assert.deepEqual(await readFile(store.path), before);

  // Readonly types do not freeze a caller's array while filesystem IO yields.
  // Force a fresh proof and mutate that array inside the existing decode seam.
  await writeFile(store.path, Buffer.concat([before, Buffer.from("\n")]));
  class MutatingCallerStore extends FileAuthorityStore {
    protected override decodeStoredDocument(value: unknown, identity: string) {
      ids.splice(0, ids.length, "");
      return super.decodeStoredDocument(value, identity);
    }
  }
  assert.deepEqual(await readAuthorityReceipts(new MutatingCallerStore(store.directory, "goal-a"), ids),
    {status: "receipts", results: [expected[2], {status: "missing"}, expected[0], expected[2]]});
});

test("file receipt batches reject invalid input and corrupt historical proof as a whole", async t => {
  const {store} = await fixture(t);
  const first = await store.commitAuthority(commit(null, "old", 1, 1));
  assert.equal(first.status, "applied");
  if (first.status !== "applied") throw new Error("seed failed");
  assert.equal((await store.commitAuthority(commit(first.provider_revision, "current", 2, 2))).status, "applied");
  const original = await readFile(store.path, "utf8");
  for (const ids of [[], Array(65).fill("old"), ["old", ""], ["old", null]]) {
    // Exercise direct provider input too, rather than only the common length guard.
    const batch = await store.readReceipts(ids as string[]);
    assert.equal(batch.status, "failed");
    assert.deepEqual(await readFile(store.path, "utf8"), original);
  }
  const invalidSingle = await store.readReceipt("");
  assert.equal(invalidSingle.status, "failed");
  if (invalidSingle.status === "failed") assert.equal(invalidSingle.reason_code, "invalid_operation_id");
  const forged = JSON.parse(original);
  forged.committed[0].receipts = [{forged: true}];
  await writeFile(store.path, JSON.stringify(forged));
  assert.equal((await readAuthorityReceipts(store, ["current", "absent"])).status, "failed",
    "unchanged current receipts and missing IDs cannot bypass corrupt older history");
  await writeFile(store.path, original);
  assert.equal((await readAuthorityReceipts(store, ["old", "current"])).status, "receipts");
  await writeFile(store.identityPath, `file:${"f".repeat(32)}`);
  assert.equal((await readAuthorityReceipts(store, ["current"])).status, "failed");
});

test("missing File receipt batches stay read-only", async t => {
  const {store} = await fixture(t);
  assert.deepEqual(await readAuthorityReceipts(store, Array(64).fill("absent")),
    {status: "receipts", results: Array(64).fill({status: "missing"})});
  await assert.rejects(readFile(store.identityPath), {code: "ENOENT"});
});

test("file receipt batches reject array holes before reading storage", async t => {
  const {root, store} = await fixture(t);
  const first = await store.commitAuthority(commit(null, "present", 1, 1));
  assert.equal(first.status, "applied");
  const original = await readFile(store.path);
  // Sparse arrays are valid string[] values in TypeScript. Every request slot
  // must be validated, even when an array method would skip an absent property.
  const empty = new Array<string>(1);
  const mixed = ["present", "hole", "absent", "present"];
  delete mixed[1];
  const masked = new Array<string>(1);
  masked[Symbol.iterator] = () => ["present"].values();
  const unreadable = new FileAuthorityStore(root, "unreadable");
  await mkdir(unreadable.path);
  assert.equal((await unreadable.readReceipts(["present"])).status, "unavailable");
  for (const ids of [empty, mixed, masked]) {
    for (const provider of [store, unreadable]) {
      for (const result of [await provider.readReceipts(ids),
        await readAuthorityReceipts(provider, ids)]) {
        assert.equal(result.status, "failed", "holes cannot produce successful undefined receipt items");
        if (result.status === "failed") assert.equal(result.reason_code, "provider_protocol_violation");
      }
    }
  }
  assert.deepEqual(await readFile(store.path), original);
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
  changed.committed[0].state.projection.authority_revision = 99;
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

test("large file read view reuses verified head and receipts without retaining history", async (t) => {
  const {root, store} = await fixture(t);
  assert.equal((await store.commitAuthority(commit(null, "operation-a", 1, 1))).status, "applied");
  const original = await readFile(store.path, "utf8");
  await writeFile(store.path, `${original}\n`, "utf8");

  class CompactStore extends FileAuthorityStore {
    static validations = 0;
    protected override fullDocumentCacheLimitBytes() { return 1; }
    protected override decodeStoredDocument(value: unknown, identity: string) {
      CompactStore.validations += 1;
      return super.decodeStoredDocument(value, identity);
    }
  }
  const first = new CompactStore(root, "goal-a");
  const second = new CompactStore(root, "goal-a");
  const head = await first.loadAuthority();
  assert.equal(head.status, "loaded");
  assert.deepEqual(await second.loadAuthority(), head);
  assert.equal((await second.readReceipt("operation-a")).status, "found");
  assert.equal(CompactStore.validations, 1);
  assert.equal((await second.scanCommitted(null, 1)).status, "page");
  assert.equal(CompactStore.validations, 2, "history scans still verify the complete journal");

  const changed = JSON.parse(original);
  changed.committed[0].state.projection.authority_revision = 99;
  changed.head.authority_revision = 99;
  await writeFile(store.path, JSON.stringify(changed), "utf8");
  assert.equal((await second.loadAuthority()).status, "failed");
  assert.equal((await second.readReceipt("operation-a")).status, "failed");
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

test("concurrent cold reads share only the same exact-byte proof and recover after failure", async t => {
  const {root, store} = await fixture(t);
  assert.equal((await store.commitAuthority(commit(null, "operation-shared", 1, 1))).status, "applied");
  const valid = await readFile(store.path, "utf8");
  await writeFile(store.path, valid + "\n");
  class CountingStore extends FileAuthorityStore {
    static validations = 0;
    protected override async decodeStoredDocument(value: unknown, identity: string) {
      CountingStore.validations++;
      // Hold the asynchronous proof open while sibling handles enter the read.
      await new Promise(resolve => setTimeout(resolve, 25));
      return super.decodeStoredDocument(value, identity);
    }
  }
  const readers = Array.from({length: 6}, () => new CountingStore(root, "goal-a"));
  const first = await Promise.all(readers.map(s => s.readReceipt("operation-shared")));
  assert.ok(first.every(r => r.status === "found"));
  assert.equal(CountingStore.validations, 1);
  const corrupt = JSON.parse(valid); corrupt.committed[0].provider_revision = "corrupt";
  await writeFile(store.path, JSON.stringify(corrupt));
  assert.ok((await Promise.all(readers.map(s => s.loadAuthority()))).every(r => r.status === "failed"));
  assert.equal(CountingStore.validations, 2);
  assert.equal((await readers[0]!.loadAuthority()).status, "failed");
  assert.equal(CountingStore.validations, 3, "a rejected promise must not remain in the in-flight registry");
  await writeFile(store.path, valid + "\n\n");
  assert.equal((await readers[0]!.loadAuthority()).status, "loaded");
  assert.equal(CountingStore.validations, 4);
});

test("alternating File stores reuse their own exact-byte proofs across handles", async t => {
  const fixtures = await Promise.all([fixture(t), fixture(t)]);
  for (const {store} of fixtures) {
    assert.equal((await store.commitAuthority(commit(null, "alternating", 1, 1))).status, "applied");
    await writeFile(store.path, (await readFile(store.path, "utf8")) + "\n");
  }
  class CountingStore extends FileAuthorityStore {
    static validations = 0;
    protected override decodeStoredDocument(value: unknown, identity: string) {
      CountingStore.validations++;
      return super.decodeStoredDocument(value, identity);
    }
  }
  for (let round = 0; round < 3; round++) {
    for (const {root} of fixtures) {
      const result = await new CountingStore(root, "goal-a").loadAuthority();
      assert.equal(result.status, "loaded");
      if (result.status === "loaded") {
        assert.equal(result.head.authority_revision, 1);
        result.head.authority_revision = "caller mutation";
      }
    }
  }
  assert.equal(CountingStore.validations, 2, "each unchanged store proves its history once");
  const first = fixtures[0]!;
  const document = JSON.parse(await readFile(first.store.path, "utf8"));
  document.committed[0].provider_revision = "tampered";
  await writeFile(first.store.path, JSON.stringify(document));
  assert.equal((await new CountingStore(first.root, "goal-a").loadAuthority()).status, "failed");
  assert.equal((await new CountingStore(fixtures[1]!.root, "goal-a").loadAuthority()).status, "loaded");
  assert.equal(CountingStore.validations, 3, "a bad store cannot invalidate an unrelated valid proof");
});

test("File proof working set evicts least-recently used stores rather than growing with Goal count", async t => {
  class CountingStore extends FileAuthorityStore {
    static validations = 0;
    protected override decodeStoredDocument(value: unknown, identity: string) {
      CountingStore.validations++;
      return super.decodeStoredDocument(value, identity);
    }
  }
  const roots: string[] = [];
  for (let index = 0; index < 5; index++) {
    const {root, store} = await fixture(t);
    roots.push(root);
    assert.equal((await store.commitAuthority(commit(null, "working-set", 1, 1))).status, "applied");
    await writeFile(store.path, (await readFile(store.path, "utf8")) + "\n");
    assert.equal((await new CountingStore(root, "goal-a").loadAuthority()).status, "loaded");
  }
  assert.equal(CountingStore.validations, 5);
  for (const index of [4, 2, 3, 1]) {
    assert.equal((await new CountingStore(roots[index]!, "goal-a").loadAuthority()).status, "loaded");
  }
  assert.equal(CountingStore.validations, 5, "four recent stores remain reusable");
  assert.equal((await new CountingStore(roots[0]!, "goal-a").loadAuthority()).status, "loaded");
  assert.equal(CountingStore.validations, 6, "the evicted store must prove history again");
  assert.equal((await new CountingStore(roots[4]!, "goal-a").loadAuthority()).status, "loaded");
  assert.equal(CountingStore.validations, 7, "access order, not insertion identity, determines eviction");
});
