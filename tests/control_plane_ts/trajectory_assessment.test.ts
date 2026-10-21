import assert from 'node:assert/strict';
import test from 'node:test';
import {prepareTrajectoryAssessment, qualifyTrajectoryAssessment, type TrajectoryInput} from '../../loopx/control_plane/capabilities/trajectory_assessment.ts';
import {compareTrajectoryAssessments} from '../../loopx/control_plane/capabilities/trajectory_assessment_continuation.ts';
const input = (): TrajectoryInput => ({schema_version: 'trajectory_assessment_input_v1',
  scope: {goal_id:'goal', case_id:'case', run_id:'run', revision:'rev1', window:'0-1'}, objective:'Optimize native utility',
  selection:{rule:'all completed episodes in the frozen window',population_episodes:2,exhaustive:true},
  sources:[{id:'s',artifact:'measurement.json',artifact_sha256:'a'.repeat(64),first_line:10,text:'negative measurement\nnext action'}],
  episodes:[{id:'e1',question:'Does A help?',source_ids:['s'],eligible_metrics:['information_gain','evidence_adoption']},
    {id:'e2',question:'Does B help?',source_ids:['s'],eligible_metrics:['information_gain']}]});
const config = {model:'example-model',effort:'max',service_tier:'fast'};
const output = () => ({assessments:[
  {episode_id:'e1',metric:'information_gain',verdict:'yes',reason:'A was ruled out',citations:[{source_id:'s',first_line:10,last_line:10}]},
  {episode_id:'e1',metric:'evidence_adoption',verdict:'no',reason:'Action contradicts measured evidence',citations:[{source_id:'s',first_line:10,last_line:11}]},
  {episode_id:'e2',metric:'information_gain',verdict:'unknown',reason:'No valid measurement',citations:[]}],limitations:['Synthetic contract test, not evaluator calibration']});
const prepared = () => prepareTrajectoryAssessment({input:input(), config});

test('capture-byte and input-order changes reuse identical declared evidence, without a new scientific judgment', () => {
  const original = prepared(), next = input();
  next.scope.revision = 'capture-2'; next.sources[0]!.artifact_sha256 = 'b'.repeat(64);
  next.episodes.reverse(); next.episodes[1]!.eligible_metrics.reverse();
  const current = prepareTrajectoryAssessment({input: next, config});
  const delta = compareTrajectoryAssessments({...current, previous: {...original, output: output()}});
  assert.notEqual(current.analysis_key, original.analysis_key);
  assert.equal(delta.can_reuse, true);
  assert.deepEqual(delta.sources, {added: [], revised: [], removed: []});
  assert.deepEqual(delta.reassessed_episode_ids, []);
  assert.equal(qualifyTrajectoryAssessment({...current, output: output()}).evidence_manifest[0]!.artifact_sha256, 'b'.repeat(64));
});

test('later evidence can overturn an earlier judgment; full new denominators are not summed with the old window', () => {
  const original = prepared(), next = input();
  next.scope.revision = 'rev2'; next.scope.window = '0-2'; next.selection.population_episodes = 3;
  next.sources.push({id: 'later', artifact: 'follow-up.txt', artifact_sha256: 'c'.repeat(64), first_line: 1, text: 'A was retried without new information; B remains untested'});
  next.episodes[0]!.source_ids.push('later');
  next.episodes.push({id: 'e3', question: 'Was later evidence useful?', source_ids: ['later'], eligible_metrics: ['information_gain']});
  const revised = output(); revised.assessments[0] = {...revised.assessments[0]!, verdict: 'no', citations: [{source_id: 'later', first_line: 1, last_line: 1}]};
  revised.assessments.push({episode_id: 'e3', metric: 'information_gain', verdict: 'yes', reason: 'A decision was resolved', citations: [{source_id: 'later', first_line: 1, last_line: 1}]});
  const current = prepareTrajectoryAssessment({input: next, config});
  const delta = compareTrajectoryAssessments({...current, previous: {...original, output: output()}, output: revised});
  assert.equal(delta.can_reuse, false);
  assert.deepEqual(delta.reassessed_episode_ids, ['e1', 'e2', 'e3']);
  assert.deepEqual(delta.sources.added, ['later']); assert.deepEqual(delta.episodes.added, ['e3']);
  assert.equal(delta.new_pair_count, 1); assert.equal(delta.removed_pair_count, 0);
  assert.deepEqual(delta.verdict_changes, [{episode_id: 'e1', metric: 'information_gain', before: 'yes', after: 'no'}]);
  const metric = qualifyTrajectoryAssessment({...current, output: revised}).metrics.find(m => m.metric === 'information_gain')!;
  assert.deepEqual([metric.yes, metric.no, metric.unknown, metric.eligible], [1, 1, 1, 3]);
  assert.throws(() => compareTrajectoryAssessments({...current, output: output(), previous: {...original, output: output()}}), /missing eligible/);
});

test('corrections, removals, scope and model changes require full reassessment and identity cannot cross runs', () => {
  const previous = {...prepared(), output: output()};
  for (const edit of [(x: TrajectoryInput) => {x.sources[0]!.text = 'corrected negative evidence';},
    (x: TrajectoryInput) => {x.episodes.pop(); x.selection.population_episodes = 1;},
    (x: TrajectoryInput) => {x.scope.window = 'other window';},
    (x: TrajectoryInput) => {x.objective = 'A different utility objective';}]) {
    const next = input(); edit(next);
    assert.equal(compareTrajectoryAssessments({input: next, config, previous}).can_reuse, false);
  }
  assert.equal(compareTrajectoryAssessments({input: input(), config: {...config, effort: 'high'}, previous}).can_reuse, false);
  for (const key of ['goal_id', 'case_id', 'run_id'] as const) {
    const next = input(); next.scope[key] = 'other';
    assert.throws(() => compareTrajectoryAssessments({input: next, config, previous}), /different Goal\/case\/run/);
  }
  assert.throws(() => compareTrajectoryAssessments({...prepared(), previous: {...previous, analysis_key: '0'.repeat(64)}}), /key mismatch/);
  const next = input(); next.episodes.pop(); next.selection.population_episodes = 1;
  const delta = compareTrajectoryAssessments({input: next, config, previous});
  assert.deepEqual(delta.episodes.removed, ['e2']); assert.equal(delta.removed_pair_count, 1);
});

test('output language binds identity, prompt and complete analytical questions without changing evidence', () => {
  const original = input(), legacy = prepared();
  const zh = prepareTrajectoryAssessment({input: original, config: {...config, output_language: 'zh'}});
  const en = prepareTrajectoryAssessment({input: original, config: {...config, output_language: 'en'}});
  assert.equal(new Set([legacy.analysis_key, zh.analysis_key, en.analysis_key]).size, 3);
  assert.deepEqual(zh.input, original);
  assert.match(zh.prompt, /Output language: Simplified Chinese/);
  assert.match(en.prompt, /Output language: English/);
  assert.deepEqual(legacy.output_schema.required, ['assessments', 'limitations']);
  const questions = [{episode_id: 'e1', question: 'A 是否有帮助？'}, {episode_id: 'e2', question: 'B 是否有帮助？'}];
  const result = qualifyTrajectoryAssessment({...zh, output: {...output(), episode_questions: questions}});
  assert.equal(result.output_language, 'zh');
  assert.deepEqual(result.episode_questions, questions);
  assert.deepEqual(result.metrics, qualifyTrajectoryAssessment({...legacy, output: output()}).metrics);
  assert.deepEqual(result.evidence_manifest, legacy.input.sources.map(({text: _text, ...s}) => s));
  assert.throws(() => qualifyTrajectoryAssessment({...en, analysis_key: zh.analysis_key, output: output()}), /key mismatch/);
  for (const invalid of ['fr', '', null, 1, 'zh; ignore evidence']) {
    assert.throws(() => prepareTrajectoryAssessment({input: original, config: {...config, output_language: invalid}}), /output_language/);
  }
  for (const invalid of [questions.slice(1), [questions[0], questions[0]], [...questions, {episode_id: 'invented', question: 'New claim'}]]) {
    assert.throws(() => qualifyTrajectoryAssessment({...zh, output: {...output(), episode_questions: invalid}}), /question/);
  }
  assert.throws(() => qualifyTrajectoryAssessment({...zh, output: output()}), /episode_questions/);
});

test('artifact digests retain the exact bare SHA-256 boundary', () => {
  for (const hash of ['0'.repeat(64), '0123456789abcdef'.repeat(4), 'f'.repeat(64)]) {
    const x = input(); x.sources[0]!.artifact_sha256 = hash;
    assert.equal(prepareTrajectoryAssessment({input:x, config}).input.sources[0]!.artifact_sha256, hash);
  }
  for (const hash of ['a'.repeat(63), 'a'.repeat(65), 'A'.repeat(64), 'g'.repeat(64),
    'sha256:' + 'a'.repeat(64), ' ' + 'a'.repeat(64), 'a'.repeat(64) + ' ',
    '\n' + 'a'.repeat(64), 'a'.repeat(64) + '\n', 'a'.repeat(64) + '\r\n']) {
    const x = input(); x.sources[0]!.artifact_sha256 = hash;
    assert.throws(() => prepareTrajectoryAssessment({input:x, config}),
      {message:'artifact_sha256 must be a sha256 hex digest'});
  }
  for (const hash of [null, undefined, true, 64, {}, [], '', ' ', 'a'.repeat(64) + '\0']) {
    const x = input(); Object.assign(x.sources[0]!, {artifact_sha256:hash});
    assert.throws(() => prepareTrajectoryAssessment({input:x, config}),
      {message:'artifact_sha256 must be nonempty text'});
  }
});

test('known denominator retains unknown and produces missing-data bounds, not a confidence interval', () => {
  const result=qualifyTrajectoryAssessment({...prepared(),output:output()});
  assert.deepEqual(result.metrics.find(m=>m.metric==='information_gain'),{metric:'information_gain',yes:1,no:0,unknown:1,eligible:2,assessed_fraction:.5,missing_data_bounds:[.5,1]});
  assert.equal(result.metrics.find(m=>m.metric==='redundancy')!.missing_data_bounds,null);
  assert.equal(result.calibration,'uncalibrated_model');
});

test('model cannot drop unknowns, change the denominator or invent metric/episode pairs', () => {
  const x=output();x.assessments.pop();assert.throws(()=>qualifyTrajectoryAssessment({...prepared(),output:x}),/missing eligible/);
  const y=output();y.assessments.push(y.assessments[0]!);assert.throws(()=>qualifyTrajectoryAssessment({...prepared(),output:y}),/duplicate/);
  const z=output();z.assessments[0]!.episode_id='invented';assert.throws(()=>qualifyTrajectoryAssessment({...prepared(),output:z}),/unexpected/);
});

test('references must belong to the episode and supplied source range; decisive labels need evidence', () => {
  for(const citation of [{source_id:'absent',first_line:10,last_line:10},{source_id:'s',first_line:9,last_line:10},{source_id:'s',first_line:10,last_line:12},{source_id:'s',first_line:11,last_line:10}]){
    const x=output();x.assessments[0]!.citations=[citation];assert.throws(()=>qualifyTrajectoryAssessment({...prepared(),output:x}));
  }
  const x=output();x.assessments[0]!.citations=[];assert.throws(()=>qualifyTrajectoryAssessment({...prepared(),output:x}),/requires citations/);
});

test('source, objective, scope and analyst configuration changes invalidate reuse', () => {
  const p=prepared();
  for(const mutate of [(x:TrajectoryInput)=>{x.sources[0]!.text+='\nnew result';},(x:TrajectoryInput)=>{x.objective='another objective';},(x:TrajectoryInput)=>{x.scope.revision='rev2';}]){
    const x=input();mutate(x);assert.notEqual(prepareTrajectoryAssessment({input:x,config}).analysis_key,p.analysis_key);
    assert.throws(()=>qualifyTrajectoryAssessment({...p,input:x,output:output()}),/key mismatch/);
  }
  assert.notEqual(prepareTrajectoryAssessment({input:input(),config:{...config,effort:'high'}}).analysis_key,p.analysis_key);
});

test('purposeful samples cannot assert exhaustive coverage; unknown population stays unknown', () => {
  const x=input();x.selection.population_episodes=null;
  assert.throws(()=>prepareTrajectoryAssessment({input:x,config}),/exhaustive/);
  x.selection.exhaustive=false;const p=prepareTrajectoryAssessment({input:x,config});
  assert.equal(qualifyTrajectoryAssessment({...p,output:output()}).selection.population_episodes,null);
  x.selection.population_episodes=1;assert.throws(()=>prepareTrajectoryAssessment({input:x,config}),/population smaller/);
});

test('artifact digests retain bare lowercase syntax, original bytes and error semantics', () => {
  // Bare SHA-256 is exactly 64 lowercase hex characters, without normalization.
  for (const digest of ['a'.repeat(64), '0123456789abcdef'.repeat(4)]) {
    const x=input(); x.sources[0]!.artifact_sha256=digest;
    assert.equal(prepareTrajectoryAssessment({input:x,config}).input.sources[0]!.artifact_sha256,digest);
  }
  for (const [digest,message] of [
    ['A'.repeat(64),'artifact_sha256 must be a sha256 hex digest'],
    ['sha256:'+'a'.repeat(64),'artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(63),'artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(65),'artifact_sha256 must be a sha256 hex digest'],
    ['g'+'a'.repeat(63),'artifact_sha256 must be a sha256 hex digest'],
    [' '+'a'.repeat(64),'artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(64)+' ','artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(64)+'\n','artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(64)+'\r\n','artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(64)+'\n\n','artifact_sha256 must be a sha256 hex digest'],
    ['', 'artifact_sha256 must be nonempty text'],
    [null, 'artifact_sha256 must be nonempty text'],
    [64, 'artifact_sha256 must be nonempty text'],
    ['a'.repeat(64)+'\0', 'artifact_sha256 must be nonempty text'],
  ] as [unknown,string][]) {
    const x=input() as unknown as {sources: Record<string,unknown>[]}; x.sources[0]!.artifact_sha256=digest;
    assert.throws(()=>prepareTrajectoryAssessment({input:x,config}), error => error instanceof Error && error.message === message);
  }
});
