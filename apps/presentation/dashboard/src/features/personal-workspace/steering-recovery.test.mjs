import assert from "node:assert/strict";
import test from "node:test";

const key = JSON.stringify(["inline", "session", "turn"]);
const request = {sessionId: "session", turnId: "turn", text: "Keep the original constraint", id: "original-request"};

test("executor-keyed bound recovery retains the original request and excludes peers", async () => {
  const bound = "bound/source-one/session", old = `${bound}:codex`;
  const peer = "bound/source-two/peer:codex";
  const queued = {...request, delivery: "queue", attachments: []};
  let stored = JSON.stringify([[old, queued], [peer, {...request, sessionId: "peer", id: "peer-request"}]]);
  let writable = false;
  globalThis.window = {sessionStorage: {getItem: () => stored, setItem: (_, value) => {
    if (!writable) throw new Error("Synthetic storage failure");
    stored = value;
  }}};
  const recovery = await import("./steering-recovery.ts?bound-legacy");
  assert.deepEqual(recovery.legacySteeringRequests("session", bound), [[old, queued]]);
  assert.equal(recovery.restoreLegacySteeringRequest(bound, peer, "session"), undefined);
  assert.deepEqual(recovery.restoreLegacySteeringRequest(bound, old, "session"), queued, "Text remains recoverable even when storage is unavailable");
  assert.equal(recovery.readSteeringRequest(bound), undefined, "Recoverable text is not a qualified dispatch identity");
  assert.deepEqual(recovery.readSteeringRequest(old), queued);
  writable = true;
  recovery.restoreLegacySteeringRequest(bound, old, "session");
  assert.deepEqual(recovery.readSteeringRequest(bound), queued);
  assert.equal(recovery.readSteeringRequest(old), undefined);
  const reloaded = await import("./steering-recovery.ts?bound-legacy-reload");
  assert.deepEqual(reloaded.readSteeringRequest(bound), queued);
  assert.equal(reloaded.readSteeringRequest(peer).id, "peer-request");
});

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

test("an undispatched storage failure leaves the draft editable after recovery", async () => {
  let available = false, stored = null;
  globalThis.window = {sessionStorage: {getItem: () => stored, setItem: (_, value) => {
    if (!available) throw new Error("synthetic storage failure");
    stored = value;
  }}};
  const recovery = await import("./steering-recovery.ts?unavailable");
  assert.equal(recovery.retainSteeringRequest(key, request), false);
  assert.equal(recovery.readSteeringRequest(key), undefined, "A refused dispatch cannot become an uncertain delivery");
  available = true;
  const edited = {...request, text: "Revised before any dispatch", id: "edited-request"};
  assert.equal(recovery.retainSteeringRequest(key, edited), true);
  const reloaded = await import("./steering-recovery.ts?recovered");
  assert.deepEqual(reloaded.readSteeringRequest(key), edited, "The first qualified attempt retains the edited draft and identity");
});

test("a silently dropped browser write is not a durable retry identity", async () => {
  globalThis.window = {sessionStorage: {getItem: () => null, setItem() {}}};
  const recovery = await import("./steering-recovery.ts?dropped");
  assert.equal(recovery.retainSteeringRequest(key, request), false);
  assert.equal(recovery.readSteeringRequest(key), undefined);
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

test("legacy recovery moves one exact Session/Turn identity into the composer", async () => {
  const peer = {...request, sessionId: "peer", id: "peer-request"};
  const peerKey = JSON.stringify(["inline", "peer", "turn"]);
  let stored = JSON.stringify([[key, request], [peerKey, peer]]);
  globalThis.window = {sessionStorage: {getItem: () => stored, setItem: (_, value) => {stored = value;}}};
  const recovery = await import("./steering-recovery.ts?legacy");
  assert.deepEqual(recovery.legacySteeringRequests("session"), [[key, request]]);
  assert.equal(recovery.restoreLegacySteeringRequest("composer", peerKey, "session"), undefined);
  assert.deepEqual(recovery.restoreLegacySteeringRequest("composer", key, "session"), request);
  assert.equal(recovery.readSteeringRequest(key), undefined);
  assert.deepEqual(recovery.readSteeringRequest("composer"), request);
  assert.deepEqual(recovery.readSteeringRequest(peerKey), peer);
  const reloaded = await import("./steering-recovery.ts?legacy-reload");
  assert.deepEqual(reloaded.readSteeringRequest("composer"), request);
  assert.equal(reloaded.legacySteeringRequests("session").length, 0);
});

test("failed legacy migration retains recovery and cannot overwrite a composer retry", async () => {
  const other = {...request, id: "other-ingress"};
  let stored = JSON.stringify([[key, request], ["other-composer", other]]), writable = false;
  globalThis.window = {sessionStorage: {getItem: () => stored, setItem: (_, value) => {
    if (!writable) throw new Error("QuotaExceededError");
    stored = value;
  }}};
  const recovery = await import("./steering-recovery.ts?legacy-failed");
  assert.deepEqual(recovery.restoreLegacySteeringRequest("composer", key, "session"), request);
  assert.deepEqual(recovery.readSteeringRequest(key), request, "The durable old location survives a failed copy");
  assert.equal(recovery.readSteeringRequest("composer"), undefined, "A failed copy creates no qualified composer retry");
  assert.deepEqual(recovery.restoreLegacySteeringRequest("composer", key, "session"), request, "The same identity remains restorable after clearing the input");
  assert.equal(recovery.retainSteeringRequest("other-composer", {...request, id: "replacement-ingress"}), false);
  assert.equal(recovery.restoreLegacySteeringRequest("other-composer", key, "session"), undefined, "A different retry is never replaced");
  assert.equal(recovery.retainSteeringRequest("composer", request), false, "Memory alone cannot qualify dispatch");
  writable = true;
  assert.equal(recovery.retainSteeringRequest("composer", request), true);
  recovery.retireSteeringRequest("composer", request.id);
  assert.deepEqual(JSON.parse(stored), [["other-composer", other]], "Acknowledgement retires only its legacy alias, preserving the other composer");
});

test("an unsaved edit cannot replace the original uncertain retry identity", async () => {
  let stored = null, writable = true;
  globalThis.window = {sessionStorage: {getItem: () => stored, setItem: (_, value) => {
    if (!writable) throw new Error("QuotaExceededError");
    stored = value;
  }}};
  const recovery = await import("./steering-recovery.ts?failed-edit");
  assert.equal(recovery.retainSteeringRequest("composer", request), true);
  writable = false;
  assert.equal(recovery.retainSteeringRequest("composer", {...request, id: "new-request", text: "new draft"}), false);
  assert.deepEqual(recovery.readSteeringRequest("composer"), request);
  assert.equal(recovery.retainSteeringRequest("composer", request), true);
});

test("an uncertain queued message keeps its delivery and exact images through reload", async () => {
  let stored = null, writable = true;
  globalThis.window = {sessionStorage: {getItem: () => stored, setItem: (_, value) => {
    if (!writable) throw new Error("QuotaExceededError");
    stored = value;
  }}};
  const queued = {...request, delivery: "queue", attachments: [{id: "image", dataUrl: "data:image/png;base64,AQ==", mimeType: "image/png", name: "source.png", size: 1}]};
  const recovery = await import("./steering-recovery.ts?queue-write");
  assert.equal(recovery.retainSteeringRequest("composer", queued), true);
  const reloaded = await import("./steering-recovery.ts?queue-reload");
  assert.deepEqual(reloaded.readSteeringRequest("composer"), queued);
  writable = false;
  assert.equal(reloaded.retainSteeringRequest("composer", {...queued, delivery: undefined}), false, "An unknown queue acknowledgement cannot turn into current-Turn steering");
  assert.equal(reloaded.retainSteeringRequest("composer", {...queued, attachments: []}), false, "Images belong to the same immutable request");
  assert.equal(reloaded.retainSteeringRequest("composer", queued), true);
  reloaded.retireSteeringRequest("composer", "late-unrelated-ack");
  assert.deepEqual(reloaded.readSteeringRequest("composer"), queued);
});

test("unrecognized delivery state is never restored as legacy steering", async () => {
  globalThis.window = {sessionStorage: {getItem: () => JSON.stringify([["composer", {...request, delivery: "future-mode"}]]), setItem() {}}};
  const recovery = await import("./steering-recovery.ts?unknown-mode");
  assert.equal(recovery.readSteeringRequest("composer"), undefined);
});
