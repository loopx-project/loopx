/** Selection only. The explicit local-bank transport owns locks and deletion.
 * Pins and recent points are hard protections, never a storage-budget hint.
 */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {authorityUnicodeCompare, canonicalAuthoritySha256, hasExactAuthorityKeys} from "../coordination/authority_store_codec.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";

type Point = {id: string; sha256: string; sealed_at: number; bytes: number};
type Reason = "latest" | "first" | "pin" | "grace" | "since" | "history";

function finite(value: unknown, label: string, minimum = 0): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum) throw new Error(`invalid retention ${label}`);
  return value;
}
function integer(value: unknown, label: string, minimum: number): number {
  const number = finite(value, label, minimum);
  if (!Number.isSafeInteger(number)) throw new Error(`invalid retention ${label}`);
  return number;
}
function identity(raw: unknown): {id: string; sha256: string} {
  const row = requireJsonObject(raw, "retention point");
  if (typeof row.id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(row.id) ||
      typeof row.sha256 !== "string" || !BARE_SHA256_PATTERN.test(row.sha256)) throw new Error("invalid retention point identity");
  return {id: row.id, sha256: row.sha256};
}

export function checkpointRetentionPlan(raw: unknown): JsonObject {
  const input = requireJsonObject(raw, "retention input");
  if (!hasExactAuthorityKeys(input, ["policy", "points", "now"])) throw new Error("invalid retention input fields");
  // The one default owner. Capture enablement and local execution remain with
  // their callers; selecting this policy grants neither.
  const policy = input.policy === null ? {strategy: "uniform", keep_latest: 1, keep_first: false,
    minimum_age_seconds: 0, history_slots: 0, protect_since: null, pins: []} :
    requireJsonObject(input.policy, "retention policy");
  const common = ["strategy", "keep_latest", "keep_first", "minimum_age_seconds", "pins", "protect_since"];
  const strategy = policy.strategy;
  const extra = strategy === "tiered" ? "bucket_seconds" : strategy === "uniform" ? "history_slots" : null;
  if (!extra || !hasExactAuthorityKeys(policy, [...common, extra]) || typeof policy.keep_first !== "boolean") {
    throw new Error("invalid retention policy fields");
  }
  const latest = integer(policy.keep_latest, "keep_latest", 1);
  const grace = finite(policy.minimum_age_seconds, "minimum_age_seconds");
  const since = policy.protect_since === null ? null : finite(policy.protect_since, "protect_since");
  const spacing = integer(policy[extra], extra, strategy === "tiered" ? 1 : 0);
  if (strategy === "uniform" && spacing > 0 && policy.keep_first !== true) {
    throw new Error("uniform retention history requires keep_first for stable time anchors");
  }
  const now = finite(input.now, "now");
  if (!Array.isArray(input.points) || !Array.isArray(policy.pins)) throw new Error("retention points and pins must be arrays");
  const seen = new Set<string>();
  const points: Point[] = input.points.map(rawPoint => {
    const p = requireJsonObject(rawPoint, "retention point");
    if (!hasExactAuthorityKeys(p, ["id", "sha256", "sealed_at", "bytes"])) throw new Error("invalid retention point fields");
    const ref = identity(p);
    if (seen.has(ref.id)) throw new Error("duplicate retention point identity");
    seen.add(ref.id);
    return {...ref, sealed_at: finite(p.sealed_at, "sealed_at"), bytes: integer(p.bytes, "bytes", 0)};
  }).sort((a, b) => a.sealed_at - b.sealed_at || authorityUnicodeCompare(a.id, b.id));
  const reasons = new Map<string, Set<Reason>>();
  const keep = (p: Point, reason: Reason) => {
    if (!reasons.has(p.id)) reasons.set(p.id, new Set());
    reasons.get(p.id)!.add(reason);
  };
  for (const rawPin of policy.pins) {
    const pin = requireJsonObject(rawPin, "retention pin");
    if (!hasExactAuthorityKeys(pin, ["id", "sha256"])) throw new Error("invalid retention pin fields");
    const ref = identity(pin), found = points.find(p => p.id === ref.id);
    if (!found || found.sha256 !== ref.sha256) throw new Error("pinned checkpoint missing or changed; reconcile before pruning");
    keep(found, "pin");
  }
  points.slice(-latest).forEach(p => keep(p, "latest"));
  if (policy.keep_first && points.length) keep(points[0], "first");
  for (const p of points) {
    // Future timestamps are protected too; clock skew cannot evict a point.
    if (now - p.sealed_at < grace) keep(p, "grace");
    if (since !== null && p.sealed_at >= since) keep(p, "since");
  }
  // Choose history independently of pins, so adding a pin can never evict an
  // existing history representative. No score, filenames or model judgments.
  const history = points.slice(0, Math.max(0, points.length - latest));
  if (strategy === "tiered" && points.length) {
    const newest = points[points.length - 1].sealed_at, buckets = new Set<number>();
    for (const p of history.slice().reverse()) {
      const age = Math.max(0, newest - p.sealed_at);
      const bucket = age < spacing ? 0 : 1 + Math.floor(Math.log2(age / spacing));
      if (!buckets.has(bucket)) {buckets.add(bucket); keep(p, "history");}
    }
  } else if (history.length && spacing) {
    // Equal-time targets across the historical range (not equal row indices).
    const start = history[0].sealed_at, end = history[history.length - 1].sealed_at;
    const remaining = new Set(history);
    for (let n = 1; n <= Math.min(spacing, history.length); n++) {
      // Include the historical end: keeping both temporal anchors makes a
      // repeated application on an unchanged bank idempotent.
      const target = start + (end - start) * n / Math.min(spacing, history.length);
      const nearest = [...remaining].sort((a, b) => Math.abs(a.sealed_at - target) - Math.abs(b.sealed_at - target) ||
        b.sealed_at - a.sealed_at || authorityUnicodeCompare(a.id, b.id))[0];
      remaining.delete(nearest); keep(nearest, "history");
    }
  }
  const retained = points.filter(p => reasons.has(p.id)).map(p => ({...p, reasons: [...reasons.get(p.id)!].sort()}));
  const removed = points.filter(p => !reasons.has(p.id));
  const expiries = points.filter(p => reasons.get(p.id)?.has("grace")).map(p => p.sealed_at + grace);
  const body: JsonObject = {policy, points, retained, removed,
    retained_bytes: retained.reduce((n, p) => n + p.bytes, 0), removable_bytes: removed.reduce((n, p) => n + p.bytes, 0),
    grace_expires_at: expiries.length ? Math.min(...expiries) : null,
    execution_authority_granted: false};
  return {...body, plan_sha256: canonicalAuthoritySha256(body)};
}
