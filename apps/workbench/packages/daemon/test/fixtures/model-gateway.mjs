import { createServer } from 'node:http';

// Anthropic-compatible discovery, including paging and temporary gateway failures, and a
// gateway's own quota report when `state.quotas` is set (404 otherwise, as a plain gateway answers).
export async function modelGateway() {
  const state = { models: [], status: 200, requests: [], pageSize: 2, quotas: null };
  const server = createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    state.requests.push({ path: req.url, authorization: req.headers.authorization, apiKey: req.headers['x-api-key'] });
    if (url.pathname === '/v1/magpie/quotas' && state.quotas) {
      res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ object: 'list', data: state.quotas }));
      return;
    }
    if (url.pathname !== '/v1/models') { res.writeHead(404).end(); return; }
    res.writeHead(state.status, { 'content-type': 'application/json' });
    if (state.status !== 200) { res.end(JSON.stringify({ error: { message: 'temporarily unavailable' } })); return; }
    const cursor = url.searchParams.get('after_id');
    const start = cursor ? state.models.findIndex(m => m.id === cursor) + 1 : 0;
    const data = state.models.slice(start, start + state.pageSize);
    res.end(JSON.stringify({ data, has_more: start + data.length < state.models.length, last_id: data.at(-1)?.id }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { state, url: `http://127.0.0.1:${server.address().port}`, close: () => new Promise(resolve => server.close(resolve)) };
}
