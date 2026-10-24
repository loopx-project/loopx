// Packaged visual/interaction contract. All task names are invented; source
// request/recovery and state parity are covered by the owning work-map smoke.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { startServer, port, outputDir } from '../../../../examples/personal-workspace-browser/fixture.mjs';
import { launchBrowser, loadPlaywright, waitForHttp } from '../../../../examples/dashboard-browser-smoke-support.mjs';
import { openWorkspacePage } from '../../../../examples/personal-workspace-browser/scenario-context.mjs';
import { installShowcaseWorkspace, openShowcaseGoal, showcaseGoalId, showcaseGoalTitle } from '../../../../examples/personal-workspace-browser/showcase-workspace.mjs';

const longTitle = `${showcaseGoalTitle}: preserve all consumer requirements, reconcile historical exports, qualify replay safety and prove recovery across two independent workers before retiring the old export service`;
const nodes = Array.from({ length: 8 }, (_, index) => ({
  node_id: `craft-${index}`, kind: 'deliverable', state: 'open', depth: index % 4,
  title: ['Inventory every downstream export consumer and preserve the complete original requirements', 'Verify replay safety with repeated writes and independent acceptance evidence', 'Reconcile archived totals and investigate every missing or inconsistent record', 'Qualify reconnect, interrupted delivery and result adoption by the original requester'][index % 4],
  owner_agent: 'ledger-migrator', task_domain: 'validation', refs: {},
}));
const edges = nodes.slice(1).map((node, index) => ({ edge_id: `craft-edge-${index}`, from_node_id: node.node_id, to_node_id: nodes[index].node_id,
  relation: 'depends_on', enforcement: 'typed_condition', reason: 'Recorded prerequisite' }));
edges.push({ edge_id: 'craft-skip', from_node_id: nodes[3].node_id, to_node_id: nodes[0].node_id, relation: 'continues', enforcement: 'lineage_only', reason: 'Recorded successor' });
const server = await startServer();
let browser;
try {
  const url = `http://127.0.0.1:${port}/chat/?statusUrl=/status.json`;
  await waitForHttp(url);
  await mkdir(outputDir, { recursive: true });
  browser = await launchBrowser(loadPlaywright().chromium);
  const state = { done: 23, cost: 24.8, deliveryFails: false };
  const session = await openWorkspacePage({ newPage: options => browser.newPage({ locale: 'zh-CN', ...options }) }, url, {
    viewport: { width: 1440, height: 1000 },
    beforeGoto: async (api, page) => {
      await installShowcaseWorkspace(api, page, state);
      const transform = api.transformStatus;
      api.transformStatus = fixture => {
        transform(fixture);
        fixture.run_history.goals.find(goal => goal.id === showcaseGoalId).display_name = longTitle;
        if (state.quiet) {
          const queue = fixture.attention_queue.items.find(item => item.goal_id === showcaseGoalId);
          Object.assign(queue.agent_todos, { advancement_done_count: 0, done_count: 0, deferred_count: 0, open_count: 0, total_count: 0, items: [], deferred_items: [] });
          Object.assign(queue.project_asset.agent_todos, { advancement_done_count: 0, done: 0, deferred_count: 0, open: 0, total: 0, items: [], recent_completed_advancement_items: [] });
          Object.assign(queue.user_todos, { open_count: 0, total_count: 0, items: [] });
          for (const agent of fixture.agent_management_projection.agents) {
            if (agent.current_todo?.goal_id === showcaseGoalId) agent.current_todo = null;
          }
        }
      };
      await page.route('**/api/chat/delivery-review?*', route => route.fulfill({ json: {
        ok: true, goal_id: showcaseGoalId, observed_at: '2026-09-01T10:00:00Z', acceptance: null, graph: null,
        goal_map: { schema_version: 'goal_task_map_v0', mode: 'read_only', goal_id: showcaseGoalId,
          limits: { node_limit: 100, emitted_node_count: nodes.length, omitted_node_count: 0, source_truncated: false, missing_endpoint_count: 0, cycle_edge_count: 0, topology_complete: true }, nodes, edges },
      } }));
    },
  });
  const { page } = session;
  try {
    await openShowcaseGoal(page);
    const inlineRail = page.getByTestId('goal-workspace-inline-rail');
    const inventory = inlineRail.locator('.goal-workspace-inventory');
    const decision = page.locator('.goal-overview [data-attention-id="showcase-gate-window"]:visible');
    assert.equal(await decision.count(), 1, 'The owner decision is shown once in the composed Overview');
    assert.equal(await inlineRail.locator('.goal-all-work').count(), 0, 'The full inventory starts collapsed instead of repeating current work');
    assert.ok(await decision.locator('.goal-attention-text').evaluate(element => parseFloat(getComputedStyle(element).fontSize) >= 16), 'Owner decisions use readable lead-size text');
    assert.ok(await decision.locator('svg').first().evaluate(element => {
      const swatch = document.createElement('span');
      swatch.style.color = 'var(--pw-caution)';
      element.parentElement.append(swatch);
      const matches = getComputedStyle(element).color === getComputedStyle(swatch).color;
      swatch.remove();
      return matches;
    }), 'The owner-decision hand retains the theme gold');
    await inventory.locator('summary').focus();
    await page.keyboard.press('Enter');
    await inlineRail.locator('.goal-work-filters').waitFor({ state: 'visible' });
    assert.equal(await page.locator('.goal-overview [data-attention-id="showcase-gate-window"]:visible').count(), 1, 'Expanding All work does not duplicate the owner decision');
    assert.equal(await inlineRail.getByRole('heading', { name: '等待决定', exact: true }).count(), 0, 'Empty work sections do not leave duplicate-looking headings');
    assert.ok(await inlineRail.locator('.goal-work-row strong').first().evaluate(element => parseFloat(getComputedStyle(element).fontSize) >= 14), 'Inventory tasks use body-size text rather than captions');
    await inlineRail.getByRole('button', { name: '活动', exact: true }).click();
    await inlineRail.locator('.goal-material-activity').waitFor({ state: 'visible' });
    await inlineRail.getByRole('button', { name: '全部工作', exact: true }).click();
    let historyReads = 0;
    page.on('request', request => { if (new URL(request.url()).pathname === '/api/chat/completed-todos') historyReads++; });
    const historyResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/chat/completed-todos');
    await inlineRail.locator('[data-testid="completed-task-lane"] > header button').click();
    await historyResponse;
    const overview = page.locator('.goal-overview');
    await overview.evaluate(element => element.style.maxWidth = '800px');
    await page.waitForFunction(() => !document.querySelector('.goal-workspace-rail .goal-all-work'));
    assert.equal(await inventory.isVisible(), false, 'Container reflow hides the inline inventory');
    const readsBeforeHiddenRefresh = historyReads;
    state.done++;
    await page.getByRole('button', { name: '刷新状态', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="goal-vitals"] .goal-vital')[1]?.querySelector('.goal-vital-value')?.textContent === '24/29');
    assert.equal(historyReads, readsBeforeHiddenRefresh, 'Refreshing a hidden inventory does not read completed history');
    await overview.evaluate(element => element.style.removeProperty('max-width'));
    await inlineRail.locator('.goal-work-filters').waitFor({ state: 'visible' });
    await inventory.locator('summary').click();
    await page.getByRole("region", {name:"当前工作",exact:true}).getByRole("button",{name:"关系图",exact:true}).click();
    const heading = page.locator('.personal-channel-title h1');
    assert.equal(await heading.innerText(), longTitle, 'The original Goal title remains complete');
    assert.doesNotMatch(await page.locator('.goal-masthead-breadcrumb').evaluate(element => [...element.children].filter(child => child.getBoundingClientRect().width > 2).map(child => child.textContent).join(' ')), /preserve all consumer/, 'Visible breadcrumb does not repeat the long request');
    assert.equal(await page.locator('.goal-masthead-breadcrumb [aria-current]').textContent(), longTitle, 'Assistive readers retain the complete current-page identity');
    assert.doesNotMatch(await page.getByTestId('overview-composer-pill').innerText(), /ledger|consumer/, 'The input invitation stays concise');
    await page.locator('.goal-work-lenses').getByRole('button', {name: /^(关系图|Map)$/}).click();
    const map = page.locator('.work-map');
    await map.scrollIntoViewIfNeeded();
    const scroll = page.locator('.personal-channel-scroll');
    const target = map.locator('.work-map-canvas .work-map-node').last();
    await target.scrollIntoViewIfNeeded();
    const scrollBefore = await scroll.evaluate(element => element.scrollTop);
    await target.focus();
    await page.keyboard.press('Enter');
    await map.getByRole('button', { name: '关闭事项详情' }).waitFor();
    assert.ok(Math.abs((await scroll.evaluate(element => element.scrollTop)) - scrollBefore) <= 2, 'Selecting a task preserves the reading position');
    const visible = await target.evaluate(element => { const node = element.getBoundingClientRect(), viewport = element.closest('.work-map-scroll').getBoundingClientRect(); return node.left >= viewport.left && node.right <= viewport.right && node.top >= viewport.top && node.bottom <= viewport.bottom; });
    assert.ok(visible, 'The selected task stays inside the canvas when the inspector opens');
    const card = await target.evaluate(element => {
      const title = element.querySelector('strong').getBoundingClientRect(), footer = element.querySelector('small').getBoundingClientRect();
      return { titleBottom: title.bottom, footerTop: footer.top, font: parseFloat(getComputedStyle(element.querySelector('strong')).fontSize), scale: getComputedStyle(element.closest('.work-map-canvas')).transform };
    });
    assert.ok(card.titleBottom <= card.footerTop, 'Task title cannot overlap its metadata');
    assert.ok(card.font >= 14, 'Task text stays at the design minimum');
    assert.equal(card.scale, 'matrix(1, 0, 0, 1, 0, 0)', 'A wide graph scrolls instead of shrinking text');
    await page.screenshot({ path: resolve(outputDir, 'goal-craft-map-desktop.png'), animations: 'disabled' });
    await map.getByRole('button', { name: '关闭事项详情' }).click();
    assert.equal(await map.locator('.work-map-node[aria-pressed="true"]').count(), 0);
    assert.ok(await target.evaluate(element => element === document.activeElement), 'Closing details returns focus to the task');
    await page.keyboard.press('Enter'); await page.keyboard.press('Escape');
    assert.equal(await map.locator('.work-map-node[aria-pressed="true"]').count(), 0, 'Escape closes the map selection');
    await inventory.locator('summary').click();
    await inlineRail.locator('.goal-all-work').waitFor({ state: 'visible' });
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForFunction(() => !document.querySelector('.goal-workspace-rail .goal-all-work'));
    assert.equal(await inventory.isVisible(), false, 'Narrow view falls back to the All work drawer instead of crowding the brief');
    assert.equal(await page.locator('.goal-overview [data-attention-id="showcase-gate-window"]:visible').count(), 1, 'The owner decision remains visible once on the narrow page');
    const openAllWork = page.getByRole('button', { name: '打开全部工作' });
    await openAllWork.click();
    const railDialog = page.locator('.goal-work-rail-dialog');
    assert.ok(await railDialog.locator('.goal-all-work.is-dialog').isVisible(), 'The narrow drawer keeps the same All work inventory reachable');
    await railDialog.locator('[data-attention-id="showcase-gate-window"]').waitFor({ state: 'visible' });
    await page.keyboard.press('Escape');
    assert.equal(await railDialog.isVisible(), false, 'Escape closes the inventory drawer');
    assert.ok(await openAllWork.evaluate(element => element === document.activeElement), 'Closing the inventory restores focus to the original control');
    const measured = await heading.evaluate(element => ({ height: element.clientHeight, line: parseFloat(getComputedStyle(element).lineHeight), overflow: element.scrollWidth - element.clientWidth }));
    assert.ok(measured.height > measured.line && measured.overflow <= 1, 'A long title wraps without clipping or page overflow');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('.goal-overview [data-attention-id="showcase-gate-window"]:visible').count(), 1, 'Phone reflow retains one owner decision');
    assert.ok(await page.locator('.goal-overview-attention .goal-attention-text').first().evaluate(element => parseFloat(getComputedStyle(element).fontSize) >= 16), 'Phone reflow does not shrink the requested reading text');
    const headerHeight = await page.locator('.personal-channel-header').evaluate(element => element.getBoundingClientRect().height);
    assert.ok(headerHeight <= 220, 'A long Goal title cannot consume the phone viewport');
    assert.equal(await heading.evaluate(element => getComputedStyle(element).overflow), 'hidden', 'Phone title clipping must survive the packaged CSS cascade, not just constrain the box height');
    await page.getByRole('button', { name: '查看完整 Goal' }).click();
    await page.locator('.personal-context-drawer').waitFor({ state: 'visible' });
    assert.match(await page.locator('.personal-context-drawer').innerText(), /prove recovery across two independent workers/, 'The compact title opens the original Goal in its existing details');
    await page.locator('.personal-context-drawer').getByRole('button', { name: /关闭|Close/ }).first().click();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await map.locator('.work-map-list').waitFor({ state: 'visible' });
    assert.equal(await map.locator('.work-map-canvas').isVisible(), false, 'Phone uses a readable task list');
    const mobile = map.locator('.work-map-list .work-map-node').first();
    assert.equal(await mobile.locator('strong').evaluate(element => getComputedStyle(element).maxHeight), 'none', 'Phone titles are not line-clipped');
    await mobile.click();
    await page.waitForFunction(() => document.activeElement?.classList.contains('work-map-inspector'));
    assert.ok(await map.locator('.work-map-inspector').evaluate(element => element === document.activeElement), 'Phone selection reaches keyboard-accessible details');
    await page.screenshot({ path: resolve(outputDir, 'goal-craft-map-phone.png'), animations: 'disabled' });
    await map.getByRole('button', { name: '关闭事项详情' }).click();
    assert.ok(await mobile.evaluate(element => element === document.activeElement));
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'The phone has no page-wide horizontal overflow');
    state.quiet = true;
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: '刷新状态', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="goal-vitals"] .goal-vital')[1]?.querySelector('.goal-vital-value')?.textContent === '0/0');
    await inlineRail.waitFor({ state: 'detached' });
    assert.ok(await page.locator('.goal-workspace-composition').evaluate(element => {
      const frame = element.getBoundingClientRect(), document = element.querySelector('.goal-workspace-document').getBoundingClientRect();
      return Math.abs(frame.width - document.width) <= 1;
    }), 'A known-quiet Goal gives its reading column the full width instead of reserving a blank rail');
    assert.deepEqual(session.errors, [], 'Packaged composition emits no browser errors');
    console.log('Goal workspace craft: single gold attention rail, readable inventory, hidden-history isolation, quiet layout, long-title, stationary selection, close/Escape/focus, 390px and reduced-motion checks passed');
  } finally { await session.close(); }
} finally { await browser?.close(); server.kill('SIGTERM'); }
