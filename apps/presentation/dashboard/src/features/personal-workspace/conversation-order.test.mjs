import assert from "node:assert/strict";
import {conversationOrder} from "./conversation-order.ts";

const message = (id, createdAt) => ({id, kind: "message", message: {id, role: "user", text: "Inspect this image", createdAt}});
const proposal = (id, createdAt, updatedAt) => ({id, kind: "proposal", proposal: {previewId: id, createdAt, updatedAt}});
const older = proposal("old", "2026-09-01T01:00:00Z", "2026-10-01T03:00:00Z");
const current = message("question", "2026-10-01T02:00:00Z");
const answer = message("answer", "2026-10-01T02:01:00Z");
const newDraft = proposal("new", "2026-10-01T02:02:00Z");
const undated = proposal("undated");
for (const cards of [[older, newDraft, undated], [undated, newDraft, older]]) {
  const input = [current, answer, ...cards];
  assert.deepEqual(conversationOrder(input).map(item => item.id), ["undated", "old", "question", "answer", "new"]);
  assert.equal(input[0], current, "Rendering does not mutate caller order");
}
assert.deepEqual(conversationOrder([message("user", "2026-10-01T00:00:00Z"), message("reply", "2026-10-01T00:00:00Z")]).map(i => i.id), ["user", "reply"]);
assert.deepEqual(conversationOrder([current, proposal("invalid", "invalid")]).map(i => i.id), ["invalid", "question"]);

// The service stores its user message after admitting the Turn. An optimistic
// work row must follow its request even when that durable timestamp is newer.
const inTurn = (id, role, createdAt, pending = false, session = "one") => ({
  id, kind: "message", message: {id, role, createdAt, pending,
    sourceSessionId: session, sourceTurnId: "turn", text: id},
});
const request = inTurn("request", "user", "2026-10-01T02:00:00.050Z");
const working = inTurn("working", "assistant", "2026-10-01T02:00:00.000Z", true);
const correction = inTurn("correction", "user", "2026-10-01T02:00:01Z");
for (const input of [[working, request, correction], [correction, request, working]]) {
  assert.deepEqual(conversationOrder(input).map(i => i.id), ["request", "working", "correction"]);
}
const otherSession = inTurn("other-session", "user", "2026-10-01T02:00:02Z", false, "two");
assert.deepEqual(conversationOrder([otherSession, working]).map(i => i.id), ["working", "other-session"], "Turn ids alone never cross session boundaries");
const finished = {...working, message: {...working.message, pending: false, createdAt: "2026-10-01T02:00:03Z"}};
assert.deepEqual(conversationOrder([finished, correction, request]).map(i => i.id), ["request", "correction", "working"], "The stored completion follows its correction");
console.log("Conversation request, work and correction order passed");

const completedBeforeReadback = {...working, message: {...working.message, pending: false}};
assert.deepEqual(conversationOrder([completedBeforeReadback, correction, request]).map(i => i.id),
  ["request", "correction", "working"], "A short completed reply without its stored timestamp cannot precede the request or steering instruction");
const storedCommentary = {...completedBeforeReadback, message: {...completedBeforeReadback.message, sourceMessageId: "stored-commentary", createdAt: "2026-10-01T02:00:00.100Z"}};
assert.deepEqual(conversationOrder([storedCommentary, correction, request]).map(i=>i.id),
  ["request", "working", "correction"], "Stored commentary keeps its actual order rather than moving behind later instructions");

const queuedReply = {id: "queued-reply", kind: "message", message: {id:"queued-reply", role:"assistant", pending:false, sourceSessionId:"one", sourceTurnId:"queued-turn", createdAt:"2026-10-01T02:00:00.500Z", endedAt:Date.parse("2026-10-01T02:00:02Z"), text:"done"}};
assert.deepEqual(conversationOrder([queuedReply, correction]).map(i=>i.id), ["correction", "queued-reply"], "Before stored readback, a completed queued reply uses its observed end rather than preparation time");
