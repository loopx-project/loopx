import assert from "node:assert/strict";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {WorkspaceSettingsPage} from "../src/features/personal-workspace/workspace-settings-page.tsx";
import {WorkspaceI18nProvider} from "../src/features/personal-workspace/i18n.tsx";
import {readSettingsRoute} from "../src/features/personal-workspace/workspace-settings-route.ts";

const goal = {goalId: "shared-goal", title: "Research", sourceAccess: {namespace: "readonly-source", label: "Read-only archive", readOnly: true}};
const render = patch => renderToStaticMarkup(React.createElement(WorkspaceI18nProvider, {initialLocale: "en"},
  React.createElement(WorkspaceSettingsPage, {callbacks: {}, goals: [goal], scope: "goal", initialGoalId: goal.goalId,
    goalNotifications: [], onChanged() {}, onClose() {}, onThemeChange() {}, onNavigate() {}, theme: "loopx", ...patch})));

for (const initialTab of ["capabilities", "ownership", "cadence", "lark"]) {
  const html = render({initialTab});
  assert.match(html, /Read-only archive/);
  assert.match(html, /read-only/);
  assert.doesNotMatch(html, /personal-(?:cadence-form|capability-detail|lark-connections|configuration-backup)/,
    "a read-only source must not mount local Goal management, regardless of category");
}
for (const initialGoalId of [undefined, "", "unknown-goal"]) {
  const html = render({initialTab: "ownership", initialGoalId});
  assert.doesNotMatch(html, /personal-cadence-form/, "missing targets cannot inherit the background Goal");
}
const machine = render({scope: "machine", initialTab: "language"});
assert.doesNotMatch(machine, /Target Goal|Task ownership|Automation cadence/);
assert.match(machine, /This device/);
assert.deepEqual(readSettingsRoute(new URL("http://localhost/chat/?settings_scope=goal&settings_goal=shared-goal&settings_tab=ownership")),
  {kind: "settings", scope: "goal", goalId: "shared-goal", tab: "ownership"});
assert.deepEqual(readSettingsRoute(new URL("http://localhost/chat/?goalId=background&settings_scope=goal")),
  {kind: "settings", scope: "goal", goalId: "", tab: "capabilities"});
assert.equal(readSettingsRoute(new URL("http://localhost/chat/?settings_goal=shared-goal")), null);
console.log("Settings target: read-only/missing Goal refusal and explicit route identity passed");
