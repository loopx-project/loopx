/** Streaming archive decoding and a private, verified input snapshot. */
import {constants, createReadStream} from "node:fs";
import {chmod, copyFile, mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStoreCommittedTransaction} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthorityObjectList,
  canonicalAuthoritySha256, hasExactAuthorityKeys, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {applyAuthorityStateDelta, decodeAuthorityStateDelta} from "./authority_state_log.ts";

export const AUTHORITY_ARCHIVE_SCHEMA = "loopx_authority_archive_v0";
const MAX_LINE_BYTES = 64 * 1024 * 1024;
const HEX = /^[0-9a-f]{64}$/;
export interface ArchiveHeader extends JsonObject {
  kind: "header";
  schema_version: typeof AUTHORITY_ARCHIVE_SCHEMA;
  goal_id: string;
  source_provider: string;
  store_identity: string;
  cursor: string;
  provider_revision: string;
  projection_sha256: string;
}
export interface AuthorityArchiveSummary {
  schema_version: typeof AUTHORITY_ARCHIVE_SCHEMA;
  goal_id: string;
  source_provider: string;
  source_store_identity: string;
  source_provider_revision: string;
  commits: string;
  projection_sha256: string;
  archive_sha256: string;
}
type ArchiveRecord = {kind: "header"; header: ArchiveHeader} |
  {kind: "transaction"; transaction: AuthorityStoreCommittedTransaction} |
  {kind: "verified"; summary: AuthorityArchiveSummary};

function invalid(reason: string): never { throw new AuthorityStoreProtocolError(reason); }
export function archiveCursor(value: unknown): string {
  if (typeof value !== "string" || !/^[1-9]\d*$/.test(value)) invalid("archive cursor must be a positive integer string");
  return value;
}
export function archiveHash(value: unknown): string {
  if (typeof value !== "string" || !HEX.test(value)) invalid("archive digest must be SHA-256");
  return value;
}
function exact(value: JsonObject, keys: readonly string[]): void {
  if (!hasExactAuthorityKeys(value, keys)) invalid("archive record has missing or unknown fields");
}
function summary(header: ArchiveHeader, digest: string): AuthorityArchiveSummary {
  return {schema_version: AUTHORITY_ARCHIVE_SCHEMA, goal_id: header.goal_id, source_provider: header.source_provider,
    source_store_identity: header.store_identity, source_provider_revision: header.provider_revision,
    commits: header.cursor, projection_sha256: header.projection_sha256, archive_sha256: digest};
}
function unsigned(value: JsonObject): JsonObject {
  const {sha256, ...body} = value;
  if (archiveHash(sha256) !== canonicalAuthoritySha256(body)) invalid("archive record digest mismatch");
  return body;
}

/** Bound a single physical line before JSON parsing, without buffering history. */
async function* lines(path: string): AsyncGenerator<string> {
  let chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of createReadStream(path)) {
    const bytes = chunk as Buffer;
    let start = 0;
    while (start < bytes.length) {
      const end = bytes.indexOf(10, start);
      const piece = bytes.subarray(start, end === -1 ? bytes.length : end);
      size += piece.length;
      if (size > MAX_LINE_BYTES) invalid("archive record exceeds 64 MiB");
      chunks.push(piece);
      if (end === -1) break;
      // Fatal UTF-8 decoding prevents replacement characters silently changing data.
      yield new TextDecoder("utf-8", {fatal: true}).decode(Buffer.concat(chunks, size));
      chunks = []; size = 0; start = end + 1;
    }
  }
  if (size !== 0) invalid("archive is truncated: final newline missing");
}

/** One decoder owns ordering, state reconstruction and the terminal seal for
 * verify and restore. Hashes detect corruption; they are not signatures. */
async function* archiveRecords(path: string): AsyncGenerator<ArchiveRecord> {
  let header: ArchiveHeader | null = null;
  let digest: string | null = null;
  let cursor = 0n;
  let state: JsonObject = {};
  let revision: string | null = null;
  let sealed = false;
  const operations = new Set<string>();
  let verified: AuthorityArchiveSummary | null = null;
  for await (const line of lines(path)) {
    if (sealed) invalid("archive has content after its terminal seal");
    const raw = canonicalAuthorityObject(JSON.parse(line) as unknown, "archive record");
    const value = unsigned(raw);
    if (header === null) {
      exact(value, ["kind", "schema_version", "goal_id", "source_provider", "store_identity",
        "cursor", "provider_revision", "projection_sha256"]);
      if (value.kind !== "header" || value.schema_version !== AUTHORITY_ARCHIVE_SCHEMA ||
          !["file", "sqlite", "postgresql", "nokv"].includes(String(value.source_provider))) invalid("invalid archive header");
      header = {...value, kind: "header", schema_version: AUTHORITY_ARCHIVE_SCHEMA,
        goal_id: requireAuthorityStoreId(value.goal_id, "archive goal id"),
        source_provider: String(value.source_provider), store_identity: requireAuthorityStoreId(value.store_identity, "store identity"),
        cursor: archiveCursor(value.cursor), provider_revision: requireAuthorityStoreId(value.provider_revision, "provider revision"),
        projection_sha256: archiveHash(value.projection_sha256)};
      yield {kind: "header", header};
    } else if (value.kind === "transaction") {
      exact(value, ["kind", "cursor", "provider_revision", "operation_id", "events", "receipts",
        "delta", "projection_sha256", "previous_sha256"]);
      const nextCursor = archiveCursor(value.cursor);
      if (value.previous_sha256 !== digest || BigInt(nextCursor) !== cursor + 1n ||
          BigInt(nextCursor) > BigInt(header.cursor)) invalid("archive transaction lineage mismatch");
      const operation = requireAuthorityStoreId(value.operation_id, "operation id");
      if (operations.has(operation)) invalid("archive operation id is duplicated");
      operations.add(operation);
      state = applyAuthorityStateDelta(state, decodeAuthorityStateDelta(value.delta));
      if (state.goal_id !== header.goal_id) invalid("archive transaction belongs to another goal");
      if (canonicalAuthoritySha256(state) !== archiveHash(value.projection_sha256)) invalid("archive state reconstruction mismatch");
      revision = requireAuthorityStoreId(value.provider_revision, "provider revision");
      cursor += 1n;
      yield {kind: "transaction", transaction: {cursor: cursor.toString(), provider_revision: revision,
        operation_id: operation, events: canonicalAuthorityObjectList(value.events, "events"),
        receipts: canonicalAuthorityObjectList(value.receipts, "receipts"), projection: state}};
    } else if (value.kind === "seal") {
      exact(value, ["kind", "cursor", "projection_sha256", "previous_sha256"]);
      if (value.previous_sha256 !== digest || value.cursor !== header.cursor || cursor.toString() !== header.cursor ||
          revision !== header.provider_revision || value.projection_sha256 !== header.projection_sha256 ||
          canonicalAuthoritySha256(state) !== header.projection_sha256) invalid("archive seal does not cover its captured head");
      sealed = true;
      verified = summary(header, archiveHash(raw.sha256));
    } else invalid("unknown archive record kind");
    digest = archiveHash(raw.sha256);
  }
  if (!sealed || verified === null) invalid("archive is incomplete: terminal seal missing");
  // Verification is delivered only after EOF, including the no-trailing-data check.
  yield {kind: "verified", summary: verified};
}

export async function verifyAuthorityArchive(path: string): Promise<AuthorityArchiveSummary> {
  for await (const record of archiveRecords(path)) if (record.kind === "verified") return record.summary;
  return invalid("archive verification did not finish");
}


/** The path stays private to this scope. TypeScript readonly protects callers;
 * private copied bytes, rather than a type assertion or an open original inode,
 * protect a restore against replacement and in-place edits of the input. */
export interface VerifiedAuthorityArchive {
  readonly summary: Readonly<AuthorityArchiveSummary>;
  transactions(): AsyncGenerator<AuthorityStoreCommittedTransaction>;
}

export async function withVerifiedAuthorityArchive<T>(path: string, expectedSha256: string,
  consume: (archive: VerifiedAuthorityArchive) => Promise<T>): Promise<T> {
  archiveHash(expectedSha256);
  const directory = await mkdtemp(join(tmpdir(), "loopx-authority-archive-"));
  const snapshot = join(directory, "input.ndjson");
  try {
    // Copy may race a writer. Full verification and the reviewed digest must
    // pass on the resulting private bytes before any target is even opened.
    await copyFile(path, snapshot, constants.COPYFILE_EXCL);
    await chmod(snapshot, 0o600);
    const verified = await verifyAuthorityArchive(snapshot);
    if (verified.archive_sha256 !== expectedSha256) invalid("restore archive differs from the reviewed digest");
    return await consume({summary: Object.freeze(verified), async *transactions() {
      for await (const record of archiveRecords(snapshot)) {
        if (record.kind === "transaction") yield record.transaction;
      }
    }});
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
}
