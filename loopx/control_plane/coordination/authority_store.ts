import type { JsonObject } from "../effect_program.ts";

/**
 * Logical guarantees every promoted authority store must expose.
 *
 * These are LoopX persistence guarantees, not claims that file, NoKV, and
 * PostgreSQL have the same native primitives. The provider profile below
 * records how each backend can implement them and which qualifications remain.
 */
export const AUTHORITY_STORE_REQUIRED_GUARANTEES = [
  "atomic_event_projection_receipt_commit",
  "conditional_provider_revision",
  "durable_same_key_readback",
  "explicit_ambiguous_commit",
  "ordered_cursor_scan",
  "stable_store_lineage",
] as const;
export type AuthorityStoreRequiredGuarantee =
  (typeof AUTHORITY_STORE_REQUIRED_GUARANTEES)[number];

export type AuthorityStoreProviderKind = "file" | "nokv" | "postgresql" | "sqlite";
export type AuthorityStoreSourceAuthority = `${AuthorityStoreProviderKind}_v0`;
export type AuthorityStoreProviderStage =
  | "stage1_implemented"
  | "stage2a_candidate"
  | "stage2b_candidate";

export interface AuthorityStoreProviderProfile {
  stage: AuthorityStoreProviderStage;
  revision_primitive: string;
  atomic_commit_mapping: string;
  receipt_and_cursor_mapping: string;
  store_lineage_mapping: string;
  trust_boundary: string;
  qualification_holds: readonly string[];
}

/**
 * Concrete backend mappings used to review adapters without flattening their
 * failure or transaction models into a fictional universal database.
 */
export const AUTHORITY_STORE_PROVIDER_PROFILES = {
  sqlite: {
    stage: "stage2b_candidate",
    revision_primitive: "database_incarnation_and_locked_sequence",
    atomic_commit_mapping: "sqlite_transaction_over_head_and_commit_outbox",
    receipt_and_cursor_mapping: "unique_operation_index_and_integer_cursor",
    store_lineage_mapping: "persistent_database_incarnation",
    trust_boundary: "trusted_local_process_and_private_directory",
    qualification_holds: ["ten_day_soak", "retention_and_compaction", "authority_source_promotion"],
  },
  file: {
    stage: "stage1_implemented",
    revision_primitive: "locked_document_revision_chain",
    atomic_commit_mapping: "single_durable_document_replace",
    receipt_and_cursor_mapping: "retained_embedded_journal",
    store_lineage_mapping: "directory_store_identity_file",
    trust_boundary: "trusted_embedded_loopx_process",
    qualification_holds: [
      "authority_source_promotion",
      "local_writer_fencing",
    ],
  },
  nokv: {
    stage: "stage2a_candidate",
    revision_primitive: "path_generation_compare_and_publish",
    atomic_commit_mapping: "single_cas_envelope",
    receipt_and_cursor_mapping: "embedded_journal_pending_capacity_proof",
    store_lineage_mapping: "workbench_workspace_incarnation_id",
    trust_boundary: "loopx_authority_owned_nokv_credentials",
    qualification_holds: [
      "service_grade_contract_adapter",
      "atomic_workspace_incarnation_publication_fence",
      "restart_and_restore_recovery",
      "capacity_and_receipt_retention",
      "availability_and_ha",
    ],
  },
  postgresql: {
    stage: "stage2b_candidate",
    revision_primitive: "goal_head_row_version_or_locked_revision",
    atomic_commit_mapping: "one_sql_transaction_over_head_events_and_receipts",
    receipt_and_cursor_mapping: "unique_operation_row_and_per_goal_sequence",
    store_lineage_mapping: "service_managed_database_incarnation",
    trust_boundary: "transaction_local_tenant_scoped_service_database_role",
    qualification_holds: [
      "service_api_authentication_and_tenant_authorization",
      "service_role_provisioning_and_audit_policy",
      "restore_incarnation_rotation",
      "failover_pool_exhaustion_and_cancellation",
      "retention_partitioning_and_measured_capacity",
      "shadow_parity_and_authority_source_promotion",
    ],
  },
} as const satisfies Record<
  AuthorityStoreProviderKind,
  AuthorityStoreProviderProfile
>;

export interface AuthorityStoreCommit {
  /** Opaque storage token. Never substitute authority_revision or lease_epoch. */
  expected_provider_revision: string | null;
  /** Stored for lookup/uniqueness; interpreted only by LoopX authority. */
  operation_id: string;
  events: readonly JsonObject[];
  next_projection: JsonObject;
  receipts: readonly JsonObject[];
}

export interface AuthorityStoreHead {
  head: JsonObject;
  provider_revision: string;
  cursor: string;
}

export interface AuthorityStoreCommittedTransaction {
  cursor: string;
  provider_revision: string;
  operation_id: string;
  events: readonly JsonObject[];
  projection: JsonObject;
  receipts: readonly JsonObject[];
}

export type AuthorityStoreReadFailure =
  | { status: "unavailable"; reason_code: string; reason: string }
  | { status: "failed"; reason_code: string; reason: string };

export type AuthorityStoreIdentityResult =
  | { status: "available"; store_identity: string }
  | AuthorityStoreReadFailure;

export type AuthorityStoreLoadResult =
  | ({ status: "loaded" } & AuthorityStoreHead)
  | { status: "missing" }
  | AuthorityStoreReadFailure;

export type AuthorityStoreCommitResult =
  | { status: "applied"; provider_revision: string; cursor: string }
  | {
    status: "conflict";
    conflict_kind: "provider_revision_mismatch" | "operation_id_exists";
    current_provider_revision: string | null;
    current_cursor: string | null;
  }
  | { status: "ambiguous"; reason_code: string; reason: string }
  | { status: "failed"; reason_code: string; reason: string };

export type AuthorityStoreReceiptResult =
  | {
    status: "found";
    cursor: string;
    provider_revision: string;
    receipts: readonly JsonObject[];
  }
  | { status: "missing" }
  | AuthorityStoreReadFailure;

/** Results retain caller order, including duplicate IDs and missing receipts.
 * Each item has exactly the same proof contract as readReceipt. No global
 * snapshot promise is made by the fallback; native providers may strengthen it. */
export type AuthorityStoreReceiptBatchResult =
  | {status: "receipts"; results: readonly AuthorityStoreReceiptResult[]}
  | AuthorityStoreReadFailure;

export type AuthorityStoreScanResult =
  | {
    status: "page";
    transactions: readonly AuthorityStoreCommittedTransaction[];
    next_cursor: string | null;
    has_more: boolean;
  }
  | AuthorityStoreReadFailure;

/** Storage-only seam. Legal transitions and receipt meaning stay in LoopX. */
export interface AuthorityStore {
  /**
   * Provider identity is observability metadata, not a semantic authority.
   * Optional keeps third-party/test stores source-compatible while built-in
   * providers expose an unambiguous runtime label.
   */
  readonly providerKind?: AuthorityStoreProviderKind;
  storeIdentity(): Promise<AuthorityStoreIdentityResult>;
  loadAuthority(): Promise<AuthorityStoreLoadResult>;
  commitAuthority(commit: AuthorityStoreCommit): Promise<AuthorityStoreCommitResult>;
  readReceipt(operationId: string): Promise<AuthorityStoreReceiptResult>;
  /** Optional bounded acceleration; never a weaker receipt verification path. */
  readReceipts?(operationIds: readonly string[]): Promise<AuthorityStoreReceiptBatchResult>;
  scanCommitted(afterCursor: string | null, limit: number): Promise<AuthorityStoreScanResult>;
}

export async function readAuthorityReceipts(store: AuthorityStore,
  operationIds: readonly string[]): Promise<AuthorityStoreReceiptBatchResult> {
  if (operationIds.length < 1 || operationIds.length > 64) throw new TypeError("receipt batch requires 1..64 operations");
  if (store.readReceipts !== undefined) return store.readReceipts(operationIds);
  const results: AuthorityStoreReceiptResult[] = [];
  for (const id of operationIds) results.push(await store.readReceipt(id));
  return {status: "receipts", results};
}

/** Map a storage implementation to the public source label used by adapters. */
export function authorityStoreSourceAuthority(store: AuthorityStore): AuthorityStoreSourceAuthority {
  const kind = store.providerKind ?? "file";
  return `${kind}_v0`;
}
