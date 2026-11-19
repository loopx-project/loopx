/** One shared codec for the report capability's canonical User action binding. */
const bindingPattern = /^periodic-report-review:r([a-f0-9]{64})$/;
export function reportReviewDigest(todo: Record<string, unknown>): string | null {
  if (todo.role !== "user" || todo.task_class !== "user_action"
      || todo.action_kind !== "review_periodic_report") return null;
  return typeof todo.capability_binding_ref === "string"
    ? bindingPattern.exec(todo.capability_binding_ref)?.[1] ?? null : null;
}
