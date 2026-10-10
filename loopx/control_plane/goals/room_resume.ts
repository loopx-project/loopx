import type {JsonObject} from "../effect_program.ts";
import {createHash} from "node:crypto";
import {requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";

/** Compare observations; this read model never accepts work or grants access. */
function quotaKey(value: unknown, goal: string, actor: string): string {
  const q = requireJsonObject(value, "resume quota");
  const identity = requireJsonObject(q.agent_identity, "quota actor");
  const selected = q.selected_todo == null ? {} : requireJsonObject(q.selected_todo, "selected Todo");
  if (q.ok !== true || q.status_health_ok !== true || q.goal_id !== goal || identity.agent_id !== actor ||
      (selected.claimed_by != null && selected.claimed_by !== actor)) throw new Error("resume quota scope unavailable");
  const interaction = requireJsonObject(q.interaction_contract, "quota interaction");
  const agent = requireJsonObject(interaction.agent_channel, "quota agent channel");
  const selectedId = q.selected_todo == null ? null : requireNonEmptyString(selected.todo_id, "selected Todo id");
  return JSON.stringify([selectedId, selected.status ?? null,
    selected.claimed_by ?? null, selected.task_class ?? null, selected.action_kind ?? null,
    q.should_run === true, q.state ?? null, q.effective_action ?? null, agent.must_attempt === true,
    agent.delivery_allowed === true, interaction.mode ?? null]);
}

export function validateRoomResumeInput(value: unknown): JsonObject {
  const p = requireJsonObject(value, "room resume input");
  const goal = requireNonEmptyString(p.goal_id, "goal id");
  const actor = requireNonEmptyString(p.actor_id, "actor id");
  const turn = requireNonEmptyString(p.turn_instance_id, "turn identity");
  const q = requireJsonObject(p.quota, "input quota");
  const receipt = requireJsonObject(q.heartbeat_receipt, "quota receipt");
  requireNonEmptyString(requireJsonObject(q.selected_todo, "admitted selected Todo").todo_id, "admitted Todo id");
  if (q.mode !== "should-run" || receipt.turn_instance_id !== turn ||
      !["committed", "replayed"].includes(String(receipt.status))) throw new Error("resume Turn receipt mismatch");
  const refs = requireStringArray(p.artifact_refs, "requested artifact references");
  if (refs.length > 8 || new Set(refs).size !== refs.length || refs.some(ref =>
    !/^viking:\/\/[A-Za-z0-9_./:-]+$/.test(ref) || ref.split("/").some(part => part === "." || part === ".."))) {
    throw new Error("invalid bounded artifact references");
  }
  const matches = quotaKey(q, goal, actor) === quotaKey(p.current_quota, goal, actor);
  return {schema_version: "loopx_room_resume_input_check_v0", matches,
    reason_code: matches ? null : "quota_observation_changed", execution_authority_granted: false};
}

export function projectRoomResumeReadback(value: unknown): JsonObject {
  const p = requireJsonObject(value, "room resume readback");
  const goal = requireNonEmptyString(p.goal_id, "goal id");
  const actor = requireNonEmptyString(p.actor_id, "actor id");
  const before = requireJsonObject(p.before_projection, "before work projection");
  const after = requireJsonObject(p.after_projection, "after work projection");
  for (const projection of [before, after]) {
    if (projection.goal_id !== goal || projection.actor_id !== actor || projection.authority_state !== "available") {
      throw new Error("resume projection scope mismatch");
    }
  }
  const stable = requireNonEmptyString(before.source_revision, "before revision") ===
    requireNonEmptyString(after.source_revision, "after revision") &&
    requireNonEmptyString(p.before_scope, "before scope") === requireNonEmptyString(p.after_scope, "after scope") &&
    quotaKey(p.before_quota, goal, actor) === quotaKey(p.after_quota, goal, actor);
  const recall = requireJsonObject(p.recall, "recall observation");
  const requested = requireStringArray(p.artifact_refs, "requested references");
  const application = recall.application == null ? {} : requireJsonObject(recall.application, "recall application");
  const receipt = application.receipt == null ? {} : requireJsonObject(application.receipt, "recall receipt");
  const accepted = receipt.memory_ref_digests == null ? [] : requireStringArray(receipt.memory_ref_digests, "accepted reference digests");
  const roots = requireStringArray(p.artifact_scope_refs, "configured artifact scopes");
  // A CLI request cannot turn an arbitrary or remembered pointer into read scope.
  const applied = recall.ok === true && recall.status === "applied" && receipt.outcome === "applied" &&
    receipt.current_artifact_verified === true && receipt.result_readback_verified === true;
  const authorized = stable && applied ? requested.filter(ref =>
      roots.some(root => ref === root || ref.startsWith(root.replace(/\/$/, "") + "/")) &&
      accepted.includes(createHash("sha256").update(ref).digest("hex").slice(0, 16))) : [];
  return {schema_version: "loopx_room_resume_readback_v0", authority_observation_stable: stable,
    context_usable: stable && applied && recall.context != null,
    artifact_references: authorized.map(ref => ({ref, source: "current_scoped_recall", target_access_granted: false})),
    omitted_reference_count: requested.length - authorized.length,
    execution_authority_granted: false, memory_grants_authority: false};
}
