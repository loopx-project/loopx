import assert from "node:assert/strict";
import test from "node:test";

const key = JSON.stringify(["inline", "session", "turn"]);
const request = {sessionId: "session", turnId: "turn", text: "Keep the original constraint", id: "original-request"};

test("steering identity survives reload only after exact persistent readback", async () => {
  let stored = null;
  globalThis.window = {sessionStorage: {getItem: () => stored, setItem: (_, value) => {stored = value;}}};
  const first = await import("./steering-recovery.ts?write");
  assert.equal(first.retainSteeringRequest(key, request), true);
  const reloaded = await import("./steering-recovery.ts?read");
  assert.deepEqual(reloaded.readSteeringRequest(key), request);
  reloaded.retireSteeringRequest(key, "another-request");
  assert.deepEqual(reloaded.readSteeringRequest(key), request, "A late response cannot retire another request");
  reloaded.retireSteeringRequest(key, request.id);
  assert.equal(reloaded.readSteeringRequest(key), undefined);
});

test("unavailable storage preserves the page draft but refuses dispatch qualification", async () => {
  let available = false, stored = null;
  globalThis.window = {sessionStorage: {getItem: () => stored, setItem: (_, value) => {
    if (!available) throw new Error("synthetic storage failure");
    stored = value;
  }}};
  const recovery = await import("./steering-recovery.ts?unavailable");
  assert.equal(recovery.retainSteeringRequest(key, request), false);
  assert.deepEqual(recovery.readSteeringRequest(key), request, "The unsent draft is still editable in this page");
  available = true;
  assert.equal(recovery.retainSteeringRequest(key, request), true);
  const reloaded = await import("./steering-recovery.ts?recovered");
  assert.deepEqual(reloaded.readSteeringRequest(key), request, "Repair does not mint a new operation identity");
});

test("a silently dropped browser write is not a durable retry identity", async () => {
  globalThis.window = {sessionStorage: {getItem: () => null, setItem() {}}};
  const recovery = await import("./steering-recovery.ts?dropped");
  assert.equal(recovery.retainSteeringRequest(key, request), false);
  assert.deepEqual(recovery.readSteeringRequest(key), request);
});


test("a durable request retries independently of another conversation's unsaved draft", async () => {
  let stored = null, writable = true;
  globalThis.window = {sessionStorage: {getItem: () => stored, setItem: (_, value) => {
    if (!writable) throw new Error("QuotaExceededError");
    stored = value;
  }}};
  const recovery = await import("./steering-recovery.ts?independent");
  assert.equal(recovery.retainSteeringRequest(key, request), true);
  writable = false;
  const otherKey = JSON.stringify(["composer", "other-goal", "other-agent"]);
  const other = {...request, sessionId: "other-session", turnId: "other-turn", id: "other-request"};
  assert.equal(recovery.retainSteeringRequest(otherKey, other), false);
  assert.equal(recovery.retainSteeringRequest(key, request), true);
  assert.deepEqual(JSON.parse(stored), [[key, request]]);
  for (const field of ["sessionId", "turnId", "text", "id"]) {
    assert.equal(recovery.retainSteeringRequest(key, {...request, [field]: "changed"}), false, field);
  }
  assert.equal(recovery.retainSteeringRequest("another-key", request), false);
  globalThis.window.sessionStorage.getItem = () => {throw new Error("read unavailable");};
  assert.equal(recovery.retainSteeringRequest(key, request), false, "page cache is not persistent evidence");
});
