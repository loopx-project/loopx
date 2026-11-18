/**
 * The LoopX steward conversation, on the Python manager service, read through the loochat proxy
 * (`/api/loopx/api/chat/*`). One session per person (context_kind "manager"), turns streamed over
 * SSE. The event stream carries the core's own words; unknown kinds pass through as-is instead of
 * being dropped, so a newer core never renders as silence.
 */

/** One stored message of the manager conversation, as the session snapshot serves it. */
export interface ManagerMessage {
  id: string;
  role: 'user' | 'agent';
  text: string;
  createdAt: string | null;
  turnId: string | null;
}

/** One live activity event of a turn (agent.phase steps, answer deltas, turn bookkeeping). */
export interface ManagerEvent {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
}

export type ManagerTurnState = 'idle' | 'sending' | 'streaming' | 'stopping';

export interface ManagerTurn {
  turnId: string;
  eventsPath: string;
  state: ManagerTurnState;
  /** Activity step labels in arrival order; the last one may still be running. */
  steps: string[];
  /** The answer text accumulated from answer.delta events. */
  answer: string;
  error: string | null;
}

export class ManagerError extends Error {
  constructor(readonly status: number, readonly code: string, message: string, readonly body: Record<string, unknown> = {}) {
    super(message);
  }
}

export interface ManagerAdapter {
  agentId: string;
  displayName: string;
  available: boolean;
  unavailableReason: string | null;
  executable: string | null;
}

export interface ManagerCapabilities {
  selectedAgentId: string | null;
  runtimeProfile: 'trusted_owner' | 'restricted' | null;
  selectionPolicy: 'preferred' | 'pinned' | 'flexible' | null;
  eligibleEndpoints: string[];
  adapters: ManagerAdapter[];
  /** Available adapters the person can switch the steward to under the current policy. */
  availableAlternatives: ManagerAdapter[];
}

export const TRUSTED_OWNER_ENDPOINTS = new Set(['codex', 'antigravity']);

async function call<T>(token: string | undefined, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { Authorization: `Bearer ${token ?? ''}`, ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
  });
  const json = await res.json().catch(() => ({})) as Record<string, unknown>;
  if (!res.ok) {
    const code = typeof json.error_code === 'string' ? json.error_code : `http_${res.status}`;
    throw new ManagerError(res.status, code, typeof json.error === 'string' ? json.error : `HTTP ${res.status}`, json);
  }
  return json as T;
}

/** Resume (or create) the person's manager session. Idempotent on the core. */
export async function openManagerSession(token: string | undefined, signal?: AbortSignal, agentId?: string): Promise<{ sessionId: string; messages: ManagerMessage[] }> {
  const created = await call<{ session_id: string }>(token, '/api/loopx/api/chat/sessions', {
    method: 'POST', body: JSON.stringify({ context_kind: 'manager', ...(agentId ? { agent_id: agentId } : {}) }), signal,
  });
  if (!created.session_id) throw new ManagerError(502, 'bad_session_answer', 'the core did not name the manager session');
  return { sessionId: created.session_id, messages: await readManagerHistory(token, created.session_id, signal) };
}

/** Read the core's adapter readiness and steward channel policy when opening the steward fails. */
export async function readManagerCapabilities(token: string | undefined, failedAgentId?: string | null, signal?: AbortSignal): Promise<ManagerCapabilities | null> {
  try {
    const raw = await call<Record<string, unknown>>(token, '/api/loopx/api/chat/capabilities', { signal });
    const manager = typeof raw.manager === 'object' && raw.manager !== null ? raw.manager as Record<string, unknown> : {};
    const runtime = typeof manager.runtime === 'object' && manager.runtime !== null ? manager.runtime as Record<string, unknown> : {};
    const binding = typeof manager.channel_binding === 'object' && manager.channel_binding !== null ? manager.channel_binding as Record<string, unknown> : {};
    const runtimeProfile = runtime.runtime_profile === 'trusted_owner' || runtime.runtime_profile === 'restricted' ? runtime.runtime_profile : null;
    const selectionPolicy = binding.selection_policy === 'preferred' || binding.selection_policy === 'pinned' || binding.selection_policy === 'flexible'
      ? binding.selection_policy : null;
    const selectedAgentId = typeof binding.executor_endpoint === 'string' && binding.executor_endpoint ? binding.executor_endpoint : (failedAgentId ?? null);
    const eligibleEndpoints = Array.isArray(binding.eligible_endpoints)
      ? binding.eligible_endpoints.filter((x): x is string => typeof x === 'string' && x.length > 0) : [];
    const adapters: ManagerAdapter[] = [];
    for (const item of Array.isArray(raw.adapters) ? raw.adapters : []) {
      if (typeof item !== 'object' || item === null) continue;
      const row = item as Record<string, unknown>;
      const agentId = typeof row.agent_id === 'string' ? row.agent_id : '';
      if (!agentId) continue;
      adapters.push({
        agentId,
        displayName: typeof row.display_name === 'string' && row.display_name ? row.display_name : agentId,
        available: row.available === true,
        unavailableReason: typeof row.unavailable_reason === 'string' ? row.unavailable_reason : null,
        executable: typeof row.executable === 'string' ? row.executable : null,
      });
    }
    const excluded = failedAgentId ?? selectedAgentId;
    const availableAlternatives = selectionPolicy === 'pinned'
      ? []
      : adapters.filter(a => a.available && a.agentId !== excluded && (selectionPolicy !== 'flexible' || eligibleEndpoints.includes(a.agentId)));
    return { selectedAgentId, runtimeProfile, selectionPolicy, eligibleEndpoints, adapters, availableAlternatives };
  } catch {
    return null;
  }
}

async function applyMachineNamespace(token: string | undefined, namespace: string, namespaceConfiguration: Record<string, unknown>, signal?: AbortSignal): Promise<string | null> {
  const preview = await call<{ plan_revision?: unknown }>(token, '/api/loopx/api/chat/machine-configuration/preview', {
    method: 'POST',
    body: JSON.stringify({ namespace, operation: 'upsert', namespace_configuration: namespaceConfiguration }),
    signal,
  });
  const revision = typeof preview.plan_revision === 'string' ? preview.plan_revision : '';
  if (!revision) return null;
  const applied = await call<{ transaction_id?: unknown; rollback_id?: unknown }>(token, '/api/loopx/api/chat/machine-configuration/apply', {
    method: 'POST',
    body: JSON.stringify({ namespace, operation: 'upsert', namespace_configuration: namespaceConfiguration, expected_plan_revision: revision }),
    signal,
  });
  if (typeof applied.transaction_id === 'string' && applied.transaction_id) return applied.transaction_id;
  if (typeof applied.rollback_id === 'string' && applied.rollback_id) return applied.rollback_id;
  return null;
}

async function rollbackMachineTransaction(token: string | undefined, transactionId: string): Promise<void> {
  try {
    const preview = await call<{ rollback_allowed?: unknown; plan_revision?: unknown }>(token, '/api/loopx/api/chat/machine-configuration/rollback', {
      method: 'POST',
      body: JSON.stringify({ transaction_id: transactionId, execute: false }),
    });
    const revision = typeof preview.plan_revision === 'string' ? preview.plan_revision : '';
    if (!revision || preview.rollback_allowed === false) return;
    await call(token, '/api/loopx/api/chat/machine-configuration/rollback', {
      method: 'POST',
      body: JSON.stringify({ transaction_id: transactionId, execute: true, expected_plan_revision: revision }),
    });
  } catch {
    // Best-effort rollback so the original session open error still surfaces.
  }
}

/** Restore the steward machine runtime profile to trusted_owner. */
export async function restoreTrustedOwnerProfile(token: string | undefined, signal?: AbortSignal): Promise<void> {
  await applyMachineNamespace(token, 'manager_runtime', {
    schema_version: 'manager_runtime_profile_v0',
    runtime_profile: 'trusted_owner',
  }, signal);
}

/** Switch the steward to an available Agent and open its session; roll back runtime profile if opening fails. */
export async function switchManagerAgent(
  token: string | undefined,
  agentId: string,
  capabilities?: ManagerCapabilities | null,
  signal?: AbortSignal,
): Promise<{ sessionId: string; messages: ManagerMessage[] }> {
  const priorProfile = capabilities?.runtimeProfile;
  let runtimeTransactionId: string | null = null;

  // Read-only adapters (Claude Code, Kiro CLI, DSH, API endpoints) require the restricted manager runtime profile.
  if (!TRUSTED_OWNER_ENDPOINTS.has(agentId) && priorProfile === 'trusted_owner') {
    try {
      runtimeTransactionId = await applyMachineNamespace(token, 'manager_runtime', {
        schema_version: 'manager_runtime_profile_v0',
        runtime_profile: 'restricted',
      }, signal);
    } catch (applyError) {
      // If Core explicitly rejected the apply (e.g. 409 conflict when Settings was updated concurrently),
      // preserve the newly confirmed setting and do not trigger any compensating write.
      if (applyError instanceof ManagerError) {
        throw applyError;
      }
      // Reconcile unconfirmed switch only when the response was lost (network error) after Core might have committed:
      const readback = await readManagerCapabilities(token, null, signal).catch(() => null);
      if (readback?.runtimeProfile === 'restricted') {
        await applyMachineNamespace(token, 'manager_runtime', {
          schema_version: 'manager_runtime_profile_v0',
          runtime_profile: priorProfile,
        }).catch(() => undefined);
      }
      throw applyError;
    }
  }

  try {
    return await openManagerSession(token, signal, agentId);
  } catch (cause) {
    // Only roll back when a configuration transaction was confirmed and attributable to this switch.
    // Core guards rollback by expected plan revision, preserving newer user settings.
    if (runtimeTransactionId) {
      await rollbackMachineTransaction(token, runtimeTransactionId);
    }
    throw cause;
  }
}

/** The stored conversation, oldest first, ready to render before any new turn runs. */
export async function readManagerHistory(token: string | undefined, sessionId: string, signal?: AbortSignal): Promise<ManagerMessage[]> {
  const snapshot = await call<{ messages?: unknown[] }>(token, `/api/loopx/api/chat/sessions/${encodeURIComponent(sessionId)}`, { signal });
  return parseManagerHistory(snapshot.messages);
}

export function parseManagerHistory(raw: unknown): ManagerMessage[] {
  if (!Array.isArray(raw)) return [];
  const out: ManagerMessage[] = [];
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue;
    const record = item as Record<string, unknown>;
    const role = record.role === 'user' || record.role === 'agent' ? record.role : null;
    const text = typeof record.text === 'string' ? record.text : '';
    if (!role || !text.trim()) continue;
    out.push({
      id: String(record.message_id ?? ''),
      role, text,
      createdAt: typeof record.created_at === 'string' ? record.created_at : null,
      turnId: typeof record.turn_id === 'string' ? record.turn_id : null,
    });
  }
  return out;
}

/** Submit a turn; the core answers 202 with the turn and its event stream. */
export async function sendManagerTurn(token: string | undefined, sessionId: string, text: string, clientTurnId: string, signal?: AbortSignal): Promise<{ turnId: string; eventsPath: string }> {
  const accepted = await call<{ turn_id?: string; events_url?: string }>(token, `/api/loopx/api/chat/sessions/${encodeURIComponent(sessionId)}/turns`, {
    method: 'POST', body: JSON.stringify({ message: text, client_turn_id: clientTurnId }), signal,
  });
  if (!accepted.turn_id || !accepted.events_url) throw new ManagerError(502, 'bad_turn_answer', 'the core did not name the turn');
  return { turnId: accepted.turn_id, eventsPath: proxyPathOfCorePath(accepted.events_url) };
}

/** Ask the running turn to stop. The stream then carries the interrupted turn's terminal event. */
export async function interruptManagerTurn(token: string | undefined, sessionId: string, turnId: string): Promise<void> {
  await call(token, `/api/loopx/api/chat/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/interrupt`, { method: 'POST', body: '{}' });
}

/** The core speaks `/api/...`; the page can only reach it through the loochat proxy's prefix. */
export function proxyPathOfCorePath(path: string): string {
  return path.startsWith('/api/') ? `/api/loopx${path}` : path;
}

export type ManagerEventListener = (event: ManagerEvent) => void;

/** Fold one SSE event into the turn's live state. Unknown kinds become an idle step, shown as text only if they carry a label. */
export function applyManagerEvent(turn: ManagerTurn, event: ManagerEvent): ManagerTurn {
  const payload = event.payload ?? {};
  switch (event.kind) {
    case 'agent.phase': {
      const label = typeof payload.label === 'string' ? payload.label : '';
      if (!label) return turn;
      // The core announces each step at least twice (started, completed); consecutive repeats are one step.
      const steps = turn.steps.length && turn.steps[turn.steps.length - 1] === label ? turn.steps : [...turn.steps, label];
      return { ...turn, steps };
    }
    case 'answer.delta': {
      const text = typeof payload.text === 'string' ? payload.text : '';
      return { ...turn, answer: turn.answer + text };
    }
    case 'answer.final': {
      const response = payload.response as { message?: unknown } | undefined;
      const message = response && typeof response.message === 'string' ? response.message : '';
      return { ...turn, answer: message || turn.answer };
    }
    case 'turn.completed':
      return { ...turn, state: 'idle', steps: [], answer: turn.answer };
    case 'turn.failed':
      return { ...turn, state: 'idle', steps: [], error: errorMessage(payload) ?? 'turn failed' };
    default:
      return turn;
  }
}

function errorMessage(payload: Record<string, unknown>): string | null {
  if (typeof payload.error === 'string' && payload.error.trim()) return payload.error;
  const response = payload.response as { message?: unknown } | undefined;
  if (response && typeof response.message === 'string' && response.message.trim()) return response.message;
  return null;
}

/** The turn statuses after which the core closes the event stream. */
const TERMINAL_KINDS = new Set(['turn.completed', 'turn.failed']);

/**
 * Read one turn's SSE stream with fetch (EventSource cannot send the proxy's Bearer token).
 * `after` resumes from an event cursor. Resolves when the turn ends or the caller aborts.
 */
export async function streamManagerEvents(token: string | undefined, path: string, options: {
  signal: AbortSignal;
  after?: string;
  onEvent: ManagerEventListener;
}): Promise<void> {
  const url = new URL(path, location.origin);
  if (options.after) url.searchParams.set('after', options.after);
  const res = await fetch(url, { headers: { Authorization: `Bearer ${token ?? ''}`, Accept: 'text/event-stream' }, signal: options.signal });
  if (!res.ok || !res.body) throw new ManagerError(res.status, res.ok ? 'no_stream_body' : 'stream_http_error', `the event stream answered ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf('\n\n');
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf('\n\n');
      const event = parseSseFrame(frame);
      if (event) {
        options.onEvent(event);
        if (TERMINAL_KINDS.has(event.kind)) return;
      }
    }
  }
}

/** One `id:/event:/data:` frame, or null for heartbeats and empty frames. */
export function parseSseFrame(frame: string): ManagerEvent | null {
  let id = '';
  let kind = '';
  let data = '';
  for (const line of frame.split('\n')) {
    if (line.startsWith('id:')) id = line.slice(3).trim();
    else if (line.startsWith('event:')) kind = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trimStart();
  }
  if (!kind || kind === 'heartbeat') return null;
  let payload: Record<string, unknown> = {};
  if (data) {
    try {
      const parsed = JSON.parse(data);
      if (parsed && typeof parsed === 'object') {
        // The data frame carries the whole event envelope; its payload field is the event's own.
        const inner = (parsed as { payload?: unknown }).payload;
        payload = inner && typeof inner === 'object' ? inner as Record<string, unknown> : parsed as Record<string, unknown>;
      }
    } catch { /* the core's frames are JSON; a stray frame stays empty rather than failing the stream */ }
  }
  // The frame's own event field is the kind; the payload's copy of it stays in the payload.
  return { id, kind, payload };
}
