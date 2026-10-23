import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {interpretQuotaShouldRunPacket} from "../../loopx/control_plane/effect_program.ts";
import {buildTurnEnvelope, evaluateTurnEnvelope, quotaActionSignatureDocument,
  turnEnvelopeActionSignatureDocument} from "../../loopx/control_plane/quota/turn_envelope.ts";

// Immutable release output; no current writer reconstructs the expected inputs.
const {cases} = JSON.parse(readFileSync(new URL("../fixtures/protocol_packet_history_v0.json", import.meta.url), "utf8"));
for (const fixture of cases) {
  test(`release ${fixture.id}: supported absence or explicit retired-format rejection`, () => {
    const {decision, envelope} = structuredClone(fixture);
    const before = JSON.stringify({decision, envelope});
    if (fixture.id === "missing_packet") {
      assert.deepEqual(quotaActionSignatureDocument(decision), turnEnvelopeActionSignatureDocument(envelope));
      assert.equal(interpretQuotaShouldRunPacket(decision).observation.protocol_summary, null);
    } else {
      const error = /unsupported format: protocol_action_packet/;
      assert.throws(() => interpretQuotaShouldRunPacket(decision), error);
      assert.throws(() => quotaActionSignatureDocument(decision), error);
      assert.throws(() => buildTurnEnvelope({payload: decision, scheduler_execution_args: ""}), error);
      assert.throws(() => turnEnvelopeActionSignatureDocument(envelope), error);
    }
    assert.equal(JSON.stringify({decision, envelope}), before);
  });
}

test("the retired reconstruction operation is unavailable", () => {
  assert.throws(() => evaluateTurnEnvelope({operation: "protocol_fields", protocol_action_inputs: {}}), /operation is unsupported/);
});
