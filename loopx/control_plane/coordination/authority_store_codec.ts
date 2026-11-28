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

export function optionalAuthorityString(value: unknown, label: string): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string") {
    throw new AuthorityStoreProtocolError(`${label} must be a string or null`);
  }
  return value;
}

export function optionalAuthoritySafeInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined) return null;
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw new AuthorityStoreProtocolError(`${label} must be a non-negative safe integer or null`);
  }
  return Number(value);
}

export function requireAuthorityBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new AuthorityStoreProtocolError(`${label} must be a boolean`);
  }
  return value;
}

export function optionalAuthorityBoolean(value: unknown, label: string): boolean | null {
  if (value === null || value === undefined) return null;
  return requireAuthorityBoolean(value, label);
}

export function requireAuthorityDate(value: unknown, label: string): Date {
  if (!(value instanceof Date) || Number.isNaN(value.valueOf())) {
    throw new AuthorityStoreProtocolError(`${label} must be a valid Date`);
  }
  return value;
}

export function requireAuthorityLiteral<T extends string>(
  value: unknown,
  values: readonly T[],
  label: string,
): T {
  if (typeof value !== "string" || !values.includes(value as T)) {
    throw new AuthorityStoreProtocolError(`${label} must be one of: ${values.join(", ")}`);
  }
  return value as T;
}

export function uniqueAuthorityStoreIds(value: readonly string[], label: string): string[] {
  if (!Array.isArray(value)) {
    throw new AuthorityStoreProtocolError(`${label} must be an array`);
  }
  const values = value.map((item, index) => requireAuthorityStoreId(item, `${label}[${index}]`));
  if (new Set(values).size !== values.length) {
    throw new AuthorityStoreProtocolError(`${label} must contain unique ids`);
  }
  return values;
}

export function canonicalAuthorityJson(
  value: unknown,
  stack = new Set<object>(),
): unknown {
  return cloneAuthorityJson(value, stack, true);
}

/** Own JSON containers without copying immutable primitives or reordering keys. */
export function copyAuthorityJson(value: unknown): unknown {
  return cloneAuthorityJson(value, new Set<object>(), false);
}

function cloneAuthorityJson(value: unknown, stack: Set<object>, canonicalKeys: boolean): unknown {
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
      if (canonicalKeys) return value.map((item) => cloneAuthorityJson(item, stack, true));
      const copy: unknown[] = Array(value.length);
      for (const key of Object.keys(value)) Object.defineProperty(copy, key, {
        value: cloneAuthorityJson(Reflect.get(value, key), stack, false),
        writable: true, enumerable: true, configurable: true,
      });
      return copy;
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
    const keys = Object.keys(value);
    if (!canonicalKeys) {
      // Fill without inherited setters (including __proto__), then normalize
      // to the same ordinary object as Object.fromEntries. No per-field tuples.
      const copy: JsonObject = Object.create(null);
      for (const key of keys) copy[key] = cloneAuthorityJson(value[key], stack, false);
      return Object.setPrototypeOf(copy, Object.prototype);
    }
    keys.sort(authorityUnicodeCompare);
    const copy: JsonObject = Object.create(null);
    for (const key of keys) copy[key] = cloneAuthorityJson(value[key], stack, true);
    return Object.setPrototypeOf(copy, Object.prototype);
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
  const objects: JsonObject[] = [];
  // Validate every persisted slot: map skips holes that JSON encodes as null.
  for (let index = 0; index < value.length; index++) {
    objects.push(canonicalAuthorityObject(value[index], `${name}[${index}]`));
  }
  return objects;
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
