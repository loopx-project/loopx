import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {launchBrowser, loadPlaywright} from '../../../../examples/dashboard-browser-smoke-support.mjs';
import {startHistoryOwner} from '../../../../examples/personal-workspace-browser/research-history.mjs';

// Synthetic human observations exercise the production pack/HTTP/packaged UI.
// No native transcript, live Goal, inferred human origin or model is used.
const repo = resolve(import.meta.dirname, '../../../..');
const root = await mkdtemp(resolve(tmpdir(), 'loopx-attention-reader-'));
const output = resolve(repo, 'output/playwright/research-human-attention');
const owners = [];
let browser;
const at = m => new Date(Date.UTC(2026, 0, 1, 0, m)).toISOString();
const source = (id, artifact, text) => ({id, artifact, text, first_line: 1,
  artifact_sha256: createHash('sha256').update(text).digest('hex')});
async function fixture(mode) {
  const base = JSON.parse(await readFile(resolve(repo, 'apps/presentation/dashboard/src/data/fixtures/research-native-readback.json'), 'utf8'));
  const a = base.analyses[0], input = a.input;
  a.outputs = {};
  a.config = {model: 'fixture', effort: 'max', service_tier: 'fast', research_synthesis: false};
  const kinds = ['clarification', 'correction', 'review', 'approval', 'status', 'other'];
  const texts = ['Human supplied an already recorded constraint.', 'Human corrected evidence applied outside its tested version.',
    'Human reviewed independent evidence before accepting the result.', 'Human approved a new scope.',
    'Human responded to a progress update.', 'Human changed the research objective.'];
  const events = Array.from({length: mode === 'empty' ? 0 : mode === 'sparse' ? 1 : 16}, (_, i) => ({
    event_id: `input-${i + 1}`, episode_id: i % 5 ? input.episodes[0].id : null,
    kind: kinds[i % 6], necessity: [0,1,4].includes(i % 6) ? 'avoidable' : i % 6 === 5 ? 'unknown' : 'required',
    occurred_at: at(i + 10), active: mode === 'sparse' ? {start: at(10), end: at(10)} : i % 3 ? {start: at(i + 10), end: at(i + 11)} : null,
    request: mode === 'sparse' ? null : i % 4 ? {start: at(i + 5), end: at(i + 10)} : null,
    citation: {source_id: 'human-receipts', first_line: i + 1, last_line: i + 1},
  }));
  const evidence = Array.from({length: 16}, (_,i) => texts[i % 6] + ' Synthetic observation only.').join('\n');
  const capture = JSON.stringify({schema_version: 'trajectory_human_attention_v1', scope: input.scope,
    start: at(0), end: at(59), complete: mode === 'empty', events: events.length ? [...events, events[0]] : []});
  a.artifacts['human-receipts.txt'] = evidence; a.artifacts['attention.json'] = capture;
  input.sources.push(source('human-receipts', 'human-receipts.txt', evidence), source('attention', 'attention.json', capture));
  input.episodes[0].source_ids.push('human-receipts', 'attention');
  if (mode !== 'absent') input.human_attention_source_id = 'attention';
  const path = resolve(root, mode + '.json'); await writeFile(path, JSON.stringify(base));
  const owner = await startHistoryOwner(path); owners.push(owner);
  return {owner, goal: base.goal_id};
}
try {
  await mkdir(output, {recursive: true});
  const full = await fixture('populated'), absent = await fixture('absent'), empty = await fixture('empty'), sparse = await fixture('sparse');
  browser = await launchBrowser(loadPlaywright().chromium);
  const page = await browser.newPage({locale: 'zh-CN', viewport: {width: 1440, height: 1000}});
  const errors = [], writes = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => {if (r.method() === 'POST') writes.push(r.url());});
  const visit = f => page.goto(`${f.owner.base}/chat/?goalId=${f.goal}&view=research&research_goal=${f.goal}&statusUrl=%2Fstatus.json`, {waitUntil: 'networkidle'});
  await visit(absent);
  await page.locator('.research-progress').waitFor();
  await page.screenshot({path: resolve(output, 'before-desktop.png')});
  await page.locator('.research-progress-basis > summary').click();
  assert.match(await page.locator('.research-human-attention').innerText(), /未采集|Not recorded/);
  await visit(full);
  const attention = page.locator('.research-human-attention');
  await attention.waitFor();
  assert.match(await attention.innerText(), /16/);
  assert.match(await attention.innerText(), /10.*分钟|10.*min/);
  assert.match(await attention.innerText(), /19.*分钟|19.*min/);
  const slices = await attention.locator('.research-attention-donut circle[data-kind]').evaluateAll(nodes => nodes.map(node => ({
    kind: node.dataset.kind, count: Number(node.dataset.count), share: Number(node.dataset.share),
    length: Number(node.getAttribute('stroke-dasharray').split(' ')[0]),
  })));
  assert.equal(slices.length, 6);
  assert.equal(slices.reduce((sum, slice) => sum + slice.count, 0), 16);
  assert.ok(Math.abs(slices.reduce((sum, slice) => sum + slice.length, 0) - 100) < 1e-8);
  assert.equal(slices.find(slice => slice.kind === 'review').length, 18.75, 'Three of sixteen recorded reviews occupy 3/16 of the ring');
  assert.equal(slices.find(slice => slice.kind === 'status').length, 12.5);
  const timeShares = await attention.locator('.research-attention-time-track > span').evaluateAll(nodes => nodes.map(n => Number(n.dataset.share)));
  assert.ok(Math.abs(timeShares[0] - 10 / 59) < 1e-8 && Math.abs(timeShares[1] - 19 / 59) < 1e-8,
    'Active and waiting tracks independently use the captured 59-minute window, not their summed time');
  assert.match(await attention.locator('.research-attention-necessity-legend').innerText(), /8.*50%/);
  assert.equal(await attention.locator('.research-attention-detail').evaluate(e => e.open), false);
  await page.screenshot({path: resolve(output, 'after-desktop.png')});
  await attention.locator('.research-attention-detail > summary').focus();
  await page.keyboard.press('Enter');
  assert.equal(await attention.locator('.research-attention-events > li').count(), 8);
  assert.equal(await attention.locator('.research-attention-opportunities > li').count(), 3, 'Required reviews and approvals are not optimization points');
  await attention.locator('.research-attention-pages button').last().click();
  assert.equal(await attention.locator('.research-attention-events').getAttribute('start'), '9');
  await attention.locator('.research-attention-pages button').first().click();
  const check = attention.locator('.research-attention-opportunities button').first();
  await check.click();
  await page.locator('.analysis-source').getByText(/Human supplied/).waitFor();
  await page.locator('.analysis-source').press('Escape');
  await page.waitForFunction(() => document.activeElement?.textContent?.includes('input-1'));
  await page.route('**/research-analysis/citation?**', route => route.fulfill({status: 503, json: {error: 'Synthetic transient source failure'}}), {times: 1});
  await check.click();
  await page.getByRole('button', {name: /重试读取原文|Retry reading source/}).click();
  await page.locator('.analysis-source').getByText(/Human supplied/).waitFor();
  await page.locator('.analysis-source').press('Escape');
  await page.screenshot({path: resolve(output, 'optimization-desktop.png')});
  await page.setViewportSize({width: 390, height: 844});
  await attention.scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await checkPanels(page);
  await page.screenshot({path: resolve(output, 'optimization-narrow.png')});
  await visit(empty);
  assert.match(await attention.locator('.research-attention-total').innerText(), /0/);
  assert.match(await attention.locator('.research-attention-times').innerText(), /未采集|Not recorded/);
  assert.equal(await attention.locator('.research-attention-donut circle[data-kind]').count(), 0, 'Zero inputs cannot invent a type share');
  assert.equal(await attention.locator('.research-attention-time-track > span').count(), 0, 'Unmeasured time is not a zero-width measurement');
  await visit(sparse);
  assert.equal(await attention.locator('.research-attention-donut circle[data-kind]').count(), 1);
  assert.equal(await attention.locator('.research-attention-donut circle[data-kind]').getAttribute('stroke-dasharray'), '100 0');
  assert.equal(await attention.locator('.attention-active .research-attention-time-track').getAttribute('data-observed'), 'true');
  assert.equal(await attention.locator('.attention-active .research-attention-time-track > span').getAttribute('data-share'), '0');
  assert.equal(await attention.locator('.attention-waiting .research-attention-time-track').getAttribute('data-observed'), 'false');
  // This preference belongs only to this disposable browser context.
  await page.addInitScript(() => localStorage.setItem('loopx-pw-locale', 'en'));
  await visit(full);
  await page.getByRole('region', {name: 'Human attention'}).waitFor().catch(async () => {
    await page.locator('[aria-label="Human attention"]').waitFor();
  });
  assert.match(await attention.innerText(), /Recorded|recorded/);
  assert.match(await attention.innerText(), /Active attention/);
  await attention.locator('.research-attention-detail > summary').click();
  assert.match(await attention.locator('.research-attention-opportunities').innerText(), /counterevidence/);
  await checkPanels(page);
  await page.screenshot({path: resolve(output, 'optimization-english-narrow.png')});
  await page.setViewportSize({width: 320, height: 844});
  await checkPanels(page);
  assert.deepEqual(errors, []); assert.deepEqual(writes, []);
  console.log('PASS: measured type shares, separate capture-window tracks, necessity shares, sparse/unknown/zero, internal overflow, pagination and source recovery; zero model writes');
} finally {
  await browser?.close();
  await Promise.all(owners.map(owner => owner.close()));
  await rm(root, {recursive: true, force: true});
}

async function checkPanels(page) {
  const overflow = await page.locator('.research-attention-breakdown, .research-attention-counts, .research-attention-times, .research-attention-necessity-legend').evaluateAll(nodes =>
    nodes.filter(n => n.scrollWidth > n.clientWidth + 1).map(n => n.className));
  assert.deepEqual(overflow, [], 'Named attention panels must fit internally, including long English labels');
}
