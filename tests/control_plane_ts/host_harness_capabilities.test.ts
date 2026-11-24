import assert from "node:assert/strict";
import test from "node:test";

import {
  AGY_HOST_CAPABILITIES,
  CANONICAL_HOST_HARNESS_CAPABILITIES,
  CLAUDE_HOST_CAPABILITIES,
  DEFAULT_HOST_CAPABILITIES_REGISTRY,
  DSH_HOST_CAPABILITIES,
  HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION,
  HOST_HARNESS_CAPABILITY_FLAGS,
  HOST_INTEGRATION_TIERS,
  createHostCapabilitiesRegistry,
  defineHostHarnessCapabilities,
  getHostCapabilities,
  hostSupportsCapability,
  isHostHarnessCapabilityFlag,
  isHostIntegrationTier,
  listHostCapabilities,
  registerHostCapabilities,
  validateHostHarnessCapabilities,
} from "../../loopx/control_plane/hosts/host_harness_capabilities.ts";

test("integration tiers and capability flags define the closed contract vocabulary", () => {
  assert.deepEqual(HOST_INTEGRATION_TIERS, [
    "boundary_cli",
    "orchestrated_app",
    "in_tree_codesigned",
  ]);
  assert.deepEqual(HOST_HARNESS_CAPABILITY_FLAGS, [
    "session_fork",
    "epoch_reset",
    "fold_and_prefetch",
    "quiet_turn",
    "compact_hook",
    "verifier_hook",
    "native_context_budget",
    "managed_thread_resume",
  ]);
  assert.equal(isHostIntegrationTier("boundary_cli"), true);
  assert.equal(isHostIntegrationTier("orchestrated_app"), true);
  assert.equal(isHostIntegrationTier("in_tree_codesigned"), true);
  assert.equal(isHostIntegrationTier("unknown_tier"), false);
  assert.equal(isHostHarnessCapabilityFlag("verifier_hook"), true);
  assert.equal(isHostHarnessCapabilityFlag("not_a_flag"), false);
});

test("canonical flagship descriptors cover all three integration tiers truthfully", () => {
  assert.equal(CANONICAL_HOST_HARNESS_CAPABILITIES.length, 3);
  assert.deepEqual(
    listHostCapabilities().map((item) => item.host_id),
    ["agy", "claude", "dsh"],
  );

  assert.equal(AGY_HOST_CAPABILITIES.integration_tier, "orchestrated_app");
  assert.deepEqual(AGY_HOST_CAPABILITIES.supported_capabilities, []);
  assert.deepEqual(AGY_HOST_CAPABILITIES.hook_events, ["Stop"]);
  assert.equal(AGY_HOST_CAPABILITIES.native_goal_primitive, true);
  assert.equal(AGY_HOST_CAPABILITIES.native_scheduler_primitive, true);

  assert.equal(CLAUDE_HOST_CAPABILITIES.integration_tier, "boundary_cli");
  assert.deepEqual(CLAUDE_HOST_CAPABILITIES.supported_capabilities, ["verifier_hook"]);
  assert.deepEqual(CLAUDE_HOST_CAPABILITIES.hook_events, ["PreToolUse"]);
  assert.equal(CLAUDE_HOST_CAPABILITIES.native_goal_primitive, false);
  assert.equal(CLAUDE_HOST_CAPABILITIES.native_scheduler_primitive, true);

  assert.equal(DSH_HOST_CAPABILITIES.integration_tier, "in_tree_codesigned");
  assert.deepEqual(DSH_HOST_CAPABILITIES.supported_capabilities, ["native_context_budget"]);
  assert.deepEqual(DSH_HOST_CAPABILITIES.hook_events, []);
  assert.equal(DSH_HOST_CAPABILITIES.native_goal_primitive, false);
  assert.equal(DSH_HOST_CAPABILITIES.native_scheduler_primitive, false);

  assert.deepEqual(getHostCapabilities("agy"), AGY_HOST_CAPABILITIES);
  assert.deepEqual(getHostCapabilities("claude"), CLAUDE_HOST_CAPABILITIES);
  assert.deepEqual(getHostCapabilities("dsh"), DSH_HOST_CAPABILITIES);
  assert.equal(Object.isFrozen(AGY_HOST_CAPABILITIES), true);
  assert.equal(Object.isFrozen(CLAUDE_HOST_CAPABILITIES.supported_capabilities), true);
});

test("capability lookup and tier/capability list filters behave deterministically", () => {
  assert.equal(hostSupportsCapability("claude", "verifier_hook"), true);
  assert.equal(hostSupportsCapability("claude", "compact_hook"), false);
  assert.equal(hostSupportsCapability("dsh", "native_context_budget"), true);
  assert.equal(hostSupportsCapability("dsh", "managed_thread_resume"), false);
  assert.equal(hostSupportsCapability("agy", "quiet_turn"), false);

  assert.deepEqual(
    listHostCapabilities({ tier: "boundary_cli" }).map((item) => item.host_id),
    ["claude"],
  );
  assert.deepEqual(
    listHostCapabilities({ tier: "orchestrated_app" }).map((item) => item.host_id),
    ["agy"],
  );
  assert.deepEqual(
    listHostCapabilities({ capability: "native_context_budget" }).map((item) => item.host_id),
    ["dsh"],
  );
});

test("unknown or unregistered hosts return null/false and invalid queries fail closed", () => {
  assert.equal(getHostCapabilities("unregistered_host"), null);
  assert.equal(hostSupportsCapability("unregistered_host", "quiet_turn"), false);

  const isolated = createHostCapabilitiesRegistry();
  assert.equal(getHostCapabilities("agy", { registry: isolated }), null);
  assert.equal(hostSupportsCapability("agy", "quiet_turn", { registry: isolated }), false);
  assert.deepEqual(listHostCapabilities({ registry: isolated }), []);

  assert.throws(() => getHostCapabilities(""), /host_id/);
  assert.throws(() => getHostCapabilities("BadHost"), /host_id/);
  assert.throws(
    () => hostSupportsCapability("claude", "unknown_flag"),
    /unsupported host harness capability flag/,
  );
  assert.throws(
    () => listHostCapabilities({ tier: "invalid_tier" }),
    /unsupported host integration tier/,
  );
  assert.throws(
    () => listHostCapabilities({ capability: "invalid_flag" }),
    /unsupported host harness capability flag/,
  );
});

test("descriptor validation enforces schema, flags, hook invariants, and canonical ordering", () => {
  const valid = defineHostHarnessCapabilities({
    host_id: "sample_host",
    integration_tier: "in_tree_codesigned",
    adapter_module: "loopx.hosts.sample",
    supported_capabilities: ["native_context_budget", "quiet_turn", "session_fork"],
    hook_events: [],
    requires_utf8_stdio: true,
    native_goal_primitive: false,
    native_scheduler_primitive: false,
    description: "  Sample descriptor with canonical ordering.  ",
  });
  assert.equal(valid.schema_version, HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION);
  assert.deepEqual(valid.supported_capabilities, [
    "session_fork",
    "quiet_turn",
    "native_context_budget",
  ]);
  assert.equal(valid.description, "Sample descriptor with canonical ordering.");

  const base = { ...valid };
  assert.throws(
    () => validateHostHarnessCapabilities({ ...base, schema_version: "wrong_v1" }),
    /schema_version/,
  );
  assert.throws(
    () => validateHostHarnessCapabilities({ ...base, extra_field: true }),
    /unsupported host harness capabilities fields/,
  );
  const withoutDescription = { ...base } as Record<string, unknown>;
  delete withoutDescription.description;
  assert.throws(
    () => validateHostHarnessCapabilities(withoutDescription),
    /missing host harness capabilities fields/,
  );
  assert.throws(
    () => validateHostHarnessCapabilities({ ...base, host_id: "UpperCase" }),
    /host_id/,
  );
  assert.throws(
    () => validateHostHarnessCapabilities({ ...base, integration_tier: "custom" }),
    /unsupported host integration tier/,
  );
  assert.throws(
    () => validateHostHarnessCapabilities({ ...base, adapter_module: "loopx/bad/path" }),
    /adapter_module/,
  );
  assert.throws(
    () =>
      validateHostHarnessCapabilities({
        ...base,
        supported_capabilities: ["quiet_turn", "quiet_turn"],
      }),
    /duplicate host harness capability flag/,
  );
  assert.throws(
    () =>
      validateHostHarnessCapabilities({
        ...base,
        supported_capabilities: ["unknown_capability"],
      }),
    /unsupported host harness capability flag/,
  );
  assert.throws(
    () =>
      validateHostHarnessCapabilities({
        ...base,
        supported_capabilities: ["compact_hook"],
        hook_events: [],
      }),
    /require non-empty hook_events/,
  );
  assert.throws(
    () =>
      validateHostHarnessCapabilities({
        ...base,
        supported_capabilities: ["verifier_hook"],
        hook_events: [],
      }),
    /require non-empty hook_events/,
  );
  assert.throws(
    () =>
      validateHostHarnessCapabilities({
        ...base,
        hook_events: ["Stop", "Stop"],
      }),
    /duplicate hook_events/,
  );
  assert.throws(
    () =>
      validateHostHarnessCapabilities({
        ...base,
        requires_utf8_stdio: "yes",
      }),
    /requires_utf8_stdio/,
  );
  assert.throws(
    () =>
      validateHostHarnessCapabilities({
        ...base,
        description: "   ",
      }),
    /description/,
  );

  for (const accepted of [
    "a".repeat(512),
    "😀".repeat(256),
    "😀".repeat(257),
    "😀".repeat(512),
  ]) {
    const validated = validateHostHarnessCapabilities({
      ...base,
      description: accepted,
    });
    assert.equal(validated.description, accepted);
  }
  for (const rejected of ["a".repeat(513), "😀".repeat(513)]) {
    assert.throws(
      () =>
        validateHostHarnessCapabilities({
          ...base,
          description: rejected,
        }),
      /description exceeds 512 Unicode code points/,
    );
  }
});

test("registry rejects conflicting registrations unless replace=true and stays idempotent for equal descriptors", () => {
  const registry = createHostCapabilitiesRegistry([CLAUDE_HOST_CAPABILITIES]);
  assert.deepEqual(
    registerHostCapabilities(CLAUDE_HOST_CAPABILITIES, { registry }),
    CLAUDE_HOST_CAPABILITIES,
  );
  assert.equal(
    registerHostCapabilities(CLAUDE_HOST_CAPABILITIES, { registry }),
    getHostCapabilities("claude", { registry }),
  );

  const conflicting = defineHostHarnessCapabilities({
    host_id: "claude",
    integration_tier: "boundary_cli",
    adapter_module: "loopx.claude_goal_mode",
    supported_capabilities: ["verifier_hook", "compact_hook"],
    hook_events: ["PreToolUse", "PreCompact"],
    requires_utf8_stdio: true,
    native_goal_primitive: false,
    native_scheduler_primitive: true,
    description: "Updated claude descriptor",
  });
  assert.throws(
    () => registerHostCapabilities(conflicting, { registry }),
    /conflicting host capabilities registration/,
  );
  const replaced = registerHostCapabilities(conflicting, {
    registry,
    replace: true,
  });
  assert.deepEqual(getHostCapabilities("claude", { registry }), replaced);
  assert.equal(hostSupportsCapability("claude", "compact_hook", { registry }), true);
  assert.equal(
    hostSupportsCapability("claude", "compact_hook", {
      registry: DEFAULT_HOST_CAPABILITIES_REGISTRY,
    }),
    false,
  );
});
