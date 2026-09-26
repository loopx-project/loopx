import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, writeFile, appendFile, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { cycleObservations } from "../../loopx/control_plane/runtime/usage_statistics_cycles.ts";
import type { CycleObservation } from "../../loopx/control_plane/runtime/usage_statistics_cycles.ts";
import { readCodexTiming } from "../../loopx/control_plane/runtime/usage_statistics_codex.ts";
import { recordGoalUsage, goalPreview } from "../../loopx/control_plane/runtime/usage_statistics_goals.ts";
import { configure, inspect, observe } from "../../loopx/control_plane/runtime/usage_statistics.ts";
const at = Date.parse("2026-09-01T10:00:00Z");
const key = "a".repeat(64), lane = "b".repeat(64), turn = "c".repeat(64);
const cycle = (phase: "start" | "spend", time: number, extra = {}): CycleObservation => ({ key, lane, turn, phase, at: time, host: "codex-app", ...extra });
async function fixture(t: test.TestContext) {
  const root = await mkdtemp(join(tmpdir(), "loopx-source-usage-"));
  t.after(()=>rm(root,{recursive:true,force:true})); return root;
}
const event = (type: string, time: number, fields = {}) => JSON.stringify({ type: "event_msg", timestamp: new Date(time).toISOString(), payload: { type, ...fields } }) + "\n";

test("universal cycles preserve first quota and first successful spend across all Host labels and reordered transport", async t => {
  const root = await fixture(t);
  for (const host of ["codex-app", "claude-code", "dsh", "opencode", "generic-cli"]) {
    const path = join(root, host);
    assert.deepEqual(await cycleObservations(path, "g", at, cycle("start", at, {host})), []);
    await cycleObservations(path, "g", at+100, cycle("start", at+100, {host}));
    const result = await cycleObservations(path, "g", at+1000, cycle("spend", at+1000, {host}));
    assert.equal(result[0].start, at); assert.equal(result[0].end, at+1000); assert.equal(result[0].measurement, "quota_cycle");
    const replay = await cycleObservations(path, "g", at+2000, cycle("spend", at+2000, {host}));
    assert.equal(replay[0].end, at+1000);
  }
  const path = join(root,"reordered");
  await cycleObservations(path,"g",at+1000,cycle("spend",at+1000));
  const delayed = await cycleObservations(path,"g",at+1100,cycle("start",at));
  assert.equal(delayed[0].end-delayed[0].start,1000);
  assert.deepEqual(await cycleObservations(join(root,"missing"),"g",at,cycle("spend",at)),[]);
});

test("missing spend never invents elapsed work; exact cycles cannot borrow another lane or turn", async t => {
  const path=join(await fixture(t),"cycles");
  await cycleObservations(path,"g",at,cycle("start",at));
  assert.deepEqual(await cycleObservations(path,"g",at+1000,cycle("spend",at+1000,{lane:"d".repeat(64)})),[]);
  assert.deepEqual(await cycleObservations(path,"g",at+1000,cycle("spend",at+1000,{turn:"e".repeat(64)})),[]);
  assert.deepEqual(await cycleObservations(path,"g",at+8*86400000,cycle("spend",at+8*86400000)),[]);
});

test("bound Codex timing is incremental, content-free, identity checked and handles interrupted/partial records", async t => {
  const path=join(await fixture(t),"rollout.jsonl");
  await writeFile(path,JSON.stringify({type:"session_meta",payload:{id:"thread"}})+"\n"+event("task_started",at-1000,{turn_id:"first",started_at:new Date(at-1000).toISOString()}));
  let result=await readCodexTiming(path,"thread",undefined,at,key,"codex_app");
  assert.equal(result.observations.length,0);
  await appendFile(path,event("task_complete",at+60000,{turn_id:"first",started_at:new Date(at-1000).toISOString(),completed_at:new Date(at+60000).toISOString(),last_agent_message:"DO NOT PERSIST PRIVATE CONTENT"}));
  result=await readCodexTiming(path,"thread",result.cursor,at+60001,key,"codex_app");
  assert.equal(result.observations[0].start,at); assert.equal(result.observations[0].end,at+60000);
  assert.ok(!JSON.stringify(result).includes("PRIVATE CONTENT"));
  assert.equal((await readCodexTiming(path,"thread",result.cursor,at+60001,key,"codex_app")).observations.length,0);
  await assert.rejects(readCodexTiming(path,"wrong",undefined,at,key,"codex_app"),/identity_mismatch/);
  const partial=event("turn_aborted",at+120000,{turn_id:"second",started_at:new Date(at+70000).toISOString()});
  await appendFile(path,partial.slice(0,-2));
  const incomplete=await readCodexTiming(path,"thread",result.cursor,at+120000,key,"codex_app");
  assert.equal(incomplete.observations.length,0);
  await appendFile(path,partial.slice(-2));
  const terminal=await readCodexTiming(path,"thread",incomplete.cursor,at+120001,key,"codex_app");
  assert.equal(terminal.observations[0].end-terminal.observations[0].start,50000);
});

test("three clocks remain separate and same-source overlaps are deduplicated", async t => {
  const path=join(await fixture(t),"goals");
  const intervals = ["quota_cycle","codex_turn","host_call"].map(measurement=>({key,start:at,end:at+60000,measurement,host:"codex_app"} as const));
  await recordGoalUsage(path,"g",at+60001,intervals as Parameters<typeof recordGoalUsage>[3]);
  await recordGoalUsage(path,"g",at+60001,intervals as Parameters<typeof recordGoalUsage>[3]);
  const rows=(await goalPreview(path,"g"))!.counters;
  assert.equal(rows.length,3); assert.ok(rows.every(r=>r.count===1 && r.duration==="lt_10m"));
});

test("opt-out prevents opening bound transcript files; disable erases cycle cursors", async t => {
  const path=join(await fixture(t),"usage.json");
  const ctx={env:{LOOPX_USAGE_PING_ENDPOINT:"http://127.0.0.1:1/v1/ping"},version:"1.0.0",python:"3.13",channel:"source",now:new Date(at)};
  await configure(path,ctx,"enable"); const generation=JSON.parse(await readFile(path,"utf8")).generation;
  const input=cycle("start",at,{codex:{path:"/nonexistent/private.jsonl",id:"thread"}});
  await observe(path,{...ctx,env:{...ctx.env,DO_NOT_TRACK:"1"}},generation,null,async()=>{throw new Error("must not send");},undefined,input);
  await assert.rejects(readFile(path+".cycles"),/ENOENT/);
  await observe(path,ctx,generation,null,async()=>204,undefined,input);
  assert.ok(await readFile(path+".cycles"));
  await configure(path,ctx,"disable");
  await assert.rejects(readFile(path+".cycles"),/ENOENT/);
});

test("oversized transcript content cannot strand later terminal timing", async t => {
  const path=join(await fixture(t),"large.jsonl");
  await writeFile(path,JSON.stringify({type:"session_meta",payload:{id:"thread"}})+"\n");
  let result=await readCodexTiming(path,"thread",undefined,at,key,"codex_app");
  await appendFile(path,JSON.stringify({type:"response_item",payload:{text:"x".repeat(2*1024*1024)}})+"\n"+event("task_complete",at+60000,{turn_id:"turn",started_at:new Date(at).toISOString()}));
  const found=[];
  for(let i=0;i<4;i++) {
    result=await readCodexTiming(path,"thread",result.cursor,at+60001,key,"codex_app");
    found.push(...result.observations);
  }
  assert.equal(found.length,1); assert.equal(found[0].end-found[0].start,60000);
});

test("unbound sequential cycles restart but exact replay keeps its original Host population", async t => {
  const path=join(await fixture(t),"cycles");
  await cycleObservations(path,"g",at,cycle("start",at,{turn:null}));
  await cycleObservations(path,"g",at+1000,cycle("spend",at+1000,{turn:null}));
  await cycleObservations(path,"g",at+2000,cycle("start",at+2000,{turn:null}));
  await cycleObservations(path,"g",at+2500,cycle("spend",at+1000,{turn:null}));
  await cycleObservations(path,"g",at+2500,cycle("start",at,{turn:null}));
  const next=await cycleObservations(path,"g",at+3000,cycle("spend",at+3000,{turn:null}));
  assert.equal(next[0].start,at+2000);
  await cycleObservations(path,"g",at,cycle("start",at));
  const replay=await cycleObservations(path,"g",at+4000,cycle("spend",at+4000,{host:"unknown"}));
  assert.equal(replay[0].host,"codex_app");
});


test("scope expansion renews disclosure and fences old observations without undoing disable", async t => {
  const path=join(await fixture(t),"usage.json");
  const ctx={env:{LOOPX_USAGE_PING_ENDPOINT:"http://127.0.0.1:1/v1/ping"},version:"1.0.0",python:"3.13",channel:"source",now:new Date(at)};
  await configure(path,ctx,"enable");
  const prior=JSON.parse(await readFile(path,"utf8")); prior.notice.version=2;
  await writeFile(path,JSON.stringify(prior));
  assert.equal((await inspect(path,ctx)).blocked_by,"notice_required");
  await observe(path,ctx,prior.generation,null,async()=>{throw new Error("unexpected send");},undefined,cycle("start",at));
  await assert.rejects(readFile(path+".cycles"),/ENOENT/);
  const enabled=await configure(path,ctx,"enable"); assert.equal(enabled.notice.version,3);
  const current=JSON.parse(await readFile(path,"utf8")); assert.notEqual(current.generation,prior.generation);
  await configure(path,ctx,"disable");
  assert.equal((await inspect(path,ctx)).consent,"disabled");
});


test("completed diagnostic cycles cannot exhaust capacity and suppress future work", async t => {
  const path=join(await fixture(t),"cycles");
  for(let n=1;n<=129;n++) {
    const identity=n.toString(16).padStart(64,"0");
    await cycleObservations(path,"g",at+n*1000,cycle("start",at+n*1000,{turn:identity}));
    const result=await cycleObservations(path,"g",at+n*1000+500,cycle("spend",at+n*1000+500,{turn:identity}));
    assert.equal(result[0].end-result[0].start,500);
  }
  assert.equal(JSON.parse(await readFile(path,"utf8")).cycles.length,128);
});
