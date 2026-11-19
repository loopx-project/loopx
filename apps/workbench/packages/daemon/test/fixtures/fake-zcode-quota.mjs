import { createServer } from 'node:http';

export async function fakeZcodeQuota() {
  const state = { status: 200, requests: [], body: { code: 200, success: true, data: { limits: [
    { type: 'TOKENS_LIMIT', unit: 3, number: 5, percentage: 12 },
    { type: 'TOKENS_LIMIT', unit: 6, percentage: 98, nextResetTime: 1792029600000 },
  ] } } };
  const server = createServer((req, res) => {
    state.requests.push({ url: req.url, authorization: req.headers.authorization });
    res.writeHead(state.status, { 'content-type': 'application/json' });
    res.end(JSON.stringify(state.body));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { state, url: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); }) };
}
