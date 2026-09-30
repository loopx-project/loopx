import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";
import { fetchChatHistory } from "../src/data/chat";

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
  child.stdin.end("inspect\n");
  assert.deepEqual(await next(), { store_unchanged: true, turn_count: 0 });
  const [exitCode] = await exited;
  assert.equal(exitCode, 0, stderr);
  console.log("conversation-history: passed (real HTTP/store, partial read, channel isolation, missing-only recovery, scoped identity, zero writes or Turns)");
} finally {
  globalThis.fetch = originalFetch;
  output.close();
  if (child.exitCode === null) { child.kill(); await exited; }
}
