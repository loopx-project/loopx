import assert from 'node:assert/strict';
import test from 'node:test';
import {summarizeTrajectoryCodexUsage as summarize} from '../../loopx/control_plane/capabilities/trajectory_codex_usage.ts';
import {reconcileTrajectoryRequests as reconcile} from '../../loopx/control_plane/capabilities/trajectory_request_usage.ts';

const tokens = (input: number, cached: number, output: number) => ({input_tokens: input,
  cached_input_tokens: cached, output_tokens: output, reasoning_output_tokens: 0,
  cache_write_input_tokens: 0, total_tokens: input + output});
const response = (id: string, turn: string, usage: object, thread: object, turnUsage = usage) => ({
  type: 'token_usage_record', payload: {session_id: 'session', thread_id: 'session', response_id: id,
    turn_id: turn, root_turn_id: turn, usage, thread_token_usage: thread, turn_token_usage: turnUsage}});
const event = (type: string, extra: object = {}) => ({type: 'event_msg', payload: {type, ...extra}});
const counter = (total: object, last = total) => event('token_count', {info: {total_token_usage: total, last_token_usage: last}});
function fixture() {
  const compact = response('compact', 'a', tokens(20, 10, 7), tokens(120, 70, 17), tokens(120, 70, 17));
  return [
    {type: 'session_meta', payload: {id: 'session'}}, event('task_started', {turn_id: 'a'}),
    response('one', 'a', tokens(100, 60, 10), tokens(100, 60, 10)), counter(tokens(100, 60, 10)),
    compact, {type: 'compacted', payload: {compaction_response_id: 'compact', latest_token_usage_record: compact.payload}},
    event('task_complete', {turn_id: 'a'}), event('task_started', {turn_id: 'b'}),
    response('two', 'b', tokens(40, 20, 5), tokens(160, 90, 22)), counter(tokens(140, 80, 15), tokens(40, 20, 5)),
    event('task_complete', {turn_id: 'b'}),
  ].map((e, i) => ({...e, line: i + 1, timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, i)).toISOString()})) as any[];
}
const run = (events = fixture(), include_incomplete = false) => summarize({events, session_id: 'session', include_incomplete});
const renumber = (es: any[]) => es.map((e,i) => ({...e, line:i+1}));

test('request reconciliation accounts for compaction once and preserves the legacy counter basis', () => {
  const result = run(), c = result.request_coverage!;
  assert.equal(result.input_tokens, 140); assert.equal(result.output_tokens, 15);
  assert.equal(c.status, 'verified'); if (c.status !== 'verified') return;
  assert.equal(c.unique_requests, 3); assert.equal(c.compaction_requests, 1);
  assert.deepEqual(c.all_requests, tokens(160, 90, 22)); assert.deepEqual(c.compaction, tokens(20, 10, 7));
  assert.equal(c.counter_observations, 2);
  assert.equal(c.counter_basis, 'ordinary_requests');
  const oldRows = fixture().filter(e => !['token_usage_record', 'compacted'].includes(e.type));
  const {request_coverage, ...legacy} = result;
  assert.deepEqual(run(oldRows), legacy);
  // Old persisted projections can be read without retroactively manufacturing coverage.
  assert.equal(run(oldRows).request_coverage, undefined);
});

test('an ambiguous early prefix does not reject coherent compaction-inclusive counters', () => {
  const es = fixture();
  es[9].payload.info.total_token_usage = tokens(160, 90, 22);
  es[9].payload.info.last_token_usage = tokens(60, 30, 12);
  const result = run(es), c = result.request_coverage!;
  assert.equal(result.input_tokens, 160); assert.equal(result.output_tokens, 22);
  assert.equal(c.status, 'verified'); if (c.status !== 'verified') return;
  assert.equal(c.counter_basis, 'all_requests');
  assert.deepEqual(c.all_requests, tokens(160, 90, 22));
  assert.deepEqual(c.compaction, tokens(20, 10, 7));
});

test('every informative snapshot must agree on a stable counter basis', () => {
  for (const first of ['all_requests', 'ordinary_requests']) {
    const es = fixture();
    const snap = counter(first === 'all_requests' ? tokens(120, 70, 17) : tokens(100, 60, 10));
    es.splice(6, 0, {...snap, timestamp: '2026-01-01T00:00:05.500Z'});
    if (first === 'ordinary_requests') es[10].payload.info.total_token_usage = tokens(160, 90, 22);
    const c = reconcile(es, 'session')!;
    assert.equal(c.status, 'unavailable');
    if (c.status === 'unavailable') assert.match(c.reason, /basis|counters/i);
  }
});

test('a new zero-token request after the final counter is still an uncovered tail', () => {
  const es = fixture();
  es.splice(-1, 0, {...response('tail', 'b', tokens(0, 0, 0), tokens(160, 90, 22), tokens(40, 20, 5)),
    timestamp: '2026-01-01T00:00:09.500Z'});
  const c = reconcile(es, 'session')!;
  assert.equal(c.status, 'unavailable');
  if (c.status === 'unavailable') assert.match(c.reason, /last session-counter/);
});

test('identical response and compaction mirrors are idempotent', () => {
  const es = fixture(); es.splice(3, 0, structuredClone(es[2]));
  const one = run(renumber(es)).request_coverage!;
  assert.equal(one.status, 'verified'); if (one.status !== 'verified') return;
  assert.equal(one.duplicate_records, 1); assert.equal(one.unique_requests, 3);
  es.splice(7, 0, structuredClone(es[6]));
  assert.deepEqual(run(renumber(es)).request_coverage, one);
});

test('missing/conflicting request evidence never rewrites validated session totals', () => {
  const mutations: Array<[string, (es: any[]) => void, RegExp]> = [
    ['missing prefix', es => {es.splice(2, 1);}, /Session counters|prefix/],
    ['conflicting duplicate', es => {const r=structuredClone(es[2]);r.payload.usage=tokens(101,60,10);es.splice(3,0,r);}, /Conflicting/],
    ['missing usage', es => {delete es[2].payload.usage.output_tokens;}, /invalid/],
    ['boolean', es => {es[2].payload.usage.input_tokens=true;}, /invalid/],
    ['unsafe integer', es => {es[2].payload.usage.input_tokens=Number.MAX_SAFE_INTEGER+1;}, /invalid/],
    ['bad total', es => {es[2].payload.usage.total_tokens=1;}, /total/],
    ['invalid subset', es => {es[2].payload.usage.reasoning_output_tokens=11;}, /subsets/],
    ['thread cumulative', es => {es[2].payload.thread_token_usage=tokens(101,60,10);}, /prefix/],
    ['Turn cumulative', es => {es[8].payload.turn_token_usage=tokens(160,90,22);}, /Turn request prefix/],
    ['mixed session', es => {es[2].payload.session_id='other';}, /identity/],
    ['mixed thread', es => {es[2].payload.thread_id='other';}, /identity/],
    ['wrong Turn', es => {es[2].payload.turn_id='b';}, /outside/],
    ['unlinked compaction', es => {es[5].payload.compaction_response_id='missing';}, /compaction|Compaction|disagree/],
    ['missing mirror', es => {delete es[5].payload.latest_token_usage_record;}, /Missing/],
    ['changed mirror', es => {es[5].payload.latest_token_usage_record=structuredClone(es[4].payload);es[5].payload.latest_token_usage_record.usage=tokens(21,10,7);}, /mirror/],
    ['timestamp order', es => {es[4].timestamp='2025-01-01T00:00:00Z';}, /timestamps/],
    ['missing compaction classification', es => {es.splice(5,1);}, /disagree/],
  ];
  for (const [name, mutate, reason] of mutations) {
    const es=fixture(); mutate(es);const x=run(renumber(es)), c=x.request_coverage!;
    assert.equal(c.status,'unavailable',name);
    if(c.status==='unavailable') {assert.match(c.reason,reason,name);assert.equal('all_requests' in c,false,name);}
    assert.equal(x.input_tokens,140,name);assert.equal(x.output_tokens,15,name);
  }
});

test('unfinished requests retain explicit partial admission; forks and malformed session counters still reject', () => {
  const es=fixture().slice(0,-1);
  assert.throws(()=>run(es),/completed Turn/);
  const x=run(es,true); assert.equal(x.request_coverage?.status,'verified');
  assert.equal(x.incomplete_turn?.usage?.output_tokens,5);
  for(const mutate of [
    (rows:any[])=>{rows[0].payload.forked_from_id='parent';},
    (rows:any[])=>{rows[0].payload.id='another';},
    (rows:any[])=>{rows[9].payload.info.total_token_usage=tokens(200,80,15);},
  ]){const rows=fixture();mutate(rows);assert.throws(()=>run(rows,true));}
});

test('an identified interruption keeps observed requests across resume without completing the aborted Turn', () => {
  const rows = fixture();
  rows[6].payload = {type: 'turn_aborted', turn_id: 'a'};
  assert.throws(() => run(rows), /aborted Turn/);
  const resumed = run(rows, true);
  assert.deepEqual(resumed.turns.map(turn => turn.turn_id), ['b']);
  assert.equal(resumed.completed_turns, 1);
  assert.equal(resumed.incomplete_turn, null);
  assert.equal(resumed.input_tokens, 140);
  assert.equal(resumed.turn_elapsed_seconds, 8);
  assert.deepEqual(resumed.interrupted_turns, [{
    turn_id: 'a', start: rows[1].timestamp, observed_through: rows[6].timestamp,
    first_line: 2, last_line: 7, counter_observations: 1,
    usage: {input_tokens: 100, cached_input_tokens: 60, uncached_input_tokens: 40, output_tokens: 10},
  }]);
  const coverage = resumed.request_coverage!;
  assert.equal(coverage.status, 'verified');
  if (coverage.status === 'verified') {
    assert.equal(coverage.unique_requests, 3);
    assert.equal(coverage.compaction_requests, 1);
    assert.deepEqual(coverage.all_requests, tokens(160, 90, 22));
  }
  const wrong = structuredClone(rows);
  wrong[6].payload.turn_id = 'b';
  assert.throws(() => run(wrong, true), /unmatched aborted Turn/);
  const anonymous = structuredClone(rows);
  delete anonymous[6].payload.turn_id;
  assert.throws(() => run(anonymous, true), /overlapping Turn/);
  const duplicate = structuredClone(rows);
  duplicate[7].payload.turn_id = 'a';
  assert.throws(() => run(duplicate, true), /overlapping Turn/);
  const interruptedOnly = run(renumber([...rows.slice(0, 7), {
    ...event('token_count'), timestamp: rows[7].timestamp,
  }]), true);
  assert.equal(interruptedOnly.completed_turns, 0);
  assert.equal(interruptedOnly.interrupted_turns?.length, 1);
  assert.equal(interruptedOnly.end, rows[6].timestamp);
});

test('a host without request records is unknown, while verified zero compactions is an observed zero', () => {
  const es=fixture().filter(e=>e.type!=='compacted' && e.payload.response_id!=='compact');
  es.find(e=>e.payload.response_id==='two').payload.thread_token_usage=tokens(140,80,15);
  const c=run(es).request_coverage!;
  assert.equal(c.status,'verified');if(c.status!=='verified')return;
  assert.equal(c.compaction_requests,0);assert.deepEqual(c.compaction,tokens(0,0,0));
  assert.equal(c.counter_basis, 'undetermined');
  const noRecords=fixture().filter(e=>e.type!=='token_usage_record');
  assert.equal(run(noRecords).request_coverage?.status,'unavailable');
});
