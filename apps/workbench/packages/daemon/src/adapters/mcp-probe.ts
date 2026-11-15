import { spawn } from 'node:child_process';

/** One stdio MCP server from the machine-local config, already ${VAR}-expanded. */
export interface ConfiguredMcpServer {
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
}

/**
 * The name zcode gives an MCP tool in the model's tool list, so the same string works in
 * session/create's toolAllowlist, which matches exactly. Mirrors zcode's own sanitizing:
 * non [A-Za-z0-9_-] characters collapse into single underscores.
 */
export function mcpToolName(serverName: string, toolName: string): string {
  const part = (name: string) => name.replace(/[^a-zA-Z0-9_-]/g, '_').replace(/_+/g, '_') || 'unknown';
  return `mcp__${part(serverName)}__${part(toolName)}`;
}

/** Expand ${VAR} placeholders from the environment; null means a referenced variable is unset. */
export function expandEnvTemplate(value: string, env: NodeJS.ProcessEnv): string | null {
  let missing: string | null = null;
  const expanded = value.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, name: string) => {
    const resolved = env[name];
    if (resolved === undefined) { missing = name; return ''; }
    return resolved;
  });
  return missing ? null : expanded;
}

/**
 * Ask one stdio MCP server for its tools and return them in zcode's model-visible form.
 * A server that never answers yields no names — its tools stay out of the allowlist, which
 * keeps session creation working instead of failing the whole thread.
 */
export async function probeMcpToolNames(server: ConfiguredMcpServer, timeoutMs = 20_000): Promise<string[]> {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(server.command, server.args, { stdio: ['pipe', 'pipe', 'ignore'], env: { ...process.env, ...server.env } });
    } catch {
      resolve([]);
      return;
    }
    let buffer = '';
    let nextId = 1;
    const pending = new Map<number, (result: any) => void>();
    const finish = (names: string[]) => {
      clearTimeout(timer);
      child.kill();
      resolve(names);
    };
    const timer = setTimeout(() => finish([]), timeoutMs);
    const send = (payload: unknown) => {
      try { child.stdin.write(JSON.stringify(payload) + '\n'); } catch { finish([]); }
    };
    child.on('error', () => finish([]));
    child.stdout!.setEncoding('utf8');
    child.stdout!.on('data', (chunk: string) => {
      buffer += chunk;
      let index;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index).trim();
        buffer = buffer.slice(index + 1);
        if (!line) continue;
        let message: any;
        try { message = JSON.parse(line); } catch { continue; }
        const settle = pending.get(message.id);
        if (settle && message.error === undefined) {
          pending.delete(message.id);
          settle(message.result);
        }
      }
    });
    const request = (method: string, params: unknown) => new Promise<any>((settle) => {
      pending.set(nextId, settle);
      send({ jsonrpc: '2.0', id: nextId++, method, params });
    });
    void (async () => {
      await request('initialize', { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'calli', version: '0.1.0' } });
      send({ jsonrpc: '2.0', method: 'notifications/initialized' });
      const listed = await request('tools/list', {});
      const tools = Array.isArray(listed?.tools) ? listed.tools : [];
      finish(tools.map((tool: any) => typeof tool?.name === 'string' ? mcpToolName(server.name, tool.name) : '').filter(Boolean));
    })().catch(() => finish([]));
  });
}
