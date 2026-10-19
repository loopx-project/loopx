// Actual packaged checkpoint controls + production owning HTTP/store/controller.
// Workspace navigation is synthetic; target /loopx responses are never fabricated.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {createHash} from "node:crypto";
import {mkdir, readFile, writeFile} from "node:fs/promises";
import {resolve} from "node:path";
import {createInterface} from "node:readline";
import {createServer} from "node:net";
import {resolveTestPython} from "../../../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright, waitForHttp} from "../../../../examples/dashboard-browser-smoke-support.mjs";

const at=process.argv.indexOf("--output");
assert.ok(at>=0 && process.argv[at+1], "Declare a fresh evidence output directory");
const out=resolve(process.argv[at+1]);
await mkdir(out,{recursive:true});
const probe=createServer();probe.listen(0,"127.0.0.1");await once(probe,"listening");
process.env.LOOPX_PERSONAL_WORKSPACE_PORT=String(probe.address().port);
await new Promise((done,reject)=>probe.close(error=>error?reject(error):done()));
process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED="1";
const {repoRoot,port,startServer}=await import("../../../../examples/personal-workspace-browser/fixture.mjs");
const {openWorkspacePage}=await import("../../../../examples/personal-workspace-browser/scenario-context.mjs");
const fixture=spawn(resolveTestPython({repoRoot}),["-u","apps/presentation/dashboard/smoke/checkpoint-recovery-http-fixture.py",resolve(out,"backend")],
  {cwd:repoRoot,stdio:["pipe","pipe","pipe"],windowsHide:true});
const exited=once(fixture,"exit");
const lines=createInterface({input:fixture.stdout});const iterator=lines[Symbol.asyncIterator]();
let diagnostic="";fixture.stderr.on("data",chunk=>{diagnostic+=String(chunk);});
async function next(){const line=await iterator.next();assert.equal(line.done,false,diagnostic);return JSON.parse(line.value);}
async function command(value){fixture.stdin.write(`${value}\n`);return next();}
async function artifact(path){return {path,sha256:createHash("sha256").update(await readFile(path)).digest("hex")};}
let server,browser,workspace;
const requests=[];
try {
  const {origin,session,original_identity}=await next();
  server=await startServer();
  const url=`http://127.0.0.1:${port}/chat/?statusUrl=/status.json`;
  await waitForHttp(url);
  browser=await launchBrowser(loadPlaywright().chromium);
  workspace=await openWorkspacePage({newPage:options=>browser.newPage({locale:"zh-CN",...options})},url,{
    beforeGoto:async(_api,page)=>{
      page.__loopxRuntime.sessions.set(session.session_id,session);
      // Only the unrelated workspace/navigation uses the existing synthetic API.
      // Every target operation, including reads across reload, uses its owning server.
      await page.route(`**/api/chat/sessions/${session.session_id}/loopx`,async route=>{
        const request=route.request();
        const body=request.method()==="POST"?request.postDataJSON():null;
        const response=await route.fetch({url:`${origin}/api/chat/sessions/${session.session_id}/loopx`});
        requests.push({method:request.method(),body,status:response.status(),response:await response.json()});
        await route.fulfill({response});
      });
    },
  });
  const {page}=workspace;
  await page.locator(".personal-goal-link",{hasText:"Product Release"}).click();
  await page.getByRole("navigation",{name:"Goal 视图"}).getByRole("button",{name:"对话",exact:true}).click();
  const section=page.getByRole("region",{name:"LoopX 运行模式"});
  const retained=page.getByText("LoopX · 结果已保留，待协调员核验",{exact:true});
  await retained.waitFor({timeout:60000});
  const before=await command("inspect");
  assert.deepEqual(before.counts,{result:1,debit:0});assert.equal(before.dispatches.length,0);
  await page.reload();await retained.waitFor({timeout:60000});
  const reloaded=await command("inspect");
  assert.deepEqual(reloaded.counts,before.counts);assert.equal(reloaded.dispatches.length,0);
  const writes=()=>requests.filter(row=>row.method==="POST" && !["operations","inspect"].includes(row.body?.operation));
  assert.equal(writes().length,0,"Reload must not dispatch or mutate execution");
  const button=page.getByRole("button",{name:"继续核验",exact:true});
  await button.scrollIntoViewIfNeeded();
  const screenshots=[];
  let path=resolve(out,"retained-desktop.png");await page.screenshot({path,animations:"disabled"});screenshots.push(await artifact(path));
  await button.click();
  let after;const deadline=Date.now()+300000;
  while(Date.now()<deadline){
    after=await command("inspect");assert.deepEqual(after.failures,[]);
    assert.ok(after.counts.debit<=1 && after.dispatches.length<=1);
    if(after.counts.debit===1 && after.decisions.length===1)break;
    await new Promise(done=>setTimeout(done,500));
  }
  assert.deepEqual(after.counts,{result:1,debit:1});assert.equal(after.dispatches.length,1);
  assert.equal(after.result_sha256,before.result_sha256);
  assert.equal(after.upstream_thread_id,before.upstream_thread_id,"Continue retains the original SDK session");
  const posted=writes();
  assert.equal(posted.length,1);assert.equal(posted[0].body.operation,"resume");assert.equal(posted[0].status,200);
  await page.reload();
  await page.getByText("LoopX · 已暂停",{exact:true}).waitFor({timeout:60000});
  assert.equal(await retained.count(),0,"Settled native readback clears the pending-result label");
  const replayed=await command("replay");
  assert.deepEqual(replayed.counts,after.counts);assert.equal(replayed.dispatches.length,1);
  assert.deepEqual(replayed.replayed_identity,original_identity);
  assert.equal(replayed.result_sha256,before.result_sha256);
  await page.setViewportSize({width:390,height:844});await page.emulateMedia({reducedMotion:"reduce"});
  await page.getByRole("button",{name:"恢复推进",exact:true}).scrollIntoViewIfNeeded();
  path=resolve(out,"settled-narrow.png");await page.screenshot({path,animations:"disabled"});screenshots.push(await artifact(path));
  const saved=await command("save");
  assert.equal(writes().length,1);
  assert.equal(workspace.errors.length,0,workspace.errors.join("\n"));
  await writeFile(resolve(out,"http-observations.json"),JSON.stringify(requests,null,2));
  const proof={packaged:true,target_http_mocked:false,transport:"deterministic_sdk",paid_calls:0,
    non_owning_shell_fixture:"existing personal-workspace navigation/status API; target sessions/loopx uses production owner",
    browser_version:browser.version(),clicked_continue_review:true,reloads:2,
    original_identity,settled_identity:saved.settled_identity,replayed_identity:replayed.replayed_identity,
    result_sha256_before:before.result_sha256,result_sha256_after:after.result_sha256,
    business_counts_before:before.counts,business_counts_after:after.counts,business_counts_replayed:replayed.counts,
    direction_dispatches:saved.dispatches.length,implementation_dispatches:0,
    session_id_before:before.session_id,session_id_after:saved.session_id,
    upstream_thread_id_before:before.upstream_thread_id,upstream_thread_id_after:saved.upstream_thread_id,
    errors:workspace.errors,screenshots,native_evidence:[]};
  for(const name of ["before.json","first-result.json","inspect.json","replay.json","save.json"])
    proof.native_evidence.push(await artifact(resolve(out,"backend",name)));
  proof.native_evidence.push(await artifact(resolve(out,"http-observations.json")));
  await writeFile(resolve(out,"proof.json"),JSON.stringify(proof,null,2));
  console.log("checkpoint-recovery: passed (packaged click/reload, production owning HTTP/controller/native checkpoint, original identity/session/result, one debit and zero replay increments; deterministic SDK)");
} catch(error){
  await writeFile(resolve(out,"http-observations-failed.json"),JSON.stringify(requests,null,2));
  await writeFile(resolve(out,"failure.txt"),`${String(error)}\n${diagnostic}`);
  if(workspace)await writeFile(resolve(out,"failure-body.txt"),await workspace.page.locator("body").innerText());
  throw error;
} finally {
  await workspace?.close();await browser?.close();server?.kill("SIGTERM");
  fixture.stdin.end();if(fixture.exitCode===null)await exited;lines.close();
  await writeFile(resolve(out,"backend-stderr.txt"),diagnostic);
}
