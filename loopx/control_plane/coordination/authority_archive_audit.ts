/** Provider-neutral recovery evidence. State equality alone cannot prove that
 * historical decisions, no-change receipts, or receipt lookup survived. */
import {readAuthorityReceipts, type AuthorityStore, type AuthorityStoreCommittedTransaction} from "./authority_store.ts";
import {canonicalAuthoritySha256} from "./authority_store_codec.ts";
import {withVerifiedAuthorityArchive, type VerifiedAuthorityArchive} from "./authority_archive_read.ts";

type AuditFailure = {
  status: "mismatch" | "unavailable";
  reason_code: string;
  /** Cursor only: private operation IDs, state and receipt bodies stay local. */
  cursor?: string;
};
type PrefixProof = {
  status: "matched";
  compared_commits: string;
  provider_revision: string | null;
  projection_sha256: string | null;
};

function semanticHash(row: AuthorityStoreCommittedTransaction): string {
  // Physical CAS tokens change across providers; logical rows must not.
  const {provider_revision: _physicalRevision, ...semantic} = row;
  return canonicalAuthoritySha256(semantic);
}

/** Compare one ordered page and prove lookup of all its original receipts. */
export async function checkArchivePage(store: AuthorityStore,
  expected: readonly AuthorityStoreCommittedTransaction[], after: string | null): Promise<PrefixProof | AuditFailure> {
  if (expected.length < 1 || expected.length > 16) throw new TypeError("archive page requires 1..16 rows");
  const scan = await store.scanCommitted(after, expected.length);
  const cursor = expected[0].cursor;
  if (scan.status !== "page") return {status: "unavailable", reason_code: "archive_history_unavailable", cursor};
  if (scan.transactions.length !== expected.length || scan.next_cursor !== expected.at(-1)!.cursor) {
    return {status: "mismatch", reason_code: "archive_history_incomplete", cursor};
  }
  for (let i = 0; i < expected.length; i++) {
    if (semanticHash(expected[i]) !== semanticHash(scan.transactions[i])) {
      return {status: "mismatch", reason_code: "archive_transaction_mismatch", cursor: expected[i].cursor};
    }
  }
  const batch = await readAuthorityReceipts(store, expected.map(row => row.operation_id));
  if (batch.status !== "receipts") return {status: "unavailable", reason_code: "archive_receipt_unavailable", cursor};
  if (batch.results.length !== expected.length) return {status: "mismatch", reason_code: "archive_receipt_mismatch", cursor};
  for (let i = 0; i < expected.length; i++) {
    const receipt = batch.results[i], row = expected[i];
    if (receipt.status === "failed" || receipt.status === "unavailable") {
      return {status: "unavailable", reason_code: "archive_receipt_unavailable", cursor: row.cursor};
    }
    if (receipt.status !== "found" || receipt.cursor !== row.cursor ||
        receipt.provider_revision !== scan.transactions[i].provider_revision ||
        canonicalAuthoritySha256(receipt.receipts) !== canonicalAuthoritySha256(row.receipts)) {
      return {status: "mismatch", reason_code: "archive_receipt_mismatch", cursor: row.cursor};
    }
  }
  const last = scan.transactions.at(-1)!;
  return {status: "matched", compared_commits: last.cursor, provider_revision: last.provider_revision,
    projection_sha256: canonicalAuthoritySha256(last.projection)};
}

/** The source is fully verified before this cursor-bounded comparison starts.
 * Reaching a partial target never accepts an unchecked archive suffix. */
export async function checkArchivePrefix(archive: VerifiedAuthorityArchive,
  store: AuthorityStore, count: string): Promise<PrefixProof | AuditFailure> {
  if (!/^(0|[1-9]\d*)$/.test(count) || BigInt(count) > BigInt(archive.summary.commits)) {
    throw new TypeError("archive prefix count is outside verified history");
  }
  let proof: PrefixProof = {status: "matched", compared_commits: "0",
    provider_revision: null, projection_sha256: null};
  if (count === "0") return proof;
  let page: AuthorityStoreCommittedTransaction[] = [];
  for await (const expected of archive.transactions()) {
    page.push(expected);
    if (page.length < 16 && expected.cursor !== count) continue;
    const checked = await checkArchivePage(store, page, proof.compared_commits === "0" ? null : proof.compared_commits);
    if (checked.status !== "matched") return checked;
    proof = checked; page = [];
    if (proof.compared_commits === count) return proof;
  }
  throw new Error("verified archive prefix is incomplete");
}

export type AuthorityArchiveAuditResult = {
  schema_version: "loopx_authority_archive_audit_v0";
  scope: "exact" | "retained_prefix";
  archive_sha256: string;
  execution_authority_granted: false;
} & (AuditFailure | {
  status: "matched";
  compared_commits: string;
  target_store_identity: string;
  captured_target_cursor: string;
  captured_target_provider_revision: string;
  matched_prefix_provider_revision: string;
});

export async function auditAuthorityArchive(path: string, target: AuthorityStore,
  expectedSha256: string, scope: "exact" | "retained_prefix" = "exact"): Promise<AuthorityArchiveAuditResult> {
  if (scope !== "exact" && scope !== "retained_prefix") throw new TypeError("invalid archive audit scope");
  return withVerifiedAuthorityArchive(path, expectedSha256, async archive => {
    const base = {schema_version: "loopx_authority_archive_audit_v0" as const, scope,
      archive_sha256: archive.summary.archive_sha256, execution_authority_granted: false as const};
    const identity = await target.storeIdentity();
    const head = await target.loadAuthority();
    if (identity.status !== "available" || head.status === "failed" || head.status === "unavailable") {
      return {...base, status: "unavailable", reason_code: "archive_target_unavailable"};
    }
    if (head.status === "missing") return {...base, status: "mismatch", reason_code: "archive_target_missing"};
    if (head.head.goal_id !== archive.summary.goal_id) return {...base, status: "mismatch", reason_code: "archive_goal_mismatch"};
    if (BigInt(head.cursor) < BigInt(archive.summary.commits)) {
      return {...base, status: "mismatch", reason_code: "archive_target_incomplete"};
    }
    if (scope === "exact" && head.cursor !== archive.summary.commits) {
      return {...base, status: "mismatch", reason_code: "archive_target_has_newer_commits"};
    }
    const proof = await checkArchivePrefix(archive, target, archive.summary.commits);
    if (proof.status !== "matched") return {...base, ...proof};
    const finalIdentity = await target.storeIdentity();
    const final = await target.loadAuthority();
    if (finalIdentity.status !== "available" || final.status !== "loaded") {
      return {...base, status: "unavailable", reason_code: "archive_target_readback_unavailable"};
    }
    if (finalIdentity.store_identity !== identity.store_identity || final.head.goal_id !== archive.summary.goal_id ||
        BigInt(final.cursor) < BigInt(head.cursor) ||
        (final.cursor === head.cursor && final.provider_revision !== head.provider_revision)) {
      return {...base, status: "mismatch", reason_code: "archive_target_lineage_changed"};
    }
    if (scope === "exact" && (final.cursor !== head.cursor || final.provider_revision !== proof.provider_revision ||
        canonicalAuthoritySha256(final.head) !== archive.summary.projection_sha256)) {
      return {...base, status: "mismatch", reason_code: "archive_target_changed"};
    }
    return {...base, status: "matched", compared_commits: proof.compared_commits,
      target_store_identity: identity.store_identity, captured_target_cursor: head.cursor,
      captured_target_provider_revision: head.provider_revision,
      matched_prefix_provider_revision: proof.provider_revision!};
  });
}
