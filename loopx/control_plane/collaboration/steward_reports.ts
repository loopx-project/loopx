/** Original-conversation report consent; calendar and Todo owners remain separate. */
import {createHash} from "node:crypto";
import {requireJsonObject, type JsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {stewardManagedBasis} from "./steward_managed_mode.ts";

export function planStewardReport(params: JsonObject): JsonObject {
  const current = stewardManagedBasis(params, true);
  if (!current.actor || !current.goal || !["enable", "pause", "cancel"].includes(String(params.operation))) {
    throw new EffectRuntimeRequestError("prepare the original private steward before configuring reports");
  }
  const route = `steward-private:${current.id}:${current.source}`;
  const before = params.configuration == null ? null : requireJsonObject(params.configuration, "current report configuration");
  if (before && (before.profile_preset !== "portfolio-progress" || before.route_ref !== route)) {
    throw new EffectRuntimeRequestError("the existing report configuration belongs to another source or destination");
  }
  const configuration = requireJsonObject(params.proposed, "normalized report configuration");
  if (configuration.profile_preset !== "portfolio-progress" || configuration.route_ref !== route
      || configuration.enabled !== (params.operation === "enable")
      || params.operation === "enable" && params.goal_active !== true) {
    throw new EffectRuntimeRequestError("report configuration must retain the original private audience and active actor");
  }
  const routeProof = requireJsonObject(params.route, "verified original private source");
  if (routeProof.binding_id !== current.id || routeProof.source_ref !== current.source
      || routeProof.session_id !== current.sessionId || routeProof.provider_ref !== current.binding.provider_ref
      || routeProof.operator_ref !== current.binding.operator_ref) {
    throw new EffectRuntimeRequestError("the original private report destination must be verified");
  }
  const planId = createHash("sha256").update(JSON.stringify([
    current.actor, current.binding, params.operation, before, configuration, routeProof, params.pending_report ?? null,
  ])).digest("hex");
  const pending = params.pending_report == null ? null : requireJsonObject(params.pending_report, "pending report window");
  const gate = params.operation === "enable" && pending?.prepared === true && pending.retired !== true && pending.verified_publication !== true
    && pending.subscription_revision !== params.proposed_subscription_revision ? "report_window_pending" : null;
  return {schema_version: "loopx_steward_report_plan_v0", plan_id: planId,
    actor: current.actor, operation: params.operation, configuration,
    retires_failed_window: params.operation === "cancel" && pending?.retired !== true
      && typeof pending?.failure_ref === "string" && Boolean(pending.failure_ref),
    gate, model_invoked: false, external_writes_performed: false};
}
