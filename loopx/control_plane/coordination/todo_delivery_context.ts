/** Source locks and same-head basis check for Turn result commits.
 * Validation runs between invocations. Provider CAS, not these locks, owns the
 * atomic Todo delta and receipt. No checkpoint provider transaction is nested. */
import {createHash} from "node:crypto";
import {readFileSync} from "node:fs";
import {join, resolve} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {settlementIdentity, settlementIdentityPayload} from "../effect_program.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {withFileMutationLock} from "../effect_runtime_io.ts";
import type {AuthorityStore, AuthorityStoreHead} from "./authority_store.ts";
import {authorityStoreSourceAuthority} from "./authority_store.ts";
import {canonicalAuthoritySha256} from "./authority_store_codec.ts";
import {indexCoordinationProjection} from "./coordination_projection.ts";
import {withCanonicalWriter} from "./local_authority_write.ts";
import {legacyCoordinationTodoLockPath} from "./legacy_writer_lock_paths.ts";
import {checkpointProviderFacts} from "../goals/checkpoint_authority.ts";
import {evaluateCheckpointReadContext, evaluateResultWorkBinding, requireCompleteCheckpointGoalSource} from "../goals/checkpoint_read_context.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {inspectCheckpointReplay} from "../goals/checkpoint_commit.ts";
import {resultFreshnessRequired, heartbeatReceiptFactFromEvent, selectEffectiveHeartbeatReceipt}
  from "../quota/heartbeat_receipt_identity.ts";
import {QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA, readQuotaSettlementFromSnapshot,
  readQuotaSettlementSnapshot} from "../quota/settlement_readback.ts";

const digest = (bytes: Uint8Array): string => createHash("sha256").update(bytes).digest("hex");
function bytes(path: string): Buffer {
  try { return readFileSync(path); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    return Buffer.alloc(0);
  }
}

/** Index -> registry -> maintenance -> Todo projection -> state -> provider CAS. */
export async function withTerminalDeliverySources<T>(
  root: string, goalId: string, input: JsonObject, write: () => Promise<T>,
): Promise<T> {
  if (input.delivery_context == null) return withCanonicalWriter(root, goalId, input.dry_run === true, write);
  const context = requireJsonObject(input.delivery_context, "delivery context");
  const registry = requireJsonObject(input.registry_source, "registry source");
  const state = resolve(requireNonEmptyString(context.state_file, "delivery state file"));
  return withFileMutationLock(join(root, "goals", goalId, "runs", "index.jsonl"), () =>
    withFileMutationLock(requireNonEmptyString(registry.path, "registry path"), () =>
      withCanonicalWriter(root, goalId, false, () =>
        withFileMutationLock(legacyCoordinationTodoLockPath(root, goalId), () =>
          withFileMutationLock(state, write, 30_000), 30_000)), 30_000), 30_000);
}

/** Called only after durable receipt recovery, against the head used for CAS. */
export function terminalDeliveryBasisCheck(root: string, input: JsonObject, store: AuthorityStore):
  ((head: AuthorityStoreHead) => Promise<JsonObject>) | undefined {
  if (input.delivery_context == null) {
    if (input.command !== "complete") return undefined;
    return async head => {
      if (indexCoordinationProjection(head.head, String(input.goal_id)).todos.get(String(input.todo_id))?.role === "user") return {ok: true};
      const snapshot = await readQuotaSettlementSnapshot(root, String(input.goal_id));
      const registrySource = requireJsonObject(input.registry_source, "registry source");
      const document = requireJsonObject(JSON.parse(readFileSync(String(registrySource.path), "utf8")), "registry");
      const registry = jsonObject(document.payload) ?? document;
      const goal = Array.isArray(registry.goals) ? registry.goals.map(jsonObject).find(row => row?.id === input.goal_id) : null;
      const goalRef = registry.profile_id === "source_session_v1"
        ? {goal_id: input.goal_id, goal_instance_id: goal?.goal_instance_id} : null;
      const relevant = snapshot.events.filter(event => event.event_kind === "quota_should_run" &&
        jsonObject(event.details)?.todo_id === input.todo_id &&
        canonicalAuthoritySha256(event.goal_ref ?? null) === canonicalAuthoritySha256(goalRef));
      const original = relevant.filter(event => event.agent_id === input.actor_agent_id &&
        [event.run_id, jsonObject(event.details)?.settlement_effect_id].includes(input.requested_completion_turn_key));
      if (original.length && !original.some(resultFreshnessRequired)) return {ok: true};
      if (relevant.some(resultFreshnessRequired)) return {ok: false,
        error_code: "delivery_work_context_required", reread_required: false,
        error: "This Todo has a protected Turn admission. Restore its original Turn and work validation before completing.",
        next_action: "Resume the admitted Turn; do not omit its identity or create a replacement submission."};
      return {ok: true};
    };
  }
  const context = requireJsonObject(input.delivery_context, "delivery context");
  const binding = requireJsonObject(context.identity, "delivery identity");
  const identity = settlementIdentity({goal_id: String(binding.goal_id), agent_id: String(binding.agent_id),
    todo_id: String(binding.todo_id), turn_instance_id: String(binding.turn_instance_id),
    replan_obligation_id: binding.replan_obligation_id == null ? null : String(binding.replan_obligation_id)});
  const rejected = (code: string, error: string): JsonObject => ({ok: false, error_code: code,
    error, reread_required: true, next_action: "Read delivery_result context for the original Turn; recheck the candidate and validation before retrying."});
  return async head => {
    if (canonicalAuthoritySha256(binding) !== canonicalAuthoritySha256(settlementIdentityPayload(identity)) ||
        identity.goal_id !== input.goal_id || identity.todo_id !== input.todo_id || identity.agent_id !== input.actor_agent_id ||
        ![identity.effect_id, identity.turn_instance_id].includes(String(input.requested_completion_turn_key))) {
      return rejected("delivery_identity_mismatch", "Delivery basis belongs to another Goal, Agent, Todo or Turn.");
    }
    const receiptPath = join(root, "goals", identity.goal_id, "checkpoint-contexts",
      `${createHash("sha256").update(identity.effect_id + ":delivery_result").digest("hex")}.json`);
    if (context.read_context_id == null) {
      // A malformed shared supplement is optional only when the original
      // writeback proves the ordinary path. A first-delivery read cannot be
      // admitted after that writeback; absent history is not such proof.
      const unknown = () => rejected("checkpoint_commit_unknown",
        "Cannot establish optional enrollment from the original Turn; preserve its receipts and reconcile before retrying.");
      try {
        try { readFileSync(receiptPath); return unknown(); }
        catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
        const snapshot = await readQuotaSettlementSnapshot(root, identity.goal_id);
        const readback = readQuotaSettlementFromSnapshot({
          ...binding, schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA, runtime_root: root,
          infer_turn_instance_id: false, allow_unbound_binding: false,
        }, snapshot);
        const prior = jsonObject(readback.writeback_run);
        const writeback = jsonObject(jsonObject(readback.writeback)?.result);
        const purpose = jsonObject(jsonObject(prior?.vision_checkpoint)?.read_context)?.purpose;
        if (prior === null || writeback?.failure !== null ||
            (purpose != null && purpose !== "supplement_checkpoint")) return unknown();
        inspectCheckpointReplay({runtime_root: root, goal_id: identity.goal_id, prior});
        return {ok: true};
      } catch { return unknown(); }
    }
    if (context.capture_error != null) return rejected("delivery_source_unavailable", String(context.capture_error));
    let receipt: JsonObject | null = null;
    try { receipt = jsonObject(JSON.parse(readFileSync(receiptPath, "utf8"))); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    const snapshot = await readQuotaSettlementSnapshot(root, identity.goal_id);
    const goalRef = jsonObject(requireJsonObject(context.facts, "delivery source facts").source)?.goal_ref ?? null;
    const entries = snapshot.events.filter(event => event.event_kind === "quota_should_run" &&
      event.agent_id === identity.agent_id && event.run_id === identity.turn_instance_id &&
      canonicalAuthoritySha256(event.goal_ref ?? null) === canonicalAuthoritySha256(goalRef))
      .map(event => ({fact: heartbeatReceiptFactFromEvent(event), value: event}));
    const required = resultFreshnessRequired(selectEffectiveHeartbeatReceipt(identity.goal_id, identity.agent_id, entries));
    if (required && receipt?.result_only !== true) return rejected("delivery_work_context_required",
      "Restore the default protected Turn's work basis before submitting.");
    const selected = indexCoordinationProjection(head.head, identity.goal_id).todos.get(identity.todo_id!);
    const resultCommitted = required && selected?.status === "done" &&
      [identity.effect_id, identity.turn_instance_id].includes(String(selected.completion_turn_key));
    // Successful result recovery ends its freshness check. A new terminal
    // upgrade still needs the actual Goal source; its own receipt is recovered
    // before this callback and does not need a new source check.
    if (resultCommitted && !(input.requested_no_followup === true && input.defer_no_followup !== true)) {
      return {ok: true};
    }
    if (required && !resultCommitted && (input.validation_receipt != null || input.goal_acceptance_validation_receipts != null)) {
      if (input.result_validation_binding == null) return rejected("delivery_validation_binding_required",
        "Native validation must remain bound to its candidate and original work basis.");
      const validation = evaluateResultWorkBinding({phase: "check_validation", identity: binding,
        receipt, terminal_input: input, binding: input.result_validation_binding});
      if (validation.ok !== true) return validation;
    }
    if (input.requested_no_followup === true && !required) {
      return {...rejected("delivery_no_followup_unsupported",
        "Protected no-follow-up completion is not admitted in this protocol stage; retain the original identity and receipts."),
        reread_required: false, next_action: "This protocol stage supports ordinary result delivery; do not retry an unsupported terminal intent or bypass an unresolved Turn."};
    }
    const facts = requireJsonObject(context.facts, "delivery source facts");
    const authority = authorityStoreSourceAuthority(store);
    if (authority !== "file_v0" && authority !== "sqlite_v0" && !(required && authority === "postgresql_v0")) {
      return rejected("delivery_provider_unsupported", "Delivery freshness requires File or SQLite authority.");
    }
    const source = requireJsonObject(facts.source, "delivery source");
    const state = resolve(requireNonEmptyString(context.state_file, "delivery state file"));
    if (resolve(String(source.state_file)) !== state ||
        digest(bytes(state)) !== context.state_sha256 ||
        digest(bytes(join(root, "goals", identity.goal_id, "runs", "index.jsonl"))) !== context.index_sha256) {
      return rejected("delivery_source_capture_changed", "Source changed while capturing the completion request; retry the same read identity.");
    }
    if (resultCommitted) {
      try { requireCompleteCheckpointGoalSource(facts); }
      catch (error) {
        if (!(error instanceof EffectRuntimeRequestError) || error.code !== "checkpoint_goal_source_incomplete") throw error;
        return {...rejected(error.code, error.message), reread_required: false,
          next_action: "Restore the Goal owner's actual document; resume this original Turn and retain its successful result and quota receipts."};
      }
      return {ok: true};
    }
    const storeId = await store.storeIdentity();
    if (storeId.status !== "available") return rejected("delivery_store_unavailable", "Cannot establish the current authority store identity.");
    const current = checkpointProviderFacts(identity.goal_id, facts, head, storeId.store_identity, authority, true);
    return evaluateCheckpointReadContext({phase: "check", purpose: "delivery_result", identity: binding,
      read_context_id: context.read_context_id, receipt,
      facts: current});
  };
}
