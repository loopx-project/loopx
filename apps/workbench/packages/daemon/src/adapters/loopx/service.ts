import { randomUUID } from 'node:crypto';
import { realpathSync, statSync } from 'node:fs';
import { join } from 'node:path';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { HttpError } from '../../errors.js';
import type { AgentAdapter, ApprovalRequest } from '../types.js';
import { isWithinWorkdirs, type Workdirs } from '../../workdirs.js';
import type { ThreadManager } from '../../threads.js';
import { goalAgentFor, LOOPX_PERMISSIONS, LoopxBindings, permissionAvailable, type LoopxBinding, type LoopxPermission } from './binding.js';
import { goalContextBlock } from './context.js';
import { applyProposal, CoreError, grantedWorkspaces, loadProposal, previewGoalCreate, workspaceRef, type GoalDraft } from './core.js';
import { coreUrl } from './proxy.js';
import { cliFromEnv, GoalStateCache, readGoalState, type GoalState, type LoopxCli } from './status.js';

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
  /** A Goal conversation never asks the person: its boundary is the sandbox, not a prompt. */
  refuse(threadId: string, request: ApprovalRequest): { optionId: string } | { cancelled: true } | null;
}

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

export class LoopxService implements LoopxHooks {
  readonly bindings: LoopxBindings;
  private readonly states: GoalStateCache;
  private manager: ThreadManager | null = null;
  private readonly core: URL;

  constructor(private readonly deps: LoopxDeps) {
    this.bindings = new LoopxBindings(join(deps.dataDir, 'loopx-threads.json'));
    const cli = deps.cli ?? cliFromEnv();
    this.states = new GoalStateCache((goalId) => readGoalState(goalId, cli));
    this.core = deps.core ?? coreUrl();
  }

  attach(manager: ThreadManager): void { this.manager = manager; }

  binding(threadId: string): LoopxBinding | null { return this.bindings.get(threadId); }

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
        permissions: LOOPX_PERMISSIONS.map((id) => ({ id, ...(goalAgent ? permissionAvailable(adapter.id, id) : { ok: false, reason: '该 agent 还不能承载 LoopX Goal' }) }))
          .map(({ id, ok, reason }) => ({ id, available: ok, ...(reason ? { reason } : {}), isDefault: id === (adapter.id === 'codex' ? 'workspace_auto' : 'read_only') })),
      };
    });
  }

  private pick(agent: unknown, permission: unknown) {
    const adapter = typeof agent === 'string' ? this.deps.adapters.get(agent) : undefined;
    if (!adapter) throw new HttpError(400, `unknown agent: ${String(agent)}`);
    const goalAgentId = goalAgentFor(adapter.id);
    if (!goalAgentId) throw new HttpError(400, '该 agent 还不能承载 LoopX Goal');
    if (!(LOOPX_PERMISSIONS as readonly string[]).includes(permission as string)) throw new HttpError(400, 'permission must be read_only or workspace_auto');
    const allowed = permissionAvailable(adapter.id, permission as LoopxPermission);
    if (!allowed.ok) throw new HttpError(400, allowed.reason ?? 'permission unavailable');
    return { adapter, goalAgentId, permission: permission as LoopxPermission };
  }

  /** The directory must be allowed here and be one the core lets a Goal be created in. */
  private async workspace(cwd: unknown): Promise<{ path: string; ref: string }> {
    const raw = asText(cwd, 'cwd', 1000);
    let real: string;
    try { real = realpathSync(raw); if (!statSync(real).isDirectory()) throw new Error('not a directory'); }
    catch { throw new HttpError(400, 'cwd does not exist'); }
    if (!isWithinWorkdirs(real, this.deps.dirs.all())) throw new HttpError(400, 'cwd is not in the allowed workdirs');
    const granted = await grantedWorkspaces(this.core).catch((err) => { throw new HttpError(err instanceof CoreError ? err.status : 502, err.message, { code: err.code ?? 'loopx_core_unavailable' }); });
    const hit = granted.find((g) => g.path === real || g.ref === workspaceRef(real));
    if (!hit) throw new HttpError(409, '这个目录还没有授予 LoopX 核心，不能在这里创建 Goal', { code: 'workspace_not_granted', candidates: granted.map((g) => ({ path: g.path, title: g.title })) });
    return { path: hit.path, ref: hit.ref };
  }

  private fail(err: unknown): never {
    if (err instanceof HttpError) throw err;
    if (err instanceof CoreError) throw new HttpError(err.status === 502 ? 502 : err.status >= 400 && err.status < 500 ? 409 : 502, err.message, { code: err.code ?? 'loopx_core_error', core: err.body });
    throw err;
  }

  /** Step 1 of a launch: have the core review the Goal. Nothing is created yet. */
  async preview(input: any) {
    const { goalAgentId, permission } = this.pick(input.agent, input.permission);
    const ws = await this.workspace(input.cwd);
    const objective = asText(input.objective, 'objective');
    const completion = typeof input.completion === 'string' ? input.completion.trim() : '';
    const boundary = typeof input.boundary === 'string' ? input.boundary.trim() : '';
    const operationId = randomUUID();
    const draft: GoalDraft = {
      goalId: `goal-${operationId}`,
      title: (typeof input.title === 'string' && input.title.trim() ? input.title.trim() : Array.from(objective).slice(0, 80).join('')),
      objective, completion, boundary, goalAgentId, workspaceRef: ws.ref, permission, operationId,
    };
    try {
      const { proposal } = await previewGoalCreate(draft, this.core);
      return { proposalId: proposal.proposal_id as string, goalId: draft.goalId, title: draft.title, workspace: ws.path, agent: input.agent, goalAgent: goalAgentId, permission, objective, completion, boundary, status: proposal.status };
    } catch (err) { return this.fail(err); }
  }

  /** Step 2: apply the reviewed Goal, then open this daemon's own thread for it and send the first message. */
  async apply(input: any) {
    const manager = this.manager;
    if (!manager) throw new HttpError(503, 'loopx adapter is not attached');
    const proposalId = asText(input.proposalId, 'proposalId', 200);
    const known = this.bindings.byProposal(proposalId);
    if (known) return { threadId: known, goalId: this.bindings.get(known)!.goalId, reused: true };
    const { adapter, goalAgentId, permission } = this.pick(input.agent, input.permission);
    try {
      const { proposal: reviewed } = await loadProposal(proposalId, this.core);
      const params = reviewed.normalized_parameters ?? {};
      if (reviewed.action_kind !== 'goal.create' || params.open_session !== false || params.agent_id !== goalAgentId) throw new HttpError(409, '这份预览不是这次发起的 Goal');
      const granted = await grantedWorkspaces(this.core);
      const ws = granted.find((g) => g.ref === params.workspace_ref);
      if (!ws) throw new HttpError(409, '预览里的目录已不在核心授予的范围内', { code: 'workspace_not_granted' });
      const applied = await applyProposal(proposalId, this.core);
      const done = applied.proposal;
      if (done.status !== 'applied' || done.receipt?.outcome !== 'goal_created') throw new HttpError(409, '核心没有创建这个 Goal（预览可能已过期），请重新预览', { code: 'proposal_not_applied', status: done.status });
      const ids = done.receipt.resource_ids ?? {};
      const goalId = String(ids.goal_id ?? params.goal_id);
      const thread = await manager.createThread({
        agent: input.agent, cwd: ws.path, title: String(params.title ?? goalId), permissionMode: 'ask',
        ...(typeof input.model === 'string' && input.model ? { model: input.model } : {}),
        ...(typeof input.effort === 'string' && input.effort ? { effort: input.effort } : {}),
      });
      this.bindings.set(thread.id, {
        goalId, goalAgentId, managedAgentId: typeof ids.managed_agent_id === 'string' ? ids.managed_agent_id : null,
        goalInstanceId: typeof ids.goal_instance_id === 'string' ? ids.goal_instance_id : null,
        workspace: ws.path, permission, proposalId,
      });
      manager.announceLoopx(thread.id);
      // A handover would move the conversation to another agent and leave the Goal's boundary behind.
      manager.autoHandover.setOverride(thread.id, false);
      const text = typeof input.text === 'string' && input.text.trim() ? input.text.trim() : `开始推进 Goal「${params.title}」。先核对目标边界和现有待办，再汇报你的计划；遇到权限边界时停下来说明。`;
      const started = await manager.startTurn(thread.id, { text, fromUser: true } as never);
      return { threadId: thread.id, goalId, turnId: 'turnId' in started ? started.turnId : null, adapter: adapter.id };
    } catch (err) { return this.fail(err); }
  }

  /**
   * A new conversation on a Goal that already exists. The core must know the Goal and have it active.
   * The conversation works in the directory an earlier conversation of this Goal used; a Goal made
   * elsewhere (the dashboard) has none yet, so the caller names the directory (`cwd`), which has to be
   * allowed here. The core does not expose a Goal's registered root to read, so that case is trusted.
   */
  async join(input: any) {
    const manager = this.manager;
    if (!manager) throw new HttpError(503, 'loopx adapter is not attached');
    const goalId = asText(input.goalId, 'goalId', 200);
    const earlier = this.bindings.threadsOf(goalId).map((id) => this.bindings.get(id)!).at(-1);
    const { goalAgentId, permission } = this.pick(input.agent, input.permission ?? earlier?.permission ?? (this.deps.adapters.get(input.agent)?.id === 'codex' ? 'workspace_auto' : 'read_only'));
    let state: GoalState;
    try { state = await this.states.get(goalId, true); }
    catch (err) { throw new HttpError(404, err instanceof Error && err.message === 'goal_not_found' ? 'LoopX 核心里没有这个 Goal' : `读不到这个 Goal：${err instanceof Error ? err.message : err}`, { code: 'goal_not_found' }); }
    if (state.activation && state.activation !== 'active') throw new HttpError(409, `这个 Goal 当前不是活跃状态（${state.activation}）`, { code: 'goal_not_active' });
    let workspace = earlier?.workspace;
    if (!workspace) {
      const raw = asText(input.cwd, 'cwd', 1000);
      try { workspace = realpathSync(raw); if (!statSync(workspace).isDirectory()) throw new Error('not a directory'); } catch { throw new HttpError(400, 'cwd does not exist'); }
      if (!isWithinWorkdirs(workspace, this.deps.dirs.all())) throw new HttpError(400, 'cwd is not in the allowed workdirs');
    }
    try {
      const thread = await manager.createThread({
        agent: input.agent, cwd: workspace, title: state.title !== goalId ? state.title : goalId, permissionMode: 'ask',
        ...(typeof input.model === 'string' && input.model ? { model: input.model } : {}),
        ...(typeof input.effort === 'string' && input.effort ? { effort: input.effort } : {}),
      });
      this.bindings.set(thread.id, { goalId, goalAgentId, managedAgentId: earlier?.managedAgentId ?? null, goalInstanceId: earlier?.goalInstanceId ?? null, workspace, permission });
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
      send(res, 200, { workspaces: await grantedWorkspaces(this.core).catch((err) => { throw new HttpError(502, err.message, { code: 'loopx_core_unavailable' }); }) });
      return true;
    }
    if (path === '/api/loopx/launch/preview' && method === 'POST') { send(res, 200, await this.preview(await body(req))); return true; }
    if (path === '/api/loopx/launch/apply' && method === 'POST') { send(res, 200, await this.apply(await body(req))); return true; }
    if (path === '/api/loopx/launch/join' && method === 'POST') { send(res, 200, await this.join(await body(req))); return true; }
    const goal = /^\/api\/loopx\/goal\/([^/]+)$/.exec(path);
    if (goal && method === 'GET') { send(res, 200, await this.goal(decodeURIComponent(goal[1]), url.searchParams.get('refresh') === '1')); return true; }
    return false;
  }
}

export type { GoalState };
