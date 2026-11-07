import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {summarizeTrajectoryCodexUsage} from '../../loopx/control_plane/capabilities/trajectory_codex_usage.ts';
import {compareTrajectoryUsage, type TrajectoryUsageCapture} from '../../loopx/control_plane/capabilities/trajectory_usage_comparison.ts';

const tokens = (input: number, cached = 0, output = 1) => ({input_tokens: input, cached_input_tokens: cached,
  cache_write_input_tokens: 0, output_tokens: output, reasoning_output_tokens: 0, total_tokens: input + output});
function capture(id = 'one', input = [100, 900], amounts?: number[]) {
  const events: any[] = [{line: 1, type: 'session_meta', payload: {id}},
    {line: 2, type: 'event_msg', payload: {type: 'task_started', turn_id: 'turn'}}];
  const tools: any[] = [];
  let total = tokens(0, 0, 0);
  for (let i = 0; i < input.length; i++) {
    const line = 3 + i * 6, usage = tokens(input[i]!, 0), response = `${id}-${i}`;
    total = tokens(total.input_tokens + usage.input_tokens, 0, total.output_tokens + 1);
    tools.push({line, kind: 'call', call_id: `call-${i}`, argv: i === 0 ? ['loopx', 'quota', 'context-read'] : ['pytest']},
      {line: line + 2, kind: 'output', call_id: `call-${i}`, bytes: 1, sha256: 'a'.repeat(64), unmeasured_parts: 0});
    events.push({line: line + 1, type: 'token_usage_record', payload: {session_id: id, thread_id: id,
      turn_id: 'turn', root_turn_id: 'turn', response_id: response, usage, thread_token_usage: {...total}, turn_token_usage: {...total}}},
      {line: line + 3, type: 'event_msg', payload: {type: 'token_count', info: {total_token_usage: {...total}, last_token_usage: usage}}});
    if (amounts?.[i] !== undefined) events.push({line: line + 4, type: 'loopx_input_token_attribution', payload: {
      method: 'provider_input_token_attribution_v1', session_id: id, thread_id: id, turn_id: 'turn', root_turn_id: 'turn',
      response_id: response, input_tokens: input[i], loopx_input_tokens: amounts[i]}});
  }
  events.push({line: 3 + input.length * 6, type: 'event_msg', payload: {type: 'task_complete', turn_id: 'turn'}});
  events.forEach(e => {e.timestamp = new Date(Date.UTC(2026, 0, 1, 0, 0, e.line)).toISOString();});
  const summarize = (changes = {}) => summarizeTrajectoryCodexUsage({events, tool_context: tools, session_id: id, ...changes});
  return {events, tools, summarize};
}
function pack(id: string, input: number[], attributed?: number[]): TrajectoryUsageCapture {
  return {pack_id: id, title: id, scope: {goal_id: 'goal', case_id: 'case', run_id: id, revision: 'fixed', window: 'first hour'},
    solver_usage: {artifact: 'session.jsonl', artifact_sha256: 'a'.repeat(64), applicability: 'current', reason_code: null,
      projection: capture(id, input, attributed).summarize()}};
}

test('whole-request footprint uses native sums and remains separate from control text and money', () => {
  const c = capture(), usage = c.summarize(), v = usage.loopx_requests!;
  assert.equal(v.status, 'observed'); if (v.status !== 'observed') return;
  assert.equal(v.recognized_requests, 1); assert.equal(v.total_requests, 2);
  assert.equal(v.tokens.input_tokens, 100); assert.equal(v.input_fraction, 0.1);
  assert.equal(v.unclassified_calls, 1); assert.equal(v.mixed_call_requests, 0);
  assert.equal(v.charged_cost, null); assert.equal(v.model_seconds, null);
  assert.equal(usage.loopx_input, undefined, 'Identified commands never manufacture control-text tokens');
  const {loopx_requests, tool_context, ...legacy} = usage;
  assert.deepEqual(c.summarize({tool_context: undefined}), legacy, 'Opt-out retains the existing complete projection');
});

test('multiple calls in one request count once; duplicates do not count twice; mixed calls remain explicit', () => {
  const c = capture();
  c.tools.push({line: 3.5, kind: 'call', call_id: 'extra', argv: ['pytest']});
  assert.equal(c.summarize().loopx_requests?.status, 'unavailable', 'Malformed source ordering is not repaired by sorting');
  const mixed = capture();
  for (const row of [...mixed.events, ...mixed.tools]) if (row.line >= 4) row.line += 3;
  mixed.tools.push({line: 4, kind: 'call', call_id: 'same-response', argv: ['loopx', 'todo', 'list']},
    {line: 5, kind: 'call', call_id: 'task-call', argv: ['pytest']},
    {line: 10, kind: 'output', call_id: 'same-response', bytes: 1, sha256: 'b'.repeat(64), unmeasured_parts: 0},
    {line: 11, kind: 'output', call_id: 'task-call', bytes: 1, sha256: 'c'.repeat(64), unmeasured_parts: 0});
  mixed.tools.sort((a,b) => a.line-b.line);
  const m = mixed.summarize().loopx_requests!;
  assert.equal(m.status, 'observed'); if (m.status !== 'observed') return;
  assert.equal(m.recognized_requests, 1); assert.equal(m.tokens.input_tokens, 100);
  assert.equal(m.mixed_call_requests, 1); assert.equal(m.requests[0]!.call_lines.length, 2);
  const d = capture();
  d.tools[2].argv = ['loopx', 'todo', 'list'];
  d.events.splice(4, 0, {...structuredClone(d.events[2]), line: 7, timestamp: '2026-01-01T00:00:07.000Z'});
  const v = d.summarize().loopx_requests!;
  assert.equal(v.status, 'observed'); if (v.status !== 'observed') return;
  assert.equal(v.recognized_requests, 2); assert.equal(v.tokens.input_tokens, 1000);
  assert.equal(d.summarize().request_coverage?.status, 'verified');
});

test('a late return across another response is unassociated; zero observed matches is distinct from missing observations', () => {
  const c = capture(); c.tools[1].line = 14;
  c.tools.sort((a,b) => a.line-b.line);
  const v = c.summarize().loopx_requests!;
  assert.equal(v.status, 'observed'); if (v.status !== 'observed') return;
  assert.equal(v.recognized_requests, 0); assert.equal(v.input_fraction, 0); assert.equal(v.unmatched_calls, 1);
  assert.equal(c.summarize({tool_context: undefined}).loopx_requests, undefined);
  const mirrored = capture();
  for (const row of [...mirrored.events, ...mirrored.tools]) if (row.line >= 4) row.line += 2;
  mirrored.tools[1].line = 4;
  mirrored.tools.splice(2, 0, {...structuredClone(mirrored.tools[1]), line: 7});
  const duplicate = mirrored.summarize().loopx_requests!;
  assert.equal(duplicate.status, 'observed'); if (duplicate.status !== 'observed') return;
  assert.equal(duplicate.recognized_requests, 0, 'A mirrored early return cannot become a new late return');
  assert.equal(duplicate.unmatched_calls, 1);
  const compacted = capture();
  compacted.events.splice(4, 0, {line: 7, timestamp: '2026-01-01T00:00:07.000Z', type: 'compacted',
    payload: {compaction_response_id: 'one-0', latest_token_usage_record: structuredClone(compacted.events[2].payload)}});
  const compact = compacted.summarize().loopx_requests!;
  assert.equal(compact.status, 'observed'); if (compact.status !== 'observed') return;
  assert.equal(compact.recognized_requests, 0, 'Automatic compaction is never a command-issuing inference');
  assert.equal(compact.unmatched_calls, 1);
});

test('comparison uses sums, preserves partial attribution, and reports a falling share with rising absolute input', () => {
  const left = pack('left', [100, 900], [10, 90]), right = pack('right', [100, 100], [10, 10]);
  const v = compareTrajectoryUsage(left, right);
  assert.equal(v.status, 'observed'); if (v.status !== 'observed') return;
  assert.equal(v.left.input.fraction, 0.1); assert.equal(v.delta.input_percentage_points, 0);
  assert.equal(v.delta.input_tokens, 80); assert.equal(v.left.total.input, 1000);
  const baseline = pack('baseline', [100, 100], [50, 0]);
  const falling = compareTrajectoryUsage(left, baseline);
  assert.equal(falling.status, 'observed'); if (falling.status !== 'observed') return;
  assert.equal(falling.input_share_down_absolute_up, true); assert.equal(falling.delta.input_percentage_points, -15);
  const partial = compareTrajectoryUsage(pack('partial', [100, 900], [10]), right);
  assert.equal(partial.status, 'observed'); if (partial.status !== 'observed') return;
  assert.equal(partial.left.input.tokens, 10); assert.equal(partial.left.input.unattributed_requests, 1);
  assert.equal(partial.delta.input_tokens, null); assert.equal(partial.delta.input_percentage_points, null);
  const weighted = compareTrajectoryUsage(pack('weighted', [100, 900], [50, 0]), right);
  assert.equal(weighted.status, 'observed'); if (weighted.status !== 'observed') return;
  assert.equal(weighted.left.input.fraction, 0.05, '50 / 1000, not the mean of 50% and 0%');
});

test('four real native prefixes retain the Bench owner counts, missing text attribution and the Codec counterexample', () => {
  const fixture = JSON.parse(readFileSync(new URL('../../apps/presentation/dashboard/src/data/fixtures/research-usage-real-captures.json', import.meta.url), 'utf8'));
  const packs: TrajectoryUsageCapture[] = fixture.packs;
  assert.equal(packs.length, 4);
  assert.deepEqual(packs.map(p => p.solver_usage!.projection.request_coverage!.status === 'verified'
    ? p.solver_usage!.projection.request_coverage!.unique_requests : null), [997, 554, 818, 956]);
  assert.deepEqual(packs.map(p => p.solver_usage!.projection.loopx_requests!.status === 'observed'
    ? p.solver_usage!.projection.loopx_requests!.recognized_requests : null), [74, 30, 58, 4]);
  const codec = compareTrajectoryUsage(packs[0]!, packs[1]!);
  assert.equal(codec.status, 'observed'); if (codec.status !== 'observed') return;
  assert.equal(codec.left.commands.uncached, 1500082); assert.equal(codec.right.commands.uncached, 91544);
  assert.equal(codec.delta.command_cached, 3388032);
  assert.equal(codec.delta.command_uncached, 1408538);
  assert.equal(codec.delta.command_output, 4699);
  assert.equal(codec.left.commands.uncached_fraction, 1500082 / 3223153);
  assert.equal(codec.right.commands.uncached_fraction, 91544 / 1215761);
  assert(Math.abs(codec.delta.command_uncached_percentage_points! - 39.01105566635043) < 1e-12,
    'The uncached share rises even though the total-input share falls');
  assert.equal(codec.command_share_down_absolute_up, true);
  assert.equal(codec.left.input.tokens, null); assert.equal(codec.right.input.fraction, null);
  assert.equal(codec.left.input.unattributed_requests, 997); assert.equal(codec.delta.input_percentage_points, null);
  assert.equal(codec.left.commands.charged_cost, null);
  const grid = compareTrajectoryUsage(packs[2]!, packs[3]!);
  assert.equal(grid.status, 'observed'); if (grid.status !== 'observed') return;
  assert.equal(grid.left.commands.uncached, 447095); assert.equal(grid.right.commands.uncached, 11721);
  assert.equal(grid.delta.command_uncached, 435374);
  assert.equal(grid.left.input.unattributed_requests, 818); assert.equal(grid.right.input.unattributed_requests, 956);
  for (let i=0; i<packs.length; i++) {
    assert.equal(packs[i]!.solver_usage!.artifact_sha256, fixture.lineage[i].source_sha256);
    assert.equal(packs[i]!.pack_id, fixture.lineage[i].registered_pack_id);
    assert.notEqual(packs[i]!.pack_id, fixture.lineage[i].original_pack_id, 'New frozen observation does not rewrite its original pack');
  }
});

test('unknown attribution and zero denominator stay unknown; source, method, session and scope mismatches fail closed', () => {
  const zero = compareTrajectoryUsage(pack('a', [0]), pack('b', [0]));
  assert.equal(zero.status, 'observed'); if (zero.status !== 'observed') return;
  assert.equal(zero.left.input.tokens, null); assert.equal(zero.left.commands.input, 0);
  assert.equal(zero.left.commands.fraction, null); assert.equal(zero.delta.command_percentage_points, null);
  assert.equal(zero.delta.command_uncached_percentage_points, null);
  assert.equal(zero.delta.command_cached, 0); assert.equal(zero.delta.command_uncached, 0);
  assert.equal(zero.delta.command_output, 0, 'Measured equal output has a known zero delta even with zero input');
  const knownZero = compareTrajectoryUsage(pack('zero-left', [0, 100]), pack('zero-right', [0, 100]));
  assert.equal(knownZero.status, 'observed'); if (knownZero.status !== 'observed') return;
  assert.equal(knownZero.left.commands.uncached_fraction, 0);
  assert.equal(knownZero.delta.command_uncached_percentage_points, 0, 'A positive denominator preserves measured zero');
  for (const mutate of [
    (p: TrajectoryUsageCapture) => {p.scope!.goal_id = 'other';},
    (p: TrajectoryUsageCapture) => {p.scope!.case_id = 'other';},
    (p: TrajectoryUsageCapture) => {p.scope!.revision = 'other';},
    (p: TrajectoryUsageCapture) => {p.scope!.window = 'later';},
    (p: TrajectoryUsageCapture) => {p.solver_usage!.applicability = 'historical';},
    (p: TrajectoryUsageCapture) => {p.solver_usage!.projection.session_id = 'left';},
    (p: TrajectoryUsageCapture) => {p.solver_usage!.projection.method = 'codex_observed_session_counters_v1';},
    (p: TrajectoryUsageCapture) => {p.solver_usage!.projection.request_coverage = undefined;},
  ]) {const right = pack('right', [100]); mutate(right); assert.equal(compareTrajectoryUsage(pack('left', [100]), right).status, 'unavailable');}
});

test('command deltas stay unknown for missing observations or a changed method', () => {
  const left = pack('left', [100]), right = pack('right', [50]);
  for (const mutate of [
    (p: TrajectoryUsageCapture) => {delete p.solver_usage!.projection.loopx_requests;},
    (p: TrajectoryUsageCapture) => {p.solver_usage!.projection.loopx_requests = {
      method: 'codex_ordered_loopx_requests_v1', status: 'unavailable', reason: 'missing_observations'};},
    (p: TrajectoryUsageCapture) => {(p.solver_usage!.projection.loopx_requests as {method: string}).method += '-changed';},
  ]) {
    const changed = structuredClone(right); mutate(changed);
    const v = compareTrajectoryUsage(left, changed);
    assert.equal(v.status, 'observed'); if (v.status !== 'observed') continue;
    assert.equal(v.delta.command_input, null); assert.equal(v.delta.command_cached, null);
    assert.equal(v.delta.command_uncached, null); assert.equal(v.delta.command_output, null);
    assert.equal(v.delta.command_uncached_percentage_points, null);
    assert.equal(v.delta.total_input, 50, 'Known total usage does not supply missing command attribution');
  }
});
