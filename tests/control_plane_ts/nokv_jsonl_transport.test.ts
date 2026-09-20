import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  NoKVAuthorityStore,
  NoKVTransportProtocolError,
  NoKVTransportUnavailableError,
} from "../../loopx/control_plane/coordination/nokv_authority_store.ts";
import { NoKVJsonLinesTransport } from "../../loopx/control_plane/coordination/nokv_jsonl_transport.ts";
import { registerAuthorityStoreConformance } from "./authority_store_conformance.ts";

const PYTHON = process.env.LOOPX_TEST_PYTHON ?? "python3";
const FAULT_HELPER = fileURLToPath(
  new URL("../fixtures/nokv_jsonl_fake_helper.py", import.meta.url),
);
const SDK_HELPER = fileURLToPath(
  new URL("../../loopx/control_plane/coordination/nokv_jsonl_helper.py", import.meta.url),
);
const FAKE_SDK_ROOT = fileURLToPath(
  new URL("../fixtures/nokv_fake_sdk", import.meta.url),
);

const ETCD_ROUTING = {
  kind: "etcd",
  endpoints: ["http://127.0.0.1:2379"],
  key_prefix: "/nokv/control",
  lease_ttl_seconds: 10,
};
// Seed routing names serving owners directly; it is the routing kind of the
// NoKV metadata-runtimes line, which drops the etcd constructor.
const SEEDS_ROUTING = { kind: "seeds", endpoints: ["127.0.0.1:7750"] };

const FAKE_INCARNATION = "a".repeat(32);

async function openSdkHelper(
  routing: Record<string, unknown> = ETCD_ROUTING,
  fakeSdkShape?: "0.11.0" | "0.11.1-unfenced",
) {
  return await NoKVJsonLinesTransport.open({
    argv: [PYTHON, SDK_HELPER],
    config: {
      root_id: "0".repeat(32),
      routing,
      object_store: { kind: "memory" },
    },
    env: {
      ...process.env,
      PYTHONPATH: process.env.PYTHONPATH
        ? `${FAKE_SDK_ROOT}:${process.env.PYTHONPATH}`
        : FAKE_SDK_ROOT,
      ...(fakeSdkShape === undefined ? {} : { LOOPX_FAKE_NOKV_SDK_SHAPE: fakeSdkShape }),
    },
    request_timeout_ms: 2_000,
  });
}

async function openFaultHelper(mode: string, maxResponseBytes?: number) {
  return await NoKVJsonLinesTransport.open({
    argv: [PYTHON, FAULT_HELPER, mode],
    config: {},
    request_timeout_ms: 2_000,
    max_response_bytes: maxResponseBytes,
  });
}

test("JSON-lines transport cannot bypass its open handshake", () => {
  let processStarted = false;
  const DirectTransport = NoKVJsonLinesTransport as unknown as new (
    options: {
      argv: readonly string[];
      config: Record<string, never>;
      process_factory: () => never;
    },
    constructionToken: symbol,
  ) => NoKVJsonLinesTransport;

  assert.throws(
    () =>
      new DirectTransport(
        {
          argv: ["injected-helper"],
          config: {},
          process_factory: () => {
            processStarted = true;
            throw new Error("constructor reached process creation");
          },
        },
        Symbol("caller-token"),
      ),
    /must be created with open\(\)/,
  );
  assert.equal(processStarted, false);
});

registerAuthorityStoreConformance("NoKV JSON-lines process", async (t) => {
  const transport = await openSdkHelper();
  t.after(async () => await transport.close());
  return {
    store: new NoKVAuthorityStore(transport, {
      tenant_id: "tenant-a",
      goal_id: "goal-a",
      workbench: "authority-workbench",
    }),
    contender: new NoKVAuthorityStore(transport, {
      tenant_id: "tenant-a",
      goal_id: "goal-a",
      workbench: "authority-workbench",
    }),
  };
});

test("JSON-lines transport starts once and reuses the helper process", async (t) => {
  const transport = await openSdkHelper();
  t.after(async () => await transport.close());

  assert.deepEqual(await transport.storeIdentity("authority-workbench"), {
    status: "available",
    store_identity: `nokv:authority-workbench:${"a".repeat(32)}`,
  });
  assert.deepEqual(
    await transport.readBlob("authority-workbench", "metadata/head.json"),
    { status: "missing" },
  );
  assert.deepEqual(
    await transport.casPublishBlob({
      workbench: "authority-workbench",
      path: "metadata/head.json",
      expected_generation: null,
      expected_workspace_incarnation_id: FAKE_INCARNATION,
      bytes: Buffer.from("payload", "utf8"),
      operation_id: "a".repeat(32),
      artifact_revision_id: "b".repeat(32),
    }),
    { status: "applied", generation: 1 },
  );
  const loaded = await transport.readBlob("authority-workbench", "metadata/head.json");
  assert.equal(loaded.status, "loaded");
  if (loaded.status === "loaded") {
    assert.equal(Buffer.from(loaded.bytes).toString("utf8"), "payload");
    assert.equal(loaded.generation, 1);
  }
});

test("JSON-lines helper refuses a stale incarnation fence typed and leaves the generation unchanged", async (t) => {
  const transport = await openSdkHelper();
  t.after(async () => await transport.close());
  assert.deepEqual(
    await transport.casPublishBlob({
      workbench: "authority-workbench",
      path: "metadata/head.json",
      expected_generation: null,
      expected_workspace_incarnation_id: FAKE_INCARNATION,
      bytes: Buffer.from("generation one", "utf8"),
      operation_id: "c".repeat(32),
      artifact_revision_id: "d".repeat(32),
    }),
    { status: "applied", generation: 1 },
  );

  assert.deepEqual(
    await transport.casPublishBlob({
      workbench: "authority-workbench",
      path: "metadata/head.json",
      expected_generation: 1,
      expected_workspace_incarnation_id: "b".repeat(32),
      bytes: Buffer.from("stale incarnation", "utf8"),
      operation_id: "e".repeat(32),
      artifact_revision_id: "f".repeat(32),
    }),
    {
      status: "failed",
      reason_code: "store_identity_mismatch",
      reason: "NoKV workbench incarnation does not match the expected incarnation",
    },
  );
  const loaded = await transport.readBlob("authority-workbench", "metadata/head.json");
  assert.equal(loaded.status, "loaded");
  if (loaded.status === "loaded") {
    assert.equal(Buffer.from(loaded.bytes).toString("utf8"), "generation one");
    assert.equal(loaded.generation, 1);
  }
});

test("JSON-lines helper refuses the 0.11.0 wheel by its version pin", async () => {
  await assert.rejects(
    openSdkHelper(ETCD_ROUTING, "0.11.0"),
    (error: unknown) => {
      assert.ok(error instanceof NoKVTransportProtocolError);
      assert.match(error.message, /must be version 0\.11\.1/);
      return true;
    },
  );
});

test("JSON-lines helper refuses a 0.11.1-labelled wheel without the publication fence", async () => {
  await assert.rejects(
    openSdkHelper(ETCD_ROUTING, "0.11.1-unfenced"),
    (error: unknown) => {
      assert.ok(error instanceof NoKVTransportProtocolError);
      assert.match(error.message, /does not fence publication/);
      assert.match(error.message, /expected_workspace_incarnation_id/);
      return true;
    },
  );
});

test("JSON-lines helper disconnect is typed unavailable", async (t) => {
  const transport = await openFaultHelper("disconnect");
  t.after(async () => await transport.close());

  await assert.rejects(
    transport.readBlob("authority-workbench", "metadata/head.json"),
    (error: unknown) => {
      assert.ok(error instanceof NoKVTransportUnavailableError);
      assert.doesNotMatch(error.message, /provider-private-diagnostic/);
      assert.match(error.message, /code=17/);
      return true;
    },
  );
});

test("JSON-lines synchronous start failure is typed and sanitized", async () => {
  await assert.rejects(
    NoKVJsonLinesTransport.open({
      argv: ["injected-helper"],
      config: {},
      process_factory: () => {
        throw new Error("private helper path and provider detail");
      },
    }),
    (error: unknown) => {
      assert.ok(error instanceof NoKVTransportUnavailableError);
      assert.equal(error.message, "NoKV helper failed to start");
      return true;
    },
  );
});

test("JSON-lines invalid response is a protocol failure", async (t) => {
  const transport = await openFaultHelper("invalid");
  t.after(async () => await transport.close());

  await assert.rejects(
    transport.readBlob("authority-workbench", "metadata/head.json"),
    NoKVTransportProtocolError,
  );
});

test("JSON-lines response limit fails closed as a protocol error", async (t) => {
  const transport = await openFaultHelper("oversized", 256);
  t.after(async () => await transport.close());

  await assert.rejects(
    transport.readBlob("authority-workbench", "metadata/head.json"),
    (error: unknown) => {
      assert.ok(error instanceof NoKVTransportProtocolError);
      assert.match(error.message, /max_response_bytes/);
      return true;
    },
  );
});

test("NoKV AuthorityStore preserves helper protocol failure as failed, not missing", async (t) => {
  const transport = await openFaultHelper("invalid");
  t.after(async () => await transport.close());
  const store = new NoKVAuthorityStore(transport, {
    tenant_id: "tenant-a",
    goal_id: "goal-a",
    workbench: "authority-workbench",
  });

  const loaded = await store.loadAuthority();
  assert.equal(loaded.status, "failed");
  if (loaded.status === "failed") {
    assert.equal(loaded.reason_code, "provider_protocol_violation");
  }
});

test("JSON-lines transport opens the real helper with seed routing", async () => {
  const transport = await openSdkHelper(SEEDS_ROUTING);
  try {
    const identity = await transport.storeIdentity("authority-workbench");
    assert.equal(identity.status, "available");
    if (identity.status !== "available") throw new Error("unreachable");
    assert.equal(identity.store_identity, `nokv:authority-workbench:${"a".repeat(32)}`);
  } finally {
    await transport.close();
  }
});

test("JSON-lines transport surfaces an unknown routing kind as a typed protocol failure", async () => {
  await assert.rejects(
    openSdkHelper({ kind: "gossip", endpoints: ["127.0.0.1:7750"] }),
    (error: unknown) =>
      error instanceof NoKVTransportProtocolError && /routing kind/.test(error.message),
  );
});

