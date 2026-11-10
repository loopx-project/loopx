import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {startHistoryOwner} from '../../../../examples/personal-workspace-browser/research-history.mjs';

// An isolated production backend registers two frozen versions. No model runs,
// active Goal writes or user-browser ownership are part of this test harness.
const repo = fileURLToPath(new URL('../../../../', import.meta.url));
const output = resolve(process.env.LOOPX_CASE_PROCESS_OUTPUT ?? resolve(repo, 'output/playwright/case-process'));
const root = await mkdtemp(resolve(tmpdir(), 'loopx-case-process-'));
const fixture = JSON.parse(await readFile(resolve(repo, 'apps/presentation/dashboard/src/data/fixtures/case-study-child.json'), 'utf8'));
const old = fixture.analyses[0];
old.title = 'Original reading';
old.outputs = {};
const illustrated = structuredClone(old);
illustrated.title = 'Illustrated reading';
delete illustrated.case_study.sections[0].table;
illustrated.case_study.sections[0].process = {
  label: '从候选到成果：三个不同的判断',
  steps: [
    {title: '看见候选', text: '窗口里出现过 **7 分、无效、3 分** 三次观测。较低误差是一个信号，但不能抹去中间失败。'},
    {title: '比较有效结果', text: '只有有效候选才有可比较的误差。无效结果没有分数，不能当成零，也不能用本地代理替代原生验收。'},
    {title: '核对最终采用', text: '最佳观测是否进入最终成果仍未知。缺少保留记录，便不能把 **3 分** 自动称为交付值。'},
  ],
};
fixture.analyses.push(illustrated);
const childPath = resolve(root, 'child.json');
await writeFile(childPath, JSON.stringify(fixture));
const parentPath = resolve(root, 'parent.json');
await writeFile(parentPath, JSON.stringify({goal_id: fixture.parent_goal_id, display_name: 'Benchmark', events: [], analyses: []}));
await mkdir(output, {recursive: true});
const owner = await startHistoryOwner([childPath, parentPath]);
const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('loopx-pw-locale', 'zh-CN'));
  await page.goto(`${owner.base}/chat/?${new URLSearchParams({goalId: fixture.goal_id, view: 'research',
    research_goal: fixture.goal_id, research_reading: 'case', statusUrl: '/status.json'})}`);
  const select = page.getByRole('combobox', {name: '选择解读版本'});
  await select.selectOption({label: 'Illustrated reading'});
  const figure = page.locator('.case-process');
  await figure.waitFor();
  const current = await select.inputValue();
  const source = await (await page.request.get(`${owner.base}/api/chat/research-analysis?${new URLSearchParams({goal_id: fixture.goal_id, pack_id: current})}`)).json();
  assert.deepEqual(source.pack.case_study.sections[0].process, illustrated.case_study.sections[0].process);
  const reading = page.locator('.research-case-study');
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: 1000});
    await reading.evaluate(el => {for (let node: HTMLElement | null = el; node; node = node.parentElement) node.scrollTop = 0; window.scrollTo(0, 0);});
    await page.screenshot({path: resolve(output, `opening-${width}.png`), animations: 'disabled'});
    await figure.scrollIntoViewIfNeeded();
    await page.screenshot({path: resolve(output, `process-${width}.png`), animations: 'disabled'});
    assert.deepEqual(await figure.locator('h4').allTextContents(), illustrated.case_study.sections[0].process.steps.map((step: {title: string}) => step.title));
    assert.equal(await figure.locator('ol > li').count(), 3);
    const geometry = await figure.evaluate(el => ({width: el.clientWidth, scrollWidth: el.scrollWidth,
      font: parseFloat(getComputedStyle(el.querySelector('p')!).fontSize)}));
    assert(geometry.scrollWidth <= geometry.width + 1, JSON.stringify(geometry));
    assert(geometry.font >= 16, 'Narrow reading cannot trade readability for fitting the diagram');
    assert.match(await reading.innerText(), /仍未知|未知/);
    for (const [index, section] of (await reading.locator('.case-section').all()).entries()) {
      await section.evaluate(el => el.scrollIntoView({block: 'start'}));
      await page.screenshot({path: resolve(output, `section-${index + 1}-${width}.png`), animations: 'disabled'});
    }
  }
  const sources = page.locator('.case-section').first().locator('.case-sources');
  await sources.locator('summary').click();
  const origin = sources.getByRole('button').first();
  let rejectOnce = true;
  await page.route('**/api/chat/research-analysis/citation?*', async route => {
    if (rejectOnce) {rejectOnce = false; await route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({ok: false, error: 'Synthetic read interruption'})});}
    else await route.continue();
  });
  await origin.focus();
  await page.keyboard.press('Enter');
  await page.getByRole('button', {name: '重试读取原文'}).click();
  const citation = page.getByRole('region', {name: '引用原文', exact: true});
  await citation.locator('pre').waitFor();
  assert.equal(await citation.locator('pre').textContent(), old.input.sources.find((source: {id: string}) => source.id === 'facts').text,
    'The retry must restore the full cited four-line original, including the unknown retention');
  await page.keyboard.press('Escape');
  assert(await origin.evaluate(el => document.activeElement === el));
  await select.selectOption({label: 'Original reading'});
  await figure.waitFor({state: 'detached'});
  await reading.locator('.case-table-scroll').waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: 1000});
    await page.locator('.case-section').first().scrollIntoViewIfNeeded();
    await page.screenshot({path: resolve(output, `original-${width}.png`), animations: 'disabled'});
  }
  await select.selectOption({label: 'Illustrated reading'});
  await figure.waitFor();
  assert.equal(await select.inputValue(), current, 'Returning to the figure retains its saved version');
  const bad = await page.request.get(`${owner.base}/api/chat/research-analysis/citation?${new URLSearchParams({
    goal_id: fixture.goal_id, pack_id: current, analysis_key: '0'.repeat(64), source_id: 'facts', first_line: '1', last_line: '4'})}`);
  assert.equal(bad.status(), 409);
  const result = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
  assert.equal(result.mutation_attempts, 0);
  assert.equal(result.source_unchanged, true);
  assert.deepEqual(errors, []);
  await writeFile(resolve(output, 'readback.json'), JSON.stringify({widths: [1440, 390], ...result}, null, 2));
  console.log('case process: packaged reader, frozen versions, citation recovery and zero writes passed');
} finally {
  await browser.close();
  await owner.close();
  await rm(root, {recursive: true, force: true});
}
