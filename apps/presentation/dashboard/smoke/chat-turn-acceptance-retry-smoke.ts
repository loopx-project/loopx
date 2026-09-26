import assert from "node:assert/strict";

import {
  ChatApiError,
  acceptChatTurn,
} from "../src/data/chat.ts";

const acceptedResponse = {
  ok: true,
  session_id: "session-one",
  turn_id: "turn-one",
  created: false,
  status: "queued",
  events_url: "/api/chat/sessions/session-one/turns/turn-one/events",
};
const originalFetch = globalThis.fetch;

try {
  const bodies: string[] = [];
  globalThis.fetch = async (_input, init) => {
    bodies.push(String(init?.body));
    if (bodies.length === 1) {
      throw new TypeError("response connection closed");
    }
    return new Response(JSON.stringify(acceptedResponse), {
      status: 202,
      headers: {"Content-Type": "application/json"},
    });
  };

  const accepted = await acceptChatTurn(
    "session-one",
    "continue",
    "stable-client-turn",
  );
  assert.deepEqual(accepted, acceptedResponse);
  assert.equal(bodies.length, 2);
  assert.deepEqual(
    bodies.map((body) => JSON.parse(body).client_turn_id),
    ["stable-client-turn", "stable-client-turn"],
  );

  const interruptedBodies: string[] = [];
  globalThis.fetch = async (_input, init) => {
    interruptedBodies.push(String(init?.body));
    if (interruptedBodies.length === 1) {
      const body = new ReadableStream({
        start(controller) {
          controller.error(new TypeError("response body connection closed"));
        },
      });
      return new Response(body, {
        status: 202,
        headers: {"Content-Type": "application/json"},
      });
    }
    return new Response(JSON.stringify(acceptedResponse), {
      status: 202,
      headers: {"Content-Type": "application/json"},
    });
  };

  await acceptChatTurn(
    "session-one",
    "continue after body loss",
    "stable-body-loss-turn",
  );
  assert.deepEqual(
    interruptedBodies.map((body) => JSON.parse(body).client_turn_id),
    ["stable-body-loss-turn", "stable-body-loss-turn"],
  );

  const unavailableBodies: string[] = [];
  globalThis.fetch = async (_input, init) => {
    unavailableBodies.push(String(init?.body));
    if (unavailableBodies.length === 1) {
      return new Response(JSON.stringify({error: "temporarily unavailable"}), {
        status: 503,
        headers: {"Content-Type": "application/json"},
      });
    }
    return new Response(JSON.stringify(acceptedResponse), {
      status: 202,
      headers: {"Content-Type": "application/json"},
    });
  };

  await acceptChatTurn(
    "session-one",
    "continue after unavailable",
    "stable-unavailable-turn",
  );
  assert.deepEqual(
    unavailableBodies.map((body) => JSON.parse(body).client_turn_id),
    ["stable-unavailable-turn", "stable-unavailable-turn"],
  );

  const resumeFailureBodies: string[] = [];
  globalThis.fetch = async (_input, init) => {
    resumeFailureBodies.push(String(init?.body));
    if (resumeFailureBodies.length === 1) {
      return new Response(JSON.stringify({
        error: "adapter startup interrupted",
        error_code: "resume_failed",
      }), {
        status: 424,
        headers: {"Content-Type": "application/json"},
      });
    }
    return new Response(JSON.stringify(acceptedResponse), {
      status: 202,
      headers: {"Content-Type": "application/json"},
    });
  };

  await acceptChatTurn(
    "session-one",
    "continue after adapter recovery",
    "stable-resume-turn",
  );
  assert.deepEqual(
    resumeFailureBodies.map((body) => JSON.parse(body).client_turn_id),
    ["stable-resume-turn", "stable-resume-turn"],
  );

  let conflictCalls = 0;
  globalThis.fetch = async () => {
    conflictCalls += 1;
    return new Response(
      JSON.stringify({
        ok: false,
        error: "another turn is active",
        active_turn_id: "turn-two",
      }),
      {
        status: 409,
        headers: {"Content-Type": "application/json"},
      },
    );
  };
  await assert.rejects(
    acceptChatTurn(
      "session-one",
      "different request",
      "different-client-turn",
    ),
    (error: unknown) => (
      error instanceof ChatApiError
      && error.payload.http_status === 409
    ),
  );
  assert.equal(conflictCalls, 1);
} finally {
  globalThis.fetch = originalFetch;
}
