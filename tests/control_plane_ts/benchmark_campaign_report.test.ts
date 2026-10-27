import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { buildReport, renderMarkdown, renderCsv, type CampaignInput } from '../../scripts/benchmark-campaign-report.ts';

function fixture(): CampaignInput {
  return {schema_version:'benchmark_campaign_report_input_v1', observed_at:'2026-01-01T00:00:00Z',title:'Campaign',
    case_ids:['one','two','three'],arms:[{arm_id:'a',label:'A'},{arm_id:'b',label:'B'}], notes:[], incidents:[],
    selection:['a','b'].flatMap(arm_id => ['one','two','three'].map((case_id,i) => ({arm_id,case_id,run_id:`${arm_id}-${case_id}`,status:'completed' as const,protocol_id:'protocol',exclude_reason:null,rerun_reason:null,
      score:{value:arm_id==='a'?[0,40,100][i]:[20,80,0][i],normalization_id:`scale-${case_id}`,evidence_ref:'receipt:score',origin:'official' as const,countable:true}})))};
}
test('common means use identical cases; missing score never becomes zero',() => {
  const d=fixture();d.selection[5].score=null;
  const r=buildReport(d);assert.equal(r.comparisons[0].n,2);
  assert.deepEqual(r.comparisons[0].means,{a:20,b:50});assert.equal(r.comparisons[0].delta_second_minus_first,30);
  assert.equal(r.summary[1].ended_without_score,1);
});
test('replacement does not restore older score; incidents count distinct cases',() => {
  const d=fixture();const row=d.selection[0];row.status='planned';row.run_id='replacement';row.score=null;row.rerun_reason='infrastructure';
  d.incidents=[1,2].map(n => ({arm_id:'a',case_id:'one',run_id:`old-${n}`,fault:'infrastructure',initial_attempt:n===1,evidence_ref:'receipt:error'}));
  const r=buildReport(d);assert.equal(r.summary[0].queued,1);assert.equal(r.summary[0].infrastructure_failures_ever,1);
  assert.equal(r.summary[0].pending_reruns.infrastructure,1);assert.equal(r.comparisons[0].n,2);
});
test('authorized failure zero stays distinct from no score and qualification',() => {
  const d=fixture();d.selection[0].status='runner_invalid';d.selection[0].score={value:0,normalization_id:'scale-one',evidence_ref:'policy:zero',origin:'authorized_failure_zero',countable:false};
  const r=buildReport(d);assert.equal(r.comparisons[0].n,3);assert.equal(r.comparisons[0].qualified_common_n,2);
});
test('known invalid scores and normalization mismatches are excluded with reasons',() => {
  const d=fixture();d.selection[0].exclude_reason='grader_defect';d.selection[0].score!.countable=false;
  d.selection[1].score!.normalization_id='different';
  const c=buildReport(d).comparisons[0];assert.deepEqual(c.case_ids,['three']);assert.ok(c.exclusions.two.includes('normalization_mismatch'));
});
test('running scores do not enter terminal averages',() => {
  const d=fixture();d.selection[0].status='running';d.selection[0].score!.countable=false;
  assert.equal(buildReport(d).comparisons[0].n,2);
});
test('duplicate or missing case selections fail closed',() => {
  const d=fixture();d.selection[0]=structuredClone(d.selection[1]);assert.throws(()=>buildReport(d),/duplicate/);
  d.selection.pop();assert.throws(()=>buildReport(d),/every arm/);
});
test('invalid numeric values and false qualification are rejected',() => {
  for(const bad of [NaN,Infinity,-1,101,true]){const d=fixture();d.selection[0].score!.value=bad as number;assert.throws(()=>buildReport(d),/finite/);}
  const d=fixture();d.selection[0].status='cancelled';assert.throws(()=>buildReport(d),/countable/);
});
test('empty intersections stay null, not zero',() => {
  const d=fixture();for(const r of d.selection)r.score=null;
  const report=buildReport(d);assert.deepEqual(report.comparisons[0].means,{a:null,b:null});assert.ok(renderMarkdown(report).includes('—'));
});
test('row order does not change cases or means',() => {
  const d=fixture();const before=buildReport(d).comparisons;d.selection.reverse();d.case_ids.reverse();
  assert.deepEqual(buildReport(d).comparisons,before);
});
test('CSV neutralizes formulas; Markdown escapes table content',() => {
  const d=fixture();d.selection[0].score!.evidence_ref='=1+1';d.arms[0].label='A | B\n<script>';
  const r=buildReport(d);assert.ok(renderCsv(r).includes("'=1+1"));assert.ok(renderMarkdown(r).includes('A \\| B &lt;script&gt;'));
});
test('real CLI writes all reports and rejects malformed input without output',() => {
  const dir=mkdtempSync(join(tmpdir(),'campaign-report-'));
  try {
    const input=join(dir,'input.json'),prefix=join(dir,'report');writeFileSync(input,JSON.stringify(fixture()));
    const command=[ '--experimental-strip-types',resolve('scripts/benchmark-campaign-report.ts'),input,prefix];
    const run=spawnSync(process.execPath,command,{encoding:'utf8'});assert.equal(run.status,0,run.stderr);
    const report=JSON.parse(readFileSync(prefix+'.json','utf8'));assert.equal(report.summary.length,2);assert.equal(report.comparisons[0].n,3);
    assert.ok(readFileSync(prefix+'.md','utf8').includes('共同题数'));assert.equal(readFileSync(prefix+'.csv','utf8').trim().split('\n').length,7);
    writeFileSync(input,'{}');command[command.length-1]=join(dir,'invalid');assert.notEqual(spawnSync(process.execPath,command).status,0);assert.equal(existsSync(join(dir,'invalid.json')),false);
  } finally {rmSync(dir,{recursive:true,force:true});}
});
