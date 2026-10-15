import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer } from 'node:net'
import { join } from 'node:path'
import { test } from 'vitest'
import { createNativeOperationTool } from '../src/native-operation.ts'

function fixture(socketPath: string) {
  const agent = { id: 'owned-sdk', session: { id: 'owned-sdk' }, status: 'running',
    options: { provider: 'fixture', model: 'fixture', reasoningEffort: 'high' } }
  let current: object
  const host = { agents: { get: () => agent }, tools: { register: () => {}, get: () => current },
    sessionProjections: { stateOf: () => ({ lastTurn: 1, openTurnStartSeq: 4 }) } }
  const tool = createNativeOperationTool(host, { socketPath })
  current = tool
  return { tool, agent, exec: { agent, signal: new AbortController().signal, callId: 'sdk-call' },
    replace: () => { current = {} } }
}

test('native tool sends actual caller/call metadata and returns the canonical receipt on the same connection', async () => {
  const directory = await mkdtemp('/tmp/lx-dsh-test-')
  const path = join(directory, 'io.sock')
  let frame: unknown
  let connections = 0
  const server = createServer(socket => {
    connections++
    socket.once('data', data => {
      frame = JSON.parse(data.toString())
      socket.end(JSON.stringify({ ok: true, execution_allowed: false, status: 'already_consumed' }) + '\n')
    })
  })
  await new Promise<void>(resolve => server.listen(path, resolve))
  try {
    const f = fixture(path)
    const result = await f.tool.execute({ action: 'context' }, f.exec)
    assert.deepEqual(result, { ok: true, execution_allowed: false, status: 'already_consumed' })
    assert.equal(connections, 1)
    assert.deepEqual(frame, {
      schema_version: 'loopx_dsh_operation_call_v0', call_id: 'sdk-call', arguments: { action: 'context' },
      native: { schema_version: 'loopx_dsh_native_context_v0', session_id: 'owned-sdk', native_turn_number: 1,
        turn_start_seq: 4, profile_source: 'agent_options', profile: { provider: 'fixture', model: 'fixture', reasoning_effort: 'high' },
        wire_profile_verified: false, execution_allowed: false },
    })
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()))
    await rm(directory, { recursive: true, force: true })
  }
})

test('missing native call ID, cancelled and replaced tools refuse before connecting', async () => {
  const f = fixture('/tmp/does-not-exist-operation.sock')
  await assert.rejects(f.tool.execute({}, { agent: f.agent, signal: f.exec.signal }), /native call identity/)
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(f.tool.execute({}, { ...f.exec, signal: controller.signal }), /aborted/)
  f.replace()
  await assert.rejects(f.tool.execute({}, f.exec), /current live/)
})

test('disconnected transport fails without an automatic retry or execute receipt', async () => {
  const f = fixture('/tmp/does-not-exist-operation.sock')
  await assert.rejects(f.tool.execute({ action: 'consume' }, f.exec), /unavailable/)
  assert.throws(() => fixture('relative.sock'), /absolute operation socket/)
})
