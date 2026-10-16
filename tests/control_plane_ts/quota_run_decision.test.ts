import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolveQuotaRunDecision} from "../../loopx/control_plane/quota/run_decision.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/quota_run_decision.json", import.meta.url), "utf8"));
for (const scenario of fixture.cases) {
  test(scenario.name, () => {
    assert.deepEqual(resolveQuotaRunDecision({...fixture.base_facts, ...scenario.facts}),
      {...fixture.base_result, ...scenario.expected});
  });
}

test("malformed admission facts cannot manufacture a runnable decision", () => {
  for (const patch of [{normal_delivery_allowed: "true"}, {replan_applies: null},
    {workspace_guard: "not a guard"}, {quota_focus_wait: 1}, {reason: null}]) {
    assert.throws(() => resolveQuotaRunDecision({...fixture.base_facts, ...patch} as JsonObject));
  }
});
