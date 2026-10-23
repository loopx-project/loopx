import assert from 'node:assert/strict';
import test from 'node:test';
import {summarizeTrajectoryCodexUsage as summarize} from '../../loopx/control_plane/capabilities/trajectory_codex_usage.ts';

const tokens = (input: number, cached: number, output: number) => ({input_tokens: input,
  cached_input_tokens: cached, cache_write_input_tokens: 0, output_tokens: output,
  reasoning_output_tokens: 0, total_tokens: input + output});
const event = (type: string, payload: object) => ({type, payload});
function fixture() {
  const request = (response_id: string, usage: object, total: object) => event('token_usage_record', {
    response_id, session_id: 'session', thread_id: 'session', turn_id: 'turn', root_turn_id: 'turn',
    usage, turn_token_usage: total, thread_token_usage: total});
  const counter = (total: object, last: object) => event('event_msg', {type: 'token_count',
    info: {total_token_usage: total, last_token_usage: last}});
  const compact = request('compact', tokens(20, 10, 7), tokens(120, 70, 17));
  return [event('session_meta', {id: 'session'}), event('event_msg', {type: 'task_started', turn_id: 'turn'}),
    request('one', tokens(100, 60, 10), tokens(100, 60, 10)), counter(tokens(100, 60, 10), tokens(100, 60, 10)),
    compact, event('compacted', {compaction_response_id: 'compact', latest_token_usage_record: compact.payload}),
    counter(tokens(120, 70, 17), tokens(20, 10, 7)),
    request('two', tokens(40, 20, 5), tokens(160, 90, 22)), counter(tokens(160, 90, 22), tokens(40, 20, 5)),
    event('event_msg', {type: 'task_complete', turn_id: 'turn'})] as any[];
}
const attribution = (response_id: string, input_tokens: number, loopx_input_tokens: number,
  loopx_cached_input_tokens?: number) => event('loopx_input_token_attribution', {
  method: 'provider_input_token_attribution_v1', response_id, session_id: 'session', thread_id: 'session',
  turn_id: 'turn', root_turn_id: 'turn', input_tokens, loopx_input_tokens,
  ...(loopx_cached_input_tokens === undefined ? {} : {loopx_cached_input_tokens})});
const run = (rows: any[], observed = false) => summarize({session_id: 'session', include_incomplete: observed,
  events: rows.map((row, i) => ({...row, line: i + 1, timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, i)).toISOString()}))});
const complete = () => [...fixture(), attribution('one', 100, 20, 12),
  attribution('compact', 20, 5, 4), attribution('two', 40, 15, 10)];

test('packet share sums same-request tokens including cache, rather than averaging request percentages', () => {
  const a = run(complete()).loopx_input!;
  assert.equal(a.status, 'complete'); if (a.status === 'unavailable') return;
  assert.equal(a.loopx_input_tokens, 40); assert.equal(a.total_input_tokens, 160);
  assert.equal(a.input_fraction, 0.25); assert.equal(a.uncached_input_fraction, 0.2);
  assert.equal(a.loopx_cached_input_tokens, 26);
  assert.equal(a.attributed_requests, 3); assert.equal(a.total_requests, 3);
  assert.deepEqual(a.input_fraction_bounds, [0.25, 0.25]);
  assert.equal(run(complete()).input_tokens, 160);
});

test('partial attribution keeps the whole-capture percentage unknown with honest possible bounds', () => {
  const a = run([...fixture(), attribution('one', 100, 20)]).loopx_input!;
  assert.equal(a.status, 'partial'); if (a.status === 'unavailable') return;
  assert.equal(a.input_fraction, null); assert.equal(a.uncached_input_fraction, null);
  assert.equal(a.loopx_cached_input_tokens, null);
  assert.equal(a.attributed_requests, 1); assert.equal(a.attributed_input_tokens, 100);
  assert.deepEqual(a.input_fraction_bounds, [0.125, 0.5]);
});

test('missing capture is unknown, and an explicitly measured zero remains zero', () => {
  assert.equal(run(fixture()).loopx_input, undefined);
  const zero = run([...fixture(), attribution('one', 100, 0, 0),
    attribution('compact', 20, 0, 0), attribution('two', 40, 0, 0)]).loopx_input!;
  assert.equal(zero.status, 'complete'); if (zero.status === 'unavailable') return;
  assert.equal(zero.input_fraction, 0); assert.equal(zero.loopx_input_tokens, 0);
});

test('response mirrors and repeated attribution deduplicate while reprocessed packet input counts in each request', () => {
  const rows = complete(); rows.push(structuredClone(rows[2]), structuredClone(rows[10]));
  assert.deepEqual(run(rows).loopx_input, run(complete()).loopx_input);
});

test('invalid attribution does not overwrite independently reconciled request or session counters', () => {
  const cases: Array<(rows: any[]) => void> = [
    rows => {rows[10].payload.response_id = 'missing';},
    rows => {rows[10].payload.session_id = 'foreign';},
    rows => {rows[10].payload.turn_id = 'foreign';},
    rows => {rows[10].payload.root_turn_id = 'foreign';},
    rows => {rows[10].payload.method = 'characters_divided_by_four';},
    rows => {rows[10].payload.input_tokens = 101;},
    rows => {rows[10].payload.loopx_input_tokens = 101;},
    rows => {rows[10].payload.loopx_input_tokens = true;},
    rows => {rows[10].payload.loopx_input_tokens = -1;},
    rows => {rows[10].payload.loopx_input_tokens = 1.5;},
    rows => {rows[10].payload.loopx_cached_input_tokens = 21;},
    rows => {rows[10].payload.loopx_cached_input_tokens = null;},
    rows => {rows[10].payload.loopx_input_tokens = 90; rows[10].payload.loopx_cached_input_tokens = 0;},
    rows => {const other = structuredClone(rows[10]); other.payload.loopx_input_tokens++; rows.push(other);},
  ];
  for (const mutate of cases) {
    const rows = complete(); mutate(rows); const a = run(rows);
    assert.equal(a.loopx_input?.status, 'unavailable');
    assert.equal(a.request_coverage?.status, 'verified'); assert.equal(a.input_tokens, 160);
    assert.equal('input_fraction' in a.loopx_input!, false);
  }
});

test('request gaps withhold attribution, and an unfinished Turn remains explicitly unfinished', () => {
  const rows = complete(); rows[2].payload.thread_token_usage = tokens(101, 60, 10);
  assert.equal(run(rows).loopx_input?.status, 'unavailable');
  const open = complete(); open.splice(9, 1);
  assert.throws(() => run(open), /completed Turn/);
  const a = run(open, true); assert.equal(a.loopx_input?.status, 'complete');
  assert.equal(a.incomplete_turn?.turn_id, 'turn');
});

test('a zero denominator is undefined, and unknown cache attribution cannot become uncached savings', () => {
  const rows = fixture().slice(0, 4);
  rows[2].payload.usage = tokens(0, 0, 0);
  rows[2].payload.thread_token_usage = tokens(0, 0, 0);
  rows[2].payload.turn_token_usage = tokens(0, 0, 0);
  rows[3].payload.info = {total_token_usage: tokens(0, 0, 0), last_token_usage: tokens(0, 0, 0)};
  rows.push(event('event_msg', {type: 'task_complete', turn_id: 'turn'}), attribution('one', 0, 0));
  const zero = run(rows).loopx_input!;
  assert.equal(zero.status, 'complete'); if (zero.status === 'unavailable') return;
  assert.equal(zero.input_fraction, null); assert.equal(zero.input_fraction_bounds, null);
  const withoutCache = complete(); delete withoutCache[10].payload.loopx_cached_input_tokens;
  const a = run(withoutCache).loopx_input!;
  assert.equal(a.status, 'complete'); if (a.status === 'unavailable') return;
  assert.equal(a.input_fraction, 0.25); assert.equal(a.uncached_input_fraction, null);
});
