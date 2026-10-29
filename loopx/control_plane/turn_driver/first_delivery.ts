/** Receipt-backed delivery observation; never settlement or direction authority. */
import type {JsonObject} from "../effect_program.ts";
import {settlementIdentityFromPlan, SETTLEMENT_IDENTITY_SCHEMA_VERSION, SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION} from "../effect_program.ts";
import {createHash} from "node:crypto";
import {readFileSync, readdirSync} from "node:fs";
import {basename, join} from "node:path";
import {jsonObject, requireJsonObject} from "../runtime_decode.ts";
import {originalTurnRequestsNoFollowup} from "../todos/completion_state.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {inspectCheckpointReadHistoryFromRows, readCheckpointHistoryRows} from "../goals/checkpoint_commit.ts";
import {checkpointContinuationRequired} from "../quota/heartbeat_receipt_identity.ts";
import {parseQuotaAccountingOwner, withQuotaAccountingOwner} from "../quota/source_admission.ts";
import {QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA, readQuotaSettlementForAdmittedOwnerFromSnapshot,
  readQuotaSettlementSnapshot, validateQuotaSettlementScope} from "../quota/settlement_readback.ts";

function receiptName(identity: JsonObject, work = false): string {
  return createHash("sha256").update(String(identity.effect_id) + (work ? ":delivery_result" : "")).digest("hex") + ".json";
}

/** Shared snapshots, original identities and disposable read proofs. Observation
 * cannot repair receipts, spend quota, rearm a Host or select subsequent work. */
export async function observeFirstDelivery(value: unknown): Promise<JsonObject | null> {
  const request = requireJsonObject(value, "delivery observation");
  const {runtimeRoot: root, goalId: goal} = validateQuotaSettlementScope(request.runtime_root, request.goal_id);
  const owner = parseQuotaAccountingOwner({runtimeRoot: root, goalId: goal,
    goalRefValue: request.goal_ref, sourceAdmissionValue: request.source_admission});
  return withQuotaAccountingOwner(owner, async () => {
    const identities = new Map<string, JsonObject>();
    const receipts = new Map<string, JsonObject>();
    let errors: JsonObject[] = [];
    const directory = join(root, "goals", goal, "checkpoint-contexts");
    let names: string[] = [];
    try { names = readdirSync(directory).filter(name => name.endsWith(".json")); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    if (request.receipt_names !== undefined) {
      if (!Array.isArray(request.receipt_names) || request.receipt_names.some(name =>
        typeof name !== "string" || basename(name) !== name || !name.endsWith(".json"))) throw new TypeError("invalid receipt scan");
      names = request.receipt_names as string[];
    }
    for (const name of names) {
      let receipt: JsonObject;
      try { receipt = requireJsonObject(JSON.parse(readFileSync(join(directory, name), "utf8")), "checkpoint receipt"); }
      catch { errors.push({scope: "goal", code: "checkpoint_receipt_unavailable", receipt: name.slice(0, -5)}); continue; }
      receipts.set(name, receipt);
      if (!["first_delivery", "delivery_result"].includes(String(receipt.purpose))) continue;
      const binding = jsonObject(receipt.identity);
      if (typeof binding?.agent_id === "string" && binding.agent_id && request.agent_id != null && binding.agent_id !== request.agent_id) continue;
      try {
        if (!binding) throw new TypeError("missing identity");
        const decoded = settlementIdentityFromPlan({settlement_plan: {identity: binding}});
        if (decoded.failure !== null || ![SETTLEMENT_IDENTITY_SCHEMA_VERSION, SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION]
            .includes(String(binding.schema_version))) throw new TypeError("invalid identity");
        const identity = binding; // Retain original facts; do not normalize historical metadata away.
        if (receipt.schema_version !== "checkpoint_read_context_v2" || identity.goal_id !== goal ||
            receiptName(identity, receipt.purpose === "delivery_result") !== name) throw new TypeError("invalid binding");
        identities.set(String(identity.effect_id), identity);
      } catch { errors.push({scope: "goal", code: "checkpoint_receipt_invalid", receipt: name.slice(0, -5)}); }
    }
    // The ordinary settlement parser and strict checkpoint parser intentionally
    // differ on incomplete tails. Capture each once; never mix changed indexes.
    let rows: JsonObject[] | null = null;
    try { rows = readCheckpointHistoryRows(root, goal); }
    catch { errors.push({scope: "goal", code: "checkpoint_history_unavailable"}); }
    const verifiedDirections = new Set<string>();
    if (rows !== null) {
      let verified: JsonObject[] = [];
      try {
        const recovered = inspectCheckpointReadHistoryFromRows({runtime_root: root, goal_id: goal, agent_id: request.agent_id,
          receipt_names: errors.filter(error => error.receipt != null).map(error => String(error.receipt) + ".json")}, rows);
        verified = recovered.verified as JsonObject[];
      } catch { errors.push({scope: "goal", code: "checkpoint_history_unavailable"}); }
      for (const item of verified) {
        errors = errors.filter(error => String(error.receipt) + ".json" !== item.receipt_name);
        const identity = requireJsonObject(item.identity, "verified checkpoint identity");
        if (request.agent_id == null || identity.agent_id === request.agent_id) {
          identities.set(String(identity.effect_id), identity);
          if (item.purpose === "first_delivery") verifiedDirections.add(String(identity.effect_id));
        }
      }
    }
    let snapshot: Awaited<ReturnType<typeof readQuotaSettlementSnapshot>> | null = null;
    if (identities.size) {
      try { snapshot = await readQuotaSettlementSnapshot(root, goal); }
      catch { errors.push({scope: "goal", code: "checkpoint_history_unavailable"}); }
      if (request.todos == null) errors.push({scope: "goal", code: "checkpoint_authority_unavailable"});
    }
    const changedIndex = rows !== null && snapshot !== null &&
      canonicalAuthoritySha256(rows) !== canonicalAuthoritySha256(snapshot.runs);
    const todos = new Map<unknown, JsonObject>();
    for (const value of Array.isArray(request.todos) ? request.todos : []) {
      const todo = requireJsonObject(value, "observed Todo");
      if (!todos.has(todo.todo_id)) todos.set(todo.todo_id, todo);
    }
    for (const identity of [...identities.values()].reverse()) {
      let progress: JsonObject;
      try {
        if (snapshot === null || request.todos == null) throw new Error("observation source unavailable");
        const readback = readQuotaSettlementForAdmittedOwnerFromSnapshot({schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
          runtime_root: root, goal_id: goal, agent_id: identity.agent_id, todo_id: identity.todo_id || null,
          turn_instance_id: identity.turn_instance_id, replan_obligation_id: identity.replan_obligation_id ?? null,
          infer_turn_instance_id: false, allow_unbound_binding: false}, owner, snapshot);
        if (readback.found !== true || jsonObject(jsonObject(readback.identity)?.result)?.value == null) throw new Error("identity unavailable");
        const continuation = checkpointContinuationRequired(jsonObject(readback.heartbeat_receipt));
        const direction = receipts.get(receiptName(identity));
        const work = receipts.get(receiptName(identity, true));
        if (!verifiedDirections.has(String(identity.effect_id)) && !continuation &&
            direction?.purpose !== "first_delivery" && !(work?.purpose === "delivery_result" && work.result_only !== true)) continue;
        progress = evaluateFirstDelivery({phase: "project", identity, completion_todo: todos.get(identity.todo_id) ?? {},
          checkpoint_continuation: continuation, completion_event: readback.completion_event,
          writeback_run: readback.writeback_run, direction_receipt: direction,
          unknown: changedIndex || errors.some(error => [receiptName(identity), receiptName(identity, true)].includes(String(error.receipt) + ".json")),
          quota_spent: readback.spend_run != null,
          settlement_complete: jsonObject(jsonObject(readback.settlement)?.result)?.failure === null,
          terminal_settlement_complete: jsonObject(jsonObject(readback.terminal_settlement)?.result)?.failure === null});
      } catch { progress = evaluateFirstDelivery({phase: "project", unknown: true}); }
      if (progress.stage !== "settled") return {...progress, settlement_identity: identity,
        ...(errors.length ? {observation_errors: errors} : {})};
    }
    return errors.length ? {...evaluateFirstDelivery({phase: "project", observation_unavailable: true}),
      goal_id: goal, agent_id: request.agent_id ?? null, observation_errors: errors} : null;
  });
}

export function evaluateFirstDelivery(value: unknown): JsonObject {
  const request = requireJsonObject(value, "first delivery");
  if (request.phase === "project") {
    if (request.observation_unavailable === true) {
      return {schema_version: "first_delivery_progress_v0", stage: "observation_unavailable",
        goal_completion_certified: false,
        next_action: "Inspect unavailable checkpoint observations before resuming the affected Turn."};
    }
    const checkpoint = jsonObject(jsonObject(request.writeback_run)?.vision_checkpoint);
    const completion = jsonObject(request.completion_event);
    const completionTodo = jsonObject(request.completion_todo);
    const identity = jsonObject(request.identity);
    const resultCommitted = request.result_committed === true || (completionTodo?.status === "done" && identity !== null &&
      [identity.effect_id, identity.turn_instance_id].includes(completionTodo.completion_turn_key)) ||
      ["completed", "terminal_no_followup", "receipt_repaired"].includes(String(completion?.status));
    const outcomeRetained = checkpoint?.required === true && checkpoint.satisfied === false;
    const direction = jsonObject(request.direction_receipt);
    const notRequired = checkpoint?.decision === "not_required" && checkpoint.required === false && checkpoint.satisfied === true;
    const directionCommitted = checkpoint?.satisfied === true &&
      (jsonObject(checkpoint.read_context)?.purpose === "first_delivery" ||
        (request.checkpoint_continuation === true && checkpoint.read_context != null));
    const terminalRequired = completionTodo !== null && identity !== null &&
      originalTurnRequestsNoFollowup(completionTodo, identity);
    const settlementComplete = request.settlement_complete === true &&
      (!terminalRequired || request.terminal_settlement_complete === true);
    const unknown = request.unknown === true ||
      (jsonObject(request.direction_receipt)?.commit_attempt != null && !directionCommitted);
    const stage = unknown ? "operation_unknown"
      : !resultCommitted && !outcomeRetained && !directionCommitted && !notRequired ? "result_review_pending"
      : !directionCommitted && direction?.direction_stopped === true ? "direction_stopped"
      : !directionCommitted && direction?.direction_host_failure != null ? "direction_failed"
      : !directionCommitted && !notRequired ? "direction_pending"
      : settlementComplete ? "settled" : "settlement_pending";
    return {schema_version: "first_delivery_progress_v0", stage,
      result_committed: resultCommitted,
      outcome_retained: outcomeRetained || directionCommitted,
      direction_committed: directionCommitted,
      direction_required: !notRequired,
      quota_spent: request.quota_spent === true,
      goal_completion_certified: false,
      next_action: stage === "operation_unknown" ? "Read back the original Turn and its run artifacts; retain its identity."
        : stage === "result_review_pending" ? "Read the original Turn's result basis and validate the candidate before committing it."
        : stage === "direction_stopped" ? "Result retained; the current Agent stopped. Inspect the original Turn before explicitly resuming."
        : stage === "direction_failed" ? "Result retained; retry the original Turn's direction using the existing Host recovery policy."
        : stage === "direction_pending" ? "Result retained; review the current direction using the original Turn, then resume its remaining settlement."
        : stage === "settlement_pending" ? "Resume the original Turn's remaining settlement; retain successful writeback and quota receipts."
        : "Turn settlement is complete. Read the current Goal obligations before selecting further work."};
  }
  throw new TypeError("Delivery observation requires the project phase");
}
