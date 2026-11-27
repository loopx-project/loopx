/** Disposable parsing acceleration; the bytes on disk remain the authority. */
import { createHash } from "node:crypto";
import type { BigIntStats } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";
import { setImmediate } from "node:timers/promises";

import type { JsonObject } from "../effect_program.ts";
import { jsonObject } from "../runtime_decode.ts";
import { jsonLineMembers } from "./json_line_members.ts";

/** What one line that failed to parse still carries of the named members. */
export interface MalformedLineMembers {
  readonly line: number;
  readonly members: readonly (readonly [string, unknown])[];
  /** Named members the line contained but could not read intact. */
  readonly unreadable: readonly string[];
  /** A key's name met an undecodable byte; the member set is incomplete. */
  readonly lostKeyName: boolean;
  /** A separator or container boundary was unknowable; later members are guessed. */
  readonly ambiguous: boolean;
}

export interface ReceiptLogSnapshot {
  readonly records: readonly JsonObject[];
  /** Physical 1-based line number of each record, in `records` order. */
  readonly recordLines: readonly number[];
  /**
   * Intact members of lines that were not records, restricted to the members a
   * caller named.  A damaged display field must not erase the identity members
   * the row's writer finished elsewhere in the same row; a row whose own writer
   * never terminated it supplies no members at all.  Callers still decide which
   * identities they trust.
   */
  readonly malformedLines: readonly MalformedLineMembers[];
  readonly firstErrorLine: number | null;
  /**
   * Physical line of an unterminated final row that still decoded, or null.
   *
   * The row never received the newline its writer must append to commit it,
   * and the next append drops those bytes.  Deduplication and freshness may
   * read the record for diagnosis, but only a newline-terminated row is a
   * committed receipt.
   */
  readonly unterminatedRecordLine: number | null;
}

interface ParsedPrefix extends ReceiptLogSnapshot {
  readonly bytes: number;
  readonly lines: number;
}

interface Prefix extends ParsedPrefix {
  readonly digest: string;
}

// Bound retained source volume and the number of histories. This is not a
// promise about JS heap bytes: decoded objects can exceed their JSON size.
export const DEFAULT_MAX_RETAINED_BYTES = 256 * 1024 * 1024;
const MAX_RETAINED_LOGS = 4;
const prefixes = new Map<string, Prefix>();
let retainedBytes = 0;
const decoding = new Map<string, { bytes: Buffer; result: Promise<ReceiptLogSnapshot> }>();

export interface ReadReceiptLogSnapshotOptions {
  readonly maxRetainedBytes?: number;
}

function resolveMaxRetainedBytes(options?: ReadReceiptLogSnapshotOptions): number {
  const value = options?.maxRetainedBytes;
  if (value === undefined) return DEFAULT_MAX_RETAINED_BYTES;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) {
    throw new Error("maxRetainedBytes must be a positive safe integer");
  }
  return value;
}

function digest(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function forget(key: string): void {
  const previous = prefixes.get(key);
  if (previous) retainedBytes -= previous.bytes;
  prefixes.delete(key);
}

function remember(
  key: string,
  prefix: Prefix,
  maxRetainedBytes: number = DEFAULT_MAX_RETAINED_BYTES,
): void {
  forget(key);
  if (prefix.bytes > maxRetainedBytes) return;
  while (prefixes.size >= MAX_RETAINED_LOGS ||
    retainedBytes + prefix.bytes > maxRetainedBytes) {
    forget(prefixes.keys().next().value!);
  }
  prefixes.set(key, prefix);
  retainedBytes += prefix.bytes;
}

/** `readonly` is erased; freeze nested values so a caller cannot poison reuse. */
function freezeMalformed(line: MalformedLineMembers): MalformedLineMembers {
  for (const [, value] of line.members) {
    if (value !== null && typeof value === "object") freezeRecord(value as JsonObject);
  }
  return Object.freeze({
    line: line.line,
    members: Object.freeze(line.members.map(([key, value]) => (
      Object.freeze([key, value] as const)
    ))),
    unreadable: Object.freeze([...line.unreadable]),
    lostKeyName: line.lostKeyName,
    ambiguous: line.ambiguous,
  });
}

function freezeRecord(record: JsonObject): JsonObject {
  const pending: object[] = [record];
  while (pending.length > 0) {
    const value = pending.pop()!;
    for (const child of Object.values(value)) {
      if (child !== null && typeof child === "object") pending.push(child);
    }
    Object.freeze(value);
  }
  return record;
}

// One strict decoder for the whole read: replacement characters must never
// become a different, valid-looking receipt identity. `ignoreBOM` matches
// Buffer#toString, so a leading BOM still fails JSON parsing as before.
const STRICT_UTF8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
function strictUtf8(bytes: Uint8Array): string | null {
  try {
    return STRICT_UTF8.decode(bytes);
  } catch {
    return null;
  }
}

function decodeLine(line: string, schemaVersion?: string): JsonObject {
  const record = jsonObject(JSON.parse(line));
  if (record === null || (schemaVersion !== undefined &&
    record.schema_version !== schemaVersion)) {
    throw new Error("receipt log row is malformed");
  }
  return freezeRecord(record);
}

async function parseThrough(
  bytes: Buffer, completeBytes: number, reusable: ParsedPrefix | null,
  schemaVersion: string | undefined, recoveryKeys: readonly string[],
): Promise<ParsedPrefix> {
  const records = reusable ? [...reusable.records] : [];
  const recordLines = reusable ? [...reusable.recordLines] : [];
  const malformedLines = reusable ? [...reusable.malformedLines] : [];
  let firstErrorLine = reusable?.firstErrorLine ?? null;
  let lines = reusable?.lines ?? 0;
  let offset = reusable?.bytes ?? 0;
  let batchStart = offset;
  // The append owner drops uncommitted torn bytes before it writes its own
  // row, so this prefix can only end on a finished row. Any rewrite (including
  // that drop) still fails the digest check and lands here as a cold read.
  while (offset < completeBytes) {
    const end = bytes.indexOf(10, offset);
    const raw = bytes.subarray(offset, end);
    const line = strictUtf8(raw);
    lines += 1;
    let record: JsonObject | null = null;
    if (line === null) {
      firstErrorLine ??= lines;
      const recovered = jsonLineMembers(raw, recoveryKeys);
      if (recovered.members.length > 0 || recovered.unreadable.length > 0 ||
        recovered.ambiguous || recovered.lostKeyName) {
        malformedLines.push(freezeMalformed({ line: lines, ...recovered }));
      }
    } else if (line.trim()) {
      try {
        record = decodeLine(line, schemaVersion);
      } catch {
        firstErrorLine ??= lines;
        const recovered = jsonLineMembers(raw, recoveryKeys);
        if (recovered.members.length > 0 || recovered.unreadable.length > 0 ||
          recovered.ambiguous || recovered.lostKeyName) {
          malformedLines.push(freezeMalformed({ line: lines, ...recovered }));
        }
      }
    }
    if (record !== null) {
      records.push(record);
      recordLines.push(lines);
    }
    offset = end + 1;
    // Cold/rebuilt histories must let the shared server service other sockets.
    if (offset - batchStart >= 1024 * 1024) {
      await setImmediate();
      batchStart = offset;
    }
  }
  return Object.freeze({
    records: Object.freeze(records),
    recordLines: Object.freeze(recordLines),
    malformedLines: Object.freeze(malformedLines),
    firstErrorLine, lines, unterminatedRecordLine: null,
    bytes: completeBytes,
  });
}

export type ReceiptLogStat = {
  readonly exists: boolean;
  readonly dev: string | null;
  readonly ino: string | null;
  readonly size: number;
  readonly mtime_ns: string | null;
  readonly ctime_ns: string | null;
  readonly mtime_ms: string | null;
};

export interface ReceiptLogSnapshotWithStat {
  readonly snapshot: ReceiptLogSnapshot | null;
  readonly stat: ReceiptLogStat;
  readonly stable: boolean;
}

export const MISSING_RECEIPT_LOG_STAT: ReceiptLogStat = Object.freeze({
  exists: false,
  dev: null,
  ino: null,
  size: 0,
  mtime_ns: null,
  ctime_ns: null,
  mtime_ms: null,
});

function formatReceiptLogStat(info: BigIntStats): ReceiptLogStat {
  if (info.size < 0n || info.size > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error("receipt log size exceeds safe integer range");
  }
  return Object.freeze({
    exists: true,
    dev: String(info.dev),
    ino: String(info.ino),
    size: Number(info.size),
    mtime_ns: String(info.mtimeNs),
    ctime_ns: String(info.ctimeNs),
    mtime_ms: String(info.mtimeMs),
  });
}

function bigIntStatsMatch(left: BigIntStats, right: BigIntStats): boolean {
  return (
    left.dev === right.dev &&
    left.ino === right.ino &&
    left.size === right.size &&
    left.mtimeNs === right.mtimeNs &&
    left.ctimeNs === right.ctimeNs
  );
}

async function safePathStat(path: string): Promise<BigIntStats | null> {
  try {
    return await stat(path, { bigint: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException)?.code === "ENOENT") return null;
    throw error;
  }
}

export function isReceiptLogStat(value: unknown): value is ReceiptLogStat {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const raw = value as Record<string, unknown>;
  if (typeof raw.exists !== "boolean") return false;
  if (typeof raw.size !== "number" || !Number.isSafeInteger(raw.size) || raw.size < 0) return false;
  const optionalString = (item: unknown) => item === null || typeof item === "string";
  return (
    optionalString(raw.dev) &&
    optionalString(raw.ino) &&
    optionalString(raw.mtime_ns) &&
    optionalString(raw.ctime_ns) &&
    optionalString(raw.mtime_ms)
  );
}

export function receiptLogStatsEqual(left: ReceiptLogStat, right: ReceiptLogStat): boolean {
  return (
    left.exists === right.exists &&
    left.dev === right.dev &&
    left.ino === right.ino &&
    left.size === right.size &&
    left.mtime_ns === right.mtime_ns &&
    left.ctime_ns === right.ctime_ns &&
    left.mtime_ms === right.mtime_ms
  );
}

export async function statReceiptLog(path: string): Promise<ReceiptLogStat> {
  const info = await safePathStat(path);
  return info === null ? MISSING_RECEIPT_LOG_STAT : formatReceiptLogStat(info);
}

/**
 * Read fresh bytes on EVERY invocation and bind the returned stat strictly to
 * the exact open file descriptor snapshot consumed during parsing.
 * Only reuse a parsed newline-terminated prefix after hashing those exact bytes.
 * Rewrites, rotation, truncation and same-size edits all remain observable,
 * including edits to an old identity-conflicting receipt.
 * Never cache an unterminated tail: an append can finish its UTF-8 character or
 * JSON token. The tail still participates in this read, including strict errors.
 * Cache loss/eviction/restart affects cost only, never the returned facts.
 */
export async function readReceiptLogSnapshotWithStat(
  path: string,
  schemaVersion?: string,
  recoveryKeys: readonly string[] = [],
  options?: ReadReceiptLogSnapshotOptions,
): Promise<ReceiptLogSnapshotWithStat> {
  const maxRetainedBytes = resolveMaxRetainedBytes(options);
  const key = JSON.stringify([resolve(path), schemaVersion ?? null, recoveryKeys]);
  const pre = await safePathStat(path);
  let bytes: Buffer;
  try {
    bytes = await readFile(path);
  } catch (error) {
    forget(key);
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      const postMissing = await safePathStat(path);
      return {
        snapshot: null,
        stat: MISSING_RECEIPT_LOG_STAT,
        stable: pre === null && postMissing === null,
      };
    }
    throw error;
  }
  const post = await safePathStat(path);
  const stable =
    pre !== null &&
    post !== null &&
    bigIntStatsMatch(pre, post) &&
    BigInt(bytes.length) === pre.size;
  const consumedStat = stable ? formatReceiptLogStat(pre) : MISSING_RECEIPT_LOG_STAT;
  const snapshot = await decodeReceiptLogBytes(
    bytes,
    key,
    schemaVersion,
    recoveryKeys,
    stable,
    maxRetainedBytes,
  );
  return { snapshot, stat: consumedStat, stable };
}

async function decodeReceiptLogBytes(
  bytes: Buffer,
  key: string,
  schemaVersion: string | undefined,
  recoveryKeys: readonly string[],
  stable: boolean,
  maxRetainedBytes: number = DEFAULT_MAX_RETAINED_BYTES,
): Promise<ReceiptLogSnapshot> {
  // Each caller owns a fresh read. Only coalesce large, byte-identical reads
  // with the same decode contract while the original parse is still running.
  if (!stable || bytes.length < 1024 * 1024 || bytes.length > maxRetainedBytes) {
    return parseSnapshot(bytes, key, schemaVersion, recoveryKeys, stable, maxRetainedBytes);
  }
  const active = decoding.get(key);
  if (active && bytes.equals(active.bytes)) return active.result;
  const result = parseSnapshot(bytes, key, schemaVersion, recoveryKeys, true, maxRetainedBytes);
  if (active || decoding.size >= MAX_RETAINED_LOGS) return result;
  decoding.set(key, { bytes, result });
  try {
    return await result;
  } finally {
    decoding.delete(key);
  }
}

export async function readReceiptLogSnapshot(
  path: string,
  schemaVersion?: string,
  recoveryKeys: readonly string[] = [],
  options?: ReadReceiptLogSnapshotOptions,
): Promise<ReceiptLogSnapshot | null> {
  const maxRetainedBytes = resolveMaxRetainedBytes(options);
  const key = JSON.stringify([resolve(path), schemaVersion ?? null, recoveryKeys]);
  let bytes: Buffer;
  try {
    bytes = await readFile(path);
  } catch (error) {
    forget(key);
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
  return decodeReceiptLogBytes(bytes, key, schemaVersion, recoveryKeys, true, maxRetainedBytes);
}

async function parseSnapshot(
  bytes: Buffer,
  key: string,
  schemaVersion: string | undefined,
  recoveryKeys: readonly string[],
  rememberPrefix = true,
  maxRetainedBytes: number = DEFAULT_MAX_RETAINED_BYTES,
): Promise<ReceiptLogSnapshot> {
  const completeBytes = bytes.lastIndexOf(10) + 1;
  const prior = prefixes.get(key);
  // The rollout log and run index are usually read together. When whole
  // histories compete for the retained-byte budget, shrink the newer peer to
  // the remaining headroom when nontrivial instead of evicting the other log
  // into alternating full/tail reparse thrashing.
  const otherBytes = retainedBytes - (prior?.bytes ?? 0);
  const peerShare = Math.floor(maxRetainedBytes / 2);
  const availableForPeer = maxRetainedBytes - otherBytes;
  const minPeerHeadroom = Math.floor(maxRetainedBytes / 4);
  const limit = otherBytes + completeBytes <= maxRetainedBytes
    ? completeBytes
    : availableForPeer >= minPeerHeadroom && (prior === undefined || availableForPeer >= prior.bytes)
      ? Math.min(completeBytes, availableForPeer)
      : Math.max(peerShare, prior?.bytes ?? 0);
  const retainedEnd = limit >= completeBytes
    ? completeBytes
    : limit <= 0
      ? 0
      : bytes.lastIndexOf(10, limit - 1) + 1;
  let reusable: Prefix | null = null;
  let retainedDigest: string | undefined;
  if (prior !== undefined && prior.bytes <= retainedEnd) {
    const hash = createHash("sha256").update(bytes.subarray(0, prior.bytes));
    const hasSuffix = prior.bytes < retainedEnd;
    const prefixDigest = (hasSuffix ? hash.copy() : hash).digest("hex");
    if (prefixDigest === prior.digest) reusable = prior;
    retainedDigest = hasSuffix
      ? hash.update(bytes.subarray(prior.bytes, retainedEnd)).digest("hex")
      : prefixDigest;
  }
  const retained = reusable !== null && reusable.bytes === retainedEnd ? reusable : Object.freeze({
    ...await parseThrough(bytes, retainedEnd, reusable, schemaVersion, recoveryKeys),
    digest: retainedDigest ?? digest(bytes.subarray(0, retainedEnd)),
  });
  if (rememberPrefix) remember(key, retained, maxRetainedBytes);
  const prefix = retainedEnd === completeBytes ? retained
    : await parseThrough(bytes, completeBytes, retained, schemaVersion, recoveryKeys);
  const tail = strictUtf8(bytes.subarray(completeBytes));
  if (tail === null) {
    return Object.freeze({
      records: prefix.records,
      recordLines: prefix.recordLines,
      malformedLines: prefix.malformedLines,
      firstErrorLine: prefix.firstErrorLine ?? prefix.lines + 1,
      unterminatedRecordLine: null,
    });
  }
  if (!tail.trim()) return prefix;
  try {
    return Object.freeze({
      records: Object.freeze([...prefix.records, decodeLine(tail, schemaVersion)]),
      recordLines: Object.freeze([...prefix.recordLines, prefix.lines + 1]),
      malformedLines: prefix.malformedLines,
      firstErrorLine: prefix.firstErrorLine,
      unterminatedRecordLine: prefix.lines + 1,
    });
  } catch {
    return Object.freeze({
      records: prefix.records,
      recordLines: prefix.recordLines,
      malformedLines: prefix.malformedLines,
      firstErrorLine: prefix.firstErrorLine ?? prefix.lines + 1,
      unterminatedRecordLine: null,
    });
  }
}
