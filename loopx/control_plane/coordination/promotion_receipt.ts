/** One durable readback contract for reviewed and already-fenced promotion.
 * The original transaction proves cutover even after later canonical writes. */
import type { JsonObject } from "../effect_program.ts";
import type { AuthorityStore, AuthorityStoreCommitResult } from "./authority_store.ts";
import { canonicalAuthorityBytes, canonicalAuthoritySha256 } from "./authority_store_codec.ts";

export interface PromotionCommitIdentity {
  operation_id: string;
  receipt: JsonObject;
  projection_sha256: string;
}
export type PromotionReadback =
  | { matched: true; provider_revision: string; cursor: string }
  | { matched: false; reason_code: string };

export async function readPromotionReceipt(
  store: AuthorityStore,
  identity: PromotionCommitIdentity,
): Promise<PromotionReadback> {
  const found = await store.readReceipt(identity.operation_id);
  if (found.status === "failed" || found.status === "unavailable") {
    return {matched: false, reason_code: found.reason_code};
  }
  if (
    found.status !== "found" ||
    found.receipts.length !== 1 ||
    !canonicalAuthorityBytes(found.receipts[0]).equals(canonicalAuthorityBytes(identity.receipt))
  ) {
    return {
      matched: false,
      reason_code:
        found.status === "found"
          ? "local_authority_promotion_identity_mismatch"
          : "local_authority_promotion_receipt_missing",
    };
  }
  const lineage = await store.scanCommitted(null, 1);
  if (lineage.status !== "page") return {matched: false, reason_code: lineage.reason_code};
  const first = lineage.transactions[0];
  if (
    first === undefined ||
    first.cursor !== "1" ||
    first.operation_id !== identity.operation_id ||
    first.provider_revision !== found.provider_revision ||
    first.cursor !== found.cursor ||
    first.receipts.length !== 1 ||
    !canonicalAuthorityBytes(first.receipts[0]).equals(canonicalAuthorityBytes(identity.receipt)) ||
    canonicalAuthoritySha256(first.projection) !== identity.projection_sha256
  ) {
    return { matched: false, reason_code: "local_authority_promotion_lineage_mismatch" };
  }
  return { matched: true, provider_revision: found.provider_revision, cursor: found.cursor };
}

export interface PromotionCommitReadback {
  commit: AuthorityStoreCommitResult | null;
  readback: PromotionReadback;
  interrupted: boolean;
}

/**
 * Read the durable identity once; never issue a second business commit here. */
export async function commitPromotionAndReadBack(
  store: AuthorityStore,
  identity: PromotionCommitIdentity,
  projection: JsonObject,
  event: JsonObject,
): Promise<PromotionCommitReadback> {
  let commit: AuthorityStoreCommitResult | null = null;
  let interrupted = false;
  try {
    commit = await store.commitAuthority({
      expected_provider_revision: null,
      operation_id: identity.operation_id,
      events: [event],
      next_projection: projection,
      receipts: [identity.receipt],
    });
  } catch {
    interrupted = true;
  }
  return { commit, interrupted, readback: await readPromotionReceipt(store, identity) };
}
