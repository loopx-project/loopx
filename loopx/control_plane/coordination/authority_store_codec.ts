import { createHash } from "node:crypto";

import type { JsonObject } from "../effect_program.ts";
import type { AuthorityStoreCommit } from "./authority_store.ts";

/** Provider-neutral validation failure at the authority-store boundary. */
export class AuthorityStoreProtocolError extends Error {}

export function isAuthorityJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function authorityUnicodeCompare(left: string, right: string): number {
  // Walk code points without allocating two arrays for every sort comparison.
  // JS's default sort compares UTF-16 units, which would change persisted
  // revisions for supplementary characters relative to BMP characters.
  let leftIndex = 0, rightIndex = 0;
  while (leftIndex < left.length && rightIndex < right.length) {
    const leftPoint = left.codePointAt(leftIndex)!;
    const rightPoint = right.codePointAt(rightIndex)!;
    if (leftPoint !== rightPoint) return leftPoint - rightPoint;
    leftIndex += leftPoint > 0xffff ? 2 : 1;
    rightIndex += rightPoint > 0xffff ? 2 : 1;
  }
  return leftIndex < left.length ? 1 : rightIndex < right.length ? -1 : 0;
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
