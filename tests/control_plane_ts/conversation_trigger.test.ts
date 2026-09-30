import assert from "node:assert/strict";
import {test} from "node:test";
import {resolveConversationTrigger as trigger} from "../../loopx/control_plane/collaboration/conversation_trigger.ts";

test("capture or human identity alone does not grant a default turn", () => {
  assert.equal(trigger({human: true}).authorized, false);
  assert.equal(trigger({mode: "addressed", human: true}).authorized, false);
  assert.equal(trigger({addressed: true}).authorized, true);
});
test("explicit human-message admission preserves origin and replay boundaries", () => {
  assert.equal(trigger({mode: "human_messages", human: true}).authorized, true);
  for (const evidence of [{}, {human: false}, {human: true, historical: true},
    {addressed: true, historical: true}, {human: true, self_message: true},
    {addressed: true, bot_message: true}]) {
    assert.equal(trigger({mode: "human_messages", ...evidence}).authorized, false);
  }
  for (const mode of ["all", "", false, {}, []]) {
    assert.throws(() => trigger({mode}), /conversation trigger/);
  }
});

test("non-admission reasons explain the actual cause without changing authority", () => {
  for (const [evidence, reason] of [
    [{mode: "addressed", human: true}, "not_addressed"],
    [{mode: "human_messages", historical: true}, "historical_context_only"],
    [{mode: "human_messages", self_message: true}, "self_message"],
    [{mode: "human_messages", bot_message: true}, "bot_message"],
    [{mode: "human_messages"}, "human_identity_unverified"],
  ] as const) {
    assert.deepEqual(trigger(evidence), {mode: evidence.mode, authorized: false, reason});
  }
});
