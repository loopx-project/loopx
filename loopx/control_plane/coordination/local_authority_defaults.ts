/** New-Goal target selection; never live inheritance or authority promotion. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {requireAuthorityStoreId} from "./authority_store_codec.ts";
import {requireLocalAuthorityRuntimeRoot, selectLocalAuthorityTarget} from "./local_authority_provider.ts";

const GOAL_STORAGE_DEFAULTS_SCHEMA = "loopx_goal_storage_defaults_v0";
const NEW_GOAL_STORAGE_TARGET_SCHEMA = "loopx_new_goal_storage_target_v0";
function provider(value: unknown): "file" | "sqlite" {
  if (value !== "file" && value !== "sqlite") throw new Error("New Goal storage must be file or sqlite");
  return value;
}
export async function manageNewGoalStorage(request: JsonObject): Promise<JsonObject> {
  if (request.action === "resolve") {
    const config = requireJsonObject(request.configuration, "Goal storage defaults");
    if (config.schema_version !== GOAL_STORAGE_DEFAULTS_SCHEMA || Object.keys(config).some(key => !["schema_version", "new_goal_provider"].includes(key))) {
      throw new Error("Invalid Goal storage defaults");
    }
    return {schema_version: NEW_GOAL_STORAGE_TARGET_SCHEMA, provider: provider(config.new_goal_provider)};
  }
  if (request.action !== "initialize") throw new Error("Unknown new Goal storage action");
  const target = requireJsonObject(request.target, "New Goal storage target");
  if (target.schema_version !== NEW_GOAL_STORAGE_TARGET_SCHEMA || Object.keys(target).some(key => !["schema_version", "provider"].includes(key))) {
    throw new Error("Invalid new Goal storage target");
  }
  const selected = await selectLocalAuthorityTarget(requireLocalAuthorityRuntimeRoot(request.runtime_root),
    requireAuthorityStoreId(request.goal_id, "goal id"), provider(target.provider), true, "creation_retry");
  return {...selected, status: selected.selection_preserved ? "existing_authority_preserved" : "selected", applies_to: "canonical_authority", promotion_performed: false};
}
