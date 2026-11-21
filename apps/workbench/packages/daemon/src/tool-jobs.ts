import type { DatabaseSync } from 'node:sqlite';

export type ToolJobState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';
export interface ToolArtifact {
  id: string;
  name: string;
  path: string;
  mimeType: string;
  size: number;
  sha256: string;
}
export interface ToolJob {
  id: string;
  owner: string;
  requestId: string;
  fingerprint: string;
  tool: string;
  agent: string;
  threadId: string | null;
  turnId: string | null;
  state: ToolJobState;
  input: Record<string, unknown>;
  summary: string;
  summaryTruncated?: boolean;
  result?: Record<string, unknown>;
  artifacts: ToolArtifact[];
  error: { code: string; message: string } | null;
  createdAt: string;
  updatedAt: string;
}

/** Jobs share the hub database; retries survive restarts without rerunning a paid action. */
export class ToolJobs {
  constructor(private readonly db: DatabaseSync) {
    db.exec(`CREATE TABLE IF NOT EXISTS tool_jobs (
      id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, data TEXT NOT NULL,
      UNIQUE(owner, request_id)
    )`);
  }
  list(): ToolJob[] {
    return (this.db.prepare('SELECT data FROM tool_jobs').all() as { data: string }[]).map(r => JSON.parse(r.data));
  }
  activeCount(): number {
    return (this.db.prepare("SELECT COUNT(*) AS n FROM tool_jobs WHERE json_extract(data, '$.state') IN ('queued', 'running')").get() as { n: number }).n;
  }
  get(id: string): ToolJob | undefined {
    const row = this.db.prepare('SELECT data FROM tool_jobs WHERE id = ?').get(id) as { data: string } | undefined;
    return row ? JSON.parse(row.data) : undefined;
  }
  previous(owner: string, requestId: string): ToolJob | undefined {
    const row = this.db.prepare('SELECT data FROM tool_jobs WHERE owner = ? AND request_id = ?').get(owner, requestId) as { data: string } | undefined;
    return row ? JSON.parse(row.data) : undefined;
  }
  save(job: ToolJob): void {
    this.db.prepare(`INSERT INTO tool_jobs (id, owner, request_id, data) VALUES (?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET data = excluded.data`).run(job.id, job.owner, job.requestId, JSON.stringify(job));
  }
}
