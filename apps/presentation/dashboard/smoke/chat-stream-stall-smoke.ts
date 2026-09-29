import assert from "node:assert/strict";

import {
  CHAT_STREAM_STALL_TIMEOUT_MS,
  ChatApiError,
  type ChatStreamEvent,
  resumeChatTurnStreaming,
  streamChatTurn,
} from "../src/data/chat.ts";

// A stuck SSE connection delivers headers and a first event, then no bytes at
// all, not even the service heartbeat. The reader must abandon it, resume from
// its cursor, and tell the pending reply it is reconnecting. A caller abort is
// different: it ends transport activity at any point and is never retried.
const encoder = new TextEncoder();
const originalFetch = globalThis.fetch;
const originalSetTimeout = globalThis.setTimeout;
const eventsUrl = "/api/chat/sessions/s/turns/t/events";
const requests: string[] = [];
let serve: (url: URL, signal: AbortSignal | null | undefined) => Response;

function sseBlock(eventId: string, kind: string, payload: Record<string, unknown>) {
  return encoder.encode(`id: ${eventId}\nevent: ${kind}\ndata: ${JSON.stringify({ created_at: "2026-09-28T00:00:00Z", event_id: eventId, kind, payload, sequence: 1 })}\n\n`);
}

function silentAfter(first: Uint8Array | null, signal: AbortSignal | null | undefined) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      if (first) controller.enqueue(first);
      signal?.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError")), { once: true });
    },
  });
}

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
  const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
  requests.push(url.search);
  return serve(url, init?.signal);
}) as typeof fetch;

try {
  // A stalled stream resumes from its cursor.
  serve = (url, signal) => url.searchParams.has("after")
    ? new Response(sseBlock("event-2", "turn.completed", { response: { message: "done" } }), { status: 200 })
    : new Response(silentAfter(sseBlock("event-1", "turn.started", {}), signal), { status: 200 });
  const events: ChatStreamEvent[] = [];
  const started = Date.now();
  await streamChatTurn(eventsUrl, (event) => events.push(event), undefined, { stallTimeoutMs: 100 });
  assert.ok(Date.now() - started < 5_000, "a stalled stream is abandoned after the stall timeout");
  assert.deepEqual(requests, ["", "?after=event-1"], "the reader resumes from the last delivered event");
  assert.deepEqual(events.map((event) => event.kind), ["turn.started", "agent.phase", "turn.completed"]);
  assert.equal(events[1].payload.method, "client/reconnect", "the pending reply learns it is reconnecting");
  assert.equal(events[1].event_id, "", "the local reconnect phase never moves the cursor");

  // A caller abort from inside a delivered event ends the stream immediately.
  requests.length = 0;
  const inEvent = new AbortController();
  await assert.rejects(streamChatTurn(eventsUrl, () => inEvent.abort(), inEvent.signal, { stallTimeoutMs: 10_000 }), isAbort);
  assert.deepEqual(requests, [""], "a caller abort is not retried");

  // An abort that happened before the call opens no connection.
  requests.length = 0;
  const before = new AbortController();
  before.abort();
  const beforeEvents: ChatStreamEvent[] = [];
  await assert.rejects(streamChatTurn(eventsUrl, (event) => beforeEvents.push(event), before.signal), isAbort);
  assert.deepEqual(requests, [], "an already aborted caller opens no connection");
  assert.deepEqual(beforeEvents, []);

  // An abort during the retry backoff ends the wait and opens no new connection.
  requests.length = 0;
  serve = () => new Response("unavailable", { status: 503 });
  const backoff = new AbortController();
  const backoffEvents: ChatStreamEvent[] = [];
  const backoffStarted = Date.now();
  const backoffRun = streamChatTurn(eventsUrl, (event) => backoffEvents.push(event), backoff.signal);
  originalSetTimeout(() => backoff.abort(), 30);
  await assert.rejects(backoffRun, isAbort);
  assert.ok(Date.now() - backoffStarted < 250, "the backoff wait ends when the caller aborts");
  assert.deepEqual(requests, [""], "no connection opens after the caller aborts during backoff");
  assert.deepEqual(backoffEvents.map((event) => event.payload.method), ["client/reconnect"]);

  // An abort while a read waits ends that read without reconnecting.
  requests.length = 0;
  serve = (_url, signal) => new Response(silentAfter(null, signal), { status: 200 });
  const reading = new AbortController();
  const readingRun = streamChatTurn(eventsUrl, () => {}, reading.signal, { stallTimeoutMs: 10_000 });
  originalSetTimeout(() => reading.abort(), 30);
  await assert.rejects(readingRun, isAbort);
  assert.deepEqual(requests, [""], "an abort during a read is not retried");

  // Four stalled connections end with the resume wrapper's typed recovery
  // error, like any other exhausted reconnect. Only the watchdog clock is
  // shortened; the retry backoff keeps its production delays.
  requests.length = 0;
  globalThis.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) =>
    originalSetTimeout(handler, timeout === CHAT_STREAM_STALL_TIMEOUT_MS ? 50 : timeout, ...args)) as typeof setTimeout;
  const exhausted = await resumeChatTurnStreaming("s", "t").then(() => null, (error: unknown) => error);
  globalThis.setTimeout = originalSetTimeout;
  assert.equal(requests.length, 4, "the watchdog reconnects a bounded number of times");
  assert.ok(exhausted instanceof ChatApiError, "stall exhaustion is a typed Chat error, not a raw abort");
  assert.equal(exhausted.payload.reconnectable, true);
  assert.equal(exhausted.payload.session_id, "s");
  assert.equal(exhausted.payload.turn_id, "t");
  assert.equal(exhausted.payload.events_url, eventsUrl);
  assert.equal(exhausted.payload.reconnect_attempts, 4);
} finally {
  globalThis.fetch = originalFetch;
  globalThis.setTimeout = originalSetTimeout;
}

console.log("chat-stream-stall-smoke: ok");
