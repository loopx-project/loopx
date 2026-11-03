import assert from "node:assert/strict";
import { test } from "node:test";
import type { Runtime, Session, SessionOptions } from "@botiverse/oar";
import { createSessionKernel, sealSession } from "@botiverse/oar/kernel";
import { scriptedRuntime, type ScriptedTurn } from "@botiverse/oar/testing";
import {
  openOarSession,
  runOarTurn,
  type OarOutputRecord,
  type OarSessionConfig,
} from "./runtime.ts";

const config: OarSessionConfig = { runtime: "codex", cwd: process.cwd() };

function deferred<T>() { return Promise.withResolvers<T>(); }

function fixture(turn: (turn: ScriptedTurn) => void | Promise<void>) {
  const scripted = scriptedRuntime({ id: "codex", turn });
  const sessions: Session[] = [];
  const opens: SessionOptions[] = [];
  let disposed = 0;
  const runtime: { -readonly [K in keyof Runtime]: Runtime[K] } = {
    ...scripted,
    session: async (installation, options) => {
      opens.push(options);
      const session = await scripted.session(installation, options);
      const owned: Session = {
        ...session,
        dispose: async () => { disposed += 1; await session.dispose(); },
      };
      sessions.push(owned);
      return owned;
    },
  };
  return { runtime, sessions, opens, disposed: () => disposed, dependencies: { loadRuntime: () => runtime } };
}

test("bounded execution uses real OAR controls and emits a result after native cleanup", async () => {
  const host = fixture(async (turn) => {
    turn.think("plan");
    await turn.tool("read", "fixture", () => "contents");
    turn.say("answer");
  });
  const records: OarOutputRecord[] = [];
  const result = await runOarTurn(config, "question", { onRecord: (record) => { records.push(record); } }, host.dependencies);
  assert.equal(result.outcome.kind, "completed");
  assert.equal(result.cleanupConfirmed, true);
  assert.equal(result.text, "answer");
  assert.ok(result.sessionId);
  assert.ok(result.usage && result.usage.output > 0);
  assert.equal(host.disposed(), 1);
  assert.equal(records.filter((record) => record.type === "result").length, 1);
  assert.equal(records.at(-1)?.type, "result");
  assert.ok(records.some((record) => record.type === "native" && record.frame.kind === "response" && record.frame.body.kind === "exited"));
  assert.ok(records.some((record) => record.type === "event" && record.event.kind === "tool_call_ended"));
  assert.ok(records.some((record) => record.type === "event" && record.event.kind === "model"), "opening events are replayed");
  assert.deepEqual(host.opens[0]?.launchArgs?.slice(-2), ["-c", 'sandbox_mode="read-only"']);
});

test("the admitted Codex sandbox overrides caller launch arguments and preserves native resume options", async () => {
  const host = fixture((turn) => { turn.say("ok"); });
  let probeEnv: unknown;
  host.runtime.installation = async (options) => {
    probeEnv = options?.env;
    return { kind: "available", via: "bundled" };
  };
  const expected: OarSessionConfig = {
    ...config,
    command: "/synthetic/codex",
    sandbox: "workspace-write",
    launchArgs: ["-c", 'sandbox_mode="danger-full-access"', "-c", 'service_tier="fast"'],
    env: { OAR_TEST_VALUE: "one", OAR_TEST_REMOVED: null },
    resume: "saved-native-session",
    model: "configured-model",
    serviceTier: "fast",
    outputSchema: { type: "object" },
    mcpServers: [{ name: "fixture", command: "fixture-tool" }],
  };
  const result = await runOarTurn(expected, "continue", {}, host.dependencies);
  assert.equal(result.sessionId, "saved-native-session");
  assert.equal(result.outcome.kind, "completed");
  assert.deepEqual(probeEnv, { ...expected.env, OAR_CODEX_BIN: expected.command });
  assert.deepEqual(host.opens[0], {
    cwd: expected.cwd,
    env: expected.env,
    model: expected.model,
    serviceTier: expected.serviceTier,
    resume: expected.resume,
    outputSchema: expected.outputSchema,
    mcpServers: expected.mcpServers,
    launchArgs: [...expected.launchArgs!, "-c", 'sandbox_mode="workspace-write"'],
  });
  assert.equal(process.env.OAR_TEST_VALUE, undefined, "the parent environment is untouched");
});

test("a missing resume target preserves its typed error and never starts a replacement conversation", async () => {
  const host = fixture(() => { assert.fail("must not prompt"); });
  let opens = 0;
  host.runtime.session = async () => {
    opens += 1;
    const missing = Object.assign(new Error("native session missing", { cause: { method: "thread/resume", native: { credential: "private" } } }), {
      name: "SessionNotFoundError", sessionId: "saved-session",
    });
    throw missing;
  };
  const records: OarOutputRecord[] = [];
  const result = await runOarTurn({ ...config, resume: "saved-session" }, "continue", {
    onRecord: (record) => { records.push(record); },
  }, host.dependencies);
  assert.equal(opens, 1);
  assert.equal(result.outcome.kind, "failed");
  assert.equal(result.sessionId, null);
  assert.equal(result.error?.name, "SessionNotFoundError");
  assert.equal(result.error?.sessionId, "saved-session");
  assert.equal(result.error?.method, "thread/resume");
  assert.ok(!JSON.stringify(result).includes("private"));
  assert.deepEqual(records.map((record) => record.type), ["result"]);
});

test("busy rejection is a failed request without adopting the running turn's text", async () => {
  const started = deferred<void>();
  const finish = deferred<void>();
  const host = fixture(async (turn) => {
    started.resolve();
    turn.say("first answer");
    await finish.promise;
  });
  const managed = await openOarSession(config, {}, host.dependencies);
  try {
    const first = managed.runTurn("first");
    await started.promise;
    const second = await managed.runTurn("second");
    assert.equal(second.outcome.kind, "failed");
    assert.equal(second.rejection?.code, "busy");
    assert.equal(second.text, "");
    finish.resolve();
    assert.equal((await first).text, "first answer");
    assert.equal(host.disposed(), 0, "a reusable session stays open between turns");
  } finally { finish.resolve(); await managed.dispose(); }
});

test("reusable sessions keep native identity and expose accepted steering", async () => {
  const started = deferred<void>();
  const finish = deferred<void>();
  let turns = 0;
  const host = fixture(async (turn) => {
    turns += 1;
    if (turns === 1) {
      started.resolve();
      await finish.promise;
      turn.say(turn.steered.join(" "));
    } else turn.say("next");
  });
  const managed = await openOarSession(config, {}, host.dependencies);
  try {
    const first = managed.runTurn("first");
    await started.promise;
    assert.equal((await managed.steer("correction")).kind, "accepted");
    finish.resolve();
    const firstResult = await first;
    const secondResult = await managed.runTurn("second");
    assert.equal(firstResult.text, "correction");
    assert.equal(secondResult.text, "next");
    assert.equal(firstResult.sessionId, secondResult.sessionId);
  } finally { finish.resolve(); await managed.dispose(); }
  await managed.dispose();
  assert.equal(host.disposed(), 1);
});

test("an already-cancelled invocation does not load or launch a runtime", async () => {
  const controller = new AbortController();
  controller.abort();
  const result = await runOarTurn(config, "unused", { signal: controller.signal }, {
    loadRuntime: () => { assert.fail("must not load"); },
  });
  assert.equal(result.outcome.kind, "aborted");
  assert.equal(result.sessionId, null);
  assert.equal(result.cleanupConfirmed, true, "no session creation began");
});

test("installation failure confirms cleanup only after the probe has settled", async () => {
  const host = fixture(() => { assert.fail("must not prompt"); });
  host.runtime.installation = async () => ({ kind: "not_found" });
  const absent = await runOarTurn(config, "unused", {}, host.dependencies);
  assert.equal(absent.outcome.kind, "failed");
  assert.equal(absent.cleanupConfirmed, true);

  const probing = deferred<void>();
  const release = deferred<void>();
  host.runtime.installation = async () => { probing.resolve(); await release.promise; return { kind: "not_found" }; };
  const controller = new AbortController();
  const run = runOarTurn(config, "unused", { signal: controller.signal }, host.dependencies);
  await probing.promise;
  controller.abort();
  const cancelled = await run;
  assert.equal(cancelled.cleanupConfirmed, false, "an in-flight probe may still own a process");
  release.resolve();
});

test("cancellation during open disposes the late session without submitting work", async () => {
  const opening = deferred<Session>();
  const started = deferred<void>();
  const disposed = deferred<void>();
  const host = fixture(() => { assert.fail("must not prompt after cancellation"); });
  host.runtime.session = async () => { started.resolve(); return opening.promise; };
  const controller = new AbortController();
  const run = runOarTurn(config, "unused", { signal: controller.signal }, host.dependencies);
  await started.promise;
  controller.abort();
  const cancelled = await run;
  assert.equal(cancelled.outcome.kind, "aborted");
  assert.equal(cancelled.cleanupConfirmed, false, "startup is still in flight");
  const scripted = scriptedRuntime({ turn: () => { assert.fail("must not prompt"); } });
  const late = await scripted.session({ kind: "available", via: "bundled" }, { cwd: config.cwd });
  opening.resolve({ ...late, dispose: async () => { await late.dispose(); disposed.resolve(); } });
  await disposed.promise;
});

test("turn timeout interrupts and disposes exactly once instead of treating partial text as success", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const started = deferred<void>();
  const finish = deferred<void>();
  const host = fixture(async (turn) => { turn.say("partial"); started.resolve(); await finish.promise; });
  const run = runOarTurn(config, "slow", { timeoutMs: 100 }, host.dependencies);
  await started.promise;
  t.mock.timers.tick(101);
  const result = await run;
  finish.resolve();
  assert.equal(result.outcome.kind, "failed");
  assert.equal(result.timedOut, true);
  assert.equal(result.cleanupConfirmed, true);
  assert.equal(result.text, "partial");
  assert.equal(host.disposed(), 1);
});

test("cancellation does not wait for a stuck prompt acknowledgement or abort acknowledgement", async () => {
  const admitted = deferred<void>();
  const pending = deferred<never>();
  const host = fixture(() => {});
  const start = host.runtime.session;
  host.runtime.session = async (installation, options) => {
    const session = await start(installation, options);
    return {
      ...session,
      prompt: async () => { admitted.resolve(); return pending.promise; },
      abort: async () => pending.promise,
    };
  };
  const controller = new AbortController();
  const run = runOarTurn(config, "pending", { signal: controller.signal }, host.dependencies);
  await admitted.promise;
  controller.abort();
  const result = await run;
  assert.equal(result.outcome.kind, "aborted");
  assert.equal(host.disposed(), 1);
});

test("only the root session's final root message becomes the result", async () => {
  const host = fixture(() => {});
  host.runtime.session = async () => {
    const kernel = createSessionKernel("root-session");
    return sealSession({
      id: kernel.sessionId,
      capabilities: { queue: { durable: false }, attribution: "nested", images: false },
      prompt: async (input, inputOptions) => kernel.control({ kind: "prompt", input, ...inputOptions }, () => {
        queueMicrotask(() => {
          kernel.frame({ type: "text", native: {}, events: [{ kind: "text_delta", text: "commentary", messageId: "commentary" }] });
          kernel.frame({ type: "child", native: {}, events: [{ kind: "text_delta", text: "wrong child answer" }, { kind: "turn_ended", outcome: { kind: "completed" } }] }, { sessionId: "child-session" });
          kernel.frame({ type: "nested", native: {}, events: [{ kind: "text_delta", text: "wrong nested answer" }] }, { agentPath: ["child"] });
          kernel.frame({ type: "text", native: {}, events: [{ kind: "text_delta", text: "final ", messageId: "final" }, { kind: "text_delta", text: "answer", messageId: "final" }] });
          kernel.frame({ type: "end", native: {}, events: [{ kind: "turn_ended", outcome: { kind: "completed" } }] });
          kernel.frame({ type: "next-turn", native: {}, events: [{ kind: "text_delta", text: "another turn", messageId: "later" }] });
        });
        return { kind: "accepted" };
      }),
      queue: async (input, inputOptions) => kernel.control({ kind: "queue", input, ...inputOptions }, () => ({ kind: "accepted" })),
      abort: async () => kernel.control({ kind: "abort" }, () => ({ kind: "rejected", code: "no_active_turn", reason: "idle" })),
      rawEvents: (callback, cursor) => kernel.rawEvents(callback, cursor),
      records: () => kernel.records(),
      graph: () => kernel.graph(),
      dispose: async () => { const request = kernel.request("toRuntime", { kind: "dispose" }); kernel.respond(request.id, { kind: "exited", code: 0 }); },
    });
  };
  const result = await runOarTurn(config, "question", {}, host.dependencies);
  assert.equal(result.outcome.kind, "completed");
  assert.equal(result.sessionId, "root-session");
  assert.equal(result.text, "final answer");
});

test("cleanup failure cannot produce a successful terminal receipt", async () => {
  const host = fixture((turn) => { turn.say("generated output"); });
  const open = host.runtime.session;
  host.runtime.session = async (installation, options) => {
    const session = await open(installation, options);
    return { ...session, dispose: async () => { await session.dispose(); throw new Error("cleanup failed"); } };
  };
  const records: OarOutputRecord[] = [];
  const result = await runOarTurn(config, "question", { onRecord: (record) => { records.push(record); } }, host.dependencies);
  assert.equal(result.outcome.kind, "failed");
  assert.equal(result.error?.message, "cleanup failed");
  assert.equal(result.cleanupConfirmed, false);
  assert.equal(result.text, "generated output");
  assert.equal(records.filter((record) => record.type === "result").length, 1);
});

test("cleanup has a bounded wait and reports an unconfirmed exit as failure", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const disposing = deferred<void>();
  const pending = deferred<void>();
  const host = fixture((turn) => { turn.say("output"); });
  const open = host.runtime.session;
  host.runtime.session = async (installation, options) => {
    const session = await open(installation, options);
    return { ...session, dispose: async () => { disposing.resolve(); await pending.promise; await session.dispose(); } };
  };
  const run = runOarTurn(config, "question", {}, host.dependencies);
  await disposing.promise;
  t.mock.timers.tick(5_001);
  const result = await run;
  pending.resolve();
  assert.equal(result.outcome.kind, "failed");
  assert.match(result.error?.message ?? "", /exit is unconfirmed/);
  assert.equal(result.cleanupConfirmed, false);
});

test("typed startup failures redact configured credentials without discarding the failure class", async () => {
  const host = fixture(() => {});
  host.runtime.session = async () => {
    throw Object.assign(new Error("Rejected secret-test-key"), {
      name: "RuntimeFailureError", failure: "auth", status: 401, credential: "rejected",
    });
  };
  const result = await runOarTurn({ ...config, env: { TEST_API_KEY: "secret-test-key" } }, "question", {}, host.dependencies);
  assert.deepEqual(result.outcome, {
    kind: "failed", failure: "auth", reason: "Rejected [redacted]", status: 401, credential: "rejected",
  });
  assert.ok(!JSON.stringify(result).includes("secret-test-key"));
});

test("observer exceptions cannot skip native cleanup", async () => {
  const host = fixture((turn) => { turn.say("done"); });
  const result = await runOarTurn(config, "question", {
    onRecord: () => { throw new Error("observer failed"); },
  }, host.dependencies);
  assert.equal(result.outcome.kind, "completed");
  assert.equal(host.disposed(), 1);
});
