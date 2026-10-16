import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {useGoalWorkspaceDigest} from "../src/features/personal-workspace/goal-masthead.tsx";
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
    // Canonical deferred records may retain done=true; legacy completed records
    // may omit status. The real adapter and inspector must distinguish them.
    for (const [fields, completed, resumable] of [
      [{done: true}, true, false],
      [{done: true, status: "done"}, true, false],
      [{done: true, status: "deferred"}, false, true],
      [{done: false, status: "blocked"}, false, true],
    ]) {
      const source = todoItemSchema.parse({todo_id: "retained-task", text: "Read retained work", ...fields});
      const todo = workspaceAgentTodoFromItem(source, "fallback-id");
      const drawer = render(ContextDrawer, {agents: [], callbacks: {}, onClose() {}, selection: {
        kind: "todo", item: {...todo, goalId: "test-goal", goalTitle: "Test Goal"},
      }});
      assert.equal(drawer.includes("personal-task-completed-note"), completed, "Completed history remains read-only");
      assert.equal(drawer.includes("personal-task-inspector-actions"), !completed, "Completed records never expose new mutations");
      assert.equal(drawer.includes(locale === "en" ? "Resume task" : "恢复任务"), resumable,
        "Only explicitly paused work offers resume");
      assert.equal(source.done, fields.done, "Presentation never rewrites the recorded lifecycle");
    }
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
    for (const detailMode of ["request_only", "missing_current"]) {
      const drawer = render(ContextDrawer, {agents:[], callbacks:{}, onClose(){}, selection:{kind:"todo",item:{todoId:"missing",sourceTodoId:"missing",goalId:"test-goal",goalTitle:"Test Goal",text:"Past request",done:false,detailMode}}});
      assert.ok(!drawer.includes("personal-task-inspector-actions"), "Unavailable inspection cannot retain effect controls");
      assert.ok(drawer.includes(detailMode === "missing_current"
        ? (locale === "en" ? "Current task state is unavailable." : "当前任务状态不可用。")
        : (locale === "en" ? "outside the current summary" : "不在当前摘要中")));
    }
    // Exact typed relations, rather than graph adjacency, classify held work.
    function DigestProbe({map}) {
      const digest = useGoalWorkspaceDigest({goalId:"relations",agentTodos:[
        {todoId:"held",text:"Held work",done:false,status:"blocked",claimedBy:"worker"},
        {todoId:"lineage",text:"Historical lineage",done:false,status:"open"},
        {todoId:"monitor-wait",text:"Monitor condition",done:false,status:"open"},
      ]}, [{todoId:"decision",goalId:"relations",blocking:true,text:"Owner decision"}], [], map);
      return JSON.stringify(digest.work.rows.map(row=>[row.todoId,row.displayClass]));
    }
    const node = (id,kind,state="open") => ({node_id:id,kind,state,depth:0,refs:{todo_ids:[id]}});
    const map = {nodes:[node("decision","gate"),node("held","deliverable"),node("lineage","deliverable"),node("watch","monitor"),node("monitor-wait","deliverable")],
      edges:[{from_node_id:"held",to_node_id:"decision",relation:"depends_on",enforcement:"typed_lifecycle"},
        {from_node_id:"lineage",to_node_id:"held",relation:"continues",enforcement:"lineage_only"},
        {from_node_id:"monitor-wait",to_node_id:"watch",relation:"depends_on",enforcement:"typed_condition"}]};
    const relations = render(DigestProbe,{map});
    assert.ok(relations.includes("needs_you"),"The actual adapter adopts the recorded decision dependency");
    assert.ok(relations.includes('[&quot;lineage&quot;,&quot;open&quot;]'),"Lineage is not a completion prerequisite");
    assert.ok(relations.includes('[&quot;monitor-wait&quot;,&quot;open&quot;]'),"Monitor generation change does not imply completion waiting");
    const completedGate = render(DigestProbe,{map:{...map,nodes:map.nodes.map(node=>node.node_id==="decision"?{...node,state:"done"}:node)}});
    assert.ok(!completedGate.includes("needs_you"),"A done map gate cannot keep a task held by that decision");
    const todo = {todoId: "run-task", text: "Inspect execution", done: false, status: "open"};
    const goal = {goalId: "run-goal", title: "Execution facts", agentTodos: [todo],
      execution: {kind: "running", work: "executing", unrecognizedModeTurns: 0, quiet: false, lastActivityAt: new Date().toISOString(), hostSurfaces: [], claimedAt: null}};
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
      if (status === "running") for (const execution of [{kind: "unknown"}, {...goal.execution, work: "unrecognized", unrecognizedModeTurns: 1}, {...goal.execution, quiet: true}, {...goal.execution, work: "claimed"}]) {
        const unknown = render(GoalTasksView, {goal: {...goal, execution}, items: [{kind: "run", id: "execution", run}], userTodos: [], onSelect() {}});
        assert.ok(!unknown.includes(`personal-task-session-status">${expected}</span>`), "A historical run cannot make unknown or stale work live");
      }
    }
  }
} finally {
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
}
console.log("todo-ownership-smoke: ok");
