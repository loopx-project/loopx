/** A page proves a contiguous segment of one retained journal snapshot.
 * Storage effects and snapshot acquisition remain with each provider. */
import type {AuthorityStoreCommittedTransaction, AuthorityStoreHead,
  AuthorityStoreReadFailure, AuthorityStoreScanResult} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityBytes, copyAuthorityJson, parseAuthorityCursor} from "./authority_store_codec.ts";

export class AuthorityJournalScan {
  readonly after: string | null;
  readonly offset: bigint;
  readonly limit: number;

  private constructor(after: string | null, offset: bigint, limit: number) {
    this.after = after; this.offset = offset; this.limit = limit;
  }

  static prepare(after: string | null, limit: number): AuthorityJournalScan | AuthorityStoreReadFailure {
    try {
      const offset = parseAuthorityCursor(after);
      if (!Number.isSafeInteger(limit) || limit < 1) {
        throw new AuthorityStoreProtocolError("scan limit must be a positive safe integer");
      }
      return new AuthorityJournalScan(after, offset, limit);
    } catch (error) {
      if (!(error instanceof AuthorityStoreProtocolError)) throw error;
      return {status: "failed", reason_code: "invalid_scan_request", reason: error.message};
    }
  }

  rangeFailure(headCursor: string | null): AuthorityStoreReadFailure | null {
    return this.offset > parseAuthorityCursor(headCursor)
      ? {status: "failed", reason_code: "scan_cursor_out_of_range",
        reason: "scan cursor is ahead of the provider head"} : null;
  }

  /** Providers fetch up to limit + 1 rows inside the same snapshot as head.
   * The extra row proves has_more and must pass the same validation. */
  page(rows: readonly AuthorityStoreCommittedTransaction[], head: AuthorityStoreHead | null): AuthorityStoreScanResult {
    const range = this.rangeFailure(head?.cursor ?? null);
    if (range) return range;
    const remaining = parseAuthorityCursor(head?.cursor ?? null) - this.offset;
    const requested = BigInt(this.limit) + 1n;
    const expected = remaining < requested ? remaining : requested;
    if (BigInt(rows.length) !== expected) {
      throw new AuthorityStoreProtocolError("committed scan does not cover its retained snapshot interval");
    }
    const operations = new Set<string>();
    for (const [index, row] of rows.entries()) {
      if (parseAuthorityCursor(row.cursor) !== this.offset + BigInt(index) + 1n) {
        throw new AuthorityStoreProtocolError("committed scan cursor lineage is invalid");
      }
      if (operations.has(row.operation_id)) {
        throw new AuthorityStoreProtocolError("committed scan operation identity is duplicated");
      }
      operations.add(row.operation_id);
      if (head && row.cursor === head.cursor && (row.provider_revision !== head.provider_revision ||
          !canonicalAuthorityBytes(row.projection).equals(canonicalAuthorityBytes(head.head)))) {
        throw new AuthorityStoreProtocolError("committed scan head lineage is invalid");
      }
    }
    // Each caller owns the mutable JSON containers; immutable primitive values
    // need no additional byte copy for every retained projection in the page.
    const transactions = copyAuthorityJson(rows.slice(0, this.limit)) as AuthorityStoreCommittedTransaction[];
    return {status: "page", transactions,
      next_cursor: transactions.at(-1)?.cursor ?? this.after, has_more: rows.length > this.limit};
  }
}
