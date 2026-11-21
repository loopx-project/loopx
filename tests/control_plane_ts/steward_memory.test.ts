import assert from "node:assert/strict";
import {test} from "node:test";
import {
  normalizeStewardMemoryPreferences,
  normalizeStewardMemorySettings,
  planStewardMemoryTurn,
} from "../../loopx/control_plane/collaboration/steward_memory.ts";

const STEWARD_CONTEXT = {
  kind: "bound_steward",
  binding_id: "111111111111111111111111",
  project_ref: "222222222222222222222222",
  workspace_path: "/tmp/steward-workspace",
  source_ref: "333333333333333333333333",
  provider_ref: "444444444444444444444444",
  operator_ref: "555555555555555555555555",
  audience: "bound_owner",
  grant: "portfolio_read",
  goal_ids: ["goal-alpha"],
};

const PRIVATE_SCOPE = {
  kind: "external_audience",
  bound_steward: true,
  private_conversation: true,
};

const ENABLED_SETTING = {
  schema_version: "steward_memory_machine_defaults_v0",
  enabled: true,
  user_space: "steward",
  auto_recall: true,
  recall_timeout_seconds: 3,
};

test("default-off and invalid settings reject cleanly", () => {
  assert.equal(normalizeStewardMemorySettings(null).enabled, false);
  const disabled = planStewardMemoryTurn({
    setting: {...ENABLED_SETTING, enabled: false},
    session: {channel_id: "manager.external.native", steward_context: STEWARD_CONTEXT},
    scope: PRIVATE_SCOPE,
  });
  assert.equal(disabled.eligible, false);
  assert.equal(disabled.reason, "disabled");

  assert.throws(() => normalizeStewardMemorySettings({...ENABLED_SETTING, user_space: "bad/space"}));
  assert.throws(() => normalizeStewardMemorySettings({...ENABLED_SETTING, recall_timeout_seconds: 30}));
  assert.throws(() => normalizeStewardMemorySettings({...ENABLED_SETTING, extra: true}));
});

test("owner vs non-owner speaker directories stay isolated and collision-safe", () => {
  const session = {
    channel_id: "manager.external.native.1111.3333",
    steward_context: STEWARD_CONTEXT,
  };
  const ownerPlan = planStewardMemoryTurn({
    setting: ENABLED_SETTING,
    session,
    scope: PRIVATE_SCOPE,
    turn: {turn_id: "t1", origin: "external"},
    sender_ref: STEWARD_CONTEXT.operator_ref,
    sender_label: "Owner",
    sender_type: "user",
  });
  assert.equal(ownerPlan.eligible, true);
  assert.equal(ownerPlan.is_owner, true);
  assert.equal(ownerPlan.speaker_directory, "owner");
  assert.equal(ownerPlan.actor_peer_id, `steward-${STEWARD_CONTEXT.binding_id}`);
  assert.equal(
    ownerPlan.speaker_preferences_uri,
    `viking://user/steward/peers/steward-${STEWARD_CONTEXT.binding_id}/memories/preferences/owner`,
  );

  const guestA = planStewardMemoryTurn({
    setting: ENABLED_SETTING,
    session,
    scope: PRIVATE_SCOPE,
    sender_ref: "ou_guest_a",
    sender_label: "Alice",
    sender_type: "user",
  });
  const guestSameName = planStewardMemoryTurn({
    setting: ENABLED_SETTING,
    session,
    scope: PRIVATE_SCOPE,
    sender_ref: "ou_guest_b",
    sender_label: "Alice",
    sender_type: "user",
  });
  const guestRenamed = planStewardMemoryTurn({
    setting: ENABLED_SETTING,
    session,
    scope: PRIVATE_SCOPE,
    sender_ref: "ou_guest_a",
    sender_label: "Alice Updated",
    sender_type: "user",
  });
  assert.equal(guestA.is_owner, false);
  assert.match(String(guestA.speaker_directory), /^Alice~[0-9a-f]{12}$/);
  assert.notEqual(guestA.speaker_directory, guestSameName.speaker_directory);
  assert.equal(
    String(guestA.speaker_directory).split("~")[1],
    String(guestRenamed.speaker_directory).split("~")[1],
  );
});

test("group chats, project chats, internal monitor/report turns, review turns, and bots are rejected", () => {
  const baseSession = {channel_id: "manager.external.native", steward_context: STEWARD_CONTEXT};
  for (const [name, input] of [
    ["group scope", {setting: ENABLED_SETTING, session: baseSession, scope: {kind: "external_audience", bound_steward: false, private_conversation: false}}],
    ["project session", {setting: ENABLED_SETTING, session: {project_context: {kind: "project_workspace"}}, scope: PRIVATE_SCOPE}],
    ["loopx_execution turn", {setting: ENABLED_SETTING, session: baseSession, scope: PRIVATE_SCOPE, turn: {loopx_execution: true}}],
    ["native_loopx turn", {setting: ENABLED_SETTING, session: baseSession, scope: PRIVATE_SCOPE, turn: {origin: "native_loopx"}}],
    ["peer turn", {setting: ENABLED_SETTING, session: baseSession, scope: PRIVATE_SCOPE, turn: {origin: "peer"}}],
    ["answer_review session", {setting: ENABLED_SETTING, session: {...baseSession, answer_review: true}, scope: PRIVATE_SCOPE}],
    ["metadata_review turn", {setting: ENABLED_SETTING, session: baseSession, scope: PRIVATE_SCOPE, turn: {metadata_review: {}}}],
    ["bot sender", {setting: ENABLED_SETTING, session: baseSession, scope: PRIVATE_SCOPE, sender_type: "bot"}],
  ]) {
    const plan = planStewardMemoryTurn(input as Record<string, unknown>);
    assert.equal(plan.eligible, false, `${name} must not receive steward memory`);
  }
});

test("normalizeStewardMemoryPreferences blocks speaker/uri overrides and enforces slug & size bounds", () => {
  const normalized = normalizeStewardMemoryPreferences([
    {operation: "upsert", topic: "PR Reply Style", content: "Prefer concise Chinese bullets"},
    {operation: "remove", topic: "Old Rule"},
  ]);
  assert.deepEqual(normalized, [
    {
      operation: "upsert",
      topic: "pr-reply-style",
      topic_file: "pr-reply-style.md",
      content: "Prefer concise Chinese bullets",
    },
    {
      operation: "remove",
      topic: "old-rule",
      topic_file: "old-rule.md",
    },
  ]);

  assert.throws(() => normalizeStewardMemoryPreferences([
    {operation: "upsert", topic: "style", content: "hi", speaker: "owner"},
  ]));
  assert.throws(() => normalizeStewardMemoryPreferences([
    {operation: "upsert", topic: "style", content: "hi", uri: "viking://user/other"},
  ]));
  assert.throws(() => normalizeStewardMemoryPreferences([
    {operation: "upsert", topic: "../escape", content: "hi"},
  ]));
  assert.throws(() => normalizeStewardMemoryPreferences([
    {operation: "upsert", topic: "too-long", content: "x".repeat(1201)},
  ]));
});
