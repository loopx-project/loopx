/** New-Goal target selection; never live inheritance or authority promotion. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {requireAuthorityStoreId} from "./authority_store_codec.ts";
import {requireLocalAuthorityRuntimeRoot, selectLocalAuthorityTarget} from "./local_authority_provider.ts";
import {EXECUTION_HANDOFF_MODES} from "./handoff_mode_vocabulary.ts";
import {initializeNewGoalAuthority} from "./new_goal_initialization.ts";

const GOAL_STORAGE_DEFAULTS_SCHEMA = "loopx_goal_storage_defaults_v0";
const NEW_GOAL_STORAGE_TARGET_SCHEMA = "loopx_new_goal_storage_target_v0";
export const CANONICAL_GOAL_STORAGE_DEFAULTS_SCHEMA = "loopx_goal_storage_defaults_v1";
export const CANONICAL_NEW_GOAL_STORAGE_TARGET_SCHEMA = "loopx_new_goal_storage_target_v1";
function provider(value: unknown): "file" | "sqlite" {
  if (value !== "file" && value !== "sqlite") throw new Error("New Goal storage must be file or sqlite");
  return value;
}
export async function manageNewGoalStorage(request: JsonObject): Promise<JsonObject> {
  if (request.action === "resolve") {
    const config = requireJsonObject(request.configuration, "Goal storage defaults");
    const canonical = config.schema_version === CANONICAL_GOAL_STORAGE_DEFAULTS_SCHEMA;
    const keys = canonical ? ["schema_version", "new_goal_provider", "canonical_creation", "new_goal_handoff_mode"] : ["schema_version", "new_goal_provider"];
    if ((!canonical && config.schema_version !== GOAL_STORAGE_DEFAULTS_SCHEMA) ||
        Object.keys(config).some(key => !keys.includes(key)) || keys.some(key => !(key in config))) {
      throw new Error("Invalid Goal storage defaults");
    }
    if (canonical && (typeof config.canonical_creation !== "boolean" ||
        !EXECUTION_HANDOFF_MODES.some(mode => mode === config.new_goal_handoff_mode))) {
      throw new Error("Canonical creation requires a boolean setting and soft_claim or hard_lease policy");
    }
    if (canonical && config.canonical_creation === true) return {
      schema_version: CANONICAL_NEW_GOAL_STORAGE_TARGET_SCHEMA, provider: provider(config.new_goal_provider),
      handoff_mode: config.new_goal_handoff_mode,
    };
    return {schema_version: NEW_GOAL_STORAGE_TARGET_SCHEMA, provider: provider(config.new_goal_provider)};
  }
  if (request.action !== "initialize") throw new Error("Unknown new Goal storage action");
  const target = requireJsonObject(request.target, "New Goal storage target");
  if (target.schema_version === CANONICAL_NEW_GOAL_STORAGE_TARGET_SCHEMA) {
    if (Object.keys(target).some(key => !["schema_version", "provider", "handoff_mode"].includes(key)) ||
        !EXECUTION_HANDOFF_MODES.some(mode => mode === target.handoff_mode)) throw new Error("Invalid canonical creation target");
    provider(target.provider);
    return await initializeNewGoalAuthority(request);
  }
  if (target.schema_version !== NEW_GOAL_STORAGE_TARGET_SCHEMA || Object.keys(target).some(key => !["schema_version", "provider"].includes(key))) {
    throw new Error("Invalid new Goal storage target");
  }
  const selected = await selectLocalAuthorityTarget(requireLocalAuthorityRuntimeRoot(request.runtime_root),
    requireAuthorityStoreId(request.goal_id, "goal id"), provider(target.provider), true, "creation_retry");
  return {...selected, status: selected.selection_preserved ? "existing_authority_preserved" : "selected", applies_to: "canonical_authority", promotion_performed: false};
}
