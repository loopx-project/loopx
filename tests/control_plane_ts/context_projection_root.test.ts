import assert from "node:assert/strict";
import test from "node:test";
import {contextProjectionChanges} from "../../loopx/control_plane/quota/diagnostic_projection.ts";

test("a scalar-only staged view keeps the transport's property-path contract", () => {
  const source = {quota: {remaining: 1}, coverage_count: 42};
  const view = {ok: true, goal_id: "synthetic", agent_id: "peer", stage: "explore",
    source_hash: `sha256:${"a".repeat(64)}`, explore_basis: null};
  const {changes} = contextProjectionChanges(source, view, undefined, {}) as {
    changes: {path: string[]; value: unknown}[];
  };
  const restored: Record<string, unknown> = {};
  for (const change of changes) {
    assert.ok(change.path.length > 0, "the consumer addresses properties, not a root replacement");
    assert.equal(change.path.length, 1);
    restored[change.path[0]] = change.value;
  }
  assert.deepEqual(restored, view);
  assert.deepEqual(source, {quota: {remaining: 1}, coverage_count: 42});
});
