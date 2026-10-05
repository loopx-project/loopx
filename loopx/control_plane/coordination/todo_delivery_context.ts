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
import {withCanonicalWriter} from "./local_authority_write.ts";
import {legacyCoordinationTodoLockPath} from "./legacy_writer_lock_paths.ts";
import {checkpointProviderFacts} from "../goals/checkpoint_authority.ts";
import {evaluateCheckpointReadContext} from "../goals/checkpoint_read_context.ts";
import {inspectCheckpointReplay} from "../goals/checkpoint_commit.ts";

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
  if (input.delivery_context == null) return undefined;
  const context = requireJsonObject(input.delivery_context, "delivery context");
  const binding = requireJsonObject(context.identity, "delivery identity");
  const identity = settlementIdentity({goal_id: String(binding.goal_id), agent_id: String(binding.agent_id),
    todo_id: String(binding.todo_id), turn_instance_id: String(binding.turn_instance_id),
    replan_obligation_id: binding.replan_obligation_id == null ? null : String(binding.replan_obligation_id)});
  const rejected = (code: string, error: string): JsonObject => ({ok: false, error_code: code,
    error, reread_required: true, next_action: "Read delivery_result context for the original Turn; recheck the candidate and validation before retrying."});
  return async head => {
    if (context.capture_error != null) return rejected("delivery_source_unavailable", String(context.capture_error));
    const facts = requireJsonObject(context.facts, "delivery source facts");
    if (canonicalAuthoritySha256(binding) !== canonicalAuthoritySha256(settlementIdentityPayload(identity)) ||
        identity.goal_id !== input.goal_id || identity.todo_id !== input.todo_id || identity.agent_id !== input.actor_agent_id ||
        ![identity.effect_id, identity.turn_instance_id].includes(String(input.requested_completion_turn_key))) {
      return rejected("delivery_identity_mismatch", "Delivery basis belongs to another Goal, Agent, Todo or Turn.");
    }
    const authority = authorityStoreSourceAuthority(store);
    if (authority !== "file_v0" && authority !== "sqlite_v0") {
      return rejected("delivery_provider_unsupported", "Delivery freshness requires File or SQLite authority.");
    }
    const source = requireJsonObject(facts.source, "delivery source");
    const state = resolve(requireNonEmptyString(context.state_file, "delivery state file"));
    if (resolve(String(source.state_file)) !== state ||
        digest(bytes(state)) !== context.state_sha256 ||
        digest(bytes(join(root, "goals", identity.goal_id, "runs", "index.jsonl"))) !== context.index_sha256) {
      return rejected("delivery_source_capture_changed", "Source changed while capturing the completion request; retry the same read identity.");
    }
    const storeId = await store.storeIdentity();
    if (storeId.status !== "available") return rejected("delivery_store_unavailable", "Cannot establish the current authority store identity.");
    const receiptPath = join(root, "goals", identity.goal_id, "checkpoint-contexts",
      `${createHash("sha256").update(identity.effect_id + ":delivery_result").digest("hex")}.json`);
    let receipt: JsonObject | null = null;
    try { receipt = jsonObject(JSON.parse(readFileSync(receiptPath, "utf8"))); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    const current = checkpointProviderFacts(identity.goal_id, facts, head, storeId.store_identity, authority, true);
    if (input.requested_no_followup === true) {
      const directionId = context.direction_read_context_id;
      if (typeof directionId !== "string") return rejected("delivery_direction_receipt_required", "Terminal closeout requires this Turn's committed direction read identity.");
      const directionPath = join(root, "goals", identity.goal_id, "checkpoint-contexts",
        `${createHash("sha256").update(identity.effect_id).digest("hex")}.json`);
      const direction = requireJsonObject(JSON.parse(readFileSync(directionPath, "utf8")), "direction receipt");
      const prior = requireJsonObject(requireJsonObject(direction.commit_attempt, "committed attempt").index_record, "direction run");
      inspectCheckpointReplay({runtime_root: root, goal_id: identity.goal_id, prior});
      const committedContext = requireJsonObject(requireJsonObject(prior.vision_checkpoint, "direction checkpoint").read_context, "committed direction context");
      if (direction.read_context_id !== directionId || committedContext.read_context_id !== directionId || direction.decision_scope !== "goal") {
        return rejected("delivery_direction_receipt_mismatch", "Terminal closeout requires the exact committed Goal-scope direction.");
      }
      const check = evaluateCheckpointReadContext({phase: "check", purpose: "first_delivery", identity: binding,
        read_context_id: directionId, decision_scope: "goal", facts: current,
        receipt: {...direction, commit_attempt: null, versions: committedContext.terminal_versions}});
      if (check.ok !== true) return check;
      // Deferred terminal paths have not committed the result yet. Preserve its
      // original candidate basis as well, allowing only this Turn's own Vision
      // which was just proven from its indexed direction receipt.
      const selected = (current.todos as JsonObject[]).find(todo => todo.todo_id === identity.todo_id);
      if (selected?.status !== "done") {
        return evaluateCheckpointReadContext({phase: "check", purpose: "delivery_result", identity: binding,
          read_context_id: context.read_context_id, facts: current, receipt: receipt === null ? null : {
            ...receipt, versions: {...requireJsonObject(receipt.versions, "result basis versions"),
              agent_vision: canonicalAuthoritySha256(current.agent_vision)},
          }});
      }
      return check;
    }
    return evaluateCheckpointReadContext({phase: "check", purpose: "delivery_result", identity: binding,
      read_context_id: context.read_context_id, receipt,
      facts: current});
  };
}
