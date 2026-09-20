import { createHash, randomUUID } from "node:crypto";
import { TextDecoder } from "node:util";

import type { JsonObject } from "../effect_program.ts";
import type {
  AuthorityStore,
  AuthorityStoreCommit,
  AuthorityStoreCommittedTransaction,
  AuthorityStoreCommitResult,
  AuthorityStoreIdentityResult,
  AuthorityStoreLoadResult,
  AuthorityStoreReadFailure,
  AuthorityStoreReceiptResult,
  AuthorityStoreScanResult,
} from "./authority_store.ts";
import {
  AuthorityStoreProtocolError,
  isAuthorityJsonObject,
  hasExactAuthorityKeys,
  canonicalAuthorityBytes,
  normalizeAuthorityStoreCommit,
  requireAuthorityStoreId,
} from "./authority_store_codec.ts";
import {appendRetainedAuthorityJournal, decodeRetainedAuthorityJournal,
  type RetainedAuthorityJournal, transactionForRevision} from "./authority_store_transactions.ts";
import {AuthorityJournalScan} from "./authority_journal_scan.ts";

const NOKV_AUTHORITY_STORE_SCHEMA = "loopx_nokv_authority_store_v0";
const DEFAULT_MAX_ENVELOPE_BYTES = 16 * 1024 * 1024;
const HEX_128_PATTERN = /^[0-9a-f]{32}$/;

export class NoKVTransportUnavailableError extends Error {}
export class NoKVTransportProtocolError extends Error {}

export type NoKVTransportFailure = {
  status: "unavailable" | "failed";
  reason_code: string;
  reason: string;
};

export type NoKVStoreIdentityResult =
  | { status: "available"; store_identity: string }
  | NoKVTransportFailure;

export type NoKVBlobReadResult =
  | { status: "loaded"; bytes: Uint8Array; generation: number }
  | { status: "missing" }
  | NoKVTransportFailure;

export interface NoKVBlobCasRequest {
  workbench: string;
  path: string;
  expected_generation: number | null;
  bytes: Uint8Array;
  operation_id: string;
  artifact_revision_id: string;
  /**
   * The workbench incarnation this publication is bound to. NoKV evaluates it
   * atomically with `expected_generation` before any durable row or object
   * exists; a stale value is refused as `failed/store_identity_mismatch`.
   */
  expected_workspace_incarnation_id: string;
}

export type NoKVBlobCasResult =
  | { status: "applied"; generation: number }
  | { status: "conflict"; current_generation: number | null }
  | { status: "ambiguous"; reason_code: string; reason: string }
  | { status: "failed"; reason_code: string; reason: string };

/** Raw byte-storage contract implemented by the Python SDK JSON-lines helper. */
export interface NoKVBlobTransport {
  storeIdentity(workbench: string): Promise<NoKVStoreIdentityResult>;
  readBlob(workbench: string, path: string): Promise<NoKVBlobReadResult>;
  casPublishBlob(request: NoKVBlobCasRequest): Promise<NoKVBlobCasResult>;
}

export interface NoKVAuthorityStoreOptions {
  tenant_id: string;
  goal_id: string;
  workbench: string;
  max_envelope_bytes?: number;
}

interface NoKVAuthorityStoreDocument extends JsonObject, RetainedAuthorityJournal {
  schema_version: typeof NOKV_AUTHORITY_STORE_SCHEMA;
  tenant_id: string;
  goal_id: string;
  store_identity: string;
  storage_generation: number;

}

type EnvelopeReadResult =
  | {
    status: "loaded";
    identity: string;
    generation: number;
    document: NoKVAuthorityStoreDocument;
  }
  | { status: "missing"; identity: string }
  | AuthorityStoreReadFailure;

function providerRevision(tenantId: string, goalId: string, storeIdentity: string, storageGeneration: number, previousRevision: string | null, transaction: ReturnType<typeof transactionForRevision>): string {
  const digest = createHash("sha256").update(canonicalAuthorityBytes({ provider: "nokv", tenant_id: tenantId, goal_id: goalId, store_identity: storeIdentity, storage_generation: storageGeneration, previous_provider_revision: previousRevision, transaction })).digest("hex").slice(0, 24);
  return `nokv:${transaction.cursor}:${digest}`;
}

function physicalAttemptIdentity(
  domain: "operation" | "artifact_revision",
  attemptNonce: string,
  operationId: string,
  expectedGeneration: number | null,
  payload: Uint8Array,
): string {
  return createHash("sha256")
    .update(`loopx.nokv.${domain}.v1\0`, "utf8")
    .update(attemptNonce, "utf8")
    .update("\0", "utf8")
    .update(operationId, "utf8")
    .update("\0", "utf8")
    .update(expectedGeneration === null ? "create" : String(expectedGeneration), "utf8")
    .update("\0", "utf8")
    .update(createHash("sha256").update(payload).digest())
    .digest("hex")
    .slice(0, 32);
}

function requireGeneration(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    throw new AuthorityStoreProtocolError(`${name} must be a positive safe integer`);
  }
  return value as number;
}

function decodeDocument(
  value: unknown,
  tenantId: string,
  goalId: string,
  storeIdentity: string,
  observedGeneration: number,
): NoKVAuthorityStoreDocument {
  if (!isAuthorityJsonObject(value) || !hasExactAuthorityKeys(value, [
    "schema_version", "tenant_id", "goal_id", "store_identity", "storage_generation",
    "provider_revision", "cursor", "head", "committed",
  ]) || value.schema_version !== NOKV_AUTHORITY_STORE_SCHEMA) {
    throw new AuthorityStoreProtocolError("NoKV authority store schema mismatch");
  }
  if (value.tenant_id !== tenantId) {
    throw new AuthorityStoreProtocolError("NoKV authority store tenant mismatch");
  }
  if (value.goal_id !== goalId) {
    throw new AuthorityStoreProtocolError("NoKV authority store goal mismatch");
  }
  if (value.store_identity !== storeIdentity) {
    throw new AuthorityStoreProtocolError("NoKV authority store lineage mismatch");
  }
  const storageGeneration = requireGeneration(
    value.storage_generation,
    "NoKV storage generation",
  );
  if (storageGeneration !== observedGeneration) {
    throw new AuthorityStoreProtocolError(
      "NoKV authority store storage generation does not match read metadata",
    );
  }
  if (!Array.isArray(value.committed) || storageGeneration !== value.committed.length) {
    throw new AuthorityStoreProtocolError("NoKV authority store generation lineage is invalid");
  }
  return {schema_version: NOKV_AUTHORITY_STORE_SCHEMA, tenant_id: tenantId, goal_id: goalId,
    store_identity: storeIdentity, storage_generation: storageGeneration,
    ...decodeRetainedAuthorityJournal(value, "NoKV authority store", (previous, transaction) =>
      providerRevision(tenantId, goalId, storeIdentity, Number(transaction.cursor), previous, transaction))};
}

function readFailure(error: unknown): AuthorityStoreReadFailure {
  if (
    error instanceof AuthorityStoreProtocolError ||
    error instanceof NoKVTransportProtocolError ||
    error instanceof SyntaxError
  ) {
    return {
      status: "failed",
      reason_code: "provider_protocol_violation",
      reason: error.message,
    };
  }
  return {
    status: "unavailable",
    reason_code: "nokv_transport_unavailable",
    reason: error instanceof Error ? error.message : "NoKV transport unavailable",
  };
}

/** The incarnation half of a validated `nokv:{workbench}:{incarnation}` identity. */
function boundIncarnation(storeIdentity: string, workbench: string): string {
  return storeIdentity.slice(`nokv:${workbench}:`.length);
}

function validStoreIdentity(value: string, workbench: string): boolean {
  const prefix = `nokv:${workbench}:`;
  return value.startsWith(prefix) && HEX_128_PATTERN.test(value.slice(prefix.length));
}

/** Stage 2A candidate. No runtime constructs this provider by default. */
export class NoKVAuthorityStore implements AuthorityStore {
  readonly providerKind = "nokv" as const;
  readonly transport: NoKVBlobTransport;
  readonly tenantId: string;
  readonly goalId: string;
  readonly workbench: string;
  readonly maxEnvelopeBytes: number;
  readonly path: string;

  constructor(transport: NoKVBlobTransport, options: NoKVAuthorityStoreOptions) {
    this.transport = transport;
    this.tenantId = requireAuthorityStoreId(options.tenant_id, "tenant id");
    this.goalId = requireAuthorityStoreId(options.goal_id, "goal id");
    this.workbench = requireAuthorityStoreId(options.workbench, "workbench");
    this.maxEnvelopeBytes = options.max_envelope_bytes ?? DEFAULT_MAX_ENVELOPE_BYTES;
    if (!Number.isSafeInteger(this.maxEnvelopeBytes) || this.maxEnvelopeBytes < 1) {
      throw new AuthorityStoreProtocolError(
        "max envelope bytes must be a positive safe integer",
      );
    }
    const digest = createHash("sha256")
      .update(canonicalAuthorityBytes({
        tenant_id: this.tenantId,
        goal_id: this.goalId,
      }))
      .digest("hex")
      .slice(0, 32);
    this.path = `metadata/loopx-authority/${digest}.json`;
  }

  async storeIdentity(): Promise<AuthorityStoreIdentityResult> {
    try {
      const result = await this.transport.storeIdentity(this.workbench);
      if (result.status !== "available") return result;
      if (!validStoreIdentity(result.store_identity, this.workbench)) {
        return {
          status: "failed",
          reason_code: "store_identity_invalid",
          reason: "NoKV store identity does not match the bound workbench incarnation",
        };
      }
      return result;
    } catch (error) {
      return readFailure(error);
    }
  }

  private async readEnvelope(): Promise<EnvelopeReadResult> {
    const identityResult = await this.storeIdentity();
    if (identityResult.status !== "available") return identityResult;
    let result: NoKVBlobReadResult;
    try {
      result = await this.transport.readBlob(this.workbench, this.path);
    } catch (error) {
      return readFailure(error);
    }
    if (result.status === "missing") {
      return { status: "missing", identity: identityResult.store_identity };
    }
    if (result.status !== "loaded") return result;
    try {
      const generation = requireGeneration(result.generation, "NoKV read generation");
      let raw: string;
      try {
        raw = new TextDecoder("utf-8", { fatal: true }).decode(result.bytes);
      } catch (error) {
        throw new AuthorityStoreProtocolError(
          `NoKV authority store bytes are not UTF-8: ${
            error instanceof Error ? error.message : "invalid UTF-8"
          }`,
        );
      }
      let value: unknown;
      try {
        value = JSON.parse(raw);
      } catch (error) {
        throw new AuthorityStoreProtocolError(
          `NoKV authority store JSON is invalid: ${
            error instanceof Error ? error.message : "invalid JSON"
          }`,
        );
      }
      return {
        status: "loaded",
        identity: identityResult.store_identity,
        generation,
        document: decodeDocument(
          value,
          this.tenantId,
          this.goalId,
          identityResult.store_identity,
          generation,
        ),
      };
    } catch (error) {
      return readFailure(error);
    }
  }

  async loadAuthority(): Promise<AuthorityStoreLoadResult> {
    const result = await this.readEnvelope();
    if (result.status === "missing") return { status: "missing" };
    if (result.status !== "loaded") return result;
    return {
      status: "loaded",
      head: structuredClone(result.document.head),
      provider_revision: result.document.provider_revision,
      cursor: result.document.cursor,
    };
  }

  private async settleCommitFromReadback(
    expectedProviderRevision: string | null,
    intended: AuthorityStoreCommittedTransaction,
    fallback: Extract<NoKVBlobCasResult, { status: "ambiguous" }>,
  ): Promise<AuthorityStoreCommitResult> {
    const observed = await this.readEnvelope();
    if (observed.status === "failed") return observed;
    if (observed.status !== "loaded") return fallback;
    const sameOperation = observed.document.committed.find(
      (entry) => entry.operation_id === intended.operation_id,
    );
    if (sameOperation) {
      if (
        canonicalAuthorityBytes(sameOperation).equals(canonicalAuthorityBytes(intended))
      ) {
        return {
          status: "applied",
          provider_revision: sameOperation.provider_revision,
          cursor: sameOperation.cursor,
        };
      }
      return {
        status: "conflict",
        conflict_kind: "operation_id_exists",
        current_provider_revision: observed.document.provider_revision,
        current_cursor: observed.document.cursor,
      };
    }
    if (observed.document.provider_revision !== expectedProviderRevision) {
      return {
        status: "conflict",
        conflict_kind: "provider_revision_mismatch",
        current_provider_revision: observed.document.provider_revision,
        current_cursor: observed.document.cursor,
      };
    }
    return fallback;
  }

  async commitAuthority(commit: AuthorityStoreCommit): Promise<AuthorityStoreCommitResult> {
    let normalized: AuthorityStoreCommit;
    try {
      normalized = normalizeAuthorityStoreCommit(commit);
    } catch (error) {
      return {
        status: "failed",
        reason_code: "invalid_commit_request",
        reason: error instanceof Error ? error.message : "invalid commit request",
      };
    }
    const current = await this.readEnvelope();
    if (current.status !== "loaded" && current.status !== "missing") {
      return {
        status: "failed",
        reason_code: current.reason_code,
        reason: current.reason,
      };
    }
    const currentDocument = current.status === "loaded" ? current.document : null;
    if ((currentDocument?.provider_revision ?? null) !== normalized.expected_provider_revision) {
      return {
        status: "conflict",
        conflict_kind: "provider_revision_mismatch",
        current_provider_revision: currentDocument?.provider_revision ?? null,
        current_cursor: currentDocument?.cursor ?? null,
      };
    }
    if (
      currentDocument?.committed.some(
        (entry) => entry.operation_id === normalized.operation_id,
      )
    ) {
      return {
        status: "conflict",
        conflict_kind: "operation_id_exists",
        current_provider_revision: currentDocument.provider_revision,
        current_cursor: currentDocument.cursor,
      };
    }
    const generation = (current.status === "loaded" ? current.generation : 0) + 1;
    const journal = appendRetainedAuthorityJournal(currentDocument, normalized, (previous, transaction) =>
      providerRevision(this.tenantId, this.goalId, current.identity, generation, previous, transaction));
    const transaction = journal.committed.at(-1)!;
    const document: NoKVAuthorityStoreDocument = {schema_version: NOKV_AUTHORITY_STORE_SCHEMA,
      tenant_id: this.tenantId, goal_id: this.goalId, store_identity: current.identity,
      storage_generation: generation, ...journal};
    const payload = canonicalAuthorityBytes(document);
    if (payload.byteLength > this.maxEnvelopeBytes) {
      return {
        status: "failed",
        reason_code: "authority_envelope_too_large",
        reason: `authority envelope exceeds ${this.maxEnvelopeBytes} bytes`,
      };
    }
    const expectedGeneration = current.status === "loaded" ? current.generation : null;
    // NoKV publication identities are terminal after a failed or quarantined
    // attempt. Keep the LoopX operation id stable in the authority envelope,
    // while giving each physical retry a fresh pair of lower-layer ids. A
    // response-lost success is still settled only by reading that envelope.
    // The request also names the incarnation the envelope was read from, so a
    // workbench restored to a new incarnation between this read and the
    // publish refuses the write instead of accepting it at a restarted
    // generation.
    const attemptNonce = randomUUID();
    let result: NoKVBlobCasResult;
    try {
      result = await this.transport.casPublishBlob({
        workbench: this.workbench,
        path: this.path,
        expected_generation: expectedGeneration,
        expected_workspace_incarnation_id: boundIncarnation(current.identity, this.workbench),
        bytes: payload,
        operation_id: physicalAttemptIdentity(
          "operation",
          attemptNonce,
          normalized.operation_id,
          expectedGeneration,
          payload,
        ),
        artifact_revision_id: physicalAttemptIdentity(
          "artifact_revision",
          attemptNonce,
          normalized.operation_id,
          expectedGeneration,
          payload,
        ),
      });
    } catch (error) {
      result = {
        status: "ambiguous",
        reason_code: "nokv_transport_lost",
        reason: error instanceof Error ? error.message : "NoKV transport outcome unknown",
      };
    }
    if (result.status === "applied" && result.generation === generation) {
      // The incarnation fence removes the stale write, not the readback
      // obligation: success is exposed only after a fresh read proves this
      // exact transaction in the current incarnation (RFC §6.2), so an owner
      // that ignored the fence still cannot make a restarted generation look
      // like a LoopX commit.
      return await this.settleCommitFromReadback(
        normalized.expected_provider_revision,
        transaction,
        {
          status: "ambiguous",
          reason_code: "nokv_applied_readback_unproved",
          reason: "NoKV applied response requires current-incarnation readback",
        },
      );
    }
    if (result.status === "failed") return result;
    const fallback: Extract<NoKVBlobCasResult, { status: "ambiguous" }> =
      result.status === "ambiguous"
        ? result
        : {
          status: "ambiguous",
          reason_code: result.status === "conflict"
            ? "nokv_cas_conflict_unresolved"
            : "nokv_publish_response_invalid",
          reason: result.status === "conflict"
            ? "NoKV CAS conflict requires authority-envelope readback"
            : "NoKV publish generation did not match the attempted envelope",
        };
    return await this.settleCommitFromReadback(
      normalized.expected_provider_revision,
      transaction,
      fallback,
    );
  }

  async readReceipt(operationId: string): Promise<AuthorityStoreReceiptResult> {
    let normalized: string;
    try {
      normalized = requireAuthorityStoreId(operationId, "operation id");
    } catch (error) {
      return {
        status: "failed",
        reason_code: "invalid_operation_id",
        reason: error instanceof Error ? error.message : "invalid operation id",
      };
    }
    const result = await this.readEnvelope();
    if (result.status === "missing") return { status: "missing" };
    if (result.status !== "loaded") return result;
    const transaction = result.document.committed.find(
      (entry) => entry.operation_id === normalized,
    );
    return transaction
      ? {
        status: "found",
        cursor: transaction.cursor,
        provider_revision: transaction.provider_revision,
        receipts: structuredClone(transaction.receipts),
      }
      : { status: "missing" };
  }

  async scanCommitted(
    afterCursor: string | null,
    limit: number,
  ): Promise<AuthorityStoreScanResult> {
    const scan = AuthorityJournalScan.prepare(afterCursor, limit);
    if (!(scan instanceof AuthorityJournalScan)) return scan;
    const result = await this.readEnvelope();
    if (result.status === "missing") return scan.page([], null);
    if (result.status !== "loaded") return result;
    try {
      const range = scan.rangeFailure(result.document.cursor);
      if (range) return range;
      const start = Number(scan.offset);
      return scan.page(result.document.committed.slice(start, start + limit + 1), result.document);
    } catch (error) { return readFailure(error); }
  }
}
