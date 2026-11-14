import assert from 'node:assert/strict';
import test from 'node:test';
import {createHash} from 'node:crypto';
import {prepareTrajectoryAssessment, type TrajectoryInput} from '../../loopx/control_plane/capabilities/trajectory_assessment.ts';
import {projectTrajectoryHumanAttention} from '../../loopx/control_plane/capabilities/trajectory_human_attention.ts';
import {compareTrajectoryAssessments} from '../../loopx/control_plane/capabilities/trajectory_assessment_continuation.ts';

const config = {model: 'fixture', effort: 'max', service_tier: 'fast'};
const scope = {goal_id: 'g', case_id: 'c', run_id: 'r', revision: 'fixed', window: 'one hour'};
const at = (minute: number) => `2026-01-01T00:${String(minute).padStart(2, '0')}:00Z`;
const event = (id: string, kind = 'correction') => ({event_id: id, episode_id: 'e', kind,
  necessity: 'avoidable', occurred_at: at(10), active: null,
  request: null, citation: {source_id: 's', first_line: 1, last_line: 1}});
function input(events: unknown[], complete = false): TrajectoryInput {
  const text = JSON.stringify({schema_version: 'trajectory_human_attention_v1', scope,
    start: at(0), end: at(59), complete, events});
  return {schema_version: 'trajectory_assessment_input_v1', scope, objective: 'Improve accepted research outcomes',
    selection: {rule: 'fixed', population_episodes: 1, exhaustive: true},
    human_attention_source_id: 'attention', sources: [
      {id: 's', artifact: 'evidence.txt', artifact_sha256: 'a'.repeat(64), first_line: 1, text: 'Human corrected a misapplied result.'},
      {id: 'attention', artifact: 'attention.json', artifact_sha256: createHash('sha256').update(text).digest('hex'), first_line: 1, text}],
    episodes: [{id: 'e', question: 'Can the next experiment use the corrected scope?', source_ids: ['s', 'attention'], eligible_metrics: ['evidence_adoption']}]};
}
test('deduplicate receipt replays; union overlapping active/wait time and preserve unknown necessity', () => {
  const a = {...event('one'), active: {start: at(10), end: at(15)}, request: {start: at(1), end: at(10)}};
  const b = {...event('two', 'review'), necessity: 'required', active: {start: at(12), end: at(17)}, request: {start: at(5), end: at(15)}};
  const c = {...event('three', 'approval'), necessity: 'unknown', request: {start: at(10), end: null}};
  const d = {...event('four', 'other'), necessity: 'unknown'};
  const result = projectTrajectoryHumanAttention(input([a,b,a,c,d]))!;
  assert.equal(result.total, 3); assert.equal(result.active_ms, 7 * 60000);
  assert.equal(result.wait_ms, 14 * 60000); assert.equal(result.active_covered, 2);
  assert.equal(result.required, 1); assert.equal(result.unknown, 1); assert.equal(result.pending_requests, 1);
  assert.equal(result.counts.approval, 0, 'An unanswered approval request is not a completed authorization decision');
  assert.deepEqual(result.optimization_points, [{kind: 'correction', event_ids: ['one']}]);
  assert.equal(result.complete, false);
});
test('absent timing is unknown; explicit measured zero and empty observed counts remain distinct', () => {
  assert.equal(projectTrajectoryHumanAttention(input([event('one')]))!.active_ms, null);
  assert.equal(projectTrajectoryHumanAttention(input([{...event('zero'), active: {start: at(10), end: at(10)}}]))!.active_ms, 0);
  const empty = projectTrajectoryHumanAttention(input([], true))!;
  assert.equal(empty.total, 0); assert.equal(empty.active_ms, null); assert.equal(empty.wait_ms, null);
  assert.deepEqual(empty.optimization_points, []);
  const pending = projectTrajectoryHumanAttention(input([{...event('pending', 'review'), request: {start: at(5), end: null}}]))!;
  assert.equal(pending.total, 0); assert.equal(pending.pending_requests, 1);
  assert.equal(pending.counts.review, 0); assert.deepEqual(pending.optimization_points, []);
});
test('reject contradictory identity, wrong scope, invalid times, inferred categories and unbound evidence', () => {
  const mutations = [
    (x: any) => {x.scope.run_id = 'another';},
    (x: any) => {x.start = '2026-02-30T00:00:00Z';},
    (x: any) => {x.events[0].occurred_at = '2026-01-02T00:00:00Z';},
    (x: any) => {x.events[0].kind = 'automatic_replan';},
    (x: any) => {x.events[0].episode_id = 'another';},
    (x: any) => {x.events[0].citation.source_id = 'attention';},
    (x: any) => {x.events[0].citation.last_line = 2;},
    (x: any) => {x.events[0].active = {start: at(10), end: at(5)};},
    (x: any) => {x.events.push({...x.events[0], kind: 'review'});},
  ];
  for (const mutate of mutations) {
    const bound = input([event('one')]); const raw = JSON.parse(bound.sources[1]!.text);
    mutate(raw); bound.sources[1]!.text = JSON.stringify(raw);
    assert.throws(() => projectTrajectoryHumanAttention(bound));
  }
  const bound = input([event('one')]); bound.episodes[0]!.source_ids = ['attention'];
  assert.throws(() => projectTrajectoryHumanAttention(bound), /assigned episode/);
});
test('feature omission preserves original preparation and does not inject attention guidance', () => {
  const bound = input([event('one')]); delete bound.human_attention_source_id;
  const old = prepareTrajectoryAssessment({input: bound, config});
  assert.equal(Object.hasOwn(old, 'human_attention'), false);
  assert.ok(!old.prompt.includes('Human attention observations are source-bound records'));
  assert.deepEqual(old.input, bound);
  const next = prepareTrajectoryAssessment({input: {...bound, human_attention_source_id: 'attention'}, config});
  assert.notEqual(old.analysis_key, next.analysis_key);
  assert.equal(next.human_attention!.total, 1);
});
test('attention rebinding requires reassessment even when source text is unchanged', () => {
  const first = input([event('one')]); first.sources.push({...first.sources[1]!, id: 'other'});
  const prepared = prepareTrajectoryAssessment({input: first, config});
  const previous = {...prepared, output: {assessments: [{episode_id: 'e', metric: 'evidence_adoption', verdict: 'unknown',
    reason: 'No subsequent observation.', citations: []}], limitations: ['Synthetic fixture.']}};
  const next = {...first, human_attention_source_id: 'other'};
  assert.equal(compareTrajectoryAssessments({input: next, config, previous}).can_reuse, false);
});
