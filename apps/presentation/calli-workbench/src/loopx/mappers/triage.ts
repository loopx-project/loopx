/**
 * Triage: needs you / running / error (+ quiet remainder) over LoopX projections.
 *
 * Rules tightened per the main-agent review (no inference from severity,
 * adapter_status or watch kinds — only signals the projection states plainly):
 *  - error:    contract errors, sessions with last_error_code, and BLOCKED
 *              todos (`status === "blocked"` verbatim from the todo index /
 *              attention todo groups). Blocked count is attached to the goal
 *              entry's evidence whichever bucket it lands in, so it is never
 *              lost to the partition.
 *  - needsYou: an attention item waiting on a HUMAN side (user/operator/...;
 *              "controller" is a control agent, NOT a person), a non-null
 *              operator question, or decision_frame.user_action_required.
 *  - running:  a session with an active turn. Waiting on external evidence or
 *              a monitor is NOT running — it lands in quiet.
 *  - quiet:    explicit remainder, so nothing is silently dropped; the UI may
 *              render it muted or fold it away.
 *
 * One goal may hold several attention items (the offline fixture ships two for
 * loopx-meta); they merge, and any user-waiting item wins. Every goal from
 * run_history lands in exactly one bucket (partition); sessions are separate
 * entries of kind 'session'.
 */
import type { ChatSessionSummary } from '../contracts/chat-schema.ts';
import { type QueueItem, type RunGoal, type StatusPayload } from '../contracts/status-schema.ts';
import type { TriageBucket, TriageEntry, TriageView } from '../types.ts';

/** Human sides only. "controller" is a control agent, not a person (main-agent ruling). */
const HUMAN_WAITING_ON = new Set(['user', 'user_or_controller', 'human', 'operator', 'owner']);

function entry(id: string, title: string, kind: TriageEntry['kind'], bucket: TriageBucket, evidence: string[], recommendedAction?: string | null): TriageEntry {
  return { id, title, kind, bucket, evidence, ...(recommendedAction ? { recommendedAction } : {}) };
}

interface BlockedRow { id: string; title: string; goalId: string }

/**
 * Blocked todos, verbatim (`status === "blocked"`). The todo_index and the
 * attention todo groups project the SAME todos; prefer the index when it has
 * rows (cross-goal, carries goal_id), else fall back to the groups.
 */
function blockedRows(status: StatusPayload): BlockedRow[] {
  const index = status.todo_index?.items ?? [];
  if (index.length) {
    return index
      .filter((row) => row.status === 'blocked')
      .map((row) => ({ id: row.todo_id ?? `index-${row.index ?? '?'}`, title: row.title ?? row.text, goalId: row.goal_id ?? '' }));
  }
  const out: BlockedRow[] = [];
  for (const item of status.attention_queue.items ?? []) {
    for (const row of [...(item.user_todos?.items ?? []), ...(item.agent_todos?.items ?? [])]) {
      if (row.status !== 'blocked') continue;
      out.push({ id: row.todo_id ?? row.title ?? 'blocked-todo', title: row.title ?? row.text ?? '', goalId: item.goal_id });
    }
  }
  return out;
}

function blockedTodosOf(status: StatusPayload, goalId: string): number {
  return blockedRows(status).filter((row) => row.goalId === goalId).length;
}

function bucketForGoal(goal: RunGoal, items: QueueItem[]): { bucket: TriageBucket; evidence: string[]; userAction: string | null } {
  const evidence: string[] = [];
  let needsUser = false;
  let userAction: string | null = null;
  for (const item of items) {
    let thisItemWantsUser = false;
    if (item.operator_question != null) { evidence.push(`operator_question=${item.operator_question}`); thisItemWantsUser = true; }
    const frame = item.goal_channel_projection?.decision_frame;
    if (frame?.user_action_required) { evidence.push('decision_frame.user_action_required=true'); thisItemWantsUser = true; }
    if (item.waiting_on && HUMAN_WAITING_ON.has(item.waiting_on)) { evidence.push(`waiting_on=${item.waiting_on}`); thisItemWantsUser = true; }
    if (thisItemWantsUser) {
      needsUser = true;
      userAction ??= item.recommended_action ?? null;
    } else if (item.waiting_on) {
      // Agent-side waits (including controller/monitor/external evidence) are quiet, not running.
      evidence.push(`waiting_on=${item.waiting_on}`);
    }
  }
  if (needsUser) return { bucket: 'needsYou', evidence, userAction };
  return { bucket: 'quiet', evidence, userAction: null };
}

export function deriveTriage(status: StatusPayload, sessions: ChatSessionSummary[]): TriageView {
  // One goal may hold several attention items — keep them all (Map<goalId, items[]>).
  const itemsByGoal = new Map<string, QueueItem[]>();
  for (const item of status.attention_queue.items ?? []) {
    const list = itemsByGoal.get(item.goal_id) ?? [];
    list.push(item);
    itemsByGoal.set(item.goal_id, list);
  }

  const goals = status.run_history?.goals ?? [];
  const needsYou: TriageEntry[] = [];
  const running: TriageEntry[] = [];
  const error: TriageEntry[] = [];
  const quiet: TriageEntry[] = [];

  for (const goal of goals) {
    const items = itemsByGoal.get(goal.id) ?? [];
    itemsByGoal.delete(goal.id);
    const { bucket, evidence, userAction } = bucketForGoal(goal, items);
    const blocked = blockedTodosOf(status, goal.id);
    if (blocked > 0) evidence.push(`blocked_todos=${blocked}`);
    const title = goal.display_name ?? goal.goal_objective ?? goal.id;
    const recommended = userAction ?? items.map((i) => i.recommended_action).find(Boolean) ?? goal.latest_runs[0]?.recommended_action ?? null;
    const e = entry(goal.id, title, 'goal', bucket, evidence, recommended);
    (bucket === 'needsYou' ? needsYou : bucket === 'running' ? running : bucket === 'error' ? error : quiet).push(e);
  }
  // Attention items whose goal is absent from run_history (index mismatch): keep, as quiet.
  for (const [goalId, items] of itemsByGoal) {
    quiet.push(entry(goalId, goalId, 'goal', 'quiet', items.flatMap((item) => [`attention_only=${item.status}`, ...(item.waiting_on ? [`waiting_on=${item.waiting_on}`] : [])]), items.map((i) => i.recommended_action).find(Boolean)));
  }

  for (const s of sessions) {
    if (s.active_turn_id != null) {
      running.push(entry(s.session_id, `${s.agent_id} turn ${s.active_turn_id}`, 'session', 'running', [`active_turn_id=${s.active_turn_id}`, `agent=${s.agent_id}`]));
    } else if (s.last_error_code != null) {
      error.push(entry(s.session_id, `${s.agent_id} session`, 'session', 'error', [`last_error_code=${s.last_error_code}`, `agent=${s.agent_id}`]));
    }
  }

  // Blocked todos are their own error entries (C's offline promise: "Failed =
  // the fixture's blocked Todos + one session with an error"), so a goal that
  // is also waiting on the user can still show its blocked work here. This is
  // the verbatim status="blocked" signal, not a severity/adapter_status guess.
  for (const row of blockedRows(status)) {
    error.push(entry(`todo:${row.id}`, row.title, 'todo', 'error', ['status=blocked', ...(row.goalId ? [`goal_id=${row.goalId}`] : [])]));
  }

  if ((status.contract_errors_total_count ?? 0) > 0 || (status.contract?.errors?.length ?? 0) > 0) {
    error.push(entry('loopx:contract', 'LoopX status contract', 'registry', 'error', [
      `contract_errors_total_count=${status.contract_errors_total_count}`,
      ...(status.contract?.errors ?? []).slice(0, 3),
    ]));
  }
  // NOTE: no other error inference. Registry findings and attention severity
  // are NOT treated as errors (main-agent review ruling); only contract
  // errors, session last_error_code and blocked todos are.

  return { needsYou, running, error, quiet, generatedAt: new Date().toISOString() };
}
