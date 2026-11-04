import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";
import { acceptChatTurn, conversationContextKey, conversationContextOfKey, conversationSupportsStatusProjection, createChatSession,
  discoverBoundChatSession, fetchChatHistory, fetchChatProjects, resolveBoundChatSession, resolveBoundConversation } from "../src/data/chat";

// Invariant: continuation and refreshed history retain the verified original
// Session. An absent/ignored selector, changed source or closed Session cannot
// create a replacement, disclose unrelated history, or redirect a message.
const repoRoot = resolve(process.cwd(), "../../..");
const fallback = process.env.LOOPX_BOUND_STEWARD_FALLBACK === "1";
const child = spawn(resolveTestPython({ repoRoot }), ["-u", "apps/presentation/dashboard/smoke/bound-conversation-http-fixture.py",
  ...(fallback ? ["--steward-fallback"] : [])],
  { cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"] });
const exited = once(child, "exit");
const output = createInterface({ input: child.stdout });
const lines = output[Symbol.asyncIterator]();
let stderr = "";
child.stderr.on("data", chunk => { stderr += String(chunk); });
async function next(): Promise<any> {
  for (;;) {
    let timer: ReturnType<typeof setTimeout>;
    const line = await Promise.race([lines.next(), new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`Fixture response timed out: ${stderr}`)), 20_000);
    })]).finally(() => clearTimeout(timer));
    assert.equal(line.done, false, stderr);
    if (line.value!.startsWith("{")) return JSON.parse(line.value!);
  }
}
const originalFetch = globalThis.fetch;
try {
  const { origin, bindingId, sessionId } = await next();
  const target = { bindingId, sessionId };
  const context = { kind: "bound" as const, ...target };
  for (const [candidate, expected] of [
    [{ kind: "manager" }, true], [{ kind: "goal", goalId: "research" }, true],
    [{ kind: "project", projectRef: "workspace" }, false], [context, false],
  ] as const) assert.equal(conversationSupportsStatusProjection(candidate), expected);
  const options = { channelId: conversationContextKey(context), bound: target };
  const calls: { path: string; method: string }[] = [];
  globalThis.fetch = (input, init) => {
    calls.push({ path: String(input), method: init?.method ?? "GET" });
    if (String(input).startsWith("/api/chat/lark/private-conversations")) {
      throw new Error("Unrelated settings inventory is unavailable");
    }
    return originalFetch(new URL(String(input), origin), init);
  };
  const original = await resolveBoundConversation(target);
  assert.equal(original.session.session_id, sessionId);
  assert.equal(original.source.binding_id, bindingId);
  assert.equal(original.source.audience, "owner");
  assert.equal(original.source.context_kind, fallback ? "steward" : "project");
  assert.equal(original.source.project_title, "project");
  assert.equal(original.source.executor_endpoint_id, fallback ? "antigravity" : "codex");
  assert.equal(original.session.agent_id, "codex");
  assert.deepEqual(calls, [{path: `/api/chat/sessions?conversation_binding_id=${bindingId}`, method: "GET"}],
    "Opening an original conversation uses one source-qualified read, independent of settings health");
  child.stdin.write("settings-unavailable\n");
  assert.equal((await next()).settings_available, false);
  assert.equal((await originalFetch(new URL("/api/chat/lark/private-conversations", origin))).status, 503);
  assert.equal((await resolveBoundConversation(target)).session.session_id, sessionId);
  child.stdin.write("settings-recover\n");
  assert.equal((await next()).settings_available, true);
  assert.deepEqual(conversationContextOfKey(conversationContextKey(context)), context);
  assert.equal((await discoverBoundChatSession(bindingId)).session_id, sessionId);
  const history = await fetchChatHistory(options);
  assert.equal(history.sessions.length, 1);
  assert.equal(history.messages.length, 2);
  assert.ok(history.messages.every(row => row.session_id === sessionId && !row.text.includes("Unrelated desktop")));
  assert.equal((await createChatSession(context, "codex")).session_id, sessionId);
  await assert.rejects(() => createChatSession(context, "codex", "new"));
  await assert.rejects(() => createChatSession(context, "codex", "resume_latest", undefined, "another-model"));
  assert.ok(calls.every(row => row.method === "GET"), "Opening original history never starts a Turn or Session");
  calls.length = 0;
  await fetchChatHistory(options, history);
  assert.equal(calls.length, 1, "Quiet refresh verifies the source using only its compact index");
  assert.equal(calls[0].path, `/api/chat/sessions?conversation_binding_id=${bindingId}`);
  let updated = history;
  if (!fallback) {
    child.stdin.write("source\n");
    assert.equal((await next()).source_turn_status, "completed");
    updated = await fetchChatHistory(options, history);
    assert.equal(updated.messages.filter(row => row.text === "A new synthetic message from the original private source.").length, 1);
    const accepted = await acceptChatTurn(sessionId, "Continue from the desktop.", "synthetic-web-update");
    assert.equal(accepted.session_id, sessionId);
  }
  child.stdin.write("deny\n");
  assert.equal((await next()).source_verified, false);
  await assert.rejects(() => fetchChatHistory(options, updated));
  await assert.rejects(() => acceptChatTurn(sessionId, "Must not execute.", "synthetic-denied-web"));
  child.stdin.write("recover\n");
  assert.equal((await next()).source_verified, true);
  assert.equal((await resolveBoundChatSession(target)).session_id, sessionId);
  const routedFetch = globalThis.fetch;
  for (const source of [undefined, {...original.source, binding_id: "f".repeat(24)},
    {...original.source, audience: "group"}, {...original.source, context_available: false}]) {
    globalThis.fetch = async () => new Response(JSON.stringify({ok: true,
      conversation_binding_id: bindingId, sessions: history.sessions, conversation_source: source}));
    await assert.rejects(() => resolveBoundConversation(target));
  }
  globalThis.fetch = routedFetch;
  if (fallback) {
    for (const fault of ["fallback-invalid-allocation", "fallback-wrong-configured-endpoint"]) {
      child.stdin.write(`${fault}\n`);
      assert.equal((await next()).fallback_state, fault);
      assert.equal((await originalFetch(new URL(`/api/chat/sessions?conversation_binding_id=${bindingId}`, origin))).status, 409);
      await assert.rejects(() => resolveBoundConversation(target));
      child.stdin.write("fallback-restore\n");
      assert.equal((await next()).fallback_state, "fallback-restore");
      assert.equal((await resolveBoundConversation(target)).session.session_id, sessionId);
    }
  }
  const projects = await fetchChatProjects();
  assert.ok(projects.projects.length > 0, "The production workspace response is accepted");
  for (const malformed of [{ok: true}, {ok: true, projects: null},
    {ok: true, projects: [{project_ref: "workspace", title: "Workspace", grant: "unexpected"}]}]) {
    globalThis.fetch = async () => new Response(JSON.stringify(malformed));
    await assert.rejects(() => fetchChatProjects(), /workspace list could not be read/);
  }
  globalThis.fetch = async () => new Response(JSON.stringify({ok: true, projects: []}));
  assert.deepEqual(await fetchChatProjects(), {ok: true, projects: []}, "An observed empty list remains valid");
  globalThis.fetch = routedFetch;
  for (const echo of [undefined, "f".repeat(24)]) {
    globalThis.fetch = async () => new Response(JSON.stringify({ ok: true, sessions: history.sessions, conversation_binding_id: echo }));
    await assert.rejects(() => resolveBoundChatSession(target), /cannot verify/);
  }
  globalThis.fetch = routedFetch;
  const beforeMalformed = calls.length;
  await assert.rejects(() => resolveBoundChatSession({ bindingId, sessionId: "../other" }));
  assert.equal(calls.length, beforeMalformed, "Malformed original addresses never issue a request");
  child.stdin.write("close\n");
  assert.equal((await next()).closed, true);
  await assert.rejects(() => fetchChatHistory(options));
  await assert.rejects(() => createChatSession(context, "codex"));
  child.stdin.write("inspect\n");
  const inspected = await next();
  assert.equal(inspected.sessions, fallback ? 3 : 2, "No discovery or denial creates a replacement Session");
  assert.equal(inspected.original_thread, "durable-thread");
  assert.equal(inspected.messages.filter((row: any) => row.text === "Must not execute.").length, 0);
  child.stdin.write("stop\n");
  const [code] = await exited;
  assert.equal(code, 0, stderr);
  console.log(fallback
    ? "bound-conversation: passed (real HTTP/SQLite/Core steward fallback readback, original identity, invalid allocation/configured endpoint and revoked-source refusal; no model transition simulated)"
    : "bound-conversation: passed (real HTTP/SQLite/Core, exact original history, no fallback/create, source and Web input, denial/recovery, closed and old-service refusal)");
} finally {
  globalThis.fetch = originalFetch;
  output.close();
  if (child.exitCode === null) { child.kill("SIGINT"); await exited; }
}
if (!fallback) {
  const alternate = spawn(process.execPath, [process.argv[1]], {
    env: {...process.env, LOOPX_BOUND_STEWARD_FALLBACK: "1"}, stdio: "inherit",
  });
  const [code] = await once(alternate, "exit");
  assert.equal(code, 0, "The same original-conversation journey must pass after quota fallback");
}
