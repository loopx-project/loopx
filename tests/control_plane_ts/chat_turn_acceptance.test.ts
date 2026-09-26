import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";

import {
  CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA,
  CHAT_TURN_ACCEPTANCE_REQUEST_SCHEMA,
  planChatTurnAcceptance,
} from "../../loopx/control_plane/turn_driver/chat_turn_acceptance.ts";

function sha256(value: string): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function textDigest(value: string): string {
  return sha256(value);
}

function jsonDigest(value: unknown): string {
  return sha256(JSON.stringify(value));
}

function packet() {
  return {
    schema_version: CHAT_TURN_ACCEPTANCE_REQUEST_SCHEMA,
    request: {
      session_id: "session-one",
      client_turn_id: "client-one",
      execution_message_sha256: textDigest("inspect"),
      display_message_sha256: textDigest("inspect"),
      attachments_sha256: jsonDigest(null),
      origin: "web",
      loopx_execution: false,
      loopx_request_sha256: jsonDigest(null),
    },
    candidate: {
      turn_id: "candidate-turn",
      message_id: "candidate-message",
      accepted_at: "2026-09-27T00:00:00Z",
    },
    session: {
      status: "ready",
      active_turn_id: null,
    },
    active_turn: null,
    matching_turn: null,
    transcript: {count: 0},
    queued_event: {count: 0},
    prepared_turn: {count: 0},
  };
}

function preparedPacket(options: {
  transcript?: "absent" | "single";
  queuedEvent?: "absent" | "single";
  active?: boolean;
} = {}) {
  const input = packet();
  const created = planChatTurnAcceptance(input);
  assert.equal(created.kind, "accepted");
  if (created.kind !== "accepted") throw new Error("expected accepted plan");
  const active = options.active ?? true;
  return {
    ...input,
    session: {
      status: active ? "busy" : "ready",
      active_turn_id: active ? "accepted-turn" : null,
    },
    active_turn: active
      ? {turn_id: "accepted-turn", status: "queued"}
      : null,
    matching_turn: {
      turn_id: "accepted-turn",
      client_turn_id: "client-one",
      status: "queued",
      execution_message_sha256: input.request.execution_message_sha256,
      origin: "web",
      loopx_execution: false,
      loopx_request_sha256: input.request.loopx_request_sha256,
      acceptance: {
        schema_version: CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA,
        phase: "prepared",
        request_sha256: created.request_sha256,
        message_id: "accepted-message",
        display_message_sha256: input.request.display_message_sha256,
        attachments_sha256: input.request.attachments_sha256,
      },
    },
    transcript: options.transcript === "single"
      ? {
          count: 1,
          message_id: "accepted-message",
          display_message_sha256: input.request.display_message_sha256,
          attachments_sha256: input.request.attachments_sha256,
          origin: "web",
        }
      : {count: 0},
    queued_event: options.queuedEvent === "single"
      ? {count: 1, payload_sha256: jsonDigest({})}
      : {count: 0},
    prepared_turn: {
      count: 1,
      turn_id: "accepted-turn",
      client_turn_id: "client-one",
    },
  };
}

function settledPacket(status: string) {
  const input = preparedPacket({
    transcript: "single",
    queuedEvent: "single",
  });
  return {
    ...input,
    session: {
      status: status === "completed" ? "ready" : "busy",
      active_turn_id: status === "completed" ? null : "accepted-turn",
    },
    active_turn: status === "completed"
      ? null
      : {turn_id: "accepted-turn", status},
    matching_turn: {
      ...input.matching_turn,
      status,
      acceptance: null,
    },
    prepared_turn: {count: 0},
  };
}

test("a new request receives one complete acceptance plan", () => {
  const result = planChatTurnAcceptance(packet());

  assert.equal(result.kind, "accepted");
  if (result.kind !== "accepted") return;
  assert.equal(result.disposition, "created");
  assert.equal(result.created, true);
  assert.equal(result.turn_id, "candidate-turn");
  assert.equal(result.message_id, "candidate-message");
  assert.match(result.request_sha256, /^sha256:[0-9a-f]{64}$/);
  assert.deepEqual(result.writes, {
    prepare_turn: true,
    activate_session: true,
    append_message: true,
    append_queued_event: true,
    settle_turn: true,
  });
  assert.deepEqual(result.dispatch, {kind: "required"});
});

for (const testCase of [
  {
    name: "prepared Turn only",
    input: preparedPacket({active: false}),
    expected: {
      prepare_turn: false,
      activate_session: true,
      append_message: true,
      append_queued_event: true,
      settle_turn: true,
    },
  },
  {
    name: "active Turn and transcript",
    input: preparedPacket({transcript: "single"}),
    expected: {
      prepare_turn: false,
      activate_session: false,
      append_message: false,
      append_queued_event: true,
      settle_turn: true,
    },
  },
  {
    name: "complete durable prefix",
    input: preparedPacket({
      transcript: "single",
      queuedEvent: "single",
    }),
    expected: {
      prepare_turn: false,
      activate_session: false,
      append_message: false,
      append_queued_event: false,
      settle_turn: true,
    },
  },
]) {
  test(`an exact retry repairs ${testCase.name}`, () => {
    const result = planChatTurnAcceptance(testCase.input);

    assert.equal(result.kind, "accepted");
    if (result.kind !== "accepted") return;
    assert.equal(result.disposition, "repaired");
    assert.equal(result.created, false);
    assert.deepEqual(result.writes, testCase.expected);
    assert.deepEqual(result.dispatch, {kind: "required"});
  });
}

test("the durable request digest rejects every changed identity field", () => {
  const original = preparedPacket();
  const changes = [
    {execution_message_sha256: textDigest("changed")},
    {display_message_sha256: textDigest("changed")},
    {attachments_sha256: jsonDigest([{id: "image-two"}])},
    {origin: "external"},
    {loopx_execution: true},
    {loopx_request_sha256: jsonDigest({operation: "start"})},
  ];

  for (const change of changes) {
    const result = planChatTurnAcceptance({
      ...original,
      request: {...original.request, ...change},
    });
    assert.deepEqual(result, {
      schema_version: "loopx_chat_turn_acceptance_result_v0",
      kind: "rejected",
      code: "request_conflict",
    });
  }
});

test("capsule corruption fails closed before retry identity is considered", () => {
  const input = preparedPacket();
  const result = planChatTurnAcceptance({
    ...input,
    matching_turn: {
      ...input.matching_turn,
      execution_message_sha256: textDigest("corrupted"),
    },
    request: {
      ...input.request,
      execution_message_sha256: textDigest("other retry"),
    },
  });

  assert.deepEqual(result, {
    schema_version: "loopx_chat_turn_acceptance_result_v0",
    kind: "rejected",
    code: "durable_state_conflict",
  });
});

test("another request cannot displace a prepared Turn", () => {
  const input = packet();
  const result = planChatTurnAcceptance({
    ...input,
    session: {status: "busy", active_turn_id: "other-turn"},
    active_turn: {turn_id: "other-turn", status: "queued"},
    prepared_turn: {
      count: 1,
      turn_id: "other-turn",
      client_turn_id: "other-client",
    },
  });

  assert.deepEqual(result, {
    schema_version: "loopx_chat_turn_acceptance_result_v0",
    kind: "rejected",
    code: "active_turn_conflict",
    active_turn_id: "other-turn",
  });
});

for (const [status, dispatch] of [
  ["queued", {kind: "required"}],
  ["starting", {kind: "not_required", reason: "already_started"}],
  ["running", {kind: "not_required", reason: "already_started"}],
  ["completing", {kind: "not_required", reason: "completion_in_progress"}],
  ["interrupting", {kind: "not_required", reason: "completion_in_progress"}],
  ["completed", {kind: "not_required", reason: "terminal"}],
] as const) {
  test(`settled ${status} replay has an independent dispatch decision`, () => {
    const result = planChatTurnAcceptance(settledPacket(status));

    assert.equal(result.kind, "accepted");
    if (result.kind !== "accepted") return;
    assert.equal(result.disposition, "replayed");
    assert.equal(result.created, false);
    assert.deepEqual(result.dispatch, dispatch);
    assert.equal(Object.values(result.writes).some(Boolean), false);
  });
}

test("a legacy Turn without its original transcript remains unavailable", () => {
  const input = settledPacket("queued");
  const result = planChatTurnAcceptance({
    ...input,
    transcript: {count: 0},
  });

  assert.deepEqual(result, {
    schema_version: "loopx_chat_turn_acceptance_result_v0",
    kind: "rejected",
    code: "original_request_unavailable",
  });
});

for (const status of ["completing", "interrupting"] as const) {
  test(`${status} replay remains valid after the Session releases the Turn`, () => {
    const input = settledPacket(status);
    const result = planChatTurnAcceptance({
      ...input,
      session: {status: "ready", active_turn_id: null},
      active_turn: null,
    });

    assert.equal(result.kind, "accepted");
    if (result.kind !== "accepted") return;
    assert.equal(result.disposition, "replayed");
    assert.deepEqual(result.dispatch, {
      kind: "not_required",
      reason: "completion_in_progress",
    });
  });
}

test("duplicate transcript rows or queued events fail closed", () => {
  const input = preparedPacket();
  for (const change of [
    {transcript: {count: 2}},
    {queued_event: {count: 2}},
    {queued_event: {count: 1, payload_sha256: jsonDigest({changed: true})}},
  ]) {
    const result = planChatTurnAcceptance({...input, ...change});
    assert.equal(result.kind, "rejected");
    if (result.kind === "rejected") {
      assert.equal(result.code, "durable_state_conflict");
    }
  }
});

test("repair accepts only a durable prefix of the declared write order", () => {
  const messageWithoutSession = preparedPacket({
    active: false,
    transcript: "single",
  });
  const eventWithoutMessage = preparedPacket({queuedEvent: "single"});

  for (const input of [messageWithoutSession, eventWithoutMessage]) {
    const result = planChatTurnAcceptance(input);
    assert.equal(result.kind, "rejected");
    if (result.kind === "rejected") {
      assert.equal(result.code, "durable_state_conflict");
    }
  }
});

test("the Effect boundary rejects malformed digests and observations", () => {
  assert.throws(
    () => planChatTurnAcceptance({
      ...packet(),
      request: {...packet().request, attachments_sha256: "not-a-digest"},
    }),
    /attachments_sha256 must be a SHA-256 digest/,
  );
  assert.throws(
    () => planChatTurnAcceptance({
      ...packet(),
      transcript: {count: -1},
    }),
    /count must not be negative/,
  );
});
