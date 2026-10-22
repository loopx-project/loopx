import type {WorkspaceDrawerSelection} from "./personal-workspace-model";

// View state only. Configuration and permission decisions remain with each
// existing editor and its backend; the background Goal is never a fallback.
export type SettingsTarget = Extract<WorkspaceDrawerSelection, {kind: "settings"}>;
const tabs = ["appearance", "capabilities", "language", "lark", "machine", "provider", "steward", "conversation", "cadence", "ownership"] as const;

export function readSettingsRoute(url: URL): SettingsTarget | null {
  const scope = url.searchParams.get("settings_scope");
  if (scope !== "machine" && scope !== "goal") return null;
  const tab = url.searchParams.get("settings_tab");
  return {kind: "settings", scope,
    ...(scope === "goal" ? {goalId: url.searchParams.get("settings_goal") ?? ""} : {}),
    tab: tabs.find(item => item === tab) ?? (scope === "goal" ? "capabilities" : "steward")};
}

export function writeSettingsRoute(target: SettingsTarget | null, replace = false) {
  const url = new URL(window.location.href);
  for (const key of ["settings_scope", "settings_goal", "settings_tab"]) url.searchParams.delete(key);
  if (target) {
    const scope = target.scope ?? (target.goalId ? "goal" : "machine");
    url.searchParams.set("settings_scope", scope);
    if (scope === "goal" && target.goalId) url.searchParams.set("settings_goal", target.goalId);
    url.searchParams.set("settings_tab", target.tab ?? (scope === "goal" ? "capabilities" : "steward"));
  }
  if (url.href !== window.location.href) window.history[replace ? "replaceState" : "pushState"](window.history.state, "", url);
}
