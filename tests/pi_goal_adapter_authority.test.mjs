import assert from "node:assert/strict";
import { createRequire, registerHooks, syncBuiltinESMExports } from "node:module";
import test from "node:test";

// Exercise the shipped command handler. Only the Pi SDK and CLI transport are
// substituted; native CLI coverage lives in control_plane/test_pi_adapter_start.py.
let cliReply;
registerHooks({
  resolve(specifier, context, next) {
    if (["typebox", "@earendil-works/pi-ai", "@earendil-works/pi-tui"].includes(specifier)) {
      return { url: "pi-authority-test:sdk", shortCircuit: true };
    }
    return next(specifier, context);
  },
  load(url, context, next) {
    if (url === "pi-authority-test:sdk") {
      return { format: "module", shortCircuit: true,
        source: `export const Type = new Proxy({}, {get: () => () => ({})});
          export const StringEnum = () => ({}); export class Text {}` };
    }
    return next(url, context);
  },
});
// promisify captures execFile's custom implementation when the adapter loads.
const childProcess = createRequire(import.meta.url)("node:child_process");
childProcess.execFile = Object.assign(() => {}, {
  [Symbol.for("nodejs.util.promisify.custom")]: async (_binary, args) => ({
    stdout: JSON.stringify(cliReply(args)),
  }),
});
syncBuiltinESMExports();
const { default: extension } = await import("../loopx/pi_goal_mode/loopx-goal.ts");

function packet(capabilities = ["task_lease_v0"]) {
  return {
    goal_id: "synthetic-goal", project: "synthetic-project",
    project_connection: { registry: "synthetic-registry", connection_state: "connected" },
    host_loop_activation: {
      activation_allowed: true, agent_id: "synthetic-agent",
      available_capabilities: capabilities,
    },
  };
}

function harness(t, { preview = packet([]), startup = packet() } = {}) {
  const commands = new Map(), tools = new Map(), events = new Map();
  const packets = [], notices = [], entries = [], widgets = [], calls = [];
  cliReply = args => {
    calls.push(args);
    assert.ok(args.includes("bootstrap-command-pack") || args.includes("start-goal"));
    return args.includes("bootstrap-command-pack") ? preview : startup;
  };
  extension({
    registerCommand: (name, command) => commands.set(name, command),
    registerTool: tool => tools.set(tool.name, tool),
    registerEntryRenderer() {},
    appendEntry: (...args) => entries.push(args),
    on: (name, handler) => events.set(name, handler),
    sendUserMessage: text => packets.push(JSON.parse(text)),
  });
  const ctx = {
    cwd: process.cwd(), isIdle: () => true,
    sessionManager: { getSessionFile: () => undefined },
    ui: {
      setWidget: (...args) => widgets.push(args),
      notify: (message, kind) => notices.push({ message, kind }),
    },
  };
  t.after(() => events.get("session_shutdown")({}, ctx));
  return {
    packets, notices, entries, widgets, calls,
    command: text => commands.get("loopx").handler(text, ctx),
    async activationError() {
      const result = await tools.get("loopx_goal_activate").execute("test", {
        activationToken: "unissued-token", objective: "synthetic task",
      }, undefined, undefined, ctx);
      return JSON.parse(result.content[0].text).error_code;
    },
  };
}

test("direct startup issues authority with the advertised capability", async t => {
  const h = harness(t);
  await h.command("Continue the synthetic task");
  assert.equal(h.packets.length, 1);
  assert.ok(h.packets[0].pi_session_authority.token);
  assert.deepEqual(h.packets[0].pi_session_authority.available_capabilities, ["task_lease_v0"]);
});

for (const command of ["", "status", "history", "list"]) {
  test(`inspection ${JSON.stringify(command)} leaves authority unbound and startup available`, async t => {
    const h = harness(t);
    await h.command(command);
    await h.command(command);
    assert.equal(h.packets.length, 0);
    assert.equal(h.entries.length, 2);
    assert.equal(h.widgets.length, 2);
    const previewActivationError = await h.activationError();
    await h.command("Continue the synthetic task");
    assert.equal(h.packets.length, 1, JSON.stringify(h.notices));
    assert.equal(previewActivationError, "authority_not_bound");
    assert.deepEqual(h.packets[0].pi_session_authority.available_capabilities, ["task_lease_v0"]);
    assert.ok(h.packets[0].pi_session_authority.token);
    assert.equal(h.calls.length, 3);
    assert.equal(h.notices.filter(n => n.kind === "error").length, 0);
  });
}

test("previewed goal does not constrain the later explicit startup", async t => {
  const preview = packet();
  preview.goal_id = "other-previewed-goal";
  const h = harness(t, { preview });
  await h.command("status");
  await h.command("Continue the synthetic task");
  assert.equal(h.packets.length, 1, JSON.stringify(h.notices));
  assert.equal(h.packets[0].pi_session_authority.goal_id, "synthetic-goal");
});

test("inspection after startup preserves the issued token and capabilities", async t => {
  const h = harness(t);
  await h.command("Continue the synthetic task");
  const original = h.packets[0].pi_session_authority;
  await h.command("status");
  await h.command("Continue the synthetic task");
  assert.equal(h.packets.length, 2, JSON.stringify(h.notices));
  assert.deepEqual(h.packets[1].pi_session_authority, original);
  assert.equal(await h.activationError(), "authority_mismatch");
});

for (const field of ["goal", "agent", "registry", "capabilities"]) {
  test(`inspection cannot unlock an established ${field}`, async t => {
    const startup = packet();
    const h = harness(t, { startup });
    await h.command("Continue the synthetic task");
    await h.command("status");
    if (field === "goal") startup.goal_id = "other-goal";
    if (field === "agent") startup.host_loop_activation.agent_id = "other-agent";
    if (field === "registry") startup.project_connection.registry = "other-registry";
    if (field === "capabilities") startup.host_loop_activation.available_capabilities = [];
    await h.command("Continue the synthetic task");
    assert.equal(h.packets.length, 1);
    assert.match(h.notices.find(n => n.kind === "error").message, /authority cannot change/);
  });
}

test("an identity-selection packet still cannot issue authority after inspection", async t => {
  const startup = packet();
  startup.host_loop_activation.activation_allowed = false;
  const h = harness(t, { startup });
  await h.command("status");
  await h.command("Continue the synthetic task");
  assert.equal(h.packets.length, 1);
  assert.equal(h.packets[0].pi_session_authority, undefined);
  assert.equal(await h.activationError(), "authority_not_bound");
});
