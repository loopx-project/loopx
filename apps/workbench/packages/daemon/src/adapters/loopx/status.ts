import { execFile } from 'node:child_process';

/** What the Goal bar and the per-turn context need from `loopx status`, never more than the core said. */
export interface GoalState {
  goalId: string;
  title: string;
  status: string;
  summary?: string;
  waitingOn?: string;
  activation?: string;
  agentTodos: TodoGroup;
  userTodos: TodoGroup;
  updatedAt?: string;
}
export interface TodoGroup {
  total: number;
  open: number;
  items: { id: string; text: string; status: string }[];
  /** False when the core listed fewer items than it counted. */
  complete: boolean;
}

export interface LoopxCli {
  /** argv prefix, e.g. ['loopx'] or ['python3', '-m', 'loopx.cli'] */
  command: string[];
  cwd?: string;
  registry?: string;
}

export function cliFromEnv(env: NodeJS.ProcessEnv = process.env): LoopxCli {
  const command = (env.LOOCHAT_LOOPX_CMD || 'loopx').split(/\s+/).filter(Boolean);
  return { command, ...(env.LOOCHAT_LOOPX_CWD ? { cwd: env.LOOCHAT_LOOPX_CWD } : {}), ...(env.LOOCHAT_LOOPX_REGISTRY ? { registry: env.LOOCHAT_LOOPX_REGISTRY } : {}) };
}

function group(todos: any): TodoGroup {
  const items = Array.isArray(todos?.items) ? todos.items.flatMap((t: any) =>
    typeof t?.todo_id === 'string' && typeof t?.text === 'string' ? [{ id: t.todo_id, text: t.text, status: typeof t.status === 'string' ? t.status : 'unknown' }] : []) : [];
  const total = Number.isInteger(todos?.total_count) ? todos.total_count : items.length;
  const open = Number.isInteger(todos?.open_count) ? todos.open_count : items.filter((t: { status: string }) => t.status === 'open').length;
  return { total, open, items, complete: items.length === total };
}

/** Projects the `loopx status --goal-id` payload. A missing or foreign goal is an error, not a guess. */
export function projectGoalState(payload: any, goalId: string): GoalState {
  if (payload?.goal_filter !== goalId || !Array.isArray(payload?.attention_queue?.items)) throw new Error('invalid_status_scope');
  const row = payload.attention_queue.items.find((i: any) => i?.goal_id === goalId && i?.status !== 'status_collection_failed');
  if (!row) throw new Error('goal_not_found');
  const channel = row.goal_channel_projection;
  return {
    goalId,
    title: channel?.display_name || goalId,
    status: typeof row.status === 'string' ? row.status : 'unknown',
    ...(typeof row.recommended_action === 'string' ? { summary: row.recommended_action } : {}),
    ...(typeof row.waiting_on === 'string' ? { waitingOn: row.waiting_on } : {}),
    ...(typeof row.activation_state === 'string' ? { activation: row.activation_state } : {}),
    agentTodos: group(row.agent_todos),
    userTodos: group(row.user_todos),
    ...(channel?.source_refs?.active_state_updated_at ? { updatedAt: channel.source_refs.active_state_updated_at } : {}),
  };
}

export function readGoalState(goalId: string, cli: LoopxCli = cliFromEnv(), timeoutMs = 20000): Promise<GoalState> {
  const [exe, ...prefix] = cli.command;
  const args = [...prefix, ...(cli.registry ? ['--registry', cli.registry] : []), '--format', 'json', 'status', '--goal-id', goalId, '--limit', '1'];
  return new Promise((resolve, reject) => {
    execFile(exe, args, { cwd: cli.cwd, timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024, env: { ...process.env, LOOPX_USAGE_PING: '0' } }, (err, stdout) => {
      // status may exit nonzero for unrelated contract warnings; only a valid scoped payload counts.
      try { resolve(projectGoalState(JSON.parse(stdout), goalId)); }
      catch (parse) { reject(err && !stdout ? new Error(`loopx_unavailable: ${err.message}`) : parse); }
    });
  });
}

/** One read at a time per goal, and a short memory: the bar and each turn ask in quick succession. */
export class GoalStateCache {
  private readonly entries = new Map<string, { at: number; value?: GoalState; pending?: Promise<GoalState> }>();
  constructor(private readonly read: (goalId: string) => Promise<GoalState>, private readonly ttlMs = 10000, private readonly now = Date.now) {}

  get(goalId: string, fresh = false): Promise<GoalState> {
    const entry = this.entries.get(goalId);
    if (entry?.pending) return entry.pending;
    if (!fresh && entry?.value && this.now() - entry.at < this.ttlMs) return Promise.resolve(entry.value);
    const pending = this.read(goalId).then((value) => { this.entries.set(goalId, { at: this.now(), value }); return value; },
      (err) => { this.entries.delete(goalId); throw err; });
    this.entries.set(goalId, { at: entry?.at ?? 0, value: entry?.value, pending });
    return pending;
  }
}
