import assert from "node:assert/strict";
import test from "node:test";
import { composerKeyAction } from "./composer-keyboard.ts";

const enter = { key: "Enter", keyCode: 13, shiftKey: false, metaKey: false };

test("candidate confirmation never sends, queues or steers", () => {
  assert.equal(composerKeyAction(enter, true), undefined);
  assert.equal(composerKeyAction({ ...enter, isComposing: true }, false), undefined);
  assert.equal(composerKeyAction({ ...enter, keyCode: 229, isComposing: false }, false), undefined);
  assert.equal(composerKeyAction({ ...enter, metaKey: true, keyCode: 229 }, false), undefined);
  // Composition has ended; a separate ordinary Enter remains usable.
  assert.equal(composerKeyAction(enter, false), "send");
});

test("one Enter expresses one intent; Shift always retains its newline", () => {
  assert.equal(composerKeyAction(enter, false), "send");
  assert.equal(composerKeyAction({ ...enter, metaKey: true }, false), "steer");
  assert.equal(composerKeyAction({ ...enter, shiftKey: true }, false), undefined);
  assert.equal(composerKeyAction({ ...enter, shiftKey: true, metaKey: true }, false), undefined);
  assert.equal(composerKeyAction({ ...enter, repeat: true }, false), undefined);
  assert.equal(composerKeyAction({ ...enter, key: "a" }, false), undefined);
});
