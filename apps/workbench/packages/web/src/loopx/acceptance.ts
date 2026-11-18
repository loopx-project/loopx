/**
 * Goal acceptance as the core owns it: outcome requirements, the configured checks and their last receipt,
 * the read-only observation (gaps, guards, next action) and the one write path (`/api/chat/goal-acceptance`).
 * Everything here is readback. The core decides what is accepted; this module never infers more than it says.
 */
import { ActionError } from './actions';

export type AcceptanceStatus = 'unverified' | 'stale' | 'failed' | 'partial' | 'accepted' | 'held';
export type OracleState = 'unverified' | 'recorded' | 'current' | 'stale' | 'failed' | 'unavailable';

export interface OutcomeRequirement {
  id: string;
  description: string;
  status: AcceptanceStatus;
  /** The agent's own review, when it wrote one. Never an independent check. */
  review: { conclusion: 'met' | 'unmet' | 'uncertain'; note: string } | null;
}
export interface Outcome { id: string; objective: string; requirements: OutcomeRequirement[]; openWork: number }

export interface Criterion { id: string; description: string; oracleState: OracleState | null }
export interface CheckResult { criterionId: string; passed: boolean; exitCode: number | null }
export interface Receipt { operationId: string; contractRevision: number; contractDigest: string; todoId: string | null; results: CheckResult[] }

/** Checks the owner configured. Absent when only the default outcome requirements exist. */
export interface Checks {
  revision: number;
  digest: string;
  objective: string;
  status: AcceptanceStatus;
  scope: 'goal' | 'all_advancement' | 'selected_work' | null;
  criteria: Criterion[];
  receipt: Receipt | null;
}

export interface Contract {
  lifecycleState: string | null;
  outcomes: Outcome[];
  checks: Checks | null;
}

export interface Gap { reason: string | null; resolutionHint: string | null; evidenceRequired: string | null; owner: string | null; failedChecks: number }
export interface Guard { reason: string | null; evidenceRequired: string | null; scope: string | null; todoId: string | null }
export interface Observation {
  goalId: string;
  coverage: 'partial' | 'unavailable';
  missingSources: string[];
  truncated: boolean;
  gaps: Gap[];
  guards: Guard[];
  nextAction: string | null;
  historicalCount: number;
  contract: Contract | null;
}

export interface Readback { goalId: string; providerRevision: string; contract: Contract | null }

const record = (v: unknown): Record<string, unknown> => (typeof v === 'object' && v !== null && !Array.isArray(v) ? v as Record<string, unknown> : {});
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const text = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);
const STATUSES: AcceptanceStatus[] = ['unverified', 'stale', 'failed', 'partial', 'accepted', 'held'];
const status = (v: unknown): AcceptanceStatus => STATUSES.includes(v as AcceptanceStatus) ? v as AcceptanceStatus : 'unverified';

function parseOutcomes(raw: unknown): Outcome[] {
  return list(record(raw).outcomes).map(o => {
    const outcome = record(o);
    return {
      id: String(outcome.id ?? ''),
      objective: text(outcome.objective) ?? '',
      requirements: list(outcome.requirements).map(r => {
        const req = record(r), review = record(req.agent_review);
        const c = review.conclusion;
        const conclusion: 'met' | 'unmet' | 'uncertain' | null = c === 'met' || c === 'unmet' || c === 'uncertain' ? c : null;
        return {
          id: String(req.id ?? ''), description: text(req.description) ?? '', status: status(req.status),
          review: conclusion ? { conclusion, note: text(review.note) ?? '' } : null,
        };
      }),
      openWork: list(outcome.work).filter(w => record(w).status !== 'done').length,
    };
  }).filter(o => o.id);
}

/** The contract as the owner supplies it; `{}` (nothing declared) is a contract with nothing in it, not an error. */
export function parseContract(raw: unknown): Contract | null {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null;
  const c = record(raw);
  const outcomes = parseOutcomes(c.outcome_requirements);
  let checks: Checks | null = null;
  if (Array.isArray(c.criteria)) {
    const v = record(c.verification);
    const scope = record(c.scope).kind;
    checks = {
      revision: Number(c.revision ?? 0), digest: String(c.digest ?? ''), objective: text(c.objective) ?? '', status: status(c.status),
      scope: scope === 'goal' || scope === 'all_advancement' || scope === 'selected_work' ? scope : null,
      criteria: c.criteria.map(item => {
        const crit = record(item), oracle = record(crit.oracle);
        return { id: String(crit.id ?? ''), description: text(crit.description) ?? '', oracleState: typeof oracle.state === 'string' ? oracle.state as OracleState : null };
      }),
      receipt: typeof v.operation_id === 'string' ? {
        operationId: v.operation_id, contractRevision: Number(v.contract_revision ?? 0), contractDigest: String(v.contract_digest ?? ''),
        todoId: text(v.todo_id),
        results: list(v.results).map(r => { const x = record(r); return { criterionId: String(x.criterion_id ?? ''), passed: x.passed === true, exitCode: typeof x.exit_code === 'number' ? x.exit_code : null }; }),
      } : null,
    };
  }
  return { lifecycleState: text(c.lifecycle_state), outcomes, checks };
}

export function parseObservation(raw: unknown): Observation | null {
  const o = record(raw);
  if (o.schema_version !== 'goal_acceptance_observation_projection_v0' || typeof o.goal_id !== 'string') return null;
  return {
    goalId: o.goal_id,
    coverage: o.coverage === 'unavailable' ? 'unavailable' : 'partial',
    missingSources: list(o.missing_sources).filter((s): s is string => typeof s === 'string'),
    truncated: o.truncated === true,
    gaps: list(o.acceptance_gaps).map(g => {
      const gap = record(g), parts = record(gap.component_checks);
      return { reason: text(gap.reason), resolutionHint: text(gap.resolution_hint), evidenceRequired: text(gap.evidence_required), owner: text(gap.owner), failedChecks: Object.values(parts).filter(v => v === false).length };
    }),
    guards: list(o.guards).map(g => { const x = record(g); return { reason: text(x.reason), evidenceRequired: text(x.evidence_required), scope: text(x.decision_scope), todoId: text(x.todo_id) }; }),
    nextAction: text(o.next_action),
    historicalCount: list(o.historical_progress).length,
    contract: parseContract(o.goal_acceptance_contract),
  };
}

/**
 * One criterion's state from the last receipt. A receipt only counts for the contract revision it ran against;
 * a check run against one task is partial, a stale or failed oracle overrides a pass.
 */
export function criterionStates(checks: Checks): Array<Criterion & { state: AcceptanceStatus }> {
  const receipt = checks.receipt;
  const same = receipt?.contractRevision === checks.revision && receipt.contractDigest === checks.digest;
  const results = new Map(receipt?.results.map(r => [r.criterionId, r.passed]));
  return checks.criteria.map(c => {
    let state: AcceptanceStatus = 'unverified';
    if (checks.status === 'held') state = 'held';
    else if (checks.status !== 'unverified' && receipt && results.has(c.id)) {
      if (checks.status === 'stale' || !same) state = 'stale';
      else if (results.get(c.id) === false) state = 'failed';
      else if (receipt.todoId !== null || checks.status === 'partial') state = 'partial';
      else state = 'accepted';
    }
    if ((state === 'accepted' || state === 'partial') && c.oracleState) {
      if (c.oracleState === 'stale') state = 'stale';
      else if (c.oracleState === 'failed') state = 'failed';
      else if (c.oracleState !== 'current') state = 'unverified';
    }
    return { ...c, state };
  });
}

/** What to say about the checks as a whole: an "accepted" that has gone stale or failed underneath is not accepted. */
export function displayStatus(checks: Checks): AcceptanceStatus {
  if (checks.status !== 'accepted' && checks.status !== 'partial') return checks.status;
  const states = criterionStates(checks).map(c => c.state);
  if (states.includes('failed')) return 'failed';
  if (states.includes('stale')) return 'stale';
  if (states.includes('unverified')) return 'unverified';
  return checks.status;
}

// ---------------------------------------------------------------- the core

async function call<T>(token: string | undefined, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { Authorization: `Bearer ${token ?? ''}`, ...(init.body ? { 'Content-Type': 'application/json' } : {}) },
  });
  const json = await res.json().catch(() => ({})) as Record<string, unknown>;
  if (!res.ok) throw new ActionError(res.status, typeof json.error_code === 'string' ? json.error_code : `http_${res.status}`, typeof json.error === 'string' ? json.error : `HTTP ${res.status}`);
  return json as T;
}

const ACCEPTANCE = '/api/loopx/api/chat/goal-acceptance';

function parseReadback(goalId: string, raw: Record<string, unknown>): Readback {
  if (raw.ok !== true || typeof raw.provider_revision !== 'string' || raw.goal_id !== goalId) throw new ActionError(502, 'acceptance_source_changed', 'Acceptance answered for another Goal');
  return { goalId, providerRevision: raw.provider_revision, contract: parseContract(raw.goal_acceptance_contract) };
}

export const readAcceptance = (token: string | undefined, goalId: string, signal?: AbortSignal): Promise<Readback> =>
  call<Record<string, unknown>>(token, `${ACCEPTANCE}?goal_id=${encodeURIComponent(goalId)}`, { signal }).then(raw => parseReadback(goalId, raw));

export type AcceptanceOperation = 'verify' | 'clear-checks';

/** Run the owner's checks (on this machine) or remove the check configuration. The revision is the one just read; a changed source is refused. */
export const operateAcceptance = (token: string | undefined, goalId: string, action: AcceptanceOperation, providerRevision: string): Promise<Readback> =>
  call<Record<string, unknown>>(token, ACCEPTANCE, {
    method: 'POST',
    body: JSON.stringify({
      goal_id: goalId, action, expected_provider_revision: providerRevision,
      ...(action === 'verify' ? {} : { operation_id: `acceptance-ui:${crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}` }),
    }),
  }).then(raw => parseReadback(goalId, raw));

/** A check plan an agent prepared: nothing runs until the person applies it. */
export interface PreparedPlan {
  proposalId: string;
  objective: string;
  scope: 'goal' | 'all_advancement' | 'selected_work' | null;
  selectedTasks: number;
  criteria: Array<{ id: string; description: string; command: string; outputs: string[] }>;
}

/** The newest applicable `goal.acceptance.check` proposal for this Goal. Two with the same creation time are ambiguous and count as none. */
export function pickPreparedPlan(proposals: unknown[], goalId: string): PreparedPlan | null {
  const own = proposals.map(record).filter(p => p.action_kind === 'goal.acceptance.check'
    && record(p.context).kind === 'goal' && record(p.context).goal_id === goalId && record(p.normalized_parameters).goal_id === goalId)
    .sort((a, b) => String(b.created_at ?? '').localeCompare(String(a.created_at ?? '')));
  const p = own[0];
  if (!p || (own[1] && own[1].created_at === p.created_at)) return null;
  if (p.permission_classification !== 'local_command_execution' || !['preview_ready', 'deferred'].includes(String(p.status))
    || p.gate || p.error || !list(p.available_transitions).includes('apply')) return null;
  const params = record(p.normalized_parameters);
  const given = record(params.document);
  const doc = given.outcome_id === undefined ? given : record(params.resolved_document);
  const criteria = list(doc.criteria).map(c => {
    const crit = record(c);
    return {
      id: String(crit.id ?? ''), description: text(crit.description) ?? '',
      command: list(crit.validation_argv).map(a => JSON.stringify(String(a))).join(' '),
      outputs: list(record(crit.oracle).output_refs).filter((r): r is string => typeof r === 'string'),
    };
  });
  const objective = text(doc.objective);
  if (!objective || !criteria.length || criteria.some(c => !c.command)) return null;
  const scope = record(doc.scope);
  return {
    proposalId: String(p.proposal_id), objective,
    scope: scope.kind === 'goal' || scope.kind === 'all_advancement' || scope.kind === 'selected_work' ? scope.kind : null,
    selectedTasks: list(scope.todo_ids).length, criteria,
  };
}

export const readPreparedPlan = (token: string | undefined, goalId: string, signal?: AbortSignal): Promise<PreparedPlan | null> =>
  call<{ proposals?: unknown[] }>(token, `/api/loopx/api/actions?goal_id=${encodeURIComponent(goalId)}`, { signal }).then(r => pickPreparedPlan(r.proposals ?? [], goalId));

/** Adopt the plan and run its checks: the core saves it, then verifies. Anything but "applied" is an error. */
export async function adoptPlan(token: string | undefined, proposalId: string): Promise<void> {
  const r = await call<{ proposal?: { status?: string } }>(token, `/api/loopx/api/actions/${encodeURIComponent(proposalId)}/apply`, { method: 'POST', body: '{}' });
  if (r.proposal?.status !== 'applied') throw new ActionError(409, 'plan_changed', 'The prepared plan changed');
}
