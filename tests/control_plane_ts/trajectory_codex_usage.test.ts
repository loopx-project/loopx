import assert from 'node:assert/strict';
import test from 'node:test';
import {summarizeTrajectoryCodexUsage as summarize} from '../../loopx/control_plane/capabilities/trajectory_codex_usage.ts';
const usage = (input: number, cached: number, output: number) => ({input_tokens:input,cached_input_tokens:cached,output_tokens:output});
function fixture() {
  return [
    {type:'session_meta', payload:{id:'session'}},
    {type:'event_msg',payload:{type:'task_started',turn_id:'a'}},
    {type:'event_msg',payload:{type:'token_count',info:{total_token_usage:usage(100,60,10),last_token_usage:usage(100,60,10)}}},
    {type:'event_msg',payload:{type:'task_complete',turn_id:'a'}},
    {type:'event_msg',payload:{type:'task_started',turn_id:'b'}},
    {type:'event_msg',payload:{type:'token_count',info:{total_token_usage:usage(140,80,15),last_token_usage:usage(40,20,5)}}},
    {type:'event_msg',payload:{type:'task_complete',turn_id:'b'}},
  ].map((e,i)=>({...e,line:i+1,timestamp:`2026-01-01T00:00:0${i}Z`})) as any[];
}
const run=(events=fixture())=>summarize({events,session_id:'session'});
test('cumulative snapshots telescope; cached input is a subset and waiting stays separate',()=>{
  const x=run();assert.equal(x.input_tokens,140);assert.equal(x.uncached_input_tokens,60);
  assert.equal(x.turns[1].usage.input_tokens,40);assert.equal(x.output_tokens,15);
  assert.equal(x.elapsed_seconds,5);assert.equal(x.turn_elapsed_seconds,4);
  assert.equal(x.turns[1].first_line,5);assert.equal(x.turns[1].last_line,7);
});
test('duplicate counter observation does not charge twice',()=>{
  const es=fixture();es.splice(3,0,structuredClone(es[2]));es.forEach((e,i)=>e.line=i+1);
  assert.equal(run(es).input_tokens,140);
});
test('unknown counters, resets, missing prefix, session mismatch and unfinished work reject',()=>{
  for(const edit of [
    (e:any[])=>{e[0].payload.id='other';},
    (e:any[])=>{e[0].payload.forked_from_id='parent';},
    (e:any[])=>{e[2].payload.info.total_token_usage.input_tokens=200;},
    (e:any[])=>{e[5].payload.info.total_token_usage.input_tokens=50;},
    (e:any[])=>{delete e[5].payload.info.total_token_usage.cached_input_tokens;},
    (e:any[])=>{e[5].payload.info.total_token_usage.cached_input_tokens=150;},
    (e:any[])=>{e[6].payload.turn_id='unknown';},
    (e:any[])=>{e[5].timestamp='2025-01-01T00:00:00Z';},
    (e:any[])=>{e.pop();},
    (e:any[])=>{e.splice(5,1);},
  ]) {const es=fixture();edit(es);assert.throws(()=>run(es));}
});
