import assert from 'node:assert/strict';
import {test} from 'node:test';
import {selectResearchUsage, exportResearchUsage} from './research-usage-selection.ts';

const turn = (n, input = 100) => ({turn_id: `turn-${n}`, start: `2026-01-01T00:${String(n).padStart(2, '0')}:00Z`,
  end: `2026-01-01T00:${String(n).padStart(2, '0')}:10Z`, first_line: n * 3, last_line: n * 3 + 2, elapsed_seconds: 10,
  usage: {input_tokens: input, cached_input_tokens: input / 2, uncached_input_tokens: input / 2, output_tokens: 10}});
const usage = {session_id: 'session', method: 'codex_observed_session_counters_v1',
  start: '2026-01-01T00:00:00Z', end: '2026-01-01T00:13:00Z', turns: Array.from({length: 12}, (_, i) => turn(i + 1)),
  incomplete_turn: {turn_id: 'tail', start: '2026-01-01T00:13:00Z', observed_through: '2026-01-01T00:13:05Z',
    first_line: 40, last_line: 41, counter_observations: 0, usage: null}, limitations: ['Only recorded counters.']};

test('inclusive chronological selection sums independent counters and excludes inter-Turn gaps', () => {
  const result = selectResearchUsage(usage, 2, 10);
  assert.equal(result.selected_turns, 9);
  assert.equal(result.observed_input_tokens, 900);
  assert.equal(result.observed_cached_input_tokens, 450);
  assert.equal(result.observed_uncached_input_tokens, 450);
  assert.equal(result.observed_output_tokens, 90);
  assert.equal(result.observed_turn_seconds, 90);
  assert.deepEqual(result.turns.map(t => t.ordinal), [2,3,4,5,6,7,8,9,10]);
});

test('unknown, partial and measured zero remain distinct', () => {
  const tail = selectResearchUsage(usage, 13, 13);
  assert.equal(tail.observed_input_tokens, null);
  assert.equal(tail.completed_turns, 0);
  assert.equal(tail.turns_without_counters, 1);
  assert.equal(tail.observed_turn_seconds, 5);
  const partial = selectResearchUsage(usage, 12, 13);
  assert.equal(partial.observed_input_tokens, 100);
  assert.equal(partial.turns_without_counters, 1);
  assert.equal(partial.completed_turns, 1);
  const zero = {...usage, incomplete_turn: {...usage.incomplete_turn, counter_observations: 1,
    usage: {input_tokens: 0, cached_input_tokens: 0, uncached_input_tokens: 0, output_tokens: 0}}};
  assert.equal(selectResearchUsage(zero, 13, 13).observed_input_tokens, 0);
  assert.equal(selectResearchUsage(zero, 13, 13).turns_without_counters, 0);
});

test('rejects empty, reversed, fractional and out-of-capture selections', () => {
  for (const [first, last] of [[0,1],[2,1],[1,14],[1.5,3],[1,NaN],[1,Infinity]]) {
    assert.throws(() => selectResearchUsage(usage, first, last), /valid captured Turn range/);
  }
  assert.throws(() => selectResearchUsage({...usage, turns: [], incomplete_turn: null}, 1, 1));
});

test('portable selection retains provenance and source lines beyond paging without exporting raw source', () => {
  const source = {pack_id: 'pack', goal_id: 'goal', artifact_sha256: 'a'.repeat(64), applicability: 'historical'};
  const before = JSON.stringify(usage);
  const result = JSON.parse(exportResearchUsage(usage, 1, 13, source, '2026-01-02T00:00:00Z'));
  assert.equal(result.selection.turns.length, 13);
  assert.equal(result.selection.turns[11].first_line, 36);
  assert.equal(result.selection.turns[12].usage, null);
  assert.equal(result.artifact_sha256, source.artifact_sha256);
  assert.equal(result.applicability, 'historical');
  assert.equal(result.session_id, 'session');
  assert.equal(result.selection.observed_input_tokens, 1200);
  assert(result.limitations.includes(usage.limitations[0]));
  assert.equal(JSON.stringify(usage), before);
});
