import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ChatModelMenu } from "../src/features/personal-workspace/chat-model-menu.tsx";
import { ChannelHeader } from "../src/features/personal-workspace/channel-header.tsx";
import { WorkspaceI18nProvider } from "../src/features/personal-workspace/i18n.tsx";

// Presentation semantics only. Real width, focus, IME and native delivery still
// belong to the packaged browser and production HTTP/provider checks.
const originalWindow = globalThis.window;
let assertions = 0;
try {
  for (const locale of ["zh-CN", "en"]) {
    globalThis.window = { localStorage: { getItem: () => locale } };
    const render = props => renderToStaticMarkup(createElement(WorkspaceI18nProvider, null,
      createElement(ChatModelMenu, { agentId: "codex", ordinary: true, model: "next-model", effort: "low", ...props })));
    const actual = render({ readback: { agent_model_id: "actual-model", agent_reasoning_effort: "high", agent_service_tier: "priority" } });
    const opening = actual.slice(0, actual.indexOf("<summary"));
    const summary = actual.slice(actual.indexOf("<summary"), actual.indexOf("</summary>"));
    assert.doesNotMatch(opening, /\bopen(?:=|>|\s)/, "Optional settings are collapsed on entry");
    assert.match(summary, /actual-model/);
    assert.match(summary, /high/);
    assert.match(summary, /Fast/);
    assert.doesNotMatch(summary, /next-model|low/, "Pending new settings never masquerade as the current Session");
    assert.match(actual, new RegExp(locale === "zh-CN" ? "后续消息" : "Subsequent messages"));
    assert.match(actual, new RegExp(locale === "zh-CN" ? "当前回合和原历史不变" : "active work and history stay unchanged"));
    const saved = render({readback: {agent_model_id: "actual-model", agent_reasoning_effort: "high",
      requested_agent_model_id: "saved-model", requested_agent_reasoning_effort: "low", model_settings_supported: true}});
    const savedSummary = saved.slice(saved.indexOf("<summary"), saved.indexOf("</summary>"));
    assert.match(savedSummary, /saved-model/);
    assert.doesNotMatch(savedSummary, /actual-model|next-model/);
    assert.match(saved, /actual-model/, "The prior actual configuration remains visible inside the menu");
    assert.match(savedSummary, new RegExp(locale === "zh-CN" ? "已保存，后续消息使用" : "Saved for subsequent messages"));
    const owned = render({readback: {agent_model_id: "host-model", model_settings_supported: false}});
    assert.match(owned, new RegExp(locale === "zh-CN" ? "原宿主或专属设置" : "owning host or dedicated settings"));
    assert.doesNotMatch(owned, /<fieldset/, "Host-owned and legacy Sessions expose readback without a misleading save operation");
    const unknown = render({});
    const unknownSummary = unknown.slice(unknown.indexOf("<summary"), unknown.indexOf("</summary>"));
    assert.match(unknownSummary, new RegExp(locale === "zh-CN" ? "模型设置" : "Model settings"));
    assert.doesNotMatch(unknownSummary, /未知|未回传|unknown|not reported/i, "The closed control offers settings instead of a permanent missing-state label");
    assert.match(unknown, new RegExp(locale === "zh-CN" ? "运行时尚未回传实际模型" : "Runtime has not reported the actual model"), "Opening settings still explains the missing observation");
    assert.doesNotMatch(unknownSummary, /next-model|low|Fast/, "Neither defaults nor a pending choice fill an unknown observation");
    const partial = render({ readback: { agent_reasoning_effort: "high", agent_service_tier: "priority" } });
    assert.doesNotMatch(partial.slice(partial.indexOf("<summary"), partial.indexOf("</summary>")), /high|Fast/, "Partial settings do not decorate a missing model as a complete current binding");
    assert.doesNotMatch(render({ ordinary: false }), new RegExp(locale === "zh-CN" ? "后续消息" : "Subsequent messages"), "Steward settings keep their existing owner");
    const defaultsLabel = locale === "zh-CN" ? "默认对话配置" : "Conversation defaults";
    assert.match(render({ onOpenDefaults() {} }), new RegExp(defaultsLabel), "Defaults remain discoverable inside the relevant settings disclosure");
    assert.doesNotMatch(render({ ordinary: false, onOpenDefaults() {} }), new RegExp(defaultsLabel), "Ordinary defaults are not offered for the steward binding");
    const header = (refreshState, overrides = {}) => renderToStaticMarkup(createElement(WorkspaceI18nProvider, null,
      createElement(ChannelHeader, { agents: [], selectedGoal: null, selectedAgentId: "status-only",
        selectedGoalTab: "chat", onSelectGoalTab() {}, onSelectAgent() {}, onRefresh() {}, refreshState, ...overrides })));
    const loadingText = locale === "zh-CN" ? "刷新中" : "Refreshing";
    const doneText = locale === "zh-CN" ? "刚刚更新" : "Updated just now";
    const failedText = locale === "zh-CN" ? "刷新失败" : "Refresh failed";
    assert.doesNotMatch(header("idle"), new RegExp(`${loadingText}|${doneText}|${failedText}`), "Idle refresh does not announce an event");
    assert.match(header("loading"), new RegExp(`<small>${loadingText}</small>`), "Loading remains visible");
    assert.match(header("loading"), /<button[^>]*disabled=""/, "An in-flight refresh cannot be repeated");
    assert.match(header("done"), new RegExp(`class="personal-sr-only" role="status">${doneText}</span>`), "Success is announced to assistive technology");
    assert.doesNotMatch(header("done"), new RegExp(`<small>${doneText}</small>`), "Success does not add a second visible header label");
    assert.match(header("error"), new RegExp(`<small>${failedText}</small>`), "A failure remains visibly actionable");
    assert.doesNotMatch(header("error"), /<button[^>]*disabled=""/, "A failed refresh can be retried");
    const stewardProps = {
      agents: [{ agentId: "codex", label: "Codex", available: true }], selectedAgentId: "codex",
      selectedAgentModelReadback: { agent_model_id: "goal-only-model", agent_reasoning_effort: "high", agent_service_tier: "priority" },
      managerChannelBinding: { executor_endpoint: "codex", executor_kind: "individual", model: "gpt-6.1-sol", reasoning_effort: "xhigh", selection_policy: "preferred", allocation_reason: "product_default" },
      managerRuntime: { runtime_profile: "trusted_owner", sandbox: "danger-full-access", status: "ready" },
      onOpenManagerSettings() {},
    };
    const steward = header("idle", stewardProps);
    assert.match(steward, /gpt-6\.1-sol/);
    assert.match(steward, /xhigh/);
    assert.match(steward, /本机工具|Local tools/);
    assert.doesNotMatch(steward, /goal-only-model|Fast|personal-agent-select/, "The steward has one model owner, without Goal runtime/default controls");
    const restricted = header("idle", { ...stewardProps, managerRuntime: { runtime_profile: "restricted", sandbox: "read-only", status: "ready" } });
    assert.match(restricted, /只读|Read-only/);
    const goal = header("idle", { ...stewardProps, selectedGoal: { goalId: "public-case", title: "Public Goal", agentLanes: [] } });
    assert.doesNotMatch(goal, /goal-only-model|Fast|personal-chat-model-menu/, "The Goal reading header leaves sending choices in the single composer");
    assert.doesNotMatch(goal, /gpt-6\.1-sol/, "Goal readback does not inherit the steward model");
    const linked = header("idle", { readOnlySourceLabel: "Historical run", selectedGoal: { goalId: "source-case", title: "Historical run", agentLanes: [] } });
    // The persistent badge is its trigger name and visible text. Its disclosure
    // panel is opened on demand and may explain the same source once.
    const badgeStart = linked.indexOf('class="personal-read-only-source"');
    const badgeEnd = linked.indexOf('class="personal-channel-actions"', badgeStart);
    const badge = linked.slice(badgeStart, badgeEnd > badgeStart ? badgeEnd : undefined)
      .replace(/<div [^>]*class="context-help-popover"[\s\S]*?<\/div>/, "");
    assert.match(badge, new RegExp(locale === "zh-CN" ? "只读" : "Read only"));
    assert.doesNotMatch(badge, /Historical run/, "The read-only source badge does not repeat the visible Goal title");
    const distinct = header("idle", { readOnlySourceLabel: "Remote build", selectedGoal: { goalId: "source-case", title: "Public Goal", agentLanes: [] } });
    assert.match(distinct, /class="personal-read-only-source-label"[^>]*>Remote build</, "A source the Goal title does not name stays visible in the badge");
    assertions += 33;
  }
  console.log(`Chat model/header: ${assertions} bilingual model, defaults and accessible refresh assertions passed (SSR only)`);
} finally { globalThis.window = originalWindow; }
