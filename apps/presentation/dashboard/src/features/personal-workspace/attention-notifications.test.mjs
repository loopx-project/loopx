import assert from "node:assert/strict";
import test from "node:test";

import {
  attentionNotificationContents,
  notifiableAttention,
  attentionNotificationKey,
  attentionNotificationSupport,
  attentionPollIntervalMs,
  newAttentionItems,
  shouldNotifyAttention,
} from "./attention-notifications.ts";

const item = (goalId, todoId, text = "Approve the cut-over window", goalTitle = "Billing migration") => ({ goalId, todoId, text, goalTitle, blocking: true });

test("a needs-you item is identified by Goal and Todo, never by text", () => {
  assert.equal(attentionNotificationKey(item("g1", "t1", "a")), attentionNotificationKey(item("g1", "t1", "b")));
  assert.notEqual(attentionNotificationKey(item("g1", "t1")), attentionNotificationKey(item("g2", "t1")));
  assert.notEqual(attentionNotificationKey(item("g1", "t1")), attentionNotificationKey(item("g1", "t2")));
});

test("only items absent from the seen set are new, in projection order", () => {
  const seen = new Set([attentionNotificationKey(item("g1", "t1"))]);
  const fresh = newAttentionItems(seen, [item("g2", "t9"), item("g1", "t1"), item("g1", "t2")]);
  assert.deepEqual(fresh.map((row) => row.todoId), ["t9", "t2"]);
  assert.deepEqual(newAttentionItems(seen, []), [], "A shrinking queue never notifies");
});

test("notify only when enabled, granted and the owner is not looking", () => {
  const base = { enabled: true, support: "granted", visible: false, focused: false };
  assert.equal(shouldNotifyAttention(base), true);
  assert.equal(shouldNotifyAttention({ ...base, visible: true, focused: false }), true, "an unfocused visible window still needs a nudge");
  assert.equal(shouldNotifyAttention({ ...base, visible: true, focused: true }), false, "a focused page already shows the lane");
  assert.equal(shouldNotifyAttention({ ...base, enabled: false }), false);
  for (const support of ["default", "denied", "unsupported"]) {
    assert.equal(shouldNotifyAttention({ ...base, support }), false, `${support} never notifies`);
  }
});

test("support reads the browser answer and tolerates a missing API", () => {
  assert.equal(attentionNotificationSupport({}), "unsupported");
  assert.equal(attentionNotificationSupport({ Notification: { permission: "granted" } }), "granted");
  assert.equal(attentionNotificationSupport({ Notification: { permission: "denied" } }), "denied");
  assert.equal(attentionNotificationSupport({ Notification: { permission: "default" } }), "default");
});

test("hidden pages re-read less often than visible ones", () => {
  assert.ok(attentionPollIntervalMs(true) > attentionPollIntervalMs(false));
  assert.ok(attentionPollIntervalMs(false) >= 15_000, "never hammer the local service");
});

test("content is owner language, bounded, and summarizes a burst", () => {
  const copy = { fallbackTitle: "LoopX", more: (count) => `${count} more decisions are waiting` };
  const items = [item("g1", "t1"), item("g1", "t2", "x".repeat(200)), item("g2", "t3", "Pick a source", ""), item("g2", "t4"), item("g2", "t5")];
  const contents = attentionNotificationContents(items, copy);
  assert.equal(contents.length, 4, "three items plus one summary");
  assert.equal(contents[0].title, "Billing migration");
  assert.equal(contents[1].body.length, 160, "long text is truncated with an ellipsis");
  assert.equal(contents[2].title, "LoopX", "a missing Goal title falls back to the product name");
  assert.deepEqual(contents[3], { body: "2 more decisions are waiting", tag: "loopx-attention-more", title: "LoopX" });
  assert.equal(new Set(contents.map((row) => row.tag)).size, 4, "tags dedupe per item");
  assert.equal(attentionNotificationContents([], copy).length, 0);
});

test("only items that wait on the owner may notify; optional actions never do", () => {
  const gate = { ...item("g1", "gate"), blocking: true };
  const optional = { ...item("g1", "optional", "Consider renaming the export folder"), blocking: false };
  assert.deepEqual(notifiableAttention([optional, gate, optional]).map((row) => row.todoId), ["gate"]);
  assert.deepEqual(notifiableAttention([optional]), []);
  assert.deepEqual(newAttentionItems(new Set(), notifiableAttention([optional])), [], "an optional item is not a new decision");
});
