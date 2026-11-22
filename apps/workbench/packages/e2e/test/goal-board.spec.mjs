import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

// The packaged web and daemon are real; only Core Goal records are synthetic.
let daemon, browser, core, bound, paused, plain;
let filesFail = false;
const additionalBindings = new Map();
const reads = [];
const goals = [
  { id: 'proposal', display_name: 'Review the launch proposal and verify the complete delivery', goal_objective: 'Prepare a proposal with clear acceptance evidence.', status: 'active_state_user_gate', activation_state: 'active' },
  { id: 'paused', display_name: 'Research the next release', status: 'connected_without_run', activation_state: 'stopped' },
];
for (let number = 3; number <= 8; number++) goals.push({ id: `core-${number}`, display_name: `Core-only Goal ${number}: verify the research conclusions`, goal_objective: `Retain independently readable results for Core Goal ${number}.`, status: number === 8 ? 'connected_without_run' : 'active_state_agent_todo', activation_state: number === 8 ? 'stopped' : 'active' });
const todo = { todo_id: 'draft', text: 'Review the proposal and choose the next step', status: 'open', task_class: 'user_action', claimed_by: null, priority: 'P1' };
const counts = (items) => ({ items, total_count: items.length, open_count: items.length, done_count: 0 });
const attention = { goal_id: 'proposal', status: 'active_state_user_gate', waiting_on: 'user', user_todos: counts([todo]), agent_todos: counts([{ ...todo, todo_id: 'agent', text: 'Run independent checks', task_class: 'advancement_task', claimed_by: 'pi' }, { ...todo, todo_id: 'peer', text: 'Review supporting evidence', task_class: 'advancement_task', claimed_by: 'reviewer' }]) };
const attentionFor = id => id === 'proposal' ? attention : { goal_id: id, status: goals.find(goal => goal.id === id)?.status ?? 'unknown', activation_state: goals.find(goal => goal.id === id)?.activation_state, goal_channel_projection: { display_name: goals.find(goal => goal.id === id)?.display_name }, user_todos: counts([]), agent_todos: counts([{ ...todo, todo_id: `${id}-task`, text: `Review supporting evidence for ${id}`, task_class: 'advancement_task', claimed_by: 'researcher' }]), recommended_action: `Read the retained findings for ${id}` };
const coreReport = { todo_id: 'core-8-report', title: 'Core-only retained report', producer_agent_id: 'researcher', sha256: 'fixture-report-digest', content_type: 'text/plain', size_bytes: 48 };
const inventory = { items: [], next_cursor: null, unavailable_count: 0, unavailable_todo_ids: [] };
before(async () => {
  core = createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost'); reads.push(url.pathname + url.search);
    res.setHeader('Content-Type', 'application/json');
    if (url.pathname === '/status.json') {
      const id = url.searchParams.get('goal_id');
      res.end(JSON.stringify({ served_at: new Date().toISOString(), ...(id ? { goal_filter: id } : {}), run_history: { goals: id ? goals.filter(goal => goal.id === id) : goals }, attention_queue: { items: id ? [attentionFor(id)] : goals.map(goal => attentionFor(goal.id)) } }));
    } else if (url.pathname === '/api/chat/goal-results') {
      res.statusCode = filesFail ? 502 : 200; res.end(JSON.stringify(filesFail ? { error: 'Synthetic Core temporarily unavailable' } : url.searchParams.get('goal_id') === 'core-8' ? { ...inventory, items: [coreReport] } : inventory));
    } else if (url.pathname === '/api/chat/goal-results/core-8-report') {
      res.end(JSON.stringify({ goal_id: 'core-8', todo_id: coreReport.todo_id, result: coreReport, text: 'Retained Core report without a Workbench conversation.' }));
    } else if (url.pathname === '/api/chat/goal-acceptance') {
      res.end(JSON.stringify({ ok: true, goal_id: url.searchParams.get('goal_id'), provider_revision: 'fixture-revision', goal_acceptance_contract: { lifecycle_state: 'active', objective: 'Review the retained findings', criteria: [{ id: 'check', description: 'Independent evidence remains readable' }] } }));
    } else if (url.pathname === '/api/actions') { res.end(JSON.stringify({ proposals: [] })); }
    else { res.statusCode = 502; res.end(JSON.stringify({ error: 'Synthetic Core temporarily unavailable' })); }
  });
  await new Promise(resolve => core.listen(0, '127.0.0.1', resolve));
  daemon = await startDaemon({ LOOCHAT_LOOPX_CORE_URL: `http://127.0.0.1:${core.address().port}`, LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'pi', adapter: 'pi', command: ['fake-pi'] }] }) });
  console.log(JSON.stringify({ fixture: 'goal-board', daemonPid: daemon.pid, daemonBase: daemon.base, corePort: core.address().port, web: 'packages/web/dist' }));
  const extensions = await (await api(daemon, '/api/extensions')).json();
  assert.equal(extensions.some(state => state.manifest.id === 'loopx' && state.installed), false, 'Goal UI works without the external LoopX extension');
  writeFileSync(join(daemon.workdir, 'proposal.txt'), 'Synthetic proposal with review evidence.');
  browser = await launchBrowser();
  const create = async (title) => {
    const response = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title }) });
    assert.equal(response.status, 201); return response.json();
  };
  bound = await create('[Launch] Proposal review');
  paused = await create('[Research] Release research');
  plain = await create('Ordinary conversation');
  for (let number = 2; number <= 5; number++) await create(`Ordinary conversation ${number}`);
  for (const [title, patch] of [
    ['[Launch] Independent checks', { stage: 'doing' }],
    ['[Launch] Approve the release notes', { stage: 'blocked' }],
    ['[Launch] Review the final evidence', { stage: 'review' }],
    ['[Launch] Accepted documentation', { done: true }],
    ['[Research] Follow-up experiment', { stage: 'backlog' }],
  ]) {
    const thread = await create(title);
    await api(daemon, `/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify(patch) });
    additionalBindings.set(thread.id, 'proposal');
  }
});
after(async () => { await browser?.close(); await daemon?.stop(); if (core) await new Promise(resolve => core.close(resolve)); });

async function pageFor(t, mobile = false, theme = 'light', bindings = true) {
  const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 960 }, colorScheme: theme, isMobile: mobile, hasTouch: mobile });
  t.after(() => context.close());
  const page = await context.newPage();
  const mutations = [];
  page.on('request', request => { if (request.method() !== 'GET' && (new URL(request.url()).pathname.startsWith('/api/loopx/') || /\/api\/threads(?:$|\/.*\/turns$)/.test(new URL(request.url()).pathname) && request.method() === 'POST')) mutations.push(request.url()); });
  t.after(() => assert.deepEqual(mutations, [], 'reading Goal details never starts work or creates a conversation'));
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  // Binding metadata normally comes from the Core's session lookup. Keep the real transport and
  // thread behavior, adding only these two public-safe identities to the HTTP read response.
  await page.route('**/api/threads**', async route => {
    const url = new URL(route.request().url());
    if (route.request().method() !== 'GET' || !/^\/api\/threads(?:\/[^/]+)?$/.test(url.pathname)) return route.continue();
    const response = await route.fetch();
    if (!response.ok()) return route.fulfill({ response });
    const raw = await response.json();
    const value = !bindings && Array.isArray(raw) ? raw.filter(thread => thread.title.startsWith('Ordinary conversation')) : raw;
    const enrich = thread => !bindings ? thread : thread.id === bound.id || additionalBindings.has(thread.id) ? { ...thread, loopxInfo: { goalId: 'proposal', goalTitle: goals[0].display_name, basis: 'core', source: 'fixture' } }
      : thread.id === paused.id ? { ...thread, loopxInfo: { goalId: 'paused', goalTitle: goals[1].display_name, basis: 'core', source: 'fixture' } } : thread;
    await route.fulfill({ response, json: Array.isArray(value) ? value.map(enrich) : enrich(value) });
  });
  await openUi(page, daemon);
  return page;
}
async function shot(page, name) {
  if (!process.env.LOOCHAT_E2E_SHOTS_DIR) return;
  mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
  await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `${name}.png`), animations: 'disabled' });
}
const noOverflow = async page => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);

test('packaged board shows Goal tags and persists the goal-only filter in desktop and mobile themes', async t => {
  for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
    const page = await pageFor(t, mobile, theme);
    await page.goto(daemon.base + '/#/board');
    await page.getByRole('button', { name: '仅长程目标', exact: true }).click();
    if (mobile) await page.getByRole('tab', { name: /待验收/ }).click();
    await page.locator('.ext-board-goal[data-goal-id="proposal"]:visible').first().waitFor();
    assert.match(await page.locator('.ext-board-goal[data-goal-id="proposal"]:visible').first().textContent(), /等你/);
    assert.match(await page.locator('.ext-board-goal[data-goal-id="paused"]').textContent(), /暂停/);
    assert.equal(await page.getByRole('button', { name: 'Ordinary conversation', exact: true }).count(), 0);
    assert.equal(await page.locator('.ext-board-goal').evaluateAll(nodes => nodes.every(node => getComputedStyle(node).whiteSpace === 'nowrap' && node.clientHeight <= 22)), true);
    await noOverflow(page);
    await shot(page, `goal-board-${mobile ? 'phone' : 'desktop'}-${theme}`);
    if (!mobile && theme === 'light') {
      await page.getByRole('button', { name: '项目', exact: true }).click();
      await shot(page, 'goal-board-desktop-filter-open');
      await page.keyboard.press('Escape');
    }
    await page.reload();
    await page.getByRole('button', { name: '仅长程目标', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '仅长程目标', exact: true }).getAttribute('aria-pressed'), 'true');
    await page.getByRole('button', { name: '仅长程目标', exact: true }).click();
    if (mobile) await page.getByRole('tab', { name: /待验收/ }).click();
    await page.getByRole('button', { name: 'Ordinary conversation', exact: true }).waitFor();
    const plainCard = page.locator('.ext-board-card').filter({ has: page.getByRole('button', { name: 'Ordinary conversation', exact: true }) });
    assert.equal(await plainCard.locator('.ext-board-tags').count(), 0, 'ordinary cards have unchanged tag markup');
    await page.close();
  }
});

test('legacy Goal deep links and All Goals lead to the one board or in-place conversation details', async t => {
  for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
    const page = await pageFor(t, mobile, theme);
    await page.goto(daemon.base + '/#/goals');
    await page.locator('.steward-overview').waitFor();
    await page.waitForURL(daemon.base + '/#/steward?goals');
    await page.getByRole('button', { name: '在看板查看 Goal 对话', exact: true }).click();
    await page.locator('.ext-board-page').waitFor();
    await page.waitForURL(daemon.base + '/#/board');
    assert.equal(await page.getByRole('button', { name: '仅长程目标', exact: true }).getAttribute('aria-pressed'), 'true');
    await page.goto(daemon.base + '/#/goals/proposal/files');
    await page.locator('[data-goal-detail="proposal"]').waitFor();
    assert.equal(await page.locator('.goal-page,.goals-page,.goal-tabs').count(), 0);
    assert.equal(await page.getByRole('button', { name: '收起目标', exact: true }).count(), 1);
    const toggles = page.locator('.goal-detail-toggle');
    assert.deepEqual(await toggles.evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-expanded'))), ['false', 'false', 'false', 'false']);
    const before = reads.filter(path => path.includes('goal_id=proposal')).length;
    await toggles.filter({ hasText: '任务' }).click();
    await page.getByRole('button', { name: /Review the proposal and choose/ }).waitFor();
    assert.ok(reads.filter(path => path.includes('goal_id=proposal')).length > before);
    assert.equal(await page.locator('.goal-tasks-board,.goal-tasks-switch').count(), 0);
    await page.locator('.goal-tasks-owner').click();
    await page.getByRole('menuitemradio', { name: 'reviewer', exact: true }).click();
    await page.getByRole('button', { name: /Review supporting evidence/ }).waitFor();
    assert.equal(await page.getByRole('button', { name: /Run independent checks/ }).count(), 0);
    await page.locator('.goal-tasks-owner').click();
    await page.getByRole('menuitemradio', { name: '全部负责人（2）', exact: true }).click();
    await noOverflow(page);
    const fitted = await page.locator('.thread-overview.with-goal-details').evaluate(el => {
      const content = el.querySelector('.overview-content').getBoundingClientRect();
      const footer = el.querySelector('.overview-foot').getBoundingClientRect();
      return content.bottom <= footer.top + 1 && el.getBoundingClientRect().bottom <= innerHeight;
    });
    assert.equal(fitted, true, 'expanded readers stay inside the panel above its footer');
    await shot(page, `goal-details-${mobile ? 'phone' : 'desktop'}-${theme}`);
    filesFail = true;
    await toggles.filter({ hasText: '文件' }).click();
    await page.locator('.goal-files [role="alert"]').waitFor();
    await shot(page, `goal-files-failure-${mobile ? 'phone' : 'desktop'}-${theme}`);
    filesFail = false;
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await page.locator('.goal-files [role="alert"]').waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: '浏览工作区', exact: true }).click();
    const files = page.locator('.files-panel');
    await files.locator('.files-row', { hasText: 'proposal.txt' }).click();
    await files.getByText('Synthetic proposal with review evidence.', { exact: false }).waitFor();
    await shot(page, `goal-workspace-${mobile ? 'phone' : 'desktop'}-${theme}`);
    await files.getByRole('button', { name: mobile ? '关闭' : '关闭文件', exact: true }).click();
    await files.waitFor({ state: 'hidden' });
    if (mobile) await page.keyboard.press('Escape');
    await page.goto(daemon.base + '/#/goals');
    await page.locator('.steward-overview').waitFor();
    await page.getByRole('button', { name: '在看板查看 Goal 对话', exact: true }).click();
    await page.locator('.ext-board-page').waitFor();
    await page.waitForURL(daemon.base + '/#/board');
    assert.equal(new URL(page.url()).hash, '#/board');
    assert.equal(await page.getByRole('button', { name: '仅长程目标', exact: true }).getAttribute('aria-pressed'), 'true');
    await page.goto(daemon.base + '/#/goals/missing');
    await page.locator('.steward-overview').waitFor();
    await page.waitForURL(daemon.base + '/#/steward?goal=missing');
    await page.locator('.overview-goal[data-goal-id="missing"]').getByText('这个 Goal 已经不在了：可能已被删除。', { exact: true }).waitFor();
    await page.close();
  }
});


test('unbound Core Goal bookmarks open a read-only steward reader and survive reload', async t => {
  for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
    const page = await pageFor(t, mobile, theme);
    await page.goto(daemon.base + '/#/goals/core-8/tasks');
    const details = page.locator('[data-goal-detail="core-8"]');
    await details.waitFor();
    await page.waitForURL(daemon.base + '/#/steward?goal=core-8');
    const row = page.locator('.overview-goal[data-goal-id="core-8"]');
    assert.match(await row.textContent(), /Core-only Goal 8/);
    assert.equal(await page.locator('.ext-board-page,.goal-page,.goals-page').count(), 0);
    const toggles = details.locator('.goal-detail-toggle');
    assert.deepEqual(await toggles.evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-expanded'))), ['false', 'false', 'false', 'false']);
    await toggles.filter({ hasText: '概览' }).click();
    await details.getByText('Retain independently readable results for Core Goal 8.', { exact: true }).waitFor();
    await noOverflow(page);
    await shot(page, `unbound-goal-${mobile ? 'phone' : 'desktop'}-${theme}`);
    await toggles.filter({ hasText: '任务' }).click();
    await details.getByRole('button', { name: /Review supporting evidence for core-8/ }).waitFor();
    await toggles.filter({ hasText: '文件' }).click();
    await details.getByRole('button', { name: /Core-only retained report/ }).click();
    await details.getByText('Retained Core report without a Workbench conversation.', { exact: true }).waitFor();
    assert.equal(await details.getByRole('button', { name: '对话', exact: true }).count(), 0);
    assert.equal(await details.getByRole('button', { name: '浏览工作区', exact: true }).count(), 0);
    await toggles.filter({ hasText: '验收' }).click();
    await details.getByText('Independent evidence remains readable', { exact: true }).waitFor();
    assert.equal(await details.locator('.goal-accept-actions').count(), 0);
    await page.reload();
    await page.locator('[data-goal-detail="core-8"]').waitFor();
    assert.match(new URL(page.url()).hash, /goal=core-8/);
    await page.close();
  }
});

test('eight Core Goals with no Workbench bindings keep their reader and hide the board shortcut', async t => {
  for (const mobile of [false, true]) {
    const page = await pageFor(t, mobile, 'light', false);
    await page.goto(daemon.base + '/#/goals');
    await page.locator('.steward-overview').waitFor();
    await page.waitForURL(daemon.base + '/#/steward?goals');
    assert.equal(await page.getByRole('button', { name: '在看板查看 Goal 对话', exact: true }).count(), 0);
    assert.equal(await page.locator('.ext-board-page').count(), 0);
    assert.equal(await page.locator('.thread-item').count(), 5);
    await page.reload();
    await page.locator('.steward-overview').waitFor();
    assert.equal(new URL(page.url()).hash, '#/steward?goals');
    await page.goto(daemon.base + '/#/goals/proposal');
    await page.locator('[data-goal-detail="proposal"]').waitFor();
    await page.waitForURL(daemon.base + '/#/steward?goal=proposal');
    assert.match(await page.locator('.overview-goal[data-goal-id="proposal"]').textContent(), /Review the launch proposal/);
    await page.close();
  }
});
