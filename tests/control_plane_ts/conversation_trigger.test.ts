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
