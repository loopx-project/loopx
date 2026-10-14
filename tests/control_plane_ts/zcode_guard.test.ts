import assert from "node:assert/strict";
import test from "node:test";
import {spawn, spawnSync, type ChildProcessWithoutNullStreams} from "node:child_process";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve, sep} from "node:path";
import {fileURLToPath} from "node:url";
import {ZCodeAppServer, terminateOwnedProcess} from "../../loopx/zcode_goal_mode/app-server.ts";
const guardian = fileURLToPath(new URL("../../loopx/zcode_goal_mode/guard.ts",import.meta.url));
const writer = String.raw`const fs=require('node:fs');const path=process.argv[1];let n=0;setInterval(()=>fs.writeFileSync(path,String(++n)),20);`;
const cli = String.raw`const cp=require('node:child_process'),fs=require('node:fs');const path=process.argv[1];let n=0;const owned=cp.spawn(process.execPath,['-e',process.argv[2],path+'.child'],{stdio:'ignore',windowsHide:true});setInterval(()=>fs.writeFileSync(path,String(++n)),20);process.stdout.write(JSON.stringify({childPid:owned.pid})+'\n');process.stdin.resume();`;
async function pulse(path:string):Promise<string|null>{try{return await readFile(path,"utf8");}catch{return null;}}
async function waitFor(fn:()=>Promise<boolean>,timeout=5000):Promise<void>{const end=Date.now()+timeout;while(!(await fn())){if(Date.now()>end)throw Error("Owned process did not reach its expected state");await new Promise(r=>setTimeout(r,20));}}
async function temporary(t:test.TestContext):Promise<string>{const dir=await mkdtemp(join(tmpdir(),"loopx-zcode-guard-test-"));t.after(async()=>{assert.ok(resolve(dir).startsWith(resolve(tmpdir())+sep));await rm(dir,{recursive:true,force:true});});return join(dir,"pulse");}
async function stops(path:string):Promise<void>{await new Promise(r=>setTimeout(r,300));const before=[await pulse(path),await pulse(path+".child")];assert.ok(before.every(v=>v!==null));await new Promise(r=>setTimeout(r,150));assert.deepEqual([await pulse(path),await pulse(path+".child")],before);}
function collect(process:ChildProcessWithoutNullStreams){let output="";process.stdout.on("data",data=>output+=data.toString());process.stderr.on("data",()=>{});return ()=>output;}

test("guardian stdin EOF terminates both directly owned CLI and descendant",async(t)=>{
 const path=await temporary(t);const guard=spawn(process.execPath,["--no-warnings","--experimental-strip-types",guardian,"--",process.execPath,"-e",cli,path,writer],{stdio:"pipe",windowsHide:true});const output=collect(guard);t.after(()=>{guard.kill();});
 await waitFor(async()=>!!(await pulse(path))&&!!(await pulse(path+".child"))&&output().includes("childPid"));
 guard.stdin.end();await waitFor(async()=>guard.exitCode!==null||guard.signalCode!==null);await stops(path);assert.equal(guard.exitCode,0);
});
test("hard-killed broker revokes its pipe guardian and complete owned CLI tree",async(t)=>{
 const path=await temporary(t);
 const brokerScript=String.raw`const cp=require('node:child_process');const child=cp.spawn(process.execPath,JSON.parse(process.argv[1]),{stdio:['pipe','pipe','ignore'],windowsHide:true});child.stdout.pipe(process.stdout);setInterval(()=>{},1000);`;
 const argv=["--no-warnings","--experimental-strip-types",guardian,"--",process.execPath,"-e",cli,path,writer];
 const broker=spawn(process.execPath,["-e",brokerScript,JSON.stringify(argv)],{stdio:"pipe",windowsHide:true});const output=collect(broker);t.after(()=>{broker.kill();});
 await waitFor(async()=>!!(await pulse(path))&&!!(await pulse(path+".child"))&&output().includes("childPid"));
 broker.kill("SIGKILL");await waitFor(async()=>broker.exitCode!==null||broker.signalCode!==null);await stops(path);
});

// The descendant deliberately ignores TERM and closes its inherited pipes.
// Independent ps observation proves it cannot execute when cleanup returns.
const persistentChild = String.raw`require('node:fs').writeFileSync(process.argv[1]+'.ready','ready');process.on('SIGTERM',()=>{});setInterval(()=>{},1000);`;
const ownedFixture = String.raw`const cp=require('node:child_process'),fs=require('node:fs'),rl=require('node:readline');const owned=cp.spawn(process.execPath,['-e',process.argv[3],process.argv[1]],{stdio:'ignore'});fs.writeFileSync(process.argv[1],JSON.stringify({leader:process.pid,child:owned.pid}));rl.createInterface({input:process.stdin}).on('line',line=>{const r=JSON.parse(line);process.stdout.write(JSON.stringify({id:r.id,result:{independentPlanState:true}})+'\n');});if(process.argv[2]==='exit')setTimeout(()=>process.exit(0),100);`;
function executable(pid:number):boolean {
 const result=spawnSync('ps',['-A','-o','pid=','-o','stat='],{encoding:'utf8',timeout:1000});
 assert.equal(result.status,0,result.stderr);
 return result.stdout.split('\n').some(line=>{const fields=line.trim().split(/\s+/);return Number(fields[0])===pid&&!fields[1]?.startsWith('Z');});
}
async function fixturePids(path:string):Promise<{leader:number;child:number}>{
 await waitFor(async()=>!!(await pulse(path))&&!!(await pulse(path+'.ready')));
 return JSON.parse((await pulse(path))!);
}
function scopedFallback(leader:number):void{try{process.kill(-leader,'SIGKILL');}catch{/* Own fixture group already gone. */}}
const posix={skip:process.platform==='win32'};
test('normal app-server close waits for guardian native group quiescence',posix,async(t)=>{
 const path=await temporary(t);
 const server=new ZCodeAppServer([process.execPath,'--no-warnings','--experimental-strip-types',guardian,'--',process.execPath,'-e',ownedFixture,path,'running',persistentChild],process.cwd(),{...process.env},{guardian:true});
 await server.initialize();const pids=await fixturePids(path);
 t.after(()=>scopedFallback(pids.leader));assert.equal(executable(pids.child),true);
 await server.close();assert.equal(executable(pids.leader),false);assert.equal(executable(pids.child),false);
 await server.close();
});
test('guardian reaps descendants before reporting native leader exit',posix,async(t)=>{
 const path=await temporary(t);const guard=spawn(process.execPath,['--no-warnings','--experimental-strip-types',guardian,'--',process.execPath,'-e',ownedFixture,path,'exit',persistentChild],{stdio:'pipe'});collect(guard);
 const pids=await fixturePids(path);t.after(()=>{scopedFallback(pids.leader);guard.kill('SIGKILL');});
 await waitFor(async()=>guard.exitCode!==null||guard.signalCode!==null);
 assert.equal(guard.exitCode,0);assert.equal(executable(pids.child),false);
});
test('owned cleanup retains descendant ownership after leader reaping',posix,async(t)=>{
 const path=await temporary(t);const leader=spawn(process.execPath,['-e',ownedFixture,path,'exit',persistentChild],{stdio:'pipe',detached:true});collect(leader);
 const pids=await fixturePids(path);t.after(()=>scopedFallback(pids.leader));
 await waitFor(async()=>leader.exitCode!==null);assert.equal(executable(pids.child),true);
 await terminateOwnedProcess(leader);assert.equal(executable(pids.child),false);
});


test('failed guardian cleanup stays rejected on repeated close',async(t)=>{
 await temporary(t);
 const failedGuardian=String.raw`const rl=require('node:readline');process.on('SIGTERM',()=>process.exit(1));rl.createInterface({input:process.stdin}).on('line',line=>{const r=JSON.parse(line);process.stdout.write(JSON.stringify({id:r.id,result:{independentPlanState:true}})+'\n');});process.stdin.on('end',()=>process.exit(1));`;
 const server=new ZCodeAppServer([process.execPath,'-e',failedGuardian],process.cwd(),{...process.env},{guardian:true});
 await server.initialize();
 await assert.rejects(server.close(),/process_cleanup_unconfirmed/);
 await assert.rejects(server.close(),/process_cleanup_unconfirmed/);
});
