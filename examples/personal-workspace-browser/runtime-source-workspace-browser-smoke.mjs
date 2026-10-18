#!/usr/bin/env node
// Focused packaged-UI/real-HTTP proof. All native records here are synthetic.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { launchBrowser, loadPlaywright } from "../dashboard-browser-smoke-support.mjs";
import { resolveTestPython } from "../../scripts/test-python.mjs";

const root = resolve(import.meta.dirname, "../..");
const output = resolve(root, "output/playwright/runtime-source-workspace");
const temporary = await mkdtemp(resolve(tmpdir(), "loopx-runtime-reader-"));
const failFile = resolve(temporary, "unavailable");
const backend = spawn(resolveTestPython(), ["-u", "-c", `
import json, pathlib, sys, threading
from loopx.presentation.runtime_workspace import RuntimeWorkspaceServer
from loopx.capabilities.explore.result_log import build_explore_node_event, build_explore_edge_event
class Source:
    goal_id = 'same-goal'
    def __init__(self, name): self.id = self.label = name
    def read(self):
        if pathlib.Path(sys.argv[1]).exists() and self.id == 'Fresh': raise OSError('unavailable fixture')
        status = dict(ok=True, registry='', runtime_root='', goal_count=1, run_count=0,
            contract=dict(ok=True, summary=dict(errors=0,warnings=0,checks=0), errors=[],warnings=[]),
            attention_queue=dict(available=True,item_count=0,needs_user_or_controller=0,needs_codex=0,watching_external_evidence=0,items=[]),
            run_history=dict(available=True,goal_count=1,run_count=0,recent_runs=[],goals=[dict(
                id=self.goal_id, display_name=self.id+' research',goal_objective='Compare two candidate routes against recorded evidence.',
                registry_member=True,activation_state='active')]))
        events = [build_explore_node_event(goal_id=self.goal_id,node_id='route'+str(i%9),
            title=self.id+' route '+str(i%9),node_kind='question',status='exploring',
            summary='Recorded comparison; native benefit is unknown.',recorded_at='2026-01-01T%02d:%02d:00Z'%(i//4,i%4*15)) for i in range(90)]
        events += [build_explore_edge_event(goal_id=self.goal_id,from_node='route'+str(i),to_node='route'+str(i+1),edge_type='depends_on',recorded_at='2026-01-01T23:%02d:00Z'%(i*30)) for i in range(2)]
        from loopx.presentation.explore_results_api import explore_history_rows
        return status, explore_history_rows(events)
assets=pathlib.Path(sys.argv[2])
servers=[RuntimeWorkspaceServer(Source(n),assets) for n in ('Retained','Fresh')]
catalog=[dict(id=s.source.id,label=s.source.label,status_url=s.status_url) for s in servers]
for s in servers:
    s.catalog=catalog
    threading.Thread(target=s.serve_forever,daemon=True).start()
print(json.dumps(catalog),flush=True)
threading.Event().wait()
`, failFile, process.env.LOOPX_RUNTIME_READER_ASSETS ?? resolve(root, "loopx/web/chat")], { cwd: root, stdio: ["ignore", "pipe", "pipe"] });
let browser;
try {
  const catalog = await new Promise((accept, reject) => {
    const timer = setTimeout(() => reject(new Error("Reader startup timed out")), 15000);
    backend.once("exit", code => { clearTimeout(timer); reject(new Error(`Reader exited ${code}`)); });
    backend.stdout.once("data", bytes => { clearTimeout(timer); accept(JSON.parse(bytes.toString().trim())); });
    backend.stderr.on("data", bytes => process.stderr.write(bytes));
  });
  await mkdir(output, { recursive: true });
  browser = await launchBrowser(loadPlaywright().chromium);
  const page = await browser.newPage({ locale: "zh-CN", viewport: { width: 1512, height: 982 } });
  const errors = [];
  page.on("pageerror", error => errors.push(String(error)));
  const url = new URL("/chat/", catalog[0].status_url);
  url.search = new URLSearchParams({ goalId: "same-goal", view: "overview", statusUrl: process.env.LOOPX_RUNTIME_READER_BEFORE === "1" ? catalog[0].status_url : "/status.json" });
  await page.goto(url.toString());
  await page.getByRole("heading", { name: "Retained research", exact: true }).waitFor();
  if (process.env.LOOPX_RUNTIME_READER_BEFORE === "1") {
    await page.screenshot({path:resolve(output,"before-desktop.png")});
  } else {
    await page.locator(".research-history-coverage").filter({hasText:"92 / 92"}).waitFor();
    assert.equal(await page.getByText("运行来源 · 只读", {exact:true}).count(), 1);
    assert.equal(await page.getByRole("button", {name:"添加 SSH 隧道来源"}).count(),0);
    assert.equal(await page.locator(".personal-goal-order-error").count(),0);
    await page.screenshot({path:resolve(output,"desktop.png")});
    await page.getByLabel("研究历程", {exact:true}).getByRole("button", {name:"关系图", exact:true}).click();
    assert.equal(await page.locator(".research-history-edges > g > path").count(),2);
    await page.locator(".research-history-node").filter({hasText:"Retained route 0"}).click();
    assert.equal(new URL(page.url()).searchParams.get("research_node"),"route0");
    await page.getByLabel("选择控制面来源", {exact:true}).click();
    await page.getByRole("option", {name:"Fresh",exact:true}).click();
    await page.getByRole("heading",{name:"Fresh research",exact:true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("research_node"),null,"a source switch must not rebind the same node id in another physical source");
    await page.locator(".research-history-coverage").filter({hasText:"92 / 92"}).waitFor();
    await page.getByLabel("研究历程", {exact:true}).getByRole("button", {name:"关系图",exact:true}).click();
    await page.locator(".research-history-node").filter({hasText:"Fresh route 0"}).waitFor();
    assert.equal(await page.locator(".research-history-node").filter({hasText:"Retained route"}).count(),0);
    await page.goBack();
    await page.getByRole("heading",{name:"Retained research",exact:true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("research_node"),"route0","Back restores the original source-local selection");
    await page.goForward();
    await page.getByRole("heading",{name:"Fresh research",exact:true}).waitFor();
    await page.reload();
    await page.locator(".research-history-coverage").filter({hasText:"92 / 92"}).waitFor();
    await writeFile(failFile, "unavailable");
    for (let attempt=0;attempt<40;attempt++) {
      if ((await fetch(catalog[1].status_url)).status === 503) break;
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    await page.getByRole("button",{name:"刷新状态",exact:true}).click();
    await page.locator(".personal-status-source-error").waitFor();
    assert.match(await page.getByLabel("选择控制面来源",{exact:true}).innerText(),/Fresh/);
    assert.equal(await page.getByRole("heading",{name:"Retained research",exact:true}).count(),0);
    await rm(failFile);
    await page.getByRole("button",{name:"刷新状态",exact:true}).click();
    await page.getByRole("heading",{name:"Fresh research",exact:true}).waitFor();
    await page.locator(".research-history-coverage").filter({hasText:"92 / 92"}).waitFor();
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:resolve(output,"phone.png")});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
  }
  console.log("runtime source workspace browser smoke: ok");
} finally {
  await browser?.close();
  backend.kill("SIGTERM");
  await rm(temporary,{recursive:true,force:true});
}
