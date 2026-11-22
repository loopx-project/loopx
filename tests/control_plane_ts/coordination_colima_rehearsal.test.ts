import assert from "node:assert/strict";
import {execFile} from "node:child_process";
import net from "node:net";
import {dirname, resolve} from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";
import {promisify} from "node:util";
import {
  ColimaPgCoordinationRehearsal,
  parsePostgreSqlServerVersion,
  type RehearsalReport,
} from "../../examples/coordination/colima-pg16-e2e-rehearsal.ts";

const execFileAsync = promisify(execFile);
const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

function pgFrame(type: string, body: Buffer): Buffer {
  const header = Buffer.allocUnsafe(5);
  header.write(type, 0, 1, "ascii");
  header.writeInt32BE(4 + body.length, 1);
  return Buffer.concat([header, body]);
}

function pgCString(value: string): Buffer {
  return Buffer.from(`${value}\0`, "utf8");
}

function pgRowDescription(columnName: string): Buffer {
  const count = Buffer.allocUnsafe(2);
  count.writeInt16BE(1, 0);
  const meta = Buffer.allocUnsafe(18);
  meta.writeInt32BE(0, 0); // table oid
  meta.writeInt16BE(0, 4); // attr num
  meta.writeInt32BE(25, 6); // TEXTOID
  meta.writeInt16BE(-1, 10); // type len
  meta.writeInt32BE(-1, 12); // type mod
  meta.writeInt16BE(0, 16); // text format
  return pgFrame("T", Buffer.concat([count, pgCString(columnName), meta]));
}

function pgDataRow(value: string): Buffer {
  const valBuf = Buffer.from(value, "utf8");
  const count = Buffer.allocUnsafe(2);
  count.writeInt16BE(1, 0);
  const len = Buffer.allocUnsafe(4);
  len.writeInt32BE(valBuf.length, 0);
  return pgFrame("D", Buffer.concat([count, len, valBuf]));
}

function pgErrorResponse(message: string): Buffer {
  return pgFrame(
    "E",
    Buffer.concat([
      Buffer.from("S", "ascii"), pgCString("ERROR"),
      Buffer.from("C", "ascii"), pgCString("42000"),
      Buffer.from("M", "ascii"), pgCString(message),
      Buffer.from([0]),
    ]),
  );
}

async function startFakePgVersionServer(
  serverVersion: string,
  serverVersionNum: string,
): Promise<{port: number; queries: string[]; close: () => Promise<void>}> {
  const queries: string[] = [];
  const sockets = new Set<net.Socket>();
  const server = net.createServer(socket => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
    let buffer = Buffer.alloc(0);
    let startupDone = false;

    socket.on("data", chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      while (buffer.length > 0) {
        if (!startupDone) {
          if (buffer.length < 8) return;
          const msgLen = buffer.readInt32BE(0);
          if (buffer.length < msgLen) return;
          const code = buffer.readInt32BE(4);
          buffer = buffer.subarray(msgLen);
          if (code === 80877103) {
            // SSLRequest -> decline TLS so driver proceeds with plaintext v3 startup
            socket.write(Buffer.from("N", "ascii"));
            continue;
          }
          startupDone = true;
          const authOk = Buffer.alloc(4);
          authOk.writeInt32BE(0, 0);
          const paramBody = Buffer.concat([pgCString("server_version"), pgCString(serverVersion)]);
          const backendKey = Buffer.alloc(8);
          backendKey.writeInt32BE(1001, 0);
          backendKey.writeInt32BE(2002, 4);
          socket.write(Buffer.concat([
            pgFrame("R", authOk),
            pgFrame("S", paramBody),
            pgFrame("K", backendKey),
            pgFrame("Z", Buffer.from("I", "ascii")),
          ]));
          continue;
        }

        if (buffer.length < 5) return;
        const msgType = String.fromCharCode(buffer[0]!);
        const msgLen = buffer.readInt32BE(1);
        if (buffer.length < 1 + msgLen) return;
        const payload = buffer.subarray(5, 1 + msgLen);
        buffer = buffer.subarray(1 + msgLen);

        if (msgType === "X") {
          socket.end();
          return;
        }
        if (msgType === "Q") {
          const sql = payload.toString("utf8").replace(/\0+$/, "").trim();
          queries.push(sql);
          const normalized = sql.toLowerCase();
          if (normalized === "show server_version") {
            socket.write(Buffer.concat([
              pgRowDescription("server_version"),
              pgDataRow(serverVersion),
              pgFrame("C", pgCString("SHOW")),
              pgFrame("Z", Buffer.from("I", "ascii")),
            ]));
          } else if (normalized === "show server_version_num") {
            socket.write(Buffer.concat([
              pgRowDescription("server_version_num"),
              pgDataRow(serverVersionNum),
              pgFrame("C", pgCString("SHOW")),
              pgFrame("Z", Buffer.from("I", "ascii")),
            ]));
          } else {
            socket.write(Buffer.concat([
              pgErrorResponse(`Unexpected write/query before version gate: ${sql}`),
              pgFrame("Z", Buffer.from("I", "ascii")),
            ]));
          }
        }
      }
    });
  });

  await new Promise<void>(resolveListen => server.listen(0, "127.0.0.1", resolveListen));
  const address = server.address();
  assert.ok(address && typeof address !== "string");

  return {
    port: address.port,
    queries,
    close: async () => {
      for (const s of sockets) s.destroy();
      await new Promise<void>((res, rej) => server.close(err => (err ? rej(err) : res())));
    },
  };
}

function assertAllRehearsalSteps(
  report: RehearsalReport,
  expectedMode: RehearsalReport["mode"],
  expectedBoundary: RehearsalReport["execution_boundary"],
): void {
  assert.equal(report.schema_version, "colima_pg16_coordination_rehearsal_report_v0");
  assert.equal(report.ok, true, `Rehearsal failed with error: ${report.error ?? JSON.stringify(report.steps)}`);
  assert.equal(report.mode, expectedMode);
  assert.equal(report.execution_boundary, expectedBoundary);
  assert.equal(report.transport, "https_tls");
  assert.equal(report.clock_mode, "controlled_service_clock");
  assert.equal(report.runtime_role_verified, expectedMode === "real_pg");
  if (expectedMode === "real_pg") {
    assert.equal(typeof report.server_version, "string");
    assert.equal(report.server_major_version, 16);
    assert.ok(
      typeof report.server_version_num === "number" &&
      report.server_version_num >= 160_000 &&
      report.server_version_num < 170_000,
    );
    assert.equal(report.pg16_qualified, true);
  } else {
    assert.equal(report.server_version, null);
    assert.equal(report.server_version_num, null);
    assert.equal(report.server_major_version, null);
    assert.equal(report.pg16_qualified, false);
  }
  assert.equal(report.tenant_id, "tenant-canary");
  assert.equal(report.goal_id, "loopx-quality-review");
  assert.equal(report.actors.actor_a, "operator.local");
  assert.equal(report.actors.actor_b, "worker.cloud");
  assert.equal(report.steps.length, 8);

  const stepsByName = new Map(report.steps.map(s => [s.step, s]));

  // Step 1: Schema initialization
  const step1 = stepsByName.get("schema_initialization_and_role_assembly");
  assert.ok(step1);
  assert.equal(step1.ok, true);
  assert.equal(step1.detail.status, "installed");
  assert.match(String(step1.detail.runtime_role), /^loopx_app_[0-9a-f]+$/);

  // Step 2: Genesis seed ingestion
  const step2 = stepsByName.get("genesis_seed_ingestion");
  assert.ok(step2);
  assert.equal(step2.ok, true);
  assert.equal(step2.detail.status, "seeded");
  assert.equal(step2.detail.cursor, "1");

  // Step 3: Actor A lease claim over HTTPS/TLS
  const step3 = stepsByName.get("actor_a_lease_claim");
  assert.ok(step3);
  assert.equal(step3.ok, true);
  assert.equal(step3.detail.result_status, "applied");
  assert.equal(step3.detail.claimed_by, "operator.local");
  assert.equal(step3.detail.transport, "https_tls");

  // Step 4: Active lease conflict rejection
  const step4 = stepsByName.get("active_lease_conflict_rejection");
  assert.ok(step4);
  assert.equal(step4.ok, true);
  assert.equal(step4.detail.status, "result");
  assert.ok(step4.detail.result_status === "rejected" || step4.detail.result_status === "failed");

  // Step 5: Default-deny boundary enforcement
  const step5 = stepsByName.get("default_deny_boundary_enforcement");
  assert.ok(step5);
  assert.equal(step5.ok, true);
  assert.equal(step5.detail.unauthorized_goal_status, "rejected");
  assert.equal(step5.detail.unauthorized_cred_status, "rejected");
  assert.equal(step5.detail.unauthorized_cred_reason, "principal_unauthenticated");

  // Step 6: Lease expiration takeover & stale-actor fencing
  const step6 = stepsByName.get("lease_expiration_takeover");
  assert.ok(step6);
  assert.equal(step6.ok, true);
  assert.equal(step6.detail.result_status, "applied");
  assert.equal(step6.detail.claimed_by, "worker.cloud");
  assert.equal(step6.detail.previous_lease_epoch, 1);
  assert.equal(step6.detail.new_lease_epoch, 2);
  assert.equal(step6.detail.stale_actor_fenced, true);
  // Prove takeover is driven from observed expires_at via controlled service clock rather than wall-clock sleep.
  const observedExpiresMs = Date.parse(String(step3.detail.expires_at));
  const advancedClockMs = Date.parse(String(step6.detail.advanced_service_clock));
  assert.ok(Number.isFinite(observedExpiresMs));
  assert.ok(Number.isFinite(advancedClockMs));
  assert.ok(observedExpiresMs - Date.now() > 60_000, "canonical lease expires ~2700s in the future");
  assert.equal(step6.detail.observed_expires_at, step3.detail.expires_at);
  assert.equal(advancedClockMs, observedExpiresMs + 1_000);

  // Step 7: Actor B task completion + historical recovery readback
  const step7 = stepsByName.get("actor_b_task_completion");
  assert.ok(step7);
  assert.equal(step7.ok, true);
  assert.equal(step7.detail.result_status, "applied");
  assert.equal(step7.detail.completed, true);
  assert.equal(step7.detail.recover_status, "found");
  assert.equal(step7.detail.historical_only, true);

  // Step 8: Cross-machine snapshot & direct authority readback
  const step8 = stepsByName.get("cross_machine_snapshot_readback");
  assert.ok(step8);
  assert.equal(step8.ok, true);
  assert.equal(step8.detail.status, "snapshot");
  assert.equal(typeof step8.detail.receipt_sha256, "string");
  assert.equal((step8.detail.receipt_sha256 as string).length, 64);
  assert.equal(step8.detail.todo_count, 1);
  assert.equal(step8.detail.read_status, "found");
  assert.equal(step8.detail.direct_cursor, "5");
}

test("Colima/PG16 coordination rehearsal completes all 8 HTTPS/TLS steps in simulation mode", async () => {
  const rehearsal = new ColimaPgCoordinationRehearsal({
    simulate: true,
    leaseTtlSeconds: 1,
  });
  const report = await rehearsal.run();
  assertAllRehearsalSteps(report, "simulation", "in_memory_simulated_pg_tls");
});

test(
  "Colima/PG16 coordination rehearsal qualifies all 8 steps against real PostgreSQL with restricted role and TLS",
  {skip: !process.env.LOOPX_TEST_POSTGRES_URL},
  async () => {
    const rehearsal = new ColimaPgCoordinationRehearsal({
      databaseUrl: process.env.LOOPX_TEST_POSTGRES_URL,
      simulate: false,
    });
    const report = await rehearsal.run();
    assertAllRehearsalSteps(report, "real_pg", "isolated_real_pg16_tls");
  },
);

test("parsePostgreSqlServerVersion extracts major version from server_version and server_version_num", () => {
  assert.deepEqual(parsePostgreSqlServerVersion("15.20", "150020"), {
    server_version: "15.20",
    server_version_num: 150020,
    server_major_version: 15,
  });
  assert.deepEqual(parsePostgreSqlServerVersion("16.4 (Debian 16.4-1.pgdg120+1)", "160004"), {
    server_version: "16.4 (Debian 16.4-1.pgdg120+1)",
    server_version_num: 160004,
    server_major_version: 16,
  });
  assert.deepEqual(parsePostgreSqlServerVersion("17.2", undefined), {
    server_version: "17.2",
    server_version_num: 170002,
    server_major_version: 17,
  });
  assert.deepEqual(parsePostgreSqlServerVersion("invalid", "unknown"), {
    server_version: "invalid",
    server_version_num: null,
    server_major_version: null,
  });
});

test("Colima/PG16 coordination rehearsal refuses PostgreSQL 15.20 before any DB write via TS and shell", async () => {
  const server = await startFakePgVersionServer("15.20", "150020");
  try {
    const url = `postgresql://postgres:secret@127.0.0.1:${server.port}/postgres`;

    // 1) Programmatic API check
    const report = await new ColimaPgCoordinationRehearsal({
      databaseUrl: url,
      simulate: false,
    }).run();
    assert.equal(report.ok, false);
    assert.equal(report.mode, "real_pg");
    assert.equal(report.execution_boundary, "isolated_real_postgresql_tls");
    assert.equal(report.server_version, "15.20");
    assert.equal(report.server_version_num, 150020);
    assert.equal(report.server_major_version, 15);
    assert.equal(report.pg16_qualified, false);
    assert.equal(report.runtime_role_verified, false);
    assert.equal(report.steps.length, 0);
    assert.match(report.error ?? "", /PostgreSQL 16 is required.*15\.20/);
    assert.deepEqual(server.queries, ["SHOW server_version", "SHOW server_version_num"]);

    // 2) Same shell --database-url entrypoint check
    server.queries.length = 0;
    let shellStdout = "";
    let exitCode: unknown = 0;
    try {
      await execFileAsync(
        resolve(REPO_ROOT, "examples/coordination/colima-pg16-e2e-rehearsal.sh"),
        ["--database-url", url],
        {cwd: REPO_ROOT},
      );
    } catch (err) {
      const execErr = err as {code?: unknown; stdout?: string};
      exitCode = execErr.code;
      shellStdout = execErr.stdout ?? "";
    }
    assert.equal(exitCode, 1);
    const shellReport = JSON.parse(shellStdout) as RehearsalReport;
    assert.equal(shellReport.ok, false);
    assert.equal(shellReport.execution_boundary, "isolated_real_postgresql_tls");
    assert.equal(shellReport.server_version, "15.20");
    assert.equal(shellReport.server_version_num, 150020);
    assert.equal(shellReport.server_major_version, 15);
    assert.equal(shellReport.pg16_qualified, false);
    assert.equal(shellReport.steps.length, 0);
    assert.deepEqual(server.queries, ["SHOW server_version", "SHOW server_version_num"]);
  } finally {
    await server.close();
  }
});

test("Colima/PG16 coordination rehearsal refuses PostgreSQL 17.2 before any DB write", async () => {
  const server = await startFakePgVersionServer("17.2", "170002");
  try {
    const report = await new ColimaPgCoordinationRehearsal({
      databaseUrl: `postgresql://postgres:secret@127.0.0.1:${server.port}/postgres`,
      simulate: false,
    }).run();
    assert.equal(report.ok, false);
    assert.equal(report.execution_boundary, "isolated_real_postgresql_tls");
    assert.equal(report.server_version, "17.2");
    assert.equal(report.server_version_num, 170002);
    assert.equal(report.server_major_version, 17);
    assert.equal(report.pg16_qualified, false);
    assert.equal(report.steps.length, 0);
    assert.deepEqual(server.queries, ["SHOW server_version", "SHOW server_version_num"]);
  } finally {
    await server.close();
  }
});

test(
  "Colima/PG16 coordination rehearsal refuses live non-16 PostgreSQL server without marking PG16",
  {skip: !process.env.LOOPX_TEST_POSTGRES_NON16_URL},
  async () => {
    const report = await new ColimaPgCoordinationRehearsal({
      databaseUrl: process.env.LOOPX_TEST_POSTGRES_NON16_URL,
      simulate: false,
    }).run();
    assert.equal(report.ok, false);
    assert.equal(report.mode, "real_pg");
    assert.equal(report.execution_boundary, "isolated_real_postgresql_tls");
    assert.equal(report.pg16_qualified, false);
    assert.notEqual(report.server_major_version, 16);
    assert.equal(report.steps.length, 0);
  },
);
