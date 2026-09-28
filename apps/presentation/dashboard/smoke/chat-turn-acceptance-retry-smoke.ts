import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {resolve} from "node:path";
import {createInterface} from "node:readline";

import {resolveTestPython} from "../../../../scripts/test-python.mjs";
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

type FetchTrace = {
  body: string;
  responseBody: string;
  status: number;
};

function parseObject(line: string): Record<string, unknown> {
  const value: unknown = JSON.parse(line);
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("expected a JSON object");
  }
  return value;
}

function requiredString(
  value: Record<string, unknown>,
  key: string,
): string {
  const field = value[key];
  if (typeof field !== "string") {
    throw new TypeError(`${key} must be a string`);
  }
  return field;
}

async function readRequiredLine(
  lines: AsyncIterableIterator<string>,
): Promise<string> {
  const line = await lines.next();
  if (line.done) {
    throw new Error("acceptance HTTP fixture exited before returning a result");
  }
  return line.value;
}

function fetchInputUrl(input: string | URL | Request): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

async function expectChatApiError(
  operation: () => Promise<unknown>,
  expectedStatus: number,
): Promise<ChatApiError> {
  try {
    await operation();
  } catch (error) {
    assert.ok(error instanceof ChatApiError);
    assert.equal(error.payload.http_status, expectedStatus);
    return error;
  }
  throw new Error(`expected ChatApiError with HTTP ${expectedStatus}`);
}

async function runHttpRecoveryScenario(
  scenario: "before_transcript" | "after_queued",
): Promise<void> {
  const dashboardRoot = process.cwd();
  const repositoryRoot = resolve(dashboardRoot, "../../..");
  const fixturePath = resolve(
    dashboardRoot,
    "smoke/chat-turn-acceptance-http-fixture.py",
  );
  const python = resolveTestPython({repoRoot: repositoryRoot});
  const child = spawn(
    python,
    ["-u", fixturePath, scenario],
    {
      cwd: repositoryRoot,
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  const exited = once(child, "exit");
  let stderr = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr += String(chunk);
  });
  const output = createInterface({input: child.stdout});
  const lines = output[Symbol.asyncIterator]();
  const traces: FetchTrace[] = [];

  try {
    const fixture = parseObject(await readRequiredLine(lines));
    const origin = requiredString(fixture, "origin");
    const sessionId = requiredString(fixture, "session_id");
    const mismatchedSessionId = requiredString(
      fixture,
      "mismatched_session_id",
    );
    globalThis.fetch = async (input, init) => {
      const response = await originalFetch(
        new URL(fetchInputUrl(input), `${origin}/`),
        init,
      );
      traces.push({
        body: String(init?.body ?? ""),
        responseBody: await response.clone().text(),
        status: response.status,
      });
      return response;
    };

    const accepted = await acceptChatTurn(
      sessionId,
      "recover this request",
      "recoverable-request",
    );
    assert.equal(accepted.created, false);
    assert.deepEqual(
      traces.map(({status}) => status),
      [503, 202],
    );
    assert.deepEqual(
      traces.map(({body}) => parseObject(body).client_turn_id),
      ["recoverable-request", "recoverable-request"],
    );
    const unavailable = parseObject(traces[0].responseBody);
    assert.equal(
      unavailable.error_code,
      "chat_turn_acceptance_unavailable",
    );
    assert.equal(unavailable.turn_replay_safe, true);
    assert.equal(
      traces[0].responseBody.includes("private"),
      false,
    );

    traces.length = 0;
    await expectChatApiError(
      () => acceptChatTurn(sessionId, "", "invalid-request"),
      400,
    );
    assert.deepEqual(traces.map(({status}) => status), [400]);

    traces.length = 0;
    await expectChatApiError(
      () => acceptChatTurn(
        sessionId,
        "conflicting request",
        "conflicting-request",
      ),
      409,
    );
    assert.deepEqual(traces.map(({status}) => status), [409]);

    traces.length = 0;
    const mismatch = await expectChatApiError(
      () => acceptChatTurn(
        mismatchedSessionId,
        "must not write",
        "home-mismatch-request",
      ),
      424,
    );
    assert.equal(mismatch.payload.error_code, "codex_home_mismatch");
    assert.deepEqual(traces.map(({status}) => status), [424]);

    child.stdin.end("inspect\n");
    const summary = parseObject(await readRequiredLine(lines));
    assert.deepEqual(summary, {
      fault_injected: true,
      turn_count: 1,
      user_message_count: 1,
      queued_event_count: 1,
      dispatch_count: 1,
      acceptance_capsule_present: false,
      mismatched_turn_count: 0,
      mismatched_message_count: 0,
      mismatched_session_status: "ready",
    });
    const [exitCode] = await exited;
    assert.equal(exitCode, 0, stderr);
  } finally {
    globalThis.fetch = originalFetch;
    output.close();
    if (child.exitCode === null) {
      child.kill();
      await exited;
    }
  }
}

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

  await runHttpRecoveryScenario("before_transcript");
  await runHttpRecoveryScenario("after_queued");
} finally {
  globalThis.fetch = originalFetch;
}
