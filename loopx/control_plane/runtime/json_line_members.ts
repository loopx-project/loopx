/**
 * Recovery of the intact top-level members of a persisted JSONL row.
 *
 * The writer of a row owns its commit fact; nothing here decides whether a row
 * was finished.  This module answers one narrower question for a row whose
 * bytes are damaged *after* its writer finished it: which of the members a
 * caller names can still be read without guessing.  A member whose key or value
 * meets an undecodable byte is dropped, and a damaged region is resynchronized
 * so a display field early in the serialized row cannot hide the identity
 * members that follow it.
 *
 * Callers name the members they need because those names are schema-level
 * top-level keys.  Recovery never walks into a nested object to invent a
 * top-level member, and it never reports a member twice: a duplicated identity
 * is ambiguity, which the caller must reject rather than resolve.
 */

const WHITESPACE = new Set([" ", "\t", "\n", "\r"]);
const MAX_DEPTH = 64;
const MAX_MEMBERS = 256;
const NUMBER_AT = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/;

/** One row decoded byte by byte, retaining where decoding had to guess. */
interface DecodedRow {
  readonly text: string;
  /** Indexes of characters produced from bytes that are not valid UTF-8. */
  readonly damaged: ReadonlySet<number>;
}

/**
 * Length of the well-formed UTF-8 sequence at `index`, or 0 when the byte does
 * not start one.  Overlong, surrogate and out-of-range encodings are invalid,
 * which keeps a replacement character from ever looking like a legal code point.
 */
function utf8SequenceLength(bytes: Uint8Array, index: number): number {
  const first = bytes[index];
  if (first < 0x80) return 1;
  const continuation = (at: number, low = 0x80, high = 0xbf): boolean =>
    at < bytes.length && bytes[at] >= low && bytes[at] <= high;
  if (first >= 0xc2 && first <= 0xdf) {
    return continuation(index + 1) ? 2 : 0;
  }
  if (first >= 0xe0 && first <= 0xef) {
    const low = first === 0xe0 ? 0xa0 : 0x80;
    const high = first === 0xed ? 0x9f : 0xbf;
    return continuation(index + 1, low, high) && continuation(index + 2) ? 3 : 0;
  }
  if (first >= 0xf0 && first <= 0xf4) {
    const low = first === 0xf0 ? 0x90 : 0x80;
    const high = first === 0xf4 ? 0x8f : 0xbf;
    return continuation(index + 1, low, high) &&
      continuation(index + 2) && continuation(index + 3) ? 4 : 0;
  }
  return 0;
}

const VALID_UTF8 = new TextDecoder("utf-8", { ignoreBOM: true });

function decodeRow(bytes: Uint8Array): DecodedRow {
  let text = "";
  const damaged = new Set<number>();
  let index = 0;
  while (index < bytes.length) {
    const length = utf8SequenceLength(bytes, index);
    if (length === 0) {
      damaged.add(text.length);
      text += "\uFFFD";
      index += 1;
      continue;
    }
    text += VALID_UTF8.decode(bytes.subarray(index, index + length));
    index += length;
  }
  return { text, damaged };
}

function skipWhitespace(row: DecodedRow, index: number): number {
  let next = index;
  while (next < row.text.length && WHITESPACE.has(row.text[next])) next += 1;
  return next;
}

interface CompleteValue {
  readonly ok: true;
  readonly value: unknown;
  readonly next: number;
}

type ParsedValue = CompleteValue | { readonly ok: false };

function parseStringAt(row: DecodedRow, index: number): ParsedValue {
  let next = index + 1;
  let value = "";
  while (next < row.text.length) {
    if (row.damaged.has(next)) return { ok: false };
    const character = row.text[next];
    if (character === '"') return { ok: true, value, next: next + 1 };
    if (character !== "\\") {
      if (character.charCodeAt(0) < 0x20) return { ok: false };
      value += character;
      next += 1;
      continue;
    }
    const escape = row.text[next + 1];
    if (escape === undefined || row.damaged.has(next + 1)) return { ok: false };
    const simple: Record<string, string> = {
      '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f",
      n: "\n", r: "\r", t: "\t",
    };
    if (Object.hasOwn(simple, escape)) {
      value += simple[escape];
      next += 2;
      continue;
    }
    if (escape !== "u") return { ok: false };
    const code = row.text.slice(next + 2, next + 6);
    if (!/^[0-9a-fA-F]{4}$/.test(code)) return { ok: false };
    value += String.fromCharCode(Number.parseInt(code, 16));
    next += 6;
  }
  return { ok: false };
}

function parseValueAt(row: DecodedRow, index: number, depth: number): ParsedValue {
  if (depth > MAX_DEPTH) return { ok: false };
  const at = skipWhitespace(row, index);
  const character = row.text[at];
  if (character === undefined || row.damaged.has(at)) return { ok: false };
  if (character === '"') return parseStringAt(row, at);
  if (character === "{") return parseObjectAt(row, at, depth + 1);
  if (character === "[") return parseArrayAt(row, at, depth + 1);
  if (row.text.startsWith("true", at)) return { ok: true, value: true, next: at + 4 };
  if (row.text.startsWith("false", at)) return { ok: true, value: false, next: at + 5 };
  if (row.text.startsWith("null", at)) return { ok: true, value: null, next: at + 4 };
  const number = NUMBER_AT.exec(row.text.slice(at));
  if (number === null) return { ok: false };
  return { ok: true, value: Number(number[0]), next: at + number[0].length };
}

function parseArrayAt(row: DecodedRow, index: number, depth: number): ParsedValue {
  const items: unknown[] = [];
  let next = skipWhitespace(row, index + 1);
  if (row.text[next] === "]") return { ok: true, value: items, next: next + 1 };
  while (next < row.text.length) {
    const item = parseValueAt(row, next, depth);
    if (!item.ok) return item;
    items.push(item.value);
    next = skipWhitespace(row, item.next);
    if (row.text[next] === ",") {
      next = skipWhitespace(row, next + 1);
      continue;
    }
    if (row.text[next] === "]") return { ok: true, value: items, next: next + 1 };
    return { ok: false };
  }
  return { ok: false };
}

function parseObjectAt(row: DecodedRow, index: number, depth: number): ParsedValue {
  const pairs: [string, unknown][] = [];
  let next = skipWhitespace(row, index + 1);
  if (row.text[next] === "}") return { ok: true, value: {}, next: next + 1 };
  while (next < row.text.length) {
    if (row.text[next] !== '"' || row.damaged.has(next)) return { ok: false };
    const key = parseStringAt(row, next);
    if (!key.ok) return key;
    next = skipWhitespace(row, key.next);
    if (row.text[next] !== ":") return { ok: false };
    next = skipWhitespace(row, next + 1);
    const value = parseValueAt(row, next, depth);
    if (!value.ok) return value;
    pairs.push([key.value as string, value.value]);
    next = skipWhitespace(row, value.next);
    if (row.text[next] === ",") {
      next = skipWhitespace(row, next + 1);
      continue;
    }
    if (row.text[next] === "}") {
      return { ok: true, value: Object.fromEntries(pairs), next: next + 1 };
    }
    return { ok: false };
  }
  return { ok: false };
}

/**
 * How much a row's structure still shows after its bytes were damaged.
 *
 * Recovery answers one narrow question: which named top-level members can be
 * read without guessing.  When a damaged byte leaves a separator unknowable,
 * the members after it may be missing for reasons the row itself cannot state;
 * callers must then treat any missing identity as possibly hidden rather than
 * absent.
 */
export interface MemberScan {
  readonly offsets: readonly number[];
  /** A key's name met an undecodable byte, so the recovered set is incomplete. */
  readonly lostKeyName: boolean;
  /** A separator or container boundary was unknowable, so the scan is inexact. */
  readonly ambiguous: boolean;
}

/**
 * Byte offsets of the named keys that sit directly in the row's root object.
 *
 * A small JSON state machine tracks the container stack, so a key inside a
 * diagnostic `details` value can never be read as a top-level schema member.
 * Damage is handled by context rather than by scanning for a plausible quote:
 *
 * - A damaged byte where a value must start can only stand for that value's
 *   opening quote, so the scan resumes inside the string and stops at its own
 *   closing quote.
 * - A damaged byte where a key must start likewise stands for that key's
 *   opening quote; the key's name is lost, which the caller is told about.
 * - A damaged byte inside a string is the lost closing quote only when the
 *   separator its role requires follows it.  Otherwise it is damage inside the
 *   text, and the string ends at the next unescaped quote as it normally would.
 *
 * Anything the state machine cannot place leaves `ambiguous` set.  The scan
 * never guesses a member from text that may be nested, and the caller must not
 * read a missing identity as an absent one when either flag is set.
 */
function topLevelMemberOffsets(row: DecodedRow, wanted: ReadonlySet<string>): MemberScan {
  const offsets: number[] = [];
  let index = skipWhitespace(row, 0);
  if (row.text[index] !== "{" || row.damaged.has(index)) {
    return {
      offsets,
      lostKeyName: false,
      ambiguous: row.text[index] !== "{",
    };
  }
  const containers: ("object" | "array")[] = ["object"];
  // True when the row's next token must be a value (after `:` or in an array).
  let expectValue = false;
  // True when the row's next token must be a key of the current object.
  let expectKey = true;
  let inString: "key" | "value" | null = null;
  let escaped = false;
  let lostKeyName = false;
  let ambiguous = false;
  index += 1;
  while (index < row.text.length && containers.length > 0) {
    if (offsets.length > MAX_MEMBERS) {
      ambiguous = true;
      break;
    }
    const character = row.text[index];
    if (row.damaged.has(index)) {
      if (inString === null) {
        if (expectValue) {
          // A value was required here: the damaged byte is its opening quote.
          inString = "value";
          escaped = false;
          expectValue = false;
          expectKey = false;
          index += 1;
          continue;
        }
        if (expectKey && containers[containers.length - 1] === "object") {
          // A key was required here: its name is lost, its structure is not.
          lostKeyName = true;
          inString = "key";
          escaped = false;
          expectKey = false;
          index += 1;
          continue;
        }
        ambiguous = true;
        index += 1;
        continue;
      }
      if (escaped) {
        escaped = false;
        index += 1;
        continue;
      }
      const after = skipWhitespace(row, index + 1);
      const follower = row.text[after];
      const closesKey = inString === "key" && follower === ":";
      const closesValue = inString === "value" &&
        (follower === "," || follower === "}" || follower === "]");
      if (closesKey || closesValue) {
        // The damaged byte sits where this string's closing quote was.
        inString = null;
        expectKey = false;
        expectValue = false;
        index = after;
        continue;
      }
      // Damage inside the text; the string still ends at its own closing quote.
      index += 1;
      continue;
    }
    if (inString !== null) {
      if (escaped) {
        escaped = false;
        index += 1;
        continue;
      }
      if (character === "\\") {
        escaped = true;
        index += 1;
        continue;
      }
      if (character !== '"') {
        index += 1;
        continue;
      }
      const role = inString;
      inString = null;
      const after = skipWhitespace(row, index + 1);
      const follower = row.text[after];
      if (role === "key") {
        if (follower !== ":") ambiguous = true;
      } else if (follower !== "," && follower !== "}" && follower !== "]") {
        // A value string must be followed by the end of its container or the
        // next member; anything else means the closing quote was misread.
        ambiguous = true;
      }
      expectKey = false;
      expectValue = false;
      index = after;
      continue;
    }
    if (character === '"') {
      const role: "key" | "value" = expectValue ||
          containers[containers.length - 1] === "array" ? "value" : "key";
      const parsed = parseStringAt(row, index);
      if (role === "key") {
        if (!parsed.ok || typeof parsed.value !== "string") {
          // The name cannot be read; keep scanning inside the string so the
          // structure behind it stays available.
          lostKeyName = true;
          inString = "key";
          escaped = false;
          index += 1;
          continue;
        }
        const colon = skipWhitespace(row, parsed.next);
        if (row.text[colon] !== ":" || row.damaged.has(colon)) {
          // This was a value string mistaken for a key; it still ended here.
          expectKey = false;
          expectValue = false;
          index = parsed.next;
          continue;
        }
        if (containers.length === 1 && wanted.has(parsed.value)) {
          offsets.push(index);
        }
        expectValue = true;
        expectKey = false;
        index = colon + 1;
        continue;
      }
      if (!parsed.ok) {
        // The value string is damaged; keep scanning inside it.
        inString = "value";
        escaped = false;
        index += 1;
        continue;
      }
      expectValue = false;
      expectKey = false;
      index = parsed.next;
      continue;
    }
    if (character === "{") {
      containers.push("object");
      expectKey = true;
      expectValue = false;
      index += 1;
      continue;
    }
    if (character === "[") {
      containers.push("array");
      expectKey = false;
      expectValue = true;
      index += 1;
      continue;
    }
    if (character === "}" || character === "]") {
      const expected = character === "}" ? "object" : "array";
      if (containers[containers.length - 1] !== expected) {
        ambiguous = true;
      } else {
        containers.pop();
      }
      expectKey = false;
      expectValue = false;
      index += 1;
      continue;
    }
    if (character === ",") {
      const top = containers[containers.length - 1];
      expectKey = top === "object";
      expectValue = top === "array";
      index += 1;
      continue;
    }
    if (character === ":") {
      expectKey = false;
      expectValue = true;
      index += 1;
      continue;
    }
    // Any other byte continues a scalar value (number or literal).
    expectKey = false;
    expectValue = false;
    index += 1;
  }
  if (containers.length > 0) ambiguous = true;
  return { offsets, lostKeyName, ambiguous };
}

/** What one row still carries of the members a caller named. */
export interface LineMembers {
  /** Complete members: key and value both read with no guessing. */
  readonly members: readonly (readonly [string, unknown])[];
  /**
   * Named keys that are present but whose value could not be read intact.
   *
   * A caller that requires such a member must treat it as unreadable rather than
   * absent: the row did contain it, so "absent" would be a different fact.
   */
  readonly unreadable: readonly string[];
  /**
   * A key's name met an undecodable byte.
   *
   * The recovered member set is therefore incomplete: a member a caller needs
   * may be the one whose name was lost, and a caller cannot read its absence as
   * proof that the row never carried it.
   */
  readonly lostKeyName: boolean;
  /**
   * A separator or container boundary was unknowable.
   *
   * The members before the damage are exact; the ones after it are best effort.
   * A caller that must decide an identity from this row has to treat a missing
   * identity as unreadable rather than absent.
   */
  readonly ambiguous: boolean;
}

/**
 * The named top-level members that a row still carries intact.
 *
 * Only complete members count: a key or value that meets an undecodable byte is
 * dropped instead of repaired, and its key is reported as unreadable.  After a
 * dropped member the scan resynchronizes on the next named key, so damage early
 * in the row does not hide the identity members the writer finished later in
 * the same row.
 */
export function jsonLineMembers(
  bytes: Uint8Array,
  keys: readonly string[],
): LineMembers {
  const row = decodeRow(bytes);
  const wanted = new Set(keys);
  const members: [string, unknown][] = [];
  const unreadable: string[] = [];
  if (wanted.size === 0) {
    return { members, unreadable, lostKeyName: false, ambiguous: false };
  }
  const scan = topLevelMemberOffsets(row, wanted);
  for (const offset of scan.offsets) {
    const key = parseStringAt(row, offset);
    if (!key.ok || typeof key.value !== "string") continue;
    const colon = skipWhitespace(row, key.next);
    const value = parseValueAt(row, skipWhitespace(row, colon + 1), 1);
    if (!value.ok) {
      unreadable.push(key.value);
      continue;
    }
    members.push([key.value, value.value]);
  }
  return {
    members,
    unreadable: [...new Set(unreadable)],
    lostKeyName: scan.lostKeyName,
    ambiguous: scan.ambiguous,
  };
}
