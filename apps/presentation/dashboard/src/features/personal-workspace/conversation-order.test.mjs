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
console.log("Conversation creation order passed");
