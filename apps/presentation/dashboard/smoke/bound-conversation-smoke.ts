import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";
import { acceptChatTurn, conversationContextKey, conversationContextOfKey, conversationSupportsStatusProjection, createChatSession,
  discoverBoundChatSession, fetchChatHistory, fetchChatQueue, fetchChatProjects, fetchChatSessionHistory, resolveBoundChatSession, resolveBoundConversation } from "../src/data/chat";

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
  const routedRead = globalThis.fetch;
  if (!fallback) {
    // The original idle queue gets one bounded recovery from a temporary
    // read failure; no Turn, fallback Session or permission retry is involved.
    const queuePath = `/api/chat/sessions/${sessionId}/queue`;
    const queueReads: string[] = [];
    const unavailable = () => Response.json({ok: false, error: "Queued messages are temporarily unavailable"}, {status: 503});
    const queueInterruption = new Error("Synthetic queue transport interrupted");
    const replies: (Response | Error)[] = [unavailable()];
    globalThis.fetch = async (input, init) => {
      if (String(input) !== queuePath) return routedRead(input, init);
      queueReads.push(init?.method ?? "GET");
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return reply ?? routedRead(input, init);
    };
    const observedQueue = await fetchChatQueue(sessionId, AbortSignal.timeout(5000));
    assert.deepEqual([observedQueue.session_id, observedQueue.active_turn_id, observedQueue.turns], [sessionId, null, []]);
    assert.deepEqual(queueReads.splice(0), ["GET", "GET"]);
    replies.push(queueInterruption);
    assert.equal((await fetchChatQueue(sessionId, AbortSignal.timeout(5000))).session_id, sessionId);
    assert.deepEqual(queueReads.splice(0), ["GET", "GET"]);

    replies.push(unavailable(), unavailable());
    await assert.rejects(() => fetchChatQueue(sessionId, AbortSignal.timeout(5000)), /temporarily unavailable/);
    assert.deepEqual(queueReads.splice(0), ["GET", "GET"], "A continued failure must remain unknown after one retry");
    replies.push(Response.json({ok: false, error: "Original scope revoked"}, {status: 403}));
    await assert.rejects(() => fetchChatQueue(sessionId), /scope revoked/);
    assert.deepEqual(queueReads.splice(0), ["GET"], "Never retry a denial");
    replies.push(Response.json({ok: true, schema_version: "loopx_chat_store_v1",
      session_id: "different-source", active_turn_id: null, turns: []}));
    await assert.rejects(() => fetchChatQueue(sessionId), /original Session queue/);
    assert.deepEqual(queueReads.splice(0), ["GET"], "Never accept or retry another Session's queue");

    const controller = new AbortController();
    globalThis.fetch = async (input, init) => {
      assert.equal(String(input), queuePath);
      queueReads.push(init?.method ?? "GET");
      controller.abort();
      return unavailable();
    };
    await assert.rejects(() => fetchChatQueue(sessionId, controller.signal), /temporarily unavailable/);
    assert.deepEqual(queueReads, ["GET"], "Leaving this read prevents another request");
    globalThis.fetch = routedRead;
    console.log("bound idle queue: one same-Session retry, denial and cancellation remain failures");

    // Repeated transport loss at later pages must advance one original prefix
    // through the existing retry caller, without ever publishing partial text.
    child.stdin.write("history\n");
    const expectedRows = (await next()).history_rows;
    const complete = await fetchChatSessionHistory(original.session);
    assert.equal(complete.messages.length, expectedRows);
    const recoveredOffsets: number[] = [];
    const failedOffsets = new Set<number>();
    const interruptedHistoryBody = (status: number) => new Response(new ReadableStream({
      start(controller) {
        controller.error(new Error("Synthetic interrupted history response body"));
      },
    }), {status});
    globalThis.fetch = async (input, init) => {
      const response = await routedRead(input, init);
      if (new URL(String(input), origin).pathname !== `/api/chat/sessions/${sessionId}`) return response;
      const value = await response.clone().json();
      const offset = value.message_page.offset;
      recoveredOffsets.push(offset);
      if (offset > 0 && !failedOffsets.has(offset)) {
        failedOffsets.add(offset);
        if (offset === 200) throw new Error("Synthetic interrupted transport after a verified prefix");
        return interruptedHistoryBody(response.status);
      }
      return response;
    };
    const interrupted = await fetchChatHistory(options);
    assert.deepEqual(interrupted.unavailableSessionIds, [sessionId]);
    assert.deepEqual(interrupted.messages, []);
    assert.deepEqual(interrupted.snapshots, []);
    const interruptedAgain = await fetchChatHistory(options, interrupted);
    assert.deepEqual(interruptedAgain.messages, []);
    assert.equal(interruptedAgain.pendingReads?.get(sessionId)?.messages.length, 400,
      "A lost body after successful response headers keeps the verified prefix private");
    const recovered = await fetchChatHistory(options, interruptedAgain);
    assert.deepEqual(recovered.unavailableSessionIds, []);
    assert.deepEqual(recovered.snapshots[0].messages, complete.messages);
    assert.deepEqual(recoveredOffsets, [0, 200, 200, 400, 400], "Only failed pages are read again");
    assert.equal(interrupted.pendingReads?.get(sessionId)?.messages.length, 200,
      "Later retries cannot mutate the previously published read state");
    assert.equal(recovered.pendingReads?.size, 0);
    assert.ok(calls.every(row => row.method === "GET"), "Recovery never starts work");
    globalThis.fetch = routedRead;
    // If the fresh post-history source read loses its connection, keep the
    // verified pages private until a later source check succeeds.
    const isBoundDiscovery = (input: RequestInfo | URL) =>
      new URL(String(input), origin).searchParams.get("conversation_binding_id") === bindingId;
    let sourceChecks = 0;
    globalThis.fetch = (input, init) => {
      if (isBoundDiscovery(input) && ++sourceChecks === 2) throw new Error("Lost post-history source connection");
      return routedRead(input, init);
    };
    const awaitingSource = await fetchChatHistory(options);
    assert.deepEqual(awaitingSource.unavailableSessionIds, [sessionId]);
    assert.deepEqual(awaitingSource.snapshots, []);
    assert.deepEqual(awaitingSource.messages, []);
    globalThis.fetch = routedRead;
    calls.length = 0;
    const sourceRecovered = await fetchChatHistory(options, awaitingSource);
    assert.deepEqual(sourceRecovered.snapshots[0].messages, complete.messages);
    assert.deepEqual(calls, [{path: `/api/chat/sessions?conversation_binding_id=${bindingId}`, method: "GET"}],
      "A fresh check of the same unchanged binding reuses the complete verified history");

    // The final source check can keep the same address while reporting a
    // different canonical Session scope. Previously read pages cannot publish.
    sourceChecks = 0;
    globalThis.fetch = async (input, init) => {
      const response = await routedRead(input, init);
      if (!isBoundDiscovery(input) || ++sourceChecks !== 2) return response;
      const changedSource = await response.json();
      changedSource.sessions[0].channel_id = "unrelated-original-channel";
      return new Response(JSON.stringify(changedSource), {status: response.status});
    };
    await assert.rejects(() => fetchChatHistory(options), /original Session scope changed during history read/);
    assert.equal(sourceChecks, 2);
    globalThis.fetch = routedRead;

    // Current source revocation blocks recovery before another history page.
    child.stdin.write("deny\n"); assert.equal((await next()).source_verified, false);
    calls.length = 0;
    await assert.rejects(() => fetchChatHistory(options, interrupted));
    await assert.rejects(() => fetchChatHistory(options, awaitingSource));
    assert.ok(calls.every(row => new URL(row.path, origin).pathname === "/api/chat/sessions"));
    child.stdin.write("recover\n"); assert.equal((await next()).source_verified, true);
    // Permanent denial, a changed source or malformed prefix cannot be retained
    // as transport progress. The following read must validate page zero again.
    for (const fault of ["denied", "denied-body", "source", "digest"]) {
      globalThis.fetch = async (input, init) => {
        const response = await routedRead(input, init);
        if (!new URL(String(input), origin).searchParams.has("message_cursor")) return response;
        if (fault === "denied") return new Response("{}", {status: 403});
        if (fault === "denied-body") return interruptedHistoryBody(403);
        const value = await response.json();
        if (fault === "source") value.session.goal_id = "unrelated-goal";
        else value.message_page.snapshot_sha256 = "0".repeat(64);
        return new Response(JSON.stringify(value));
      };
      const rejected = await fetchChatHistory(options, interrupted);
      assert.deepEqual(rejected.messages, []);
      assert.equal(rejected.pendingReads?.size, 0);
      globalThis.fetch = routedRead;
      calls.length = 0;
      assert.deepEqual((await fetchChatHistory(options, rejected)).snapshots[0].messages, complete.messages);
      assert.equal(new URL(calls.find(row => row.path.includes(`${sessionId}?`))!.path, origin).searchParams.has("message_cursor"), false);
    }
    // Real HTTP responses beyond the old interactive deadline must still hydrate
    // the complete original history. Source verification keeps its own deadline.
    child.stdin.write("slow-history\n");
    assert.equal((await next()).slow_history, true);
    const slow = await fetchChatHistory(options);
    assert.deepEqual(slow.messages, recovered.messages);
    assert.deepEqual(slow.unavailableSessionIds, []);
    // Delay only the post-history fresh source check, not initial discovery.
    const beforeSlowSource = calls.length;
    globalThis.fetch = async (input, init) => {
      const response = await routedRead(input, init);
      if (String(input).startsWith(`/api/chat/sessions/${sessionId}?`)) {
        child.stdin.write("slow-history-source\n");
        assert.equal((await next()).slow_history_source, true);
      }
      return response;
    };
    const sourceSlow = await fetchChatHistory(options);
    assert.deepEqual(sourceSlow.messages, recovered.messages);
    assert.deepEqual(sourceSlow.unavailableSessionIds, []);
    assert.ok(calls.slice(beforeSlowSource).every(row => row.method === "GET"));
    globalThis.fetch = routedRead;
    child.stdin.write("slow-history-cancel\n");
    assert.equal((await next()).slow_history, true);
    await assert.rejects(() => fetchChatSessionHistory(original.session, AbortSignal.timeout(50)));
    const readRequests = () => calls.filter(row => new URL(row.path, origin).pathname === "/api/chat/sessions/" + sessionId)
      .map(row => new URL(row.path, origin));
    child.stdin.write("history-extra\n");
    const added = await next();
    calls.length = 0;
    const resumed = await fetchChatHistory(options, recovered);
    assert.equal(readRequests().length, 1, "A proven append must not download the old complete transcript");
    assert.equal(readRequests()[0].searchParams.has("known_history"), true);
    assert.deepEqual(resumed.messages.slice(0, -1), recovered.messages);
    assert.equal(resumed.messages.length, added.history_rows);
    assert.equal(resumed.messages.at(-1)?.text, "A full new original result. ".repeat(150));
    assert.equal(calls.filter(row => new URL(row.path, origin).pathname === "/api/chat/sessions").length, 2,
      "The original source is verified again after a suffix read");
    calls.length = 0;
    const changedWhileWaiting = await fetchChatHistory(options, awaitingSource);
    assert.deepEqual(changedWhileWaiting.messages, resumed.messages);
    assert.ok(readRequests().length > 2, "A changed transcript cannot publish the pending history");

    child.stdin.write("history-rewrite\n");
    assert.equal((await next()).history_rows, added.history_rows);
    calls.length = 0;
    const rewritten = await fetchChatHistory(options, resumed);
    assert.ok(readRequests().length > 2);
    assert.equal(readRequests()[0].searchParams.has("known_history"), true);
    assert.equal(readRequests()[1].searchParams.has("message_cursor"), false, "A changed prefix restarts at page zero");
    assert.ok(rewritten.messages.some(row => row.text === "Changed earlier original row 10"));
    assert.ok(!rewritten.messages.some(row => row.text === "Paged bound row 10"));

    child.stdin.write("history-return\n");
    const {messageId} = await next();
    const queued = await fetchChatHistory(options, rewritten);
    const returnStatus = (snapshot: typeof queued) => snapshot.messages
      .find(row => row.message_id === messageId)?.return_delivery?.status;
    assert.equal(returnStatus(queued), "queued");
    calls.length = 0;
    assert.equal(returnStatus(await fetchChatHistory(options, queued)), "queued");
    assert.equal(readRequests().length, 1, "An unchanged pending return only needs the history proof");

    sourceChecks = 0;
    globalThis.fetch = (input, init) => {
      if (isBoundDiscovery(input) && ++sourceChecks === 2) throw new Error("Lost pending return source check");
      return routedRead(input, init);
    };
    const awaitingReturnSource = await fetchChatHistory(options);
    assert.deepEqual(awaitingReturnSource.messages, []);
    assert.deepEqual(awaitingReturnSource.snapshots, []);
    assert.equal(awaitingReturnSource.pendingBoundSnapshot?.snapshot.messages
      .find(row => row.message_id === messageId)?.return_delivery?.status, "queued");
    globalThis.fetch = routedRead;

    calls.length = 0;
    assert.equal(returnStatus(await fetchChatHistory(options, awaitingReturnSource)), "queued");
    assert.deepEqual(readRequests().map(url => url.searchParams.has("known_history")), [true],
      "Recovery of a private transcript with a pending return only needs the history proof if it still matches");
    assert.equal(calls.filter(row => new URL(row.path, origin).pathname === "/api/chat/sessions").length, 2,
      "Recovery checks the original binding again after verifying the retained history");

    child.stdin.write("history-return-delivered\n");
    assert.equal((await next()).messageId, messageId);
    calls.length = 0;
    const delivered = await fetchChatHistory(options, queued);
    assert.equal(delivered.sessions[0].transcript_revision, queued.sessions[0].transcript_revision);
    assert.equal(returnStatus(delivered), "delivered", "A return can complete without rewriting the Chat transcript");
    assert.equal(delivered.messages.length, queued.messages.length);
    assert.equal(readRequests()[0].searchParams.has("known_history"), true);
    assert.ok(calls.every(row => row.method === "GET"));
    calls.length = 0;
    const deliveredWhileWaiting = await fetchChatHistory(options, awaitingReturnSource);
    assert.deepEqual(deliveredWhileWaiting.messages, delivered.messages);
    assert.equal(readRequests()[0].searchParams.has("known_history"), true);
    assert.equal(readRequests()[1].searchParams.has("message_cursor"), false,
      "A pending return that changed while the source was unavailable restarts at page zero");
    calls.length = 0;
    assert.equal(returnStatus(await fetchChatHistory(options, delivered)), "delivered");
    assert.equal(readRequests().length, 0, "A settled immutable transcript uses the compact discovery read again");

    child.stdin.write("history-return-unreadable\n");
    assert.equal((await next()).messageId, messageId);
    const unreadable = await fetchChatHistory(options, queued);
    assert.equal(returnStatus(unreadable), "explicit_unverified");
    assert.equal(unreadable.messages.find(row => row.message_id === messageId)?.return_delivery?.error,
      "delivery_state_unreadable");

    child.stdin.write("history-return-unverified\n");
    assert.equal((await next()).messageId, messageId);
    const unverified = await fetchChatHistory(options, unreadable);
    assert.equal(returnStatus(unverified), "explicit_unverified");
    assert.equal(unverified.messages.find(row => row.message_id === messageId)?.return_delivery?.error,
      "provider_delivery_unverified", "The owner recorded a failed verification, not a successful delivery");
    calls.length = 0;
    assert.equal(returnStatus(await fetchChatHistory(options, unverified)), "explicit_unverified");
    assert.equal(readRequests().length, 0, "An owner-recorded terminal failure does not repeatedly fetch history");

    child.stdin.write("history-extra\n");
    await next();
    child.stdin.write("history-unavailable\n");
    assert.equal((await next()).history_available, false);
    const coldUnavailable = await fetchChatHistory(options);
    assert.deepEqual(coldUnavailable.snapshots, [], "No verified transcript exists on a first read");
    assert.deepEqual(coldUnavailable.unavailableSessionIds, [sessionId]);
    calls.length = 0;
    const degraded = await fetchChatHistory(options, unverified);
    assert.deepEqual(degraded.messages, unverified.messages, "An HTTP 503 alone must not hide verified history");
    assert.equal(degraded.snapshots[0]?.session.session_id, sessionId, "The same original composer retains a usable snapshot");
    assert.deepEqual(degraded.unavailableSessionIds, [sessionId], "Report the incomplete refresh without emptying history");
    assert.equal(calls.filter(row => isBoundDiscovery(row.path)).length, 2,
      "A cached transcript is usable only after the bound source is checked again");
    child.stdin.write("deny\n");
    assert.equal((await next()).source_verified, false);
    await assert.rejects(() => fetchChatHistory(options, degraded),
      "Missing authority is not a degraded history read");
    child.stdin.write("recover\n");
    assert.equal((await next()).source_verified, true);
    child.stdin.write("history-available\n");
    assert.equal((await next()).history_available, true);
    const resumedAfterUnavailable = await fetchChatHistory(options, degraded);
    assert.equal(resumedAfterUnavailable.messages.length, unverified.messages.length + 1);
    assert.deepEqual(resumedAfterUnavailable.unavailableSessionIds, []);
    child.stdin.write("history-extra\n");
    await next();
    globalThis.fetch = async (input, init) => {
      const response = await routedRead(input, init);
      if (new URL(String(input), origin).searchParams.has("known_history")) {
        child.stdin.write("deny\n");
        assert.equal((await next()).source_verified, false);
      }
      return response;
    };
    await assert.rejects(() => fetchChatHistory(options, resumedAfterUnavailable), "A revoked original binding cannot publish a suffix");
    globalThis.fetch = routedRead;
    child.stdin.write("recover\n");
    assert.equal((await next()).source_verified, true);
  }
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
