import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

const answer = [
  '建议先走 CPU 验证路线：',
  '',
  '```bash',
  'git clone https://example.com/flash/FlashProve.git && cd FlashProve',
  '# 编译验证器',
  'bash verifier/build.sh',
  'bin/cp_verify -id "$(cat examples/ID.txt)"',
  '```',
  '',
  '本机评测使用的 key：',
  '',
  '```text',
  'demo_sk_0000_not_a_real_key_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  '```',
  '',
  '```json',
  '{ "region": "cn", "retries": 3 }',
  '```',
].join('\n');

test('code in an answer is one card: its language in plain type, copy at the right, highlighted; plain text names nothing', async () => {
  let daemon, browser;
  try {
    daemon = await startDaemon();
    browser = await launchBrowser();
    const post = (path, body) => api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
    const thread = await (await post('/api/threads', { agent: 'pi', cwd: daemon.workdir })).json();
    await post(`/api/threads/${thread.id}/turns`, { text: answer });
    const deadline = Date.now() + 10_000;
    while ((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status === 'running') {
      assert.ok(Date.now() < deadline, 'fake agent finished');
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-item', { hasText: '建议先走' }).click();
        const blocks = page.locator('.row.assistant .code-block');
        const bash = blocks.filter({ hasText: 'verifier/build.sh' });
        await bash.locator('.hljs-comment', { hasText: '编译验证器' }).waitFor();
        assert.equal(await bash.locator('.code-lang').innerText(), 'Bash');
        assert.ok(await bash.locator('.hljs-string').count(), 'the quoted part is coloured');
        // One card: the name sits on the code's own background, with no line under it.
        const look = await bash.evaluate(el => ({ head: getComputedStyle(el.querySelector('.code-head')).borderBottomStyle, pre: getComputedStyle(el.querySelector('pre')).backgroundColor }));
        assert.equal(look.head, 'none');
        assert.equal(look.pre, 'rgba(0, 0, 0, 0)');
        const plain = blocks.filter({ hasText: 'demo_sk_' });
        assert.equal(await plain.locator('.code-lang').count(), 0);
        assert.match(await plain.getAttribute('class'), /\bbare\b/);
        await plain.getByRole('button', { name: '复制代码' }).waitFor();
        assert.equal(await blocks.filter({ hasText: 'retries' }).locator('.code-lang').innerText(), 'JSON');
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await bash.scrollIntoViewIfNeeded();
          await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `code-blocks-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
      } finally { await context.close(); }
    }
  } finally {
    await browser?.close();
    await daemon?.stop();
  }
});
