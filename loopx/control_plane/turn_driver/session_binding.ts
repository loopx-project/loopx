/** Conversation reuse policy for normal planning and original-Turn recovery.
 * Persistence and Python's historical scalar codec remain at the host IO edge.
 */
import {requireJsonObject, requireStringLiteral, type JsonObject} from "../runtime_decode.ts";
import {TURN_ROUTES} from "./turn_contract_generated.ts";

const SESSION_SCHEMA = "loopx_turn_session_binding_v0";
const CONTEXT_SCHEMA = "loopx_iteration_context_policy_v0";

type ContextPolicy = {
  schema_version: typeof CONTEXT_SCHEMA;
  mode: "fresh" | "resume";
  scope: "iteration";
  binding_scope?: "agent";
};
type SessionDecision =
  | {action: "none"; binding_status: "not_applicable"; context_policy: ContextPolicy}
  | {action: "start_new"; binding_status?: "existing_binding_ignored" | "not_found"; context_policy: ContextPolicy}
  | {action: "resume"; binding_status: "compatible"; context_policy: ContextPolicy}
  | {action: "reject"; binding_status: "missing_turn_lineage" | "unsupported_schema" | "identity_mismatch"};
type SessionPlan = {schema_version: typeof SESSION_SCHEMA} & SessionDecision;

export function turnSessionPlan(request: JsonObject): {session: SessionPlan; error: string | null} {
  const scope = requireStringLiteral(request.session_scope, ["todo", "agent"], "session scope");
  const mode = requireStringLiteral(request.iteration_context_policy, ["fresh", "resume"], "iteration context policy");
  const route = requireStringLiteral(request.route, TURN_ROUTES, "Turn route");
  const lineage = requireJsonObject(request.lineage, "Turn lineage");
  // Lineage and binding identity are encoded by the production caller,
  // including historical Python str/or values. Never trim that identity.
  const fields = scope === "todo" && !(lineage.request_binding_id && mode === "fresh")
    ? ["goal_id", "agent_id", "todo_id"] : ["goal_id", "agent_id"];
  const context: ContextPolicy = {schema_version: CONTEXT_SCHEMA, mode, scope: "iteration",
    ...(scope === "agent" ? {binding_scope: scope} : {})};
  const result = (plan: SessionDecision, error: string | null = null): {session: SessionPlan; error: string | null} =>
    ({session: {schema_version: SESSION_SCHEMA, ...plan}, error});
  if (route !== "ready_for_host" && route !== "repair_required" && route !== "replan_required") {
    return result({action: "none", binding_status: "not_applicable", context_policy: context});
  }
  if ([...fields, "action_hash"].some(field => !lineage[field]) ||
      !(lineage.todo_id || lineage.replan_obligation_id || lineage.request_binding_id)) {
    return result({action: "reject", binding_status: "missing_turn_lineage"},
      "host-bound routes require goal, agent, todo, and action-hash lineage");
  }
  const binding = requireJsonObject(request.session_binding, "session binding");
  const hasBinding = Object.keys(binding).length > 0;
  if (mode === "fresh") {
    return result({action: "start_new", binding_status: hasBinding ? "existing_binding_ignored" : "not_found",
      context_policy: context});
  }
  if (!hasBinding) return result({action: "start_new", context_policy: context});
  if (binding.schema_version !== SESSION_SCHEMA) {
    return result({action: "reject", binding_status: "unsupported_schema"},
      "unsupported LoopX Turn session binding schema");
  }
  const identity = requireJsonObject(request.binding_identity_text, "encoded session identity");
  if (fields.some(field => identity[field] !== lineage[field])) {
    return result({action: "reject", binding_status: "identity_mismatch"},
      "session binding does not match the current " + fields.join(", "));
  }
  return result({action: "resume", binding_status: "compatible", context_policy: context});
}
