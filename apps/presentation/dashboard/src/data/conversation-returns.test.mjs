import assert from "node:assert/strict";
import { conversationReturnSessions, reconcileConversationReturns } from "./conversation-returns.ts";

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
console.log("conversation-returns: passed (session isolation, late return, deduplication, transport uncertainty, stream preservation and watch retirement)");
