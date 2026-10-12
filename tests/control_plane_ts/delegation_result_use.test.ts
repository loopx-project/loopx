import test from "node:test";
import assert from "node:assert/strict";
import {qualifyDelegationResultUse} from "../../loopx/control_plane/collaboration/delegation_result_use.ts";

const hash = "a".repeat(64);
const input = (operation_id: string) => ({operation_id, ref: "output.json", sha256: hash,
  input_ref: "source.json", input_available: true});
const node = (operation_id: string, inputs: ReturnType<typeof input>[] = []) => ({operation_id,
  accepted: true, artifacts: [{ref: "output.json", sha256: hash}], inputs});

test("a consumer cannot reuse accepted output with unavailable declared ancestry", () => {
  const nodes = [node("source"), node("middle", [{...input("source"), input_available: false}]),
    node("consumer", [input("middle")])];
  const result = qualifyDelegationResultUse({roots: [input("consumer")], nodes});
  assert.equal(result.state, "unavailable");
  assert.equal(result.reason, "input_unavailable");
  assert.equal(result.blocking_operation_id, "middle");
  assert.equal(result.blocking_input_ref, "source.json");
  const receiver = qualifyDelegationResultUse({roots: [{...input("source"), input_available: false}], nodes: []});
  assert.equal(receiver.blocking_operation_id, undefined, "A receiver input failure must not blame the source execution");
  assert.equal(receiver.blocking_input_ref, "source.json");
  assert.deepEqual(result.path, ["consumer", "middle"]);
  assert.equal(qualifyDelegationResultUse({roots: [input("consumer")], nodes: nodes.map(row =>
    row.operation_id === "middle" ? node("middle", [input("source")]) : row)}).state, "current");
});

test("missing source, wrong version and cycles are unavailable; siblings cannot hide them", () => {
  assert.equal(qualifyDelegationResultUse({roots: [input("missing")], nodes: []}).reason, "source_unavailable");
  assert.equal(qualifyDelegationResultUse({roots: [{...input("source"), sha256: "b".repeat(64)}],
    nodes: [node("source")]}).reason, "source_version_changed");
  assert.equal(qualifyDelegationResultUse({roots: [input("a")],
    nodes: [node("a", [input("b")]), node("b", [input("a")])]}).reason, "dependency_cycle");
  assert.equal(qualifyDelegationResultUse({roots: [input("source"), input("missing")],
    nodes: [node("source")]}).state, "unavailable");
});

test("incomplete collection is never acceptance, and a diamond verifies each source once", () => {
  assert.equal(qualifyDelegationResultUse({roots: [input("source")], nodes: [],
    truncated: true}).reason, "verification_budget_exhausted");
  const result = qualifyDelegationResultUse({roots: [input("left"), input("right")],
    nodes: [node("source"), node("left", [input("source")]), node("right", [input("source")])]});
  assert.equal(result.state, "current");
  assert.equal(result.checked_operation_count, 3);
  assert.throws(() => qualifyDelegationResultUse({roots: [input("source")],
    nodes: [node("source"), node("source")]}), /duplicate/);
});

 test("collection deadline and depth cannot become current; existing long IDs remain valid", () => {
  const id = "a".repeat(160);
  assert.equal(qualifyDelegationResultUse({roots: [input(id)], nodes: [node(id)]}).state, "current");
  assert.equal(qualifyDelegationResultUse({roots: [input("a")], nodes: [node("a")],
    truncated: true}).reason, "verification_budget_exhausted");
  const nodes = Array.from({length: 17}, (_, i) => node(`op-${i}`, i < 16 ? [input(`op-${i + 1}`)] : []));
  assert.equal(qualifyDelegationResultUse({roots: [input("op-0")], nodes}).reason, "verification_budget_exhausted");
});
