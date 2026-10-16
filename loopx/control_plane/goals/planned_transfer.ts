/** Goal-owned staging for a prepared transfer, deliberately before admission.
 *
 * Ordinary bootstrap owns fresh, empty creation. This owner retains the original
 * Goal/Agent context and proposes a stopped destination registration over its
 * original history. Historical policy, credentials, Host ids and memberships
 * are review input; none is installed as a current destination grant here.
 */
import {isAbsolute, join, relative, resolve, sep} from "node:path";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {fileURLToPath} from "node:url";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys,
  requireAuthorityStoreId} from "../coordination/authority_store_codec.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {withFileMutationLock} from "../effect_runtime_io.ts";
import {legacyCoordinationTodoLockPath} from "../coordination/legacy_writer_lock_paths.ts";

export const PLANNED_TRANSFER_GOAL_STAGE_SCHEMA = "loopx_planned_transfer_goal_stage_v0";
const executeFile = promisify(execFile);

function reject(message: string): never { throw new EffectRuntimeConflictError(message); }
function absolute(value: unknown, label: string): string {
  const path = requireAuthorityStoreId(value, label);
  if (!isAbsolute(path) || resolve(path) !== path) reject(`${label} must be an absolute normalized path`);
  return path;
}
function overlaps(a: string, b: string): boolean {
  const below = (root: string, path: string) => {
    const suffix = relative(root, path);
    return suffix === "" || (!isAbsolute(suffix) && suffix !== ".." && !suffix.startsWith(`..${sep}`));
  };
  return below(a, b) || below(b, a);
}

export interface PreparedGoalStage {
  goalId: string;
  operationId: string;
  context: JsonObject;
  goal: JsonObject;
  statePath: string;
  contextPath: string;
  identity: JsonObject;
}

/** Capture Goal intent through its actual registered source while the caller
 * holds maintenance. Read the registered Goal through the existing Python
 * registry codec using the source-selected interpreter, never another registry
 * parser. Python owns codec/byte IO; Goal identity and profile decisions remain
 * here. The lifecycle-only profile cannot become a generic runtime grant. */
export async function withVerifiedGoalTransferContext<T>(raw: unknown, rawWitness: unknown,
  goalId: string, root: string, operation: (context: JsonObject) => Promise<T>): Promise<T> {
  const context = requireJsonObject(raw, "original Goal context");
  if (!hasExactAuthorityKeys(context, ["goal", "state_text", "runtime_root", "registry_path"]) ||
      context.runtime_root !== root || typeof context.state_text !== "string" || !context.state_text.trim()) reject("Complete registered Goal context is required");
  const goal = requireJsonObject(context.goal, "original Goal");
  if (goal.id !== goalId || goal.goal_instance_id !== undefined) reject("Original Goal identity requires its owning lifecycle");
  const project = absolute(goal.repo, "source project"), registry = absolute(context.registry_path, "source registry");
  const state = resolve(project, requireAuthorityStoreId(goal.state_file, "registered Goal state"));
  const witness = requireJsonObject(rawWitness, "codec-validated source witness");
  if (!hasExactAuthorityKeys(witness, ["path", "sha256"]) || witness.path !== registry ||
      typeof witness.sha256 !== "string" || !BARE_SHA256_PATTERN.test(witness.sha256) || state === registry) reject("Invalid registered Goal context witness");
  const verify = async () => {
    const python = process.env.LOOPX_EFFECT_RUNTIME_PYTHON;
    if (!python || !isAbsolute(python) || python.includes("\0")) reject("Registered Goal context requires the source-selected Python codec adapter");
    let observed: JsonObject;
    try {
      const result = await executeFile(python, ["-m", "loopx.control_plane.goals.planned_transfer_io", registry, goalId], {
        env: {...process.env, PYTHONPATH: fileURLToPath(new URL("../../../", import.meta.url))},
        timeout: 30_000, maxBuffer: 16 * 1024 * 1024,
      });
      observed = requireJsonObject(JSON.parse(result.stdout), "registered context codec readback");
    } catch { reject("Registered Goal context codec readback is unavailable; preserve the original operation and retry through its installed source"); }
    if (observed.registry_profile !== null) reject("This source context profile requires the normal project registry; lifecycle-only adoption remains held");
    if (!canonicalAuthorityBytes(observed.registry_witness).equals(canonicalAuthorityBytes(witness))) reject("Registered Goal context changed; review its original transfer");
    if (!canonicalAuthorityBytes(observed.source_context).equals(canonicalAuthorityBytes(context))) reject("Registered Goal intent differs from its reviewed transfer context");
  };
  return withFileMutationLock(legacyCoordinationTodoLockPath(root, goalId), () =>
    withFileMutationLock(state, () => withFileMutationLock(registry, async () => {
      await verify();
      const result = await operation(context);
      await verify();
      return result;
    })));
}

/** Codec-decoded registry facts are supplied by the transaction-owning adapter.
 * This decision never serializes or mutates a project registry itself. */
export function planPreparedGoalStage(value: JsonObject): PreparedGoalStage {
  if (!hasExactAuthorityKeys(value, ["operation_id", "source_context", "archive_sha256", "target", "destination_registry"])) {
    reject("Goal transfer staging requires the complete original context and destination observation");
  }
  const operation = requireAuthorityStoreId(value.operation_id, "original transfer operation");
  if (typeof value.archive_sha256 !== "string" || !BARE_SHA256_PATTERN.test(value.archive_sha256)) reject("Invalid transfer archive digest");
  const context = requireJsonObject(value.source_context, "original Goal context");
  if (!hasExactAuthorityKeys(context, ["goal", "state_text", "runtime_root", "registry_path"]) ||
      typeof context.state_text !== "string" || !context.state_text.trim()) reject("Original complete Goal context is required");
  const original = requireJsonObject(context.goal, "original Goal");
  const goalId = requireAuthorityStoreId(original.id, "original Goal id");
  if (/[\\/]/.test(goalId) || goalId === "." || goalId === "..") reject("This local transfer profile requires a single-segment Goal id");
  if (original.goal_instance_id !== undefined) reject("This staged local profile does not adopt a Goal lifetime; use its owning lifecycle");
  const sourceProject = absolute(original.repo, "source project");
  const sourceRoot = absolute(context.runtime_root, "source runtime");
  absolute(context.registry_path, "source registry");
  const target = requireJsonObject(value.target, "transfer destination");
  if (!hasExactAuthorityKeys(target, ["runtime_root", "project", "registry_path", "provider"]) ||
      (target.provider !== "file" && target.provider !== "sqlite")) reject("Transfer destination requires the declared local File/SQLite profile");
  const root = absolute(target.runtime_root, "destination runtime"), project = absolute(target.project, "destination project");
  const registryPath = absolute(target.registry_path, "destination registry");
  if (registryPath !== join(project, ".loopx", "registry.json")) reject("This profile requires the destination project's canonical registry route");
  if ([root, project].some(path => [sourceRoot, sourceProject].some(source => overlaps(source, path)))) {
    reject("Transfer staging must not overlap the original source project or runtime");
  }
  const directory = join(project, ".loopx", "goals", goalId);
  const statePath = join(directory, "ACTIVE_GOAL_STATE.md"), contextPath = join(directory, "TRANSFER_CONTEXT.json");
  const identity = {schema_version: PLANNED_TRANSFER_GOAL_STAGE_SCHEMA, goal_id: goalId, operation_id: operation,
    source_context_sha256: canonicalAuthoritySha256(context), archive_sha256: value.archive_sha256, target};
  // Copy no adapter credentials, Host/session identity, machine capability
  // configuration, creation receipt or live membership into this registration.
  const goal: JsonObject = {id: goalId, status: "active", role: "controller", domain: "project-goal-control-plane",
    display_name: typeof original.display_name === "string" ? original.display_name : goalId,
    repo: project, state_file: relative(project, statePath),
    activation: {schema_version: "loopx_goal_activation_v1", state: "stopped", reason: "Planned transfer requires fresh destination admission"},
    adapter: {kind: "generic_project_goal_v0", status: "connected"},
    spawn_policy: {mode: "default", allowed: false, max_children: 3, allowed_domains: []},
    coordination: {registered_agents: [], write_scope: [], requires_parent_approval: ["write", "publish", "production-action"]},
    authority_sources: [{kind: "migration_report", path: contextPath, role: "original_transfer_context"}],
    planned_transfer: {schema_version: PLANNED_TRANSFER_GOAL_STAGE_SCHEMA, operation_id: operation,
      identity_sha256: canonicalAuthoritySha256(identity), state: "staged", identity_continuity: "same_goal_read_only",
      fresh_admission_required: true}};
  const registry = requireJsonObject(value.destination_registry, "destination registry");
  if (registry.profile_id !== undefined) reject("Destination registry profile requires its own lifetime admission");
  if (registry.common_runtime_root !== undefined && registry.common_runtime_root !== root) reject("Destination registry belongs to a different runtime");
  if (registry.goals !== undefined && !Array.isArray(registry.goals)) reject("Invalid destination Goal inventory");
  const matches = ((registry.goals ?? []) as unknown[]).map(item => requireJsonObject(item, "registered destination Goal"))
    .filter(item => item.id === goalId);
  if (matches.length > 1 || (matches.length === 1 && !canonicalAuthorityBytes(matches[0]).equals(canonicalAuthorityBytes(goal)))) {
    reject("Destination Goal is occupied or its staged registration changed; preserve it and recover the original transfer");
  }
  return {goalId, operationId: operation, context, goal, statePath, contextPath, identity};
}

export function stagedGoalRegistry(registry: JsonObject, plan: PreparedGoalStage, runtime: string): JsonObject {
  const others = ((registry.goals ?? []) as JsonObject[]).filter(goal => goal.id !== plan.goalId);
  return {...registry, schema_version: registry.schema_version ?? "0.1", common_runtime_root: runtime, goals: [...others, plan.goal]};
}
