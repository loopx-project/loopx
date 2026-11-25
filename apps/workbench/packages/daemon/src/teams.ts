import { randomUUID } from 'node:crypto';
import { isAbsolute } from 'node:path';
import { statSync } from 'node:fs';
import type { Store } from './store.js';
import type { ThreadManager } from './threads.js';
import type { CoordinationManager } from './coordination.js';
import { HttpError } from './errors.js';
import { isWithinWorkdirs } from './workdirs.js';

export interface TeamMember { name: string; agent: string; model?: string; effort?: string; cwd?: string; instruction?: string }
export interface TeamDefinition {
  id: string; name: string; description?: string; entry: string; members: TeamMember[];
  relations: { kind: 'delegate'; from: string[]; to: string }[];
  budget?: { maxDeliveries?: number }; createdAt: string; updatedAt: string;
}
export type TeamSource = { kind: 'http' | 'web' | 'cli' };
/** Team messages follow the normal image contract, with a shorter text limit. */
export function teamMessage(input: { text?: unknown; images?: unknown }): { text: string; images: unknown[] } {
  if (input.text !== undefined && typeof input.text !== 'string') throw new HttpError(400, '任务文字必须是字符串');
  if (input.images !== undefined && !Array.isArray(input.images)) throw new HttpError(400, 'images must be an array');
  const text = typeof input.text === 'string' ? input.text.trim() : '';
  const images = Array.isArray(input.images) ? input.images : [];
  if ((!text && !images.length) || (typeof input.text === 'string' && input.text.length > 32000)) throw new HttpError(400, '团队任务需要文字或图片，文字最多 32000 字符');
  return { text, images };
}
export type TaskStatus = 'running' | 'awaiting_confirm' | 'needs_attention' | 'done';
export interface TeamTask {
  taskId: string; teamId: string; snapshot: TeamDefinition; status: TaskStatus; source: TeamSource; entry: string;
  resultTurnId: string | null; deliveries: number; createdAt: string; error?: string;
  delegations: { from: string; to: string; deliveryId: string; closed: boolean }[];
  processed: string[];
  continuationNotices?: string[];
  history: { taskId: string; name: string; createdAt: string; resultTurnId: string | null }[];
}

/** Only allowed line-start mentions delimit messages. Invalid mentions remain ordinary text. */
export function parseTeamMentions(text: string, allowed: string[]): { to: string; text: string }[] {
  const matches = [...text.matchAll(/^@([\p{L}\p{N}_-]{1,40})(?=[ \t:：\r\n]|$)[ \t:：]*/gmu)]
    .filter(m => allowed.includes(m[1]));
  const background = text.slice(0, matches[0]?.index ?? 0).trim();
  return matches.map((m, i) => ({ to: m[1], text: [background, text.slice(m.index! + m[0].length, matches[i + 1]?.index ?? text.length).trim()].filter(Boolean).join('\n\n') }));
}

export class TeamsManager {
  constructor(private store: Store, private manager: ThreadManager, private coordination: CoordinationManager) { store.teamRecords(); }
  list(): TeamDefinition[] { return this.store.teamRecords().map(row => JSON.parse(row.data)); }
  get(id: string): TeamDefinition { const value = this.list().find(t => t.id === id); if (!value) throw new HttpError(404, '团队不存在'); return value; }
  cwd(value: unknown): string {
    if (typeof value !== 'string' || !isAbsolute(value) || !isWithinWorkdirs(value, this.manager.dirs.all())) throw new HttpError(400, '目录不在允许范围内');
    try { if (!statSync(value).isDirectory()) throw new Error(); } catch { throw new HttpError(400, '目录不存在'); }
    return value;
  }
  validate(input: unknown, id: string = randomUUID()): TeamDefinition {
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new HttpError(400, '团队定义无效');
    const v = input as Record<string, any>;
    const string = (value: unknown, field: string, max = 200, required = false): string | undefined => {
      if (value === undefined && !required) return undefined;
      if (typeof value !== 'string' || value.length > max || (required && !value.trim())) throw new HttpError(400, `${field}无效`);
      return value.trim();
    };
    const name = string(v.name, '团队名称', 200, true)!;
    if (this.list().some(t => t.id !== id && t.name === name)) throw new HttpError(409, '团队名称已存在');
    if (!Array.isArray(v.members) || v.members.length < 1 || v.members.length > 12) throw new HttpError(400, '团队需要 1～12 个成员');
    const names = new Set<string>();
    const members: TeamMember[] = v.members.map((m: any) => {
      if (!m || typeof m.name !== 'string' || !/^[\p{L}\p{N}_-]{1,40}$/u.test(m.name) || m.name === 'user' || names.has(m.name)) throw new HttpError(400, '成员名称无效或已存在');
      names.add(m.name);
      return { name: m.name, agent: string(m.agent, '成员实例', 200, true)!, model: string(m.model, '模型'), effort: string(m.effort, '强度'),
        cwd: m.cwd === undefined || m.cwd === '' ? undefined : this.cwd(m.cwd), instruction: string(m.instruction, '成员说明（最多 4000 字符）', 4000) };
    });
    if (!names.has(v.entry)) throw new HttpError(400, '入口必须是团队成员');
    if (!Array.isArray(v.relations)) throw new HttpError(400, '关系无效');
    const relations = v.relations.map((r: any) => {
      if (!r || r.kind !== 'delegate' || !Array.isArray(r.from) || !r.from.length || !names.has(r.to) || r.from.some((n: unknown) => typeof n !== 'string' || !names.has(n) || n === r.to)) throw new HttpError(400, '关系必须是成员之间的 delegate，不能自环');
      return { kind: 'delegate' as const, from: [...new Set<string>(r.from)], to: r.to as string };
    });
    if (v.budget !== undefined && (!v.budget || typeof v.budget !== 'object' || Array.isArray(v.budget))) throw new HttpError(400, '预算无效');
    const maxDeliveries = v.budget?.maxDeliveries === undefined ? 30 : v.budget.maxDeliveries;
    if (!Number.isInteger(maxDeliveries) || maxDeliveries < 1 || maxDeliveries > 200) throw new HttpError(400, '投递预算必须是 1～200');
    const now = new Date().toISOString();
    return { id, name, description: string(v.description, '团队说明', 4000), entry: v.entry, members, relations, budget: { maxDeliveries }, createdAt: this.list().find(t => t.id === id)?.createdAt ?? now, updatedAt: now };
  }
  save(input: unknown, id?: string): TeamDefinition {
    if (id) this.get(id);
    const team = this.validate(input, id);
    this.store.saveTeam(team.id, JSON.stringify(team)); return team;
  }
  remove(id: string): void { this.get(id); this.store.deleteTeam(id); }
  export(id: string): TeamDefinition { return this.get(id); }
  import(input: unknown): TeamDefinition { return this.save(input); }
  async template(): Promise<Omit<TeamDefinition, 'id' | 'createdAt' | 'updatedAt'>> {
    const agent = (await this.manager.listAgents()).find(a => a.available && a.capabilities.coordination)?.id ?? '';
    return { name: '编码队', description: '写代码、交叉审查、牵头验收', entry: 'lead', members: [
      { name: 'lead', agent, instruction: '拆任务、写验收标准、最终验收并向用户汇报；不亲自写代码' },
      { name: 'dev', agent, instruction: '按 lead 给的契约实现并跑测试，完成后先找 reviewer' },
      { name: 'reviewer', agent, instruction: '只审查不修改，对照验收标准找正确性问题，结论交回 dev' },
    ], relations: [{ kind: 'delegate', from: ['lead'], to: 'dev' }, { kind: 'delegate', from: ['dev'], to: 'reviewer' }], budget: { maxDeliveries: 30 } };
  }
  async available(snapshot: TeamDefinition): Promise<void> {
    const agents = await this.manager.listAgents();
    for (const m of snapshot.members) {
      if (!agents.some(a => a.id === m.agent && a.available && a.capabilities.coordination)) throw new HttpError(409, `成员 ${m.name} 的实例 ${m.agent} 不可用或不支持协作`);
      if (m.cwd) this.cwd(m.cwd);
    }
  }
  async dispatch(input: { teamId: string; text?: unknown; images?: unknown; references?: unknown; cwd: string; source: TeamSource }, authenticatedHuman = false): Promise<{ taskId: string; threadId: string }> {
    const snapshot = this.get(input.teamId);
    const cwd = this.cwd(input.cwd);
    const { text, images } = teamMessage(input);
    // The source label is informational. Only the authenticated HTTP entry supplies this evidence.
    const prepared = this.manager.references.prepare('', text, authenticatedHuman, undefined, input.references);
    await this.available(snapshot);
    const entry = snapshot.members.find(m => m.name === snapshot.entry)!;
    await this.manager.checkImages(entry.agent, entry.model ?? null, images);
    return this.coordination.startTeam(snapshot, prepared.displayText, cwd, input.source, images, authenticatedHuman, prepared.references.map(reference => reference.id));
  }
}
