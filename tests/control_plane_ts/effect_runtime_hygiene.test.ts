import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {connect, type Socket} from "node:net";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {setTimeout as delay} from "node:timers/promises";
import test from "node:test";
import {createRequire} from "node:module";

import {
  canonicalAuthorityObject,
  hashCanonicalCommitEnvelope,
} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {commitDigest, SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {createEffectRuntimeHandlers} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import {exchangeEffectRuntimeRequest} from "../../loopx/control_plane/effect_runtime_exchange.ts";

test("effect_runtime_server retires promptly on shutdown despite orphaned silent and aborted sockets", {timeout: 30_000}, async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-server-hygiene-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const infoPath = join(root, "runtime.json");
  const token = "hygiene-test-token";
  const child = spawn(process.execPath, [
    "--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
    "loopx/control_plane/effect_runtime_server.ts",
    "--info", infoPath, "--fingerprint", "hygiene-fixture",
  ], {
    env: {...process.env, LOOPX_EFFECT_RUNTIME_TOKEN: token, LOOPX_EFFECT_RUNTIME_IDLE_MS: "60000"},
    stdio: "ignore",
  });
  const exited = once(child, "close");
  t.after(async () => {
    if (child.exitCode === null) child.kill();
    await exited;
  });

  let info: {port: number; token: string} | undefined;
  for (let i = 0; i < 200; i += 1) {
    try {
      info = JSON.parse(await readFile(infoPath, "utf8"));
      break;
    } catch {
      if (child.exitCode !== null) throw new Error("server exited before publishing locator");
      await delay(20);
    }
  }
  assert.ok(info);

  const openSockets: Socket[] = [];
  t.after(() => {
    for (const sock of openSockets) sock.destroy();
  });

  const rpc = (method: string, params: Record<string, unknown> = {}): Promise<any> =>
    new Promise((resolve, reject) => {
      const sock = connect(info!.port, "127.0.0.1");
      sock.setEncoding("utf8");
      sock.setTimeout(5_000, () => sock.destroy(new Error("RPC timeout")));
      sock.on("error", reject);
      sock.on("connect", () => {
        sock.write(JSON.stringify({
          schema_version: "loopx_effect_runtime_request_v0",
          token,
          request_id: `req-${Math.random()}`,
          method,
          params,
        }) + "\n");
      });
      let raw = "";
      sock.on("data", chunk => { raw += chunk; });
      sock.on("end", () => {
        try { resolve(JSON.parse(raw)); }
        catch (err) { reject(err); }
      });
    });

  // Orphaned silent connection that never sends a newline and never closes.
  const silentSocket = connect(info.port, "127.0.0.1");
  openSockets.push(silentSocket);
  await once(silentSocket, "connect");

  // Client sending a partial unterminated frame and half-closing without newline.
  const partialSocket = connect(info.port, "127.0.0.1");
  openSockets.push(partialSocket);
  await once(partialSocket, "connect");
  partialSocket.end('{"incomplete":true');
  await once(partialSocket, "close");

  // Oversized frame cleanly rejected.
  const oversized = await new Promise<any>((resolve, reject) => {
    const sock = connect(info!.port, "127.0.0.1");
    sock.setEncoding("utf8");
    sock.on("error", reject);
    sock.on("connect", () => {
      sock.write("a".repeat(8 * 1024 * 1024 + 64));
    });
    let raw = "";
    sock.on("data", c => { raw += c; });
    sock.on("end", () => resolve(JSON.parse(raw)));
  });
  assert.equal(oversized.ok, false);
  assert.equal(oversized.error.code, "request_too_large");

  // Repeated RPCs succeed cleanly.
  for (let i = 0; i < 10; i += 1) {
    const ping = await rpc("runtime.ping");
    assert.equal(ping.ok, true);
    assert.equal(ping.result.ready, true);
  }

  // Requesting shutdown must retire cleanly even with silentSocket still connected.
  const shutdown = await rpc("runtime.shutdown");
  assert.equal(shutdown.ok, true);
  assert.equal(shutdown.result.stopped, true);
  const [code] = await Promise.race([
    exited,
    delay(5_000).then(() => { throw new Error("runtime.shutdown wedged on orphaned silent socket"); }),
  ]);
  assert.equal(code, 0);
});

test("effect_runtime_server times out orphaned silent sockets while staying alive for dispatched requests", {timeout: 15_000}, async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-server-timeout-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const infoPath = join(root, "runtime.json");
  const token = "timeout-test-token";
  const child = spawn(process.execPath, [
    "--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
    "loopx/control_plane/effect_runtime_server.ts",
    "--info", infoPath, "--fingerprint", "timeout-fixture",
  ], {
    env: {...process.env, LOOPX_EFFECT_RUNTIME_TOKEN: token, LOOPX_EFFECT_RUNTIME_IDLE_MS: "350"},
    stdio: "ignore",
  });
  const exited = once(child, "close");
  t.after(async () => {
    if (child.exitCode === null) child.kill();
    await exited;
  });

  let info: {port: number} | undefined;
  for (let i = 0; i < 200; i += 1) {
    try { info = JSON.parse(await readFile(infoPath, "utf8")); break; }
    catch { await delay(20); }
  }
  assert.ok(info);

  const silent = connect(info.port, "127.0.0.1");
  await once(silent, "connect");
  const silentClosed = once(silent, "close");

  // Keep server busy every 120ms so server-wide idleTimer stays reset; per-socket
  // idle timeout must still reap the orphaned silent connection.
  let closed = false;
  void silentClosed.then(() => { closed = true; });
  for (let i = 0; i < 8 && !closed; i += 1) {
    await delay(120);
    await new Promise<void>((resolve, reject) => {
      const sock = connect(info!.port, "127.0.0.1");
      sock.on("error", reject);
      sock.on("connect", () => sock.write(JSON.stringify({
        schema_version: "loopx_effect_runtime_request_v0",
        token, request_id: `keep-${i}`, method: "runtime.ping", params: {},
      }) + "\n"));
      sock.on("end", () => resolve());
      sock.resume();
    });
  }
  await silentClosed;
  assert.equal(closed, true);
});

test("exchangeEffectRuntimeRequest reuses resolved handlers cleanly across repeated dispatches", async () => {
  const token = "exchange-token";
  const handlers = createEffectRuntimeHandlers({fingerprint: "exchange-fp", requestShutdown: () => undefined});
  for (let i = 0; i < 5; i += 1) {
    const raw = await exchangeEffectRuntimeRequest(
      JSON.stringify({
        schema_version: "loopx_effect_runtime_request_v0",
        token,
        request_id: `req-${i}`,
        method: "task_lease.write_scopes.overlap",
        params: {left: ["docs/**"], right: ["docs/a.md"]},
      }),
      token,
      handlers,
    );
    assert.ok(raw !== null);
    const parsed = JSON.parse(raw.toString("utf8"));
    assert.equal(parsed.ok, true);
    assert.equal(parsed.result.overlap, true);
  }
  const rejectedRaw = await exchangeEffectRuntimeRequest(
    JSON.stringify({
      schema_version: "loopx_effect_runtime_request_v0",
      token: "wrong-token",
      request_id: "req-auth-fail",
      method: "runtime.ping",
      params: {},
    }),
    token,
    handlers,
  );
  assert.ok(rejectedRaw !== null);
  const rejected = JSON.parse(rejectedRaw.toString("utf8"));
  assert.equal(rejected.ok, false);
  assert.equal(rejected.request_id, "req-auth-fail");
  assert.equal(rejected.error.code, "authentication_failed");
});

test("hashCanonicalCommitEnvelope matches commitDigest across special keys and preserves BEGIN failure", async (t) => {
  const projection = JSON.parse(
    '{"": {"k": 1}, "__proto__": {"m": "v"}, "10": "ten", "2": "two", "z": [{"__proto__": "nested"}]}',
  ) as Record<string, unknown>;
  const canonicalProjection = canonicalAuthorityObject(projection, "projection");
  const events = [{kind: "observed", sequence: 1}];
  const receipts = [{ok: true}];
  const expected = commitDigest("sqlite:" + "a".repeat(32), 2n, "op-special", projection, events, receipts);
  const actual = hashCanonicalCommitEnvelope(
    {
      expected_provider_revision: `sqlite:${"a".repeat(32)}:1`,
      operation_id: "op-special",
      events,
      receipts,
    },
    (hash) => {
      hash.update(JSON.stringify(canonicalProjection), "utf8");
    },
  );
  assert.equal(actual, expected);

  const dir = await mkdtemp(join(tmpdir(), "loopx-sqlite-hygiene-"));
  t.after(() => rm(dir, {recursive: true, force: true}));
  const store = new SqliteAuthorityStore(dir, "goal-hygiene");
  const r1 = await store.commitAuthority({
    expected_provider_revision: null,
    operation_id: "op-special-1",
    next_projection: projection,
    events,
    receipts,
  });
  assert.equal(r1.status, "applied");
  const r2 = await store.commitAuthority({
    expected_provider_revision: r1.status === "applied" ? r1.provider_revision : null,
    operation_id: "op-special-2",
    next_projection: {...projection, extra: 2},
    events,
    receipts,
  });
  assert.equal(r2.status, "applied");
  assert.equal((await store.loadAuthority()).status, "loaded");
  assert.equal((await store.readReceipts(["op-special-1", "op-special-2", "missing-op"])).status, "receipts");
  assert.equal((await store.verifyAuthorityHistory()).status, "verified");

  // Verify open() rollback guard preserves BEGIN IMMEDIATE error without masking by ROLLBACK error.
  const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
  const holder = new DatabaseSync(store.path);
  holder.exec("BEGIN EXCLUSIVE");
  try {
    const busy = await store.commitAuthority({
      expected_provider_revision: r2.status === "applied" ? r2.provider_revision : null,
      operation_id: "op-busy",
      next_projection: projection,
      events: [],
      receipts: [],
    });
    assert.equal(busy.status, "failed");
    assert.equal(busy.reason_code, "provider_transaction_failed");
  } finally {
    holder.exec("ROLLBACK");
    holder.close();
  }
});
