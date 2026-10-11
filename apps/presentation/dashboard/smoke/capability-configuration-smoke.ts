import assert from "node:assert/strict";

import { goalStorageEditorValue, parseEditableCapabilityJson, projectEditableCapabilityConfiguration } from "../src/data/capability-configuration.js";

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
