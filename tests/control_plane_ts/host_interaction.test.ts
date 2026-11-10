import assert from "node:assert/strict";
import test from "node:test";
import { projectMcpInteraction } from "../../loopx/control_plane/turn_driver/host_interaction.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

function guard(overrides: JsonObject = {}): JsonObject {
  return {ok: true, normal_delivery_allowed: true, selected_todo: {todo_id: "todo_example"},
    interaction_contract: {mode: "bounded_delivery", agent_channel: {must_attempt: true},
      cli_channel: {next_cli_actions: ["refresh-state", "spend-slot"],
        settlement_plan: {steps: ["validation", "durable_writeback", "quota_spend"]},
        required_reads: ["acceptance"], delivery_workspace_causality: {requirement: "required"}}},
    ...overrides};
}

test("MCP delivery replaces duplicate CLI procedure, preserving admission and scope facts", () => {
  const source = guard();
  const before = structuredClone(source);
  const projected = projectMcpInteraction(source);
  const contract = projected.interaction_contract as JsonObject;
  const cli = contract.cli_channel as JsonObject;
  assert.deepEqual(cli.next_cli_actions, []);
  assert.equal(cli.settlement_plan, undefined);
  assert.deepEqual(cli.delivery_workspace_causality, {requirement: "required"});
  assert.deepEqual(cli.required_reads, ["acceptance"]);
  assert.deepEqual(contract.agent_channel, {must_attempt: true});
  assert.equal(projected.normal_delivery_allowed, true);
  assert.equal((contract.mcp_channel as JsonObject).delivery_todo_id, "todo_example");
  assert.deepEqual(source, before);
});

test("replan, blocked and unavailable guards never lose their actual actions", () => {
  for (const overrides of [{normal_delivery_allowed: false}, {selected_todo: null}]) {
    const source = guard(overrides);
    assert.deepEqual((projectMcpInteraction(source).interaction_contract as JsonObject).cli_channel,
      (source.interaction_contract as JsonObject).cli_channel);
  }
  const failed = guard({ok: false});
  assert.deepEqual(projectMcpInteraction(failed), failed);
  assert.deepEqual(projectMcpInteraction({ok: true}), {ok: true});
});

test("declared complete contracts fail closed before legacy MCP projection", () => {
  for (const schema_version of [null, "loopx_turn_envelope_v1", "loopx_turn_envelope_v99"]) {
    assert.throws(() => projectMcpInteraction(guard({schema_version})),
      /complete Turn decision|Turn contract binding/);
    assert.throws(() => projectMcpInteraction(guard({schema_version, ok: false})),
      /complete Turn decision|Turn contract binding/);
  }
  assert.throws(() => projectMcpInteraction(guard({contract_binding: null})),
    /unsupported complete Turn decision version/);
  const historical = {schema_version: "loopx_turn_envelope_v0", decision: "backoff"};
  assert.deepEqual(projectMcpInteraction(historical), historical);
});
