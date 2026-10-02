import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";
import { fetchChatHistory } from "../src/data/chat";
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
  assert.deepEqual(requests, ["/api/chat/sessions/old"], "Recovery reads only the missing session");
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
  assert.equal(transcript.at(-1)?.text, "First checked result");
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
  assert.equal(transcript.at(-1)?.text, "First checked result", "Failed reads preserve the first result");
  child.stdin.write("recover\n");
  assert.equal((await next()).recovered, true);
  requests.length = 0;
  await observe();
  assert.deepEqual(requests, ["/api/chat/sessions?", "/api/chat/sessions/old"], "Retry reads only the changed old session");
  assert.equal(transcript.at(-1)?.text, "Revised checked result");
  assert.equal(transcript.filter(row => row.text === "Revised checked result").length, 1);
  assert.equal(transcript.find(row => row.sourceSessionId === "current"), originalCurrent, "Old updates preserve current text and object identity");
  await observe();
  assert.equal(transcript.filter(row => row.text === "Revised checked result").length, 1);
  child.stdin.end("inspect\n");
  assert.deepEqual(await next(), { store_unchanged: true, turn_count: 0 });
  const [exitCode] = await exited;
  assert.equal(exitCode, 0, stderr);
  console.log("conversation-history: passed (real HTTP/store, partial read, channel isolation, missing-only recovery, scoped identity, quiet index reads, late revisions, recovery, zero read-side writes or Turns)");
} finally {
  globalThis.fetch = originalFetch;
  output.close();
  if (child.exitCode === null) { child.kill(); await exited; }
}
