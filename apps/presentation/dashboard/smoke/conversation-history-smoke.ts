import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";
import { fetchChatHistory, fetchChatSessionHistory } from "../src/data/chat";
import { readConversationReturns } from "../src/data/conversation-return-observation";
import { conversationReturnSessions, reconcileConversationReturns } from "../src/data/conversation-returns";

const repoRoot = resolve(process.cwd(), "../../..");
const child = spawn(resolveTestPython({ repoRoot }), ["-u", "apps/presentation/dashboard/smoke/conversation-history-http-fixture.py"],
  { cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"] });
const exited = once(child, "exit");
let stderr = "";
child.stderr.on("data", chunk => { stderr += String(chunk); });
const output = createInterface({ input: child.stdout });
const lines = output[Symbol.asyncIterator]();
const originalFetch = globalThis.fetch;
async function next() {
  const line = await lines.next();
  assert.equal(line.done, false, stderr);
  return JSON.parse(line.value!);
}
try {
  const { origin } = await next();
  const requests: string[] = [];
  globalThis.fetch = (input, init) => {
    const path = String(input);
    assert.ok(!init?.method || init.method === "GET", "History recovery is read-only");
    requests.push(path);
    return originalFetch(new URL(path, origin), init);
  };
  const options = { agentId: "codex", goalId: "research", channelId: "goal.research" };
  const partial = await fetchChatHistory(options);
  assert.deepEqual(partial.unavailableSessionIds, ["old"]);
  assert.deepEqual(partial.messages.map(row => row.text), ["Current public report"]);
  assert.equal(partial.sessions[0].session_id, "current");
  assert.equal(partial.sessions.some(row => row.session_id === "other-channel"), false);
  child.stdin.write("recover\n");
  assert.equal((await next()).recovered, true);
  requests.length = 0;
  const restored = await fetchChatHistory(options, partial);
  assert.deepEqual(requests.map(request => new URL(request, origin).pathname),
    ["/api/chat/sessions/old"], "Recovery reads only the missing session");
  assert.deepEqual(restored.unavailableSessionIds, []);
  assert.deepEqual(restored.messages.map(row => row.text), ["Earlier public report", "Current public report"]);
  assert.deepEqual(restored.messages.map(row => row.session_id), ["old", "current"], "Colliding IDs preserve both sessions");
  requests.length = 0;
  await fetchChatHistory(options, restored);
  assert.deepEqual(requests, [], "A complete cached history does not poll");
  const revisions = new Map<string, string>();
  let transcript = restored.messages.map(row => ({ sourceSessionId: row.session_id!, sourceMessageId: row.message_id, sourceCreatedAt: row.created_at, text: row.text }));
  const originalCurrent = transcript.find(row => row.sourceSessionId === "current");
  const observe = () => readConversationReturns({
    sessionIds: conversationReturnSessions("current", transcript), pendingSessionIds: new Set(), revisions,
    signal: AbortSignal.timeout(5000), receive(snapshot) {
      const id = snapshot.session.session_id;
      transcript = reconcileConversationReturns(transcript, id, snapshot.messages, row => ({
        sourceSessionId: id, sourceMessageId: row.message_id, sourceCreatedAt: row.created_at, text: row.text,
      }));
    },
  });
  await observe();
  requests.length = 0;
  await observe();
  assert.equal(requests.length, 1, "Quiet observation reads only the compact index");
  child.stdin.write("result\n");
  assert.equal((await next()).published, "result");
  await observe();
  const firstResult = "First checked result\n\nForm: https://example.org/forms/result。";
  const revisedResult = "Revised checked result\n\nForm: https://example.org/forms/revision。";
  assert.equal(transcript.at(-1)?.text, firstResult);
  requests.length = 0;
  await observe();
  assert.equal(requests.length, 1, "Delivery does not trigger continuous full history reads");
  requests.length = 0;
  await readConversationReturns({ sessionIds: ["old", "current"], pendingSessionIds: new Set(["old"]), revisions,
    signal: AbortSignal.timeout(5000), receive() {} });
  assert.deepEqual(requests, ["/api/chat/sessions?", "/api/chat/sessions/old"], "Outstanding metadata refreshes without a transcript change");
  child.stdin.write("revision\nfault\n");
  assert.equal((await next()).published, "revision");
  assert.equal((await next()).unavailable, "old");
  assert.deepEqual(await observe(), ["old"]);
  assert.equal(transcript.at(-1)?.text, firstResult, "Failed reads preserve the complete first result, including its link");
  child.stdin.write("recover\n");
  assert.equal((await next()).recovered, true);
  requests.length = 0;
  await observe();
  assert.deepEqual(requests, ["/api/chat/sessions?", "/api/chat/sessions/old"], "Retry reads only the changed old session");
  assert.equal(transcript.at(-1)?.text, revisedResult);
  assert.equal(transcript.filter(row => row.text === revisedResult).length, 1);
  assert.equal(transcript.find(row => row.sourceSessionId === "current"), originalCurrent, "Old updates preserve current text and object identity");
  await observe();
  assert.equal(transcript.filter(row => row.text === revisedResult).length, 1);
  child.stdin.write("history\n");
  const expectedRows = (await next()).history_rows;
  requests.length = 0;
  const complete = await fetchChatSessionHistory("old");
  assert.equal(complete.messages.length, expectedRows);
  assert.equal(new Set(complete.messages.map(row => row.message_id)).size, expectedRows);
  assert.equal(complete.messages.find(row => row.message_id === "history-201")?.text, "Original full body. ".repeat(600));
  assert.equal(requests.length, 3, "A complete original requires every real HTTP page");
  assert.ok(requests.every(path => new URL(path, origin).searchParams.get("message_limit") === "200"));
  assert.deepEqual((await fetchChatSessionHistory(complete.session)).messages, complete.messages,
    "Existing discovered-summary history and delegation-ID readers share one complete-prefix owner");
  child.stdin.write("page-fault\n");
  assert.equal((await next()).unavailable_message_pages, true);
  await assert.rejects(() => fetchChatSessionHistory("old"));
  child.stdin.write("recover\n"); assert.equal((await next()).recovered, true);
  assert.equal((await fetchChatSessionHistory("old")).messages.length, expectedRows);
  const routedFetch = globalThis.fetch;
  // Challenge the original-source and frozen-prefix rules, not only length.
  for (const mutate of [
    (value: any) => {value.session.goal_id = "unrelated-goal";},
    (value: any) => {value.message_page.snapshot_sha256 = "0".repeat(64);},
    (value: any) => {value.message_page.offset = 0;},
    (value: any) => {value.messages[0].message_id = complete.messages[0].message_id;},
    (value: any) => {delete value.message_page;},
  ]) {
    globalThis.fetch = async (input, init) => {
      const response = await routedFetch(input, init);
      if (!new URL(String(input), origin).searchParams.has("message_cursor")) return response;
      const value = await response.json(); mutate(value);
      return new Response(JSON.stringify(value), {status: response.status});
    };
    await assert.rejects(() => fetchChatSessionHistory("old"), "Never accept mixed or partial pages");
  }
  globalThis.fetch = routedFetch;
  const abort = new AbortController(); abort.abort(); requests.length = 0;
  await assert.rejects(() => fetchChatSessionHistory("old", abort.signal));
  assert.equal(requests.length, 0, "A closed reader must not start another page read");
  const inFlight = new AbortController();
  let pageAborted = false;
  globalThis.fetch = async (input, init) => {
    if (!new URL(String(input), origin).searchParams.has("message_cursor")) return routedFetch(input, init);
    assert.ok(init?.signal);
    inFlight.abort();
    pageAborted = init.signal.aborted;
    init.signal.throwIfAborted();
    throw new Error("The page did not receive the reader's cancellation");
  };
  await assert.rejects(() => fetchChatSessionHistory("old", inFlight.signal));
  assert.equal(pageAborted, true, "The in-flight page shares the reader's cancellation");
  // The existing full-snapshot service remains compatible on the first read.
  globalThis.fetch = async () => new Response(JSON.stringify(complete));
  assert.deepEqual((await fetchChatSessionHistory(complete.session)).messages, complete.messages);
  globalThis.fetch = routedFetch;
  child.stdin.end("inspect\n");
  assert.deepEqual(await next(), { store_unchanged: true, turn_count: 0 });
  const [exitCode] = await exited;
  assert.equal(exitCode, 0, stderr);
  console.log("conversation-history: passed (real HTTP/store, channel isolation, missing-only recovery, quiet index reads, late revisions, complete bounded pages, source/prefix conflicts, cancellation, legacy parity, zero read-side writes or Turns)");
} finally {
  globalThis.fetch = originalFetch;
  output.close();
  if (child.exitCode === null) { child.kill(); await exited; }
}
