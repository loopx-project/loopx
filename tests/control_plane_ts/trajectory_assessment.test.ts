import assert from 'node:assert/strict';
import test from 'node:test';
import {prepareTrajectoryAssessment, qualifyTrajectoryAssessment, type TrajectoryInput} from '../../loopx/control_plane/capabilities/trajectory_assessment.ts';
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
