/** AuthorityStore interpreter for the work-items batch. Provider commit and
 * operation receipt share one CAS; display projection is a recoverable effect. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore} from "../coordination/authority_store.ts";
import {authorityStoreSourceAuthority} from "../coordination/authority_store.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthorityObject} from "../coordination/authority_store_codec.ts";
import {CoordinationCommandReceipt} from "../coordination/command_receipt.ts";
import {indexCoordinationProjection, prepareCoordinationProjectionCommit, validateCoordinationTodoReadModel} from "../coordination/coordination_projection.ts";
import {withCanonicalWriter} from "../coordination/local_authority_write.ts";
import {openLocalAuthorityStore, localAuthorityOpenFailure} from "../coordination/local_authority_provider.ts";
import {planTeamTransaction, replayTeamTransaction, teamTransactionIdentity, TEAM_TRANSACTION_SCHEMA} from "./team_plan.ts";
import {isAbsolute} from "node:path";

export async function commitTeamPlan(store: AuthorityStore, request: JsonObject): Promise<JsonObject> {
  const identity = teamTransactionIdentity(request);
  const receipt = new CoordinationCommandReceipt({result_schema: TEAM_TRANSACTION_SCHEMA, identity,
    failure: (reason_code, reason) => ({schema_version: TEAM_TRANSACTION_SCHEMA, status: "failed", reason_code, reason}),
    decode: (original, phase) => {
      const recovered = replayTeamTransaction(request, original);
      return {fields: {result: phase === "applied" ? original.result : recovered}, changed: true};
    }});
  const previous = await receipt.read(store);
  if (previous) return previous;
  const observation = await receipt.observe(store);
  if (observation.kind === "receipt") return observation.result;
  const head = observation.authority;
  if (head.status !== "loaded") return {...head};
  // The canonical revision is included at preview. Stale work cannot be
  // admitted merely because its Markdown projection has not caught up yet.
  if (request.expected_provider_revision != null && request.expected_provider_revision !== head.provider_revision) {
    return {status: "failed", reason_code: "team_plan_preview_stale", reason: "canonical Todo state changed after preview"};
  }
  const projection = indexCoordinationProjection(head.head, String(identity.goal_id));
  validateCoordinationTodoReadModel(head.head, String(identity.goal_id));
  const plan = planTeamTransaction({...request, todos: [...projection.todos.values()],
    read_model_schema: canonicalAuthorityObject(head.head.todo_read_model, "Todo read model").schema_version});
  const todos = plan.todos as JsonObject[];
  if (!todos.length) return {status: "no_change", result: plan.result};
  const commit = prepareCoordinationProjectionCommit({goal_id: String(identity.goal_id), operation_id: String(identity.operation_id),
    expected_provider_revision: head.provider_revision, projection: head.head,
    mutations: todos.map(todo => ({kind: "todo_upsert" as const, todo}))});
  commit.receipts = [canonicalAuthorityObject(plan.receipt, "team plan receipt")];
  return receipt.commit(store, commit);
}

export async function commitLocalTeamPlan(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "team plan commit");
  const root = String(request.runtime_root);
  if (!isAbsolute(root)) throw new Error("runtime_root must be absolute");
  const identity = teamTransactionIdentity(request);
  try {
    return await withCanonicalWriter(root, String(identity.goal_id), false, async () => {
      const store = await openLocalAuthorityStore(root, String(identity.goal_id));
      return {...await commitTeamPlan(store, request), source_authority: authorityStoreSourceAuthority(store),
        decision_read_from_provider: true, legacy_fallback_used: false};
    });
  } catch (error) {
    return {status: "failed", reason_code: error instanceof EffectRuntimeRequestError ? error.code : "team_plan_commit_failed",
      reason: error instanceof Error ? error.message : String(error), ...localAuthorityOpenFailure(error)};
  }
}
