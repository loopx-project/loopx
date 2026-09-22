/** Durable command recovery, owned by coordination rather than by a provider.
 * A receipt proves a historical decision, never current execution authority.
 * Business planners and request hashes remain with their command owners. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreCommit, AuthorityStoreCommitResult,
  AuthorityStoreReceiptResult, AuthorityStoreLoadResult} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject} from "./authority_store_codec.ts";
import {projectionDelivery} from "../todos/projection_delivery.ts";

export type ReceiptPhase = "applied" | "recovered" | "replayed";
interface ReceiptPayload {
  fields: JsonObject;
  changed: boolean;
}
interface CommandReceiptContract<S extends string> {
  result_schema: S;
  identity: JsonObject & {schema_version: string; operation_id: string; goal_id: string; request_sha256: string};
  /** Decode only the command's historical payload, without reading current state. */
  decode(original: JsonObject, phase: ReceiptPhase): ReceiptPayload;
  failure(code: string, reason: string): JsonObject & {schema_version: S};
}
type Result<S extends string> = JsonObject & {schema_version: S};

/** A head and a receipt are separate reads. The receipt observed after the head
 * wins: that head may already include this operation's effects. */
export type CommandObservation<S extends string> =
  | {kind: "receipt"; result: Result<S>}
  | {kind: "authority"; authority: AuthorityStoreLoadResult};

/** One envelope identity, result projection and post-commit state machine for
 * canonical Todo commands. Existing wire schemas and request digests are retained. */
export class CoordinationCommandReceipt<S extends string> {
  readonly contract: CommandReceiptContract<S>;
  constructor(contract: CommandReceiptContract<S>) { this.contract = contract; }

  private project(receipt: AuthorityStoreReceiptResult, phase: ReceiptPhase): Result<S> | null {
    if (receipt.status === "missing") return null;
    const {result_schema, identity, decode, failure} = this.contract;
    if (receipt.status !== "found") return {schema_version: result_schema, ...receipt, changed: false};
    const original = receipt.receipts[0];
    if (receipt.receipts.length !== 1 || !original ||
        Object.entries(identity).some(([key, value]) => original[key] !== value)) {
      return failure("coordination_operation_identity_mismatch",
        "operation id already names a different coordination request");
    }
    let payload: ReceiptPayload;
    try {
      payload = decode(original, phase);
    } catch (error) {
      if (!(error instanceof AuthorityStoreProtocolError)) throw error;
      return failure("invalid_coordination_command_receipt", error.message);
    }
    return {...payload.fields, schema_version: result_schema,
      status: phase === "applied" && !payload.changed ? "no_change" : phase,
      changed: phase !== "replayed" && payload.changed,
      provider_revision: receipt.provider_revision, cursor: receipt.cursor,
      projection_delivery: projectionDelivery(payload.changed),
      projection_source: "committed_authority_journal"};
  }

  async read(store: AuthorityStore): Promise<Result<S> | null> {
    return this.project(await store.readReceipt(this.contract.identity.operation_id), "replayed");
  }

  /** Call after the initial historical lookup and command-specific source gates.
   * Recheck before interpreting the head (including unavailable/stale heads).
   * This is not a transaction or a write retry: a later competing commit remains
   * subject to the provider CAS and normal post-commit receipt recovery. */
  async observe(store: AuthorityStore): Promise<CommandObservation<S>> {
    const authority = await store.loadAuthority();
    const replay = await this.read(store);
    return replay === null ? {kind: "authority", authority} : {kind: "receipt", result: replay};
  }

  async commit(store: AuthorityStore, commit: AuthorityStoreCommit): Promise<Result<S>> {
    const {identity, result_schema, failure} = this.contract;
    if (commit.operation_id !== identity.operation_id) {
      throw new AuthorityStoreProtocolError("commit and receipt operation identities differ");
    }
    // Catch only the effect whose response can be lost after durable acceptance.
    // Never retry the write here, and never turn an exception into no-write proof.
    let committed: AuthorityStoreCommitResult;
    try { committed = await store.commitAuthority(commit); }
    catch {
      committed = {status: "ambiguous", reason_code: "coordination_commit_response_lost",
        reason: "commit response was not received; recover the original operation"};
    }
    let receipt: AuthorityStoreReceiptResult;
    try { receipt = await store.readReceipt(identity.operation_id); }
    catch {
      receipt = {status: "unavailable", reason_code: "coordination_receipt_read_failed",
        reason: "durable receipt read did not complete"};
    }
    if (receipt.status === "found") {
      return this.project(receipt, committed.status === "applied" ? "applied" : "recovered")!;
    }
    if (receipt.status === "missing" && committed.status === "applied") {
      return failure("coordination_commit_readback_mismatch", "applied command lacks its durable receipt");
    }
    if (committed.status === "ambiguous" ||
        (committed.status === "applied" && receipt.status !== "missing")) {
      return {schema_version: result_schema, status: "ambiguous", changed: false,
        reason_code: "coordination_receipt_recovery_required",
        reason: "commit may be durable; recover its receipt using the same operation id",
        commit_status: committed.status, receipt_status: receipt.status,
        recovery: {operation_id: identity.operation_id, retry_with_same_operation_id: true},
        ...(receipt.status === "missing" ? {} : {readback_reason_code: receipt.reason_code})};
    }
    // A conclusive rejection is not erased by a later diagnostic read failure.
    return {schema_version: result_schema, ...committed, changed: false};
  }
}

/** Terminal/archive results persist an explicit change decision. Missing or
 * malformed decisions must not be coerced into a successful no-op. */
export function commandReceiptResult(original: JsonObject): ReceiptPayload {
  const fields = canonicalAuthorityObject(original.result, "command receipt result");
  if (typeof fields.changed !== "boolean") {
    throw new AuthorityStoreProtocolError("command receipt result.changed must be boolean");
  }
  return {fields: {...fields, original_receipt: original}, changed: fields.changed};
}
