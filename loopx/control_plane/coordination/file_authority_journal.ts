/** File's physical journal codec. Logical revisions, receipts and transactions
 * stay unchanged; only repeated projections become checkpoints and deltas. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStoreCommit, AuthorityStoreCommittedTransaction} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityBytes, canonicalAuthorityObject,
  hasExactAuthorityKeys, isAuthorityJsonObject, parseAuthorityCursor,
  requireAuthorityStoreId} from "./authority_store_codec.ts";
import {decodeAuthorityTransaction, transactionForRevision,
  type JournalRevision} from "./authority_store_transactions.ts";
import {applyAuthorityStateDelta, authorityStateCheckpointCursor, authorityStateDelta,
  decodeAuthorityStateDelta, authorityStateDeltaReconstructs, isAuthorityStateCheckpoint, type AuthorityStateDelta} from "./authority_state_log.ts";

export const FILE_AUTHORITY_JOURNAL_SCHEMA = "loopx_file_authority_store_v1";
type StoredState = {kind: "checkpoint"; projection: JsonObject} |
  {kind: "delta"; delta: AuthorityStateDelta};
type StoredCommit = Omit<AuthorityStoreCommittedTransaction, "projection"> & {state: StoredState};
type TransactionMetadata = Omit<AuthorityStoreCommittedTransaction, "projection">;
type History = {rows: readonly StoredCommit[]};
const HEADER_KEYS = ["schema_version", "goal_id", "store_identity", "provider_revision", "cursor", "head", "committed"];

function invalid(message: string): never {
  throw new AuthorityStoreProtocolError(`file authority store ${message}`);
}

function storedState(value: unknown, cursor: bigint): StoredState {
  if (!isAuthorityJsonObject(value)) return invalid("state is invalid");
  if (isAuthorityStateCheckpoint(cursor)) {
    if (value.kind !== "checkpoint" || !hasExactAuthorityKeys(value, ["kind", "projection"])) {
      return invalid("checkpoint is missing or invalid");
    }
    return {kind: "checkpoint", projection: canonicalAuthorityObject(value.projection, "file checkpoint")};
  }
  if (value.kind !== "delta" || !hasExactAuthorityKeys(value, ["kind", "delta"])) {
    return invalid("delta is missing or invalid");
  }
  return {kind: "delta", delta: decodeAuthorityStateDelta(value.delta)};
}

function project(state: StoredState, previous: JsonObject | null): JsonObject {
  if (state.kind === "checkpoint") return state.projection;
  if (previous === null) return invalid("delta has no predecessor");
  return applyAuthorityStateDelta(previous, state.delta);
}

function retain(transaction: AuthorityStoreCommittedTransaction, previous: JsonObject | null): StoredCommit {
  const {projection, ...metadata} = transaction;
  if (isAuthorityStateCheckpoint(parseAuthorityCursor(transaction.cursor))) {
    return {...metadata, state: {kind: "checkpoint", projection}};
  }
  if (previous === null) return invalid("retained delta has no predecessor");
  const delta = authorityStateDelta(previous, projection);
  if (!authorityStateDeltaReconstructs(previous, delta, projection)) return invalid("delta reconstruction mismatch");
  return {...metadata, state: {kind: "delta", delta}};
}

/** Only verified or locally committed rows enter this object. V1 retains compact
 * history. Old formats are accepted only by the explicit migration owner. */
export class FileAuthorityJournal {
  readonly goal_id: string;
  readonly store_identity: string;
  readonly head: JsonObject;
  readonly cursor: string;
  readonly provider_revision: string;
  private readonly history: History;
  private readonly operations: ReadonlyMap<string, TransactionMetadata>;

  private constructor(goal: string, identity: string, head: JsonObject, history: History) {
    const rows = history.rows;
    this.goal_id = goal; this.store_identity = identity; this.head = head;
    this.history = history;
    this.cursor = rows.at(-1)!.cursor;
    this.provider_revision = rows.at(-1)!.provider_revision;
    this.operations = new Map(rows.map(row => [row.operation_id, row]));
  }

  static decode(value: unknown, goal: string, identity: string, revisionFor: JournalRevision): FileAuthorityJournal {
    if (!isAuthorityJsonObject(value) || !hasExactAuthorityKeys(value, HEADER_KEYS) ||
        value.schema_version !== FILE_AUTHORITY_JOURNAL_SCHEMA) {
      return invalid("schema mismatch; run loopx authority-archive upgrade --execute before opening this store");
    }
    if (value.goal_id !== goal) return invalid("goal mismatch");
    if (value.store_identity !== identity) return invalid("lineage mismatch");
    const cursor = requireAuthorityStoreId(value.cursor, "provider cursor");
    const revision = requireAuthorityStoreId(value.provider_revision, "provider revision");
    const head = canonicalAuthorityObject(value.head, "file authority store head");
    if (!Array.isArray(value.committed) || value.committed.length === 0 ||
        parseAuthorityCursor(cursor) !== BigInt(value.committed.length)) return invalid("lineage is invalid");
    const rows: StoredCommit[] = [], operations = new Set<string>();
    let previous: JsonObject | null = null, previousRevision: string | null = null;
    for (const [index, raw] of value.committed.entries()) {
      if (!isAuthorityJsonObject(raw) || !hasExactAuthorityKeys(raw,
        ["cursor", "provider_revision", "operation_id", "events", "receipts", "state"])) {
        return invalid("committed transaction is invalid");
      }
      const {state: rawState, ...metadata} = raw;
      const state = storedState(rawState, BigInt(index + 1));
      const transaction = decodeAuthorityTransaction({...metadata, projection: project(state, previous)});
      if (parseAuthorityCursor(transaction.cursor) !== BigInt(index + 1)) return invalid("cursor lineage is invalid");
      if (operations.has(transaction.operation_id)) return invalid("operation identity is duplicated");
      if (transaction.provider_revision !== revisionFor(previousRevision, transactionForRevision(transaction))) {
        return invalid("revision lineage is invalid");
      }
      operations.add(transaction.operation_id);
      const {projection, ...entry} = transaction;
      rows.push({...entry, state});
      previous = projection; previousRevision = transaction.provider_revision;
    }
    if (rows.at(-1)!.cursor !== cursor || previousRevision !== revision ||
        !canonicalAuthorityBytes(previous).equals(canonicalAuthorityBytes(head))) return invalid("head lineage is invalid");
    return new FileAuthorityJournal(goal, identity, head, {rows});
  }

  static append(current: FileAuthorityJournal | null, goal: string, identity: string,
    commit: AuthorityStoreCommit, revisionFor: JournalRevision): FileAuthorityJournal {
    const transaction = {cursor: (parseAuthorityCursor(current?.cursor ?? null) + 1n).toString(),
      operation_id: commit.operation_id, events: commit.events, projection: commit.next_projection,
      receipts: commit.receipts};
    const row = retain({...transaction,
      provider_revision: revisionFor(current?.provider_revision ?? null, transaction)}, current?.head ?? null);
    return new FileAuthorityJournal(goal, identity, commit.next_projection,
      {rows: [...(current?.history.rows ?? []), row]});
  }

  /** Migration boundary: callers supply fully verified logical transactions.
   * Decode the resulting wire format again before it can be adopted. */
  static fromTransactions(goal: string, identity: string, rows: readonly AuthorityStoreCommittedTransaction[],
    revisionFor: JournalRevision): FileAuthorityJournal {
    let previous: JsonObject | null = null;
    const compact = rows.map(transaction => {
      const encoded = retain(transaction, previous);
      previous = transaction.projection;
      return encoded;
    });
    const journal = new FileAuthorityJournal(goal, identity, rows.at(-1)!.projection, {rows: compact});
    return FileAuthorityJournal.decode(journal.toDocument(), goal, identity, revisionFor);
  }

  receiptEntries(): Iterable<TransactionMetadata> {
    return this.operations.values();
  }

  receipt(operation: string): TransactionMetadata | undefined {
    const row = this.operations.get(operation);
    if (!row) return undefined;
    return {cursor: row.cursor, provider_revision: row.provider_revision,
      operation_id: row.operation_id, events: row.events, receipts: row.receipts};
  }

  /** At most 63 predecessor deltas plus the requested page (and lookahead).
   * Full-file integrity validation still happens before this verified view. */
  scan(offset: number, limit: number): AuthorityStoreCommittedTransaction[] {
    const {rows} = this.history;
    if (offset >= rows.length) return [];
    const checkpoint = Number(authorityStateCheckpointCursor(BigInt(offset + 1))) - 1;
    const end = Math.min(rows.length, offset + limit);
    const result: AuthorityStoreCommittedTransaction[] = [];
    let previous: JsonObject | null = null;
    for (let i = checkpoint; i < end; i++) {
      const {state, ...metadata} = this.history.rows[i]!;
      previous = project(state, previous);
      if (i >= offset) result.push({...metadata, projection: previous});
    }
    return result;
  }

  toDocument(): JsonObject {
    return {schema_version: FILE_AUTHORITY_JOURNAL_SCHEMA, goal_id: this.goal_id,
      store_identity: this.store_identity, provider_revision: this.provider_revision,
      cursor: this.cursor, head: this.head, committed: this.history.rows};
  }
}
