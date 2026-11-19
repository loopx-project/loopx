import assert from "node:assert/strict";
import {test} from "node:test";
import {selectScenarioShard} from "./shard.mjs";
import {requireBackendReadback} from "../personal-workspace-browser-smoke.mjs";
import {fetchWorkspaceBackend} from "./scenario-context.mjs";
test("shards cover each current and newly appended scenario exactly once", () => {
  for (const length of [49, 50, 51]) {
    const catalog = Array.from({length}, (_, id) => ({id}));
    assert.equal(selectScenarioShard(catalog), catalog);
    const shards = [1,2,3].map(index => selectScenarioShard(catalog, `${index}/3`));
    const ids = shards.flat().map(row => row.id);
    assert.equal(new Set(ids).size, length);
    assert.deepEqual(ids.sort((a,b) => a-b), catalog.map(row => row.id));
  }
});
test("invalid partitions fail before running a partial acceptance", () => {
  for (const shard of ["", "0/3", "4/3", "1/0", "1/4", "1/NaN", "1/2/3"]) {
    assert.throws(() => selectScenarioShard([1,2,3], shard));
  }
});

test("a required backend journey cannot qualify from an early return", () => {
  const required = {id: "backend-fixture", requiresBackendReadback: true};
  for (const result of [{coverageEntries: []}, {backendReadbackVerified: false}]) {
    assert.throws(() => requireBackendReadback(required, result), /Required backend readback did not execute/);
  }
  assert.doesNotThrow(() => requireBackendReadback(required, {backendReadbackVerified: true}));
  assert.doesNotThrow(() => requireBackendReadback({id: "ordinary-fixture"}, {coverageEntries: []}));
});

function proxyFixture(method, failures) {
  const calls = [];
  const response = {ok: true};
  return {calls, response, route: {
    request: () => ({method: () => method, url: () => "http://127.0.0.1:5196/api/chat/sessions?goal_id=fixture",
      headers: () => ({host: "127.0.0.1:5196", origin: "http://127.0.0.1:5196"})}),
    fetch: async options => {
      calls.push(options);
      if (failures.length) throw failures.shift();
      return response;
    },
  }};
}

test("native socket failures retain bounded GET and HEAD recovery", async () => {
  for (const method of ["GET", "HEAD"]) {
    for (const code of ["ECONNRESET", "ECONNREFUSED", "EPIPE"]) {
      const fixture = proxyFixture(method, [Object.assign(new Error("native socket failure"), {code})]);
      assert.equal(await fetchWorkspaceBackend(fixture.route, "http://127.0.0.1:9123"), fixture.response);
      assert.equal(fixture.calls.length, 2);
      assert.deepEqual(fixture.calls[0], fixture.calls[1]);
      assert.equal(fixture.calls[0].url, "http://127.0.0.1:9123/api/chat/sessions?goal_id=fixture");
      assert.equal(fixture.calls[0].headers.origin, "http://127.0.0.1:9123");
    }
  }
  const fixture = proxyFixture("GET", [new Error("route.fetch: write EPIPE\nCall log:\nrequest")]);
  assert.equal(await fetchWorkspaceBackend(fixture.route, "http://127.0.0.1:9123"), fixture.response);
  assert.equal(fixture.calls.length, 2);
});

test("persistent socket failure cannot exceed the existing three attempts", async () => {
  const errors = Array.from({length: 3}, () => Object.assign(new Error("socket failure"), {code: "EPIPE"}));
  const last = errors[2];
  const fixture = proxyFixture("GET", errors);
  await assert.rejects(fetchWorkspaceBackend(fixture.route, "http://127.0.0.1:9123"), error => error === last);
  assert.equal(fixture.calls.length, 3);
});

test("write requests and arbitrary diagnostic prose never authorize a retry", async () => {
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    const error = Object.assign(new Error("socket failure"), {code: "EPIPE"});
    const fixture = proxyFixture(method, [error]);
    await assert.rejects(fetchWorkspaceBackend(fixture.route, "http://127.0.0.1:9123"), observed => observed === error);
    assert.equal(fixture.calls.length, 1);
  }
  const error = new Error("Backend result mentioned EPIPE; this is not a socket signature");
  const fixture = proxyFixture("GET", [error]);
  await assert.rejects(fetchWorkspaceBackend(fixture.route, "http://127.0.0.1:9123"), observed => observed === error);
  assert.equal(fixture.calls.length, 1);
});
