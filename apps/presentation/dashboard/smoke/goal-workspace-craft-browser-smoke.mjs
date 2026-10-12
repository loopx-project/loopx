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
    await page.setViewportSize({ width: 1024, height: 768 });
    const measured = await heading.evaluate(element => ({ height: element.clientHeight, line: parseFloat(getComputedStyle(element).lineHeight), overflow: element.scrollWidth - element.clientWidth }));
    assert.ok(measured.height > measured.line && measured.overflow <= 1, 'A long title wraps without clipping or page overflow');
    await page.setViewportSize({ width: 390, height: 844 });
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
    assert.deepEqual(session.errors, [], 'Packaged composition emits no browser errors');
    console.log('Goal workspace craft: packaged long-title, readable geometry, stationary desktop selection, close/Escape/focus, 390px and reduced-motion checks passed');
  } finally { await session.close(); }
} finally { await browser?.close(); server.kill('SIGTERM'); }
