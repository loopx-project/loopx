/** AuthorityStore interpreter for the work-items batch. Provider commit and
 * operation receipt share one CAS; display projection is a recoverable effect. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore} from "../coordination/authority_store.ts";
import {authorityStoreSourceAuthority} from "../coordination/authority_store.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {canonicalAuthorityObject} from "../coordination/authority_store_codec.ts";
import {CoordinationCommandReceipt} from "../coordination/command_receipt.ts";
import {indexCoordinationProjection, prepareCoordinationProjectionCommit, validateCoordinationTodoReadModel} from "../coordination/coordination_projection.ts";
import {openLocalAuthorityStore, localAuthorityOpenFailure, requireLocalAuthorityRuntimeRoot} from "../coordination/local_authority_provider.ts";
import {type AuthoritySourceCheck, uncheckedAuthoritySource} from "../coordination/authority_source.ts";
import {legacyCoordinationTodoLockPath} from "../coordination/legacy_writer_lock_paths.ts";
import {requireShadowPrimaryWriteAllowed, shadowMaintenanceLockPath} from "../coordination/shadow_management.ts";
import {claimFileMutationLock, mutationLockOwner, releaseFileMutationLock,
  releaseFileMutationLockClaim, type FileMutationLockClaim} from "../effect_runtime_io.ts";
import {planTeamTransaction, replayTeamTransaction, teamTransactionIdentity, TEAM_TRANSACTION_SCHEMA} from "./team_plan.ts";
import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import {isAbsolute, resolve} from "node:path";

export async function commitTeamPlan(store: AuthorityStore, request: JsonObject,
  sourceCheck: AuthoritySourceCheck = uncheckedAuthoritySource): Promise<JsonObject> {
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
  if (!await sourceCheck()) {
    return {status: "failed", reason_code: "team_plan_preview_stale", reason: "team plan sources changed after preview"};
  }
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
  const commit = prepareCoordinationProjectionCommit({goal_id: String(identity.goal_id), operation_id: String(identity.operation_id),
    expected_provider_revision: head.provider_revision, projection: head.head,
    mutations: todos.map(todo => ({kind: "todo_upsert" as const, todo}))});
  commit.receipts = [canonicalAuthorityObject(plan.receipt, "team plan receipt")];
  return receipt.commit(store, commit);
}

export async function commitLocalTeamPlan(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "team plan commit");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const identity = teamTransactionIdentity(request);
  const goal = String(identity.goal_id);
  const claims: {target: string; token: string; claim: FileMutationLockClaim}[] = [];
  let adopted = false;
  try {
    const source = requireJsonObject(request.source, "team plan source");
    const registry = requireNonEmptyString(source.registry, "registry");
    const state = requireNonEmptyString(source.state_file, "state_file");
    if (!isAbsolute(registry) || !isAbsolute(state) || resolve(registry) === resolve(state)) {
      throw new EffectRuntimeRequestError("team plan source paths must be distinct absolute paths");
    }
    const targets = [shadowMaintenanceLockPath(root, goal), legacyCoordinationTodoLockPath(root, goal),
      state, registry].map(path => resolve(path));
    if (!Array.isArray(request.locks) || request.locks.length !== targets.length) {
      throw new EffectRuntimeRequestError("team plan requires the complete source lock handoff");
    }
    const witnesses = request.locks.map((raw, i) => {
      const witness = requireJsonObject(raw, "team plan lock witness");
      if (witness.target !== targets[i]) throw new EffectRuntimeRequestError("team plan lock target mismatch");
      return {target: targets[i]!, token: requireNonEmptyString(witness.token, "lock token"), pid: witness.pid};
    });
    for (const [i, witness] of witnesses.entries()) {
      const claim = await claimFileMutationLock(witness.target, witness.token);
      if (!claim) break;
      claims.push({...witness, claim});
      const owner = await mutationLockOwner(witness.target);
      if (owner?.token !== witness.token || owner.pid !== witness.pid) break;
      if (i === witnesses.length - 1) adopted = true;
    }
    const store = await openLocalAuthorityStore(root, goal);
    const result = await commitTeamPlan(store, request, async () => {
      // Exact committed receipts replay before these admission checks. An
      // expired handoff may read history, but can never authorize a new write.
      if (!adopted) throw new EffectRuntimeRequestError("team plan source lock handoff expired; retry the original confirmation");
      await requireShadowPrimaryWriteAllowed(root, goal);
      const digest = async (path: string) => createHash("sha256").update(await readFile(path)).digest("hex");
      return await digest(registry) === source.registry_sha256 && await digest(state) === source.state_sha256;
    });
    return {...result, source_authority: authorityStoreSourceAuthority(store),
      decision_read_from_provider: true, legacy_fallback_used: false};
  } catch (error) {
    return {status: "failed", reason_code: error instanceof EffectRuntimeRequestError ? error.code : "team_plan_commit_failed",
      reason: error instanceof Error ? error.message : String(error), ...localAuthorityOpenFailure(error)};
  } finally {
    // Claims keep the source markers alive even after a requesting Python
    // process exits. The native effect releases the handed-off critical section.
    for (const entry of claims.reverse()) {
      if (adopted) await releaseFileMutationLock(entry.target, entry.token, entry.claim, true);
      else await releaseFileMutationLockClaim(entry.claim);
    }
  }
}
