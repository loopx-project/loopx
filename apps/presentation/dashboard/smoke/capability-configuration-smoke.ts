import assert from "node:assert/strict";

import { changeStewardExecutorDraft, goalReplanCadenceEditorValue, goalReplanCadenceWriteValue, hasReplanCadenceOverride, goalStorageEditorValue, parseEditableCapabilityJson, projectEditableCapabilityConfiguration, replanCadenceEditorDefaults } from "../src/data/capability-configuration.js";

const codexDraft = { executor_endpoint: "codex", executor_model: "gpt-fixture", executor_reasoning_effort: "high", selection_policy: "preferred" };
const managedDraft = changeStewardExecutorDraft(codexDraft, "executor_endpoint", "dsh");
assert.deepEqual(managedDraft, { ...codexDraft, executor_endpoint: "dsh", executor_model: null, executor_reasoning_effort: null },
  "switching Runtime lets the owning backend resolve the new provider defaults");
assert.deepEqual(changeStewardExecutorDraft({ ...managedDraft, executor_model: "deepseek-fixture" }, "executor_endpoint", "codex"),
  { ...codexDraft, executor_model: null, executor_reasoning_effort: null }, "switching back cannot send a DeepSeek model to Codex");
assert.deepEqual(changeStewardExecutorDraft(codexDraft, "executor_endpoint", "codex"), codexDraft,
  "reselecting the same Runtime preserves explicit model choices");
assert.deepEqual(changeStewardExecutorDraft(codexDraft, "executor_model", "custom-model"), { ...codexDraft, executor_model: "custom-model" });
assert.equal(codexDraft.executor_model, "gpt-fixture", "editing the draft does not mutate the accepted configuration");

const periodicReportEditor = {
  fields: [
    { key: "enabled" },
    { key: "profile_preset" },
    { key: "route_ref" },
    { key: "timezone" },
    { key: "schedule", nullable: true },
  ],
};

const storageEditor = { fields: [{ key: "new_goal_provider" }, { key: "canonical_creation" }, { key: "new_goal_handoff_mode" }] };
const cadenceEditor = { fields: [{ key: "count_unit" }, { key: "count" }, { key: "stride_strategy" }] };
const cadenceDefault = { count_unit: "effective_turns", count: 6, stride_strategy: "off" };
assert.deepEqual(projectEditableCapabilityConfiguration(cadenceEditor,
  { stride_strategy: "evidence_conditioned_v1" },
  replanCadenceEditorDefaults({ count_unit: "effective_turns", count: 3, stride_strategy: "off" }, cadenceDefault)),
  { count_unit: "effective_turns", count: 3, stride_strategy: "evidence_conditioned_v1" },
  "a sparse JSON draft returning to the form preserves the effective review interval");
assert.deepEqual(projectEditableCapabilityConfiguration(cadenceEditor,
  { count_unit: "completed_todos", count: 2 },
  replanCadenceEditorDefaults({ count_unit: "effective_turns", count: 3, stride_strategy: "evidence_conditioned_v1" }, cadenceDefault)),
  { count_unit: "completed_todos", count: 2, stride_strategy: "off" },
  "an explicit cadence wins and omitted strategy retains default-off semantics");
assert.deepEqual(replanCadenceEditorDefaults(
  { schema_version: "todo_replan_cadence_machine_defaults_v0", completed_todos: 4 }, cadenceDefault),
  { count_unit: "completed_todos", count: 4, stride_strategy: "off" },
  "legacy effective intervals retain their own unit and count");
const inheritedReview = { count_unit: "effective_turns", count: 3, stride_strategy: "off" };
assert.deepEqual(goalReplanCadenceWriteValue({...inheritedReview, planning_horizon_goal: true}, false, false),
  {stride_strategy: "off"}, "displaying the default Goal context does not create an override");
for (const enabled of [true, false]) {
  assert.deepEqual(goalReplanCadenceWriteValue({...inheritedReview, planning_horizon_goal: enabled}, false, true),
    {stride_strategy: "off", planning_horizon_goal: enabled}, "explicit horizon choices survive a form save");
}
assert.equal(replanCadenceEditorDefaults({...inheritedReview, planning_horizon_goal: false},
  {...cadenceDefault, planning_horizon_goal: true}).planning_horizon_goal, false,
  "a sparse Goal form displays the effective machine opt-out");
assert.deepEqual(goalReplanCadenceEditorValue({ stride_strategy: "evidence_conditioned_v1" }, inheritedReview),
  { ...inheritedReview, stride_strategy: "evidence_conditioned_v1" },
  "strategy-only Goal settings display the live review interval");
assert.deepEqual(goalReplanCadenceWriteValue({ ...inheritedReview, stride_strategy: "evidence_conditioned_v1" }, false),
  { stride_strategy: "evidence_conditioned_v1" },
  "changing only the strategy must not pin the displayed interval");
assert.deepEqual(goalReplanCadenceWriteValue({ ...inheritedReview, count: 2, stride_strategy: "evidence_conditioned_v1" }, true),
  { count_unit: "effective_turns", count: 2, stride_strategy: "evidence_conditioned_v1" },
  "an edited interval remains an explicit Goal override");
assert.deepEqual(goalReplanCadenceWriteValue({ count_unit: "completed_todos", count: 2, stride_strategy: "off" },
  false),
  { stride_strategy: "off" },
  "legacy device units can be inherited without pinning their count");
assert.equal(hasReplanCadenceOverride({ stride_strategy: "evidence_conditioned_v1" }), false);
assert.equal(hasReplanCadenceOverride({ count_unit: "effective_turns", count: 3 }), true);
assert.equal(hasReplanCadenceOverride({ completed_todos: 2 }), true, "legacy pinned cadences stay explicit");
assert.deepEqual(goalReplanCadenceWriteValue({ ...inheritedReview, stride_strategy: "evidence_conditioned_v1" }, true),
  { ...inheritedReview, stride_strategy: "evidence_conditioned_v1" },
  "an explicit interval remains pinned even when it matches the machine policy");
assert.deepEqual(goalReplanCadenceWriteValue({ count_unit: "effective_turns", count: 2, stride_strategy: "evidence_conditioned_v1" }, false),
  { stride_strategy: "evidence_conditioned_v1" },
  "removing JSON interval fields clears only the cadence, including a previously pinned value");
const storageDefault = { schema_version: "loopx_goal_storage_defaults_v1", new_goal_provider: "sqlite",
  canonical_creation: true, new_goal_handoff_mode: "hard_lease" };
assert.deepEqual(projectEditableCapabilityConfiguration(storageEditor,
  goalStorageEditorValue({ schema_version: "loopx_goal_storage_defaults_v0", new_goal_provider: "file" }), storageDefault),
  { new_goal_provider: "file", canonical_creation: false, new_goal_handoff_mode: "hard_lease" },
  "opening a historical selector must not enable canonical creation through today's defaults");
assert.deepEqual(projectEditableCapabilityConfiguration(storageEditor, goalStorageEditorValue(storageDefault), storageDefault),
  { new_goal_provider: "sqlite", canonical_creation: true, new_goal_handoff_mode: "hard_lease" },
  "an unconfigured device editor shows the shared new-Goal default");
assert.equal((goalStorageEditorValue({ ...storageDefault, canonical_creation: false }) as typeof storageDefault).canonical_creation, false,
  "an explicit v1 opt-out survives editor mode changes");

assert.deepEqual(
  projectEditableCapabilityConfiguration(periodicReportEditor, {
    schema_version: "periodic_report_machine_defaults_v0",
    enabled: true,
    inheritance: "live_machine_default",
    profile_preset: "weekly-progress",
    route_ref: "loopx-manager",
    timezone: "Asia/Shanghai",
  }),
  {
    enabled: true,
    profile_preset: "weekly-progress",
    route_ref: "loopx-manager",
    timezone: "Asia/Shanghai",
  },
  "machine-only envelope fields must never enter a Goal write request",
);

assert.deepEqual(
  projectEditableCapabilityConfiguration(
    { fields: [{ key: "enabled" }] },
    { enabled: true, mode: "shadow_file", status: "active" },
  ),
  { enabled: true },
  "derived Goal readback fields must remain read-only",
);

assert.deepEqual(
  projectEditableCapabilityConfiguration(
    { fields: [{ key: "enabled" }, { key: "timezone" }] },
    { enabled: true },
    { enabled: false, timezone: "UTC", schema_version: "ignored" },
  ),
  { enabled: true, timezone: "UTC" },
  "editor defaults fill absent writable fields without granting authority to hidden keys",
);

assert.deepEqual(
  projectEditableCapabilityConfiguration(
    { fields: [{ key: "enabled" }, { key: "max_children" }, { key: "allowed_domains" }] },
    { enabled: false, max_children: null, allowed_domains: [] },
    { enabled: false, max_children: 4, allowed_domains: [] },
  ),
  { enabled: false, max_children: 4, allowed_domains: [] },
  "typed editors must replace non-editable null projections with capability defaults",
);


assert.deepEqual(parseEditableCapabilityJson(periodicReportEditor, '{"enabled":false,"timezone":"UTC"}'), { enabled: false, timezone: "UTC" });
for (const invalid of ['{', 'null', '[]', 'true', '{"schema_version":"injected"}', '{"enabled":true,"status":"active"}', '{"__proto__":{}}']) {
  assert.equal(parseEditableCapabilityJson(periodicReportEditor, invalid), null, `reject invalid or unregistered JSON fields: ${invalid}`);
}

console.log("capability configuration projection and JSON boundary smoke: ok");

const schedule = { schema_version: "periodic_report_schedule_v0", schedule_id: "weekly",
  rrule: "FREQ=WEEKLY;BYDAY=FR;BYHOUR=18;BYMINUTE=0", timezone: "Asia/Shanghai" };
assert.deepEqual(projectEditableCapabilityConfiguration(periodicReportEditor, { schedule }), { schedule });
assert.deepEqual(parseEditableCapabilityJson(periodicReportEditor, JSON.stringify({ schedule })), { schedule });
assert.deepEqual(projectEditableCapabilityConfiguration(periodicReportEditor, { schedule: null }, { schedule }), { schedule: null },
  "explicit nullable clear must not restore the inherited schedule on editor mode changes");

assert.deepEqual(
  projectEditableCapabilityConfiguration(
    { fields: [{ key: "wait_for_ci" }, { key: "review_priority" }] },
    { wait_for_ci: false, review_priority: "other-developers-first", schema_version: "pull_request_review_goal_configuration_v0" },
    { wait_for_ci: true },
  ),
  { wait_for_ci: false, review_priority: "other-developers-first" },
  "an explicit Goal CI opt-out must survive default projection and omit envelope fields",
);
