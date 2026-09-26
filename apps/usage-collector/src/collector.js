// Pure request handling for the LoopX usage collector. worker.js binds it to
// Cloudflare; tests bind it to an in-memory database.

export const PAYLOAD_SCHEMA = "loopx_usage_ping_v0";
export const MAX_BODY_BYTES = 1024;
export const RETENTION_DAYS = 400;
export const MIN_BUCKET = 5;

const FIELDS = ["schema", "install_id", "version", "os", "python", "channel"];
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const VERSION = /^\d{1,3}\.\d{1,3}\.\d{1,4}(?:[.+-][0-9A-Za-z.]{1,20})?$/;
const PYTHON = /^3\.\d{1,2}$/;
const OS = new Set(["darwin", "linux", "windows", "other"]);
const CHANNEL = new Set(["pip", "local_release", "source", "unknown"]);

export function validatePing(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return { error: "body must be a JSON object" };
  const keys = Object.keys(value).sort();
  if (keys.join() !== [...FIELDS].sort().join()) return { error: `fields must be exactly ${FIELDS.join(", ")}` };
  if (!FIELDS.every((key) => typeof value[key] === "string")) return { error: "every field must be a string" };
  if (value.schema !== PAYLOAD_SCHEMA) return { error: "unsupported schema" };
  if (!UUID_V4.test(value.install_id)) return { error: "install_id must be a lowercase UUIDv4" };
  if (!VERSION.test(value.version)) return { error: "invalid version" };
  if (!OS.has(value.os)) return { error: "invalid os" };
  if (!PYTHON.test(value.python)) return { error: "invalid python" };
  if (!CHANNEL.has(value.channel)) return { error: "invalid channel" };
  return { ping: Object.fromEntries(FIELDS.map((key) => [key, value[key]])) };
}

export function utcDay(now) {
  return now.toISOString().slice(0, 10);
}

function shiftDays(day, delta) {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + delta);
  return utcDay(date);
}

function recentMonths(day, count) {
  const [year, month] = day.split("-").map(Number);
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(Date.UTC(year, month - 1 - index, 1));
    return date.toISOString().slice(0, 7);
  }).reverse();
}

// Buckets smaller than MIN_BUCKET are merged into "other" so the public
// breakdown never singles out a handful of installations.
export function suppressSmall(rows, minimum = MIN_BUCKET) {
  const out = {};
  let other = 0;
  for (const { key, installs } of rows) {
    if (installs >= minimum) out[key] = installs;
    else other += installs;
  }
  if (other) out.other = (out.other ?? 0) + other;
  return out;
}

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), { status, headers: { ...JSON_HEADERS, ...extra } });
}

export async function recordPing(db, ping, day) {
  await db.batch([
    db.prepare("INSERT OR IGNORE INTO installs (install_id, first_day) VALUES (?1, ?2)").bind(ping.install_id, day),
    db
      .prepare(
        "INSERT INTO pings (day, install_id, version, os, python, channel) VALUES (?1, ?2, ?3, ?4, ?5, ?6) " +
          "ON CONFLICT (day, install_id) DO UPDATE SET version = excluded.version, os = excluded.os, " +
          "python = excluded.python, channel = excluded.channel",
      )
      .bind(day, ping.install_id, ping.version, ping.os, ping.python, ping.channel),
  ]);
}

export async function buildStats(db, day) {
  const months = recentMonths(day, 12);
  const first = `${months[0]}-01`;
  const active = await db
    .prepare("SELECT substr(day, 1, 7) AS month, COUNT(DISTINCT install_id) AS n FROM pings WHERE day >= ?1 GROUP BY month")
    .bind(first)
    .all();
  const fresh = await db
    .prepare("SELECT substr(first_day, 1, 7) AS month, COUNT(*) AS n FROM installs WHERE first_day >= ?1 GROUP BY month")
    .bind(first)
    .all();
  const daily = await db
    .prepare("SELECT day, COUNT(*) AS n FROM pings WHERE day >= ?1 GROUP BY day ORDER BY day")
    .bind(shiftDays(day, -29))
    .all();
  const rolling = await db
    .prepare("SELECT COUNT(DISTINCT install_id) AS n FROM pings WHERE day >= ?1")
    .bind(shiftDays(day, -29))
    .first();
  const byMonth = (rows) => Object.fromEntries(rows.results.map((row) => [row.month, row.n]));
  const activeByMonth = byMonth(active);
  const freshByMonth = byMonth(fresh);
  const breakdown = {};
  for (const column of ["version", "os", "channel"]) {
    // Latest attribute per installation this month, then count installations.
    const rows = await db
      .prepare(
        `SELECT ${column} AS key, COUNT(*) AS installs FROM (` +
          `SELECT install_id, ${column}, MAX(day) FROM pings WHERE substr(day, 1, 7) = ?1 GROUP BY install_id` +
          `) GROUP BY key ORDER BY installs DESC`,
      )
      .bind(day.slice(0, 7))
      .all();
    breakdown[column] = suppressSmall(rows.results);
  }
  return {
    schema: "loopx_usage_stats_v0",
    generated_on: day,
    definition:
      "Counts opted-in installations only. monthly_active: distinct installation ids with at least one ping " +
      "in the calendar month (UTC). new_installs: ids first seen that month. Buckets under " +
      `${MIN_BUCKET} installations are merged into "other".`,
    rolling_30d_active: rolling?.n ?? 0,
    months: months.map((month) => ({
      month,
      monthly_active: activeByMonth[month] ?? 0,
      new_installs: freshByMonth[month] ?? 0,
    })),
    daily_active: daily.results.map((row) => ({ day: row.day, active: row.n })),
    current_month_breakdown: breakdown,
  };
}

export async function purge(db, day) {
  const cutoff = shiftDays(day, -RETENTION_DAYS);
  await db.batch([
    db.prepare("DELETE FROM pings WHERE day < ?1").bind(cutoff),
    db.prepare("DELETE FROM installs WHERE install_id NOT IN (SELECT DISTINCT install_id FROM pings)"),
  ]);
}

export async function handle(request, db, now = new Date()) {
  const url = new URL(request.url);
  const day = utcDay(now);
  if (url.pathname === "/v0/ping") {
    if (request.method !== "POST") return json({ error: "method not allowed" }, 405, { allow: "POST" });
    if (!(request.headers.get("content-type") ?? "").startsWith("application/json")) {
      return json({ error: "content-type must be application/json" }, 415);
    }
    const raw = await request.text();
    if (new TextEncoder().encode(raw).length > MAX_BODY_BYTES) return json({ error: "body too large" }, 413);
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return json({ error: "invalid JSON" }, 400);
    }
    const { ping, error } = validatePing(parsed);
    if (error) return json({ error }, 400);
    await recordPing(db, ping, day);
    return new Response(null, { status: 204 });
  }
  if (url.pathname === "/v0/stats") {
    if (request.method !== "GET") return json({ error: "method not allowed" }, 405, { allow: "GET" });
    return json(await buildStats(db, day), 200, {
      "cache-control": "public, max-age=3600",
      "access-control-allow-origin": "*",
    });
  }
  return json({ error: "not found" }, 404);
}
