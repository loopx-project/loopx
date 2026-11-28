import assert from "node:assert/strict";
import test from "node:test";
import {lintExternalTransports} from "./lint-external-transports.mjs";

test("direct transport access is restricted to its declared owner and API", () => {
  const consumer = "loopx/control_plane/example.ts";
  for (const source of [
    "await fetch(url)", "const send = globalThis.fetch", 'await window["fetch"](url)',
    'import {request as send} from "node:https"', 'await import("undici")', 'require("http")',
  ]) assert.ok(lintExternalTransports(consumer, source).length > 0, source);
  assert.deepEqual(lintExternalTransports(consumer, "client.fetch(); const callbacks = {fetch: handler}"), []);
  assert.deepEqual(lintExternalTransports(consumer, "function lookup(fetch) { return fetch(url); }"), []);
  assert.deepEqual(lintExternalTransports(consumer, 'import {type RequestOptions} from "node:https"'), []);
  const owner = "loopx/control_plane/runtime/usage_statistics.ts";
  assert.deepEqual(lintExternalTransports(owner, "await fetch(url)"), []);
  assert.equal(lintExternalTransports(owner, 'require("node:http")').length, 1);
});
