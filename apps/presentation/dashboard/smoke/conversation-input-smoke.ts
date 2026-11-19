import assert from "node:assert/strict";
import { conversationInputKey, recoverBoundConversationInput } from "../src/features/personal-workspace/use-conversation-input-state";

// A source read cannot change the owner of unsent input or of an uncertain send.
// Ordinary multi-executor drafts remain independent; recovery never combines them.
const original = "bound/source-one/session-one";
const peer = "bound/source-two/session-two";
assert.equal(conversationInputKey(original, "original-conversation", true), conversationInputKey(original, "codex", true));
assert.notEqual(conversationInputKey("manager", "codex", false), conversationInputKey("manager", "claude", false));
assert.notEqual(conversationInputKey(original, "codex", true), conversationInputKey(peer, "codex", true));
const image = { name: "draft.png", mimeType: "image/png", dataUrl: "data:image/png;base64,c3ludGhldGlj", id: "draft-image", size: 9 };
const input = (composer: string) => ({ composer, sending: false, steering: false, actionFeedback: null,
  imageAttachments: [], imageAttachmentError: null, loopxMessageReceipt: "" });
const oldKey = `${original}:codex`;
const legacy = { [oldKey]: { ...input("Retain this unsent message"), imageAttachments: [image] }, [peer]: input("Peer stays separate") };
const recovered = recoverBoundConversationInput(legacy, original);
assert.equal(recovered[original], legacy[oldKey], "A unique old draft keeps all in-memory fields");
assert.equal(recovered[peer], legacy[peer]);
assert.equal(recovered[oldKey], undefined);
assert.equal(legacy[oldKey].composer, "Retain this unsent message", "Recovery does not mutate its input");
const conflicting = { ...legacy, [`${original}:original-conversation`]: input("A different draft") };
assert.equal(recoverBoundConversationInput(conflicting, original), conflicting, "Multiple drafts require selection, never overwrite or concatenate");
const current = { ...legacy, [original]: input("") };
assert.equal(recoverBoundConversationInput(current, original), current, "Clearing an existing draft stays cleared");
assert.deepEqual(recoverBoundConversationInput({ [peer]: input("Peer") }, original), { [peer]: input("Peer") });

console.log("conversation-input: passed (stable original Session, independent ordinary drafts and lossless legacy draft recovery)");
