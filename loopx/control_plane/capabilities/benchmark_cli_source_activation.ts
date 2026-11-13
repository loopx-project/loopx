/** Explicit, quiescent activation of an already staged CLI release.
 * The host observes its physical writer barrier and persists these decisions.
 * This neither grants execution nor changes work, native history, or clocks.
 */
import type {JsonObject, SettlementIdentityInput} from "../effect_program.ts";
import {settlementIdentity} from "../effect_program.ts";
import {hasExactAuthorityKeys} from "../coordination/authority_store_codec.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {posix} from "node:path";

function sha(value: unknown, label: string): string {
  const text = requireNonEmptyString(value, label);
  if (!BARE_SHA256_PATTERN.test(text)) throw new Error(`${label} must be SHA-256`);
  return text;
}

function denied(reason: string, held: boolean): JsonObject {
  return {qualified: false, reason, source_change_allowed: false,
    resume_allowed: false, reconcile_required: held, execution_authority_granted: false};
}

/** Native/settlement facts come from their existing owners over the paused copy.
 * In particular, a passive app-server is not an active model request. Unknown
 * processes and an unqualified native observation cannot establish idleness.
 */
export function qualifyCliSourceActivation(value: unknown): JsonObject {
  const input = requireJsonObject(value, "CLI source activation");
  if (!hasExactAuthorityKeys(input, ["operation", "scope", "observation"])) {
    throw new Error("invalid CLI source activation fields");
  }
  const scope = requireJsonObject(input.scope, "activation scope");
  if (!hasExactAuthorityKeys(scope, ["activation_id", "container_id", "started_at", "goal_id", "agent_id", "todo_id", "turn_instance_id", "session_id",
    "registry_path", "launcher", "launcher_uid", "launcher_gid", "old_target", "new_target", "release_root", "target_revision", "target_archive_sha256", "worker_lock"])) {
    throw new Error("invalid CLI source activation scope");
  }
  for (const key of ["goal_id", "agent_id", "todo_id", "turn_instance_id", "session_id", "started_at"]) requireNonEmptyString(scope[key], key);
  for (const [key, pattern] of [
    ["activation_id", /^[a-f0-9]{32}$/],
    ["container_id", BARE_SHA256_PATTERN],
    ["target_revision", /^[a-f0-9]{40}$/],
  ] as const) {
    if (!pattern.test(requireNonEmptyString(scope[key], key))) throw new Error(`invalid ${key}`);
  }
  sha(scope.target_archive_sha256, "target source archive");
  for (const key of ["launcher_uid", "launcher_gid"]) {
    if (!Number.isSafeInteger(scope[key]) || (scope[key] as number) < 0) throw new Error(`invalid ${key}`);
  }
  for (const key of ["registry_path", "launcher", "old_target", "new_target", "release_root", "worker_lock"]) {
    const path = requireNonEmptyString(scope[key], key);
    if (!posix.isAbsolute(path) || posix.normalize(path) !== path) throw new Error(`invalid ${key}`);
  }
  const releaseRoot = scope.release_root as string;
  for (const key of ["old_target", "new_target"]) {
    const relative = posix.relative(releaseRoot, scope[key] as string).split("/");
    if (relative.length !== 3 || relative[0] === ".." || relative[1] !== "scripts" || relative[2] !== "loopx") {
      throw new Error("CLI activation must select an owned versioned launcher");
    }
  }
  if (scope.old_target === scope.new_target || (scope.launcher as string).startsWith(releaseRoot + "/")) {
    throw new Error("CLI activation needs distinct releases and a separate launcher");
  }
  const observed = requireJsonObject(input.observation, "activation observation");
  const held = observed.paused === true;
  if (observed.control !== "continue" || observed.source_stopped === true ||
      ((input.operation !== "preflight" || observed.source_control !== undefined) && observed.source_control !== "continue")) {
    return denied(observed.control === "stop" || observed.source_stopped === true || observed.source_control === "stop" ? "stop_requested" : "control_unknown", held);
  }
  if (observed.container_id !== scope.container_id || observed.started_at !== scope.started_at ||
      observed.running !== true) return denied("source_namespace_unverified", held);
  if (input.operation === "preflight") {
    const scheduler = requireJsonObject(observed.scheduler, "source scheduler");
    const unknown = observed.unknown_process_ids;
    const ready = observed.paused === false && observed.ready_present === false &&
      Array.isArray(unknown) && unknown.length === 0 && scheduler.host_pending === false &&
      scheduler.host_attempt === null && scheduler.last_wake_status === "wake_ok" && scheduler.last_wake_rc === 0;
    return {qualified: false, pause_allowed: ready, reason: ready ? "candidate_idle_boundary" : "source_busy_or_unknown",
      source_change_allowed: false, resume_allowed: false, execution_authority_granted: false};
  }
  if (!held || observed.owned_barrier !== true) return denied("writer_barrier_unverified", held);
  if (input.operation === "readback" || input.operation === "repair") {
    const oldTarget = requireNonEmptyString(scope.old_target, "old launcher target");
    const newTarget = requireNonEmptyString(scope.new_target, "new launcher target");
    if (oldTarget === newTarget) throw new Error("CLI source activation needs distinct releases");
    if (observed.unchanged_source_witness !== true) return denied("source_changed_under_barrier", true);
    if (input.operation === "repair") {
      if (observed.qualified_source !== true || !["old", "new"].includes(observed.repair_target as string)) {
        return denied("repair_source_unqualified", true);
      }
      return {qualified: true, source_change_allowed: true, resume_allowed: false,
        repair_target: observed.repair_target, execution_authority_granted: false};
    }
    const target = observed.launcher_target;
    const disposition = target === newTarget ? "new" : target === oldTarget ? "old" : "unknown";
    const identityMatches = observed.launcher_uid === scope.launcher_uid && observed.launcher_gid === scope.launcher_gid &&
      observed.launcher_mode === 0o777;
    const known = identityMatches && (disposition === "old" || (disposition === "new" && observed.qualified_source === true));
    return {qualified: known, disposition, source_change_allowed: false,
      resume_allowed: known, reconcile_required: !known,
      execution_authority_granted: false};
  }
  if (input.operation !== "qualify") throw new Error("unsupported CLI source activation operation");
  if (observed.ready_present !== false) return denied("checkpoint_handoff_pending", true);
  if (observed.launcher_target !== scope.old_target || observed.launcher_uid !== scope.launcher_uid ||
      observed.launcher_gid !== scope.launcher_gid || observed.launcher_mode !== 0o777) return denied("launcher_compare_failed", true);
  if (observed.probe_status !== "ready") return denied("source_quiescence_unavailable", true);
  const unknown = observed.unknown_process_ids;
  if (!Array.isArray(unknown) || unknown.length) return denied("process_quiescence_unknown", true);
  const scheduler = requireJsonObject(observed.scheduler, "source scheduler");
  if (scheduler.host_pending !== false || scheduler.host_attempt !== null ||
      scheduler.last_wake_status !== "wake_ok" || scheduler.last_wake_rc !== 0) return denied("host_turn_pending", true);
  const settlement = requireJsonObject(observed.settlement, "source settlement");
  const identityResult = requireJsonObject(requireJsonObject(settlement.identity, "source identity").result, "source identity result");
  if (identityResult.failure !== null || identityResult.value === null) return denied("settlement_identity_unknown", true);
  const identity = settlementIdentity(requireJsonObject(identityResult.value, "settlement identity") as unknown as SettlementIdentityInput);
  if (identity.goal_id !== scope.goal_id || identity.agent_id !== scope.agent_id ||
      identity.todo_id !== scope.todo_id || identity.turn_instance_id !== scope.turn_instance_id ||
      requireJsonObject(settlement.progress, "source settlement progress").state !== "settled") return denied("original_turn_unsettled", true);
  const native = requireJsonObject(observed.native, "native source cursor");
  if (native.execution_authority_granted !== false || native.model_calls !== 0 ||
      native.quiescent_readback !== true ||
      native.session_id !== scope.session_id || !Array.isArray(native.turn_ids) || !native.turn_ids.length ||
      native.completed_turn_id !== native.turn_ids.at(-1) || native.interrupted_guard !== undefined) return denied("native_cursor_not_idle", true);
  sha(native.visible_history_sha256, "native visible history");
  const before = sha(observed.source_witness_before, "source witness");
  if (sha(observed.source_witness_after, "confirmed source witness") !== before) return denied("source_changed_during_observation", true);
  return {qualified: true, reason: "settled_native_idle_at_owned_barrier", source_change_allowed: true,
    resume_allowed: false, reconcile_required: false, execution_authority_granted: false,
    source_witness_sha256: before, original_settlement_identity: identity,
    native_session_id: native.session_id, native_completed_turn_id: native.completed_turn_id};
}
