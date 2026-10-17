import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ChatModelMenu } from "../src/features/personal-workspace/chat-model-menu.tsx";
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
    assert.match(actual, new RegExp(locale === "zh-CN" ? "新对话配置" : "New conversation settings"));
    assert.match(actual, new RegExp(locale === "zh-CN" ? "保留原历史和活动回合" : "existing history and active work are preserved"));
    const unknown = render({});
    const unknownSummary = unknown.slice(unknown.indexOf("<summary"), unknown.indexOf("</summary>"));
    assert.match(unknownSummary, new RegExp(locale === "zh-CN" ? "模型未回传" : "Model not reported"));
    assert.doesNotMatch(unknownSummary, /next-model|low|Fast/, "Neither defaults nor a pending choice fill an unknown observation");
    assert.doesNotMatch(render({ ordinary: false }), new RegExp(locale === "zh-CN" ? "新对话配置" : "New conversation settings"), "Steward settings keep their existing owner");
    assertions += 10;
  }
  console.log(`Chat model menu: ${assertions} bilingual current-vs-requested, unknown, collapsed and steward-boundary assertions passed (SSR only)`);
} finally { globalThis.window = originalWindow; }
