/** Explicit migration boundary for removed hierarchical Todo continuation policies. */
import type { JsonObject } from "../effect_program.ts";
import { AuthorityStoreProtocolError } from "../coordination/authority_store_codec.ts";
import { normalizeTodoAgent, stripPythonWhitespace } from "../coordination/todo_agents.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";

function existingAgent(value: unknown): string | null {
  try {
    return normalizeTodoAgent(value, "agent_id");
  } catch (error) {
    if (error instanceof AuthorityStoreProtocolError) return null;
    throw error;
  }
}

/** Recognize the retained legacy policy markers without authorizing a repair. */
export function removedTodoContinuationPolicy(value: unknown): "primary_review" | "review_handoff" | null {
  const policy = stripPythonWhitespace(String(value ?? "")).toLowerCase();
  return policy === "primary_review" || policy === "review_handoff" ? policy : null;
}

export function validateLegacyContinuationPolicyRepair(
  block: JsonObject,
  intent: JsonObject,
  todoId: string,
): void {
  const removed = removedTodoContinuationPolicy(block.removed_continuation_policy);
  if (removed === null) return;
  const prefix = `todo_id '${todoId}' uses removed continuation_policy=${removed}; `;
  if (intent.claim_only) {
    throw new EffectRuntimeRequestError(prefix + "repair it before claiming");
  }
  const repair = stripPythonWhitespace(
    String(intent.continuation_policy ?? ""),
  ).toLowerCase();
  const exclusions = Array.isArray(intent.excluded_agents) ? intent.excluded_agents : [];
  if (
    repair !== "independent_handoff" ||
    !exclusions.some((agent) => existingAgent(agent) !== null)
  ) {
    throw new EffectRuntimeRequestError(
      prefix +
        "repair it explicitly with continuation_policy=independent_handoff and " +
        "excluded_agents=<author>",
    );
  }
}
