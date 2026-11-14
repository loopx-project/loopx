/**
 * LoopX chat sessions (+ goal titles) → Calli Thread[].
 * Pure function; every Calli-only field comes from placeholders.ts.
 *
 * PRIVACY (main-agent review): the thread cwd shows the goal's repository
 * label (e.g. "loopx-project/loopx") from /api/chat/goals/contexts — never the
 * raw local root path or any absolute path. Unknown → empty string.
 */
import { placeholder } from '../placeholders.ts';
import { UNASSIGNED_SECTION_ID } from './misc.ts';
import type { ChatSessionSummary } from '../contracts/chat-schema.ts';
import type { RunGoal } from '../contracts/status-schema.ts';
import type { Thread } from '../types.ts';

export function mapSessionsToThreads(
  sessions: ChatSessionSummary[],
  goalsById: Map<string, RunGoal>,
  repoByGoal: Map<string, string>,
): Thread[] {
  return sessions.map((s) => {
    const goal = s.goal_id ? goalsById.get(s.goal_id) : undefined;
    // Derived fallback registered in placeholders.ts ("Thread.cwd"): repository
    // label when the session's goal has one, else empty — no absolute paths.
    const cwd = (s.goal_id ? repoByGoal.get(s.goal_id) : undefined) ?? '';
    const status: Thread['status'] =
      s.active_turn_id != null ? 'running' : s.last_error_code != null ? 'error' : 'idle';
    return {
      id: s.session_id,
      agent: s.agent_id,
      cwd,
      title: goal?.display_name ?? goal?.goal_objective ?? `${s.agent_id} · ${s.goal_id ?? 'no-goal'}`,
      agentSessionId: null,
      status,
      pinned: placeholder<boolean>('Thread.unread / Thread.pinned / Thread.terminalActivity'),
      permissionMode: placeholder<string>('Thread.permissionMode'),
      model: s.agent_model_id ?? null,
      effort: s.agent_reasoning_effort ?? null,
      archivedAt: null,
      // Goal grouping (main-agent ruling): sectionId = the session's goal_id
      // WHEN that goal is in run_history; goal-less sessions AND sessions
      // whose goal exists only on the session side (e.g. "loopx-manager") land
      // in the fixed unassigned group — raw ids never become group titles.
      sectionId: s.goal_id && goalsById.has(s.goal_id) ? s.goal_id : UNASSIGNED_SECTION_ID,
      forkedFrom: null,
      unread: false,
      preview: placeholder<null>('Thread.preview'),
      terminalActivity: false,
      waiting: placeholder<boolean>('Thread.waiting'),
      queued: placeholder<never[]>('Thread.queued'),
      resumeCommand: placeholder<null>('Thread.resumeCommand'),
      cwdReal: cwd,
      createdAt: s.created_at,
      updatedAt: s.updated_at,
      runningSince: s.active_turn_id != null ? s.updated_at || null : null,
    } satisfies Thread;
  });
}
