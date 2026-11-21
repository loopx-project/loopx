import { DatabaseSync } from 'node:sqlite';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { timedUsage, validCount } from './live-usage.js';
import type { UsageSample } from '../telemetry.js';

/** Some native session summaries subtract repeated input. Read measured requests when that is the case. */
export class ZcodeUsageFile {
  private db: DatabaseSync | null = null;
  private readonly seen = new Set<string>();
  private readonly startedAt = Date.now();
  private since = this.startedAt;
  constructor(private readonly env: NodeJS.ProcessEnv, private readonly cwd: string, private readonly session: string) {}

  start(): boolean {
    const home = this.env.HOME || homedir();
    const path = this.env.ZCODE_SESSION_DB_PATH || join(home, '.zcode', 'cli', 'db', 'db.sqlite');
    try {
      this.db = new DatabaseSync(resolve(this.cwd, path.startsWith('~/') ? join(home, path.slice(2)) : path), { readOnly: true });
      this.db.exec('PRAGMA busy_timeout=0');
      // Validate the known schema without writing or migrating the agent database.
      this.db.prepare('SELECT id,session_id,started_at,completed_at,input_tokens,output_tokens,computed_total_tokens FROM model_usage LIMIT 0').all();
      return true;
    } catch { this.close(); return false; }
  }

  read(emit: (sample: UsageSample) => void): void {
    if (!this.db) return;
    try {
      const rows = this.db.prepare(`SELECT id,started_at,completed_at,input_tokens,output_tokens,reasoning_tokens,
        cache_read_input_tokens,cache_creation_input_tokens,computed_total_tokens,provider_total_tokens,model_id,provider_id,
        duration_ms,time_to_first_token_ms,status FROM model_usage
        WHERE session_id=? AND started_at>=? AND completed_at>=? ORDER BY completed_at,id`).all(this.session, this.startedAt, this.since) as any[];
      for (const row of rows) {
        if (this.seen.has(row.id) || !validCount(row.completed_at)) continue;
        const total = validCount(row.provider_total_tokens) ? row.provider_total_tokens : row.computed_total_tokens;
        if (!validCount(total) || !validCount(row.output_tokens) || !validCount(row.input_tokens)) continue;
        let input = row.input_tokens;
        const cache = (validCount(row.cache_read_input_tokens) ? row.cache_read_input_tokens : 0) + (validCount(row.cache_creation_input_tokens) ? row.cache_creation_input_tokens : 0);
        // Older records excluded cache from input. Only normalize when the native total agrees.
        if (input + cache + row.output_tokens === total) input += cache;
        this.seen.add(row.id); this.since = Math.max(this.since, row.completed_at);
        const counts = row.status === 'error' && total === 0 ? {} : { total, input, output: row.output_tokens,
          cacheRead: row.cache_read_input_tokens, cacheWrite: row.cache_creation_input_tokens, reasoning: row.reasoning_tokens };
        emit(timedUsage({ id: `zcode:${row.id}`, scope: 'request', source: 'zcode.model_usage', ...counts,
          model: row.model_id, provider: row.provider_id, durationMs: row.duration_ms, ttftMs: row.time_to_first_token_ms,
          ...(row.status === 'error' ? { error: '模型请求失败' } : {}),
        }, row.started_at, row.completed_at));
      }
    } catch { /* Locked or changed schema: leave existing measurements intact. */ }
  }

  close(): void { this.db?.close(); this.db = null; }
}
