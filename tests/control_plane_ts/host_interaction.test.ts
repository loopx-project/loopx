import assert from "node:assert/strict";
import test from "node:test";
import { projectMcpInteraction } from "../../loopx/control_plane/turn_driver/host_interaction.ts";

test("MCP accepts a collection diagnostic and requires C1 for every decision", () => {
  const diagnostic = {ok: false, reason: "provider unavailable"};
  assert.deepEqual(projectMcpInteraction(diagnostic), diagnostic);
  for (const invalid of [{ok: true}, {ok: false, interaction_contract: {}}, {contract_binding: null},
    {ok: false, schema_version: null}, {schema_version: "loopx_turn_envelope_v99"},
    {schema_version: "loopx_turn_envelope_v0", decision: "backoff"}]) {
    assert.throws(() => projectMcpInteraction(invalid), /unsupported complete Turn decision version/);
  }
  assert.throws(() => projectMcpInteraction({schema_version: "loopx_turn_envelope_v1"}),
    /Turn contract binding/);
});
