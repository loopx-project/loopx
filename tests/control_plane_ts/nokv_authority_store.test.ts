import assert from "node:assert/strict";
import test from "node:test";

import {
  NoKVAuthorityStore,
  type NoKVBlobCasRequest,
  type NoKVBlobCasResult,
  type NoKVBlobReadResult,
  type NoKVBlobTransport,
  type NoKVStoreIdentityResult,
} from "../../loopx/control_plane/coordination/nokv_authority_store.ts";
import {
  authorityStoreCommitFixture as commit,
  registerAuthorityStoreConformance,
} from "./authority_store_conformance.ts";

type PublishFault =
  | "ambiguous_before"
  | "terminal_ambiguous_before"
  | "ambiguous_after"
  | "ambiguous_after_then_read_unavailable"
  | "failed"
  | null;

interface FakeNoKVBackend {
  identity: string;
  blob: { bytes: Uint8Array; generation: number } | null;
  identityUnavailable: boolean;
  rotateIdentityBeforePublish: string | null;
  readUnavailable: number;
  publishFault: PublishFault;
  casRequests: NoKVBlobCasRequest[];
  terminalPhysicalIds: Set<string>;
  /**
   * Mirrors NoKV 0.11.1: the expected incarnation is evaluated before the path
   * is claimed, so a stale fence leaves no row, object, or generation behind.
   * `false` models an owner that ignored the fence, to pin the readback guard.
   */
  enforceIncarnationFence: boolean;
}

function fakeBackend(): FakeNoKVBackend {
  return {
    identity: `nokv:authority-workbench:${"a".repeat(32)}`,
    blob: null,
    identityUnavailable: false,
    rotateIdentityBeforePublish: null,
    readUnavailable: 0,
    publishFault: null,
    casRequests: [],
    terminalPhysicalIds: new Set(),
    enforceIncarnationFence: true,
  };
}

function incarnationOf(identity: string): string {
  return identity.slice("nokv:authority-workbench:".length);
}

class FakeNoKVTransport implements NoKVBlobTransport {
  readonly backend: FakeNoKVBackend;

  constructor(backend: FakeNoKVBackend) {
    this.backend = backend;
  }

  async storeIdentity(_workbench: string): Promise<NoKVStoreIdentityResult> {
    if (this.backend.identityUnavailable) {
      return {
        status: "unavailable",
        reason_code: "injected_identity_unavailable",
        reason: "identity lookup unavailable",
      };
    }
    return { status: "available", store_identity: this.backend.identity };
  }

  async readBlob(_workbench: string, _path: string): Promise<NoKVBlobReadResult> {
    if (this.backend.readUnavailable > 0) {
      this.backend.readUnavailable -= 1;
      return {
        status: "unavailable",
        reason_code: "injected_read_unavailable",
        reason: "blob read unavailable",
      };
    }
    return this.backend.blob
      ? {
        status: "loaded",
        bytes: this.backend.blob.bytes.slice(),
        generation: this.backend.blob.generation,
      }
      : { status: "missing" };
  }

  async casPublishBlob(request: NoKVBlobCasRequest): Promise<NoKVBlobCasResult> {
    this.backend.casRequests.push({ ...request, bytes: request.bytes.slice() });
    if (this.backend.rotateIdentityBeforePublish !== null) {
      this.backend.identity = this.backend.rotateIdentityBeforePublish;
      this.backend.rotateIdentityBeforePublish = null;
    }
    if (
      this.backend.terminalPhysicalIds.has(request.operation_id) ||
      this.backend.terminalPhysicalIds.has(request.artifact_revision_id)
    ) {
      return {
        status: "ambiguous",
        reason_code: "injected_terminal_identity_spent",
        reason: "physical publication identity is terminal",
      };
    }
    if (
      this.backend.enforceIncarnationFence &&
      request.expected_workspace_incarnation_id !== incarnationOf(this.backend.identity)
    ) {
      return {
        status: "failed",
        reason_code: "store_identity_mismatch",
        reason: "workbench incarnation does not match the expected incarnation",
      };
    }
    const current = this.backend.blob?.generation ?? null;
    if (current !== request.expected_generation) {
      return { status: "conflict", current_generation: current };
    }
    if (this.backend.publishFault === "failed") {
      this.backend.publishFault = null;
      return {
        status: "failed",
        reason_code: "injected_publish_rejected",
        reason: "publish rejected before SDK call",
      };
    }
    if (this.backend.publishFault === "ambiguous_before") {
      this.backend.publishFault = null;
      return {
        status: "ambiguous",
        reason_code: "injected_lost_response",
        reason: "publish outcome unknown",
      };
    }
    if (this.backend.publishFault === "terminal_ambiguous_before") {
      this.backend.publishFault = null;
      this.backend.terminalPhysicalIds.add(request.operation_id);
      this.backend.terminalPhysicalIds.add(request.artifact_revision_id);
      return {
        status: "ambiguous",
        reason_code: "injected_terminal_identity_spent",
        reason: "physical publication identity failed terminally",
      };
    }
    const generation = (request.expected_generation ?? 0) + 1;
    this.backend.blob = { bytes: request.bytes.slice(), generation };
    if (
      this.backend.publishFault === "ambiguous_after" ||
      this.backend.publishFault === "ambiguous_after_then_read_unavailable"
    ) {
      if (this.backend.publishFault === "ambiguous_after_then_read_unavailable") {
        this.backend.readUnavailable += 1;
      }
      this.backend.publishFault = null;
      return {
        status: "ambiguous",
        reason_code: "injected_lost_response",
        reason: "publish response was lost",
      };
    }
    return { status: "applied", generation };
  }
}

function store(backend: FakeNoKVBackend, tenantId = "tenant-a", goalId = "goal-a") {
  return new NoKVAuthorityStore(new FakeNoKVTransport(backend), {
    tenant_id: tenantId,
    goal_id: goalId,
    workbench: "authority-workbench",
  });
}

registerAuthorityStoreConformance("NoKV single-envelope provider", async () => {
  const backend = fakeBackend();
  return { store: store(backend), contender: store(backend) };
});

test("NoKV provider uses a deterministic CLI-readable metadata path", () => {
  const backend = fakeBackend();
  const first = store(backend);
  const same = store(backend);
  const otherTenant = store(backend, "tenant-b");

  assert.equal(first.path, same.path);
  assert.match(first.path, /^metadata\/loopx-authority\/[0-9a-f]{32}\.json$/);
  assert.notEqual(first.path, otherTenant.path);
});

test("NoKV provider keeps proven missing distinct from identity and read unavailability", async () => {
  const backend = fakeBackend();
  const provider = store(backend);
  assert.deepEqual(await provider.loadAuthority(), { status: "missing" });

  backend.readUnavailable = 1;
  const readUnavailable = await provider.loadAuthority();
  assert.equal(readUnavailable.status, "unavailable");
  if (readUnavailable.status === "unavailable") {
    assert.equal(readUnavailable.reason_code, "injected_read_unavailable");
  }

  backend.identityUnavailable = true;
  const identityUnavailable = await provider.loadAuthority();
  assert.equal(identityUnavailable.status, "unavailable");
  assert.equal((await provider.storeIdentity()).status, "unavailable");
});

test("NoKV provider reconciles a lost success from the embedded operation receipt", async () => {
  const backend = fakeBackend();
  const provider = store(backend);
  backend.publishFault = "ambiguous_after";

  const applied = await provider.commitAuthority(commit(null, "operation-a", 1, 7));
  assert.equal(applied.status, "applied");
  const receipt = await provider.readReceipt("operation-a");
  assert.equal(receipt.status, "found");
  if (receipt.status === "found") assert.equal(receipt.receipts[0]?.lease_epoch, 7);
});

test("NoKV provider leaves an outcome ambiguous until readback becomes available", async () => {
  const backend = fakeBackend();
  const provider = store(backend);
  backend.publishFault = "ambiguous_after_then_read_unavailable";

  const unknown = await provider.commitAuthority(commit(null, "operation-a", 1, 8));
  assert.equal(unknown.status, "ambiguous");
  const receipt = await provider.readReceipt("operation-a");
  assert.equal(receipt.status, "found");
  if (receipt.status === "found") assert.equal(receipt.receipts[0]?.lease_epoch, 8);
});

test("NoKV provider does not invent a receipt when an ambiguous publish did not land", async () => {
  const backend = fakeBackend();
  const provider = store(backend);
  backend.publishFault = "ambiguous_before";

  const unknown = await provider.commitAuthority(commit(null, "operation-a", 1, 9));
  assert.equal(unknown.status, "ambiguous");
  assert.deepEqual(await provider.readReceipt("operation-a"), { status: "missing" });
  assert.deepEqual(await provider.loadAuthority(), { status: "missing" });
});

test("NoKV provider retries one logical commit with fresh physical identities", async () => {
  const backend = fakeBackend();
  const provider = store(backend);
  const request = commit(null, "operation-a", 1, 1);
  backend.publishFault = "terminal_ambiguous_before";

  assert.equal((await provider.commitAuthority(request)).status, "ambiguous");
  assert.equal((await provider.commitAuthority(request)).status, "applied");
  assert.equal(backend.casRequests.length, 2);
  assert.notEqual(
    backend.casRequests[0]?.operation_id,
    backend.casRequests[1]?.operation_id,
  );
  assert.notEqual(
    backend.casRequests[0]?.artifact_revision_id,
    backend.casRequests[1]?.artifact_revision_id,
  );
  assert.match(backend.casRequests[0]!.operation_id, /^[0-9a-f]{32}$/);
  assert.match(backend.casRequests[0]!.artifact_revision_id, /^[0-9a-f]{32}$/);
  assert.notEqual(
    backend.casRequests[0]?.operation_id,
    backend.casRequests[0]?.artifact_revision_id,
  );
});

test("NoKV provider fences restored bytes with a different workspace incarnation", async () => {
  const backend = fakeBackend();
  const original = store(backend);
  const applied = await original.commitAuthority(commit(null, "operation-a", 1, 1));
  assert.equal(applied.status, "applied");
  const callsBeforeRestore = backend.casRequests.length;

  backend.identity = `nokv:authority-workbench:${"b".repeat(32)}`;
  const restored = store(backend);
  const loaded = await restored.loadAuthority();
  assert.equal(loaded.status, "failed");
  if (loaded.status === "failed") assert.match(loaded.reason, /lineage mismatch/);

  const rejected = await restored.commitAuthority(
    commit(
      applied.status === "applied" ? applied.provider_revision : null,
      "operation-b",
      2,
      2,
    ),
  );
  assert.equal(rejected.status, "failed");
  assert.equal(backend.casRequests.length, callsBeforeRestore);
});

test("NoKV provider names the bound workbench incarnation on every publication", async () => {
  const backend = fakeBackend();
  const provider = store(backend);
  const created = await provider.commitAuthority(commit(null, "operation-a", 1, 1));
  assert.equal(created.status, "applied");
  const second = commit(
    created.status === "applied" ? created.provider_revision : null,
    "operation-b",
    2,
    2,
  );
  // A terminal physical failure leaves the logical commit ambiguous; the
  // caller's retry publishes with fresh lower-layer ids and the same fence.
  backend.publishFault = "terminal_ambiguous_before";
  assert.equal((await provider.commitAuthority(second)).status, "ambiguous");
  assert.equal((await provider.commitAuthority(second)).status, "applied");

  assert.equal(backend.casRequests.length, 3);
  assert.notEqual(backend.casRequests[1]?.operation_id, backend.casRequests[2]?.operation_id);
  for (const request of backend.casRequests) {
    assert.equal(request.expected_workspace_incarnation_id, "a".repeat(32));
  }
});

test("NoKV provider is refused before writing across a workbench-incarnation race", async () => {
  const backend = fakeBackend();
  const provider = store(backend);
  const created = await provider.commitAuthority(commit(null, "operation-a", 1, 1));
  assert.equal(created.status, "applied");
  const bytesBefore = Buffer.from(backend.blob!.bytes).toString("base64");
  const requestsBefore = backend.casRequests.length;
  // The store reads generation 1 in incarnation a; the workbench is restored to
  // incarnation b before the publish reaches the owner.
  backend.rotateIdentityBeforePublish = `nokv:authority-workbench:${"b".repeat(32)}`;

  const result = await provider.commitAuthority(
    commit(
      created.status === "applied" ? created.provider_revision : null,
      "operation-b",
      2,
      2,
    ),
  );

  assert.equal(result.status, "failed");
  if (result.status === "failed") {
    assert.equal(result.reason_code, "store_identity_mismatch");
  }
  assert.equal(backend.casRequests.length, requestsBefore + 1);
  assert.equal(
    backend.casRequests.at(-1)?.expected_workspace_incarnation_id,
    "a".repeat(32),
  );
  assert.equal(backend.blob?.generation, 1);
  assert.equal(Buffer.from(backend.blob!.bytes).toString("base64"), bytesBefore);
  const reloaded = await provider.loadAuthority();
  assert.equal(reloaded.status, "failed");
  if (reloaded.status === "failed") assert.match(reloaded.reason, /lineage mismatch/);
  assert.equal((await provider.readReceipt("operation-b")).status, "failed");
});

test("NoKV provider does not report applied when an owner ignores the incarnation fence", async () => {
  const backend = fakeBackend();
  backend.enforceIncarnationFence = false;
  const provider = store(backend);
  backend.rotateIdentityBeforePublish = `nokv:authority-workbench:${"b".repeat(32)}`;

  const result = await provider.commitAuthority(commit(null, "operation-a", 1, 1));

  assert.equal(result.status, "failed");
  if (result.status === "failed") {
    assert.equal(result.reason_code, "provider_protocol_violation");
    assert.match(result.reason, /lineage mismatch/);
  }
  assert.equal(backend.casRequests[0]?.expected_workspace_incarnation_id, "a".repeat(32));
  assert.equal((await provider.loadAuthority()).status, "failed");
});

test("NoKV provider fails closed when persisted generation and bytes diverge", async () => {
  const backend = fakeBackend();
  const provider = store(backend);
  assert.equal(
    (await provider.commitAuthority(commit(null, "operation-a", 1, 1))).status,
    "applied",
  );
  backend.blob!.generation += 1;

  const loaded = await provider.loadAuthority();
  assert.equal(loaded.status, "failed");
  if (loaded.status === "failed") assert.match(loaded.reason, /storage generation/);
});

test("NoKV provider treats non-UTF8 persisted bytes as a protocol failure", async () => {
  const backend = fakeBackend();
  backend.blob = { bytes: Uint8Array.of(0xff), generation: 1 };

  const loaded = await store(backend).loadAuthority();
  assert.equal(loaded.status, "failed");
  if (loaded.status === "failed") {
    assert.equal(loaded.reason_code, "provider_protocol_violation");
  }
});

test("NoKV provider enforces its candidate envelope capacity before CAS", async () => {
  const backend = fakeBackend();
  const provider = new NoKVAuthorityStore(new FakeNoKVTransport(backend), {
    tenant_id: "tenant-a",
    goal_id: "goal-a",
    workbench: "authority-workbench",
    max_envelope_bytes: 64,
  });

  const result = await provider.commitAuthority(commit(null, "operation-a", 1, 1));
  assert.equal(result.status, "failed");
  if (result.status === "failed") {
    assert.equal(result.reason_code, "authority_envelope_too_large");
  }
  assert.equal(backend.casRequests.length, 0);
});
