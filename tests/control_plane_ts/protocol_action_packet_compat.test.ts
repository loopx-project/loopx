import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFileSync} from "node:fs";
import test from "node:test";
import {interpretQuotaShouldRunPacket} from "../../loopx/control_plane/effect_program.ts";
import {buildTurnEnvelope, evaluateTurnEnvelope, quotaActionSignatureDocument,
  turnEnvelopeActionSignatureDocument} from "../../loopx/control_plane/quota/turn_envelope.ts";

// Immutable release output; no current writer reconstructs the expected inputs.
const {cases} = JSON.parse(readFileSync(new URL("../fixtures/protocol_packet_history_v0.json", import.meta.url), "utf8"));

// Captured receipts are verified from their captured bytes, not re-created by
// the current writer, which now preserves complete execution contracts.
function capturedDigest(value: unknown): string {
  function ordered(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(ordered);
    if (value !== null && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
        .map(([key, item]) => [key, ordered(item)]));
    }
    return value;
  }
  return `sha256:${createHash("sha256").update(JSON.stringify(ordered(value))).digest("hex")}`;
}

for (const fixture of cases) {
  test(`release ${fixture.id}: supported absence or explicit retired-format rejection`, () => {
    const {decision, envelope} = structuredClone(fixture);
    const before = JSON.stringify({decision, envelope});
    if (fixture.id === "missing_packet") {
      assert.equal(capturedDigest(turnEnvelopeActionSignatureDocument(envelope)),
        envelope.action_signature.envelope_hash);
      assert.equal(capturedDigest(decision), envelope.action_signature.source_decision_hash);
      const current = buildTurnEnvelope({payload: decision, scheduler_execution_args: ""});
      assert.deepEqual((current.contract_capsule as Record<string, unknown>).execution_obligation, decision.execution_obligation);
      assert.deepEqual(quotaActionSignatureDocument(decision), turnEnvelopeActionSignatureDocument(current));
      assert.equal((current.action_signature as Record<string, unknown>).matches, true);
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
