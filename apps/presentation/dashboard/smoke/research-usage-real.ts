/** Frozen, real nine-hour counter projections. Synthetic cases are limited to
 * transport failure, cancellation and stale-source boundaries, never outcomes. */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import type {Browser, Page} from 'playwright';

type Owner = {base: string; close(): Promise<void>};
type Context = {repoRoot: string; outputDir: string; startOwner(path: string): Promise<Owner>};

export async function readRealUsage(browser: Browser, context: Context) {
  const source = JSON.parse(await readFile(resolve(context.repoRoot,
    'apps/presentation/dashboard/src/data/fixtures/research-usage-real-captures.json'), 'utf8'));
  const directory = await mkdtemp(resolve(tmpdir(), 'loopx-usage-display-'));
  const path = resolve(directory, 'readbacks.json');
  const records: Record<string, unknown> = {};
  const record = (key: string, query: Record<string, string>, response: unknown) => {
    records[key] = {request_path: '/api/chat/research-analysis?' + new URLSearchParams(query), response,
      response_sha256: createHash('sha256').update(JSON.stringify(response)).digest('hex')};
  };
  const ids: string[] = source.packs.map((p: {pack_id: string}) => p.pack_id);
  for (const language of ['original', 'zh', 'en']) {
    const identities = source.readback_identities[language];
    const packs = source.packs.map((p: {pack_id: string}) => ({...p, ...identities[p.pack_id]}));
    const query = {goal_id: source.owner_goal_id, ...(language === 'original' ? {} : {output_language: language})};
    for (const pack of packs) {
      const response = {ok: true, execution_enabled: false,
        catalog: {availability: 'available', reason_code: null, default_pack_id: source.default_pack_id, packs}, pack};
      record(`${language}:${pack.pack_id}`, {...query, pack_id: pack.pack_id}, response);
      if (pack.pack_id === source.default_pack_id) record(language, query, response);
      for (const reference of ids.filter(id => id !== pack.pack_id)) {
        record(`${language}:${pack.pack_id}:${reference}`, {...query, pack_id: pack.pack_id, compare_pack_id: reference},
          source.comparisons[`${pack.pack_id}:${reference}`]);
      }
    }
  }
  await writeFile(path, JSON.stringify({goal_id: source.owner_goal_id, display_name: 'LoopX Benchmark', events: [], analysis_readbacks: records}));
  let owner: Owner | undefined, page: Page | undefined;
  let mutations = 0;
  try {
    owner = await context.startOwner(path);
    page = await browser.newPage({viewport: {width: 1440, height: 900}});
    await page.addInitScript(() => localStorage.setItem('loopx-pw-locale',
      new URLSearchParams(location.search).get('fixture_language') === 'zh' ? 'zh-CN' : 'en'));
    page.on('request', request => {if (request.method() !== 'GET') mutations++;});
    const open = async (index: number, language = 'en', reference?: number) => {
      const query = new URLSearchParams({goalId: source.owner_goal_id, view: 'research',
        fixture_language: language,
        research_analysis_goal: source.owner_goal_id, research_pack: ids[index]!,
        ...(reference === undefined ? {} : {research_compare: ids[reference]!})});
      await page!.goto(`${owner!.base}/chat/?${query}`);
      await page!.waitForFunction(() => document.querySelector('.research-history')?.getAttribute('aria-busy') === 'false');
      const progress = page!.locator('.research-progress');
      await progress.getByRole('combobox', {name: language === 'zh' ? '评估范围' : 'Evaluation scope', exact: true}).waitFor();
      await progress.locator(`[data-pack-id="${ids[index]}"]`).waitFor();
      if (!await progress.locator('.research-progress-basis').evaluate(element => (element as HTMLDetailsElement).open)) {
        await progress.locator('.research-progress-basis > summary').click();
      }
      const comparison = progress.locator('.research-usage-comparison');
      if (!await comparison.evaluate(element => (element as HTMLDetailsElement).open)) await comparison.locator(':scope > summary').click();
      return comparison;
    };
    const codec = await open(0, 'en', 1);
    await codec.locator('table').waitFor();
    assert.match(await codec.innerText(), /share fell while its absolute input rose/);
    const row = (label: string) => codec.getByRole('row').filter({has: page!.getByRole('rowheader', {name: label, exact: true})});
    assert.deepEqual(await row('All-request input (includes cache)').locator('td').allTextContents(), ['184,098,417', '95,917,713', '+88,180,704']);
    assert.deepEqual(await row('LoopX command requests · whole input').locator('td').allTextContents(), ['10,358,194', '5,561,624', '+4,796,570']);
    assert.deepEqual(await row('LoopX command requests · of which cached').locator('td').allTextContents(), ['8,858,112', '5,470,080', '+3,388,032']);
    assert.deepEqual(await row('LoopX command requests · uncached').locator('td').allTextContents(), ['1,500,082', '91,544', '+1,408,538']);
    assert.deepEqual(await row('LoopX command requests · output').locator('td').allTextContents(), ['20,026', '15,327', '+4,699']);
    assert.deepEqual(await row('Control text / input').locator('td').allTextContents(), ['—', '—', '— pp']);
    assert.deepEqual(await row('LoopX command requests / input').locator('td').allTextContents(), ['5.6%', '5.8%', '-0.17 pp']);
    assert.match(await page.getByRole('region', {name: 'Requests issuing LoopX commands', exact: true}).innerText(), /74 \/ 997.*incomplete attribution/);
    await codec.locator('.research-comparison-sources > details > summary').first().click();
    assert.match(await codec.innerText(), /codec-sol-xhigh-03-explore-on-fourarm-r12-a2/);
    assert.match(await codec.innerText(), /78a399ae364790c30481e448276c9dddb57b00ee1ddc61300110d45673243be8/);
    assert.equal(await page.getByRole('button', {name: /Analyze selected scope|用中文分析/}).count(), 0);
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      await codec.scrollIntoViewIfNeeded();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert((await codec.locator('caption').boundingBox())!.height < 80,
        'The narrow caption must remain a line, not one character per row');
      await page.screenshot({path: resolve(context.outputDir, `research-usage-real-codec-en-${width}.png`), animations: 'disabled'});
    }
    await codec.getByRole('combobox').selectOption(ids[2]!);
    await codec.getByRole('status').filter({hasText: 'cannot be aligned'}).waitFor();
    assert.equal(await codec.locator('table').count(), 0, 'Cross-case selection must remove the old comparison');
    await page.goBack();
    await page.locator('.research-progress-basis > summary').waitFor();
    if (!await page.locator('.research-progress-basis').evaluate(element => (element as HTMLDetailsElement).open)) {
      await page.locator('.research-progress-basis > summary').click();
    }
    if (!await codec.evaluate(element => (element as HTMLDetailsElement).open)) await codec.locator(':scope > summary').click();
    await codec.locator('table').waitFor();
    assert.equal(await codec.getByRole('combobox').inputValue(), ids[1]);
    const grid = await open(2, 'zh', 3);
    await grid.locator('table').waitFor();
    assert.match(await page.getByRole('region', {name: 'LoopX 命令请求', exact: true}).innerText(), /58 \/ 818/);
    assert.match(await grid.innerText(), /10,759,031/);
    assert.match(await grid.innerText(), /373,449/);
    await page.reload();
    await page.locator('.research-progress-basis > summary').click();
    const restored = page.locator('.research-usage-comparison');
    await restored.locator(':scope > summary').click();
    await restored.locator('table').waitFor();
    assert.equal(await restored.getByRole('combobox').inputValue(), ids[3]);
    await restored.locator(':scope > summary').focus();
    await page.keyboard.press('Enter');
    assert.equal(await restored.evaluate(element => (element as HTMLDetailsElement).open), false);
    await page.keyboard.press('Enter');
    await restored.locator('table').waitFor();
    await page.screenshot({path: resolve(context.outputDir, 'research-usage-real-grid-zh-390.png'), animations: 'disabled'});

    // An explicit transient transport failure cannot retain an earlier number.
    const endpoint = '**/api/chat/research-analysis?*';
    await page.route(endpoint, route => new URL(route.request().url()).searchParams.has('compare_pack_id')
      ? route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: 'Source unavailable'})}) : route.continue());
    await restored.locator(':scope > summary').click();
    await restored.locator(':scope > summary').click();
    await restored.getByRole('alert').waitFor();
    assert.equal(await restored.locator('table').count(), 0);
    await page.unroute(endpoint);
    await restored.getByRole('button', {name: '重试读取', exact: true}).click();
    await restored.locator('table').waitFor();

    // Cancel a delayed successful response; its late completion cannot win.
    let release!: () => void;
    const released = new Promise<void>(accept => {release = accept;});
    let started!: () => void;
    const pending = new Promise<void>(accept => {started = accept;});
    await page.route(endpoint, async route => {
      const query = new URL(route.request().url()).searchParams;
      if (!query.has('compare_pack_id')) return route.continue();
      started(); await released;
      await route.fulfill({json: source.comparisons[`${ids[2]}:${ids[3]}`]}).catch(() => {});
    });
    await restored.locator(':scope > summary').click();
    await restored.locator(':scope > summary').click();
    await pending;
    await restored.getByRole('button', {name: '取消读取', exact: true}).click();
    assert.match(await restored.innerText(), /已取消读取对照/);
    assert.equal(await restored.locator(':scope > summary').evaluate(element => element === document.activeElement), true);
    release(); await page.unrouteAll({behavior: 'wait'});
    assert.equal(await restored.locator('table').count(), 0);
    await restored.getByRole('button', {name: '重试读取', exact: true}).click();
    await restored.locator('table').waitFor();

    // Older servers omit additive delta fields; never render NaN or invent them in the view.
    const legacy = structuredClone(source.comparisons[`${ids[2]}:${ids[3]}`]);
    for (const key of ['command_cached', 'command_uncached', 'command_output']) delete legacy.comparison.delta[key];
    await page.route(endpoint, route => new URL(route.request().url()).searchParams.has('compare_pack_id')
      ? route.fulfill({json: legacy}) : route.continue());
    await restored.locator(':scope > summary').click();
    await restored.locator(':scope > summary').click();
    await restored.locator('table').waitFor();
    for (const name of ['LoopX 命令请求 · 其中缓存', 'LoopX 命令请求 · 未缓存', 'LoopX 命令请求 · 输出']) {
      const cells = restored.getByRole('row').filter({has: page.getByRole('rowheader', {name, exact: true})}).locator('td');
      await cells.last().filter({hasText: /^—$/}).waitFor();
      assert.equal(await cells.last().innerText(), '—');
    }
    await page.unroute(endpoint);
    const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
    assert.equal(readback.source_unchanged, true);
    assert.equal(readback.mutation_attempts, 0);
    assert.equal(mutations, 0, 'Browsing, language, comparison, back, reload, cancellation and retry are GET-only');
    return {real_captures: 4, native_responses: 3325, recognized: [74, 30, 58, 4], model_calls: 0, source_unchanged: true};
  } catch (error) {
    if (page) console.error('Real usage readback failure', await page.evaluate(() => ({
      query: location.search, comparison: document.querySelector('.research-usage-comparison')?.textContent?.slice(0,1200),
      selections: [...document.querySelectorAll('.research-progress select')].map(element => (element as HTMLSelectElement).value),
      disclosures: [...document.querySelectorAll('.research-progress details')].map(element => ({
        name: element.querySelector('summary')?.textContent?.slice(0,80), open: (element as HTMLDetailsElement).open})).slice(-4),
    })));
    throw error;
  } finally {
    await page?.close(); await owner?.close(); await rm(directory, {recursive: true, force: true});
  }
}
