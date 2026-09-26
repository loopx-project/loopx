import { parseTodoResumeCondition } from "../src/features/personal-workspace/todo-resume-condition.js";

for (const condition of ["todo_done:todo_release", "pr_merged:example/project#42", "capacity_available:worker", "resume_at:2026-10-01T09:00:00Z"]) {
  if (parseTodoResumeCondition(condition) !== condition.toLowerCase()) throw new Error(`Supported resume condition rejected: ${condition}`);
}
for (const value of ["tomorrow", "owner_resume", "pr_merged:#0", "todo_done:", "resume_at:2026-10-01"]) {
  if (parseTodoResumeCondition(value) !== null) throw new Error(`Unsupported resume condition admitted: ${value}`);
}
console.log("todo-resume-condition-smoke: ok");
