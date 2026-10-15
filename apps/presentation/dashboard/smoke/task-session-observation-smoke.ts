import assert from "node:assert/strict";
import { readTaskSessions } from "../src/data/conversation-return-observation";
import type { ChatSessionSnapshot, ChatSessionSummary } from "../src/data/chat";

const originalFetch = globalThis.fetch;
const signal = new AbortController().signal;
const requests: string[] = [];
const failures = new Set<string>();
let abortDuringRead: AbortController | null = null;
let concurrent = 0;
let peakConcurrent = 0;
let sessions: ChatSessionSummary[] = Array.from({ length: 20 }, (_, index) => ({
  session_id: `task-${index}`, goal_id: "goal-a", agent_id: "worker", adapter_kind: "codex",
  channel_id: `task.todo-${index}`, status: "idle", active_turn_id: null, last_error_code: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  last_activity_at: "2026-01-01T00:00:00Z", transcript_revision: "revision-1", resumable: true,
}));
const served = new Map<string, ChatSessionSnapshot>();
function publish(session: ChatSessionSummary, text: string) {
  served.set(session.session_id, {
    ok: true, schema_version: "loopx_chat_store_v1", session: { ...session }, active_turn: null,
    messages: [{ message_id: "answer", turn_id: session.active_turn_id, role: "assistant", text, created_at: session.updated_at }],
  });
}
sessions.forEach((session) => publish(session, "Retained result"));

globalThis.fetch = (async (input, init) => {
  init?.signal?.throwIfAborted();
  assert.ok(!init?.method || init.method === "GET", "Observation never writes or starts work");
  const url = new URL(String(input), "http://127.0.0.1");
  requests.push(url.pathname);
  if (url.pathname === "/api/chat/sessions") {
    assert.equal(url.searchParams.get("goal_id"), "goal-a");
    return Response.json({ sessions });
  }
  const id = url.pathname.split("/").at(-1)!;
  concurrent += 1;
  peakConcurrent = Math.max(peakConcurrent, concurrent);
  try {
    await Promise.resolve();
    abortDuringRead?.abort();
    if (failures.has(id)) return Response.json({ error: "temporarily unavailable" }, { status: 503 });
    return Response.json(served.get(id));
  } finally {
    concurrent -= 1;
  }
}) as typeof fetch;

try {
  let observed = await readTaskSessions("goal-a", {}, signal);
  assert.equal(Object.keys(observed.snapshots).length, 20);
  assert.equal(requests.length, 21, "Initial population loads each task result once");
  assert.ok(peakConcurrent <= 4, "Initial discovery bounds snapshot concurrency");
  requests.length = 0;
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  assert.deepEqual(requests, ["/api/chat/sessions"], "Twenty quiet tasks need only one compact index read");

  sessions[0] = { ...sessions[0], status: "running", active_turn_id: "turn-a" };
  publish(sessions[0], "First progress");
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  requests.length = 0;
  publish(sessions[0], "Latest progress");
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  assert.equal(observed.snapshots["task-0"].messages[0].text, "Latest progress");
  assert.deepEqual(requests, ["/api/chat/sessions", "/api/chat/sessions/task-0"], "Active Turns refresh even at the same transcript revision");

  sessions[0] = { ...sessions[0], status: "idle", active_turn_id: null, transcript_revision: "revision-2" };
  publish(sessions[0], "Final result");
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  assert.equal(observed.snapshots["task-0"].messages[0].text, "Final result");
  requests.length = 0;
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  assert.equal(requests.length, 1, "A completed task stops repeated detail reads after final readback");

  sessions[0] = { ...sessions[0], transcript_revision: "revision-3" };
  publish(sessions[0], "Late corrected result");
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  assert.equal(observed.snapshots["task-0"].messages[0].text, "Late corrected result");
  sessions[0] = { ...sessions[0], transcript_revision: "revision-4" };
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  publish(sessions[0], "Current result");
  requests.length = 0;
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  assert.equal(observed.snapshots["task-0"].messages[0].text, "Current result");
  assert.equal(requests.length, 2, "An older snapshot never acknowledges a newer index revision");

  const retained = observed.snapshots;
  sessions[0] = { ...sessions[0], transcript_revision: "revision-5" };
  publish(sessions[0], "Recovered result");
  failures.add("task-0");
  observed = await readTaskSessions("goal-a", retained, signal);
  assert.deepEqual(observed.unavailableSessionIds, ["task-0"]);
  assert.equal(observed.snapshots["task-0"], retained["task-0"], "A read failure preserves the known result");
  failures.clear();
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  assert.deepEqual(observed.unavailableSessionIds, []);
  assert.equal(observed.snapshots["task-0"].messages[0].text, "Recovered result");
  assert.equal(retained["task-0"].messages[0].text, "Current result", "Observation does not mutate the caller's prior cache");

  sessions = sessions.slice(1);
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  assert.equal("task-0" in observed.snapshots, false, "Removed sessions leave the observation cache");
  sessions[0] = { ...sessions[0], transcript_revision: null };
  publish(sessions[0], "Legacy result");
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  publish(sessions[0], "Updated legacy result");
  observed = await readTaskSessions("goal-a", observed.snapshots, signal);
  assert.equal(observed.snapshots["task-1"].messages[0].text, "Updated legacy result", "Missing revision hints never freeze old results");

  requests.length = 0;
  abortDuringRead = new AbortController();
  const aborted = await readTaskSessions("goal-a", {}, abortDuringRead.signal);
  assert.deepEqual(aborted.snapshots, {}, "An abandoned observation publishes no snapshots");
  assert.ok(requests.length <= 5, "Cancellation stops subsequent batches");
  console.log("task-session-observation: passed (20 quiet tasks: 21 to 1 requests; active/final/late results, stale index races, failure recovery, pruning, legacy revisions, cancellation)");
} finally {
  globalThis.fetch = originalFetch;
}
