/** Portable retained-journal recovery. A restored store is an isolated copy,
 * never an authority selection, writer-fence rollback or execution grant. */
import {randomUUID} from "node:crypto";
import {createReadStream} from "node:fs";
import {link, open, unlink} from "node:fs/promises";
import {dirname} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreCommittedTransaction} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthorityObjectList,
  canonicalAuthoritySha256, hasExactAuthorityKeys, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {applyAuthorityStateDelta, authorityStateDelta, decodeAuthorityStateDelta} from "./authority_state_log.ts";

const SCHEMA = "loopx_authority_archive_v0";
const MAX_LINE_BYTES = 64 * 1024 * 1024;
const HEX = /^[0-9a-f]{64}$/;
interface ArchiveHeader extends JsonObject {
  kind: "header";
  schema_version: typeof SCHEMA;
  goal_id: string;
  source_provider: string;
  store_identity: string;
  cursor: string;
  provider_revision: string;
  projection_sha256: string;
}
export interface AuthorityArchiveSummary {
  schema_version: typeof SCHEMA;
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
function positive(value: unknown): string {
  if (typeof value !== "string" || !/^[1-9]\d*$/.test(value)) invalid("archive cursor must be a positive integer string");
  return value;
}
function hash(value: unknown): string {
  if (typeof value !== "string" || !HEX.test(value)) invalid("archive digest must be SHA-256");
  return value;
}
function exact(value: JsonObject, keys: readonly string[]): void {
  if (!hasExactAuthorityKeys(value, keys)) invalid("archive record has missing or unknown fields");
}
function summary(header: ArchiveHeader, digest: string): AuthorityArchiveSummary {
  return {schema_version: SCHEMA, goal_id: header.goal_id, source_provider: header.source_provider,
    source_store_identity: header.store_identity, source_provider_revision: header.provider_revision,
    commits: header.cursor, projection_sha256: header.projection_sha256, archive_sha256: digest};
}
function signed(value: JsonObject): JsonObject {
  return {...value, sha256: canonicalAuthoritySha256(value)};
}
function unsigned(value: JsonObject): JsonObject {
  const {sha256, ...body} = value;
  if (hash(sha256) !== canonicalAuthoritySha256(body)) invalid("archive record digest mismatch");
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
async function* records(path: string): AsyncGenerator<ArchiveRecord> {
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
      if (value.kind !== "header" || value.schema_version !== SCHEMA ||
          !["file", "sqlite", "postgresql", "nokv"].includes(String(value.source_provider))) invalid("invalid archive header");
      header = {...value, kind: "header", schema_version: SCHEMA,
        goal_id: requireAuthorityStoreId(value.goal_id, "archive goal id"),
        source_provider: String(value.source_provider), store_identity: requireAuthorityStoreId(value.store_identity, "store identity"),
        cursor: positive(value.cursor), provider_revision: requireAuthorityStoreId(value.provider_revision, "provider revision"),
        projection_sha256: hash(value.projection_sha256)};
      yield {kind: "header", header};
    } else if (value.kind === "transaction") {
      exact(value, ["kind", "cursor", "provider_revision", "operation_id", "events", "receipts",
        "delta", "projection_sha256", "previous_sha256"]);
      const nextCursor = positive(value.cursor);
      if (value.previous_sha256 !== digest || BigInt(nextCursor) !== cursor + 1n ||
          BigInt(nextCursor) > BigInt(header.cursor)) invalid("archive transaction lineage mismatch");
      const operation = requireAuthorityStoreId(value.operation_id, "operation id");
      if (operations.has(operation)) invalid("archive operation id is duplicated");
      operations.add(operation);
      state = applyAuthorityStateDelta(state, decodeAuthorityStateDelta(value.delta));
      if (state.goal_id !== header.goal_id) invalid("archive transaction belongs to another goal");
      if (canonicalAuthoritySha256(state) !== hash(value.projection_sha256)) invalid("archive state reconstruction mismatch");
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
      verified = summary(header, hash(raw.sha256));
    } else invalid("unknown archive record kind");
    digest = hash(raw.sha256);
  }
  if (!sealed || verified === null) invalid("archive is incomplete: terminal seal missing");
  // Verification is delivered only after EOF, including the no-trailing-data check.
  yield {kind: "verified", summary: verified};
}

export async function verifyAuthorityArchive(path: string): Promise<AuthorityArchiveSummary> {
  for await (const record of records(path)) if (record.kind === "verified") return record.summary;
  return invalid("archive verification did not finish");
}

/** Pin one retained prefix. Concurrent appends are allowed; a changed store
 * identity, missing interval or rewritten captured head is not. Output is
 * published without replacing an existing file, only after independent readback. */
export async function exportAuthorityArchive(store: AuthorityStore, goalId: string, output: string,
  options: {pageSize?: number} = {}): Promise<AuthorityArchiveSummary> {
  requireAuthorityStoreId(goalId, "goal id");
  const pageSize = options.pageSize ?? 64;
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 512) invalid("archive page size must be 1..512");
  const head = await store.loadAuthority();
  if (head.status !== "loaded") invalid("archive requires a committed source head");
  const identity = await store.storeIdentity();
  if (identity.status !== "available") invalid("archive source identity is unavailable");
  if (head.head.goal_id !== goalId) invalid("archive source goal mismatch");
  const header: ArchiveHeader = {kind: "header", schema_version: SCHEMA, goal_id: goalId,
    source_provider: store.providerKind ?? "file", store_identity: identity.store_identity,
    cursor: positive(head.cursor), provider_revision: head.provider_revision,
    projection_sha256: canonicalAuthoritySha256(head.head)};
  const temporary = `${output}.${randomUUID()}.partial`;
  const handle = await open(temporary, "wx", 0o600);
  let closed = false;
  try {
    let digest = "";
    const append = async (value: JsonObject) => {
      const record = signed(value);
      const text = JSON.stringify(record) + "\n";
      if (Buffer.byteLength(text) > MAX_LINE_BYTES) invalid("archive record exceeds 64 MiB");
      await handle.writeFile(text);
      digest = String(record.sha256);
    };
    await append(header);
    let after: string | null = null;
    let state: JsonObject = {};
    let revision: string | null = null;
    while (after !== header.cursor) {
      const remaining = BigInt(header.cursor) - BigInt(after ?? "0");
      const limit = Number(remaining < BigInt(pageSize) ? remaining : BigInt(pageSize));
      const page = await store.scanCommitted(after, limit);
      if (page.status !== "page" || page.transactions.length !== limit ||
          page.next_cursor !== page.transactions.at(-1)?.cursor) invalid("archive source scan is incomplete");
      for (const row of page.transactions) {
        if (BigInt(positive(row.cursor)) !== BigInt(after ?? "0") + 1n) invalid("archive source cursor gap");
        await append({kind: "transaction", cursor: row.cursor, provider_revision: row.provider_revision,
          operation_id: row.operation_id, events: row.events, receipts: row.receipts,
          delta: authorityStateDelta(state, row.projection), projection_sha256: canonicalAuthoritySha256(row.projection),
          previous_sha256: digest});
        state = row.projection; after = row.cursor; revision = row.provider_revision;
      }
    }
    const finalIdentity = await store.storeIdentity();
    if (finalIdentity.status !== "available" || finalIdentity.store_identity !== identity.store_identity ||
        revision !== header.provider_revision || canonicalAuthoritySha256(state) !== header.projection_sha256) {
      invalid("archive source lineage changed during capture");
    }
    await append({kind: "seal", cursor: after, projection_sha256: header.projection_sha256, previous_sha256: digest});
    await handle.sync(); await handle.close(); closed = true;
    const verified = await verifyAuthorityArchive(temporary);
    await link(temporary, output);
    const directory = await open(dirname(output), "r");
    try { await directory.sync(); } finally { await directory.close(); }
    return verified;
  } finally {
    if (!closed) await handle.close();
    await unlink(temporary).catch(error => { if (error.code !== "ENOENT") throw error; });
  }
}

function semanticTransaction(row: AuthorityStoreCommittedTransaction): JsonObject {
  const {provider_revision: _revision, ...semantic} = row;
  return semantic;
}

/** Restore only into a caller-owned isolated store. CLI enforces a private
 * destination manifest/lock. Existing prefixes must match every retained
 * transaction; mismatches and extra writes reject without overwriting them.
 * A crash leaves an explicitly incomplete copy, resumable with the same digest. */
export async function restoreAuthorityArchive(path: string, target: AuthorityStore,
  expectedArchiveSha256: string): Promise<AuthorityArchiveSummary & {status: "restored"; target_store_identity: string; target_provider_revision: string}> {
  const verified = await verifyAuthorityArchive(path);
  if (verified.archive_sha256 !== hash(expectedArchiveSha256)) invalid("restore archive differs from the reviewed digest");
  const identity = await target.storeIdentity();
  if (identity.status !== "available" || identity.store_identity === verified.source_store_identity) {
    invalid("restore requires an independent available target lineage");
  }
  const head = await target.loadAuthority();
  if (head.status !== "missing" && head.status !== "loaded") invalid("restore target is unavailable");
  if (head.status === "loaded" && BigInt(positive(head.cursor)) > BigInt(verified.commits)) invalid("restore target has extra commits");
  let previous: string | null = null;
  let after: string | null = null;
  let completed = false;
  for await (const record of records(path)) {
    if (record.kind === "header") continue;
    if (record.kind === "verified") {
      if (record.summary.archive_sha256 !== verified.archive_sha256) invalid("archive changed during restore; target remains isolated");
      completed = true; continue;
    }
    const row = record.transaction;
    if (BigInt(row.cursor) > BigInt(head.status === "loaded" ? head.cursor : "0")) {
      // The sole write attempt may commit and lose its response. Read the exact
      // retained row below, never repeat the write or infer success from state alone.
      try {
        await target.commitAuthority({expected_provider_revision: previous, operation_id: row.operation_id,
          events: row.events, next_projection: row.projection, receipts: row.receipts});
      } catch { /* exact journal readback is the recovery proof */ }
    }
    const readback = await target.scanCommitted(after, 1);
    if (readback.status !== "page" || readback.transactions.length !== 1 ||
        canonicalAuthoritySha256(semanticTransaction(readback.transactions[0])) !==
        canonicalAuthoritySha256(semanticTransaction(row))) invalid("restore target transaction differs or is unavailable; resume the same archive");
    const durable = readback.transactions[0];
    const receipt = await target.readReceipt(row.operation_id);
    if (receipt.status !== "found" || receipt.cursor !== row.cursor || receipt.provider_revision !== durable.provider_revision ||
        canonicalAuthoritySha256(receipt.receipts) !== canonicalAuthoritySha256(row.receipts)) invalid("restore receipt readback mismatch");
    previous = durable.provider_revision; after = row.cursor;
  }
  const final = await target.loadAuthority();
  const finalIdentity = await target.storeIdentity();
  if (!completed || finalIdentity.status !== "available" || finalIdentity.store_identity !== identity.store_identity ||
      final.status !== "loaded" || final.cursor !== verified.commits || final.provider_revision !== previous ||
      canonicalAuthoritySha256(final.head) !== verified.projection_sha256) invalid("restore final readback mismatch");
  return {...verified, status: "restored", target_store_identity: identity.store_identity, target_provider_revision: final.provider_revision};
}
