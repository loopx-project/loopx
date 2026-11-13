import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {resolveTestPython} from '../../scripts/test-python.mjs';
import {outputDir, repoRoot} from './fixture.mjs';
import {startHistoryOwner} from './research-history.mjs';

export const researchTurnCostsScenario = {
  id: 'research-turn-costs',
  async run({browser}) {
    const temporary = await mkdtemp(resolve(tmpdir(), 'loopx-turn-costs-'));
    let owner, page;
    try {
      const fixture = resolve(temporary, 'capture.json');
      execFileSync(resolveTestPython(), ['-c', `
import hashlib,json,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
from loopx.capabilities.explore.result_log import build_explore_node_event
goal='synthetic-cost-study'
text='Compare observed costs; no causal benefit is established.'
pack=dict(schema_version='trajectory_assessment_input_v1',scope=dict(goal_id=goal,case_id='synthetic',run_id='fixed',revision='fixture',window='one captured session'),objective=text,
 selection=dict(rule='synthetic fixture',population_episodes=1,exhaustive=True),
 sources=[dict(id='facts',artifact='facts.txt',artifact_sha256=hashlib.sha256(text.encode()).hexdigest(),first_line=1,text=text)],
 episodes=[dict(id='episode',question=text,source_ids=['facts'],eligible_metrics=['evidence_adoption'])])
rows=[];seconds=0;totals=dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
def append(kind,payload):
 rows.append(dict(type=kind,timestamp=(datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=seconds)).isoformat(),payload=payload))
append('session_meta',dict(id='session'))
for i in range(10):
 seconds+=1;append('event_msg',dict(type='task_started',turn_id=f'turn-{i+1}'))
 seconds+=1;append('response_item',dict(type='message',role='user',content='SENSITIVE_TEXT_NOT_FOR_BROWSER'))
 counts=dict(input_tokens=5000 if i==2 else 100,cached_input_tokens=3000 if i==2 else 60,output_tokens=10)
 totals={key:totals[key]+counts[key] for key in totals}
 seconds+=120 if i==6 else 1
 append('event_msg',dict(type='token_count',info=dict(total_token_usage=totals.copy(),last_token_usage=counts)))
 seconds+=1;append('event_msg',dict(type='turn_aborted' if i==6 else 'task_complete',turn_id=f'turn-{i+1}'))
seconds+=1;append('event_msg',dict(type='task_started',turn_id='unfinished'))
analysis=dict(title='Observed session',input=pack,artifacts={'facts.txt':text,'usage.jsonl':'\\n'.join(map(json.dumps,rows))},outputs={},usage_jsonl='usage.jsonl',usage_session_id='session',include_incomplete_usage=True)
Path(sys.argv[1]).write_text(json.dumps(dict(goal_id=goal,display_name='Synthetic cost study',events=[build_explore_node_event(goal_id=goal,node_id='question',title=text,recorded_at='2026-01-01T00:00:00Z')],analyses=[analysis])))
`, fixture], {cwd: repoRoot});
      owner = await startHistoryOwner(fixture);
      page = await browser.newPage({viewport: {width: 1440, height: 1000}});
      const errors = [], writes = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => {if (request.method() === 'POST') writes.push(request.url());});
      await page.goto(`${owner.base}/chat/?goalId=synthetic-cost-study&view=research&research_goal=synthetic-cost-study&statusUrl=%2Fstatus.json`, {waitUntil: 'networkidle'});
      const progress = page.locator('.research-progress');
      await progress.locator('.research-usage').waitFor({state: 'attached'});
      await page.screenshot({path: resolve(outputDir, 'research-turn-costs-overview.png'), animations: 'disabled'});
      await progress.locator('.research-progress-basis > summary').click();
      const costs = progress.locator('.research-turn-costs');
      await costs.locator('summary').focus(); await page.keyboard.press('Enter');
      const items = costs.locator('li');
      assert.equal(await items.count(), 8);
      assert.match(await items.first().innerText(), /第 3 轮/);
      assert.match(await items.first().innerText(), /5,000/);
      assert.match(await items.first().innerText(), /3,000/);
      assert.match(await items.first().innerText(), /2,000/);
      await costs.getByRole('combobox').selectOption('elapsed');
      assert.match(await items.first().innerText(), /第 7 轮.*未完成/s);
      const turnCostEvidence = items.first().getByRole('button');
      const turnCostEvidenceHandle = await turnCostEvidence.elementHandle();
      assert(turnCostEvidenceHandle);
      await turnCostEvidence.click();
      const source = page.getByRole('region', {name: '引用原文', exact: true});
      await source.locator('pre').waitFor();
      assert.match(await source.innerText(), /turn-7/);
      assert.match(await source.innerText(), /turn_aborted/);
      assert.match(await source.innerText(), /token_count/);
      assert.ok(!(await source.innerText()).includes('SENSITIVE_TEXT_NOT_FOR_BROWSER'));
      await source.focus(); await page.keyboard.press('Escape');
      await page.waitForFunction(element => element === document.activeElement, turnCostEvidenceHandle);
      await costs.getByRole('combobox').selectOption('time');
      await costs.getByRole('button', {name: '下一页', exact: true}).click();
      assert.equal(await items.count(), 3);
      assert.match(await items.last().innerText(), /未完成/);
      assert.match(await items.last().innerText(), /—/);
      await items.last().getByRole('button').click();
      await source.locator('pre').waitFor();
      assert.match(await source.innerText(), /unfinished/);
      await source.getByRole('button', {name: '关闭原文', exact: true}).click();
      await costs.getByRole('combobox').selectOption('input');
      const firstTurn = costs.getByRole('spinbutton', {name: '起始轮次', exact: true});
      const lastTurn = costs.getByRole('spinbutton', {name: '结束轮次', exact: true});
      const selection = costs.locator('.research-usage-range-summary');
      assert.match(await selection.innerText(), /已选 11 \/ 11 轮.*完成 9 轮.*无计数 1 轮/);
      const download = async () => {
        const waiting = page.waitForEvent('download');
        await costs.getByRole('button', {name: '下载所选用量', exact: true}).click();
        const file = await waiting;
        return JSON.parse(await readFile(await file.path(), 'utf8'));
      };
      const all = await download();
      assert.equal(all.selection.turns.length, 11, 'Download includes every selected Turn beyond the eight-row page');
      assert.equal(all.selection.observed_input_tokens, 5900);
      assert.equal(all.selection.turns.find(turn => turn.turn_id === 'turn-7').incomplete, true);
      assert.equal(all.goal_id, 'synthetic-cost-study');
      assert.equal(all.artifact_sha256.length, 64);
      assert.ok(!JSON.stringify(all).includes('SENSITIVE_TEXT_NOT_FOR_BROWSER'));
      await firstTurn.fill('3'); await lastTurn.fill('7');
      assert.equal(await items.count(), 5);
      assert.match(await selection.innerText(), /5,400/);
      const range = await download();
      assert.deepEqual(range.selection.turns.map(turn => turn.turn_id), ['turn-3','turn-4','turn-5','turn-6','turn-7']);
      assert.equal(range.selection.observed_turn_seconds, 134);
      await firstTurn.fill('11'); await lastTurn.fill('11');
      assert.match(await selection.innerText(), /完成 0 轮.*无计数 1 轮/);
      assert.equal((await download()).selection.observed_input_tokens, null);
      await firstTurn.fill('12');
      assert.equal(await costs.getByRole('button', {name: '下载所选用量', exact: true}).isDisabled(), true);
      await costs.getByRole('alert').waitFor();
      await costs.getByRole('button', {name: '全部轮次', exact: true}).click();
      // A browser download failure can be retried without losing the selection.
      await page.evaluate(() => {window.savedUsageCreateURL = URL.createObjectURL; URL.createObjectURL = () => {throw new Error('synthetic download failure');};});
      await costs.getByRole('button', {name: '下载所选用量', exact: true}).click();
      assert.match(await costs.getByRole('alert').innerText(), /无法开始下载/);
      await page.evaluate(() => {URL.createObjectURL = window.savedUsageCreateURL; delete window.savedUsageCreateURL;});
      assert.equal((await download()).selection.turns.length, 11);
      assert.equal(await costs.getByRole('alert').count(), 0);
      const helpButton = costs.getByRole('button', {name: '轮次耗时与输入说明', exact: true});
      const helpPopover = costs.locator('.context-help-popover');
      await helpButton.focus();
      await helpPopover.waitFor({state: 'visible'});
      assert.match(await helpPopover.innerText(), /仅比较当前捕获内的轮次/);
      assert.match(await helpPopover.innerText(), /轮次输入不等于 LoopX 控制输入/);
      await page.keyboard.press('Escape');
      await helpPopover.waitFor({state: 'hidden'});
      assert.equal(await helpButton.evaluate(element => element === document.activeElement), true);
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 1000});
        await costs.locator('summary').evaluate((element, offset) => {element.style.scrollMarginTop = `${offset}px`; element.scrollIntoView({block: 'start'});}, width < 600 ? 190 : 125);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        const sortBox = await costs.getByRole('combobox').boundingBox();
        assert(sortBox && sortBox.width >= 80 && sortBox.height >= (width < 600 ? 44 : 36));
        await helpButton.click();
        await helpPopover.waitFor({state: 'visible'});
        const helpBox = await helpPopover.boundingBox();
        assert(helpBox && helpBox.x >= 12 && helpBox.x + helpBox.width <= width - 12);
        await page.screenshot({path: resolve(outputDir, `research-turn-costs-${width}.png`), animations: 'disabled'});
        await page.keyboard.press('Escape');
        await helpPopover.waitFor({state: 'hidden'});
        assert.equal(await helpButton.evaluate(element => element === document.activeElement), true);
        await costs.locator('legend').evaluate(element => element.scrollIntoView({block: 'center'}));
        await page.screenshot({path: resolve(outputDir, `research-usage-range-${width}.png`), animations: 'disabled'});
        await costs.getByRole('combobox').selectOption('elapsed');
        await items.first().evaluate(element => element.scrollIntoView({block: 'center'}));
        await page.screenshot({path: resolve(outputDir, `research-interrupted-turn-${width}.png`), animations: 'disabled'});
        await costs.getByRole('combobox').selectOption('input');
      }
      await page.evaluate(() => localStorage.setItem('loopx-pw-locale', 'en'));
      await page.reload({waitUntil: 'networkidle'});
      await progress.locator('.research-progress-basis').waitFor();
      if (!await progress.locator('.research-progress-basis').evaluate(element => element.open)) {
        await progress.locator('.research-progress-basis > summary').focus(); await page.keyboard.press('Enter');
      }
      await costs.locator('summary').click();
      await costs.getByRole('spinbutton', {name: 'First Turn', exact: true}).fill('3');
      await costs.getByRole('spinbutton', {name: 'Last Turn', exact: true}).fill('7');
      assert.match(await selection.innerText(), /5 \/ 11 Turns selected.*4 completed.*0 without counters/);
      assert.match(await selection.innerText(), /5,400/);
      const englishDownload = page.waitForEvent('download');
      await costs.getByRole('button', {name: 'Download selected usage', exact: true}).click();
      assert.equal(JSON.parse(await readFile(await (await englishDownload).path(), 'utf8')).selection.observed_input_tokens, 5400);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await costs.locator('legend').evaluate(element => element.scrollIntoView({block: 'center'}));
      await page.screenshot({path: resolve(outputDir, 'research-usage-range-en-390.png'), animations: 'disabled'});
      assert.deepEqual(errors, []);
      assert.deepEqual(writes, [], 'Cost inspection never starts analysis or mutates Goal state');
      return {coverageEntries: [], note: 'Real registered capture, typed reduction, HTTP and source UI: sort, pagination, incomplete/unknown counters, accessible contextual help, exact source evidence, keyboard return and mobile. No model or live benchmark.'};
    } catch (error) {
      if (page && !page.isClosed()) await page.screenshot({path: resolve(outputDir, 'research-turn-costs-failed.png')});
      throw error;
    } finally {
      if (page) await page.close();
      if (owner) await owner.close();
      await rm(temporary, {recursive: true, force: true});
    }
  },
};
