import assert from "node:assert/strict";

import { type ChatStreamEvent, streamChatTurn } from "../src/data/chat.ts";

// A stuck SSE connection delivers headers and a first event, then no bytes at
// all, not even the service heartbeat. The reader must abandon it, resume from
// its cursor, and tell the pending reply it is reconnecting.
const encoder = new TextEncoder();
const originalFetch = globalThis.fetch;
const requests: string[] = [];

function sseBlock(eventId: string, kind: string, payload: Record<string, unknown>) {
  return encoder.encode(`id: ${eventId}\nevent: ${kind}\ndata: ${JSON.stringify({ created_at: "2026-09-28T00:00:00Z", event_id: eventId, kind, payload, sequence: 1 })}\n\n`);
}

function silentAfter(first: Uint8Array, signal: AbortSignal | null | undefined) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(first);
      signal?.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError")), { once: true });
    },
  });
}

globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
  const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
  requests.push(url.search);
  if (!url.searchParams.has("after")) {
    return new Response(silentAfter(sseBlock("event-1", "turn.started", {}), init?.signal), { status: 200 });
  }
  return new Response(sseBlock("event-2", "turn.completed", { response: { message: "done" } }), { status: 200 });
}) as typeof fetch;

try {
  const events: ChatStreamEvent[] = [];
  const started = Date.now();
  await streamChatTurn("/api/chat/sessions/s/turns/t/events", (event) => events.push(event), undefined, { stallTimeoutMs: 100 });
  assert.ok(Date.now() - started < 5_000, "a stalled stream is abandoned after the stall timeout");
  assert.deepEqual(requests, ["", "?after=event-1"], "the reader resumes from the last delivered event");
  assert.deepEqual(events.map((event) => event.kind), ["turn.started", "agent.phase", "turn.completed"]);
  assert.equal(events[1].payload.method, "client/reconnect", "the pending reply learns it is reconnecting");
  assert.equal(events[1].event_id, "", "the local reconnect phase never moves the cursor");

  // A caller abort still ends the stream immediately instead of reconnecting.
  requests.length = 0;
  const caller = new AbortController();
  const aborted = streamChatTurn("/api/chat/sessions/s/turns/t/events", () => caller.abort(), caller.signal, { stallTimeoutMs: 10_000 });
  await assert.rejects(aborted, (error: unknown) => error instanceof DOMException && error.name === "AbortError");
  assert.deepEqual(requests, [""], "a caller abort is not retried");
} finally {
  globalThis.fetch = originalFetch;
}

console.log("chat-stream-stall-smoke: ok");
