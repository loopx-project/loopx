import type { DatabaseSync } from 'node:sqlite';

/**
 * The daily digest of what the models were asked and what they called. Everything comes from the
 * hub's own log, so nothing is inferred and nothing is read from the agents' files; the one thing
 * the log cannot hold — the whole system prompt — is fetched per thread from the adapter
 * (`readSystemPrompt`) and is not part of this report.
 */

export interface ActivityPrompt {
  threadId: string;
  turnId: string;
  ts: string;
  /** The user's own words, as they typed them. Long ones are cut; the thread holds the whole turn. */
  text: string;
  title: string;
  agent: string;
  cwd: string;
  archived: boolean;
  /** Tool calls the turn made, so the page shows which asks did work and which only talked. */
  tools: number;
}

export interface ActivityTool {
  /** The raw tool name (`Read`, `mcp__github__create_issue`), or a kind label for older rows. */
  name: string;
  count: number;
  mcp: boolean;
  /** The MCP server this tool belongs to, when the call was to one. */
  server?: string;
}

export interface ActivityDay {
  /** Local calendar day, `YYYY-MM-DD`. */
  date: string;
  turns: number;
  threads: number;
  prompts: ActivityPrompt[];
  /** Prompts beyond the per-day cap, when a scheduled task flooded a day. */
  morePrompts: number;
  tools: ActivityTool[];
  /** MCP servers called that day, rolled up from their tools. */
  mcp: Array<{ server: string; count: number }>;
}

const DAY_MS = 86_400_000;
/** How much of each day is sent. The page is a digest, not an archive; threads hold everything. */
const PROMPTS_PER_DAY = 50;
const TOOLS_PER_DAY = 12;
/** Prompts longer than this are cut in the report; the thread still shows the whole turn. */
const PROMPT_CHARS = 400;

/** Kind labels for rows logged before tool names were kept. Named tools speak for themselves. */
const KIND_LABELS: Record<string, string> = {
  read: '读取文件',
  search: '搜索',
  fetch: '抓取网页',
  edit: '编辑文件',
  write: '写入文件',
  create: '创建文件',
  delete: '删除文件',
  move: '移动文件',
  execute: '运行命令',
  other: '其他工具',
};

/** The server and tool an event's data names, when it does: `mcp__server__tool` or Codex's pair. */
export function mcpCallOf(data: Record<string, unknown>): { server: string; tool: string } | null {
  if (typeof data.server === 'string' && data.server) {
    return { server: data.server, tool: typeof data.tool === 'string' ? data.tool : '' };
  }
  if (typeof data.tool === 'string' && data.tool.startsWith('mcp__')) {
    const [, server, ...rest] = data.tool.split('__');
    if (server && rest.length) return { server, tool: rest.join('__') };
  }
  return null;
}

export class Activity {
  constructor(private readonly db: DatabaseSync) {}

  /** Days that have anything, oldest first. The user's words and the tools called, per day. */
  read(days: number, agent = ''): ActivityDay[] {
    const since = new Date(Date.now() - Math.max(1, Math.min(90, days)) * DAY_MS).toISOString();
    const byDay = new Map<string, ActivityDay>();
    const threadsPerDay = new Map<string, Set<string>>();
    const day = (ts: string): ActivityDay => {
      const key = localDay(ts);
      let entry = byDay.get(key);
      if (!entry) {
        entry = { date: key, turns: 0, threads: 0, prompts: [], morePrompts: 0, tools: [], mcp: [] };
        byDay.set(key, entry);
      }
      return entry;
    };
    const sawThread = (ts: string, threadId: string) => {
      const key = localDay(ts);
      const set = threadsPerDay.get(key) ?? new Set<string>();
      set.add(threadId);
      threadsPerDay.set(key, set);
    };

    // The user's words, newest first; turn_text keeps imported and terminal-synced turns too.
    const promptRows = this.db.prepare(`SELECT tt.thread_id, tt.turn_id, tt.ts, tt.text, t.title, t.agent, t.cwd, t.archived_at
      FROM turn_text tt JOIN threads t ON t.id = tt.thread_id
      WHERE tt.role = 'user' AND tt.ts >= ? ${agent ? 'AND t.agent = ?' : ''} ORDER BY tt.ts DESC`) as unknown as { all: (...args: string[]) => Array<{ thread_id: string; turn_id: string; ts: string; text: string; title: string; agent: string; cwd: string; archived_at: string | null }> };
    for (const row of promptRows.all(...(agent ? [since, agent] : [since]))) {
      const entry = day(row.ts);
      sawThread(row.ts, row.thread_id);
      entry.turns += 1;
      if (entry.prompts.length < PROMPTS_PER_DAY) {
        entry.prompts.push({
          threadId: row.thread_id, turnId: row.turn_id, ts: row.ts,
          text: row.text.length > PROMPT_CHARS ? `${row.text.slice(0, PROMPT_CHARS)}…` : row.text,
          title: row.title, agent: row.agent, cwd: row.cwd, archived: row.archived_at !== null, tools: 0,
        });
      } else {
        entry.morePrompts += 1;
      }
    }

    // Tool calls, by the raw name when the row kept it and by kind when it predates them. Old rows
    // are not re-guessed from their titles; their count simply shows under the kind it logged.
    const toolCounts = new Map<string, Map<string, { count: number; mcp: boolean; mcpServer?: string }>>();
    const serverCounts = new Map<string, Map<string, number>>();
    const perTurn = new Map<string, number>();
    const toolRows = this.db.prepare(`SELECT thread_id, turn_id, ts, data FROM events WHERE type = 'tool.start' AND ts >= ?
      ${agent ? 'AND thread_id IN (SELECT id FROM threads WHERE agent = ?)' : ''}`) as unknown as { iterate: (...args: string[]) => Iterable<{ thread_id: string; turn_id: string | null; ts: string; data: string }> };
    for (const row of toolRows.iterate(...(agent ? [since, agent] : [since]))) {
      let data: Record<string, unknown>;
      try {
        data = JSON.parse(row.data);
      } catch {
        continue;
      }
      const key = localDay(row.ts);
      day(row.ts);
      if (row.turn_id) perTurn.set(`${row.thread_id}:${row.turn_id}`, (perTurn.get(`${row.thread_id}:${row.turn_id}`) ?? 0) + 1);
      sawThread(row.ts, row.thread_id);
      const mcp = mcpCallOf(data);
      const name = typeof data.tool === 'string' && data.tool ? data.tool : KIND_LABELS[String(data.kind)] ?? KIND_LABELS.other;
      const perTool = toolCounts.get(key) ?? new Map();
      const slot = perTool.get(name) ?? { count: 0, mcp: Boolean(mcp), mcpServer: mcp?.server };
      slot.count += 1;
      perTool.set(name, slot);
      toolCounts.set(key, perTool);
      if (mcp) {
        const perServer = serverCounts.get(key) ?? new Map();
        perServer.set(mcp.server, (perServer.get(mcp.server) ?? 0) + 1);
        serverCounts.set(key, perServer);
      }
    }

    for (const entry of byDay.values()) {
      for (const p of entry.prompts) p.tools = perTurn.get(`${p.threadId}:${p.turnId}`) ?? 0;
    }
    for (const [key, threads] of threadsPerDay) {
      const entry = byDay.get(key);
      if (entry) entry.threads = threads.size;
    }
    for (const [key, perTool] of toolCounts) {
      const entry = byDay.get(key)!;
      entry.tools = [...perTool.entries()]
        .map(([name, slot]) => ({ name, count: slot.count, mcp: slot.mcp, ...(slot.mcpServer ? { server: slot.mcpServer } : {}) }))
        .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
        .slice(0, TOOLS_PER_DAY);
      const perServer = serverCounts.get(key);
      if (perServer) entry.mcp = [...perServer.entries()].map(([server, count]) => ({ server, count })).sort((a, b) => b.count - a.count);
    }

    return [...byDay.values()].sort((a, b) => a.date.localeCompare(b.date));
  }
}

/** The local calendar day of an ISO timestamp, the way the sidebar groups "today" and "yesterday". */
function localDay(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts.slice(0, 10);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}
