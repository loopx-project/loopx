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
import {createHash, type Hash} from "node:crypto";
import type {AuthorityStoreCommit} from "./authority_store.ts";
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
export function authorityStateDigest(projection: JsonObject): string {
  return canonicalAuthoritySha256(projection);
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
  const replay = new AuthorityStateReplay(previous);
  replay.apply(delta);
  return replay.snapshot();
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

/**
 * One read/audit's privately owned canonical state. Changed paths are copied;
 * unchanged subtrees keep their identity and exact JSON encoding. Inputs and
 * returned snapshots never share objects with this owner. Weak keys retain no
 * historical roots; the long-string cache is separately capped at 4 MiB.
 * No cached proof crosses a provider read or bypasses a row's digest check.
 */
export class AuthorityStateReplay {
  #state: JsonObject;
  #byteObjects = new WeakMap<object, Buffer>();
  #encoded = new WeakMap<object, string>();
  #strings = new Map<string, Buffer>();
  #stringBytes = 0;

  constructor(projection: unknown) {
    this.#state = canonicalAuthorityObject(projection, "authority replay state");
  }

  apply(delta: AuthorityStateDelta): void {
    let next = this.#state;
    // Decode before applying and publish only after the whole batch succeeds.
    // A rejected suffix cannot leave a half-applied replay frontier.
    for (const operation of decodeAuthorityStateDelta(delta).operations) {
      next = applyAuthorityStateOperation(next, operation, 0);
    }
    this.#state = next;
  }

  snapshot(): JsonObject { return structuredClone(this.#state); }

  /** Immutable canonical text; storage readers may parse it into independent rows. */
  canonicalJson(): string { return this.#encode(this.#state); }

  stateDigest(): string {
    return this.#updateProjection(createHash("sha256")).digest("hex");
  }

  commitDigest(fields: Omit<AuthorityStoreCommit, "next_projection">): string {
    // Canonicalize the ordinary envelope, inserting our owned projection's
    // exact bytes at its key. This is the existing v0 digest, not a Merkle hash
    // or a new persistent proof. Integer-like keys obey native JSON ordering.
    const envelope = canonicalAuthorityObject({...fields, next_projection: null}, "commit proof");
    const keys = Object.keys(envelope), split = keys.indexOf("next_projection");
    const field = (key: string) => JSON.stringify(key) + ":" + JSON.stringify(envelope[key]);
    const before = keys.slice(0, split).map(field), after = keys.slice(split + 1).map(field);
    const hash = createHash("sha256").update("{" + (before.length ? before.join(",") + "," : "") + '"next_projection":');
    return this.#updateProjection(hash).update((after.length ? "," + after.join(",") : "") + "}").digest("hex");
  }

  #updateProjection(hash: Hash): Hash {
    hash.update("{");
    let first = true;
    for (const key of Object.keys(this.#state)) {
      hash.update((first ? "" : ",") + JSON.stringify(key) + ":"); first = false;
      const value = this.#state[key];
      if (value !== null && typeof value === "object") {
        let bytes = this.#byteObjects.get(value);
        if (!bytes) { bytes = Buffer.from(this.#encode(value), "utf8"); this.#byteObjects.set(value, bytes); }
        hash.update(bytes);
      } else if (typeof value === "string" && value.length >= 1024) hash.update(this.#longString(value));
      else hash.update(JSON.stringify(value));
    }
    return hash.update("}");
  }

  #longString(value: string): Buffer {
    const known = this.#strings.get(value);
    if (known) return known;
    const bytes = Buffer.from(JSON.stringify(value), "utf8");
    if (bytes.byteLength > 2 * 1024 ** 2) return bytes;
    while (this.#stringBytes + bytes.byteLength > 4 * 1024 ** 2) {
      const oldest = this.#strings.keys().next().value!;
      this.#stringBytes -= this.#strings.get(oldest)!.byteLength;
      this.#strings.delete(oldest);
    }
    this.#strings.set(value, bytes); this.#stringBytes += bytes.byteLength;
    return bytes;
  }

  #encode(value: unknown): string {
    if (value === null || typeof value !== "object") {
      return typeof value === "string" && value.length >= 1024
        ? this.#longString(value).toString("utf8") : JSON.stringify(value);
    }
    const known = this.#encoded.get(value);
    if (known !== undefined) return known;
    const encoded = Array.isArray(value)
      ? "[" + Array.from({length: value.length}, (_, index) =>
        Object.hasOwn(value, index) ? this.#encode(value[index]) : "null").join(",") + "]"
      : "{" + Object.keys(value).map(key => JSON.stringify(key) + ":" +
        this.#encode((value as JsonObject)[key])).join(",") + "}";
    this.#encoded.set(value, encoded);
    return encoded;
  }
}

/** Copy only the modified object path. Canonical children remain private and immutable. */
function applyAuthorityStateOperation(
  container: JsonObject, operation: AuthorityStateOperation, depth: number,
): JsonObject {
  if (!isAuthorityJsonObject(container)) protocol("authority state delta path leaves the previous state");
  const key = operation.path[depth]!;
  const next = {...container};
  const set = (value: unknown): void => {
    // Treat __proto__ as data, including keys newly introduced by a delta.
    Object.defineProperty(next, key, {value, writable: true, enumerable: true, configurable: true});
  };
  const present = Object.hasOwn(container, key);
  if (depth + 1 < operation.path.length) {
    if (!present) protocol("authority state delta path leaves the previous state");
    set(applyAuthorityStateOperation(container[key] as JsonObject, operation, depth + 1));
  } else if (operation.op === "splice") {
    if (!present) protocol("authority state delta path leaves the previous state");
    const target = container[key];
    if (!Array.isArray(target)) protocol("authority state delta spliced a value that is not an array");
    if (operation.index + operation.remove > target.length) protocol("authority state delta splice is out of range");
    set(target.slice(0, operation.index).concat(operation.insert, target.slice(operation.index + operation.remove)));
  } else if (operation.op === "remove") {
    if (!present) protocol("authority state delta removed a value that was never stored");
    delete next[key];
  } else set(operation.value);
  // Only this changed container needs reordering, not every nested Todo.
  return Object.fromEntries(Object.keys(next).sort(authorityUnicodeCompare).map(name => [name, next[name]]));
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
    operations: Array.from(value.operations, (operation, index) =>
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
      insert: Array.from(value.insert, item => canonicalAuthorityJson(item))};
  }
  return protocol(`${label} op is unsupported`);
}

function decodeAuthorityStatePath(value: unknown, label: string): AuthorityStatePath {
  if (!Array.isArray(value) || value.length === 0) protocol(`${label} path is invalid`);
  return Array.from(value, segment => {
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
