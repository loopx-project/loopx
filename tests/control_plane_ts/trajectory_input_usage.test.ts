import assert from 'node:assert/strict';
import test from 'node:test';
import {summarizeTrajectoryCodexUsage as summarize} from '../../loopx/control_plane/capabilities/trajectory_codex_usage.ts';

function nativeEstimate() {
  const digest = 'a'.repeat(64), other = 'b'.repeat(64);
  const first = tokens(100, 60, 5), second = tokens(300, 200, 5), total = tokens(400, 260, 10);
  const rows = [event('session_meta', {id: 'session'}), event('event_msg', {type: 'task_started', turn_id: 'turn'})];
  for (const [id, usage, cumulative] of [['first', first, first], ['second', second, total]] as const) {
    rows.push(event('token_usage_record', {response_id: id, session_id: 'session', thread_id: 'session',
      turn_id: 'turn', root_turn_id: 'turn', usage, thread_token_usage: cumulative, turn_token_usage: cumulative}),
    event('event_msg', {type: 'token_count', info: {total_token_usage: cumulative, last_token_usage: usage}}));
  }
  rows.push(event('event_msg', {type: 'task_complete', turn_id: 'turn'}));
  const trace: any = {source: {schema_version: 'loopx_codex_input_source_v1', session_id: 'session', goal_id: 'goal',
    source_sha256: digest, text_sha256: digest}, source_artifact_sha256: digest, trace_sha256: other,
    manifest: {schema_version: 1, root_thread_id: 'session', rollout_id: 'session'},
    tokenizer: {name: 'tiktoken', version: '0.12.0', encoding: 'o200k_base'}, events: []};
  for (const [id, usage] of [['first', first], ['second', second]] as const) {
    trace.events.push({schema_version: 1, seq: trace.events.length + 1, rollout_id: 'session', thread_id: 'session',
      codex_turn_id: 'turn', payload: {type: 'inference_started', inference_call_id: id, thread_id: 'session',
        codex_turn_id: 'turn', model: 'native-model', request: {sha256: other, model: 'native-model',
          previous_response_id: null, input: [{id: 'source-item', type: 'message', role: 'user',
            content: [{type: 'input_text', sha256: digest, tokens: 20}]}]}}},
    {schema_version: 1, seq: trace.events.length + 2, rollout_id: 'session', thread_id: 'session', codex_turn_id: 'turn',
      payload: {type: 'inference_completed', inference_call_id: id, response_id: id, upstream_request_id: id,
        response: {sha256: other, response_id: id, upstream_request_id: id, token_usage: usage}}});
  }
  return {trace, rows: rows as any[]};
}
const estimate = (fixture = nativeEstimate()) => summarize({events: fixture.rows.map((r, i) => ({...r, line: i + 1,
  timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, i)).toISOString()})), session_id: 'session',
  input_goal_id: 'goal', loopx_input_trace: fixture.trace});

test('native packet estimate sums repeated consumption against same-request totals and stays labeled', () => {
  const result = estimate(), value = result.loopx_input!;
  assert.equal(value.method, 'tiktoken_o200k_base_input_estimate_v1');
  assert.equal(value.status, 'complete'); if (value.status === 'unavailable') return;
  assert.equal(value.loopx_input_tokens, 40); assert.equal(value.input_fraction, 0.1);
  assert.equal(value.total_input_tokens, 400); assert.equal(value.uncached_input_fraction, null);
  assert.equal(value.loopx_cached_input_tokens, null); assert.equal(value.input_fraction_bounds, null);
  assert.equal(value.estimate_basis?.scope, 'recorded_turn_packets');
  assert.deepEqual(value.estimate_basis?.models, ['native-model']);
  assert.equal(result.input_tokens, 400); assert.equal(result.cached_input_tokens, 260);
});

test('source copies do not become extra LoopX input; changed ids, text and unreplayed deltas stay partial', () => {
  const original = nativeEstimate();
  const copied = structuredClone(original.trace.events[2].payload.request.input[0]); copied.id = 'quoted-copy';
  original.trace.events[2].payload.request.input.push(copied);
  assert.equal(estimate(original).loopx_input?.status, 'complete');
  const a = estimate(original).loopx_input!; if (a.status !== 'unavailable') assert.equal(a.loopx_input_tokens, 40);
  for (const change of [
    (r: any) => {r.input[0].id = 'different-item';},
    (r: any) => {r.input[0].role = 'assistant';},
    (r: any) => {r.input[0].content[0].sha256 = 'c'.repeat(64);},
    (r: any) => {r.previous_response_id = 'unreplayed';},
  ]) {
    const f = nativeEstimate(); change(f.trace.events[2].payload.request);
    const value = estimate(f).loopx_input!; assert.equal(value.status, 'partial');
    if (value.status !== 'unavailable') {assert.equal(value.input_fraction, null); assert.equal(value.attributed_requests, 1);}
  }
});

test('bad native identity, source, sequence, counter and estimator evidence withholds only attribution', () => {
  const mutations = [
    (t: any) => {t.source.goal_id = 'foreign';}, (t: any) => {t.source.session_id = 'foreign';},
    (t: any) => {t.source.source_sha256 = 'f'.repeat(64);},
    (t: any) => {t.events[0].seq = 2;}, (t: any) => {t.events[0].thread_id = 'foreign';},
    (t: any) => {t.events[1].codex_turn_id = 'foreign';},
    (t: any) => {t.events[1].payload.response_id = 'foreign';},
    (t: any) => {t.events[1].payload.response.token_usage = tokens(101, 60, 5);},
    (t: any) => {t.events[0].payload.request.model = 'foreign';},
    (t: any) => {t.events[0].payload.request.input[0].content[0].tokens = true;},
    (t: any) => {t.events[0].payload.request.input[0].content[0].tokens = 101;},
    (t: any) => {t.events[0].payload.request.input.push(structuredClone(t.events[0].payload.request.input[0]));},
    (t: any) => {t.tokenizer.encoding = 'character-count';},
  ];
  for (const mutate of mutations) {
    const f = nativeEstimate(); mutate(f.trace); const result = estimate(f);
    assert.equal(result.loopx_input?.status, 'unavailable'); assert.equal(result.input_tokens, 400);
    assert.equal(result.request_coverage?.status, 'verified');
  }
});

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
