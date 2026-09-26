// Runs the collector against real SQLite through a minimal D1-shaped shim.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import { MAX_BODY_BYTES, handle, purge, suppressSmall, validatePing } from "../src/collector.js";

const SCHEMA = readFileSync(new URL("../schema.sql", import.meta.url), "utf8");

function d1() {
  const db = new DatabaseSync(":memory:");
  db.exec(SCHEMA);
  // D1 returns plain objects; node:sqlite returns null-prototype rows.
  const plain = (row) => (row ? { ...row } : null);
  const statement = (sql, params = []) => ({
    bind: (...args) => statement(sql, args),
    run: async () => db.prepare(sql).run(...params),
    all: async () => ({ results: db.prepare(sql).all(...params).map(plain) }),
    first: async () => plain(db.prepare(sql).get(...params)),
  });
  const raw = {
    all: (sql) => db.prepare(sql).all().map(plain),
    get: (sql) => plain(db.prepare(sql).get()),
  };
  return {
    raw,
    prepare: (sql) => statement(sql),
    batch: async (statements) => {
      db.exec("BEGIN");
      try {
        for (const stmt of statements) await stmt.run();
        db.exec("COMMIT");
      } catch (error) {
        db.exec("ROLLBACK");
        throw error;
      }
    },
  };
}

const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const ping = (n, extra = {}) => ({
  schema: "loopx_usage_ping_v0",
  install_id: id(n),
  version: "1.2.0",
  os: "darwin",
  python: "3.12",
  channel: "pip",
  ...extra,
});
const post = (body, headers = { "content-type": "application/json" }) =>
  new Request("https://collector.example/v0/ping", {
    method: "POST",
    headers,
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
const at = (day) => new Date(`${day}T12:00:00Z`);

test("validation accepts exactly the documented payload", () => {
  assert.ok(validatePing(ping(1)).ping);
  assert.match(validatePing({ ...ping(1), hostname: "laptop" }).error, /fields must be exactly/);
  assert.match(validatePing({ ...ping(1), install_id: "not-a-uuid" }).error, /UUIDv4/);
  assert.match(validatePing({ ...ping(1), os: "haiku" }).error, /os/);
  assert.match(validatePing({ ...ping(1), channel: "/Users/me/loopx" }).error, /channel/);
  assert.match(validatePing({ ...ping(1), version: 120 }).error, /string/);
  assert.match(validatePing([ping(1)]).error, /object/);
});

test("ping endpoint rejects wrong method, type, size and JSON", async () => {
  const db = d1();
  const get = await handle(new Request("https://collector.example/v0/ping"), db);
  assert.equal(get.status, 405);
  assert.equal((await handle(post(ping(1), { "content-type": "text/plain" }), db)).status, 415);
  assert.equal((await handle(post("x".repeat(MAX_BODY_BYTES + 1)), db)).status, 413);
  assert.equal((await handle(post("{"), db)).status, 400);
  assert.equal(db.raw.get("SELECT COUNT(*) AS n FROM pings").n, 0);
});

test("one row per installation per day; first day kept; only payload fields stored", async () => {
  const db = d1();
  assert.equal((await handle(post(ping(1)), db, at("2026-10-01"))).status, 204);
  assert.equal((await handle(post(ping(1, { version: "1.2.1" })), db, at("2026-10-01"))).status, 204);
  await handle(post(ping(1)), db, at("2026-10-02"));
  const rows = db.raw.all("SELECT * FROM pings ORDER BY day");
  assert.equal(rows.length, 2);
  assert.equal(rows[0].version, "1.2.1");
  assert.deepEqual(Object.keys(rows[0]).sort(), ["channel", "day", "install_id", "os", "python", "version"]);
  assert.deepEqual(db.raw.all("SELECT * FROM installs"), [{ install_id: id(1), first_day: "2026-10-01" }]);
});

test("stats report monthly active, new installs and suppressed breakdowns", async () => {
  const db = d1();
  for (let n = 1; n <= 6; n++) await handle(post(ping(n)), db, at("2026-09-10"));
  for (let n = 1; n <= 3; n++) await handle(post(ping(n)), db, at("2026-10-03"));
  await handle(post(ping(7, { os: "linux" })), db, at("2026-10-04"));
  await handle(post(ping(8, { os: "linux" })), db, at("2026-10-05"));
  const response = await handle(new Request("https://collector.example/v0/stats"), db, at("2026-10-05"));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("access-control-allow-origin"), "*");
  const stats = await response.json();
  const month = (m) => stats.months.find((row) => row.month === m);
  assert.equal(stats.months.length, 12);
  assert.deepEqual(month("2026-09"), { month: "2026-09", monthly_active: 6, new_installs: 6 });
  assert.deepEqual(month("2026-10"), { month: "2026-10", monthly_active: 5, new_installs: 2 });
  assert.equal(stats.rolling_30d_active, 8); // window reaches back to 2026-09-06
  // 3 darwin + 2 linux: both under the threshold, so neither is published.
  assert.deepEqual(stats.current_month_breakdown.os, { other: 5 });
  assert.deepEqual(stats.current_month_breakdown.version, { "1.2.0": 5 });
});

test("suppressSmall folds small buckets into other", () => {
  assert.deepEqual(
    suppressSmall([
      { key: "1.2.0", installs: 9 },
      { key: "1.1.0", installs: 2 },
      { key: "1.0.0", installs: 1 },
    ]),
    { "1.2.0": 9, other: 3 },
  );
});

test("purge drops rows past retention and orphaned installs", async () => {
  const db = d1();
  await handle(post(ping(1)), db, at("2025-01-01"));
  await handle(post(ping(2)), db, at("2026-09-01"));
  await purge(db, "2026-09-26");
  assert.deepEqual(db.raw.all("SELECT install_id FROM installs"), [{ install_id: id(2) }]);
  assert.equal(db.raw.get("SELECT COUNT(*) AS n FROM pings").n, 1);
});

test("unknown paths are 404", async () => {
  assert.equal((await handle(new Request("https://collector.example/"), d1())).status, 404);
});
