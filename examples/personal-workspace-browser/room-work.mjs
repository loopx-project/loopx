import assert from "node:assert/strict";
import {spawn, spawnSync} from "node:child_process";
import {mkdtemp, realpath, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import {createInterface} from "node:readline";
import {repoRoot, outputDir} from "./fixture.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";
import {testPythonCommand} from "../../scripts/test-python.mjs";

const connection = {agent_id:"agent-a",connection_id:"agent-a",conversation_kind:"goal",app_label:"Fixture Bot",app_ref:"room-fixture",
  chat_name:"Product group",enabled:true,capture_scope:"addressed_only",goal_id:"product-release",goal_title:"Product Release",
  health_error_code:null,incoming_mode:"mentions",ingress_mode:"async_inbox",event_count:1,last_event_reason:null,last_event_status:"replied_and_acknowledged",
  listener_error_code:null,listener_status:"listening",replied_count:1,reply_ready:true,reply_mode:"topic_reply",target_ref:"fixture",topic_name:"Product Release",topic_setup_required:false};

export const roomWorkScenario = {
  id: "room-work",
  async run({browser, url}) {
    const root = await realpath(await mkdtemp(join(tmpdir(), "loopx-room-work-browser-")));
    const code = `import runpy; runpy.run_path(${JSON.stringify(resolve(repoRoot,"examples/personal-workspace-browser/room_work_fixture.py"))},run_name="__main__")`;
    const python = testPythonCommand(code);
    const child = spawn(python.command, [...python.args, root], {cwd:repoRoot, stdio:["ignore","pipe","pipe"]});
    let diagnostics="", context;
    child.stderr.on("data", x => {diagnostics=(diagnostics+x).slice(-8000);});
    const exited = new Promise(accept => child.once("exit",accept));
    try {
      const ready = await new Promise((accept,reject) => {
        const lines=createInterface({input:child.stdout});
        const timer=setTimeout(()=>reject(new Error(`room backend readiness: ${diagnostics}`)),30000);
        lines.once("line", line=>{clearTimeout(timer);lines.close();try{accept(JSON.parse(line));}catch(e){reject(e);}});
        child.once("exit", code=>{clearTimeout(timer);reject(new Error(`room backend exited ${code}: ${diagnostics}`));});
      });
      const backend=`http://127.0.0.1:${ready.port}`;
      const before=await (await fetch(backend+"/fixture/readback")).json();
      const submitted=[], responses=[];
      let readMode=null;
      context=await openWorkspacePage(browser,url,{beforeGoto:async(api,page)=>{
        api.larkConnections.push(connection);
        // This room fixture has no separate private-conversation bindings.
        // Supply that companion region's real empty readback shape.
        await page.route("**/api/chat/lark/private-conversations",async route=>{
          assert.equal(route.request().method(),"GET");
          await route.fulfill({json:{ok:true,revision:0,connections:[],stop_recoveries:[]}});
        });
        await page.route("**/api/chat/projects",async route=>{
          assert.equal(route.request().method(),"GET");
          await route.fulfill({json:{ok:true,projects:[]}});
        });
        await page.route("**/api/chat/lark/room-work**",async route=>{
          if(route.request().method()==="POST")submitted.push(route.request().postDataJSON());
          const response=await fetchWorkspaceBackend(route,backend);
          const payload=await response.json();
          if (route.request().method()==="GET" && readMode) {
            // Consumer negatives use copies of a real response. Producer source
            // facts and envelope decisions are independently checked over HTTP.
            if (readMode==="unknown") delete payload.projection_envelope;
            else payload.projection_envelope={...payload.projection_envelope,
              fresh:readMode==="incomplete",complete:readMode!=="incomplete",alert:true,
              alert_reasons:[readMode==="incomplete"?"incomplete_coverage":readMode==="stale"?"stale_sources":"unreadable_sources"]};
          }
          responses.push(payload);await route.fulfill({response,json:payload});
        });
      }});
      const {page}=context;
      async function open() {
        await page.getByRole("button",{name:"设置",exact:true}).click();
        await page.locator(".personal-settings-tabs").getByRole("button",{name:"Lark",exact:true}).click();
        await page.screenshot({path:resolve(outputDir,"room-work-connections.png"),animations:"disabled"});
        await page.getByRole("button",{name:"房间工作",exact:true}).click();
        await page.getByRole("button",{name:"刷新当前状态"}).waitFor();
        await page.getByRole("button",{name:"刷新当前状态"}).waitFor({state:"visible"});
        await page.waitForFunction(()=>!document.querySelector("[role=dialog] button:disabled"));
      }
      await open();
      await page.screenshot({path:resolve(outputDir,"room-work-before-claim.png"),animations:"disabled"});
      await page.getByRole("dialog").screenshot({path:resolve(outputDir,"room-work-dialog-desktop.png"),animations:"disabled"});
      for (const mode of ["unknown","stale","unreadable","incomplete"]) {
        readMode=mode;
        await page.getByRole("button",{name:"刷新当前状态"}).click();
        await page.getByText("此读数可能已过时、不完整或未经核验。请刷新后再领取或确认归属。",{exact:true}).waitFor();
        assert.equal(await page.getByRole("button",{name:"领取任务并发送凭据"}).isDisabled(),true);
        assert.equal(await page.locator(".personal-room-work-counts").count(),0);
        assert.equal(submitted.length,0,"old, unknown or partial readings cannot submit new claims");
        if (mode==="stale") await page.getByRole("dialog").screenshot({path:resolve(outputDir,"room-work-stale.png"),animations:"disabled"});
      }
      readMode=null;
      await page.getByRole("button",{name:"刷新当前状态"}).click();
      await page.waitForFunction(()=>!document.querySelector(".personal-primary-action:disabled"));
      await page.locator(".personal-room-work-counts").waitFor();
      await page.evaluate(()=>{
        window.__roomWorkSetItem = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key,value) {
          if (this === sessionStorage && key.startsWith("loopx.room-work:")) throw new DOMException("fixture storage unavailable");
          return window.__roomWorkSetItem.call(this,key,value);
        };
      });
      await page.getByRole("button",{name:"领取任务并发送凭据"}).click();
      await page.getByText("浏览器无法保存恢复请求。请启用会话存储后再领取。",{exact:true}).waitFor();
      assert.equal(submitted.length,0,"unrecoverable browser storage cannot start a claim");
      await page.evaluate(()=>{Storage.prototype.setItem = window.__roomWorkSetItem; delete window.__roomWorkSetItem;});
      await fetch(backend+"/fixture/lose-message-response",{method:"POST"});
      await page.getByRole("button",{name:"领取任务并发送凭据"}).evaluate(button=>{button.click();button.click();});
      await page.getByText("任务已领取，房间消息仍需恢复。",{exact:true}).waitFor();
      assert.equal(submitted.length,1,"synchronous duplicate click does not mint another request");
      const accepted=await (await fetch(backend+"/fixture/readback")).json();
      assert.notEqual(accepted.snapshot.provider_revision,before.snapshot.provider_revision);
      await page.reload({waitUntil:"networkidle"});
      await page.getByTestId("personal-goal-home").waitFor();
      await open();
      await page.getByRole("button",{name:"核对此次领取"}).click();
      await page.getByText("历史领取已接受",{exact:true}).waitFor();
      await page.getByText("房间凭据已确认",{exact:true}).waitFor();
      assert.deepEqual(submitted[1],submitted[0],"reload recovery reuses exact intent and original key");
      const final=await (await fetch(backend+"/fixture/readback")).json();
      assert.equal(final.messages,1);assert.equal(final.snapshot.provider_revision,accepted.snapshot.provider_revision);
      assert.equal(final.snapshot.todos[0].claimed_by,"agent-a");
      const cli=testPythonCommand(`from loopx.cli import main; main(["--registry",${JSON.stringify(ready.registry)},"--format","json","todo","list","--goal-id","product-release"])`);
      const direct=spawnSync(cli.command,cli.args,{cwd:repoRoot,encoding:"utf8"});
      assert.equal(direct.status,0,direct.stderr);assert.equal(JSON.parse(direct.stdout).todos[0].claimed_by,"agent-a");
      await page.getByRole("button",{name:"刷新当前状态"}).click();
      await page.getByText("最近回读的当前归属: agent-a",{exact:true}).waitFor();
      await page.getByRole("button",{name:"刷新当前状态",exact:true}).click({trial:true});
      assert.equal(await page.getByRole("dialog").getByRole("alert").count(),0);
      assert.equal(await page.locator(".personal-private-conversation [role=alert]").count(),0);
      assert.equal(JSON.stringify(responses).includes("Synthetic private task text"),false);
      assert.ok(responses.every(p=>p.execution_authority_granted===false));
      await page.screenshot({path:resolve(outputDir,"room-work-recovered.png"),animations:"disabled"});
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:resolve(outputDir,"room-work-mobile.png"),animations:"disabled"});
      await page.getByRole("dialog").screenshot({path:resolve(outputDir,"room-work-dialog-mobile.png"),animations:"disabled"});
      await page.keyboard.press("Escape");
      await page.getByRole("dialog").waitFor({state:"hidden"});
      await page.waitForFunction(()=>document.activeElement?.textContent === "房间工作");
      await page.getByRole("button",{name:"房间工作",exact:true}).click();
      await page.getByRole("dialog").waitFor();
      await page.keyboard.press("Escape");
      await page.getByRole("dialog").waitFor({state:"hidden"});
      await page.setViewportSize({width:1512,height:982});
      await page.addInitScript(()=>localStorage.setItem("loopx-pw-locale","en"));
      await page.reload({waitUntil:"networkidle"});
      await page.getByTestId("personal-goal-home").waitFor();
      await page.getByRole("button",{name:"Settings",exact:true}).click();
      await page.locator(".personal-settings-tabs").getByRole("button",{name:"Lark",exact:true}).click();
      await page.getByRole("button",{name:"Room work",exact:true}).click();
      await page.getByText("Available tasks",{exact:true}).waitFor();
      await page.keyboard.press("Tab");
      assert.equal(await page.getByRole("button",{name:"Close room work"}).evaluate(button=>button===document.activeElement),true);
      await page.keyboard.press("Shift+Tab");
      assert.equal(await page.getByRole("button",{name:"Refresh current state"}).evaluate(button=>button===document.activeElement),true);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:resolve(outputDir,"room-work-english.png"),animations:"disabled"});
      return {coverageEntries:context.coverageEntries,errors:context.errors,note:"Packaged room UI -> real File authority, explicit claim, duplicate click, uncertain delivery plus reload/same-key recovery, one room card and independent CLI current-owner readback; provider transport and connection discovery synthetic."};
    } finally {
      await context?.close();child.kill("SIGTERM");const timer=setTimeout(()=>child.kill("SIGKILL"),5000);
      try{await exited;}finally{clearTimeout(timer);await rm(root,{recursive:true,force:true,maxRetries:10,retryDelay:100});}
    }
  },
};
