import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { GoalTasksView } from "../src/features/personal-workspace/goal-tasks-view.tsx";
import { CompletedTaskLane } from "../src/features/personal-workspace/completed-task-lane.tsx";
import { ContextDrawer } from "../src/features/personal-workspace/context-drawer.tsx";
import { WorkspaceI18nProvider } from "../src/features/personal-workspace/i18n.tsx";

// A Goal's default executor is not evidence that a Todo has been claimed.
// Exercise the real consumers, including retained history and stale labels.
const originalWindow = globalThis.window;
try {
  for (const [locale, unassigned, owner] of [["en", "Unassigned", "Owner"], ["zh-CN", "未分配", "Owner"]]) {
    globalThis.window = { localStorage: { getItem: () => locale } };
    const render = (component, props) => renderToStaticMarkup(createElement(
      WorkspaceI18nProvider, null, createElement(component, props),
    ));
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
  }
} finally {
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
}
console.log("todo-ownership-smoke: ok");
