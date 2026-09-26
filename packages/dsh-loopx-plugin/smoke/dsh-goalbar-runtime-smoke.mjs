#!/usr/bin/env node

import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { spawn, spawnSync } from 'node:child_process'
import { request as httpRequest } from 'node:http'
import {
  chmod,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rm,
  stat,
  writeFile,
} from 'node:fs/promises'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import vm from 'node:vm'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const packageId = 'dsh-loopx-plugin'
const dshBin = process.env.DSH_BIN || join(packageRoot, 'node_modules', '.bin', 'dsh')
const sessionId = 'runtime-session'
const goalId = 'runtime-goal'
const loopxAgentId = 'runtime-agent'
const requestVersion = 'loopx_goalbar_request_v2'
const approvedRequires = new Set([
  'react',
  'react/jsx-runtime',
  'react-dom',
  'react-dom/client',
  '@deepseek-ai/cordis',
  '@deepseek-ai/dsh-client-ui-primitives',
  '@deepseek-ai/dsh-client-ui-renderer/client',
  '@deepseek-ai/dsh-client-ui-slots',
])
const packedStaticEntries = new Set([
  'package/LICENSE',
  'package/NOTICE',
  'package/README.md',
  'package/cordis.patch.yml',
  'package/lib/client.js',
  'package/lib/driver.js',
  'package/lib/index.js',
  'package/lib/init-command.js',
  'package/lib/observer.js',
  'package/lib/types/cli.d.ts',
  'package/lib/types/client/LoopXGoalBar.d.ts',
  'package/lib/types/client/index.d.ts',
  'package/lib/types/client/locale.d.ts',
  'package/lib/types/client/rpc.d.ts',
  'package/lib/types/client/useGoalBar.d.ts',
  'package/lib/types/driver.d.ts',
  'package/lib/types/goalbar/connection-rpc.d.ts',
  'package/lib/types/goalbar/events.d.ts',
  'package/lib/types/goalbar/project-registry-wire.d.ts',
  'package/lib/types/goalbar/protocol.d.ts',
  'package/lib/types/goalbar/read-model.d.ts',
  'package/lib/types/goalbar/service.d.ts',
  'package/lib/types/index.d.ts',
  'package/lib/types/init-command.d.ts',
  'package/lib/types/managed-runtime.d.ts',
  'package/lib/types/observer.d.ts',
  'package/package.json',
])
const packedHashedEntries = [
  ['managed runtime chunk', /^package\/lib\/managed-runtime-[A-Za-z0-9_-]{8}\.js$/u],
  ['Driver chunk', /^package\/lib\/driver-[A-Za-z0-9_-]{8}\.js$/u],
]

function run(file, args, env = process.env) {
  const result = spawnSync(file, args, {
    cwd: packageRoot,
    env,
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
    shell: false,
    timeout: 30_000,
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw new Error(`${file} ${args.join(' ')} failed\n${result.stdout}\n${result.stderr}`)
  }
  return result.stdout
}

function parseArgs(argv) {
  const values = argv.filter(value => value !== '--')
  if (values.length === 0) return undefined
  if (values.length !== 2 || values[0] !== '--tarball') {
    throw new Error('usage: dsh-goalbar-runtime-smoke.mjs [--tarball PATH]')
  }
  return resolve(values[1])
}

function assertPackedArtifact(tarball) {
  const entries = run('tar', ['-tf', tarball]).trim().split('\n')
  assert.equal(new Set(entries).size, entries.length, 'packed runtime contains duplicate entries')
  for (const required of packedStaticEntries) {
    assert(entries.includes(required), `packed runtime missing ${required}`)
  }
  const hashedCounts = new Map(packedHashedEntries.map(([label]) => [label, 0]))
  for (const entry of entries) {
    if (packedStaticEntries.has(entry)) continue
    const match = packedHashedEntries.find(([, pattern]) => pattern.test(entry))
    assert(match, `packed runtime contains unapproved entry ${entry}`)
    hashedCounts.set(match[0], hashedCounts.get(match[0]) + 1)
  }
  for (const [label] of packedHashedEntries) {
    assert.equal(hashedCounts.get(label), 1, `packed runtime requires exactly one ${label}`)
  }
  assert.equal(entries.length, packedStaticEntries.size + packedHashedEntries.length)
}

function bindingPayload(mode) {
  if (mode === 'missing') {
    return {
      ok: true,
      schema_version: 'loopx_thread_agent_binding_resolution_v0',
      host_surface: 'deepseek-harness-native',
      thread_id: sessionId,
      status: 'missing',
      goal_id: null,
      agent_id: null,
      matches: [],
    }
  }
  if (mode === 'ambiguous') {
    return {
      ok: false,
      schema_version: 'loopx_thread_agent_binding_resolution_v0',
      host_surface: 'deepseek-harness-native',
      thread_id: sessionId,
      status: 'ambiguous',
      goal_id: null,
      agent_id: null,
      error_kind: 'thread_agent_binding_ambiguous',
      matches: [
        { goal_id: goalId, agent_id: loopxAgentId },
        { goal_id: 'other-goal', agent_id: 'other-agent' },
      ],
    }
  }
  return {
    ok: true,
    schema_version: 'loopx_thread_agent_binding_resolution_v0',
    host_surface: 'deepseek-harness-native',
    thread_id: sessionId,
    status: 'bound',
    goal_id: goalId,
    agent_id: loopxAgentId,
    matches: [{ goal_id: goalId, agent_id: loopxAgentId }],
  }
}

function activationPayload(state) {
  const active = state === 'active'
  return {
    ok: true,
    schema_version: 'loopx_goal_activation_transition_v1',
    dry_run: true,
    execute: false,
    goal_id: goalId,
    before_state: state,
    after_state: 'stopped',
    changed: active,
    written: false,
    partial_write: false,
    readback: {
      schema_version: 'loopx_goal_activation_readback_v1',
      status: active ? 'not_executed' : 'not_required',
      verified: !active,
    },
  }
}

function todoPayload(progress) {
  const item = { status: 'open', text: 'sentinel-must-not-cross-wire' }
  return {
    ok: true,
    read_only: true,
    command: 'list',
    goal_id: goalId,
    agent_id_filter: loopxAgentId,
    role: 'agent',
    explicit_limit: 1,
    todo_count: 1,
    returned_todo_count: 1,
    todos: [item],
    todo_list_projection: {
      schema_version: 'agent_lane_todo_list_projection_v0',
      view: 'explicit_limit_cold_path',
      item_limit_per_role: 1,
      counts_cover_full_match: true,
      matched_todo_count: progress.total,
      returned_todo_count: 1,
    },
    agent_todos: {
      done_count: progress.processed,
      open_count: progress.remaining,
      total_count: progress.total,
      items: [item],
    },
  }
}

function executionPayload(target) {
  return {
    ok: true,
    schema_version: 'loopx_goal_activation_transition_v1',
    dry_run: false,
    execute: true,
    goal_id: goalId,
    before_state: target,
    after_state: target,
    changed: false,
    written: false,
    partial_write: false,
    readback: {
      schema_version: 'loopx_goal_activation_readback_v1',
      status: 'verified',
      verified: true,
      goal_id: goalId,
      expected_state: target,
      source_state: target,
      target_state: target,
    },
  }
}

function latestSessionEventSeq(session) {
  return [...session.events].reverse().find(event => Number.isSafeInteger(event.seq))?.seq ?? null
}

function latestCandidateSeq(session) {
  return [...session.events].reverse().find(
    event => event.type === 'step/end' || event.type === 'turn/end',
  )?.seq ?? null
}

function coordinatorHarness() {
  const waiters = new Set()
  let activateCalls = 0
  let cancelCalls = 0
  let abortedWaits = 0
  return {
    openWatchService() {
      let disposed = false
      return {
        waitForSessionChange(session, afterSessionEventSeq, options) {
          const latest = latestCandidateSeq(session)
          if (latest !== null
            && (afterSessionEventSeq === null || latest > afterSessionEventSeq)) {
            return Promise.resolve({
              kind: 'candidate', sessionId: String(session.id), sessionEventSeq: latest,
            })
          }
          if (options.getAgentStatus() !== options.observedAgentStatus) {
            return Promise.resolve({
              kind: 'runtime_changed',
              sessionId: String(session.id),
              agentStatus: options.getAgentStatus(),
            })
          }
          return new Promise(resolve => {
            let settled = false
            const finish = receipt => {
              if (settled) return
              settled = true
              clearTimeout(timer)
              options.signal.removeEventListener('abort', aborted)
              waiters.delete(waiter)
              resolve(receipt)
            }
            const aborted = () => {
              abortedWaits += 1
              finish({ kind: 'aborted' })
            }
            const timer = setTimeout(() => finish(
              options.isSessionCurrent()
                ? {
                    kind: 'timeout',
                    sessionEventSeq: latestSessionEventSeq(session) ?? afterSessionEventSeq,
                  }
                : { kind: 'session_unavailable' },
            ), options.timeoutMs)
            const waiter = { session, afterSessionEventSeq, finish, options }
            waiters.add(waiter)
            options.signal.addEventListener('abort', aborted, { once: true })
            if (disposed) finish({ kind: 'service_disposed' })
            else if (options.signal.aborted) aborted()
          })
        },
        dispose() {
          disposed = true
          for (const waiter of [...waiters]) waiter.finish({ kind: 'service_disposed' })
        },
      }
    },
    publishCandidate(session, type, seq) {
      const event = {
        type,
        seq,
        time: 1,
        data: type === 'step/end'
          ? { turn: 0, step: 0 }
          : { turn: 0, reason: { kind: 'completed' } },
      }
      session.events.push(event)
      for (const waiter of [...waiters]) {
        if (waiter.session !== session
          || (waiter.afterSessionEventSeq !== null && seq <= waiter.afterSessionEventSeq)) continue
        waiter.finish(waiter.options.isSessionCurrent()
          ? { kind: 'candidate', sessionId: String(session.id), sessionEventSeq: seq }
          : { kind: 'session_unavailable' })
      }
    },
    publishAgentStatus(session, agentStatus) {
      for (const waiter of [...waiters]) {
        if (waiter.session !== session || waiter.options.observedAgentStatus === agentStatus) continue
        waiter.finish(waiter.options.isSessionCurrent()
          ? { kind: 'runtime_changed', sessionId: String(session.id), agentStatus }
          : { kind: 'session_unavailable' })
      }
    },
    async evaluateActivatedSession() {
      activateCalls += 1
      return { kind: 'applied' }
    },
    async cancelQueued() {
      cancelCalls += 1
      return { kind: 'applied' }
    },
    counts: () => ({ activateCalls, cancelCalls, waiters: waiters.size }),
    abortedWaits: () => abortedWaits,
  }
}

async function openPackedConnection(host, service) {
  const harnessRequire = createRequire(join(packageRoot, 'package.json'))
  const dshRequire = createRequire(harnessRequire.resolve('@deepseek-ai/dsh/package.json'))
  const appRequire = createRequire(dshRequire.resolve('@deepseek-ai/dsh-web-app/package.json'))
  const [{ Context }, { HostConnectionService }, { WebServer }] = await Promise.all([
    import(pathToFileURL(harnessRequire.resolve('@deepseek-ai/cordis')).href),
    import(pathToFileURL(harnessRequire.resolve('@deepseek-ai/dsh-client-connection')).href),
    import(pathToFileURL(appRequire.resolve('@deepseek-ai/dsh-host-webserver')).href),
  ])
  const ctx = new Context()
  await ctx.plugin(WebServer, { host: '127.0.0.1', port: 0 })
  // 0.1.5 constructs the service as `(ctx, trustedHosts, browserAuth)`. The
  // deployment's Connection plugin owns the concrete browser-auth face and is
  // gated on a `credentials` service, so this carrier probe supplies its own
  // permissive face to isolate the host-half state machine; the real fence is
  // asserted by the real-profile phase against the installed application.
  const connection = new HostConnectionService(ctx, [], {
    isAuthenticated: () => true,
    authorizeIndex: () => true,
    authenticatedUrl: url => url,
  })
  const disposeRpc = host.registerGoalBarConnectionRpc(connection, service)
  return {
    baseUrl: `http://127.0.0.1:${String(ctx.webServer.port)}`,
    // This phase registers the standalone authenticated `/loopx` channel
    // explicitly, so its client must address that channel. The shared
    // `/api/loopx.goalbar` route is owned by the real-profile phase, which goes
    // through the installed `registerGoalBarConnectionTransport` dispatcher.
    sharedApi: false,
    disposeRpc,
    close: () => ctx.fiber.dispose(),
  }
}

async function waitUntil(predicate, message) {
  const deadline = Date.now() + 2_000
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(message)
    await new Promise(resolveWait => setTimeout(resolveWait, 5))
  }
}

async function exercisePackedService(installed) {
  const installedRequire = createRequire(join(installed, 'package.json'))
  const host = await import(pathToFileURL(installedRequire.resolve(packageId)).href)
  const events = []
  let status = 'idle'
  const session = {
    id: sessionId,
    header: { version: 0, id: sessionId, createdAt: 1, cwd: installed },
    // The fixture owns the log: `events` stays its mutable store for the
    // harness, and `snapshotEvents()` is the installed-generation reader.
    events,
    snapshotEvents: () => events,
    surface: { nodes: [] },
  }
  const agent = {
    id: sessionId,
    session,
    get status() { return status },
  }
  let currentAgent = agent
  let binding = 'bound'
  let activation = 'stopped'
  const progress = { processed: 3, remaining: 2, total: 5 }
  let replaceAfterBinding = false
  const calls = []
  const coordinator = coordinatorHarness()
  const runner = async (_file, args) => {
    calls.push([...args])
    if (args.includes('resolve-agent-thread')) {
      const payload = bindingPayload(binding)
      if (replaceAfterBinding) currentAgent = { ...agent, session: { ...session } }
      return { exitCode: binding === 'ambiguous' ? 2 : 0, stdout: JSON.stringify(payload), stderr: '' }
    }
    if (args.includes('goal-lifecycle') && args.includes('--execute')) {
      const operation = args[args.indexOf('--operation') + 1]
      activation = operation === 'resume' ? 'active' : 'stopped'
      return { exitCode: 0, stdout: JSON.stringify(executionPayload(activation)), stderr: '' }
    }
    if (args.includes('goal-lifecycle')) {
      return { exitCode: 0, stdout: JSON.stringify(activationPayload(activation)), stderr: '' }
    }
    if (args.includes('todo')) {
      return { exitCode: 0, stdout: JSON.stringify(todoPayload(progress)), stderr: '' }
    }
    throw new Error('unexpected GoalBar runner argv')
  }
  const service = host.createGoalBarService({
    getAgent: id => id === sessionId ? currentAgent : undefined,
    coordinator,
    command: { file: 'loopx', prefix: [], skillCommand: 'loopx', version: 'loopx smoke' },
    runner,
    retryDelaysMs: [0, 0],
    watchTimeoutMs: 50,
  })
  const connection = await openPackedConnection(host, service)
  const call = async (endpoint, payload, signal) => {
    const result = await rpc(
      connection.baseUrl,
      endpoint,
      payload,
      {},
      signal,
      connection.sharedApi,
    )
    assert.equal(result.response.status, 200)
    assert.equal(result.body.rpcId, result.rpcId)
    return result.body.result
  }
  const read = async () => (await call(
    'goalbar/read',
    { v: requestVersion, op: 'read', sessionId },
  )).value.result

  let rpcDisposed = false
  try {
  assert.equal(calls.length, 0, 'idle service construction invoked LoopX')
  binding = 'missing'
  let beforeRead = calls.length
  const hidden = await read()
  assert.equal(hidden.kind, 'hidden')
  assert.equal(hidden.reason, 'binding_missing')
  assert.equal(hidden.baseSessionEventSeq, null)
  assert.match(hidden.sourceRevision, /^sha256:[0-9a-f]{64}$/u)
  assert.equal(calls.length, beforeRead + 1, 'missing binding triggered downstream reads')

  const hiddenRuntimeCalls = calls.length
  const hiddenRuntime = await call('goalbar/watch', {
    v: requestVersion,
    op: 'watch',
    sessionId,
    afterSessionEventSeq: hidden.baseSessionEventSeq,
    sourceRevision: hidden.sourceRevision,
    expected: null,
    agentStatus: null,
  })
  assert.deepEqual(hiddenRuntime.value.result, {
    kind: 'runtime_changed', sessionEventSeq: null, agentStatus: 'idle',
  })
  assert.equal(calls.length, hiddenRuntimeCalls, 'hidden runtime update invoked LoopX')

  const midTurnWatch = call('goalbar/watch', {
    v: requestVersion,
    op: 'watch',
    sessionId,
    afterSessionEventSeq: hidden.baseSessionEventSeq,
    sourceRevision: hidden.sourceRevision,
    expected: null,
    agentStatus: 'idle',
  })
  await waitUntil(
    () => coordinator.counts().waiters === 1,
    'real Connection mid-turn watch never became pending',
  )
  await mkdir(join(installed, '.loopx'), { recursive: true })
  await writeFile(join(installed, '.loopx', 'registry.json'), '{"goals":["runtime-goal"]}')
  binding = 'bound'
  coordinator.publishCandidate(session, 'step/end', 7)
  assert.deepEqual((await midTurnWatch).value.result, {
    kind: 'source_changed', sessionEventSeq: 7,
  })
  assert.equal(calls.length, hiddenRuntimeCalls, 'mid-turn source watch invoked LoopX')

  const present = await read()
  assert.equal(present.kind, 'present')
  assert.equal(present.baseSessionEventSeq, 7)
  assert.deepEqual(present.snapshot.progress, progress)
  assert(!JSON.stringify(present).includes('sentinel-must-not-cross-wire'))

  const runtimeCalls = calls.length
  const runtimeWatch = call('goalbar/watch', {
    v: requestVersion,
    op: 'watch',
    sessionId,
    afterSessionEventSeq: present.baseSessionEventSeq,
    sourceRevision: present.sourceRevision,
    expected: { goalId, loopxAgentId },
    agentStatus: 'idle',
  })
  await waitUntil(
    () => coordinator.counts().waiters === 1,
    'real Connection runtime-only watch never became pending',
  )
  status = 'running'
  coordinator.publishAgentStatus(session, status)
  assert.deepEqual((await runtimeWatch).value.result, {
    kind: 'runtime_changed', sessionEventSeq: 7, agentStatus: 'running',
  })
  assert.equal(calls.length, runtimeCalls, 'runtime-only watch invoked LoopX')

  const externalCalls = calls.length
  const externalWatch = call('goalbar/watch', {
    v: requestVersion,
    op: 'watch',
    sessionId,
    afterSessionEventSeq: present.baseSessionEventSeq,
    sourceRevision: present.sourceRevision,
    expected: { goalId, loopxAgentId },
    agentStatus: 'running',
  })
  await mkdir(join(installed, '.loopx', 'goals', goalId), { recursive: true })
  await writeFile(
    join(installed, '.loopx', 'goals', goalId, 'ACTIVE_GOAL_STATE.md'),
    'runtime smoke state revision\n',
  )
  assert.deepEqual((await externalWatch).value.result, {
    kind: 'source_changed', sessionEventSeq: 7,
  })
  assert.equal(calls.length, externalCalls, 'lease revision reconciliation invoked LoopX')

  binding = 'ambiguous'
  beforeRead = calls.length
  assert.equal((await read()).reason, 'binding_ambiguous')
  assert.equal(calls.length, beforeRead + 1, 'ambiguous binding triggered downstream reads')
  binding = 'bound'
  const current = await read()
  assert.deepEqual(current.snapshot.progress, progress)

  const abortedBefore = coordinator.abortedWaits()
  const cancelledWatch = disconnectableRpc(
    connection.baseUrl,
    'goalbar/watch',
    {
      v: requestVersion,
      op: 'watch',
      sessionId,
      afterSessionEventSeq: current.baseSessionEventSeq,
      sourceRevision: current.sourceRevision,
      expected: { goalId, loopxAgentId },
      agentStatus: 'running',
    },
  )
  const disconnected = cancelledWatch.completion.then(
    () => { throw new Error('disconnected real Connection watch unexpectedly completed') },
    error => error,
  )
  await waitUntil(
    () => coordinator.counts().waiters === 1,
    'real Connection watch never became pending',
  )
  const disconnectError = cancelledWatch.disconnect()
  assert.equal(await disconnected, disconnectError)
  await waitUntil(
    () => coordinator.abortedWaits() === abortedBefore + 1,
    'real Connection disconnect did not reach the exact Host watch signal',
  )
  assert.equal(
    coordinator.abortedWaits(),
    abortedBefore + 1,
    'real Connection disconnect did not reach the exact Host watch signal',
  )
  await waitUntil(
    () => coordinator.counts().waiters === 0,
    'real Connection cancellation retained the Host waiter',
  )

  const expected = { goalId, loopxAgentId }
  status = 'idle'
  const started = await call('goalbar/start', {
    v: requestVersion, op: 'start', sessionId, expected,
  })
  assert.equal(started.value.result.kind, 'succeeded')
  assert.equal(started.value.result.snapshot.goalActivation, 'active')
  status = 'running'
  const paused = await call('goalbar/pause', {
    v: requestVersion, op: 'pause', sessionId, expected,
  })
  assert.equal(paused.value.result.kind, 'succeeded')
  assert.equal(paused.value.result.snapshot.goalActivation, 'stopped')
  assert.deepEqual(coordinator.counts(), { activateCalls: 1, cancelCalls: 1, waiters: 0 })
  assert(calls.some(args => args.includes('--execute') && args.includes('resume')))
  assert(calls.some(args => args.includes('--execute') && args.includes('stop')))

  status = 'idle'
  replaceAfterBinding = true
  const replaced = await read()
  assert.equal(replaced.kind, 'fault')
  assert.equal(replaced.code, 'session_unavailable')
  assert.equal(replaced.baseSessionEventSeq, 7)
  assert.match(replaced.sourceRevision, /^sha256:[0-9a-f]{64}$/u)
  replaceAfterBinding = false
  currentAgent = agent
  await connection.disposeRpc()
  rpcDisposed = true
  const removed = await rpc(connection.baseUrl, 'goalbar/read', {
    v: requestVersion, op: 'read', sessionId,
  })
  assert.equal(removed.response.status, 404, 'disposed real Connection handler remained reachable')
  } finally {
    if (!rpcDisposed) {
      await Promise.resolve(connection.disposeRpc()).catch(() => {})
    }
    await service.dispose()
    await connection.close()
  }
}

function createStyleDocument() {
  const styles = []
  const document = {
    createElement(name) {
      assert.equal(name, 'style')
      const style = {
        dataset: {},
        textContent: '',
        getAttribute(key) {
          return key === 'data-plugin' ? style.dataset.plugin ?? null
            : key === 'data-plugin-css' ? style.dataset.pluginCss ?? null : null
        },
        setAttribute(key, value) {
          if (key === 'data-plugin') style.dataset.plugin = value
          if (key === 'data-plugin-css') style.dataset.pluginCss = value
        },
        remove() {
          const at = styles.indexOf(style)
          if (at >= 0) styles.splice(at, 1)
        },
      }
      return style
    },
    querySelector(selector) {
      const match = selector.match(/^style\[data-plugin-css=(.+)\]$/u)
      return match ? styles.find(style => style.dataset.pluginCss === JSON.parse(match[1])) ?? null : null
    },
    querySelectorAll(selector) {
      if (selector === 'style[data-plugin]') return styles.filter(style => style.dataset.plugin)
      return []
    },
    head: { appendChild: style => styles.push(style) },
  }
  return { document, styles }
}

async function createClientModuleSystem(context, staticModules) {
  const localRequire = createRequire(join(packageRoot, 'package.json'))
  const dshRequire = createRequire(localRequire.resolve('@deepseek-ai/dsh/package.json'))
  const appRequire = createRequire(dshRequire.resolve('@deepseek-ai/dsh-web-app/package.json'))
  const sourcePath = appRequire.resolve('@deepseek-ai/dsh-client-modules/client')
  let loader
  const registrationTarget = {
    mode: 'queue',
    pendingQueue: [],
    load: value => { loader = value },
  }
  context.__ModuleLoader__ = registrationTarget
  vm.runInContext(await readFile(sourcePath, 'utf8'), context, { filename: sourcePath })
  const exports = loader.factory(() => { throw new Error('unexpected module-system dependency') })
  if (typeof exports.createClientModuleSystem === 'function') {
    return exports.createClientModuleSystem(
      registrationTarget,
      { id: loader.id, exports },
      {
        boot: {
          rev: 'dsh-loopx-plugin-runtime-smoke',
          entries: [{
            id: packageId,
            url: `/plugins/${packageId}/client.js`,
            rev: 'dsh-loopx-plugin-runtime-smoke',
            external: [],
          }],
          // 0.1.5 requires every entry to belong to exactly one initial-load
          // batch, so the synthetic manifest carries the application batch the
          // real host composes around this single row.
          batches: [{
            phase: 'application',
            url: `/plugins/${packageId}/client.js`,
            rev: 'dsh-loopx-plugin-runtime-smoke',
            entries: [packageId],
          }],
        },
        staticModules,
      },
    )
  }
  delete context.__ModuleLoader__
  return new exports.ClientModuleSystem({ modules: [], staticModules })
}

async function materializeServedClient(source) {
  const { document, styles } = createStyleDocument()
  const context = vm.createContext({ document })
  context.window = context
  const staticModules = Object.fromEntries([...approvedRequires].map(id => [id, Object.freeze({})]))
  const modules = await createClientModuleSystem(context, staticModules)
  vm.runInContext(source, context, { filename: 'served-client.js' })
  const client = await modules.import(packageId, '', {})
  assert.equal(typeof client.apply, 'function')
  assert.deepEqual([...client.inject], ['connection', 'locale', 'slots'])

  const applyClient = () => {
    const effects = []
    const slots = new Map([
      ['goal', { order: 10 }],
      ['queue', { order: 20 }],
    ])
    const ctx = {
      connection: { rpc: { call: async () => { throw new Error('no component mounted') } } },
      effect(execute) {
        const dispose = execute()
        if (typeof dispose === 'function') effects.push(dispose)
      },
      locale: { register: () => () => undefined },
      slots: {
        inject(name, install) {
          assert.equal(name, 'conversation.input.dock')
          const dispose = install()
          if (typeof dispose === 'function') effects.push(dispose)
        },
        register(config, component) {
          assert.equal(typeof component, 'function')
          slots.set(config.id, config)
          return () => slots.delete(config.id)
        },
      },
      on: () => () => undefined,
    }
    client.apply(ctx)
    const config = slots.get('loopx-goal')
    assert.equal(config.order, 15)
    assert.deepEqual([...slots].sort((a, b) => a[1].order - b[1].order).map(([id]) => id), [
      'goal', 'loopx-goal', 'queue',
    ])
    assert.equal(config.inject('session-one').rpcSessionId, 'session-one')
    assert.equal(config.inject('session-two').rpcSessionId, 'session-two')
    return {
      slots,
      dispose: () => { for (const dispose of effects.splice(0).reverse()) dispose() },
    }
  }

  const first = applyClient()
  assert.equal(styles.length, 1)
  first.dispose()
  assert.deepEqual([...first.slots.keys()], ['goal', 'queue'])
  assert.equal(styles.length, 0)
  assert.equal(await modules.import(packageId, '', {}), client)
  const reapplied = applyClient()
  assert.equal(styles.length, 1, 'cached Client reapply did not restore CSS')
  reapplied.dispose()
  assert.equal(styles.length, 0)
}

async function waitForWebUrl(child, output) {
  return await new Promise((resolveUrl, reject) => {
    const timeout = setTimeout(() => reject(new Error(
      `DSH URL timeout\n${redactWebOutput(output.text)}`,
    )), 20_000)
    const inspect = chunk => {
      output.text += chunk.toString()
      const match = output.text.match(
        /dsh web: (http:\/\/127\.0\.0\.1:\d+(?:\/\?[A-Za-z0-9_-]+=[A-Za-z0-9_-]+)?)/u,
      )
      if (match) {
        clearTimeout(timeout)
        resolveUrl(match[1])
      }
    }
    child.stdout.on('data', inspect)
    child.stderr.on('data', inspect)
    child.once('exit', (code, signal) => {
      clearTimeout(timeout)
      reject(new Error(
        `DSH exited before URL (code=${code}, signal=${signal})\n${redactWebOutput(output.text)}`,
      ))
    })
  })
}

function redactWebOutput(value) {
  return value.replace(/([?&][A-Za-z0-9_-]+=)[A-Za-z0-9_-]+/gu, '$1<redacted>')
}

async function openAuthenticatedWeb(launchUrl) {
  const url = new URL(launchUrl)
  if (!url.searchParams.has('token')) {
    return {
      baseUrl: url.origin,
      headers: {},
      index: await fetch(url, { signal: AbortSignal.timeout(5_000) }),
    }
  }
  const exchange = await fetch(url, {
    redirect: 'manual',
    signal: AbortSignal.timeout(5_000),
  })
  assert.equal(exchange.status, 303)
  const cookie = exchange.headers.get('set-cookie')?.split(';', 1)[0]
  assert(cookie, 'DSH launch-token exchange omitted its browser session cookie')
  const cleanUrl = new URL(exchange.headers.get('location') ?? '/', url)
  const headers = { cookie }
  return {
    baseUrl: cleanUrl.origin,
    headers,
    index: await fetch(cleanUrl, {
      headers,
      signal: AbortSignal.timeout(5_000),
    }),
  }
}

async function stopChild(child) {
  if (child.exitCode !== null || child.signalCode !== null) return
  const closed = new Promise(resolveClose => child.once('close', resolveClose))
  child.kill('SIGINT')
  const graceful = await Promise.race([
    closed.then(() => true),
    new Promise(resolveTimeout => setTimeout(() => resolveTimeout(false), 8_000)),
  ])
  if (graceful) return
  child.kill('SIGTERM')
  const terminated = await Promise.race([
    closed.then(() => true),
    new Promise(resolveTimeout => setTimeout(() => resolveTimeout(false), 2_000)),
  ])
  if (!terminated) child.kill('SIGKILL')
  await closed
}

async function rpc(
  baseUrl,
  endpoint,
  payload,
  extraHeaders = {},
  signal,
  sharedApi = false,
) {
  const rpcId = randomUUID()
  const requestSignal = signal ?? AbortSignal.timeout(5_000)
  const wireEndpoint = sharedApi ? 'loopx.goalbar' : endpoint
  const channel = sharedApi ? 'api' : 'loopx'
  const response = await fetch(`${baseUrl}/${channel}/${wireEndpoint}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...extraHeaders },
    body: JSON.stringify({ type: 'client-request', rpcId, method: wireEndpoint, payload }),
    signal: requestSignal,
  })
  const body = response.headers.get('content-type')?.includes('json')
    ? await response.json()
    : await response.text()
  return { response, body, rpcId }
}

async function hostRpc(baseUrl, method, args, extraHeaders = {}) {
  const rpcId = randomUUID()
  const response = await fetch(`${baseUrl}/api/${method}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...extraHeaders },
    // The 0.1.5 gateway accepts exactly one plain-object `args` field as the
    // Remote-method payload instead of the bare request object.
    body: JSON.stringify({ type: 'client-request', rpcId, method, payload: { args } }),
    signal: AbortSignal.timeout(5_000),
  })
  assert.equal(response.status, 200)
  const body = await response.json()
  assert.equal(body.rpcId, rpcId)
  assert.equal(body.result.ok, true, JSON.stringify(body.result.error))
  return body.result.value
}

function disconnectableRpc(baseUrl, endpoint, payload) {
  const body = Buffer.from(JSON.stringify({
    type: 'client-request',
    rpcId: randomUUID(),
    method: endpoint,
    payload,
  }))
  let request
  const completion = new Promise((resolveRequest, reject) => {
    request = httpRequest(new URL(`/loopx/${endpoint}`, baseUrl), {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'content-length': String(body.byteLength),
      },
    }, response => {
      response.resume()
      response.once('end', resolveRequest)
    })
    request.once('error', reject)
    request.setTimeout(5_000, () => request.destroy(
      new Error('real Connection watch disconnect timed out'),
    ))
    request.end(body)
  })
  return {
    completion,
    disconnect() {
      const error = new Error('intentional real Connection watch disconnect')
      request.destroy(error)
      return error
    },
  }
}

async function exerciseRealDshWeb(
  home,
  env,
  cliLog,
  skipSessionFixture,
  sharedApi,
) {
  const output = { text: '' }
  const child = spawn(dshBin, ['--profile', 'web', '--port', '0', '--no-open'], {
    cwd: packageRoot,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let baseUrl
  try {
    const launchUrl = await waitForWebUrl(child, output)
    const authenticated = await openAuthenticatedWeb(launchUrl)
    baseUrl = authenticated.baseUrl
    const index = authenticated.index
    const authHeaders = authenticated.headers
    assert.equal(index.status, 200)
    const html = await index.text()
    const bootText = /(?:window\.__DSH_BOOT__|globalThis\["__DSH_BOOT__"\])\s*=\s*(\{.*?\})<\/script>/su
      .exec(html)?.[1]
    assert(bootText, 'real DSH index omitted the boot manifest')
    const boot = JSON.parse(bootText)
    const row = boot.entries.find(entry => entry.id === packageId)
    assert(row, 'real DSH boot graph omitted dsh-loopx-plugin')
    assert.deepEqual(row.inject, [
      '@deepseek-ai/dsh-client-connection',
      '@deepseek-ai/dsh-client-locale',
      '@deepseek-ai/dsh-client-ui-conversation',
      '@deepseek-ai/dsh-client-ui-renderer',
    ])
    const bundle = await fetch(new URL(row.url, baseUrl), {
      headers: authHeaders,
      signal: AbortSignal.timeout(5_000),
    })
    assert.equal(bundle.status, 200)
    const clientSource = await bundle.text()
    assert(clientSource.includes('id: "dsh-loopx-plugin"'))
    await materializeServedClient(clientSource)

    const sessionId = 'loopx-bootstrap-readback'
    const initializationLog = await readFile(cliLog, 'utf8').catch(() => '(no LoopX calls)')
    assert(
      await stat(join(env.DSH_AGENTS_HOME, 'skills', 'loopx', 'SKILL.md'))
        .then(() => true, () => false),
      `automatic initialization did not create the isolated loopx skill: ${initializationLog}\n${redactWebOutput(output.text)}`,
    )
    if (!skipSessionFixture) {
      // 0.1.5 namespaces Remote endpoints as `<namespace>/<method>` and names
      // the wire arguments after the Host method's own parameter, so the
      // session and skill hosts are addressed by namespace and `request`.
      await hostRpc(baseUrl, 'session/create', {
        request: { sessionId, cwd: packageRoot },
      }, authHeaders)
      const skillCatalog = await hostRpc(baseUrl, 'skills/list', {
        request: { sessionId },
      }, authHeaders)
      assert(
        skillCatalog.skills.some(skill => skill.name === 'loopx'),
        `automatic initialization did not expose the loopx skill before DSH readiness: ${JSON.stringify(skillCatalog)}\n${redactWebOutput(output.text)}`,
      )
    }

    const startupCalls = (await readFile(cliLog, 'utf8')).trim().split('\n')
    assert.equal(startupCalls.length, 4, `unexpected automatic initialization calls: ${startupCalls.join(' | ')}`)
    assert(startupCalls[0].endsWith('--version'))
    assert(startupCalls[1].includes('workflow-skills'))
    assert(startupCalls[2].includes('--install'))
    assert(startupCalls[3].includes('workflow-skills'))

    const malformed = await rpc(
      baseUrl,
      'goalbar/read',
      { reflected: 'must-not-return' },
      authHeaders,
      undefined,
      sharedApi,
    )
    assert.equal(
      malformed.response.status,
      200,
      `GoalBar route unavailable: ${JSON.stringify(malformed.body)}\n${redactWebOutput(output.text)}`,
    )
    assert.equal(malformed.body.rpcId, malformed.rpcId)
    assert.equal(malformed.body.result.ok, false)
    assert.equal(malformed.body.result.error.code, 'bad-request')
    assert(!JSON.stringify(malformed.body).includes('must-not-return'))

    const read = await rpc(baseUrl, 'goalbar/read', {
      v: requestVersion, op: 'read', sessionId: 'runtime-no-agent',
    }, authHeaders, undefined, sharedApi)
    const unavailable = read.body.result.value.result
    assert.equal(unavailable.kind, 'fault')
    assert.equal(unavailable.code, 'session_unavailable')
    assert.equal(unavailable.baseSessionEventSeq, null)
    assert.match(unavailable.sourceRevision, /^sha256:[0-9a-f]{64}$/u)

    const rejected = await rpc(baseUrl, 'goalbar/read', {
      v: requestVersion, op: 'read', sessionId: 'runtime-no-agent',
    }, { ...authHeaders, origin: 'http://non-loopback.invalid' }, undefined, sharedApi)
    assert.equal(rejected.response.status, 403)
    assert.equal(rejected.body, 'forbidden')
    await new Promise(resolveWait => setTimeout(resolveWait, 150))
    const idleCalls = (await readFile(cliLog, 'utf8')).trim().split('\n')
    assert.deepEqual(idleCalls, startupCalls, 'idle real DSH runtime invoked LoopX after bootstrap')
  } finally {
    await stopChild(child)
  }
  await assert.rejects(fetch(baseUrl, { signal: AbortSignal.timeout(1_000) }))
  assert(output.text.includes('dsh web:'), 'real DSH runtime never became ready')
  assert((await stat(join(home, 'profiles', 'web')).then(() => true, () => false)))
}

async function main() {
  const suppliedTarball = parseArgs(process.argv.slice(2))
  const dshVersion = run(dshBin, ['--version']).trim()
  const expectedDshVersion = process.env.DSH_EXPECTED_VERSION
  if (expectedDshVersion) assert.equal(dshVersion, expectedDshVersion)
  const skipSessionFixture = process.env.DSH_RUNTIME_SKIP_SESSION_FIXTURE === '1'
  const temp = await mkdtemp(join(tmpdir(), 'dsh-loopx-goalbar-runtime-'))
  const home = join(temp, 'dsh-home')
  const tarball = suppliedTarball ?? join(temp, 'dsh-loopx-plugin.tgz')
  const cliLog = join(temp, 'loopx-invocations.log')
  const fakeLoopX = join(temp, 'loopx-bootstrap-probe')
  let installed
  try {
    if (suppliedTarball === undefined) {
      run('pnpm', ['--dir', packageRoot, 'pack', '--out', tarball])
    }
    assert((await stat(tarball)).isFile(), 'packed artifact is unavailable')
    assertPackedArtifact(tarball)
    await writeFile(fakeLoopX, String.raw`#!/usr/bin/env sh
set -eu
printf '%s\n' "$*" >> "${cliLog}"
case " $* " in
  *" --version "*)
    printf '%s\n' 'loopx smoke'
    exit 0
    ;;
esac
skills_dir=''
previous=''
for argument in "$@"; do
  if [ "$previous" = '--skills-dir' ]; then skills_dir="$argument"; fi
  previous="$argument"
done
case " $* " in
  *" workflow-skills "*)
    case " $* " in
      *" --install "*)
        for skill in loopx loopx-benchmark loopx-doc-registry loopx-pr-program loopx-pr-review loopx-project loopx-self-repair; do
          mkdir -p "$skills_dir/$skill"
          printf '%s\n' '---' "name: \"$skill\"" "description: \"LoopX runtime smoke skill.\"" '---' '' '# LoopX smoke' > "$skills_dir/$skill/SKILL.md"
        done
        printf '%s\n' '{"ok":true,"schema_version":"loopx_workflow_skill_install_v0","operation":"install","host_surface":"deepseek-harness-native","installed":{"loopx-benchmark":"created","loopx-doc-registry":"created","loopx-pr-program":"created","loopx-pr-review":"created","loopx-project":"created","loopx-self-repair":"created"},"entry":{"status":"created"}}'
        ;;
      *)
        if [ -f "$skills_dir/loopx/SKILL.md" ]; then required=false; else required=true; fi
        printf '%s\n' "{\"ok\":true,\"schema_version\":\"loopx_workflow_skill_install_v0\",\"operation\":\"inspect\",\"host_surface\":\"deepseek-harness-native\",\"install_required\":$required}"
        ;;
    esac
    ;;
  *)
    printf '%s\n' '{"ok":false,"error":"unexpected smoke command"}'
    exit 1
    ;;
esac
`)
    await chmod(fakeLoopX, 0o755)
    const env = {
      ...process.env,
      DSH_HOME: home,
      DSH_AGENTS_HOME: join(temp, 'agents'),
      LOOPX_BIN: fakeLoopX,
    }
    run(dshBin, [
      'plugin', '--profile', 'web', 'add', tarball,
      '--prefer-offline', '--ignore-scripts',
    ], env)
    installed = await realpath(join(home, 'profiles', 'web', 'node_modules', packageId))
    const dshInstallRequire = createRequire(
      join(dirname(dirname(dshBin)), '..', 'package.json'),
    )
    const installedDshRequire = createRequire(
      dshInstallRequire.resolve('@deepseek-ai/dsh/package.json'),
    )
    const installedConnection = await import(pathToFileURL(
      installedDshRequire.resolve('@deepseek-ai/dsh-client-connection'),
    ).href)
    const sharedApi = Reflect.has(installedConnection.HostConnectionService.prototype, 'fetch')
    await exerciseRealDshWeb(home, env, cliLog, skipSessionFixture, sharedApi)
    await exercisePackedService(installed)
    const installedDump = run(dshBin, ['--profile', 'web', '--dump-config'], env)
    let previousRow = -1
    for (const [id, name] of [
      ['loopx-goalbar', packageId],
      ['loopx-init-command', `${packageId}/init-command`],
      ['loopx-driver', `${packageId}/driver`],
      ['loopx-shadow-observer', `${packageId}/observer`],
    ]) {
      const row = installedDump.indexOf(`id: ${id}`)
      assert(
        row > previousRow && installedDump.includes(`name: ${name}`),
        `missing or unordered ${id}`,
      )
      previousRow = row
    }
    assert.match(installedDump, /id: webserver[\s\S]*inject:\n\s+- webStartup\n\s+- loopxBootstrap/u)
    assert.match(installedDump, /id: web-runtime[\s\S]*inject:\n\s+- webStartup\n\s+- loopxBootstrap/u)
    run(dshBin, ['plugin', '--profile', 'web', 'remove', packageId], env)
    const dump = run(dshBin, ['--profile', 'web', '--dump-config'], env)
    assert(!dump.includes(packageId), 'profile removal retained a LoopX Loader row')
  } finally {
    await rm(temp, { recursive: true, force: true })
  }
  process.stdout.write([
    'dsh-loopx GoalBar runtime smoke passed',
    `  real-profile: DSH ${dshVersion}, packed install, awaited automatic initialization, immediate /loopx skill readback, boot graph, served/materialized Client, loopback fence, process teardown, idle no-extra-CLI`,
    '  packed-connection: live mid-turn binding, lease revision reconciliation, runtime-only update, pending-watch abort, successful Start/Pause, handler disposal',
    '  client-lifecycle: slot coexistence/session injection plus ordinary-unload and cached-reapply CSS cleanup',
    '  manual-evidence: mounted Client-to-carrier Start/Pause stays in the owner-reviewed packed-browser gate',
  ].join('\n') + '\n')
}

await main()
