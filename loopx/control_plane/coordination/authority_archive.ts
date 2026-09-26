/** Portable retained-journal recovery; activation remains a separate operation. */
import {randomUUID} from "node:crypto";
import {link, open, unlink} from "node:fs/promises";
import {dirname} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreCommittedTransaction} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthoritySha256, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {authorityStateDelta} from "./authority_state_log.ts";
import {AUTHORITY_ARCHIVE_SCHEMA, archiveCursor as positive, verifyAuthorityArchive,
  withVerifiedAuthorityArchive, type ArchiveHeader, type AuthorityArchiveSummary} from "./authority_archive_read.ts";
import {checkArchivePage, checkArchivePrefix} from "./authority_archive_audit.ts";
export {verifyAuthorityArchive} from "./authority_archive_read.ts";
export type {AuthorityArchiveSummary} from "./authority_archive_read.ts";

const MAX_LINE_BYTES = 64 * 1024 * 1024;
function invalid(reason: string): never { throw new AuthorityStoreProtocolError(reason); }
function signed(value: JsonObject): JsonObject { return {...value, sha256: canonicalAuthoritySha256(value)}; }

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
  const header: ArchiveHeader = {kind: "header", schema_version: AUTHORITY_ARCHIVE_SCHEMA, goal_id: goalId,
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

/** Restore a verified private snapshot into an isolated target. Validate every
 * existing row and receipt before writing the suffix. Recovery never retries an
 * ambiguous write: only exact journal and original-receipt readback proves it. */
export async function restoreAuthorityArchive(path: string, target: AuthorityStore,
  expectedArchiveSha256: string): Promise<AuthorityArchiveSummary & {status: "restored"; target_store_identity: string; target_provider_revision: string}> {
  return withVerifiedAuthorityArchive(path, expectedArchiveSha256, async archive => {
    const verified = archive.summary;
    const identity = await target.storeIdentity();
    if (identity.status !== "available" || identity.store_identity === verified.source_store_identity) {
      invalid("restore requires an independent available target lineage");
    }
    const head = await target.loadAuthority();
    if (head.status !== "missing" && head.status !== "loaded") invalid("restore target is unavailable");
    const retained = head.status === "loaded" ? positive(head.cursor) : "0";
    if (BigInt(retained) > BigInt(verified.commits)) invalid("restore target has extra commits");
    // Prefix auditing is paged and read-only, including the receipt index. A
    // divergent or unreadable prefix never receives a speculative new suffix.
    const prefix = await checkArchivePrefix(archive, target, retained);
    if (prefix.status !== "matched") invalid(`restore ${prefix.reason_code}; resume the same archive`);
    if (head.status === "loaded" && (prefix.provider_revision !== head.provider_revision ||
        prefix.projection_sha256 !== canonicalAuthoritySha256(head.head))) invalid("restore target head differs from retained history");
    let previous = prefix.provider_revision;
    let after: string | null = retained === "0" ? null : retained;
    let pending: AuthorityStoreCommittedTransaction[] = [];
    for await (const row of archive.transactions()) {
      if (BigInt(row.cursor) <= BigInt(retained)) continue;
      let acknowledged: Awaited<ReturnType<AuthorityStore["commitAuthority"]>> | undefined;
      try {
        acknowledged = await target.commitAuthority({expected_provider_revision: previous, operation_id: row.operation_id,
          events: row.events, next_projection: row.projection, receipts: row.receipts});
      } catch { /* exact journal readback is the recovery proof */ }
      pending.push(row);
      if (acknowledged?.status === "applied") {
        if (acknowledged.cursor !== row.cursor) invalid("restore commit acknowledgement cursor mismatch");
        previous = acknowledged.provider_revision;
      }
      // Normal writes share a bounded page proof. An uncertain/rejected write
      // forces immediate readback before any later write; never issue it twice.
      if (pending.length === 16 || row.cursor === verified.commits || acknowledged?.status !== "applied") {
        const checked = await checkArchivePage(target, pending, after);
        if (checked.status !== "matched") invalid(`restore ${checked.reason_code}; resume the same archive`);
        if (acknowledged?.status === "applied" && checked.provider_revision !== previous) {
          invalid("restore commit acknowledgement differs from durable revision");
        }
        previous = checked.provider_revision; after = row.cursor; pending = [];
      }
    }
    const final = await target.loadAuthority();
    const finalIdentity = await target.storeIdentity();
    if (finalIdentity.status !== "available" || finalIdentity.store_identity !== identity.store_identity ||
        final.status !== "loaded" || final.cursor !== verified.commits || final.provider_revision !== previous ||
        canonicalAuthoritySha256(final.head) !== verified.projection_sha256) invalid("restore final readback mismatch");
    return {...verified, status: "restored", target_store_identity: identity.store_identity, target_provider_revision: final.provider_revision};
  });
}
