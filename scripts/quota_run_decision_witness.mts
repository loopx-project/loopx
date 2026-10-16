// Registry data cannot select another classifier or replace the independent fixture.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolveQuotaRunDecision} from "../loopx/control_plane/quota/run_decision.ts";
import type {JsonObject} from "../loopx/control_plane/effect_program.ts";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const {values} = JSON.parse(input) as {values: string[]};
const fixture = JSON.parse(readFileSync(new URL(
  "../tests/control_plane_ts/fixtures/quota_run_decision.json", import.meta.url), "utf8")) as {
    base_facts: JsonObject; base_result: JsonObject;
    cases: {facts: JsonObject; expected: JsonObject}[];
  };
assert(fixture.cases.length > 0, "quota witness needs ordered guard cases");
const actions = fixture.cases.map(scenario => {
  const decision = resolveQuotaRunDecision({...fixture.base_facts, ...scenario.facts});
  assert.deepEqual(decision, {...fixture.base_result, ...scenario.expected},
    "quota output differs from the ordered guard fixture");
  assert(values.includes(decision.effective_action as string), "quota produced an unregistered action");
  return decision.effective_action;
});
const source = readFileSync(new URL("../loopx/control_plane/quota/run_decision.ts", import.meta.url), "utf8");
const line = source.split("\n").findIndex(text => text.startsWith("export function resolveQuotaRunDecision(")) + 1;
assert(line > 0, "quota classifier must retain its source anchor");
process.stdout.write(JSON.stringify({
  site: "loopx/control_plane/quota/run_decision.ts::resolveQuotaRunDecision", line, actions,
}));
