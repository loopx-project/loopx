// Packaged UI + real read-only HTTP owner. Original R7 records stay unchanged;
// only transport availability and the catalog shell are controlled by this smoke.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, mkdir, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {pathToFileURL} from "node:url";
import type {Browser} from "playwright";
import {launchBrowser, loadPlaywright} from "../dashboard-browser-smoke-support.mjs";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {analysisReadback} from "./research-history.mjs";

const root = resolve(import.meta.dirname, "../..");
const output = resolve(root, "output/playwright/research-source-recovery");
type CapturedHistory = {goal_id:string; display_name:string; events:(Record<string,unknown> & {event_id:string})[]};
export const researchSourceRecoveryScenario = {
  id: "research-source-recovery",
  async run({browser}: {browser: Pick<Browser, "newPage">}) {
    const temporary = await mkdtemp(resolve(tmpdir(), "loopx-research-recovery-"));
    const fault = resolve(temporary, "unavailable");
    const fixturePaths = ["on", "off"].map(arm => resolve(root,
      `apps/presentation/dashboard/src/data/fixtures/edgebench-grid-context-${arm}-20261103-history.json`));
    const originals = await Promise.all(fixturePaths.map(path => readFile(path, "utf8")));
    const captures: CapturedHistory[] = originals.map(value => JSON.parse(value));
    assert.equal(captures[0].goal_id, captures[1].goal_id, "Physical sources can share one logical Goal id");
    const backend = spawn(resolveTestPython(), ["-u", "-c", `
import json, pathlib, sys, threading
from loopx.presentation.runtime_workspace import RuntimeWorkspaceServer
from loopx.presentation.chat_bundle import validate_bundle
from loopx.control_plane.status.goal_source import GoalSource
validate_bundle(pathlib.Path(sys.argv[2]), source_root=pathlib.Path(sys.argv[2]).parents[2])
class Source(GoalSource):
    def __init__(self, name, capture):
        self.capture = capture
        super().__init__(name.replace(' ','-'),name,'/registry','/runtime','/cli','/python','/bin','local','host',capture['goal_id'])
    def read_status(self, goal_id=None):
        self.check_access(goal_id)
        return dict(ok=True, registry='', runtime_root='', goal_count=1, run_count=0,
            contract=dict(ok=True,summary=dict(errors=0,warnings=0,checks=0),errors=[],warnings=[]),
            attention_queue=dict(available=True,item_count=0,needs_user_or_controller=0,needs_codex=0,watching_external_evidence=0,items=[]),
            run_history=dict(available=True,goal_count=1,run_count=0,recent_runs=[],goals=[dict(
                id=self.goal_id,display_name=self.capture['display_name'],registry_member=True,activation_state='active')]))
    def read_events(self, goal_id):
        self.check_access(goal_id)
        marker = pathlib.Path(sys.argv[1])
        if marker.exists() and marker.read_text() == self.label: raise OSError('Controlled history transport failure')
        return self.capture['events']
servers=[RuntimeWorkspaceServer(Source(name,json.loads(pathlib.Path(path).read_text())),pathlib.Path(sys.argv[2]),
    analysis_runtime_root=pathlib.Path(sys.argv[1]).parent / 'analysis')
    for name,path in zip(('R7 context on','R7 context off'),sys.argv[3:])]
catalog=[dict(id=s.source.id,label=s.source.label,status_url=s.status_url) for s in servers]
for s in servers:
    s.catalog=catalog
    threading.Thread(target=s.serve_forever,daemon=True).start()
print(json.dumps(catalog),flush=True)
threading.Event().wait()
`, fault, resolve(root, "loopx/web/chat"), ...fixturePaths], {cwd:root, stdio:["ignore", "pipe", "pipe"]});
    try {
      const catalog = await new Promise<{label:string;status_url:string}[]>((accept, reject) => {
        const timer = setTimeout(() => reject(new Error("History owner startup timed out")), 15000);
        backend.once("exit", code => {clearTimeout(timer); reject(new Error(`History owner exited ${code}`));});
        backend.stdout.once("data", bytes => {clearTimeout(timer); accept(JSON.parse(bytes.toString()));});
        backend.stderr.on("data", bytes => process.stderr.write(bytes));
      });
      await mkdir(output, {recursive:true});
      for (const language of ["zh", "en"]) {
        const zh = language === "zh";
        const page = await browser.newPage({locale:zh ? "zh-CN" : "en-US", reducedMotion:"reduce", viewport:{width:1440,height:900}});
        const errors: string[] = [], writes: string[] = [];
        page.on("pageerror", error => errors.push(String(error)));
        page.on("request", request => {if (request.method() !== "GET") writes.push(request.url());});
        const url = new URL("/chat/", catalog[0].status_url);
        url.search = new URLSearchParams({goalId:captures[0].goal_id,view:"overview",statusUrl:"/status.json"}).toString();
        await page.goto(url.toString());
        await page.getByRole("heading", {name:captures[0].display_name,exact:true}).waitFor();
        await page.locator(".personal-goal-tabs").getByRole("button", {name:zh ? "研究" : "Research",exact:true}).click();
        const coverage = page.locator(".research-history-coverage");
        const readError = page.locator(".research-history-read-error");
        await coverage.filter({hasText:"33 / 33"}).waitFor();
        await writeFile(fault, catalog[0].label);
        await page.locator(".research-history-controls").getByRole("button", {name:zh ? "刷新记录" : "Refresh history",exact:true}).click();
        await readError.waitFor();
        assert.match(await coverage.innerText(), /33 \/ 33/, "Same-source refresh failure preserves its valid snapshot");
        assert.match(await readError.locator(":scope > div > strong").innerText(), /保留|preserv/i,
          "A refresh failure still tells the user that this source's valid records remain readable");
        await rm(fault);
        await readError.getByRole("button", {name:zh ? "重试" : "Retry",exact:true}).click();
        await readError.waitFor({state:"hidden"});
        await writeFile(fault, catalog[1].label);
        await page.getByLabel(zh ? "选择控制面来源" : "Select control plane source", {exact:true}).click();
        await page.getByRole("option", {name:catalog[1].label,exact:true}).click();
        await page.getByRole("heading", {name:captures[1].display_name,exact:true}).waitFor();
        await readError.waitFor();
        assert.equal(await page.getByRole("heading", {name:captures[0].display_name,exact:true}).count(), 0);
        await page.screenshot({path:resolve(output, `source-unavailable-${language}-1440.png`)});
        assert.doesNotMatch(await readError.locator(":scope > div > strong").innerText(), /保留|preserv|remain/i,
          "A first-read failure cannot claim that records from this source were retained");
        assert.match(await readError.locator(":scope > div > strong").innerText(), /来源|source/i);
        assert.doesNotMatch(await coverage.innerText(), /33 \/ 33/, "A new source cannot inherit the old source's complete history");
        assert.equal(await page.locator(".research-history-node").count(), 0, "No old graph survives the failed source switch");
        await page.setViewportSize({width:390,height:844});
        await page.screenshot({path:resolve(output, `source-unavailable-${language}-390.png`)});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await readError.getByRole("button", {name:zh ? "重试" : "Retry",exact:true}).focus();
        await rm(fault);
        await page.keyboard.press("Enter");
        await coverage.filter({hasText:"11 / 11"}).waitFor();
        await readError.waitFor({state:"hidden"});
        await page.screenshot({path:resolve(output, `source-recovered-${language}-390.png`)});
        await page.setViewportSize({width:1440,height:900});
        await page.screenshot({path:resolve(output, `source-recovered-${language}-1440.png`)});
        // Recovered data must be the new source's original records, including its
        // negative findings, rather than a renamed or summarized old snapshot.
        await page.getByRole("button", {name:zh ? "搜索研究记录" : "Search research records",exact:true}).click();
        const records = page.locator(".research-record-search");
        const downloading = page.waitForEvent("download");
        await records.getByRole("button", {name:zh ? "下载匹配记录（JSON）" : "Download matches (JSON)",exact:true}).click();
        const exported = JSON.parse(await readFile(await (await downloading).path(), "utf8")) as {records:CapturedHistory["events"];coverage:unknown};
        assert.deepEqual(exported.records.map(event => event.event_id), captures[1].events.map(event => event.event_id));
        assert.deepEqual(exported.coverage, {loaded_records:11,exported_records:11,history_complete:true,refresh_failed:false});
        for (const [index, original] of captures[1].events.entries()) {
          for (const field of ["title", "summary", "status", "recorded_at", "goal_id", "run_id", "result_id"]) {
            assert.equal(exported.records[index][field], original[field] ?? "");
          }
          assert.deepEqual(exported.records[index].measurements ?? [], original.measurements ?? []);
        }
        await page.goBack();
        await page.getByRole("heading", {name:captures[0].display_name,exact:true}).waitFor();
        await coverage.filter({hasText:"33 / 33"}).waitFor();
        await page.goForward();
        await coverage.filter({hasText:"11 / 11"}).waitFor();

        const empty = analysisReadback("c");
        empty.pack.result = null;
        empty.pack.job = null;
        const saved = analysisReadback("a");
        const namespace = new URL(catalog[1].status_url).searchParams.get("source_namespace");
        await page.route("**/api/chat/research-analysis?*", async route => {
          const query = new URL(route.request().url()).searchParams;
          if (query.get("source_namespace") !== namespace) return route.continue();
          const response = query.get("pack_id") === empty.pack.pack_id ? empty : saved;
          const language = query.get("output_language");
          const config = {...response.pack.config, ...(language ? {output_language:language} : {})};
          const result = response.pack.result ? {...response.pack.result, output_language:language} : null;
          await route.fulfill({json:{...response, execution_enabled:false, pack:{...response.pack, config, result}}});
        });
        await page.reload();
        const progress = page.locator(".research-progress");
        const scope = progress.locator(":scope > header select");
        const savedScope = progress.locator(`.research-progress-scope[data-pack-id="${saved.pack.pack_id}"]`);
        const emptyScope = progress.locator(`.research-progress-scope[data-pack-id="${empty.pack.pack_id}"]`);
        await savedScope.waitFor();
        const originalSelect = await scope.elementHandle();
        await scope.focus();
        await scope.selectOption(empty.pack.pack_id);
        await emptyScope.waitFor();
        assert.equal(await scope.evaluate((select, original) => select === original && select === document.activeElement, originalSelect), true,
          "Moving the empty analysis below history keeps the focused selector");
        assert.equal(await progress.evaluate(element =>
          Boolean(document.querySelector(".research-axis")!.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)), true);
        await scope.selectOption(saved.pack.pack_id);
        await savedScope.waitFor();
        assert.equal(await scope.evaluate((select, original) => select === original && select === document.activeElement, originalSelect), true,
          "Moving the saved analysis above history keeps the focused selector");
        assert.equal(await progress.evaluate(element =>
          Boolean(element.compareDocumentPosition(document.querySelector(".research-axis")!) & Node.DOCUMENT_POSITION_FOLLOWING)), true);
        await page.keyboard.press("Tab");
        assert.equal(await progress.evaluate(element => element.contains(document.activeElement)), true,
          "The next Tab continues within the saved analysis");
        assert.deepEqual(writes, [], "Browsing and retry never dispatch work, analysis or source mutations");
        assert.deepEqual(errors, []);
        await page.close();
      }
      for (let i=0;i<fixturePaths.length;i++) assert.equal(await readFile(fixturePaths[i], "utf8"), originals[i]);
      return {coverageEntries:[], note:"Original R7 on/off; same-source retention, cross-source failure/retry/export, Back/Forward, bilingual/narrow/keyboard, analysis scope focus, zero writes."};
    } finally {
      if (backend.exitCode === null && backend.signalCode === null) {
        const stopped = once(backend, "exit"); backend.kill("SIGTERM"); await stopped;
      }
      await rm(temporary, {recursive:true,force:true});
    }
  },
};

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const browser = await launchBrowser(loadPlaywright().chromium);
  try {console.log(await researchSourceRecoveryScenario.run({browser}));}
  finally {await browser.close();}
}
