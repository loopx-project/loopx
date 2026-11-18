/**
 * The core's typed actions (`/api/loopx/api/actions`), used for the Goal lifecycle: the UI
 * previews, the person confirms what the preview said, and only then does the apply run. This
 * module is deliberately narrow — `goal.lifecycle` with stop / resume / delete is all the work
 * pages need; other action kinds stay the manager conversation's to propose.
 */

export type LifecycleOperation = 'stop' | 'resume' | 'delete';

export interface LifecycleProposal {
  proposalId: string;
  summary: string;
  status: string;
  /** What the core checked before offering this transition; shown as the review's own words. */
  evidence: string[];
  stale: boolean;
  error: string | null;
}

export class ActionError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
  }
}

async function call<T>(token: string | undefined, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { Authorization: `Bearer ${token ?? ''}`, ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
  });
  const json = await res.json().catch(() => ({})) as Record<string, unknown>;
  if (!res.ok) {
    const code = typeof json.error_code === 'string' ? json.error_code : `http_${res.status}`;
    const detail = typeof json.error === 'string' && json.error.trim() ? json.error : `HTTP ${res.status}`;
    throw new ActionError(res.status, code, detail);
  }
  return json as T;
}

function parseProposal(raw: unknown): LifecycleProposal {
  if (typeof raw !== 'object' || raw === null) throw new ActionError(502, 'bad_proposal', 'the core did not return a proposal');
  const record = raw as Record<string, unknown>;
  if (typeof record.proposal_id !== 'string' || !record.proposal_id) throw new ActionError(502, 'bad_proposal', 'the proposal has no id');
  return {
    proposalId: record.proposal_id,
    summary: typeof record.summary === 'string' ? record.summary : '',
    status: typeof record.status === 'string' ? record.status : 'unknown',
    evidence: Array.isArray(record.validation_evidence)
      ? record.validation_evidence.flatMap(item => (typeof item === 'string' && item.trim() ? [item] : []))
      : [],
    stale: record.status === 'stale',
    error: record.error && typeof record.error === 'object' && typeof (record.error as { message?: unknown }).message === 'string'
      ? (record.error as { message: string }).message
      : null,
  };
}

/** Preview one lifecycle transition. Nothing changes on the Goal until the apply. */
export async function previewLifecycle(token: string | undefined, goalId: string, operation: LifecycleOperation, options: { summary: string; signal?: AbortSignal }): Promise<LifecycleProposal> {
  const envelope = await call<{ proposal?: unknown }>(token, '/api/loopx/api/actions/preview', {
    method: 'POST', signal: options.signal,
    body: JSON.stringify({
      action_kind: 'goal.lifecycle',
      summary: options.summary,
      context: { kind: 'goal', goal_id: goalId },
      normalized_parameters: { goal_id: goalId, operation },
      // The core requires one compact opaque id per preview; a fresh UUID is exactly that.
      idempotency_key: `wb-${(crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replaceAll('-', '').slice(0, 24)}`,
    }),
  });
  return parseProposal(envelope.proposal);
}

/** Apply a previewed transition. A stale or gated proposal comes back as an error, never as success. */
export async function applyLifecycle(token: string | undefined, proposalId: string): Promise<LifecycleProposal> {
  const envelope = await call<{ proposal?: unknown }>(token, `/api/loopx/api/actions/${encodeURIComponent(proposalId)}/apply`, {
    method: 'POST', body: '{}',
  });
  return parseProposal(envelope.proposal);
}
