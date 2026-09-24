import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readFile, rename, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeLockTimeoutError } from "../effect_runtime_errors.ts";
import { withFileMutationLock } from "../effect_runtime_io.ts";
import type {
  AuthorityStore,
  AuthorityStoreCommit,
  AuthorityStoreCommitResult,
  AuthorityStoreIdentityResult,
  AuthorityStoreLoadResult,
  AuthorityStoreHead,
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

const FILE_AUTHORITY_STORE_SCHEMA = "loopx_file_authority_store_v0";
const STORE_IDENTITY_PATTERN = /^file:[0-9a-f]{32}$/;
// File-v0 retains every projection in one envelope. A managed Effect server
// opens a new store handle for each request, so revalidating an unchanged
// journal on every read makes one Goal's history dominate the RPC budget.
// Keep only one verified document process-wide; bytes and store identity must
// both match before a later handle may reuse that validation.
const MAX_CACHED_DOCUMENT_BYTES = 128 * 1024 * 1024;
let verifiedDocument: {
  path: string;
  identity: string;
  digest: string;
  document: FileAuthorityStoreDocument;
} | null = null;

function documentDigest(raw: Uint8Array): string {
  return createHash("sha256").update(raw).digest("hex");
}

function rememberVerifiedDocument(path: string, identity: string, raw: Uint8Array,
  digest: string, document: FileAuthorityStoreDocument): void {
  verifiedDocument = raw.byteLength <= MAX_CACHED_DOCUMENT_BYTES
    ? {path, identity, digest, document}
    : null;
}

interface FileAuthorityStoreDocument extends JsonObject, RetainedAuthorityJournal {
  schema_version: typeof FILE_AUTHORITY_STORE_SCHEMA;
  goal_id: string;
  store_identity: string;

}

class FileStoreUnavailableError extends Error {}

export type FileAuthorityArchiveResult =
  | {
    status: "applied";
    archived_provider_revision: string;
    archived_cursor: string;
    archive_id: string;
  }
  | {
    status: "replayed";
    archived_provider_revision: string;
    archived_cursor: string;
    archive_id: string;
  }
  | { status: "missing" }
  | {
    status: "conflict";
    conflict_kind: string;
    current_provider_revision?: string;
    current_cursor?: string;
    archived_provider_revision?: string;
    archive_id?: string;
  }
  | {
    status: "ambiguous";
    reason_code: string;
    reason: string;
  }
  | {
    status: "failed";
    reason_code: string;
    reason: string;
  };

function providerRevision(goalId: string, storeIdentity: string, previousRevision: string | null, transaction: ReturnType<typeof transactionForRevision>): string {
  const digest = createHash("sha256").update(canonicalAuthorityBytes({ goal_id: goalId, store_identity: storeIdentity, previous_provider_revision: previousRevision, transaction })).digest("hex").slice(0, 24);
  return `file:${transaction.cursor}:${digest}`;
}

async function syncDirectory(directory: string): Promise<void> {
  if (process.platform === "win32") return;
  const handle = await open(directory, "r");
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function durableReplace(path: string, payload: Uint8Array): Promise<void> {
  const directory = dirname(path);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const temporary = `${path}.tmp-${process.pid}-${randomUUID()}`;
  const handle = await open(temporary, "wx", 0o600);
  try {
    try {
      await handle.writeFile(payload);
      await handle.sync();
    } finally {
      await handle.close();
    }
    await rename(temporary, path);
    await syncDirectory(directory);
  } finally {
    await rm(temporary, { force: true });
  }
}

function decodeDocument(
  value: unknown,
  goalId: string,
  storeIdentity: string,
): FileAuthorityStoreDocument {
  if (!isAuthorityJsonObject(value) || !hasExactAuthorityKeys(value, [
    "schema_version", "goal_id", "provider_revision", "cursor", "store_identity",
    "head", "committed",
  ]) || value.schema_version !== FILE_AUTHORITY_STORE_SCHEMA) {
    throw new AuthorityStoreProtocolError("file authority store schema mismatch");
  }
  if (value.goal_id !== goalId) throw new AuthorityStoreProtocolError("file authority store goal mismatch");
  if (value.store_identity !== storeIdentity) {
    throw new AuthorityStoreProtocolError("file authority store lineage mismatch");
  }
  return {schema_version: FILE_AUTHORITY_STORE_SCHEMA, goal_id: goalId, store_identity: storeIdentity,
    ...decodeRetainedAuthorityJournal(value, "file authority store", (previous, transaction) =>
      providerRevision(goalId, storeIdentity, previous, transaction))};
}

function readFailure(error: unknown): AuthorityStoreReadFailure {
  if (error instanceof AuthorityStoreProtocolError || error instanceof SyntaxError) {
    return { status: "failed", reason_code: "provider_protocol_violation", reason: error.message };
  }
  return {
    status: "unavailable",
    reason_code: "provider_read_unavailable",
    reason: error instanceof Error ? error.message : "provider read unavailable",
  };
}

/** File-backed Stage 1 conformance provider; LoopX owns all domain decisions. */
export class FileAuthorityStore implements AuthorityStore {
  readonly providerKind = "file" as const;
  readonly goalId: string;
  readonly directory: string;
  readonly path: string;
  readonly identityPath: string;
  private readonly existingOnly: boolean;

  constructor(directory: string, goalId: string, options: { existingOnly?: boolean } = {}) {
    this.goalId = requireAuthorityStoreId(goalId, "goal id");
    if (typeof directory !== "string" || directory.length === 0) {
      throw new AuthorityStoreProtocolError("store directory is required");
    }
    this.directory = resolve(directory);
    const digest = createHash("sha256").update(goalId, "utf8").digest("hex").slice(0, 16);
    this.path = join(this.directory, `authority-store-${digest}.json`);
    this.identityPath = join(this.directory, "store-identity");
    this.existingOnly = options.existingOnly === true;
  }

  /** Narrow effect seam for crash-window qualification; not a semantic hook. */
  protected async replaceDurably(path: string, payload: Uint8Array): Promise<void> {
    await durableReplace(path, payload);
  }

  /** Filesystem-only crash seam; the archive owner must still fsync both parents. */
  protected async archiveRenamed(): Promise<void> {}

  /** Full-history verification seam; unchanged byte-identical reads may reuse it. */
  protected decodeStoredDocument(value: unknown, identity: string): FileAuthorityStoreDocument {
    return decodeDocument(value, this.goalId, identity);
  }

  private async readStoreIdentity(createIfMissing = !this.existingOnly): Promise<string> {
    try {
      const identity = await readFile(this.identityPath, "utf8");
      if (!STORE_IDENTITY_PATTERN.test(identity)) {
        throw new AuthorityStoreProtocolError("store identity does not match file:<32 lowercase hex>");
      }
      if (createIfMissing) await syncDirectory(this.directory);
      return identity;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    if (!createIfMissing) throw new FileStoreUnavailableError("existing store identity is missing");
    return await withFileMutationLock(this.identityPath, async () => {
      try {
        const identity = await readFile(this.identityPath, "utf8");
        if (!STORE_IDENTITY_PATTERN.test(identity)) {
          throw new AuthorityStoreProtocolError("store identity does not match file:<32 lowercase hex>");
        }
        await syncDirectory(this.directory);
        return identity;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      }
      const identity = `file:${randomUUID().replaceAll("-", "")}`;
      await this.replaceDurably(this.identityPath, Buffer.from(identity, "ascii"));
      return identity;
    });
  }

  async storeIdentity(): Promise<AuthorityStoreIdentityResult> {
    try {
      return { status: "available", store_identity: await this.readStoreIdentity() };
    } catch (error) {
      if (error instanceof AuthorityStoreProtocolError) {
        return { status: "failed", reason_code: "store_identity_invalid", reason: error.message };
      }
      return {
        status: "unavailable",
        reason_code: error instanceof EffectRuntimeLockTimeoutError
          ? "store_identity_lock_timeout"
          : "store_identity_unavailable",
        reason: error instanceof Error ? error.message : "store identity unavailable",
      };
    }
  }

  private async readDocument(knownIdentity?: string): Promise<FileAuthorityStoreDocument | null> {
    let raw: Buffer;
    try {
      raw = await readFile(this.path);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
      throw new FileStoreUnavailableError(
        error instanceof Error ? error.message : "authority document unavailable",
      );
    }
    const identity = knownIdentity ?? await this.readStoreIdentity();
    try {
      const digest = documentDigest(raw);
      if (verifiedDocument?.path === this.path &&
          verifiedDocument.identity === identity && verifiedDocument.digest === digest) {
        return verifiedDocument.document;
      }
      const document = this.decodeStoredDocument(JSON.parse(raw.toString("utf8")), identity);
      rememberVerifiedDocument(this.path, identity, raw, digest, document);
      return document;
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw new AuthorityStoreProtocolError(`file authority store JSON is invalid: ${error.message}`);
      }
      throw error;
    }
  }

  async loadAuthority(): Promise<AuthorityStoreLoadResult> {
    try {
      const document = await this.readDocument();
      return document
        ? {
          status: "loaded",
          head: structuredClone(document.head),
          provider_revision: document.provider_revision,
          cursor: document.cursor,
        }
        : { status: "missing" };
    } catch (error) {
      return readFailure(error);
    }
  }

  /** Checkpoint-only external append: retain the real writer lock through the
   * synchronous callback. This neither commits nor advances authority revision. */
  async withCheckpointHead(save: (head: AuthorityStoreHead, identity: string) => JsonObject): Promise<JsonObject> {
    return await withFileMutationLock(this.path, async () => {
      const identity = await this.readStoreIdentity(false);
      const current = await this.readDocument();
      if (!current) throw new FileStoreUnavailableError("checkpoint authority is missing");
      const result = save({head: structuredClone(current.head),
        provider_revision: current.provider_revision, cursor: current.cursor}, identity);
      if (result instanceof Promise) throw new Error("checkpoint save must be synchronous");
      return result;
    });
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
    try {
      return await withFileMutationLock(this.path, async () => {
        let identity: string;
        let current: FileAuthorityStoreDocument | null;
        try {
          // Read the identity under the same document lock used by the commit.
          // A restored directory must not race a missing-head bootstrap and
          // bind new authority bytes to an identity observed before the lock.
          identity = await this.readStoreIdentity();
          current = await this.readDocument(identity);
        } catch (error) {
          return {
            status: "failed",
            reason_code: error instanceof AuthorityStoreProtocolError
              ? "provider_protocol_violation"
              : "provider_read_unavailable",
            reason: error instanceof Error ? error.message : "provider read unavailable",
          };
        }
        if (this.existingOnly && current === null) {
          return { status: "failed", reason_code: "existing_authority_missing", reason: "existing-only store cannot bootstrap a missing authority" };
        }
        if ((current?.provider_revision ?? null) !== normalized.expected_provider_revision) {
          return {
            status: "conflict",
            conflict_kind: "provider_revision_mismatch",
            current_provider_revision: current?.provider_revision ?? null,
            current_cursor: current?.cursor ?? null,
          };
        }
        if (current?.committed.some((entry) => entry.operation_id === normalized.operation_id)) {
          return {
            status: "conflict",
            conflict_kind: "operation_id_exists",
            current_provider_revision: current.provider_revision,
            current_cursor: current.cursor,
          };
        }
        const journal = appendRetainedAuthorityJournal(current, normalized, (previous, transaction) =>
          providerRevision(this.goalId, identity, previous, transaction));
        const {cursor, provider_revision: revision} = journal;
        const document: FileAuthorityStoreDocument = {schema_version: FILE_AUTHORITY_STORE_SCHEMA,
          goal_id: this.goalId, store_identity: identity, ...journal};
        try {
          const bytes = canonicalAuthorityBytes(document);
          await this.replaceDurably(this.path, bytes);
          rememberVerifiedDocument(this.path, identity, bytes, documentDigest(bytes), document);
        } catch (error) {
          // A failure after rename may already have published the new bytes.
          // The next read must prove the actual file rather than reuse either
          // the previous or attempted document.
          verifiedDocument = null;
          return {
            status: "ambiguous",
            reason_code: "commit_outcome_unknown",
            reason: error instanceof Error ? error.message : "commit outcome unknown",
          };
        }
        return { status: "applied", provider_revision: revision, cursor };
      });
    } catch (error) {
      return {
        status: "failed",
        reason_code: error instanceof EffectRuntimeLockTimeoutError
          ? "provider_lock_timeout"
          : "provider_write_unavailable",
        reason: error instanceof Error ? error.message : "provider write unavailable",
      };
    }
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
    try {
      const transaction = (await this.readDocument())?.committed.find(
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
    } catch (error) {
      return readFailure(error);
    }
  }

  async scanCommitted(afterCursor: string | null, limit: number): Promise<AuthorityStoreScanResult> {
    const scan = AuthorityJournalScan.prepare(afterCursor, limit);
    if (!(scan instanceof AuthorityJournalScan)) return scan;
    try {
      const document = await this.readDocument();
      if (!document) return scan.page([], null);
      const range = scan.rangeFailure(document.cursor);
      if (range) return range;
      const start = Number(scan.offset);
      return scan.page(document.committed.slice(start, start + limit + 1), document);
    } catch (error) { return readFailure(error); }
  }

  /**
   * Quarantine one exact pre-promotion shadow lineage without deleting it.
   *
   * This is intentionally file-provider-specific administrative behavior, not
   * part of the provider-neutral AuthorityStore contract. The caller must
   * fence the exact observed revision; an exact retry replays from the durable
   * archive while a re-used operation id cannot retire a later lineage.
   */
  async archiveAuthorityDocument(
    expectedProviderRevision: string,
    operationId: string,
  ): Promise<FileAuthorityArchiveResult> {
    let expectedRevision: string;
    let normalizedOperationId: string;
    try {
      expectedRevision = requireAuthorityStoreId(
        expectedProviderRevision,
        "expected provider revision",
      );
      normalizedOperationId = requireAuthorityStoreId(operationId, "operation id");
    } catch (error) {
      return {
        status: "failed",
        reason_code: "invalid_archive_request",
        reason: error instanceof Error ? error.message : "invalid archive request",
      };
    }
    const archiveId = this.authorityArchiveId(normalizedOperationId);
    const archiveDirectory = join(this.directory, "rollback");
    const archivePath = this.authorityArchivePath(normalizedOperationId);
    let renameStarted = false;
    try {
      return await withFileMutationLock(this.path, async () => {
        const identity = await this.readStoreIdentity(false);
        let archived: FileAuthorityStoreDocument | null = null;
        try {
          archived = decodeDocument(
            JSON.parse(await readFile(archivePath, "utf8")),
            this.goalId,
            identity,
          );
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
        }
        const current = await this.readDocument();
        if (archived) {
          if (archived.provider_revision !== expectedRevision) {
            return {
              status: "conflict",
              conflict_kind: "archive_operation_identity_mismatch",
              archived_provider_revision: archived.provider_revision,
              archive_id: archiveId,
            };
          }
          if (current) {
            return {
              status: "conflict",
              conflict_kind: "archive_operation_reused_after_rebootstrap",
              current_provider_revision: current.provider_revision,
              archive_id: archiveId,
            };
          }
          return {
            status: "replayed",
            archived_provider_revision: archived.provider_revision,
            archived_cursor: archived.cursor,
            archive_id: archiveId,
          };
        }
        if (!current) return { status: "missing" };
        if (current.provider_revision !== expectedRevision) {
          return {
            status: "conflict",
            conflict_kind: "provider_revision_mismatch",
            current_provider_revision: current.provider_revision,
            current_cursor: current.cursor,
          };
        }
        await mkdir(archiveDirectory, { recursive: true, mode: 0o700 });
        renameStarted = true;
        await rename(this.path, archivePath);
        await this.archiveRenamed();
        await syncDirectory(this.directory);
        await syncDirectory(archiveDirectory);
        return {
          status: "applied",
          archived_provider_revision: current.provider_revision,
          archived_cursor: current.cursor,
          archive_id: archiveId,
        };
      });
    } catch (error) {
      return {
        status: renameStarted ? "ambiguous" : "failed",
        reason_code: renameStarted
          ? "archive_outcome_unknown"
          : error instanceof EffectRuntimeLockTimeoutError
            ? "provider_lock_timeout"
            : "provider_archive_unavailable",
        reason: error instanceof Error ? error.message : "provider archive unavailable",
      };
    }
  }

  /** Stable provider-owned destination; callers cannot supply an archive path. */
  authorityArchiveId(operationId: string): string {
    requireAuthorityStoreId(operationId, "operation id");
    return createHash("sha256").update(this.goalId, "utf8").update("\0", "utf8")
      .update(operationId, "utf8").digest("hex").slice(0, 24);
  }

  authorityArchivePath(operationId: string): string {
    return join(this.directory, "rollback", `authority-store-${this.authorityArchiveId(operationId)}.json`);
  }
}
