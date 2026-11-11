/** Descriptive request footprint over the same reconciled native capture.
 * A call must precede one receipt and return before the next receipt in that
 * Turn. This conservative order association is not provider input attribution,
 * pure-control classification, complete workflow cost or a savings estimate. */
import type {TrajectoryLoopXRequests, TrajectoryRequestCoverage, TrajectoryRequestTokens} from './trajectory_assessment_types.ts';
import {isIdentifiedTurnAbort} from './trajectory_request_usage.ts';
import {trajectoryLoopXOperation} from './trajectory_tool_context.ts';

type Row = {line: number; type?: string; payload?: Record<string, any>};
type Call = {line: number; operation: string | null; response?: string; returned: boolean};
const keys: Array<keyof TrajectoryRequestTokens> = ['input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
  'output_tokens', 'reasoning_output_tokens', 'total_tokens'];

/** Empty recognized sums describe coverage, not usage, if any calls are unresolved.
 * Also used when reading frozen captures that stored zero before this distinction. */
export function hasObservedTrajectoryLoopXRequestUsage(value: Pick<Extract<TrajectoryLoopXRequests, {status: 'observed'}>,
  'recognized_requests' | 'unmatched_calls' | 'unclassified_calls'>): boolean {
  return value.recognized_requests > 0 || (value.unmatched_calls === 0 && value.unclassified_calls === 0);
}

export function summarizeTrajectoryLoopXRequests(rows: Row[], tools: unknown,
  coverage: TrajectoryRequestCoverage | undefined): TrajectoryLoopXRequests | undefined {
  if (tools === undefined) return undefined;
  const method = 'codex_ordered_loopx_requests_v1';
  try {
    if (!Array.isArray(tools) || coverage?.status !== 'verified') throw new Error('Reconciled request and tool observations required.');
    const ordered = [...rows.filter(r => r.type === 'token_usage_record'
      || r.type === 'event_msg' && (['task_started', 'task_complete'].includes(r.payload?.type)
        || isIdentifiedTurnAbort(r.payload))),
      ...tools.map(r => ({...r, tool: true}))].sort((a, b) => a.line - b.line);
    const calls = new Map<string, Call>(), seen = new Set<string>(), outputs = new Set<string>();
    const compactions = new Set(rows.filter(r => r.type === 'compacted').map(r => r.payload?.compaction_response_id));
    const groups = new Map<string, {row: Row; calls: Call[]; other: number}>();
    let active: string | null = null, pending: Call[] = [], current: string | null = null, last = 0;
    const finish = () => {pending = []; current = null;};
    for (const row of ordered) {
      if (!Number.isSafeInteger(row.line) || row.line <= last) throw new Error('Ambiguous observation order.');
      last = row.line;
      if (row.tool) {
        if (row.kind === 'call') {
          if (!active || typeof row.call_id !== 'string' || !row.call_id || calls.has(row.call_id)) throw new Error('Ambiguous call identity or Turn.');
          const call: Call = {line: row.line, operation: trajectoryLoopXOperation(row.argv), returned: false};
          calls.set(row.call_id, call); pending.push(call);
        } else if (row.kind === 'output') {
          if (outputs.has(row.call_id)) continue;
          outputs.add(row.call_id);
          const call = calls.get(row.call_id);
          // A return after another response cannot establish the association.
          if (call?.response && call.response === current) call.returned = true;
        }
        continue;
      }
      const p = row.payload!;
      if (row.type === 'event_msg') {
        finish(); active = p.type === 'task_started' ? p.turn_id : null;
      } else {
        if (seen.has(p.response_id)) continue; // Reconciliation already checks duplicate equality.
        if (!active || active !== p.turn_id) throw new Error('Request outside the observed Turn.');
        seen.add(p.response_id);
        current = p.response_id;
        const bracket = pending; pending = [];
        // Automatic compaction does not issue shell commands. An intervening
        // compaction cannot repair an ambiguous call/response association.
        if (!compactions.has(current!)) {
          for (const call of bracket) call.response = current!;
          groups.set(current!, {row, calls: bracket, other: bracket.filter(c => !c.operation).length});
        }
      }
    }
    if (seen.size !== coverage.unique_requests) throw new Error('Request coverage changed.');
    const requests: Extract<TrajectoryLoopXRequests, {status: 'observed'}>['requests'] = [];
    const tokens = Object.fromEntries(keys.map(k => [k, 0])) as unknown as TrajectoryRequestTokens;
    for (const [id, group] of groups) {
      const recognized = group.calls.filter(c => c.operation && c.returned);
      if (!recognized.length) continue;
      const usage = group.row.payload!.usage as TrajectoryRequestTokens;
      for (const key of keys) {
        tokens[key] += usage[key];
        if (!Number.isSafeInteger(tokens[key])) throw new Error('Request sum exceeds exact integer range.');
      }
      requests.push({response_id: id, turn_id: group.row.payload!.turn_id, line: group.row.line,
        call_lines: recognized.map(c => c.line), operations: [...new Set(recognized.map(c => c.operation!))].sort(),
        other_calls: group.other, tokens: {...usage}});
    }
    const all = coverage.all_requests, uncached = all.input_tokens - all.cached_input_tokens;
    const recognition = {recognized_requests: requests.length,
      unmatched_calls: [...calls.values()].filter(c => c.operation && !c.returned).length,
      unclassified_calls: [...calls.values()].filter(c => !c.operation).length};
    const measured = hasObservedTrajectoryLoopXRequestUsage(recognition);
    return {method, status: 'observed', total_requests: seen.size, ...recognition,
      mixed_call_requests: requests.filter(r => r.other_calls > 0).length,
      tokens, input_fraction: measured && all.input_tokens > 0 ? tokens.input_tokens / all.input_tokens : null,
      uncached_input_fraction: measured && uncached > 0 ? (tokens.input_tokens - tokens.cached_input_tokens) / uncached : null,
      model_seconds: null, charged_cost: null, requests};
  } catch (error) {
    return {method, status: 'unavailable', reason: error instanceof Error ? error.message : 'Request association unavailable.'};
  }
}
