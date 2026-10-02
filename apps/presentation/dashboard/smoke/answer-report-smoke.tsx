import assert from "node:assert/strict";
import {renderToStaticMarkup} from "react-dom/server";
import type {ChatVisibleMessage} from "../src/data/chat";
import {answerReportNavigation, AnswerReportSummary} from "../src/features/personal-workspace/answer-report-page";
import {WorkspaceI18nProvider} from "../src/features/personal-workspace/i18n";

const current = "http://127.0.0.1/chat/?goalId=wrong&view=overview&reportSessionId=old&reportMessageId=answer";
for (const zh of [false, true]) {
  for (const [channel_id, goal, context, label] of [
    ["manager", "", zh ? "管家" : "Steward", zh ? "返回管家" : "Back to Steward"],
    ["manager.external.synthetic", "", zh ? "管家 · 外部对话" : "Steward · External conversation", zh ? "打开管家" : "Open Steward"],
    ["goal.community", "community", "community", zh ? "返回对话" : "Back to conversation"],
    ["goal.other", "", zh ? "已保存的对话" : "Saved conversation", zh ? "打开工作区" : "Open workspace"],
  ]) {
    const result = answerReportNavigation({goal_id: "community", channel_id}, current, "/selected/status.json", zh);
    assert.equal(result.context, context);
    assert.equal(result.label, label);
    const url = new URL(result.url);
    assert.equal(url.searchParams.get("goalId"), goal, "A storage Goal is not conversation scope");
    assert.equal(url.searchParams.get("view"), "conversation");
    assert.equal(url.searchParams.get("statusUrl"), "/selected/status.json");
    assert.equal(url.searchParams.has("reportSessionId"), false);
    assert.equal(url.searchParams.has("reportMessageId"), false);
  }
  for (const session of [null, {goal_id: "community"}, {goal_id: "loopx-manager", channel_id: "goal.loopx-manager"}]) {
    assert.equal(new URL(answerReportNavigation(session, current, "", zh).url).searchParams.get("goalId"), "");
  }
  // Text saying "completed" cannot upgrade an interim phase or a delivery fact.
  for (const [phase, expected] of [
    ["decision", zh ? "进度更新" : "Progress update"],
    ["conclusion", zh ? "处理结论" : "Conclusion"],
    [undefined, zh ? "完整答复" : "Full answer"],
  ] as const) {
    const message: ChatVisibleMessage = {message_id: "answer", turn_id: "turn", role: "agent", text: "Completed!", created_at: "2026-01-01T00:00:00Z"};
    if (phase) message.return_delivery = {schema_version: "manager_return_delivery_status_v0", phase, status: "delivered"};
    const html = renderToStaticMarkup(<WorkspaceI18nProvider><AnswerReportSummary message={message} zh={zh}/></WorkspaceI18nProvider>);
    assert.ok(html.includes(`<h1>${expected}</h1>`));
    assert.equal(html.includes('role="status"'), Boolean(phase));
  }
}
for (const [status, verification, expected] of [
  ["verification_required", undefined, "Verifying delivery without resending"],
  ["explicit_unverified", undefined, "Delivery remains explicitly unverified"],
  ["retry_pending", undefined, "Return queued"],
  ["delivered", "reconciled_after_restart", "Delivery verified after recovery"],
] as const) {
  const message: ChatVisibleMessage = {message_id: "answer", turn_id: null, role: "agent", text: "done", created_at: "", return_delivery: {schema_version: "manager_return_delivery_status_v0", phase: "decision", status, verification}};
  const html = renderToStaticMarkup(<WorkspaceI18nProvider><AnswerReportSummary message={message} zh={false}/></WorkspaceI18nProvider>);
  assert.ok(html.includes(expected));
  assert.ok(html.includes("Progress update"));
}
console.log("answer-report: scope isolation, actual phase, delivery uncertainty and legacy answers passed");
