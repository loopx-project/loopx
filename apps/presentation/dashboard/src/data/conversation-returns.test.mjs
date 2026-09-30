import assert from "node:assert/strict";
import { conversationReturnSessions, reconcileConversationHistory, reconcileConversationReturns } from "./conversation-returns.ts";

const collaboration = { returns: [] };
const original = [
  { sourceSessionId: "old", sourceMessageId: "brief", collaboration, text: "Original brief" },
  { sourceSessionId: "current", sourceMessageId: "brief", text: "New conversation" },
  { sourceSessionId: "current", sourceTurnId: "running", text: "Streaming text", pending: true },
];
const conclusion = { message_id: "result", turn_id: "original", origin: "manager_followup", text: "Checked result",
  return_delivery: { phase: "conclusion", status: "verification_required" } };
const createReply = (row) => ({ sourceSessionId: "old", sourceMessageId: row.message_id, text: row.text, returnDelivery: row.return_delivery });
assert.deepEqual(conversationReturnSessions("current", original), ["current", "old"]);
// Reading another session must not erase the old brief, even with colliding IDs.
const current = reconcileConversationReturns(original, "current", [{ message_id: "brief", text: "New conversation" }], createReply);
assert.equal(current, original);
assert.equal(reconcileConversationReturns(original, "old", [], createReply), original);
assert.equal(reconcileConversationReturns(original, "old", [{ message_id: "brief" }], createReply), original);
const arrived = reconcileConversationReturns(original, "old", [conclusion, conclusion], createReply);
assert.equal(arrived.length, original.length + 1);
assert.equal(arrived[2], original[2]);
assert.equal(reconcileConversationReturns(arrived, "old", [conclusion], createReply), arrived);
assert.equal(arrived.at(-1).text, "Checked result");
// The worker has concluded, but transport uncertainty still requires readback.
const settledBrief = { message_id: "brief", collaboration: { returns: [{ phase: "conclusion", status: "delivered" }] } };
const waiting = reconcileConversationReturns(arrived, "old", [settledBrief, conclusion], createReply);
assert.deepEqual(conversationReturnSessions("current", waiting), ["current", "old"]);
const delivered = reconcileConversationReturns(waiting, "old", [settledBrief, { ...conclusion,
  return_delivery: { phase: "conclusion", status: "delivered" } }], createReply);
assert.deepEqual(conversationReturnSessions("current", delivered), ["current"]);
assert.equal(delivered.length, arrived.length);
// Recovered Turns acquire stored identity without replacing the live text.
const hydrated = reconcileConversationReturns(original, "current", [{ message_id: "answer", turn_id: "running",
  role: "agent", text: "Stored text", collaboration }], createReply);
assert.equal(hydrated[2].sourceMessageId, "answer");
assert.equal(hydrated[2].text, "Streaming text");
assert.equal(hydrated[2].pending, true);
assert.equal(hydrated[0], original[0]);
assert.deepEqual(conversationReturnSessions(undefined, delivered), ["current"]);
assert.deepEqual(conversationReturnSessions(undefined, original), ["current", "old"]);
assert.deepEqual(conversationReturnSessions(undefined, [delivered[0], delivered.at(-1)]), []);
const historyRows = [
  { session_id: "old", message_id: "answer", role: "agent", turn_id: "old-turn", text: "Older answer", created_at: "2026-08-01" },
  { session_id: "current", message_id: "answer", role: "agent", turn_id: "current-turn", text: "Stored answer", created_at: "2026-08-02", collaboration },
];
const live = [{ sourceSessionId: "current", sourceTurnId: "current-turn", text: "Live answer", pending: true }];
const createHistory = (row) => ({ sourceSessionId: row.session_id, sourceMessageId: row.message_id,
  sourceCreatedAt: row.created_at, text: row.text });
const recoveredHistory = reconcileConversationHistory(live, historyRows, createHistory);
assert.equal(recoveredHistory.length, 2, "Same Turn keeps its existing live answer");
assert.equal(recoveredHistory[0].text, "Older answer");
assert.equal(recoveredHistory[1].text, "Live answer");
assert.equal(recoveredHistory[1].pending, true);
assert.equal(recoveredHistory[1].collaboration, collaboration);
assert.equal(reconcileConversationHistory(recoveredHistory, historyRows, createHistory), recoveredHistory, "Repeated read is idempotent");
assert.equal(reconcileConversationHistory(recoveredHistory, [historyRows[0]], createHistory), recoveredHistory, "An incomplete read never retracts known history");
const bothRoles = [
  { sourceSessionId: "current", sourceTurnId: "same-turn", role: "user", text: "My request" },
  { sourceSessionId: "current", sourceTurnId: "same-turn", role: "assistant", text: "Live answer" },
];
const storedRoles = [
  { session_id: "current", message_id: "user-message", turn_id: "same-turn", role: "user", text: "My request" },
  { session_id: "current", message_id: "agent-message", turn_id: "same-turn", role: "agent", text: "Stored answer" },
];
const hydratedRoles = reconcileConversationHistory(bothRoles, storedRoles, createHistory);
assert.equal(hydratedRoles.length, 2);
assert.equal(hydratedRoles[0].sourceMessageId, "user-message");
assert.equal(hydratedRoles[1].sourceMessageId, "agent-message");
assert.equal(hydratedRoles[1].text, "Live answer");
console.log("conversation-returns: passed (session isolation, late return, deduplication, transport uncertainty, stream preservation and watch retirement)");
