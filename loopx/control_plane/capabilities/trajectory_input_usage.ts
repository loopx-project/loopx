/** Bind optional native input observations to the existing request ledger.
 * This estimates only explicitly recorded LoopX Turn packets. It never labels
 * arbitrary prose, a quoted packet, tool output or all compaction as LoopX. */
import type {TrajectoryLoopXInput, TrajectoryRequestCoverage} from './trajectory_assessment_types.ts';

const method = 'tiktoken_o200k_base_input_estimate_v1';
class InputObservationError extends Error {}
const fail = (message: string): never => { throw new InputObservationError(message); };
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail('Missing native input observation.');
  return value as Record<string, unknown>;
}
function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : fail('Missing native input records.');
}
function text(value: unknown): string {
  return typeof value === 'string' && value.length > 0 ? value : fail('Missing native input identity.');
}
function count(value: unknown): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
    ? value : fail('Invalid input token estimate.');
}
function digest(value: unknown): string {
  return typeof value === 'string' && /^[a-f0-9]{64}$/.test(value) ? value : fail('Missing input source digest.');
}

export function estimateTrajectoryLoopXInput(value: unknown, session: string, goal: unknown,
  rows: Array<{type?: unknown; payload?: unknown}>, coverage: TrajectoryRequestCoverage | undefined): TrajectoryLoopXInput | undefined {
  if (value === undefined) return undefined;
  try {
    if (coverage?.status !== 'verified') return fail('Request coverage must reconcile before estimating input.');
    const input = object(value), source = object(input.source), manifest = object(input.manifest);
    if (source.schema_version !== 'loopx_codex_input_source_v1' || source.session_id !== session
      || source.goal_id !== text(goal) || manifest.schema_version !== 1
      || manifest.root_thread_id !== session || manifest.rollout_id !== session) return fail('Input source Goal or native session mismatch.');
    const sourceDigest = digest(source.source_sha256);
    if (sourceDigest !== digest(source.text_sha256)) return fail('Recorded LoopX source changed.');
    digest(input.source_artifact_sha256); digest(input.trace_sha256);
    const tokenizer = object(input.tokenizer);
    if (tokenizer.name !== 'tiktoken' || tokenizer.encoding !== 'o200k_base') return fail('Unsupported input estimator.');
    const version = text(tokenizer.version);
    const native = new Map(rows.filter(row => row.type === 'token_usage_record').map(row => {
      const p = object(row.payload); return [text(p.response_id), p] as const;
    }));
    if (!native.size || native.size !== coverage.unique_requests) return fail('Native request coverage changed.');
    const started = new Map<string, {turn: string; request: Record<string, unknown>}>();
    const completed = new Map<string, {input: number; loopx: number}>();
    const models = new Set<string>();
    let sequence = 0, sourceItem: string | null = null, sourcePart = -1;
    for (const raw of array(input.events)) {
      const row = object(raw), event = object(row.payload);
      if (row.schema_version !== 1 || row.rollout_id !== session || row.seq !== ++sequence) return fail('Native trace sequence or session mismatch.');
      if (!['inference_started', 'inference_completed'].includes(event.type as string)) continue;
      if (row.thread_id !== session) return fail('Native trace request belongs to another session.');
      const id = text(event.inference_call_id), turn = text(row.codex_turn_id);
      if (event.type === 'inference_started') {
        if (started.has(id)) return fail('Duplicate native inference identity.');
        const request = object(event.request);
        digest(request.sha256);
        if (request.model !== event.model || event.thread_id !== session || event.codex_turn_id !== turn) return fail('Native request identity or model mismatch.');
        started.set(id, {turn, request});
      } else {
        const start = started.get(id);
        if (!start || start.turn !== turn) return fail('Native response has no matching request.');
        const response = object(event.response), responseId = text(event.response_id);
        digest(response.sha256);
        if (responseId !== response.response_id || event.upstream_request_id !== response.upstream_request_id) return fail('Native response identity mismatch.');
        const original = native.get(responseId);
        if (!original || original.turn_id !== turn) return fail('Native input response is outside the usage capture.');
        const usage = object(response.token_usage), expected = object(original.usage);
        for (const key of ['input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
          'output_tokens', 'reasoning_output_tokens', 'total_tokens']) {
          if (count(usage[key]) !== expected[key]) return fail('Native response counters disagree with the usage capture.');
        }
        // A compressed websocket request is not a full model-visible input.
        // It stays uncovered until its previous-response lineage is replayed.
        if (start.request.previous_response_id != null) continue;
        const items = array(start.request.input).map(object);
        if (sourceItem === null) {
          const matches: Array<{id: string; part: number}> = [];
          for (const item of items) {
            if (item.type !== 'message' || item.role !== 'user') continue;
            array(item.content).map(object).forEach((part, index) => {
              if (part.type === 'input_text' && part.sha256 === sourceDigest) matches.push({id: text(item.id), part: index});
            });
          }
          if (matches.length > 1) return fail('Recorded source is ambiguous in the native request.');
          if (!matches.length) continue;
          sourceItem = matches[0]!.id; sourcePart = matches[0]!.part;
        }
        const matches = items.filter(item => item.id === sourceItem);
        if (matches.length !== 1 || matches[0]!.type !== 'message' || matches[0]!.role !== 'user') continue;
        const part = object(array(matches[0]!.content)[sourcePart]);
        if (part.type !== 'input_text' || part.sha256 !== sourceDigest) continue;
        const loopx = count(part.tokens), total = count(usage.input_tokens);
        if (loopx > total) return fail('Input estimator exceeds the native request total; no clamping is permitted.');
        const previous = completed.get(responseId);
        if (previous && (previous.input !== total || previous.loopx !== loopx)) return fail('Conflicting input observations for one response.');
        completed.set(responseId, {input: total, loopx}); models.add(text(start.request.model));
      }
    }
    let attributed = 0, loopx = 0;
    for (const request of completed.values()) {
      attributed = count(attributed + request.input); loopx = count(loopx + request.loopx);
    }
    const complete = completed.size === native.size, total = coverage.all_requests.input_tokens;
    return {method, status: complete ? 'complete' : 'partial',
      estimate_basis: {tokenizer: 'tiktoken', version, encoding: 'o200k_base', scope: 'recorded_turn_packets', models: [...models].sort()},
      attributed_requests: completed.size, total_requests: native.size,
      attributed_input_tokens: attributed, total_input_tokens: total, loopx_input_tokens: loopx,
      loopx_cached_input_tokens: null, input_fraction: complete && total > 0 ? loopx / total : null,
      uncached_input_fraction: null, input_fraction_bounds: null};
  } catch (error) {
    if (!(error instanceof InputObservationError)) throw error;
    return {method, status: 'unavailable', reason: error.message};
  }
}
