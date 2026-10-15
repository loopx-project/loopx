import assert from 'node:assert/strict'
import { test } from 'vitest'
import { createNativeContextTool, NATIVE_CONTEXT_TOOL } from '../src/native-operation-context.ts'

function fixture() {
  const agent = { id: 'sdk-session', session: { id: 'sdk-session' }, status: 'running',
    options: { provider: 'fixture-provider', model: 'fixture-model', reasoningEffort: 'high', apiKey: 'private-fixture' } }
  let live: object | undefined = agent
  let current: object | undefined
  const boundary = { lastTurn: 1, openTurnStartSeq: 0 as number | null }
  const host = {
    agents: { get: (_id: string) => live },
    tools: { register: () => {}, get: (_name: string, _scope: object) => current },
    sessionProjections: { stateOf: (_session: object, _key: 'turnBoundary') => boundary },
  }
  const tool = createNativeContextTool(host)
  current = tool
  const exec = { agent, signal: new AbortController().signal }
  return { agent, boundary, tool, exec, revokeAgent: () => { live = undefined }, revokeTool: () => { current = undefined } }
}

test('native context uses the live agent and open Turn; copies no credential fields', async () => {
  const f = fixture()
  const r = await f.tool.execute({}, f.exec)
  assert.equal(f.tool.name, NATIVE_CONTEXT_TOOL)
  assert.equal(r.session_id, 'sdk-session')
  assert.equal(r.native_turn_number, 1)
  assert.equal(r.turn_start_seq, 0)
  assert.equal(r.profile_source, 'agent_options')
  assert.deepEqual(r.profile, { provider: 'fixture-provider', model: 'fixture-model', reasoning_effort: 'high' })
  assert.equal(r.wire_profile_verified, false)
  assert.equal(r.execution_allowed, false)
  assert.ok(!JSON.stringify(r).includes('private-fixture'))
  f.boundary.lastTurn = 2
  f.agent.options.model = 'replacement-model'
  const next = await f.tool.execute({}, f.exec)
  assert.equal(next.native_turn_number, 2)
  assert.equal(next.profile.model, 'replacement-model')
})

test('caller-supplied identity and malformed arguments never select a session', async () => {
  const f = fixture()
  for (const args of [null, [], true, 1, { session_id: 'forged' }, { action: 'consume' }]) {
    await assert.rejects(f.tool.execute(args, f.exec), /no caller identity/)
  }
})

test('missing, idle, mismatched and replaced native callers are refused', async () => {
  for (const change of [
    (f: ReturnType<typeof fixture>) => { f.exec.agent = undefined as never },
    (f: ReturnType<typeof fixture>) => { f.agent.status = 'idle' },
    (f: ReturnType<typeof fixture>) => { f.agent.session.id = 'different-session' },
    (f: ReturnType<typeof fixture>) => { f.revokeAgent() },
    (f: ReturnType<typeof fixture>) => { f.revokeTool() },
  ]) {
    const f = fixture()
    change(f)
    await assert.rejects(f.tool.execute({}, f.exec), /current live/)
  }
})

test('closed and invalid native Turns cannot produce a context', async () => {
  const invalid: [number, number | null][] = [[0, 0], [1, null], [1, -1], [1.5, 0], [1, 0.5], [Number.MAX_SAFE_INTEGER + 1, 0]]
  for (const [turn, seq] of invalid) {
    const f = fixture()
    f.boundary.lastTurn = turn as number
    f.boundary.openTurnStartSeq = seq
    await assert.rejects(f.tool.execute({}, f.exec), /open native Turn/)
  }
})

test('cancelled calls stop before context readback', async () => {
  const f = fixture()
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(f.tool.execute({}, { ...f.exec, signal: controller.signal }), /aborted/)
})
