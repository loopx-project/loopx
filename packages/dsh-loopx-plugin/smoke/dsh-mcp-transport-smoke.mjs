#!/usr/bin/env node

import assert from 'node:assert/strict'
import { once } from 'node:events'
import { createServer } from 'node:http'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'

// Resolve through the qualified host, not a separate top-level test SDK.
const require = createRequire(import.meta.url)
const host = createRequire(require.resolve('@deepseek-ai/dsh/package.json'))
const mcp = createRequire(host.resolve('@deepseek-ai/dsh-mcp-client'))
const { StreamableHTTPClientTransport } = await import(
  pathToFileURL(mcp.resolve('@modelcontextprotocol/client')).href
)

let foreignRequests = 0
const accepted = []
const reply = (response) => {
  response.writeHead(200, { 'content-type': 'application/json' })
  response.end(JSON.stringify({ jsonrpc: '2.0', id: 1, result: {} }))
}
const foreign = createServer((request, response) => {
  foreignRequests += 1
  request.resume()
  reply(response)
})
const origin = createServer(async (request, response) => {
  if (request.url === '/mcp') {
    let body = ''
    for await (const chunk of request) body += chunk
    accepted.push({ header: request.headers['x-loopx-fixture'], body: JSON.parse(body) })
    reply(response)
    return
  }
  const [, kind, code] = request.url.split('/')
  request.resume()
  response.writeHead(Number(code), {
    location: kind === 'same' ? '/mcp' : `${foreignUrl}/mcp`,
  })
  response.end()
})

async function listen(server) {
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return `http://127.0.0.1:${server.address().port}`
}

let foreignUrl
try {
  foreignUrl = await listen(foreign)
  const originUrl = await listen(origin)
  for (const code of [307, 308]) {
    for (const kind of ['foreign', 'same']) {
      const messages = []
      const transport = new StreamableHTTPClientTransport(new URL(`${originUrl}/${kind}/${code}`), {
        requestInit: {
          headers: { 'x-loopx-fixture': 'synthetic' },
          signal: AbortSignal.timeout(5_000),
        },
      })
      transport.onmessage = (message) => messages.push(message)
      try {
        await transport.start()
        const send = () => transport.send({ jsonrpc: '2.0', id: 1, method: 'ping' })
        if (kind === 'foreign') {
          await assert.rejects(send, 'DSH must refuse cross-origin MCP redirects')
          assert.equal(foreignRequests, 0, 'no request may reach the other origin')
        } else {
          await send()
          assert.deepEqual(messages, [{ jsonrpc: '2.0', id: 1, result: {} }])
        }
      } finally {
        await transport.close()
      }
    }
  }
  assert.deepEqual(accepted, [307, 308].map(() => ({
    header: 'synthetic', body: { jsonrpc: '2.0', id: 1, method: 'ping' },
  })))
  process.stdout.write('dsh-loopx MCP transport smoke passed (cross-origin rejected, same-origin retained)\n')
} finally {
  for (const server of [origin, foreign]) {
    server.closeAllConnections()
    await new Promise((resolve, reject) => server.close((error) => {
      if (error && error.code !== 'ERR_SERVER_NOT_RUNNING') reject(error)
      else resolve()
    }))
  }
}
