import assert from "node:assert/strict";
import test from "node:test";
import {planSupervisorEventAppend as plan} from "../../loopx/control_plane/agents/supervisor_event_append.ts";
const event = {event_id: "receipt-1", fingerprint: "a".repeat(64)};
const request = {schema_version: "loopx_supervisor_event_append_plan_v0", last_sequence: 7,
  existing: null, event};
test("one new receipt advances the sequence; replay preserves the original slot", () => {
  assert.deepEqual(plan(request), {status: "planned", kind: "append", append_sequence: 8});
  assert.deepEqual(plan({...request, existing: {...event, append_sequence: 3}}),
    {status: "planned", kind: "replay", append_sequence: 3});
});
test("changed content for an existing id is rejected", () => {
  assert.equal(plan({...request, existing: {...event, fingerprint: "b".repeat(64),
    append_sequence: 3}}).reason_code, "event_id_conflict");
});
test("sequence exhaustion rejects new writes but permits replay", () => {
  const full = {...request, last_sequence: Number.MAX_SAFE_INTEGER};
  assert.equal(plan(full).reason_code, "event_sequence_exhausted");
  assert.equal(plan({...full, existing: {...event, append_sequence: 3}}).kind, "replay");
  for (const last_sequence of [true, -1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => plan({...request, last_sequence}));
  }
});
test("stored identities must refer to the requested event and a real sequence", () => {
  for (const existing of [{...event, append_sequence: 8}, {...event, append_sequence: 0},
    {...event, event_id: "other", append_sequence: 3}]) assert.throws(() => plan({...request, existing}));
});
