/**
 * Bounded state log for one retained authority journal.
 *
 * Every provider must keep each committed transaction, its original receipts
 * and the exact projection that transaction published. Keeping a full
 * projection inside every retained transaction satisfies that contract but
 * makes retained bytes and recovery work grow with history instead of with
 * live state. This owner keeps the logical contract and removes the redundant
 * copies: one checkpoint per bounded window plus one exact delta per commit.
 *
 * Domain meaning stays outside this module. It only encodes how one canonical
 * projection became the next, so reconstruction is exact or fails closed; it
 * never decides Todo, lease, quota or promotion semantics.
 */
import type {JsonObject} from "../effect_program.ts";
import type {CanonicalAuthorityDigest} from "./authority_store_codec.ts";
import {
  AuthorityStoreProtocolError,
  authorityUnicodeCompare,
  canonicalAuthorityBytes,
  canonicalAuthorityJson,
  canonicalAuthorityObject,
  canonicalAuthoritySha256,
  isAuthorityJsonObject,
} from "./authority_store_codec.ts";

export const AUTHORITY_STATE_DELTA_SCHEMA = "authority_state_delta_v0";

/**
 * Commits between two checkpoints. A larger window retains fewer checkpoints
 * and reconstructs from more deltas; the declared bound is one checkpoint per
 * window plus this many bounded delta applications for any read or recovery.
 */
export const AUTHORITY_STATE_CHECKPOINT_INTERVAL = 64;

/**
 * Object path inside one projection. Arrays are addressed as a whole value;
 * element identity is owned by the domain, not by this codec.
 *
 * Segments are JSON object keys, and strict JSON allows any string as a key:
 * the empty string is a key, and so is `__proto__`. V1 retained whole
 * projections, so both are legal retained data and stay legal here.
 */
export type AuthorityStatePath = readonly string[];

export type AuthorityStateOperation =
  | {op: "set"; path: AuthorityStatePath; value: unknown}
  | {op: "remove"; path: AuthorityStatePath}
  | {op: "splice"; path: AuthorityStatePath; index: number; remove: number; insert: readonly unknown[]};

export interface AuthorityStateDelta {
  schema_version: typeof AUTHORITY_STATE_DELTA_SCHEMA;
  operations: readonly AuthorityStateOperation[];
}

function protocol(message: string): never {
  throw new AuthorityStoreProtocolError(message);
}

function canonicalBytesEqual(left: unknown, right: unknown): boolean {
  return canonicalAuthorityBytes(left).equals(canonicalAuthorityBytes(right));
}

/** Digest of one committed projection; the anchor every reconstruction checks. */
export function authorityStateDigest(
  projection: JsonObject, digest: CanonicalAuthorityDigest = canonicalAuthoritySha256,
): string {
  return digest(projection);
}

/** The one checkpoint cursor that covers a positive commit cursor. */
export function authorityStateCheckpointCursor(cursor: bigint): bigint {
  if (cursor < 1n) protocol("authority state checkpoint cursor must be positive");
  return ((cursor - 1n) / BigInt(AUTHORITY_STATE_CHECKPOINT_INTERVAL)) *
    BigInt(AUTHORITY_STATE_CHECKPOINT_INTERVAL) + 1n;
}

export function isAuthorityStateCheckpoint(cursor: bigint): boolean {
  return authorityStateCheckpointCursor(cursor) === cursor;
}

/** Largest number of deltas any reconstruction applies after a checkpoint. */
export function authorityStateReplayBudget(): number {
  return AUTHORITY_STATE_CHECKPOINT_INTERVAL - 1;
}

/**
 * Names the shortest object path from `previous` to `next`.
 *
 * Objects recurse per key so a single field edit stays one small operation.
 * Arrays compare their canonical elements and splice only the differing
 * middle, which keeps one changed record inside a large Todo array bounded
 * while remaining exact for insertions, removals and reordering.
 */
export function authorityStateDelta(previous: JsonObject, next: JsonObject): AuthorityStateDelta {
  const operations: AuthorityStateOperation[] = [];
  diffAuthorityState(previous, next, [], operations);
  return {schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations};
}

function diffAuthorityState(
  previous: unknown,
  next: unknown,
  path: AuthorityStatePath,
  operations: AuthorityStateOperation[],
): void {
  if (canonicalBytesEqual(previous, next)) return;
  if (isAuthorityJsonObject(previous) && isAuthorityJsonObject(next)) {
    const keys = [...new Set([...Object.keys(previous), ...Object.keys(next)])]
      .sort(authorityUnicodeCompare);
    for (const key of keys) {
      const childPath = [...path, key];
      if (!Object.hasOwn(next, key)) {
        operations.push({op: "remove", path: childPath});
      } else if (!Object.hasOwn(previous, key)) {
        operations.push({op: "set", path: childPath, value: next[key]});
      } else {
        diffAuthorityState(previous[key], next[key], childPath, operations);
      }
    }
    return;
  }
  if (Array.isArray(previous) && Array.isArray(next)) {
    operations.push(...arraySpliceOperations(previous, next, path));
    return;
  }
  operations.push({op: "set", path, value: next});
}

function arraySpliceOperations(
  previous: readonly unknown[],
  next: readonly unknown[],
  path: AuthorityStatePath,
): AuthorityStateOperation[] {
  const previousBytes = previous.map(item => canonicalAuthorityBytes(item));
  const nextBytes = next.map(item => canonicalAuthorityBytes(item));
  const shared = Math.min(previousBytes.length, nextBytes.length);
  let prefix = 0;
  while (prefix < shared && previousBytes[prefix]!.equals(nextBytes[prefix]!)) prefix += 1;
  let suffix = 0;
  while (suffix < shared - prefix &&
    previousBytes[previousBytes.length - 1 - suffix]!.equals(nextBytes[nextBytes.length - 1 - suffix]!)) {
    suffix += 1;
  }
  const removed = previousBytes.length - prefix - suffix;
  const inserted = next.slice(prefix, nextBytes.length - suffix);
  if (removed === 0 && inserted.length === 0) return [];
  return [{op: "splice", path, index: prefix, remove: removed, insert: inserted}];
}

/**
 * Reconstruct one committed projection.
 *
 * Reconstructed state is canonicalized before it is returned, so callers can
 * compare bytes and digests without depending on the storage encoding. A path
 * that no longer exists, an out-of-range splice or a non-JSON payload fails
 * closed instead of producing a partially applied state.
 */
export function applyAuthorityStateDelta(
  previous: JsonObject,
  delta: AuthorityStateDelta,
): JsonObject {
  const decoded = decodeAuthorityStateDelta(delta);
  const result = structuredClone(previous);
  for (const operation of decoded.operations) applyAuthorityStateOperation(result, operation);
  return canonicalAuthorityObject(result, "reconstructed authority state");
}

/**
 * Does this delta rebuild exactly this projection from `previous`?
 *
 * The live SQLite writer and the V1 migration both have to prove that the delta
 * they are about to persist really reconstructs the projection they are
 * publishing, so the rule has one owner instead of one inline comparison per
 * caller: the caller only ever needs to know whether the log it is writing is
 * readable by its own read path. A delta that cannot be decoded, or that does
 * not apply to `previous`, is a failed reconstruction rather than a different
 * outcome, because a failed proof is what makes those callers fail closed.
 */
export function authorityStateDeltaReconstructs(
  previous: JsonObject,
  delta: AuthorityStateDelta,
  projection: JsonObject,
): boolean {
  try {
    return canonicalBytesEqual(applyAuthorityStateDelta(previous, delta), projection);
  } catch {
    return false;
  }
}

function applyAuthorityStateOperation(root: JsonObject, operation: AuthorityStateOperation): void {
  const segments = operation.path;
  if (segments.length === 0) protocol("authority state delta cannot target the root state");
  let container: unknown = root;
  for (const segment of segments.slice(0, -1)) container = descend(container, segment);
  const last = segments[segments.length - 1]!;
  if (!isAuthorityJsonObject(container)) protocol("authority state delta path leaves the previous state");
  const present = Object.hasOwn(container, last);
  if (operation.op === "splice") {
    if (!present) protocol("authority state delta path leaves the previous state");
    const target = container[last];
    if (!Array.isArray(target)) protocol("authority state delta spliced a value that is not an array");
    if (operation.index < 0 || operation.remove < 0 ||
      operation.index + operation.remove > target.length) {
      protocol("authority state delta splice is out of range");
    }
    target.splice(operation.index, operation.remove,
      ...operation.insert.map(item => structuredClone(item)));
    return;
  }
  if (operation.op === "remove") {
    if (!present) protocol("authority state delta removed a value that was never stored");
    delete container[last];
  }
  else setOwnJsonKey(container, last, structuredClone(operation.value));
}

/**
 * Write one decoded key as an own data property.
 *
 * `container[key] = value` would run the inherited `__proto__` accessor and
 * replace the reconstructed object's prototype with the stored value, so a
 * retained projection carrying that key would silently lose it. Decoded
 * deltas are data, so every key is created the same way the canonicalizer
 * creates keys, which keeps reconstruction exact for every JSON object key.
 */
function setOwnJsonKey(container: JsonObject, key: string, value: unknown): void {
  Object.defineProperty(container, key, {
    value, writable: true, enumerable: true, configurable: true,
  });
}

function descend(container: unknown, segment: string): unknown {
  if (!isAuthorityJsonObject(container) || !Object.hasOwn(container, segment)) {
    protocol("authority state delta path leaves the previous state");
  }
  return container[segment];
}

/** Boundary decoder: stored or transported deltas enter as `unknown`. */
export function decodeAuthorityStateDelta(value: unknown): AuthorityStateDelta {
  if (!isAuthorityJsonObject(value) ||
    value.schema_version !== AUTHORITY_STATE_DELTA_SCHEMA ||
    !Array.isArray(value.operations)) {
    protocol("authority state delta schema mismatch");
  }
  requireExactKeys(value, ["schema_version", "operations"], "authority state delta");
  return {schema_version: AUTHORITY_STATE_DELTA_SCHEMA,
    operations: value.operations.map((operation, index) =>
      decodeAuthorityStateOperation(operation, index))};
}

function decodeAuthorityStateOperation(value: unknown, index: number): AuthorityStateOperation {
  const label = `authority state delta operation ${index}`;
  if (!isAuthorityJsonObject(value) || typeof value.op !== "string") protocol(`${label} is invalid`);
  const path = decodeAuthorityStatePath(value.path, label);
  if (value.op === "set") {
    requireExactKeys(value, ["op", "path", "value"], label);
    return {op: "set", path, value: canonicalAuthorityJson(value.value)};
  }
  if (value.op === "remove") {
    requireExactKeys(value, ["op", "path"], label);
    return {op: "remove", path};
  }
  if (value.op === "splice") {
    requireExactKeys(value, ["op", "path", "index", "remove", "insert"], label);
    if (!Number.isSafeInteger(value.index) || !Number.isSafeInteger(value.remove) ||
      (value.index as number) < 0 || (value.remove as number) < 0 || !Array.isArray(value.insert)) {
      protocol(`${label} splice bounds are invalid`);
    }
    return {op: "splice", path, index: value.index as number, remove: value.remove as number,
      insert: value.insert.map(item => canonicalAuthorityJson(item))};
  }
  return protocol(`${label} op is unsupported`);
}

function decodeAuthorityStatePath(value: unknown, label: string): AuthorityStatePath {
  if (!Array.isArray(value) || value.length === 0) protocol(`${label} path is invalid`);
  return value.map(segment => {
    // Any string is a legal JSON key, including the empty string.
    if (typeof segment === "string") return segment;
    return protocol(`${label} path segment is invalid`);
  });
}

function requireExactKeys(value: JsonObject, keys: readonly string[], label: string): void {
  const actual = Object.keys(value).sort(authorityUnicodeCompare);
  const expected = [...keys].sort(authorityUnicodeCompare);
  if (actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])) {
    protocol(`${label} has unexpected fields`);
  }
}
