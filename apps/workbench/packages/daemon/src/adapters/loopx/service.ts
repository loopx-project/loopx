import { randomUUID } from 'node:crypto';
import { realpathSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { HttpError } from '../../errors.js';
import type { AgentAdapter, ApprovalRequest } from '../types.js';
import { isWithinWorkdirs, type Workdirs } from '../../workdirs.js';
import type { ThreadManager } from '../../threads.js';
import { corePermission, fromCorePermission, goalAgentFor, goalThreadMode, LOOPX_PERMISSIONS, workspaceSandboxed, LoopxBindings, permissionAvailable, type GoalAdapter, type LoopxBinding, type LoopxPermission } from './binding.js';
import { goalContextBlock } from './context.js';
import { applyProposal, CoreError, coreJson, goalLocale, grantedWorkspaces, loadProposal, previewGoalCreate, registeredGoalRoot, workspaceRef, type GoalDraft } from './core.js';
import { coreUrl } from './proxy.js';
import { cliFromEnv, GoalStateCache, GoalStatusUnavailable, readGoalState, type GoalState, type LoopxCli } from './status.js';
import type { LoopxLookup, SessionLoopx } from '../../session-enrich.js';

export interface LoopxDeps {
  dataDir: string;
  adapters: Map<string, AgentAdapter>;
  dirs: Workdirs;
  cli?: LoopxCli;
  core?: URL;
}

/** What the thread manager asks of this adapter. It has no other knowledge of Goals. */
export interface LoopxHooks {
  binding(threadId: string): LoopxBinding | null;
  /** The block that rides with a Goal conversation's message; empty for an ordinary one. */
  context(threadId: string): Promise<string>;
  /** What marks this thread as a Goal conversation in a list: its binding, or what the scan read from its session. */
  info(threadId: string, scanned?: SessionLoopx): SessionLoopx | null;
  /** A Goal conversation never asks the person: its boundary is the sandbox, not a prompt. */
  refuse(threadId: string, request: ApprovalRequest): { optionId: string } | { cancelled: true } | null;
}

/** createThread's permission field for a Goal conversation; an agent with no permission levels gets none. */
const threadMode = (adapter: GoalAdapter, permission: LoopxPermission) => {
  const mode = goalThreadMode(adapter, permission);
  return mode ? { permissionMode: mode } : {};
};

const asText = (v: unknown, field: string, max = 4000): string => {
  if (typeof v !== 'string' || !v.trim()) throw new HttpError(400, `${field} is required`);
  if (v.length > max) throw new HttpError(400, `${field} is too long`);
  return v.trim();
};

async function body(req: IncomingMessage): Promise<any> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  if (!chunks.length) return {};
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { throw new HttpError(400, 'invalid JSON body'); }
}

function send(res: ServerResponse, status: number, value: unknown): void {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(value));
}

/** What only an agent that was asked can produce. Session start-up notices are not among them. */
const AGENT_OUTPUT = new Set(['message.delta', 'message.replace', 'reasoning.delta', 'tool.start', 'tool.update', 'tool.end',
  'plan.updated', 'plan.delta', 'plan.completed', 'input.request', 'approval.request']);

export class LoopxService implements LoopxHooks {
  readonly bindings: LoopxBindings;
  private readonly states: GoalStateCache;
  private manager: ThreadManager | null = null;
  private readonly core: URL;
  private readonly cli: LoopxCli;
  /**
   * What each preview was asked for, by proposal. Confirming a preview dispatches exactly this, never what
   * the confirmation request says. Held in memory: after a restart the person previews again.
   */
  private readonly launching = new Map<string, Promise<unknown>>();
  private readonly previews = new Map<string, { agent: string; goalAgentId: string; permission: LoopxPermission; path: string; ref: string }>();

  constructor(private readonly deps: LoopxDeps) {
    this.bindings = new LoopxBindings(join(deps.dataDir, 'loopx-threads.json'));
    const cli = this.cli = deps.cli ?? cliFromEnv();
    this.states = new GoalStateCache((goalId) => readGoalState(goalId, cli));
    this.core = deps.core ?? coreUrl();
  }

  attach(manager: ThreadManager): void { this.manager = manager; }

  binding(threadId: string): LoopxBinding | null { return this.bindings.get(threadId); }

  info(threadId: string, scanned?: SessionLoopx): SessionLoopx | null {
    const b = this.bindings.get(threadId);
    if (!b) return scanned ?? null;
    return {
      goalId: b.goalId, goalTitle: b.goalTitle || b.goalId, ...(b.goalMissing ? { missing: true } : {}),
      basis: b.basis ?? 'workbench', source: b.source ?? (b.proposalId ? 'launch' : 'join'),
    };
  }

  /** The core's side of a session scan: its external-session bindings, and whether a Goal still exists. */
  lookup(): LoopxLookup {
    return {
      bindings: async () => {
        const res = await coreJson<{ bindings?: Array<{ session_id?: string; goal_id?: string; goal_title?: string | null; host_surface?: string }> }>('/api/attached-sessions', {}, this.core);
        const out = new Map<string, { goalId: string; goalTitle: string | null; hostSurface: string }>();
        for (const row of res.bindings ?? []) {
          if (typeof row.session_id === 'string' && typeof row.goal_id === 'string') out.set(row.session_id, { goalId: row.goal_id, goalTitle: row.goal_title ?? null, hostSurface: String(row.host_surface ?? '') });
        }
        return out;
      },
      goal: async (goalId) => {
        try { return { title: (await this.states.get(goalId)).title }; }
        catch (err) {
          if (err instanceof GoalStatusUnavailable) return err.goalTitle ? { title: err.goalTitle } : null;
          return err instanceof Error && err.message === 'goal_not_found' ? 'missing' : null;
        }
      },
    };
  }

  private syncing: Promise<void> | null = null;
  private syncAgain = false;

  /** `syncSessions` for the latest scan, one run at a time: a call during a run asks for one more after it. */
  syncSoon(read: () => Parameters<LoopxService['syncSessions']>[0]): Promise<void> {
    if (this.syncing) { this.syncAgain = true; return this.syncing; }
    this.syncing = (async () => {
      try { do { this.syncAgain = false; await this.syncSessions(read()); } while (this.syncAgain); }
      finally { this.syncing = null; }
    })();
    return this.syncing;
  }

  /**
   * Binds what a scan found. A session on a Goal the core has is imported once it is idle (history is
   * read then, not before) and bound to that Goal under the Goal's registered root; one on a Goal the core
   * no longer has stays as it is, marked in lists but bound to nothing. Safe to run again.
   */
  async syncSessions(sessions: Array<{ agent: string; sessionId: string; cwd?: string; updatedAt: string | null; loopx?: SessionLoopx }>, idleMs = 60_000): Promise<void> {
    const manager = this.manager;
    if (!manager) return;
    const titles = new Map<string, string>();
    for (const s of sessions) if (s.loopx && !s.loopx.missing) titles.set(s.loopx.goalId, s.loopx.goalTitle);
    // Bindings made here or earlier follow the core: a renamed Goal is renamed, a deleted one is marked.
    for (const [threadId, binding] of this.bindings.entries()) {
      const found = await this.lookup().goal(binding.goalId);
      if (found === null) continue;
      const patch: Partial<LoopxBinding> = found === 'missing' ? { goalMissing: true } : { goalMissing: undefined, goalTitle: found.title };
      if (Boolean(binding.goalMissing) !== Boolean(patch.goalMissing) || (found !== 'missing' && binding.goalTitle !== found.title)) { this.bindings.update(threadId, patch); manager.announceLoopx(threadId); }
    }
    for (const session of sessions) {
      const info = session.loopx;
      if (!info || info.missing) continue;
      let thread = manager.threadOfSession(session.agent, session.sessionId);
      if (!thread) {
        // A conversation that is still being written to by its host is left until it has been quiet a while.
        if (session.updatedAt && Date.now() - Date.parse(session.updatedAt) < idleMs) continue;
        // The list shows sessions from every directory, but this sync never widens the workdirs: one
        // outside them is left for the person to import, without asking its agent again every scan.
        if (session.cwd !== undefined && !isWithinWorkdirs(session.cwd, this.deps.dirs.all())) continue;
        thread = await manager.importSession({ agent: session.agent, sessionId: session.sessionId }).catch((err) => { console.warn(`loopx: could not import session for Goal ${info.goalId}: ${err instanceof Error ? err.message : err}`); return null; });
        if (!thread) continue;
      }
      if (this.bindings.get(thread.id)) continue;
      const adapter = this.deps.adapters.get(thread.agent);
      const goalAgentId = adapter ? goalAgentFor(adapter.id) : null;
      if (!adapter || !goalAgentId) continue;
      const earlier = this.bindings.threadsOf(info.goalId).map((id) => this.bindings.get(id)!).at(-1);
      // The core's grant is read from an earlier conversation of the Goal; a Goal with none joins read-only.
      const permission: LoopxPermission = earlier?.permission === 'workspace_auto' && permissionAvailable(adapter, 'workspace_auto').ok ? 'workspace_auto' : permissionAvailable(adapter, 'read_only').ok ? 'read_only' : 'workspace_auto';
      let workspace = thread.cwd;
      const root = await registeredGoalRoot(info.goalId, this.cli).catch(() => null);
      try { if (root) workspace = realpathSync(root); } catch { /* the root is gone; the thread's own directory stands */ }
      if (!isWithinWorkdirs(workspace, this.deps.dirs.all())) workspace = thread.cwd;
      this.bindings.set(thread.id, {
        goalId: info.goalId, goalAgentId, managedAgentId: earlier?.managedAgentId ?? null, goalInstanceId: earlier?.goalInstanceId ?? null,
        workspace, permission, goalTitle: titles.get(info.goalId) ?? info.goalTitle, basis: info.basis, source: info.source,
      });
      manager.announceLoopx(thread.id);
      manager.autoHandover.setOverride(thread.id, false);
    }
  }

  async context(threadId: string): Promise<string> {
    const binding = this.bindings.get(threadId);
    if (!binding) return '';
    try { return goalContextBlock(binding, await this.states.get(binding.goalId)); }
    catch (err) { return goalContextBlock(binding, null, err instanceof Error ? err.message : String(err)); }
  }

  refuse(threadId: string, request: ApprovalRequest): { optionId: string } | { cancelled: true } | null {
    if (!this.bindings.get(threadId)) return null;
    const reject = request.options.find((o) => o.kind === 'reject_once') ?? request.options.find((o) => o.kind.startsWith('reject'));
    return reject ? { optionId: reject.optionId } : { cancelled: true };
  }

  /** Which of this daemon's agents can hold a Goal conversation, and under which permissions. */
  capabilities() {
    return [...this.deps.adapters].map(([agent, adapter]) => {
      const goalAgent = goalAgentFor(adapter.id);
      return {
        agent,
        available: goalAgent !== null,
        ...(goalAgent === null ? { reason: '该 agent 还不能承载 LoopX Goal' } : {}),
        permissions: LOOPX_PERMISSIONS.map((id) => ({ id, ...(goalAgent ? permissionAvailable(adapter, id) : { ok: false, reason: '该 agent 还不能承载 LoopX Goal' }) }))
          .map(({ id, ok, reason }) => ({ id, available: ok, ...(reason ? { reason } : {}), isDefault: id === 'workspace_auto',
            ...(id === 'workspace_auto' ? { sandboxed: workspaceSandboxed(adapter.id) } : {}) })),
      };
    });
  }

  private pick(agent: unknown, permission: unknown) {
    const adapter = typeof agent === 'string' ? this.deps.adapters.get(agent) : undefined;
    if (!adapter) throw new HttpError(400, `unknown agent: ${String(agent)}`);
    const goalAgentId = goalAgentFor(adapter.id);
    if (!goalAgentId) throw new HttpError(400, '该 agent 还不能承载 LoopX Goal');
    if (!(LOOPX_PERMISSIONS as readonly string[]).includes(permission as string)) throw new HttpError(400, 'permission must be read_only or workspace_auto');
    const allowed = permissionAvailable(adapter, permission as LoopxPermission);
    if (!allowed.ok) throw new HttpError(400, allowed.reason ?? 'permission unavailable');
    return { adapter, goalAgentId, permission: permission as LoopxPermission };
  }

  private allowedDir(cwd: unknown): string {
    const raw = asText(cwd, 'cwd', 1000);
    let real: string;
    try { real = realpathSync(raw); if (!statSync(real).isDirectory()) throw new Error('not a directory'); }
    catch { throw new HttpError(400, 'cwd does not exist'); }
    if (!isWithinWorkdirs(real, this.deps.dirs.all())) throw new HttpError(400, 'cwd is not in the allowed workdirs');
    return real;
  }

  /** The directory must be allowed here and be one the core lets a Goal be created in, matched by the core's own reference. */
  private async workspace(cwd: unknown): Promise<{ path: string; ref: string }> {
    const real = this.allowedDir(cwd);
    const granted = await grantedWorkspaces(this.core).catch((err) => { throw new HttpError(err instanceof CoreError ? err.status : 502, err.message, { code: err.code ?? 'loopx_core_unavailable' }); });
    const ref = workspaceRef(real);
    if (!granted.some((g) => g.ref === ref)) throw new HttpError(409, '这个目录还没有授予 LoopX 核心，不能在这里创建 Goal', { code: 'workspace_not_granted', candidates: granted.map((g) => ({ title: g.title })) });
    return { path: real, ref };
  }

  /**
   * Where a granted directory is on this machine, so a person can pick it: the core names it only by
   * reference and a title relative to some root, so the allowed roots (and their parents) are tried.
   * Null when none of them gives the core's reference, or the directory is outside the allowed workdirs.
   */
  private locate(g: { ref: string; title: string }): string | null {
    for (const root of this.deps.dirs.all()) {
      let real: string;
      try { real = realpathSync(root); } catch { continue; }
      for (const candidate of [real, join(real, g.title), join(dirname(real), g.title)]) {
        if (workspaceRef(candidate) === g.ref && isWithinWorkdirs(candidate, this.deps.dirs.all())) return candidate;
      }
    }
    return null;
  }

  private fail(err: unknown): never {
    if (err instanceof HttpError) throw err;
    if (err instanceof CoreError) throw new HttpError(err.status === 502 ? 502 : err.status >= 400 && err.status < 500 ? 409 : 502, err.message, { code: err.code ?? 'loopx_core_error', core: err.body });
    throw err;
  }

  /** Step 1 of a launch: have the core review the Goal. Nothing is created yet. */
  async preview(input: any, acceptLanguage?: unknown) {
    const { goalAgentId, permission } = this.pick(input.agent, input.permission);
    const ws = await this.workspace(input.cwd);
    const objective = asText(input.objective, 'objective');
    const completion = typeof input.completion === 'string' ? input.completion.trim() : '';
    const boundary = typeof input.boundary === 'string' ? input.boundary.trim() : '';
    const operationId = randomUUID();
    const draft: GoalDraft = {
      goalId: `goal-${operationId}`,
      title: (typeof input.title === 'string' && input.title.trim() ? input.title.trim() : Array.from(objective).slice(0, 80).join('')),
      objective, completion, boundary, goalAgentId, workspaceRef: ws.ref, permission: corePermission(permission), operationId, locale: goalLocale(acceptLanguage),
    };
    try {
      const { proposal } = await previewGoalCreate(draft, this.core);
      this.previews.set(proposal.proposal_id, { agent: input.agent, goalAgentId, permission, path: ws.path, ref: ws.ref });
      return { proposalId: proposal.proposal_id as string, goalId: draft.goalId, title: draft.title, workspace: ws.path, agent: input.agent, goalAgent: goalAgentId, permission, objective, completion, boundary, status: proposal.status };
    } catch (err) { return this.fail(err); }
  }

  /**
   * Step 2: apply the reviewed Goal, then open this daemon's own thread for it and send the first message.
   * Agent, permission and directory come from the preview and the core's reviewed proposal; a request that
   * names different ones is refused. A launch that failed after the core created the Goal is safe to retry.
   */
  async apply(input: any) {
    if (!this.manager) throw new HttpError(503, 'loopx adapter is not attached');
    const proposalId = asText(input.proposalId, 'proposalId', 200);
    // Confirmations of one proposal run one at a time: a second that arrives while the first is
    // still opening the thread waits for it and then finds that thread, instead of opening another.
    const before = this.launching.get(proposalId) ?? Promise.resolve();
    const run = before.catch(() => undefined).then(() => this.applyOnce(proposalId, input));
    const tail = run.catch(() => undefined);
    this.launching.set(proposalId, tail);
    void tail.then(() => { if (this.launching.get(proposalId) === tail) this.launching.delete(proposalId); });
    return run;
  }

  private async applyOnce(proposalId: string, input: any) {
    const manager = this.manager!;
    const known = this.bindings.byProposal(proposalId);
    if (known) {
      const bound = this.bindings.get(known)!;
      if (bound.firstMessage === undefined) return { threadId: known, goalId: bound.goalId, reused: true };
      try { return { ...(await this.dispatch(known, bound.firstMessage)), goalId: bound.goalId, reused: true }; } catch (err) { return this.fail(err); }
    }
    const asked = this.previews.get(proposalId);
    if (!asked) throw new HttpError(409, '这份预览已失效，请重新预览', { code: 'preview_expired' });
    if (input.agent !== undefined && input.agent !== asked.agent) throw new HttpError(409, '确认的 agent 和预览时不同，请重新预览', { code: 'agent_mismatch' });
    if (input.permission !== undefined && input.permission !== asked.permission) throw new HttpError(409, '确认的权限和预览时不同，请重新预览', { code: 'permission_mismatch' });
    const adapter = this.pick(asked.agent, asked.permission).adapter;
    try {
      const { proposal: reviewed } = await loadProposal(proposalId, this.core);
      const params = reviewed.normalized_parameters ?? {};
      const permission = fromCorePermission(params.permission);
      if (reviewed.action_kind !== 'goal.create' || params.open_session !== false || params.agent_id !== asked.goalAgentId
        || params.workspace_ref !== asked.ref || permission !== asked.permission) throw new HttpError(409, '这份预览不是这次发起的 Goal');
      const done = reviewed.status === 'applied' ? reviewed : await (async () => {
        if (!(await grantedWorkspaces(this.core)).some((g) => g.ref === asked.ref)) throw new HttpError(409, '预览里的目录已不在核心授予的范围内', { code: 'workspace_not_granted' });
        return (await applyProposal(proposalId, this.core)).proposal;
      })();
      if (done.status !== 'applied' || done.receipt?.outcome !== 'goal_created') throw new HttpError(409, '核心没有创建这个 Goal（预览可能已过期），请重新预览', { code: 'proposal_not_applied', status: done.status });
      if (fromCorePermission(done.normalized_parameters?.permission ?? params.permission) !== permission) throw new HttpError(409, '核心创建的 Goal 权限和预览不一致', { code: 'permission_mismatch' });
      const ids = done.receipt.resource_ids ?? {};
      const goalId = String(ids.goal_id ?? params.goal_id);
      const thread = await manager.createThread({
        agent: asked.agent, cwd: asked.path, title: String(params.title ?? goalId), ...threadMode(adapter, asked.permission),
        ...(typeof input.model === 'string' && input.model ? { model: input.model } : {}),
        ...(typeof input.effort === 'string' && input.effort ? { effort: input.effort } : {}),
      });
      const text = typeof input.text === 'string' && input.text.trim() ? input.text.trim() : `开始推进 Goal「${params.title}」。先核对目标边界和现有待办，再汇报你的计划；遇到权限边界时停下来说明。`;
      this.bindings.set(thread.id, {
        goalId, goalAgentId: asked.goalAgentId, managedAgentId: typeof ids.managed_agent_id === 'string' ? ids.managed_agent_id : null,
        goalInstanceId: typeof ids.goal_instance_id === 'string' ? ids.goal_instance_id : null,
        workspace: asked.path, permission, proposalId, firstMessage: text, goalTitle: String(params.title ?? goalId), basis: 'workbench', source: 'launch',
      });
      this.previews.delete(proposalId);
      manager.announceLoopx(thread.id);
      // A handover would move the conversation to another agent and leave the Goal's boundary behind.
      manager.autoHandover.setOverride(thread.id, false);
      return { ...(await this.dispatch(thread.id, text)), goalId, adapter: adapter.id };
    } catch (err) { return this.fail(err); }
  }

  /**
   * Sends a launch's first message. It counts as delivered only on a fact from the agent: something
   * it produced, or a turn that ended normally. prompt_sent records the intent just before the prompt
   * is handed over, not that it arrived, so a turn that ended after prompt_sent with nothing from the
   * agent is uncertain: the message is neither sent again (it may have arrived) nor forgotten, and the
   * answer says so; the conversation shows the interrupted turn, where it can be sent again by hand.
   * A turn still open is in flight and left alone; one that ended before prompt_sent is sent again.
   */
  private async dispatch(threadId: string, text: string) {
    const events = this.manager!.listEvents(threadId, 0);
    const turns = events.filter((e) => e.type === 'turn.started').map((e) => e.turnId);
    const of = (turnId: string | null) => events.filter((e) => e.turnId === turnId);
    const delivered = (turnId: string | null) => of(turnId).some((e) => AGENT_OUTPUT.has(e.type)
      || (e.type === 'turn.done' && e.data?.stopReason !== 'cancelled'));
    const done = turns.find(delivered);
    if (done !== undefined) {
      this.bindings.update(threadId, { firstMessage: undefined });
      return { threadId, turnId: done };
    }
    const last = turns.at(-1);
    if (last !== undefined) {
      const ended = of(last).some((e) => e.type === 'turn.error' || e.type === 'turn.done');
      if (!ended) return { threadId, turnId: last };
      if (of(last).some((e) => e.type === 'turn.phase' && e.data?.phase === 'prompt_sent')) return { threadId, turnId: last, delivery: 'uncertain' as const };
    }
    const started = await this.manager!.startTurn(threadId, { text, fromUser: true } as never);
    return { threadId, turnId: 'turnId' in started ? started.turnId : null };
  }

  /**
   * A new conversation on a Goal that already exists. The core must know the Goal and have it active. The
   * conversation works in the root the Goal is registered under, read back from the registry; a `cwd` the
   * caller names must be that root. The permission is the one an earlier conversation of this Goal was
   * created under by the core; a Goal with none here (made in the dashboard) joins read-only.
   */
  async join(input: any) {
    const manager = this.manager;
    if (!manager) throw new HttpError(503, 'loopx adapter is not attached');
    const goalId = asText(input.goalId, 'goalId', 200);
    const earlier = this.bindings.threadsOf(goalId).map((id) => this.bindings.get(id)!).at(-1);
    const ceiling: LoopxPermission = earlier?.permission ?? 'read_only';
    const joining = this.deps.adapters.get(input.agent);
    const asked = input.permission ?? (ceiling === 'workspace_auto' || (joining && !permissionAvailable(joining, 'read_only').ok) ? 'workspace_auto' : 'read_only');
    if (asked === 'workspace_auto' && ceiling !== 'workspace_auto') throw new HttpError(409, '这个 Goal 不是以工作区写入权限创建的，只能只读加入', { code: 'permission_mismatch' });
    const { adapter, goalAgentId, permission } = this.pick(input.agent, asked);
    let state: GoalState;
    try { state = await this.states.get(goalId, true); }
    catch (err) { throw new HttpError(404, err instanceof Error && err.message === 'goal_not_found' ? 'LoopX 核心里没有这个 Goal' : `读不到这个 Goal：${err instanceof Error ? err.message : err}`, { code: 'goal_not_found' }); }
    if (state.activation && state.activation !== 'active') throw new HttpError(409, `这个 Goal 当前不是活跃状态（${state.activation}）`, { code: 'goal_not_active' });
    if (!this.cli.registry) throw new HttpError(503, '没有配置 LoopX registry，读不到这个 Goal 的工作目录', { code: 'goal_root_unknown' });
    const root = await registeredGoalRoot(goalId, this.cli).catch(() => null);
    if (!root) throw new HttpError(409, 'registry 里读不到这个 Goal 的工作目录', { code: 'goal_root_unknown' });
    let workspace: string;
    try { workspace = realpathSync(root); } catch { throw new HttpError(409, `这个 Goal 的工作目录不存在：${root}`, { code: 'goal_root_unknown' }); }
    if (!isWithinWorkdirs(workspace, this.deps.dirs.all())) throw new HttpError(400, 'Goal 的工作目录不在允许的目录内', { code: 'workspace_not_allowed' });
    if (input.cwd !== undefined && input.cwd !== null && input.cwd !== '' && this.allowedDir(input.cwd) !== workspace) throw new HttpError(409, '只能在 Goal 登记的工作目录里加入', { code: 'workspace_mismatch', workspace });
    try {
      const thread = await manager.createThread({
        agent: input.agent, cwd: workspace, title: state.title !== goalId ? state.title : goalId, ...threadMode(adapter, permission),
        ...(typeof input.model === 'string' && input.model ? { model: input.model } : {}),
        ...(typeof input.effort === 'string' && input.effort ? { effort: input.effort } : {}),
      });
      this.bindings.set(thread.id, { goalId, goalAgentId, managedAgentId: earlier?.managedAgentId ?? null, goalInstanceId: earlier?.goalInstanceId ?? null, workspace, permission, goalTitle: state.title, basis: 'workbench', source: 'join' });
      manager.announceLoopx(thread.id);
      manager.autoHandover.setOverride(thread.id, false);
      const text = typeof input.text === 'string' && input.text.trim() ? input.text.trim() : '';
      const started = text ? await manager.startTurn(thread.id, { text, fromUser: true } as never) : null;
      return { threadId: thread.id, goalId, workspace, permission, ...(started && 'turnId' in started ? { turnId: started.turnId } : {}) };
    } catch (err) { return this.fail(err); }
  }

  async goal(threadId: string, refresh: boolean) {
    const binding = this.bindings.get(threadId);
    if (!binding) throw new HttpError(404, 'not a LoopX Goal conversation');
    const observedAt = new Date().toISOString();
    try { return { binding, state: await this.states.get(binding.goalId, refresh), freshness: 'fresh', observedAt }; }
    catch (err) { return { binding, state: null, freshness: 'unavailable', observedAt, error: err instanceof Error ? err.message : String(err) }; }
  }

  async health() {
    const core = await fetch(new URL('/healthz', this.core), { signal: AbortSignal.timeout(3000) }).then((r) => r.ok, () => false);
    return { core: { ok: core, url: this.core.origin } };
  }

  /** Handles `/api/loopx/...` routes of its own. Returns false for anything it does not own. */
  async handle(req: IncomingMessage, res: ServerResponse, path: string, url: URL): Promise<boolean> {
    const method = req.method ?? 'GET';
    if (path === '/api/loopx/health' && method === 'GET') { send(res, 200, await this.health()); return true; }
    if (path === '/api/loopx/capabilities' && method === 'GET') { send(res, 200, { agents: this.capabilities() }); return true; }
    if (path === '/api/loopx/workspaces' && method === 'GET') {
      const granted = await grantedWorkspaces(this.core).catch((err) => { throw new HttpError(502, err.message, { code: 'loopx_core_unavailable' }); });
      send(res, 200, { workspaces: granted.map((g) => ({ ...g, path: this.locate(g) })) });
      return true;
    }
    if (path === '/api/loopx/launch/preview' && method === 'POST') { send(res, 200, await this.preview(await body(req), req.headers['accept-language'])); return true; }
    if (path === '/api/loopx/launch/apply' && method === 'POST') { send(res, 200, await this.apply(await body(req))); return true; }
    if (path === '/api/loopx/launch/join' && method === 'POST') { send(res, 200, await this.join(await body(req))); return true; }
    const goal = /^\/api\/loopx\/goal\/([^/]+)$/.exec(path);
    if (goal && method === 'GET') { send(res, 200, await this.goal(decodeURIComponent(goal[1]), url.searchParams.get('refresh') === '1')); return true; }
    return false;
  }
}

export type { GoalState };
