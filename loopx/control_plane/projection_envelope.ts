/** Kernel projection envelope: when a read model observed each source, how
 * fresh each read is when the copy is served, and how much of the requested
 * scope it covers. `ok`, a passing check, or a cache hit never implies fresh
 * or complete sources; consumers read this envelope before stating current
 * state. */
import type { JsonObject } from "./effect_program.ts";
import { EffectRuntimeRequestError } from "./effect_runtime_errors.ts";
import {
  requireBoolean,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringLiteral,
} from "./runtime_decode.ts";
import { parseIsoTimestamp } from "./runtime_timestamp.ts";

export const PROJECTION_ENVELOPE_SCHEMA_VERSION = "loopx_projection_envelope_v0";
export const PROJECTION_ENVELOPE_SEAL_REQUEST = "loopx_projection_envelope_seal_request_v0";
export const PROJECTION_ENVELOPE_SERVE_REQUEST = "loopx_projection_envelope_serve_request_v0";
export const DEFAULT_SOURCE_WINDOW_SECONDS = 300;
export const SOURCE_READ_STATUSES = ["read", "missing", "unreadable", "not_read"] as const;
export type SourceReadStatus = typeof SOURCE_READ_STATUSES[number];
export type SourceStatus = "fresh" | "stale" | Exclude<SourceReadStatus, "read">;

const MAX_SOURCES = 32;
const MAX_OMISSIONS = 16;
const MAX_REFS = 8;
const MAX_UPSTREAM = 4;
const MAX_REF_CHARS = 128;
const IDENTIFIER = /^[a-z][a-z0-9_.]{0,63}$/u;
const REASON = /^[a-z][a-z0-9_.:-]{0,63}$/u;
const EXPLICIT_OFFSET = /(?:Z|z|[+-]\d{2}(?::?\d{2})?)$/u;

interface SourceFact {
  source_id: string;
  via: string | null;
  required: boolean;
  read_status: SourceReadStatus;
  last_read_at: string | null;
  source_updated_at: string | null;
  window_seconds: number;
  item_count: number | null;
  missing_count: number;
  unreadable_count: number;
}

interface Omission {
  reason: string;
  count: number;
  refs: string[];
}

interface CoverageFact {
  scope: string;
  expected_count: number | null;
  included_count: number;
  omitted: Omission[];
  shown_count: number | null;
  available_count: number | null;
}

interface UpstreamSummary {
  projection: string;
  observed_at: string;
  complete: boolean;
}

function timestamp(value: unknown, label: string): { text: string; millis: number } {
  const text = requireNonEmptyString(value, label).trim();
  const parsed = EXPLICIT_OFFSET.test(text) ? parseIsoTimestamp(text) : null;
  if (parsed === null) {
    throw new EffectRuntimeRequestError(`${label} must be an ISO timestamp with an explicit offset`);
  }
  return { text, millis: parsed.getTime() };
}

function optionalTimestamp(value: unknown, label: string): string | null {
  return value === null || value === undefined ? null : timestamp(value, label).text;
}

function identifier(value: unknown, label: string): string {
  const text = requireNonEmptyString(value, label);
  if (!IDENTIFIER.test(text)) throw new EffectRuntimeRequestError(`${label} is not a projection identifier`);
  return text;
}

function count(value: unknown, label: string): number {
  const result = requireInteger(value, label);
  if (result < 0) throw new EffectRuntimeRequestError(`${label} must be non-negative`);
  return result;
}

function optionalCount(value: unknown, label: string): number | null {
  return value === null || value === undefined ? null : count(value, label);
}

function boundedArray(value: unknown, limit: number, label: string): unknown[] {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  if (value.length > limit) throw new EffectRuntimeRequestError(`${label} exceeds ${limit} entries`);
  return value;
}

function decodeSource(value: unknown, index: number): SourceFact {
  const label = `sources[${index}]`;
  const row = requireJsonObject(value, label);
  const readStatus = requireStringLiteral(row.read_status, SOURCE_READ_STATUSES, `${label}.read_status`);
  const lastReadAt = optionalTimestamp(row.last_read_at, `${label}.last_read_at`);
  if (readStatus === "read" && lastReadAt === null) {
    throw new EffectRuntimeRequestError(`${label}.last_read_at is required for a read source`);
  }
  if (readStatus === "not_read" && lastReadAt !== null) {
    throw new EffectRuntimeRequestError(`${label} cannot be not_read with a last_read_at`);
  }
  const window = row.window_seconds === undefined || row.window_seconds === null
    ? DEFAULT_SOURCE_WINDOW_SECONDS
    : count(row.window_seconds, `${label}.window_seconds`);
  if (window === 0) throw new EffectRuntimeRequestError(`${label}.window_seconds must be positive`);
  return {
    source_id: identifier(row.source_id, `${label}.source_id`),
    via: row.via === undefined || row.via === null ? null : identifier(row.via, `${label}.via`),
    required: row.required === undefined ? true : requireBoolean(row.required, `${label}.required`),
    read_status: readStatus,
    last_read_at: lastReadAt,
    source_updated_at: optionalTimestamp(row.source_updated_at, `${label}.source_updated_at`),
    window_seconds: window,
    item_count: optionalCount(row.item_count, `${label}.item_count`),
    missing_count: optionalCount(row.missing_count, `${label}.missing_count`) ?? 0,
    unreadable_count: optionalCount(row.unreadable_count, `${label}.unreadable_count`) ?? 0,
  };
}

function decodeOmission(value: unknown, index: number): Omission {
  const label = `coverage.omitted[${index}]`;
  const row = requireJsonObject(value, label);
  const reason = requireNonEmptyString(row.reason, `${label}.reason`);
  if (!REASON.test(reason)) throw new EffectRuntimeRequestError(`${label}.reason is not a reason code`);
  const refs = row.refs === undefined ? [] : boundedArray(row.refs, MAX_REFS, `${label}.refs`).map((ref, refIndex) => {
    const text = requireNonEmptyString(ref, `${label}.refs[${refIndex}]`);
    if (text.length > MAX_REF_CHARS) throw new EffectRuntimeRequestError(`${label}.refs[${refIndex}] is too long`);
    return text;
  });
  const omittedCount = count(row.count, `${label}.count`);
  if (omittedCount === 0) throw new EffectRuntimeRequestError(`${label}.count must be positive`);
  return { reason, count: omittedCount, refs };
}

function decodeCoverage(value: unknown): CoverageFact {
  const coverage = requireJsonObject(value, "coverage");
  return {
    scope: identifier(coverage.scope, "coverage.scope"),
    expected_count: optionalCount(coverage.expected_count, "coverage.expected_count"),
    included_count: count(coverage.included_count, "coverage.included_count"),
    omitted: coverage.omitted === undefined
      ? []
      : boundedArray(coverage.omitted, MAX_OMISSIONS, "coverage.omitted").map(decodeOmission),
    shown_count: optionalCount(coverage.shown_count, "coverage.shown_count"),
    available_count: optionalCount(coverage.available_count, "coverage.available_count"),
  };
}

function decodeUpstreamSummary(value: unknown, index: number): UpstreamSummary {
  const label = `upstream[${index}]`;
  const row = requireJsonObject(value, label);
  return {
    projection: identifier(row.projection, `${label}.projection`),
    observed_at: timestamp(row.observed_at, `${label}.observed_at`).text,
    complete: requireBoolean(row.complete, `${label}.complete`),
  };
}

function sealSource(fact: SourceFact, servedMillis: number): JsonObject {
  const staleness = fact.last_read_at === null
    ? null
    : Math.max(0, Math.floor((servedMillis - timestamp(fact.last_read_at, "last_read_at").millis) / 1000));
  let status: SourceStatus;
  if (fact.read_status === "read") {
    status = staleness !== null && staleness > fact.window_seconds ? "stale" : "fresh";
  } else {
    status = fact.read_status;
  }
  const reasons: string[] = [];
  if (status === "stale" || status === "unreadable") reasons.push(status);
  if ((status === "missing" || status === "not_read") && fact.required) reasons.push(status);
  if (fact.unreadable_count > 0 && status !== "unreadable") reasons.push("partially_unreadable");
  return {
    source_id: fact.source_id,
    ...(fact.via === null ? {} : { via: fact.via }),
    required: fact.required,
    read_status: fact.read_status,
    last_read_at: fact.last_read_at,
    source_updated_at: fact.source_updated_at,
    window_seconds: fact.window_seconds,
    staleness_seconds: staleness,
    status,
    item_count: fact.item_count,
    missing_count: fact.missing_count,
    unreadable_count: fact.unreadable_count,
    alert: reasons.length > 0,
    alert_reasons: reasons,
  };
}

function sealFacts(
  projection: string,
  observed: { text: string; millis: number },
  served: { text: string; millis: number },
  servedFromCache: boolean,
  facts: SourceFact[],
  coverage: CoverageFact,
  upstream: UpstreamSummary[],
): JsonObject {
  const seen = new Set<string>();
  for (const fact of facts) {
    const key = `${fact.via ?? ""}/${fact.source_id}`;
    if (seen.has(key)) throw new EffectRuntimeRequestError(`duplicate source ${key}`);
    seen.add(key);
  }
  const sources = facts.map((fact) => sealSource(fact, served.millis));
  const truncated = coverage.shown_count !== null && coverage.available_count !== null
    && coverage.available_count > coverage.shown_count;
  const complete = coverage.omitted.length === 0
    && (coverage.expected_count === null || coverage.included_count >= coverage.expected_count)
    && upstream.every((row) => row.complete);
  const reasons = new Set<string>();
  const alertSourceIds: string[] = [];
  for (const row of sources) {
    const rowReasons = row.alert_reasons as string[];
    if (rowReasons.length === 0) continue;
    alertSourceIds.push(typeof row.via === "string" ? `${row.via}/${row.source_id}` : String(row.source_id));
    for (const reason of rowReasons) {
      reasons.add(reason === "partially_unreadable" ? "unreadable_sources"
        : reason === "stale" ? "stale_sources"
        : reason === "unreadable" ? "unreadable_sources" : "missing_required_sources");
    }
  }
  if (!complete) reasons.add("incomplete_coverage");
  const fresh = !reasons.has("stale_sources") && !reasons.has("unreadable_sources")
    && !reasons.has("missing_required_sources");
  return {
    schema_version: PROJECTION_ENVELOPE_SCHEMA_VERSION,
    projection,
    observed_at: observed.text,
    served_at: served.text,
    age_seconds: Math.max(0, Math.floor((served.millis - observed.millis) / 1000)),
    served_from_cache: servedFromCache,
    fresh,
    complete,
    alert: reasons.size > 0,
    alert_reasons: [...reasons].sort(),
    alert_source_ids: alertSourceIds,
    sources,
    coverage: {
      scope: coverage.scope,
      expected_count: coverage.expected_count,
      included_count: coverage.included_count,
      omitted: coverage.omitted.map((row) => ({ ...row, refs: [...row.refs] })),
      shown_count: coverage.shown_count,
      available_count: coverage.available_count,
      truncated,
      complete,
    },
    upstream: upstream.map((row) => ({ ...row })),
  };
}

function decodeSealedEnvelope(value: unknown, label: string): {
  projection: string;
  observed: { text: string; millis: number };
  sources: SourceFact[];
  coverage: CoverageFact;
  upstream: UpstreamSummary[];
  complete: boolean;
} {
  const envelope = requireJsonObject(value, label);
  if (envelope.schema_version !== PROJECTION_ENVELOPE_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(`${label} schema mismatch`);
  }
  const sources = boundedArray(envelope.sources, MAX_SOURCES, `${label}.sources`).map(decodeSource);
  const coverage = decodeCoverage(envelope.coverage);
  const upstream = boundedArray(envelope.upstream ?? [], MAX_UPSTREAM, `${label}.upstream`)
    .map(decodeUpstreamSummary);
  const projection = identifier(envelope.projection, `${label}.projection`);
  const observed = timestamp(envelope.observed_at, `${label}.observed_at`);
  const complete = coverage.omitted.length === 0
    && (coverage.expected_count === null || coverage.included_count >= coverage.expected_count)
    && upstream.every((row) => row.complete);
  return { projection, observed, sources, coverage, upstream, complete };
}

/** Seal compact read facts, optionally composing upstream sealed envelopes. A
 * derived projection inherits every upstream source row, so its staleness is
 * bounded by the oldest read it depends on, never by its own assembly time. */
export function sealProjectionEnvelope(value: unknown): JsonObject {
  const request = requireJsonObject(value, "projection envelope request");
  if (request.schema_version === PROJECTION_ENVELOPE_SERVE_REQUEST) {
    const sealed = decodeSealedEnvelope(request.envelope, "envelope");
    return sealFacts(
      sealed.projection,
      sealed.observed,
      timestamp(request.served_at, "served_at"),
      true,
      sealed.sources,
      sealed.coverage,
      sealed.upstream,
    );
  }
  if (request.schema_version !== PROJECTION_ENVELOPE_SEAL_REQUEST) {
    throw new EffectRuntimeRequestError("projection envelope request schema mismatch");
  }
  const projection = identifier(request.projection, "projection");
  const observed = timestamp(request.observed_at, "observed_at");
  const served = request.served_at === undefined || request.served_at === null
    ? observed
    : timestamp(request.served_at, "served_at");
  const facts = boundedArray(request.sources, MAX_SOURCES, "sources").map(decodeSource);
  const upstream: UpstreamSummary[] = [];
  const upstreamValues = boundedArray(request.upstream ?? [], MAX_UPSTREAM, "upstream");
  upstreamValues.forEach((value, index) => {
    const sealed = decodeSealedEnvelope(value, `upstream[${index}]`);
    upstream.push({ projection: sealed.projection, observed_at: sealed.observed.text, complete: sealed.complete });
    for (const fact of sealed.sources) facts.push({ ...fact, via: fact.via ?? sealed.projection });
  });
  if (facts.length > MAX_SOURCES) throw new EffectRuntimeRequestError(`sources exceed ${MAX_SOURCES} entries`);
  return sealFacts(projection, observed, served, false, facts, decodeCoverage(request.coverage), upstream);
}
