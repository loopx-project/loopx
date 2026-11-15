/**
 * Opening a new loochat conversation on a Goal that already exists (the daemon's own
 * `/api/loopx/launch/join`). The conversation lands in the Goal's earlier workspace; a Goal with
 * no earlier conversation here names none, and the daemon's answer says what it needs instead.
 */

export interface JoinGoalInput {
  goalId: string;
  agent: string;
  /** The first message; when empty the thread opens with an empty composer, as a new thread does. */
  text?: string;
}

export interface JoinGoalResult {
  threadId: string;
  goalId: string;
  workspace: string;
  permission: string;
  turnId?: string | null;
}

export async function joinGoalConversation(token: string | undefined, input: JoinGoalInput, signal?: AbortSignal): Promise<JoinGoalResult> {
  const res = await fetch('/api/loopx/launch/join', {
    method: 'POST', signal,
    headers: { Authorization: `Bearer ${token ?? ''}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ goalId: input.goalId, agent: input.agent, ...(input.text ? { text: input.text } : {}) }),
  });
  const json = await res.json().catch(() => ({})) as Record<string, unknown>;
  if (!res.ok || typeof json.threadId !== 'string') {
    throw new Error(typeof json.error === 'string' && json.error.trim() ? json.error : `HTTP ${res.status}`);
  }
  return { threadId: json.threadId, goalId: String(json.goalId ?? input.goalId), workspace: String(json.workspace ?? ''), permission: String(json.permission ?? ''), turnId: typeof json.turnId === 'string' ? json.turnId : null };
}
