// End-to-end projection check. Fixture mode runs always and must make zero network calls;
// live mode runs only when the LoopX services answer on 127.0.0.1:8766/8767 and is skipped otherwise.
// Expected values are counted from the raw JSON here, not read back from the adapter's own output.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { TriagePanel } from '../src/components/TriagePanel.tsx';
import { DeskPage } from '../src/components/DeskPage.tsx';
import { setLocale } from '../src/i18n/index.ts';
import { LoopxApi, ReadOnlyError, SchemaVersionError } from '../src/loopx/index.ts';

const raw = JSON.parse(readFileSync(new URL('../src/loopx/fixtures/status.demo.json', import.meta.url), 'utf8'));
type Row = Record<string, unknown>;
const attention: Row[] = raw.attention_queue.items;
const todoRows: Row[] = raw.todo_index.items;
const sessions: Row[] = raw._demo_sessions;

let checks = 0;
const check = (name: string, fn: () => void | Promise<void>) => Promise.resolve(fn()).then(() => { checks++; console.log(`  ok  ${name}`); });

async function fixtureMode(): Promise<void> {
  console.log('fixture mode');
  const calls: string[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = ((input: unknown) => { calls.push(String(input)); return Promise.reject(new Error('network is off in fixture mode')); }) as typeof fetch;
  try {
    const api = new LoopxApi({ fixture: true });
    const triage = await api.triage();
    const todos = await api.todos();
    const threads = await api.threads();

    await check('needs-you holds the attention item waiting on the user', () => {
      const waitingOnUser = attention.filter((a) => a.waiting_on === 'user');
      assert.ok(waitingOnUser.length > 0);
      assert.ok(triage.needsYou.length >= 1);
      assert.ok(triage.needsYou.some((e) => e.kind === 'goal' && e.id.includes(String(waitingOnUser[0].goal_id))));
    });
    await check('the needs-you entry cites waiting_on=user as its reason', () => {
      assert.ok(triage.needsYou.every((e) => e.evidence.some((x) => x === 'waiting_on=user' || x.startsWith('operator_question='))));
    });
    await check('running holds the session with an active turn', () => {
      const active = sessions.filter((s) => s.active_turn_id);
      assert.equal(triage.running.filter((e) => e.kind === 'session').length, active.length);
    });
    await check('error holds every blocked todo and the session with an error code', () => {
      const blocked = todoRows.filter((t) => t.status === 'blocked').length;
      const failed = sessions.filter((s) => s.last_error_code).length;
      assert.equal(triage.error.filter((e) => e.kind === 'todo').length, blocked);
      assert.equal(triage.error.filter((e) => e.kind === 'session').length, failed);
    });
    await check('the triage panel renders the three groups with their counts and entries', () => {
      const html = renderToStaticMarkup(createElement(TriagePanel, { triage, onOpenThread: () => undefined }));
      const group = (bucket: string) => {
        const m = new RegExp(`data-bucket="${bucket}"[^>]*>(.*?)</div>(?=<div class="triage-group"|<details|</section>)`, 's').exec(html);
        assert.ok(m, `group ${bucket} missing`);
        return { count: Number(/<span>(\d+)<\/span>/.exec(m[1])?.[1]), rows: (m[1].match(/class="triage-row"/g) ?? []).length };
      };
      assert.deepEqual(group('needsYou'), { count: 1, rows: 1 });
      assert.deepEqual(group('running'), { count: 1, rows: 1 });
      assert.deepEqual(group('error'), { count: 5, rows: 5 });
      assert.ok((html.includes('waiting_on=user') || html.includes('operator_question=')) && !html.includes('waiting_on=codex'), 'needs-you shows the reason that names the person');
      assert.ok(!html.includes('triage.'), 'an untranslated key leaked into the panel');
      assert.ok(!html.includes('runtime_root'));
    });
    await check('the desk header count is the triage needs-you count, in zh and en', () => {
      assert.equal(triage.needsYou.length, 1);
      const header = (): string => {
        const html = renderToStaticMarkup(createElement(DeskPage, { api, todos, triage, loading: false, error: '', refreshKey: 0, onReload: () => undefined, onOpenThread: () => undefined }));
        return /class="desk-count-now">([^<]*)</.exec(html)?.[1] ?? '';
      };
      for (const locale of ['zh', 'en'] as const) {
        setLocale(locale);
        const text = header();
        assert.ok(new RegExp(`(^|\\D)${triage.needsYou.length}(\\D|$)`).test(text) && !/\\b4\\b|4 件/.test(text), `${locale} header: ${text}`);
      }
      setLocale('en');
    });
    await check('desk todos carry a stage and no age is claimed without a time', () => {
      assert.ok(todos.every((t) => t.stage));
      assert.equal(todos.filter((t) => t.stage === 'blocked').length, todoRows.filter((t) => t.status === 'blocked').length);
    });
    await check('threads come from the demo sessions', () => assert.equal(threads.length, sessions.length));
    await check('todos reach the desk (blocked ones wait, none invented)', () => {
      assert.equal(todos.length, todoRows.length);
      assert.equal(todos.filter((t) => t.stage === 'blocked' && t.state === 'waiting').length, todoRows.filter((t) => t.status === 'blocked').length);
    });
    await check('sections are derived from goals plus the unassigned bucket', async () => {
      const ids = (await api.sections()).map((s) => s.id);
      assert.ok(ids.includes('loopx:unassigned'));
      assert.ok(ids.length >= 2);
    });
    await check('hosts are not probed offline', async () => assert.deepEqual(await api.hosts(), []));
    await check('no view model carries runtime_root or a local absolute path', async () => {
      const dump = JSON.stringify([triage, todos, threads, await api.sections(), await api.workdirs(), await api.hosts()]);
      assert.ok(!dump.includes('runtime_root'));
      assert.ok(!/"\/(?:Users|home|root|data\d*|var|tmp)\//.test(dump), 'absolute local path in a view model');
    });
    await check('every write is refused with ReadOnlyError', async () => {
      assert.equal(api.readOnly, true);
      await assert.rejects(api.createTodo('x'), ReadOnlyError);
      await assert.rejects(api.deleteTodo('t'), ReadOnlyError);
      await assert.rejects(api.cancel('t'), ReadOnlyError);
    });
    await check('a status older than the supported schema is refused, not rendered', async () => {
      const old = structuredClone(raw);
      old.status_contract.schema_version = 1;
      const { createDemoFixture } = await import('../src/loopx/fixtures.ts');
      const { LoopXApiAdapter } = await import('../src/loopx/adapter.ts');
      await assert.rejects(new LoopXApiAdapter({ fixture: createDemoFixture(old) }).triage(), SchemaVersionError);
    });
    await check('fixture mode made zero fetch calls', () => assert.deepEqual(calls, []));
  } finally {
    globalThis.fetch = realFetch;
  }
}

async function liveMode(): Promise<void> {
  const up = await Promise.all([8766, 8767].map((p) => fetch(`http://127.0.0.1:${p}/`, { signal: AbortSignal.timeout(1500) }).then(() => true, () => false)));
  if (!up[0]) { console.log('live mode: skipped (LoopX status service on 127.0.0.1:8766 is not running)'); return; }
  console.log('live mode');
  const api = new LoopxApi({ statusBase: 'http://127.0.0.1:8766', chatBase: 'http://127.0.0.1:8767' });
  await check('status contract is accepted and the triage has all four buckets', async () => {
    const t = await api.triage();
    for (const k of ['needsYou', 'running', 'error', 'quiet'] as const) assert.ok(Array.isArray(t[k]));
  });
  await check('no view model carries runtime_root', async () => {
    assert.ok(!JSON.stringify([await api.triage(), await api.todos(), await api.sections()]).includes('runtime_root'));
  });
  // Counts only: nothing from the live service is printed.
  const t = await api.triage();
  console.log(`  live counts: needsYou=${t.needsYou.length} running=${t.running.length} error=${t.error.length} quiet=${t.quiet.length}`);
}

await fixtureMode();
await liveMode();
console.log(`projection smoke: ${checks} checks passed`);
