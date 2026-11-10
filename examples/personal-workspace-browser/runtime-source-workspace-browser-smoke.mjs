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
import hashlib, json, pathlib, sys, threading
from datetime import datetime, timedelta, timezone
from loopx.presentation.runtime_workspace import RuntimeWorkspaceServer
from loopx.control_plane.status.goal_source import GoalSource, GoalDocumentSnapshot
from loopx.capabilities.explore.result_log import build_explore_node_event, build_explore_edge_event
class Source(GoalSource):
    def __init__(self, name):
        goal_id = 'other-goal' if name == 'Distinct' else 'same-goal'
        folder = pathlib.Path(sys.argv[1]).parent / name
        folder.mkdir()
        document = folder / 'captured.md'
        document.write_text('# '+name+' original task\\n\\n## Accepted outcome\\n\\n'+
            'Compare complete outcomes, including zero, negative evidence and unknown costs. '*45+
            '\\n\\n<script>window.documentExecuted=true</script>\\n\\n## Original boundary\\n\\nNo extra execution authority.\\n')
        registry = folder / 'registry.json'
        declared = '/original/task.md'
        registry.write_text(json.dumps(dict(goals=[dict(id=goal_id,repo='/original',authority_sources=[
            dict(kind='goal_doc',role='primary_goal_document',path=declared)])])))
        super().__init__(name,name,str(registry),'/runtime','/cli',sys.executable,'/bin','local','host',goal_id,
            goal_document_snapshot=GoalDocumentSnapshot(declared,str(document),hashlib.sha256(document.read_bytes()).hexdigest()))
    def read_status(self, goal_id=None):
        self.check_access(goal_id)
        if pathlib.Path(sys.argv[1]).exists() and self.id == 'Fresh': raise OSError('unavailable fixture')
        return dict(ok=True, registry='', runtime_root='', goal_count=1, run_count=0,
            contract=dict(ok=True, summary=dict(errors=0,warnings=0,checks=0), errors=[],warnings=[]),
            attention_queue=dict(available=True,item_count=0,needs_user_or_controller=0,needs_codex=0,watching_external_evidence=0,items=[]),
            run_history=dict(available=True,goal_count=1,run_count=0,recent_runs=[],goals=[dict(
                id=self.goal_id, display_name=self.id+' research',goal_objective='Compare two candidate routes against recorded evidence.',
                registry_member=True,activation_state='active')]))
    def read_run_history(self, goal_id):
        self.check_access(goal_id)
        rows=[]
        for i in range(23):
            rows.append(dict(goal_id=self.goal_id,agent_id='source-worker',classification='state_recorded',
                json_path='native-record-'+str(i)+'.json',
                generated_at='' if i == 7 else (datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(minutes=41*i)).isoformat(),
                agent_vision=dict(schema_version='goal_vision_replan_contract_v0',agent_id='source-worker',
                    vision_patch=dict(vision_summary=self.id+' original objective '+str(i),acceptance_summary='Compare independently checked outcomes.'),
                    path_delta=dict(prior_assumption='A candidate might improve the original outcome.',
                        observed_reality='Negative result retained. Actual benefit remains unknown. '+('完整原文。 '*80),
                        changed=['Revisit the candidate against the same acceptance.'],retained=['Original outcome and cost boundary.'],
                        stopped=['Rejected candidate: negative evidence.'],unresolved_questions=['Did the next action adopt this plan?']),
                    stride_decision=dict(next_probe=dict(scope='Record a discriminating next probe.',prediction='No gain without new evidence.',
                        adoption_criterion='Independent acceptance.',cost_bound='Existing authorized bound.',minimum_evidence='Original result and complete cost.'),
                        previous_review=dict(lesson='A replan record does not demonstrate adoption.')))))
        return dict(ok=True,goal_filter=self.goal_id,goals=[dict(id=self.goal_id,registry_member=True,unique_runs=len(rows),latest_runs=rows)])
    def read_registry_goal(self, goal_id):
        self.check_access(goal_id)
        return json.loads(pathlib.Path(self.registry).read_text())
    def read_events(self, goal_id):
        self.check_access(goal_id)
        if self.id == 'Distinct': return []
        events = [build_explore_node_event(goal_id=self.goal_id,node_id='route'+str(i%9),
            title=self.id+' route '+str(i%9),node_kind='question',status='exploring',
            summary='Recorded comparison; native benefit is unknown.',recorded_at='2026-01-01T%02d:%02d:00Z'%(i//4,i%4*15)) for i in range(90)]
        events += [build_explore_edge_event(goal_id=self.goal_id,from_node='route'+str(i),to_node='route'+str(i+1),edge_type='depends_on',recorded_at='2026-01-01T23:%02d:00Z'%(i*30)) for i in range(2)]
        return events
assets=pathlib.Path(sys.argv[2])
servers=[RuntimeWorkspaceServer(Source(n),assets,analysis_runtime_root=pathlib.Path(sys.argv[1]).parent / "analysis") for n in ('Retained','Fresh','Distinct')]
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
  const page = await browser.newPage({ locale: "zh-CN", reducedMotion: "reduce", viewport: { width: 1512, height: 982 } });
  const errors = [];
  page.on("pageerror", error => errors.push(String(error)));
  const url = new URL("/chat/", catalog[0].status_url);
  url.search = new URLSearchParams({ goalId: "same-goal", view: "overview", statusUrl: process.env.LOOPX_RUNTIME_READER_BEFORE === "1" ? catalog[0].status_url : "/status.json",
    messageSession: "saved-session", message: "saved-answer" });
  let holdAnalysis = process.env.LOOPX_RUNTIME_READER_BEFORE !== "1", failAnalysis = false, releaseAnalysis, readStarted;
  let analysisWrites = 0;
  let strategyReads = 0, failStrategies = false;
  let documentReads = 0, failDocument = false;
  await page.route("**/api/chat/goal-source-document?*", async route => {
    assert.equal(route.request().method(),"GET"); documentReads++;
    if (failDocument) return route.fulfill({status:503,json:{error:"Synthetic document failure"}});
    return route.continue();
  });
  await page.route("**/api/chat/goal-source-strategies?*", async route => {
    assert.equal(route.request().method(), "GET"); strategyReads++;
    if (failStrategies) return route.fulfill({status:503,json:{error:"Synthetic source failure"}});
    return route.continue();
  });
  await page.route("**/api/chat/research-analysis?*", async route => {
    if (route.request().method() !== "GET") {analysisWrites++; return route.abort();}
    if (holdAnalysis) {readStarted(); await new Promise(resolve => {releaseAnalysis = resolve;});}
    if (failAnalysis) return route.fulfill({status:503, json:{error:"Synthetic analysis transport failure"}});
    return route.continue();
  });
  await page.goto(url.toString());
  await page.getByRole("heading", { name: "Retained research", exact: true }).waitFor();
  if (process.env.LOOPX_RUNTIME_READER_BEFORE === "1") {
    await page.locator(".personal-goal-tabs").getByRole("button",{name:"研究",exact:true}).click();
    await page.locator(".research-history-coverage").filter({hasText:"92 / 92"}).waitFor();
    await page.screenshot({path:resolve(output,"before-desktop.png")});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:resolve(output,"before-phone.png")});
  } else {
    await page.waitForFunction(() => new URL(location.href).searchParams.get("statusUrl")?.includes("source_namespace"));
    assert.equal(new URL(page.url()).searchParams.get("messageSession"), "saved-session", "initial alias binding preserves the original answer's Session");
    assert.equal(new URL(page.url()).searchParams.get("message"), "saved-answer", "initial alias binding preserves the original answer bookmark");
    const readOnlyNotice = page.locator(".personal-read-only-notice");
    await readOnlyNotice.locator(":scope > strong").filter({hasText:"只读来源"}).waitFor();
    assert.doesNotMatch(await readOnlyNotice.innerText(), /你可以查看/, "The permission state stays visible without a default explanation paragraph");
    await readOnlyNotice.getByRole("button", {name:"只读来源",exact:true}).focus();
    const explanation = page.locator(".context-help-popover:popover-open").filter({hasText:"写入、纠偏和 Agent 会话仍留在来源主机"});
    await explanation.waitFor();
    await page.keyboard.press("Escape");
    await explanation.waitFor({state:"hidden"});
    const initialRead = new Promise(resolve => {readStarted = resolve;});
    await page.locator(".personal-goal-tabs").getByRole("button", {name:"研究", exact:true}).click();
    await initialRead;
    await page.locator(".research-history-coverage").filter({hasText:"92 / 92"}).waitFor();
    const history = page.getByRole("region", {name:"研究历程",exact:true});
    await page.screenshot({path:resolve(output,"analysis-pending-desktop.png")});
    assert.doesNotMatch(await history.innerText(),/尚未接入深度分析|尚无可分析/,
      "A pending read does not prove an analysis connection is absent");
    await history.getByRole("status",{name:"正在读取分析"}).waitFor();
    failAnalysis = true; holdAnalysis = false; releaseAnalysis();
    const failure = history.getByRole("alert");
    await failure.waitFor();
    assert.equal(await history.locator(".research-analysis").count(),0,"Retry is available without opening the analysis panel");
    await page.screenshot({path:resolve(output,"analysis-failure-desktop.png")});
    const retryRead = new Promise(resolve => {readStarted = resolve;});
    holdAnalysis = true;
    await failure.getByRole("button",{name:"重试读取",exact:true}).click();
    await retryRead;
    assert.equal(await failure.getByRole("button",{name:"重试读取",exact:true}).isDisabled(),true);
    assert.doesNotMatch(await history.innerText(),/尚未接入深度分析|尚无可分析/);
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:resolve(output,"analysis-retry-phone.png")});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    failAnalysis = false; holdAnalysis = false; releaseAnalysis();
    await failure.waitFor({state:"hidden"});
    await history.locator(".analysis-read-loading").waitFor({state:"hidden"});
    assert.equal(analysisWrites,0,"Read recovery never starts an analysis");
    await page.setViewportSize({width:1512,height:982});
    assert.equal(documentReads,0,"Ordinary research never eagerly reads task documents");
    const originalTask = page.locator(".source-goal-document");
    await originalTask.locator(":scope > summary").click();
    await originalTask.getByRole("heading",{name:"Retained original task",exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>window.documentExecuted),undefined,"Original document text cannot execute HTML");
    assert.ok(await originalTask.locator("article p").first().evaluate(element=>parseFloat(getComputedStyle(element).fontSize)>=16));
    await page.screenshot({path:resolve(output,"original-task-desktop.png")});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:resolve(output,"original-task-phone.png")});
    failDocument = true;
    await originalTask.getByRole("button",{name:"刷新原始任务",exact:true}).click();
    await originalTask.getByRole("alert").waitFor();
    await originalTask.getByRole("heading",{name:"Retained original task",exact:true}).waitFor();
    await page.screenshot({path:resolve(output,"original-task-failure-phone.png")});
    failDocument = false;
    await originalTask.getByRole("button",{name:"重试读取原始任务",exact:true}).click();
    await originalTask.getByRole("alert").waitFor({state:"hidden"});
    await originalTask.locator("article").focus();await page.keyboard.press("Escape");
    assert.equal(await originalTask.getAttribute("open"),null);
    await page.waitForFunction(()=>document.activeElement?.matches('.source-goal-document > summary'));
    await page.setViewportSize({width:1512,height:982});
    assert.equal(strategyReads,0,"Normal Explore reading does not eagerly read strategy history");
    await page.screenshot({path:resolve(output,"strategies-before-desktop.png")});
    await page.locator(".research-strategy-entry").click();
    const strategies = page.getByRole("region",{name:"策略历史",exact:true});
    const chronology = strategies.getByRole("list",{name:"全部策略记录",exact:true});
    await chronology.getByText("Retained original objective 22",{exact:true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("research_state"),"all","Opening history keeps the whole chronology instead of selecting the latest record");
    assert.equal(await chronology.locator("li").count(),23,"The overview retains every version beyond the status window");
    assert.match(await chronology.locator("li").first().innerText(),/Retained original objective 0/);
    assert.match(await chronology.locator("li").last().innerText(),/时间未记录[\s\S]*Retained original objective 7/,"Unknown recorded times stay readable without inventing a date");
    const span = await strategies.locator(".source-strategies-meta").first().locator("time").evaluateAll(elements=>elements.map(element=>element.getAttribute("datetime")));
    assert.equal(Date.parse(span[1])-Date.parse(span[0]),15*3600000+2*60000,"The overview spans the complete fifteen-hour record window");
    const earliestStrategy = await chronology.locator("button").first().getAttribute("data-strategy-id");
    await page.screenshot({path:resolve(output,"strategies-overview-desktop.png")});
    await chronology.getByRole("button").filter({hasText:"Retained original objective 22"}).click();
    assert.ok(await strategies.getByText("Retained original objective 22",{exact:true}).evaluate(element=>parseFloat(getComputedStyle(element).fontSize)>=16),"Original prose keeps the reading-body size");
    const strategySelect = strategies.getByRole("combobox",{name:"策略记录",exact:true});
    assert.equal(await strategySelect.locator("option").count(),23);
    const newestStrategy = new URL(page.url()).searchParams.get("research_state");
    assert.notEqual(newestStrategy,earliestStrategy);
    await page.screenshot({path:resolve(output,"strategies-desktop.png")});
    await strategySelect.selectOption(earliestStrategy);
    await strategies.getByText("Retained original objective 0",{exact:true}).waitFor();
    assert.equal(strategyReads,1,"Switching original versions reuses the qualified snapshot");
    await page.goBack();
    await strategies.getByText("Retained original objective 22",{exact:true}).waitFor();
    await page.goForward();
    await strategies.getByText("Retained original objective 0",{exact:true}).waitFor();
    await page.reload();
    await strategies.getByText("Retained original objective 0",{exact:true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("research_state"),earliestStrategy);
    await page.getByLabel("选择控制面来源",{exact:true}).click();
    await page.getByRole("option",{name:"Fresh",exact:true}).click();
    await page.getByRole("heading",{name:"Fresh research",exact:true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("research_state"),null);
    assert.equal(await strategies.count(),0,"A source switch clears the old strategy reading selection");
    assert.equal(await originalTask.locator("article").count(),0,"A source switch clears the previous document synchronously");
    await originalTask.locator(":scope > summary").click();
    await originalTask.getByRole("heading",{name:"Fresh original task",exact:true}).waitFor();
    assert.equal(await originalTask.getByRole("heading",{name:"Retained original task",exact:true}).count(),0);
    await originalTask.locator(":scope > summary").click();
    await page.goBack();
    await strategies.getByText("Retained original objective 0",{exact:true}).waitFor();
    await page.setViewportSize({width:390,height:844});
    await strategies.getByRole("heading",{name:"策略历史",exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(output,"strategies-phone.png")});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await strategies.getByText("完整原始记录与来源",{exact:true}).click();
    await strategies.locator("details pre").filter({hasText:"Rejected candidate: negative evidence."}).waitFor();
    assert.match(await strategies.locator("details pre").innerText(),/A replan record does not demonstrate adoption/);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    failStrategies = true;
    await strategies.getByRole("button",{name:"刷新策略历史",exact:true}).click();
    await strategies.getByRole("alert").waitFor();
    await strategies.getByText("Retained original objective 0",{exact:true}).waitFor();
    await strategies.getByRole("alert").scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(output,"strategies-failure-phone.png")});
    failStrategies = false;
    await strategies.getByRole("button",{name:"重试",exact:true}).click();
    await strategies.getByRole("alert").waitFor({state:"hidden"});
    const absentStrategy = new URL(page.url()); absentStrategy.searchParams.set("research_state","f".repeat(64));
    await page.goto(absentStrategy.href);
    await strategies.getByText("原来源中找不到所选版本。可选择其他记录。",{exact:true}).waitFor();
    assert.equal(await strategies.getByRole("article",{name:"策略原文",exact:true}).count(),0,"Missing exact versions never substitute the latest one");
    await strategySelect.selectOption(earliestStrategy);
    await strategies.getByRole("button",{name:"返回策略总览",exact:true}).click();
    await page.waitForFunction(id=>document.activeElement?.dataset.strategyId===id,earliestStrategy);
    await page.screenshot({path:resolve(output,"strategies-overview-phone.png")});
    await strategies.getByRole("button",{name:"返回研究历程",exact:true}).focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(()=>document.activeElement?.classList.contains("research-strategy-entry"));
    await page.setViewportSize({width:1512,height:982});
    await page.locator(".research-history-coverage").filter({hasText:"92 / 92"}).waitFor();
    assert.equal(new URL(new URL(page.url()).searchParams.get("statusUrl")).searchParams.get("source_namespace"),new URL(catalog[0].status_url).searchParams.get("source_namespace"),"the initial relative entry binds its physical source before subsequent navigation");
    assert.equal(await page.getByText("运行来源 · 只读", {exact:true}).count(), 1);
    assert.equal(await page.getByRole("button", {name:"添加 SSH 隧道来源"}).count(),0);
    assert.equal(await page.locator(".personal-goal-order-error").count(),0);
    await page.screenshot({path:resolve(output,"desktop.png")});
    await page.getByLabel("研究历程", {exact:true}).getByRole("button", {name:"关系图", exact:true}).click();
    assert.equal(await page.locator(".research-history-edges > g > path").count(),2);
    await page.locator(".research-history-node").filter({hasText:"Retained route 0"}).click();
    assert.equal(new URL(page.url()).searchParams.get("research_node"),"route0");
    // Even an unavailable finding in a bookmarked URL belongs to this source.
    const findingLink = new URL(page.url()); findingLink.searchParams.set("research_finding", "source-local-finding");
    findingLink.searchParams.set("messageSession", "saved-session"); findingLink.searchParams.set("message", "saved-answer");
    await page.goto(findingLink.href);
    await page.locator(".research-history-detail").waitFor();
    await page.getByLabel("选择控制面来源", {exact:true}).click();
    await page.getByRole("option", {name:"Fresh",exact:true}).click();
    await page.getByRole("heading",{name:"Fresh research",exact:true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("research_node"),null,"a source switch must not rebind the same node id in another physical source");
    assert.equal(new URL(page.url()).searchParams.get("research_finding"),null,"a source switch clears finding focus alongside node focus");
    assert.equal(new URL(page.url()).searchParams.get("messageSession"), null, "a source switch clears the original answer's Session focus");
    assert.equal(new URL(page.url()).searchParams.get("message"), null, "a source switch never rebinds an answer id in another physical source");
    await page.locator(".research-history-coverage").filter({hasText:"92 / 92"}).waitFor();
    await page.getByLabel("研究历程", {exact:true}).getByRole("button", {name:"关系图",exact:true}).click();
    await page.locator(".research-history-node").filter({hasText:"Fresh route 0"}).waitFor();
    assert.equal(await page.locator(".research-history-node").filter({hasText:"Retained route"}).count(),0);
    await page.goBack();
    assert.equal(await page.getByLabel("研究历程", {exact:true}).getByRole("button", {name:"时间线",exact:true}).getAttribute("aria-pressed"), "true", "Back restores the previous lens before leaving the source");
    await page.goBack();
    await page.getByRole("heading",{name:"Retained research",exact:true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("research_node"),"route0","Back restores the original source-local selection");
    assert.equal(new URL(page.url()).searchParams.get("research_finding"),"source-local-finding");
    assert.equal(new URL(page.url()).searchParams.get("messageSession"), "saved-session", "Back restores the original source's Session focus");
    assert.equal(new URL(page.url()).searchParams.get("message"), "saved-answer", "Back restores the original source's answer bookmark");
    await page.goForward();
    await page.getByRole("heading",{name:"Fresh research",exact:true}).waitFor();
    await page.reload();
    await page.locator(".research-history-coverage").filter({hasText:"92 / 92"}).waitFor();
    await page.goBack();
    await page.getByRole("heading",{name:"Retained research",exact:true}).waitFor();
    // Different Goal ids exercise automatic selection cleanup as well as the
    // source-local node fence above. Cleanup must not rewrite the old source's
    // entry or add an extra entry that traps Back/Forward on the new source.
    await page.getByLabel("选择控制面来源",{exact:true}).click();
    await page.getByRole("option",{name:"Distinct",exact:true}).click();
    await page.getByRole("button",{name:/Distinct research/}).first().waitFor();
    await page.waitForFunction(()=>new URL(location.href).searchParams.get("goalId")==="");
    await page.goBack();
    await page.getByRole("heading",{name:"Retained research",exact:true}).waitFor({timeout:10000});
    assert.equal(new URL(page.url()).searchParams.get("goalId"),"same-goal");
    assert.equal(new URL(page.url()).searchParams.get("research_node"),"route0");
    await page.screenshot({path:resolve(output,"source-return-desktop.png")});
    await page.goForward();
    await page.getByRole("button",{name:/Distinct research/}).first().waitFor();
    await page.getByRole("button",{name:/Distinct research/}).first().click();
    await page.getByRole("heading",{name:"Distinct research",exact:true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("goalId"),"other-goal");
    await page.reload();
    await page.getByRole("heading",{name:"Distinct research",exact:true}).waitFor();
    await page.locator(".personal-goal-tabs").getByRole("button",{name:"研究",exact:true}).click();
    await page.getByText("这份来源没有可读的 Explore 记录。",{exact:true}).waitFor();
    await chronology.getByText("Distinct original objective 22",{exact:true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("research_state"),null,"A proven-empty Explore source shows all original strategies without another interaction");
    assert.equal(await chronology.locator("li").count(),23);
    assert.equal(await strategies.getByText("Retained original objective 22",{exact:true}).count(),0);
    assert.equal(await page.locator(".research-history-node").count(),0,"Native strategies do not fabricate Explore nodes");
    await page.screenshot({path:resolve(output,"empty-explore-strategies-desktop.png")});
    await chronology.getByRole("button").filter({hasText:"Distinct original objective 0"}).click();
    await strategies.getByRole("article",{name:"策略原文",exact:true}).getByText("Distinct original objective 0",{exact:true}).waitFor();
    await strategies.getByRole("button",{name:"返回策略总览",exact:true}).click();
    await page.waitForFunction(()=>document.activeElement?.dataset.strategyId);
    await page.getByLabel("选择控制面来源",{exact:true}).click();
    await page.getByRole("option",{name:"Retained",exact:true}).click();
    await page.getByRole("button",{name:/Retained research/}).first().waitFor();
    await page.waitForFunction(()=>new URL(location.href).searchParams.get("goalId")==="");
    await page.goBack();
    await page.getByRole("heading",{name:"Distinct research",exact:true}).waitFor({timeout:10000});
    assert.equal(new URL(page.url()).searchParams.get("goalId"),"other-goal");
    await page.goForward();
    await page.getByRole("button",{name:/Retained research/}).first().waitFor();
    await page.getByRole("button",{name:/Retained research/}).first().click();
    await page.getByRole("heading",{name:"Retained research",exact:true}).waitFor();
    await page.locator(".personal-goal-tabs").getByRole("button", {name:"研究", exact:true}).click();
    await writeFile(failFile, "unavailable");
    for (let attempt=0;attempt<40;attempt++) {
      if ((await fetch(catalog[1].status_url)).status === 503) break;
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    await page.getByLabel("选择控制面来源",{exact:true}).click();
    await page.getByRole("option",{name:"Fresh",exact:true}).click();
    await page.locator(".personal-runtime-source-error").waitFor();
    assert.equal(await page.locator(".research-history-node").count(),0,"a failed selected source must not keep another source's graph visible");
    assert.match(await page.getByLabel("选择控制面来源",{exact:true}).innerText(),/Fresh/);
    assert.equal(await page.getByRole("heading",{name:"Retained research",exact:true}).count(),0);
    await page.setViewportSize({width:390,height:844});
    assert.match(await page.getByRole("alert").innerText(),/Fresh/);
    await page.getByRole("button",{name:"重试",exact:true}).waitFor({state:"visible"});
    await page.screenshot({path:resolve(output,"unavailable-phone.png")});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    const failedRetry=page.waitForResponse(response=>new URL(response.url()).origin===new URL(catalog[1].status_url).origin && response.status()===503);
    await page.getByRole("button",{name:"重试",exact:true}).click();
    await failedRetry;
    await page.locator(".personal-runtime-source-error > button:enabled").waitFor();
    assert.match(await page.getByRole("alert").innerText(),/Fresh/);
    assert.equal(await page.getByRole("heading",{name:"Retained research",exact:true}).count(),0,"retry cannot fall back to the previously loaded source");
    await rm(failFile);
    await page.getByRole("button",{name:"重试",exact:true}).click();
    await page.getByRole("heading",{name:"Fresh research",exact:true}).waitFor();
    await page.locator(".research-history-coverage").filter({hasText:"92 / 92"}).waitFor();
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:resolve(output,"phone.png")});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.equal(analysisWrites,0);
    await page.evaluate(()=>localStorage.setItem("loopx-pw-locale","en"));
    await page.reload();
    await page.getByRole("heading",{name:"Fresh research",exact:true}).waitFor();
    await originalTask.locator(":scope > summary").filter({hasText:"Original task"}).click();
    await originalTask.getByRole("heading",{name:"Fresh original task",exact:true}).waitFor();
    await page.getByRole("region",{name:"Original task file",exact:true}).waitFor();
    await page.screenshot({path:resolve(output,"original-task-phone-en.png")});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
  }
  console.log("runtime source workspace browser smoke: ok");
} catch (error) {
  const failedPage = browser?.contexts()[0]?.pages()[0];
  if (failedPage) {
    await failedPage.screenshot({path:resolve(output,"failure.png")}).catch(()=>{});
    await writeFile(resolve(output,"failure.txt"),await failedPage.locator("body").innerText()).catch(()=>{});
  }
  throw error;
} finally {
  await browser?.close();
  backend.kill("SIGTERM");
  await rm(temporary,{recursive:true,force:true});
}
