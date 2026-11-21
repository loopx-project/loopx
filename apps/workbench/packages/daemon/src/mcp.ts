import type { DatabaseSync } from 'node:sqlite';
import type { AgentAdapter, McpServerInfo } from './adapters/types.js';
import { HttpError } from './errors.js';
import { MCP_NAME, checkMcpConfig, stableStringify } from './mcp-format.js';

/**
 * MCP management, in the same spirit as skills: what each agent instance's own config says is the
 * truth, read straight off the filesystem without starting anything. The hub adds one layer of its
 * own — a registry entry per server name with the business line and the purpose in words, which is
 * the part no config file knows — and performs the one-entry writes the page asks for. Installing
 * between machines is driven by the page, machine by machine, through these same routes.
 */

/** One agent instance's MCP servers, as this machine's daemon sees them. */
export interface McpAgentListing {
  agent: string;
  /** Whether this instance's MCP config can also be written (install/remove). */
  writable: boolean;
  servers: McpServerInfo[];
  /** Why the servers could not be read; one broken agent must not hide the others. */
  error?: string;
}

export interface McpListing {
  agents: McpAgentListing[];
}

export type McpInstallStatus = 'installed' | 'exists' | 'same' | 'error';

export interface McpInstallOutcome {
  agent: string;
  status: McpInstallStatus;
  error?: string;
}

/** A manually registered server, or just the notes for a name the scan finds. */
export interface McpEntry {
  name: string;
  businessLine: string;
  description: string;
  /** The server config, for entries typed on the page; null when the entry only annotates. */
  server: Record<string, unknown> | null;
  createdAt: string;
  updatedAt: string;
}

const MAX_BUSINESS_LINE = 100;
const MAX_DESCRIPTION = 2000;

/** The registry of notes and manual entries, in the hub's own database. */
export class McpRegistry {
  constructor(private readonly db: DatabaseSync) {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS mcp_entries (
        name TEXT PRIMARY KEY,
        business_line TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        server TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
    `);
  }

  list(): McpEntry[] {
    const rows = this.db.prepare('SELECT name, business_line, description, server, created_at, updated_at FROM mcp_entries ORDER BY name').all() as Array<{
      name: string; business_line: string; description: string; server: string | null; created_at: string; updated_at: string;
    }>;
    return rows.map((row) => ({
      name: row.name,
      businessLine: row.business_line,
      description: row.description,
      server: row.server ? (JSON.parse(row.server) as Record<string, unknown>) : null,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    }));
  }

  /** Create or update one entry. Without `server`, an existing config is dropped: the entry goes
   * back to being notes for whatever the machines themselves hold. */
  save(input: { name?: unknown; businessLine?: unknown; description?: unknown; server?: unknown }): McpEntry {
    const name = typeof input.name === 'string' ? input.name.trim() : '';
    if (!MCP_NAME.test(name)) throw new HttpError(400, 'MCP 名称须以字母或数字开头，仅含字母、数字、下划线和连字符');
    const businessLine = typeof input.businessLine === 'string' ? input.businessLine.trim().slice(0, MAX_BUSINESS_LINE) : '';
    const description = typeof input.description === 'string' ? input.description.trim().slice(0, MAX_DESCRIPTION) : '';
    const server = input.server === null || input.server === undefined ? null : checkMcpConfig(input.server);
    const now = new Date().toISOString();
    this.db
      .prepare('INSERT INTO mcp_entries (name, business_line, description, server, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET business_line = excluded.business_line, description = excluded.description, server = excluded.server, updated_at = excluded.updated_at')
      .run(name, businessLine, description, server ? JSON.stringify(server) : null, now, now);
    return { name, businessLine, description, server, createdAt: now, updatedAt: now };
  }

  remove(name: string): boolean {
    return this.db.prepare('DELETE FROM mcp_entries WHERE name = ?').run(name).changes > 0;
  }
}

export class McpManager {
  constructor(private readonly adapters: Map<string, AgentAdapter>, readonly entries: McpRegistry) {}

  /** Every instance whose adapter knows where its MCP config lives. */
  listing(): McpListing {
    const agents: McpAgentListing[] = [];
    for (const [id, adapter] of this.adapters) {
      if (!adapter.mcpServers) continue;
      const writable = Boolean(adapter.mcpInstall && adapter.mcpRemove);
      try {
        agents.push({ agent: id, writable, servers: adapter.mcpServers() });
      } catch (e) {
        // One unreadable config must not hide the machine's other agents.
        agents.push({ agent: id, writable, servers: [], error: e instanceof Error ? e.message : String(e) });
      }
    }
    return { agents };
  }

  /** Write one server into the config of each listed instance. `exists` and `same` keep a
   * reinstall from clobbering a config the target has changed on its own. */
  async install(input: { targets?: unknown; name?: unknown; config?: unknown; overwrite?: unknown }): Promise<McpInstallOutcome[]> {
    const name = typeof input.name === 'string' ? input.name.trim() : '';
    if (!MCP_NAME.test(name)) throw new HttpError(400, 'MCP 名称须以字母或数字开头，仅含字母、数字、下划线和连字符');
    const config = checkMcpConfig(input.config);
    const targets = Array.isArray(input.targets) ? input.targets.filter((t): t is string => typeof t === 'string') : [];
    if (!targets.length) throw new HttpError(400, '未选择要安装到的 agent');
    const outcomes: McpInstallOutcome[] = [];
    for (const target of targets) {
      const adapter = this.adapters.get(target);
      if (!adapter?.mcpInstall || !adapter.mcpRemove || !adapter.mcpServers) {
        outcomes.push({ agent: target, status: 'error', error: '此 agent 的 MCP 配置不支持写入' });
        continue;
      }
      try {
        const existing = adapter.mcpServers().find((s) => s.name === name);
        if (existing) {
          if (stableStringify(existing.config) === stableStringify(config)) {
            outcomes.push({ agent: target, status: 'same' });
            continue;
          }
          if (!input.overwrite) {
            outcomes.push({ agent: target, status: 'exists' });
            continue;
          }
        }
        adapter.mcpInstall(name, config);
        outcomes.push({ agent: target, status: 'installed' });
      } catch (e) {
        outcomes.push({ agent: target, status: 'error', error: e instanceof Error ? e.message : String(e) });
      }
    }
    return outcomes;
  }

  /** Take one server out of one instance's config. */
  async remove(agent: string, name: string): Promise<void> {
    if (!MCP_NAME.test(name)) throw new HttpError(400, 'MCP 名称不合法');
    const adapter = this.adapters.get(agent);
    if (!adapter?.mcpServers || !adapter.mcpRemove) throw new HttpError(400, '此 agent 的 MCP 配置不支持写入');
    if (!adapter.mcpServers().some((s) => s.name === name)) throw new HttpError(404, `${agent} 没有名为 ${name} 的 MCP 服务`);
    adapter.mcpRemove(name);
  }
}
