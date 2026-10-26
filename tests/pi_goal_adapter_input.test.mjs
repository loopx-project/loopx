import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { createRequire, registerHooks, syncBuiltinESMExports } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createBindingStore, sessionKey } from "../loopx/pi_goal_mode/pi-goal-loop-runtime.mjs";

// Actual adapter and durable binding; SDK/UI and CLI admission are synthetic.
// Pi's input contract delivers queued steer/followUp without before_agent_start.
registerHooks({
  resolve(specifier, context, next) {
    if (["typebox", "@earendil-works/pi-ai", "@earendil-works/pi-tui"].includes(specifier))
      return { url: "pi-input-test:sdk", shortCircuit: true };
    return next(specifier, context);
  },
  load(url, context, next) {
    if (url === "pi-input-test:sdk") return {
      format: "module", shortCircuit: true,
      source: "export const Type = new Proxy({}, {get: () => () => ({})}); export const StringEnum = () => ({}); export class Text {}",
    };
    return next(url, context);
  },
});
let cliReply;
const childProcess = createRequire(import.meta.url)("node:child_process");
childProcess.execFile = Object.assign(() => {}, {
  [Symbol.for("nodejs.util.promisify.custom")]: async (_binary, args) => ({
    stdout: JSON.stringify(cliReply(args)),
  }),
});
syncBuiltinESMExports();
const { default: extension } = await import("../loopx/pi_goal_mode/loopx-goal.ts");

async function harness(t, { prime = true } = {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "pi-owner-input-"));
  const file = path.join(directory, "session.jsonl");
  const commands = new Map(), tools = new Map(), events = new Map();
  const outgoing = [], notices = [];
  let idle = true, probes = 0;
  cliReply = args => {
    if (args.includes("should-run")) {
      probes += 1;
      return { ok: true, should_run: true };
    }
    return {
      goal_id: "synthetic-goal", project: "synthetic-project",
      project_connection: { registry: "synthetic-registry", connection_state: "connected" },
      host_loop_activation: {
        activation_allowed: true, agent_id: "synthetic-agent",
        available_capabilities: ["task_lease_v0"],
      },
    };
  };
  extension({
    registerCommand: (name, command) => commands.set(name, command),
    registerTool: tool => tools.set(tool.name, tool),
    registerEntryRenderer() {}, appendEntry() {},
    on: (name, handler) => events.set(name, handler),
    sendUserMessage: text => outgoing.push(text),
  });
  const ctx = {
    cwd: directory, isIdle: () => idle,
    sessionManager: { getSessionFile: () => file },
    ui: { setWidget() {}, notify: (message, kind) => notices.push({message, kind}) },
  };
  const emit = (name, event = {}) => events.get(name)?.({type: name, ...event}, ctx);
  const command = text => commands.get("loopx").handler(text, ctx);
  const store = createBindingStore(directory);
  const read = () => store.read(sessionKey(file));
  const settle = async () => { idle = true; await emit("agent_settled"); };
  t.after(async () => {
    await emit("session_shutdown");
    await rm(directory, {recursive: true, force: true});
  });
  await command("Continue the synthetic task");
  const packet = JSON.parse(outgoing.shift());
  const result = await tools.get("loopx_goal_activate").execute("test", {
    activationToken: packet.pi_session_authority.token, objective: "Synthetic task body",
  }, undefined, undefined, ctx);
  assert.equal(JSON.parse(result.content[0].text).ok, true);
  if (prime) {
    await settle();
    const prompt = outgoing.shift();
    assert.equal(prompt, "Synthetic task body");
    await emit("input", {source: "extension", text: prompt});
    await emit("before_agent_start", {messageType: "user", prompt});
    assert.equal((await read()).autoResume, true);
  }
  notices.length = 0;
  return {emit, command, read, settle, outgoing, notices,
    busy: () => { idle = false; }, probes: () => probes};
}

for (const source of ["interactive", "rpc"]) {
  for (const streamingBehavior of ["steer", "followUp"]) {
    test(source + " " + streamingBehavior + " pauses subsequent dispatch across compaction", async t => {
      const h = await harness(t);
      h.busy();
      const before = await h.read();
      const input = {source, streamingBehavior, text: "Inspect the synthetic result"};
      assert.deepEqual(await h.emit("input", input), {action: "continue"});
      const paused = await h.read();
      assert.equal(paused.autoResume, false);
      assert.equal(paused.generation, before.generation + 1);
      assert.equal(paused.pauseReason, "user_prompt");
      assert.equal(h.notices.length, 1);
      // Recovery inside the current run does not re-arm automatic dispatch.
      await h.emit("session_compact", {reason: "overflow", willRetry: true});
      await h.emit("agent_end", {messages: [{role: "assistant", stopReason: "stop"}]});
      const probes = h.probes();
      await h.settle();
      await h.settle();
      assert.equal(h.probes(), probes);
      assert.equal(h.outgoing.length, 0);
      await h.command("resume");
      await h.settle();
      assert.equal(h.probes(), probes + 1);
      assert.equal(h.outgoing.length, 1);
    });
  }

  test(source + " input identical to the injected body pauses exactly once", async t => {
    const h = await harness(t);
    const before = await h.read();
    await h.emit("input", {source, text: before.lastInjectedPrompt});
    assert.equal((await h.read()).autoResume, false);
    await h.emit("session_compact", {reason: "threshold", willRetry: false});
    await h.emit("before_agent_start", {messageType: "user", prompt: before.lastInjectedPrompt});
    assert.equal((await h.read()).autoResume, false);
    await h.emit("before_agent_start", {messageType: "user", prompt: "Expanded input"});
    const paused = await h.read();
    assert.equal(paused.autoResume, false);
    assert.equal(paused.generation, before.generation + 1);
    assert.equal(h.notices.length, 1);
    await h.settle();
    assert.equal(h.outgoing.length, 0);
  });
}

test("image-only input before the first injection pauses", async t => {
  const h = await harness(t, {prime: false});
  const images = [{type: "image", mimeType: "image/png", data: "synthetic"}];
  await h.emit("input", {source: "interactive", text: "", images});
  await h.emit("before_agent_start", {messageType: "user", prompt: "", images});
  assert.equal((await h.read()).autoResume, false);
  assert.equal(h.notices.length, 1);
  await h.settle();
  assert.equal(h.outgoing.length, 0);
});

test("own extension input continues; foreign extension starts retain the fallback", async t => {
  const h = await harness(t);
  await h.emit("session_compact", {reason: "manual", willRetry: false});
  await h.settle();
  assert.equal(h.outgoing.length, 1);
  assert.equal((await h.read()).autoResume, true);
  await h.emit("input", {source: "extension", text: "Foreign extension task"});
  await h.emit("before_agent_start", {messageType: "user", prompt: "Foreign extension task"});
  assert.equal((await h.read()).autoResume, false);
});

test("custom starts cannot match an empty lastInjectedPrompt", async t => {
  const h = await harness(t, {prime: false});
  await h.emit("before_agent_start", {
    messageType: "custom",
    message: {role: "custom", customType: "synthetic", content: "", display: false, timestamp: 0},
  });
  assert.equal((await h.read()).autoResume, false);
  await h.settle();
  assert.equal(h.outgoing.length, 0);
});

test("an input observed after abort preserves the existing pause reason", async t => {
  const h = await harness(t);
  await h.emit("agent_end", {messages: [{role: "assistant", stopReason: "aborted"}]});
  const aborted = await h.read();
  await h.emit("input", {source: "interactive", text: "Inspect the result"});
  await h.emit("before_agent_start", {messageType: "user", prompt: "Inspect the result"});
  assert.equal((await h.read()).pauseReason, "aborted");
  assert.equal((await h.read()).generation, aborted.generation);
});
