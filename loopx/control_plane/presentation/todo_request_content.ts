type RecordValue = Record<string, unknown>;
export type TodoRequestContent = {text: string; note: string | null; evidence: string | null};
const text = (value: unknown): string | null => typeof value === "string" && value.trim() ? value.trim() : null;

/** Read-only display join inside one Goal snapshot. Never copy authority,
 * membership or lifecycle from the richer row into the selected row. */
export function todoRequestContent(
  selected: RecordValue, snapshot: {goal_id: string; items: RecordValue[]}, goalId: string,
): TodoRequestContent | null {
  const id = text(selected.todo_id);
  if (!id || !goalId || snapshot.goal_id !== goalId
      || (selected.goal_id != null && selected.goal_id !== goalId)) return null;
  const matches = snapshot.items.filter(item => item.todo_id === id);
  if (matches.length !== 1) return null;
  const source = matches[0];
  if ((source.goal_id != null && source.goal_id !== goalId)
      || source.role !== "user" || (selected.role != null && selected.role !== "user")
      || !text(selected.updated_at) || selected.updated_at !== source.updated_at
      || !text(selected.status) || selected.status !== source.status
      || !text(selected.task_class) || selected.task_class !== source.task_class
      || (typeof selected.done === "boolean" && selected.done !== source.done)
      || text(selected.superseded_by) !== text(source.superseded_by)
      || source.content_redacted === true) return null;
  const body = text(source.text);
  const label = text(selected.text);
  // Existing summaries end in a known truncation marker. Unrelated text is
  // not a richer version of the selected request even at the same timestamp.
  if (!body || !label || (body !== label
      && !(label.endsWith("...") && body.startsWith(label.slice(0, -3).trimEnd()))
      && !(label.endsWith("…") && body.startsWith(label.slice(0, -1).trimEnd())))) return null;
  return {text: body, note: text(source.note), evidence: text(source.evidence)};
}
