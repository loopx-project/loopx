type ConversationScope = Record<string, unknown> & (
  | {kind: "owner_portfolio"; goal_ids: null; private_conversation: true}
  | {kind: "owner_goal"; goal_ids: [string]; private_conversation: true}
  | {kind: "external_audience" | "unavailable"; goal_ids: []; private_conversation: false}
);

/** Classify a host-owned conversation, never model-supplied role or scope.
 * This selects existing read/delivery boundaries; it grants no execution rights.
 */
export function resolveConversationScope(input: Record<string, unknown>): ConversationScope {
  const channel = input.channel_id;
  const goal = input.goal_id;
  if (channel === "manager") {
    return {kind: "owner_portfolio", goal_ids: null, private_conversation: true};
  }
  if (typeof goal === "string" && /^[A-Za-z0-9._-]{1,160}$/.test(goal)
      && goal !== "." && goal !== ".." && goal !== "loopx-manager" && channel === `goal.${goal}`
      && (input.origin === undefined || input.origin === "web")) {
    return {kind: "owner_goal", goal_ids: [goal], private_conversation: true};
  }
  if (typeof channel === "string" && channel.startsWith("manager.external.")
      && channel.length > "manager.external.".length) {
    return {kind: "external_audience", goal_ids: [], private_conversation: false};
  }
  return {kind: "unavailable", goal_ids: [], private_conversation: false};
}
