import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { GoalTasksView } from "../src/features/personal-workspace/goal-tasks-view.tsx";
import { CompletedTaskLane } from "../src/features/personal-workspace/completed-task-lane.tsx";
import { todoItemSchema } from "../src/data/status.ts";
import { workspaceAgentTodoFromItem } from "../src/features/personal-workspace/personal-workspace-model.ts";
import { ContextDrawer } from "../src/features/personal-workspace/context-drawer.tsx";
import { WorkspaceI18nProvider, useWorkspaceI18n } from "../src/features/personal-workspace/i18n.tsx";
import { ChatApiError } from "../src/data/chat.ts";
import { todoCompletionPreviewFeedback } from "../src/features/personal-workspace/todo-completion-feedback.ts";
import { RunRow } from "../src/features/personal-workspace/cards/run-row.tsx";

// A Goal's default executor is not evidence that a Todo has been claimed.
// Exercise the real consumers, including retained history and stale labels.
const originalWindow = globalThis.window;
try {
  for (const [locale, unassigned, owner] of [["en", "Unassigned", "Owner"], ["zh-CN", "未分配", "Owner"]]) {
    globalThis.window = { localStorage: { getItem: () => locale } };
    const render = (component, props) => renderToStaticMarkup(createElement(
      WorkspaceI18nProvider, null, createElement(component, props),
    ));
    const completionRequest = {actionKind: "todo.update", normalizedParameters: {operation: "complete"}};
    function CompletionFeedback({error, request}) {
      return todoCompletionPreviewFeedback(error, request, useWorkspaceI18n().t);
    }
    for (const code of ["handoff_mode_requires_lease", "lease_fence_required"]) {
      const error = new ChatApiError("Canonical refusal", {error_code: code, http_status: 400});
      const feedback = render(CompletionFeedback, {error, request: completionRequest});
      assert.ok(feedback.includes(locale === "en" ? "This page cannot complete" : "此页面无法代为完成"));
      assert.ok(feedback.includes(locale === "en" ? "has not been marked complete" : "尚未标记完成"));
      assert.equal(error.payload.error_code, code, "Presentation preserves the canonical diagnostic");
      for (const request of [
        {...completionRequest, actionKind: "gate.resolve"},
        {...completionRequest, normalizedParameters: {operation: "edit"}},
      ]) assert.equal(render(CompletionFeedback, {error, request}), "", "Unrelated actions retain their original errors");
    }
    for (const error of [new Error("lease_fence_required"),
      new ChatApiError("lease_fence_required", {error_code: "provider_unavailable"}),
      new ChatApiError("Foreign execution", {error_code: "lease_cas_mismatch"})]) {
      assert.equal(render(CompletionFeedback, {error, request: completionRequest}), "",
        "Neither error prose nor other refusals establish this recovery route");
    }
    const notesLabel = locale === "en" ? "Notes" : "备注";
    const evidenceLabel = locale === "en" ? "Evidence" : "证据";
    for (const invalid of [null, "dependency_cycle"]) {
      const source = todoItemSchema.parse({todo_id: "todo_cycle", text: "Review dependent work", done: false,
        status: "open", resume_when: "todo_done:todo_peer", resume_ready: false,
        resume_condition: {satisfied: false, ...(invalid ? {invalid_state: invalid} : {})}});
      const todo = workspaceAgentTodoFromItem(source, "fallback-id");
      const drawer = render(ContextDrawer, {agents: [], callbacks: {}, onClose() {}, selection: {
        kind: "todo", item: {...todo, goalId: "test-goal", goalTitle: "Test Goal"},
      }});
      const cycleLabel = locale === "en" ? "Circular dependency" : "循环依赖";
      const pendingLabel = locale === "en" ? "Waiting for condition" : "等待条件满足";
      assert.equal(drawer.includes(cycleLabel), Boolean(invalid));
      assert.equal(drawer.includes(pendingLabel), !invalid);
    }
    const longNote = "Preserve separate notes. ".repeat(800) + "NOTE_END_SENTINEL";
    for (const fields of [{}, {note: null, evidence: null}, {note: "", evidence: ""},
      {note: longNote}, {evidence: "RECORDED_EVIDENCE"}, {note: longNote, evidence: "RECORDED_EVIDENCE"}]) {
      for (const done of [false, true]) {
        const source = todoItemSchema.parse({todo_id: "metadata-todo", text: "Read source metadata", done,
          status: done ? "done" : "open", ...fields});
        const todo = workspaceAgentTodoFromItem(source, "fallback-id");
        assert.equal(todo.note, fields.note ?? null, "Notes remain an independent source field");
        assert.equal(todo.evidence, fields.evidence ?? null, "An absent evidence field cannot be inferred from notes");
        const drawer = render(ContextDrawer, {agents: [], callbacks: {}, onClose() {}, selection: {
          kind: "todo", item: {...todo, goalId: "test-goal", goalTitle: "Test Goal"},
        }});
        assert.equal(drawer.includes(`<summary>${notesLabel}</summary>`), Boolean(fields.note));
        assert.equal(drawer.includes(`aria-label="${evidenceLabel}"`), Boolean(fields.evidence));
        assert.equal(drawer.includes("NOTE_END_SENTINEL"), Boolean(fields.note), "Both active and completed inspectors preserve the end of long notes");
        assert.equal(drawer.includes("RECORDED_EVIDENCE"), Boolean(fields.evidence));
        assert.equal(source.note, fields.note, "Reading never mutates canonical notes");
        assert.equal(source.evidence, fields.evidence, "Reading never mutates canonical evidence");
      }
    }
    for (const claim of [undefined, null, "actual-worker"]) {
      const todo = {todoId: "test-todo", text: "Read claim identity", done: false, status: "open", claimedBy: claim};
      const goal = {goalId: "test-goal", title: "Test Goal", agentId: "goal-default", agentLabel: "Goal default worker", agentTodos: [todo], state: "已安排", agentSentence: "", nextSentence: ""};
      const expected = claim ?? unassigned;
      const active = render(GoalTasksView, {goal, items: [], userTodos: [], onSelect() {}});
      assert.ok(active.includes(expected), "Active cards must identify only the recorded claim");
      assert.ok(!active.includes("Goal default worker"), "Goal defaults cannot substitute for a task claim");
      const completed = render(CompletedTaskLane, {goal, agentId: "all", seed: [{...todo, done: true, status: "done"}], enabled: false, onSelect() {}});
      assert.ok(completed.includes(expected), "Completed history preserves the same claim semantics");
      assert.ok(!completed.includes("Goal default worker"));
      const drawer = render(ContextDrawer, {agents: [], callbacks: {}, onClose() {}, selection: {
        kind: "todo", item: {...todo, goalId: goal.goalId, goalTitle: goal.title,
          ownerLabel: claim ? claim : "Goal default worker"},
      }});
      assert.ok(drawer.includes(`<dt>${owner}</dt><dd>${expected}</dd>`), "A stale display label cannot invent an absent claim");
      assert.equal(todo.claimedBy, claim, "Rendering never mutates the source claim");
    }
    const todo = {todoId: "run-task", text: "Inspect execution", done: false, status: "open"};
    const goal = {goalId: "run-goal", title: "Execution facts", agentTodos: [todo]};
    for (const [status, en, zh] of [
      ["queued", "Scheduled", "已安排"], ["running", "Running", "执行中"],
      ["waiting", "Waiting", "等待条件"], ["failed", "Needs review", "需检查"],
      ["interrupted", "Interrupted", "已中断"], ["completed", "Completed", "已完成"],
    ]) {
      const run = {todoId: todo.todoId, goalId: goal.goalId, status, title: todo.text, agentLabel: "Worker"};
      const board = render(GoalTasksView, {goal, items: [{kind: "run", id: "execution", run}], userTodos: [], onSelect() {}});
      const timeline = render(RunRow, {run, onSelect() {}});
      const expected = locale === "en" ? en : zh;
      assert.ok(board.includes(`personal-task-session-status">${expected}</span>`), `Task cards preserve ${status}`);
      assert.ok(timeline.includes(expected), `Timeline and task cards agree for ${status}`);
    }
  }
} finally {
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
}
console.log("todo-ownership-smoke: ok");
