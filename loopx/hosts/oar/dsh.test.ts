import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { awaitTurnEnd } from "@botiverse/oar/observe";
import type { Event, RawEvent, Session } from "@botiverse/oar";
import { resolveTestPython } from "../../../scripts/test-python.mjs";
import { createDshRuntime } from "./dsh.ts";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const python = resolveTestPython();
const fakeSdk = `
import json, os, time
from pathlib import Path
from types import SimpleNamespace

class DeepSeekHarnessConfig:
    def __init__(self, **kwargs): self.kwargs = kwargs

class DeepSeekHarness:
    def __init__(self, config): self.config = config.kwargs
    def __enter__(self): return self
    def __exit__(self, *args): Path(os.environ['OAR_TEST_CLOSED']).write_text('closed')
    def run(self, prompt, *, session_id, on_notification):
        Path(os.environ['OAR_TEST_CAPTURE']).write_text(json.dumps({
            'config': self.config, 'prompt': prompt, 'session_id': session_id,
            'permission': os.environ.get('DSH_PERMISSION_MODE'),
            'removed': os.environ.get('OAR_TEST_REMOVE'), 'pid': os.getpid(),
        }))
        print('SDK diagnostic stays off the JSONL stream')
        events = []
        def emit(kind, data, child=False):
            event = {'type': kind, 'data': data}
            if not child: events.append(event)
            on_notification(SimpleNamespace(method='session.event', payload={
                'sessionId': 'child-session' if child else session_id, 'event': event,
            }))
        emit('turn/start', {'turn': 1})
        if prompt == 'wait':
            while True: time.sleep(0.01)
        if prompt == 'exit': os._exit(0)
        if prompt == 'exception':
            error = RuntimeError('provider refused ' + os.environ['DEEPSEEK_API_KEY'])
            error.code = 'rate_limit'
            raise error
        emit('assistant/message', {'message': {'id': 'child-message', 'content': [
            {'type': 'text', 'text': 'child output must remain separate'},
        ]}}, True)
        emit('request/context', {'model': self.config.get('model')})
        emit('tool/call', {'callId': 'call-1', 'name': 'read', 'arguments': '{"path":"README.md"}'})
        emit('tool/result', {'message': {'toolCallId': 'call-1', 'isError': False,
            'content': [{'type': 'text', 'text': 'tool result'}]}})
        emit('assistant/message', {'message': {'id': 'answer', 'content': [
            {'type': 'reasoning', 'text': 'checked the result'},
            {'type': 'text', 'text': 'root answer'},
        ]}, 'usage': {'inputTokens': 5, 'outputTokens': 4, 'cacheReadTokens': 7,
                     'cacheWriteTokens': 2, 'reasoningTokens': 1}})
        reason = {'kind': 'completed'}
        if prompt == 'limit': reason = {'kind': 'max-tokens'}
        if prompt == 'failure': reason = {'kind': 'error', 'error': {
            'code': 'rate_limit', 'message': 'provider refused ' + os.environ['DEEPSEEK_API_KEY'],
        }}
        if prompt == 'unknown': reason = {'kind': 'extension-closed'}
        if prompt != 'missing': emit('turn/end', {'turn': 1, 'reason': reason})
        return SimpleNamespace(final_response='root answer', events=events,
            finish_reason=None if prompt == 'missing' else 'error' if prompt == 'contradictory' else reason['kind'])
`;

async function fixture(t: TestContext, additionalEnv: Record<string, string | null> = {}) {
  const directory = await mkdtemp(join(tmpdir(), "loopx-oar-dsh-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  await writeFile(join(directory, "deepseek_harness.py"), fakeSdk);
  const capture = join(directory, "capture.json"), closed = join(directory, "closed");
  const env = {
    PYTHONPATH: [directory, root].join(delimiter), DSH_PERMISSION_MODE: "read-only",
    DEEPSEEK_API_KEY: "synthetic-oar-secret", OAR_TEST_CAPTURE: capture, OAR_TEST_CLOSED: closed,
    ...additionalEnv,
  };
  const runtime = createDshRuntime({ python_executable: python,
    provider: "fixture-provider", dsh_home: join(directory, "sessions"), max_tokens: 256,
    cordis: join(directory, "settings.yml"), runtime_bin: join(directory, "native-dsh"),
    request_timeout_seconds: 45,
  });
  const installation = await runtime.installation!({ env });
  assert.equal(installation.kind, "available");
  if (installation.kind !== "available") throw new Error("fixture SDK unavailable");
  const session = await runtime.session(installation, {
    cwd: directory, model: "fixture-model", effort: "high", resume: "chosen-session", env,
  });
  const observed: Event[] = [];
  session.events((event) => observed.push(event));
  t.after(() => session.dispose());
  return { session, runtime, installation, env, directory, capture, closed, observed };
}

function resultFrame(session: Session): Extract<RawEvent, { kind: "frame" }> {
  const found = session.records().find((record) => record.kind === "frame" && record.body.type === "dsh/result");
  assert.ok(found && found.kind === "frame");
  return found;
}

test("DSH SDK transport preserves configuration, native identity, read-only mode and terminal evidence", async (t) => {
  const f = await fixture(t, { OAR_TEST_REMOVE: null });
  const prompt = await f.session.prompt("run");
  assert.equal(prompt.kind, "accepted");
  assert.deepEqual(await awaitTurnEnd(f.session, prompt.seq), { kind: "completed" });
  assert.ok(existsSync(f.closed), "SDK cleanup precedes the terminal OAR event");
  const captured = JSON.parse(await readFile(f.capture, "utf8"));
  assert.equal(captured.session_id, "chosen-session");
  assert.equal(captured.prompt, "run");
  assert.equal(captured.permission, "read-only");
  assert.equal(captured.config.env.DSH_PERMISSION_MODE, "read-only");
  assert.equal(captured.removed, null);
  assert.equal(captured.config.provider, "fixture-provider");
  assert.equal(captured.config.model, "fixture-model");
  assert.equal(captured.config.reasoning_effort, "high");
  assert.equal(captured.config.cwd, f.directory);
  assert.equal(captured.config.dsh_home, join(f.directory, "sessions"));
  assert.equal(captured.config.max_tokens, 256);
  assert.deepEqual(captured.config.patches, [join(f.directory, "settings.yml")]);
  assert.equal(captured.config.dsh_bin, join(f.directory, "native-dsh"));
  assert.equal(captured.config.request_timeout_seconds, 45);
  assert.deepEqual(f.observed.filter((event) => event.kind === "text_delta").map((event) => event.text), ["root answer"]);
  assert.equal(f.session.model().value, "fixture-model");
  assert.deepEqual(f.session.usage().value.total, { input: 14, output: 4, cacheRead: 7, cacheWrite: 2, reasoning: 1 });
  assert.ok(f.observed.some((event) => event.kind === "tool_call_started" && event.callId === "call-1"));
  assert.ok(f.observed.some((event) => event.kind === "tool_call_ended" && event.result === "ok"));
  assert.ok(f.session.records().some((record) => record.sessionId === "child-session"));
  const native = resultFrame(f.session).body.native as { final_response: string; finish_reason: string; events: unknown[] };
  assert.equal(native.final_response, "root answer");
  assert.equal(native.finish_reason, "completed");
  assert.ok(native.events.length > 0);
  assert.equal(f.observed.filter((event) => event.kind === "turn_ended").length, 1);
  assert.deepEqual(f.session.records().map((record) => record.seq), f.session.records().map((_, index) => index));
});

test("DSH owns one native Turn and refuses queued or repeated inputs before another SDK launch", async (t) => {
  const f = await fixture(t);
  assert.equal(f.session.withdraw, undefined, "there is no held queue to withdraw from");
  const queuedBeforePrompt = await f.session.queue("must not consume the first turn");
  assert.equal(queuedBeforePrompt.kind, "rejected");
  if (queuedBeforePrompt.kind === "rejected") assert.equal(queuedBeforePrompt.code, "unsupported");
  assert.equal(existsSync(f.capture), false);

  const prompt = await f.session.prompt("run");
  assert.equal(prompt.kind, "accepted");
  assert.deepEqual(await awaitTurnEnd(f.session, prompt.seq), { kind: "completed" });
  const captured = await readFile(f.capture, "utf8");
  for (const outcome of [await f.session.prompt("must not reopen the native id"), await f.session.queue("must not queue")]) {
    assert.equal(outcome.kind, "rejected");
    if (outcome.kind === "rejected") assert.equal(outcome.code, "unsupported");
  }
  assert.equal(f.session.id, "chosen-session");
  assert.equal(f.observed.filter((event) => event.kind === "turn_active").length, 1);
  await f.session.dispose();
  assert.equal(await readFile(f.capture, "utf8"), captured, "rejected inputs cannot overwrite the native invocation");
  const disposed = await f.session.prompt("after disposal");
  assert.equal(disposed.kind, "rejected");
  if (disposed.kind === "rejected") assert.equal(disposed.code, "disposed");
});

test("DSH never treats missing, contradictory, unknown or limited terminal evidence as completion", async (t) => {
  for (const input of ["missing", "contradictory", "unknown", "limit", "failure"]) {
    await t.test(input, async (child) => {
      const f = await fixture(child);
      const prompt = await f.session.prompt(input);
      const outcome = await awaitTurnEnd(f.session, prompt.seq);
      assert.equal(outcome.kind, "failed");
      if (input === "limit" && outcome.kind === "failed") assert.equal(outcome.failure, "limit_reached");
      if (input === "missing" || input === "contradictory") {
        assert.equal(f.observed.filter((event) => event.kind === "turn_ended").length, 0,
          "malformed transport must not invent a native turn-end fact");
      }
      assert.ok(resultFrame(f.session));
      assert.ok(!JSON.stringify(f.session.records()).includes("synthetic-oar-secret"));
      assert.ok(!JSON.stringify(outcome).includes("synthetic-oar-secret"));
      const repeated = await f.session.prompt("must not retry with the same or a fresh native id");
      assert.equal(repeated.kind, "rejected");
      if (repeated.kind === "rejected") {
        assert.equal(repeated.code, input === "missing" || input === "contradictory" ? "runtime_exited" : "unsupported");
      }
    });
  }
});

test("DSH busy controls and abort use OAR records and unwind the actual SDK worker", async (t) => {
  const f = await fixture(t);
  let ready!: () => void;
  const began = new Promise<void>((resolve) => { ready = resolve; });
  f.session.events((event) => { if (event.kind === "turn_active") ready(); });
  const first = await f.session.prompt("wait");
  await began;
  const second = await f.session.prompt("must not run");
  assert.equal(second.kind, "rejected");
  if (second.kind === "rejected") assert.equal(second.code, "busy");
  assert.equal(f.session.steer, undefined);
  const queued = await f.session.queue("must not be taken over while the first turn runs");
  assert.equal(queued.kind, "rejected");
  if (queued.kind === "rejected") assert.equal(queued.code, "unsupported");
  assert.equal(f.session.withdraw, undefined);
  assert.equal((await f.session.abort()).kind, "accepted");
  // Real callers abort and dispose together; this must not signal twice and
  // interrupt Python's finally block before it reaps its native runtime.
  await f.session.dispose();
  assert.equal((await awaitTurnEnd(f.session, first.seq)).kind, "aborted");
  assert.ok(existsSync(f.closed));
  assert.equal(f.observed.filter((event) => event.kind === "turn_ended").length, 0,
    "cancellation records the observed exit, never a fabricated native turn/end");
  assert.equal((await f.session.prompt("after disposal")).kind, "rejected");
});

test("DSH abort without dispose retains the native exit and cannot restart the Session", async (t) => {
  const f = await fixture(t);
  let ready!: () => void;
  const began = new Promise<void>((resolve) => { ready = resolve; });
  f.session.events((event) => { if (event.kind === "turn_active") ready(); });
  const first = await f.session.prompt("wait");
  await began;
  assert.equal((await f.session.abort()).kind, "accepted");
  assert.deepEqual(await awaitTurnEnd(f.session, first.seq), { kind: "aborted" });
  assert.ok(existsSync(f.closed), "abort alone still waits for SDK cleanup before its observed exit");
  assert.equal(f.session.records().some((record) => record.kind === "request"
    && record.direction === "toRuntime" && record.body.kind === "dispose"), false);
  const repeated = await f.session.prompt("must not reopen after abort");
  assert.equal(repeated.kind, "rejected");
  if (repeated.kind === "rejected") assert.equal(repeated.code, "runtime_exited");
  assert.equal(f.session.id, "chosen-session");
  const captured = JSON.parse(await readFile(f.capture, "utf8"));
  assert.equal(captured.prompt, "wait");
  assert.equal(captured.session_id, "chosen-session");
});

test("DSH unexpected zero exit and SDK exceptions cannot report a successful turn", async (t) => {
  for (const input of ["exit", "exception"]) {
    await t.test(input, async (child) => {
      const f = await fixture(child);
      const prompt = await f.session.prompt(input);
      const outcome = await awaitTurnEnd(f.session, prompt.seq);
      assert.equal(outcome.kind, "failed");
      assert.equal(f.observed.filter((event) => event.kind === "turn_ended").length, 0);
      if (input === "exception") {
        const error = f.session.records().find((record) => record.kind === "frame" && record.body.type === "dsh/error");
        assert.ok(error && error.kind === "frame");
        assert.equal((error.body.native as { failure_kind: string }).failure_kind, "rate_limited");
        assert.ok(!JSON.stringify(error).includes("synthetic-oar-secret"));
      }
    });
  }
});

test("DSH refuses unsupported policy and image options before spawning a worker", async (t) => {
  const f = await fixture(t);
  await assert.rejects(f.runtime.session(f.installation, {
    cwd: f.directory, disallowedTools: ["shell"],
  }), { name: "UnsupportedOptionError" });
  const prompt = await f.session.prompt("image", { images: [{ path: join(f.directory, "image.png") }] });
  assert.equal(prompt.kind, "rejected");
  if (prompt.kind === "rejected") assert.equal(prompt.code, "unsupported");
  assert.equal(existsSync(f.capture), false);
});
