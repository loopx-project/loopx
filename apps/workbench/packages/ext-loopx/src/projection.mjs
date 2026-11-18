import labels from './labels.json' with { type: 'json' };
/** LoopX 1.4.2 status projection. Missing source fields are never guessed. */
export function projectStatus(payload, goalId) {
  if (payload?.goal_filter !== goalId || !Array.isArray(payload?.attention_queue?.items)) throw new Error('invalid_status_scope');
  const row = payload.attention_queue.items.find(i => i.goal_id === goalId && i.status !== 'status_collection_failed');
  if (!row) throw new Error('goal_not_found');
  const items = [];
  let complete = true;
  for (const role of ['user', 'agent']) {
    const todos = row[`${role}_todos`];
    if (!todos || !Array.isArray(todos.items) || !Number.isInteger(todos.total_count)) { complete = false; continue; }
    let count = 0;
    for (const todo of todos.items) {
      if (typeof todo.todo_id !== 'string' || typeof todo.text !== 'string') { complete = false; continue; }
      items.push({ id: `${role}:${todo.todo_id}`, text: todo.text, status: typeof todo.status === 'string' ? todo.status : 'unknown' });
      count++;
    }
    if (count !== todos.total_count) complete = false;
  }
  const channel = row.goal_channel_projection;
  return {
    title: channel?.display_name || goalId,
    status: typeof row.status === 'string' ? row.status : 'unknown',
    statusLabel: labels[row.status],
    summary: typeof row.recommended_action === 'string' ? row.recommended_action : undefined,
    updatedAt: channel?.source_refs?.active_state_updated_at ?? undefined,
    items, complete,
  };
}
