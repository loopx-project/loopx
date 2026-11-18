import assert from "node:assert/strict";
import {conversationChannelId, conversationContextKey, conversationContextOfKey, createChatSession} from "../src/data/chat";
import {conversationInputKey} from "../src/features/personal-workspace/use-conversation-input-state";

const manager = {kind: "manager"} as const;
const goal = {kind: "goal", goalId: "manager"} as const;
const ordinaryGoal = {kind: "goal", goalId: "another-goal"} as const;
const managerKey = conversationContextKey(manager);
const goalKey = conversationContextKey(goal);
assert.notEqual(goalKey, managerKey, "a Goal named manager must not share the steward's context key");
assert.equal(managerKey, "manager", "existing steward drafts keep their key");
assert.equal(conversationContextKey(ordinaryGoal), ordinaryGoal.goalId, "other Goal keys stay compatible");
assert.deepEqual(conversationContextOfKey(ordinaryGoal.goalId), ordinaryGoal);
assert.deepEqual(conversationContextOfKey(goalKey), goal);
assert.deepEqual(conversationContextOfKey(managerKey), manager);
assert.notEqual(conversationInputKey(goalKey, "codex", false), conversationInputKey(managerKey, "codex", false));
assert.equal(conversationChannelId(goal), "goal.manager");
assert.equal(conversationChannelId(manager), "manager");

const originalFetch = globalThis.fetch;
const requests: Array<Record<string, unknown>> = [];
globalThis.fetch = async (_input, init) => {
  requests.push(JSON.parse(String(init?.body)));
  return Response.json({ok: true});
};
try {
  await createChatSession(conversationContextOfKey(goalKey), "codex");
  await createChatSession(conversationContextOfKey(managerKey), "codex");
  assert.equal(requests[0].context_kind, "goal");
  assert.equal(requests[0].goal_id, "manager");
  assert.equal(requests[1].context_kind, "manager");
} finally {
  globalThis.fetch = originalFetch;
}
console.log("manager-goal-routing: separate contexts, drafts and session requests passed");
