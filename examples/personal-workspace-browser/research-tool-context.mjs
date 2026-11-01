import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {startHistoryOwner} from "./research-history.mjs";

// Real source verification, TS reduction, pack registry, HTTP and packaged UI.
// The fixture has no model outputs: ordinary metric reading needs no analysis.
export const researchToolContextScenario = {
  id: "research-tool-context",
  async run({browser}) {
    const temporary = await mkdtemp(resolve(tmpdir(), "loopx-tool-context-"));
    let owner, page;
    try {
      const fixture = resolve(temporary, "capture.json");
      execFileSync(resolveTestPython(), ["-c", `
import hashlib,json,sys
from pathlib import Path
from loopx.capabilities.explore.result_log import build_explore_node_event
goal='synthetic-context-reading'
text='Synthetic research observation; utility remains unknown.'
scope=dict(goal_id=goal,case_id='synthetic',run_id='fixed',revision='fixture',window='fixed capture')
pack=dict(schema_version='trajectory_assessment_input_v1',scope=scope,objective='Compare research outcomes and complete costs',
 selection=dict(rule='one synthetic episode',population_episodes=1,exhaustive=True),
 sources=[dict(id='facts',artifact='facts.txt',artifact_sha256=hashlib.sha256(text.encode()).hexdigest(),first_line=1,text=text)],
 episodes=[dict(id='episode',question='Did new evidence change the next experiment?',source_ids=['facts'],eligible_metrics=['evidence_adoption'])])
usage=dict(input_tokens=100,cached_input_tokens=60,output_tokens=5)
rows=[dict(type='session_meta',payload=dict(id='session')),
 dict(type='event_msg',payload=dict(type='task_started',turn_id='turn')),
 dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text='x'*100)])),
 dict(type='response_item',payload=dict(type='function_call',name='exec_command',call_id='control',arguments=json.dumps(dict(cmd='loopx quota context-read')))),
 dict(type='response_item',payload=dict(type='function_call_output',call_id='control',output='x'*60)),
 dict(type='response_item',payload=dict(type='function_call_output',call_id='task',output='x'*40)),
 dict(type='event_msg',payload=dict(type='token_count',info=dict(total_token_usage=usage,last_token_usage=usage))),
 dict(type='event_msg',payload=dict(type='task_complete',turn_id='turn'))]
for i,row in enumerate(rows):row['timestamp']=f'2026-01-01T00:00:0{i}Z'
analyses=[]
for enabled in [False,True]:
 analyses.append(dict(title='Captured tool context' if enabled else 'Without tool inventory',input=pack,
  artifacts={'facts.txt':text,'usage.jsonl':'\\n'.join(map(json.dumps,rows))},outputs={},
  usage_jsonl='usage.jsonl',usage_session_id='session',include_tool_context=enabled))
broken=json.loads(json.dumps(analyses[-1]))
broken['title']='Conflicting tool capture'
conflicting=rows[:5]+[dict(type='response_item',payload=dict(type='function_call_output',call_id='control',output='different'))]+rows[5:]
broken['artifacts']['usage.jsonl']='\\n'.join(map(json.dumps,conflicting))
analyses.append(broken)
Path(sys.argv[1]).write_text(json.dumps(dict(goal_id=goal,display_name='Synthetic context cost study',
 events=[build_explore_node_event(goal_id=goal,node_id='question',title='Synthetic research question',recorded_at='2026-01-01T00:00:00Z')],analyses=analyses)))
`, fixture], {cwd: repoRoot});
      owner = await startHistoryOwner(fixture);
      page = await browser.newPage({viewport: {width: 1440, height: 900}});
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      await page.goto(`${owner.base}/chat/?goalId=synthetic-context-reading&view=research&research_goal=synthetic-context-reading&statusUrl=%2Fstatus.json`, {waitUntil: "networkidle"});
      const progress = page.locator('.research-progress');
      const selector = progress.locator('select');
      await selector.selectOption({label: 'Without tool inventory'});
      await progress.locator('.research-usage').waitFor({state: 'attached'});
      await page.screenshot({path: resolve(outputDir, 'research-tool-context-before.png'), animations: 'disabled'});
      assert.equal(await progress.locator('[data-loopx-tool-context-share]').count(), 0);
      await selector.selectOption({label: 'Captured tool context'});
      const basis = progress.locator('.research-progress-basis');
      await basis.locator(':scope > summary').focus(); await page.keyboard.press('Enter');
      const share = progress.locator('[data-loopx-tool-context-share]');
      await share.waitFor({state: 'visible'});
      assert.equal(await share.textContent(), '30%');
      const inventory = progress.getByRole('region', {name: 'LoopX 工具上下文', exact: true});
      const details = inventory.locator('details');
      await details.locator('summary').focus(); await page.keyboard.press('Enter');
      assert.match(await details.innerText(), /60 B/);
      assert.match(await details.innerText(), /200 B/);
      assert.match(await details.innerText(), /quota context-read/);
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 900});
        await inventory.scrollIntoViewIfNeeded();
        assert(await inventory.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
        await page.screenshot({path: resolve(outputDir, `research-tool-context-${width}.png`), animations: 'disabled'});
      }
      await page.evaluate(() => localStorage.setItem('loopx-pw-locale', 'en'));
      await page.reload({waitUntil: 'networkidle'});
      const english = progress.getByRole('region', {name: 'LoopX tool context', exact: true});
      await share.waitFor({state: 'attached'});
      if (!await progress.locator('.research-progress-basis').evaluate(node => node.open)) {
        await progress.locator('.research-progress-basis > summary').focus(); await page.keyboard.press('Enter');
      }
      await english.waitFor({state: 'visible'});
      assert.equal(await share.textContent(), '30%');
      await selector.selectOption({label: 'Conflicting tool capture'});
      const unavailable = progress.getByRole('region', {name: 'LoopX tool context', exact: true, includeHidden: true});
      await unavailable.locator('[role="status"]').waitFor({state: 'attached'});
      if (!await progress.locator('.research-progress-basis').evaluate(node => node.open)) {
        await progress.locator('.research-progress-basis > summary').focus(); await page.keyboard.press('Enter');
      }
      assert.match(await unavailable.innerText(), /share unknown/);
      assert.equal(await progress.locator('[data-loopx-tool-context-share]').count(), 0);
      const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
      assert(readback.source_unchanged && readback.mutation_attempts === 0);
      assert.deepEqual(errors, []);
      return {coverageEntries: [], note: 'real-owner byte inventory, opt-out parity, unknown analysis, keyboard, desktop/phone and English; zero model calls'};
    } catch (error) {
      if (page && !page.isClosed()) await page.screenshot({path: resolve(outputDir, 'research-tool-context-failed.png')});
      throw error;
    } finally {
      await page?.close(); await owner?.close(); await rm(temporary, {recursive: true, force: true});
    }
  },
};
