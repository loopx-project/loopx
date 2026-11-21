import assert from "node:assert/strict";
import test from "node:test";
import {projectAgentDiscovery} from "../../loopx/capabilities/manager_context/discovery_projection.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

test("bounded discovery retains unread sources and independent continuation offsets", () => {
  const sources = Array.from({length: 14}, (_, i) => ({source_id: `ssh:source-${i}`, source_host: `source-${i}`}));
  const pages = sources.slice(0, 12).map(() => ({ok: true, matched: 2, rows: [
    {goal_id: "shared-name", agent_id: "first"}, {goal_id: "shared-name", agent_id: "second"}]}));
  const result = projectAgentDiscovery({arguments: {view: "agents", limit: 8}, sources, pages});
  assert.equal((result.rows as unknown[]).length, 8);
  assert.equal(result.unknown, true);
  assert.equal(result.matched, null);
  const next = result.next_reads as JsonObject[];
  assert.equal(next.length, 14);
  assert.equal(next[0].offset, 1);
  assert.equal(next[8].offset, 0);
  assert.equal((result.source_pages as JsonObject[])[13].status, "not_read");
  assert.equal(new Set((result.rows as JsonObject[]).map(row => row.source_id)).size, 8);
});

test("a failed source never becomes an empty successful search", () => {
  const result = projectAgentDiscovery({arguments: {view: "agents", query: "release"},
    sources: [{source_id: "local"}, {source_id: "ssh:sample"}],
    pages: [{ok: true, matched: 0, rows: []}, {ok: false, error: "unavailable", reason_code: "timeout", rows: []}]});
  assert.equal(result.matched, null);
  assert.equal(result.unknown, true);
  assert.equal((result.source_pages as JsonObject[])[1].reason_code, "timeout");
  assert.deepEqual(result.next_reads, [{view: "agents", query: "release", source_id: "ssh:sample", offset: 0}]);
});
