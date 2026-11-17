/** Disposable parsing acceleration; the bytes on disk remain the authority. */
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
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
}

interface Prefix extends ReceiptLogSnapshot {
  readonly bytes: number;
  readonly digest: string;
  readonly lines: number;
}

// Bound retained source volume and the number of histories. This is not a
// promise about JS heap bytes: decoded objects can exceed their JSON size.
const MAX_RETAINED_BYTES = 128 * 1024 * 1024;
const MAX_RETAINED_LOGS = 4;
const prefixes = new Map<string, Prefix>();
let retainedBytes = 0;

function digest(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function forget(key: string): void {
  const previous = prefixes.get(key);
  if (previous) retainedBytes -= previous.bytes;
  prefixes.delete(key);
}

function remember(key: string, prefix: Prefix): void {
  forget(key);
  if (prefix.bytes > MAX_RETAINED_BYTES) return;
  while (prefixes.size >= MAX_RETAINED_LOGS ||
    retainedBytes + prefix.bytes > MAX_RETAINED_BYTES) {
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

/**
 * Read fresh bytes on EVERY invocation. Only reuse a parsed newline-terminated
 * prefix after hashing those exact bytes; size/mtime/inode are not evidence of
 * unchanged history. Rewrites, rotation, truncation and same-size edits all
 * remain observable, including edits to an old identity-conflicting receipt.
 *
 * Never cache an unterminated tail: an append can finish its UTF-8 character or
 * JSON token. The tail still participates in this read, including strict errors.
 * Cache loss/eviction/restart affects cost only, never the returned facts.
 */
export async function readReceiptLogSnapshot(
  path: string,
  schemaVersion?: string,
  recoveryKeys: readonly string[] = [],
): Promise<ReceiptLogSnapshot | null> {
  const key = JSON.stringify([resolve(path), schemaVersion ?? null, recoveryKeys]);
  let bytes: Buffer;
  try {
    bytes = await readFile(path);
  } catch (error) {
    forget(key);
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
  const completeBytes = bytes.lastIndexOf(10) + 1;
  const prior = prefixes.get(key);
  const reusable = prior !== undefined && prior.bytes <= completeBytes &&
    digest(bytes.subarray(0, prior.bytes)) === prior.digest ? prior : null;
  let prefix: Prefix;
  if (reusable !== null && reusable.bytes === completeBytes) {
    prefix = reusable;
  } else {
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
        if (recovered.members.length > 0 || recovered.unreadable.length > 0) {
          malformedLines.push(freezeMalformed({ line: lines, ...recovered }));
        }
      } else if (line.trim()) {
        try {
          record = decodeLine(line, schemaVersion);
        } catch {
          firstErrorLine ??= lines;
          const recovered = jsonLineMembers(raw, recoveryKeys);
          if (recovered.members.length > 0 || recovered.unreadable.length > 0) {
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
    prefix = Object.freeze({
      records: Object.freeze(records),
      recordLines: Object.freeze(recordLines),
      malformedLines: Object.freeze(malformedLines),
      firstErrorLine, lines,
      bytes: completeBytes, digest: digest(bytes.subarray(0, completeBytes)),
    });
  }
  remember(key, prefix);
  const tail = strictUtf8(bytes.subarray(completeBytes));
  if (tail === null) {
    return Object.freeze({
      records: prefix.records,
      recordLines: prefix.recordLines,
      malformedLines: prefix.malformedLines,
      firstErrorLine: prefix.firstErrorLine ?? prefix.lines + 1,
    });
  }
  if (!tail.trim()) return prefix;
  try {
    return Object.freeze({
      records: Object.freeze([...prefix.records, decodeLine(tail, schemaVersion)]),
      recordLines: Object.freeze([...prefix.recordLines, prefix.lines + 1]),
      malformedLines: prefix.malformedLines,
      firstErrorLine: prefix.firstErrorLine,
    });
  } catch {
    return Object.freeze({
      records: prefix.records,
      recordLines: prefix.recordLines,
      malformedLines: prefix.malformedLines,
      firstErrorLine: prefix.firstErrorLine ?? prefix.lines + 1,
    });
  }
}
