import { api } from '../harness.mjs';

const post = async (daemon, text, extra = {}) => (await api(daemon, '/api/todos', { method: 'POST', body: JSON.stringify({ text, lane: 'you', ...extra }) })).json();

/** Real matters, and scripted runs on them: one of each state the desk can show. */
export async function arrangeProactive(daemon) {
  const sandboxTodo = await post(daemon, '修导出边界');
  const prepareTodo = await post(daemon, '周五的评审会');
  const mergedTodo = await post(daemon, '已经合入的事');
  const failedTodo = await post(daemon, '补迁移脚本');
  const at = minutes => new Date(Date.now() - minutes * 60_000).toISOString();
  const runs = [
    { id: 'sandbox-1', matterId: sandboxTodo.id, kind: 'sandbox', repo: '/repo', branch: `calli/proactive/${sandboxTodo.id}`, threadId: 'x1', startedAt: at(40), endedAt: at(30), status: 'ready', summary: '补好导出的边界测试，修了空列表时的崩溃。', testReport: 'npm test: 12 passed', files: [{ path: 'src/export.ts', additions: 10, deletions: 2 }, { path: 'test/export.test.ts', additions: 24, deletions: 0 }] },
    { id: 'prepare-1', matterId: prepareTodo.id, kind: 'prepare', threadId: 'x2', startedAt: at(90), endedAt: at(85), status: 'ready', summary: '列好目标、待决策问题和上次遗留。', files: [{ path: `matters/${prepareTodo.id}/artifacts/outline.md`, additions: 12, deletions: 0 }] },
    { id: 'merged-1', matterId: mergedTodo.id, kind: 'sandbox', repo: '/repo', branch: `calli/proactive/${mergedTodo.id}`, threadId: 'x3', startedAt: at(200), endedAt: at(190), status: 'merged', summary: '文档里的命令更新了。', files: [{ path: 'docs/a.md', additions: 1, deletions: 1 }] },
    { id: 'failed-1', matterId: failedTodo.id, kind: 'sandbox', repo: '/repo', threadId: 'x4', startedAt: at(300), endedAt: at(295), status: 'failed', summary: '测试没跑起来：缺少数据库。' },
  ];
  const requests = { searches: [], read(search) { this.searches.push(search); }, reads() { return this.searches.length; }, diffs: 0, actions: [] };
  return { sandboxTodo, prepareTodo, mergedTodo, failedTodo, runs, requests, failNext: {} };
}

/** Answers the proactive API from the scripted runs, so a page sees them move. */
export async function routes(page, { runs, requests, failNext, mode = 'auto' }) {
  const counts = predicate => runs.filter(predicate).reduce((sum, run) => ({ ...sum, [run.matterId]: (sum[run.matterId] ?? 0) + 1 }), {});
  await page.route(/\/api\/personal\/proactive(\?.*)?$/, route => {
    const url = new URL(route.request().url());
    const settings = { mode, enabled: mode !== 'off', repos: [] };
    if (url.searchParams.has('summary')) return route.fulfill({ json: { settings, counts: counts(run => run.status !== 'discarded'), asking: counts(run => run.status === 'asking') } });
    requests.read(url.search);
    return route.fulfill({ json: { settings, runs: url.searchParams.has('matterId') ? runs.filter(run => run.matterId === url.searchParams.get('matterId')) : runs } });
  });
  await page.route(/\/api\/personal\/proactive\/runs\/[^/]+\/diff$/, route => { requests.diffs++; return route.fulfill({ json: { files: [{ path: 'src/export.ts', additions: 10, deletions: 2 }, { path: 'test/export.test.ts', additions: 24, deletions: 0 }] } }); });
  await page.route(/\/api\/personal\/proactive\/runs\/[^/]+\/(merge|discard|revert|approve|answer|close-matter|dismiss|allow-repo)$/, route => {
    const [, id, action] = new URL(route.request().url()).pathname.match(/runs\/([^/]+)\/([\w-]+)$/);
    requests.actions.push(`${id}/${action}`);
    if (action === 'answer') (requests.answers ??= []).push(route.request().postDataJSON());
    if (failNext[action]) { const error = failNext[action]; delete failNext[action]; return route.fulfill({ status: 409, json: { error } }); }
    const run = runs.find(item => item.id === id);
    run.status = { merge: 'merged', discard: 'discarded', revert: 'reverted', approve: 'approved', answer: 'answered', 'close-matter': 'closed', dismiss: 'discarded', 'allow-repo': 'approved' }[action];
    return route.fulfill({ json: run });
  });
  // The counts belong with the plans; the real ones are all zero because the runs here are scripted.
  await page.route(/\/api\/todos$/, async route => {
    if (route.request().method() !== 'GET') return route.continue();
    // A refresh still in flight when a test closes its page must not fail the next test.
    try {
      const response = await route.fetch();
      const count = counts(run => run.status !== 'discarded');
      await route.fulfill({ response, json: (await response.json()).map(todo => ({ ...todo, proactiveRunCount: count[todo.id] ?? 0, done: todo.done || runs.some(run => run.matterId === todo.id && run.status === 'closed') })) });
    } catch (error) {
      if (!page.isClosed()) throw error;
    }
  });
}

export async function arrangeAdvance(daemon) {
  const todos = await Promise.all(['选择上线环境', '验收导出修复', '等待检查结果', '允许修复仓库'].map(text => post(daemon, text)));
  const base = { kind: 'advance', threadId: 'advance-thread', turnId: 'turn-1', startedAt: new Date().toISOString() };
  const runs = [
    { ...base, id: 'question', matterId: todos[0].id, status: 'asking', reason: 'user', question: { text: '先部署到预发还是生产？', options: ['先到预发', '直接生产'] } },
    { ...base, id: 'done', matterId: todos[1].id, status: 'asking', reason: 'done', line: '导出修复已通过全部测试' },
    { ...base, id: 'waiting', matterId: todos[2].id, status: 'waiting', reason: 'external', wait: { what: '持续集成检查', then: '通过后继续' }, wakeAt: '2026-11-11T12:30:00Z' },
    { ...base, id: 'permission', matterId: todos[3].id, kind: 'sandbox', status: 'observed', needsRepo: true, repo: '/workspace/project', summary: '允许在这个仓库的沙盒里继续修复吗？' },
  ];
  return { todos, runs, requests: { searches: [], read(search) { this.searches.push(search); }, reads() { return this.searches.length; }, diffs: 0, actions: [] }, failNext: {} };
}
