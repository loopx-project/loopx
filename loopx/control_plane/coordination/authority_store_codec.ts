import { createHash } from "node:crypto";

import type { JsonObject } from "../effect_program.ts";
import type { AuthorityStoreCommit } from "./authority_store.ts";

/** Provider-neutral validation failure at the authority-store boundary. */
export class AuthorityStoreProtocolError extends Error {}

export function isAuthorityJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function authorityUnicodeCompare(left: string, right: string): number {
  const leftPoints = Array.from(left, (item) => item.codePointAt(0) ?? 0);
  const rightPoints = Array.from(right, (item) => item.codePointAt(0) ?? 0);
  const shared = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < shared; index += 1) {
    const difference = leftPoints[index] - rightPoints[index];
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}

export function hasExactAuthorityKeys(
  value: JsonObject,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value).sort(authorityUnicodeCompare);
  const expected = [...keys].sort(authorityUnicodeCompare);
  return actual.length === expected.length &&
    actual.every((key, index) => key === expected[index]);
}

export function requireAuthorityStoreId(value: unknown, name: string): string {
  if (typeof value !== "string" || value.trim() !== value || value.length === 0) {
    throw new AuthorityStoreProtocolError(`${name} must be a non-empty trimmed string`);
  }
  return value;
}

export function canonicalAuthorityJson(
  value: unknown,
  stack = new Set<object>(),
): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new AuthorityStoreProtocolError("JSON numbers must be finite");
    }
    return value;
  }
  if (Array.isArray(value)) {
    if (stack.has(value)) throw new AuthorityStoreProtocolError("JSON value must be acyclic");
    stack.add(value);
    try {
      return value.map((item) => canonicalAuthorityJson(item, stack));
    } finally {
      stack.delete(value);
    }
  }
  if (!isAuthorityJsonObject(value)) {
    throw new AuthorityStoreProtocolError("value must be strict JSON");
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    throw new AuthorityStoreProtocolError("JSON objects must be plain objects");
  }
  if (stack.has(value)) throw new AuthorityStoreProtocolError("JSON value must be acyclic");
  stack.add(value);
  try {
    return Object.fromEntries(
      Object.keys(value).sort(authorityUnicodeCompare).map((key) => [
        key,
        canonicalAuthorityJson(value[key], stack),
      ]),
    );
  } finally {
    stack.delete(value);
  }
}

export function canonicalAuthorityObject(value: unknown, name: string): JsonObject {
  if (!isAuthorityJsonObject(value)) {
    throw new AuthorityStoreProtocolError(`${name} must be an object`);
  }
  return canonicalAuthorityJson(value) as JsonObject;
}

export function canonicalAuthorityObjectList(
  value: unknown,
  name: string,
): JsonObject[] {
  if (!Array.isArray(value)) {
    throw new AuthorityStoreProtocolError(`${name} must be an array`);
  }
  return value.map((item, index) =>
    canonicalAuthorityObject(item, `${name}[${index}]`)
  );
}

export function canonicalAuthorityBytes(value: unknown): Buffer {
  return Buffer.from(JSON.stringify(canonicalAuthorityJson(value)), "utf8");
}

export function canonicalAuthoritySha256(value: unknown): string {
  return createHash("sha256").update(canonicalAuthorityBytes(value)).digest("hex");
}

export type CanonicalAuthorityDigest = (value: unknown) => string;

/**
 * Recompute the exact canonical v0 digest while reusing encoded long strings
 * within one verification window. Retained projections often share a large
 * unchanged value across many deltas; serializing it for every state and
 * commit proof dominates historical reads. The cache is bounded and never
 * survives the caller's window, so a later read still verifies fresh bytes.
 */
export function createCanonicalAuthorityDigestWindow(): CanonicalAuthorityDigest {
  const maxEntryBytes = 2 * 1024 * 1024;
  const maxCachedBytes = 4 * 1024 * 1024;
  const encoded = new Map<string, Buffer>();
  let cachedBytes = 0;
  const stringBytes = (value: string): string | Buffer => {
    if (value.length < 1024) return JSON.stringify(value);
    const previous = encoded.get(value);
    if (previous) return previous;
    const bytes = Buffer.from(JSON.stringify(value), "utf8");
    if (bytes.byteLength > maxEntryBytes) return bytes;
    while (cachedBytes + bytes.byteLength > maxCachedBytes) {
      const oldest = encoded.keys().next().value;
      if (oldest === undefined) break;
      cachedBytes -= encoded.get(oldest)!.byteLength;
      encoded.delete(oldest);
    }
    encoded.set(value, bytes);
    cachedBytes += bytes.byteLength;
    return bytes;
  };
  return (value: unknown): string => {
    const canonical = canonicalAuthorityJson(value);
    const hash = createHash("sha256");
    const visit = (part: unknown): void => {
      if (part === null) { hash.update("null"); return; }
      if (typeof part === "string") { hash.update(stringBytes(part)); return; }
      if (typeof part === "number" || typeof part === "boolean") {
        hash.update(JSON.stringify(part)); return;
      }
      if (Array.isArray(part)) {
        hash.update("[");
        for (let index = 0; index < part.length; index++) {
          if (index) hash.update(",");
          if (Object.hasOwn(part, index)) visit(part[index]);
          else hash.update("null"); // JSON.stringify represents sparse slots as null.
        }
        hash.update("]");
        return;
      }
      hash.update("{");
      let first = true;
      for (const key of Object.keys(part as JsonObject)) {
        if (!first) hash.update(",");
        first = false;
        hash.update(stringBytes(key));
        hash.update(":");
        visit((part as JsonObject)[key]);
      }
      hash.update("}");
    };
    visit(canonical);
    return hash.digest("hex");
  };
}

export function parseAuthorityCursor(value: string | null): bigint {
  if (value === null) return 0n;
  if (typeof value !== "string" || !/^[1-9]\d*$/.test(value)) {
    throw new AuthorityStoreProtocolError("provider cursor is invalid");
  }
  return BigInt(value);
}

export function normalizeAuthorityStoreCommit(
  commit: AuthorityStoreCommit,
): AuthorityStoreCommit {
  const expectedRevision = commit.expected_provider_revision;
  if (
    expectedRevision !== null &&
    (typeof expectedRevision !== "string" || expectedRevision.length === 0)
  ) {
    throw new AuthorityStoreProtocolError("expected provider revision is invalid");
  }
  return {
    expected_provider_revision: expectedRevision,
    operation_id: requireAuthorityStoreId(commit.operation_id, "operation id"),
    events: canonicalAuthorityObjectList(commit.events, "events"),
    next_projection: canonicalAuthorityObject(commit.next_projection, "projection"),
    receipts: canonicalAuthorityObjectList(commit.receipts, "receipts"),
  };
}
