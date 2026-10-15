import assert from "node:assert/strict";
import test from "node:test";

import { deriveRuntimeReadiness, runtimeRequirement } from "./runtime-readiness.ts";

const codex = { agentId: "codex", label: "Codex", available: true, executable: "codex" };
const codexMissing = { ...codex, available: false, unavailableReason: "executable_not_found" };
const claudeMissing = { agentId: "claude-code", label: "Claude Code", available: false, unavailableReason: "executable_not_found", executable: "claude" };
const apiMissing = { agentId: "anthropic-api", label: "Claude API", available: false, unavailableReason: "api_key_not_set", environmentVariable: "ANTHROPIC_API_KEY" };

test("an empty list is still detecting, never evidence of absence", () => {
  assert.deepEqual(deriveRuntimeReadiness([], "codex"), { kind: "detecting" });
});

test("nothing available lists every runtime as missing", () => {
  const readiness = deriveRuntimeReadiness([codexMissing, claudeMissing, apiMissing], "codex");
  assert.equal(readiness.kind, "none_available");
  assert.deepEqual(readiness.missing.map((row) => row.agentId), ["codex", "claude-code", "anthropic-api"]);
});

test("a selected runtime that is not available names the alternatives", () => {
  const readiness = deriveRuntimeReadiness([codexMissing, { ...claudeMissing, available: true }], "codex");
  assert.equal(readiness.kind, "selected_unavailable");
  assert.equal(readiness.selected.agentId, "codex");
  assert.deepEqual(readiness.alternatives.map((row) => row.agentId), ["claude-code"]);
});

test("an available selection, or no selection with something available, is ready", () => {
  assert.deepEqual(deriveRuntimeReadiness([codex, claudeMissing], "codex"), { kind: "ready", selected: codex });
  assert.deepEqual(deriveRuntimeReadiness([codex, claudeMissing], null), { kind: "ready", selected: null });
  assert.deepEqual(deriveRuntimeReadiness([codex], "unknown-agent"), { kind: "ready", selected: null }, "an unknown selection does not block");
});

test("requirements are named from the service's reason, never guessed", () => {
  assert.deepEqual(runtimeRequirement(codexMissing), { kind: "executable", name: "codex" });
  assert.deepEqual(runtimeRequirement(apiMissing), { kind: "environment_variable", name: "ANTHROPIC_API_KEY" });
  assert.deepEqual(runtimeRequirement({ ...claudeMissing, unavailableReason: "host offline", executable: null }), { kind: "other", reason: "host offline" });
  assert.deepEqual(runtimeRequirement({ ...claudeMissing, unavailableReason: null }), { kind: "other", reason: null });
});
