import { createHash } from "node:crypto";
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireBoolean, requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";

/** Stable lookup does not depend on a successful first stage or log mutation. */
export function rewardMemoryAppOutcomeIdentity(params: JsonObject): JsonObject {
  const identity = requireJsonObject(params.identity, "identity");
  const fields = ["goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id"] as const;
  const values = fields.map(key => requireNonEmptyString(identity[key], key));
  const digest = createHash("sha256").update(JSON.stringify(values)).digest("hex");
  return { ...Object.fromEntries(fields.map((key, index) => [key, values[index]])),
    candidate_id: `app:${digest.slice(0, 20)}` };
}

/** One private candidate per exact settlement. Reflection content may not replace
 * an earlier candidate; configuration and validation remain separate fences. */
export function bindRewardMemoryAppOutcome(params: JsonObject): JsonObject {
  const identity = rewardMemoryAppOutcomeIdentity(params);
  const policy = requireJsonObject(params.policy, "policy");
  const agent = String(identity.agent_id);
  const automation = jsonObject(policy.automation) ?? {};
  const receipt = jsonObject(jsonObject(policy.enablement_receipts)?.[agent]) ?? {};
  const hash = (value: unknown) => createHash("sha256").update(JSON.stringify(value)).digest("hex");
  const expected: JsonObject = {
    ...identity,
    reflection_digest: requireNonEmptyString(params.reflection_digest, "reflection_digest"),
    binding_fingerprint: `sha256:${hash([
      policy.binding_revision ?? null, policy.config_path ?? "", policy.config_digest ?? "",
      policy.enabled === true, policy.experimental === true,
      Array.isArray(policy.enabled_agents) && policy.enabled_agents.includes(agent),
      automation.automatic_ingest ?? null, automation.automatic_recall ?? null,
      receipt.status ?? null, receipt.config_digest ?? null,
      receipt.actor_binding_verified ?? null, receipt.writability_verified ?? null,
      receipt.exact_readback_verified ?? null,
    ])}`,
    validation_declaration_sha256: params.validation_declaration_sha256 == null ? null
      : requireNonEmptyString(params.validation_declaration_sha256, "validation_declaration_sha256"),
  };
  const revalidating = requireBoolean(params.revalidating, "revalidating");
  if (params.previous != null) {
    const previous = requireJsonObject(params.previous, "previous");
    for (const [key, value] of Object.entries(expected)) {
      // A new exact validator invocation can replace validation, never the
      // candidate's identity, reflection or destination binding.
      if (revalidating && key === "validation_declaration_sha256") continue;
      if (previous[key] !== value) {
        throw new EffectRuntimeRequestError(`Codex App reward memory candidate ${key} mismatch`);
      }
    }
  }
  return expected;
}
