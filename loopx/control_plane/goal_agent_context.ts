/** Composition root only: each capability owns its advisory lifecycle rules. */
import { projectAgentContext } from "./agent_context.ts";
import type { JsonObject } from "./effect_program.ts";
import { jsonObject, requireJsonObject } from "./runtime_decode.ts";
import { subagentContextConfiguration, subagentContextProvider } from "./subagent_context.ts";
import { improvementContextProvider } from "./capabilities/goal_capability_organization.ts";

export function evaluateGoalAgentContext(value: unknown): JsonObject | null {
  const input = requireJsonObject(value, "Goal agent context");
  const policy = jsonObject(input.capability_improvement);
  return projectAgentContext({
    phase: input.phase, scope: input.scope, observations: input.observations ?? {},
    capabilities: {
      multi_subagent: subagentContextConfiguration(input.orchestration),
      goal_capability_organization: { enabled: policy?.mode === "bounded", policy },
    },
  }, [subagentContextProvider, improvementContextProvider]);
}
