#!/usr/bin/env node

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtemp, readFile, realpath, rm } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const dshBin = process.env.DSH_BIN || join(packageRoot, 'node_modules', '.bin', 'dsh')
const packageId = 'dsh-loopx-plugin'
const rows = [
  ['loopx-goalbar', packageId],
  ['loopx-init-command', `${packageId}/init-command`],
  ['loopx-driver', `${packageId}/driver`],
  ['loopx-shadow-observer', `${packageId}/observer`],
]
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

function specs(argv) {
  argv = argv.filter(value => value !== '--')
  if (argv.length === 0) return [{ kind: 'path', value: packageRoot }]
  const result = []
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index]
    const value = argv[index + 1]
    if ((flag !== '--package-path' && flag !== '--tarball') || !value) {
      throw new Error('use --package-path PATH and/or --tarball PATH')
    }
    result.push({ kind: flag === '--tarball' ? 'tarball' : 'path', value: resolve(value) })
  }
  if (!result.length) throw new Error('one package input is required')
  return result
}

function run(file, args, env) {
  const result = spawnSync(file, args, {
    cwd: packageRoot,
    env,
    encoding: 'utf8',
    maxBuffer: 8 * 1024 * 1024,
    shell: false,
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw new Error(`${file} ${args.join(' ')} failed\n${result.stdout}\n${result.stderr}`)
  }
  return result.stdout
}

function assertConfig(dump) {
  const packageNames = dump
    .split(/\r?\n/u)
    .map(line => line.match(/^\s*name:\s+(\S+)\s*$/u)?.[1])
    .filter(name => name === packageId || name?.startsWith(`${packageId}/`))
  assert.deepEqual(packageNames, rows.map(([, module]) => module))
  let previous = -1
  for (const [id, module] of rows) {
    const position = dump.indexOf(`id: ${id}`)
    assert(position > previous, `missing or unordered ${id}`)
    assert(dump.includes(`name: ${module}`), `missing ${module}`)
    previous = position
  }
}

function assertTarball(path) {
  const entries = run('tar', ['-tf', path], process.env).trim().split('\n')
  assert.equal(new Set(entries).size, entries.length, 'tarball contains duplicate entries')
  for (const required of packedStaticEntries) {
    assert(entries.includes(required), `tarball missing ${required}`)
  }
  const hashedCounts = new Map(packedHashedEntries.map(([label]) => [label, 0]))
  for (const entry of entries) {
    if (packedStaticEntries.has(entry)) continue
    const match = packedHashedEntries.find(([, pattern]) => pattern.test(entry))
    assert(match, `tarball contains unapproved entry ${entry}`)
    hashedCounts.set(match[0], hashedCounts.get(match[0]) + 1)
  }
  for (const [label] of packedHashedEntries) {
    assert.equal(hashedCounts.get(label), 1, `tarball requires exactly one ${label}`)
  }
  assert.equal(entries.length, packedStaticEntries.size + packedHashedEntries.length)
}

async function clientModulesClass() {
  const localRequire = createRequire(join(packageRoot, 'package.json'))
  const dshRequire = createRequire(localRequire.resolve('@deepseek-ai/dsh/package.json'))
  const appRequire = createRequire(dshRequire.resolve('@deepseek-ai/dsh-web-app/package.json'))
  return (await import(pathToFileURL(appRequire.resolve('@deepseek-ai/dsh-client-modules')).href))
    .ClientModuleRegistry
}

async function assertClientDiscovery(installed, manifest) {
  assert.deepEqual(manifest.exports?.['./client'], {
    types: './lib/types/client/index.d.ts',
    default: './lib/client.js',
  })
  assert.equal(manifest.dsh?.client?.platform, 'web')
  assert(Array.isArray(manifest.dsh?.client?.inject))

  const ClientModuleRegistry = await clientModulesClass()
  const installedRequire = createRequire(join(installed, 'package.json'))
  const registry = Object.create(ClientModuleRegistry.prototype)
  registry.pkgMeta = new Map()
  registry.table = new Map()
  registry.sources = new Map()
  registry.dirty = new Set()
  registry.initialRevisionNonce = 'profile-smoke-nonce'
  registry.nextInitialRevision = 0
  const baseUrl = pathToFileURL(join(installed, 'package.json')).href
  // 0.1.5 resolves the mounted row through the Loader's own `internal`
  // resolver and reads the base URL from the entry's tree context.
  registry.ctx = {
    logger: { warn() {} },
    loader: {
      internal: {
        version: 'v1',
        resolveSync: specifier => ({
          url: pathToFileURL(installedRequire.resolve(`${specifier}/package.json`)).href,
        }),
      },
      entries: () => [{
        options: { name: packageId },
        parent: { tree: { ctx: { baseUrl } } },
        fiber: {},
        disabled: false,
      }],
    },
  }
  // 0.1.5 returns the located manifest beside the parsed client declaration,
  // and the initial row revision is an opaque allocation rather than a hash.
  const resolved = registry.resolveMeta(packageId, baseUrl)
  assert.equal(resolved.meta.clientPath, join(installed, 'lib', 'client.js'))
  assert.deepEqual(resolved.meta.inject, manifest.dsh.client.inject)
  assert.equal(registry.processOne(packageId, error => { throw error }), true)
  assert.equal(registry.table.get(packageId)?.entry.rev ?? '', 'profile-smoke-nonce-0')
  const client = await readFile(join(installed, 'lib', 'client.js'), 'utf8')
  assert(client.startsWith('window.__ModuleLoader__.load({'))
  assert(client.includes('id: "dsh-loopx-plugin"'))
}

async function exerciseInstalled(installed) {
  const requireFromPlugin = createRequire(join(installed, 'package.json'))
  const [hostModule, initModule, driverModule] = await Promise.all([
    import(pathToFileURL(requireFromPlugin.resolve(packageId)).href),
    import(pathToFileURL(requireFromPlugin.resolve('dsh-loopx-plugin/init-command')).href),
    import(pathToFileURL(requireFromPlugin.resolve('dsh-loopx-plugin/driver')).href),
  ])
  assert.equal(hostModule.name, packageId)
  assert.deepEqual(hostModule.inject, ['agents', 'connection', 'loopxBootstrap'])
  assert.equal(typeof hostModule.createGoalBarService, 'function')
  const commands = new Map()
  const services = new Map()
  const initCalls = []
  const initWarnings = []
  await initModule.apply({
    commands: { register: definition => commands.set(definition.name, definition) },
    logger: { warn: message => initWarnings.push(message) },
    reflect: {
      provide(name, value) {
        services.set(name, value)
        return () => services.delete(name)
      },
    },
  }, {
    skillsDir: join(installed, '.fixture-skills'),
    runner: async (_file, args, options) => {
      initCalls.push([...args])
      if (options.signal?.aborted) {
        throw new hostModule.LoopXCliError('aborted', 'cancelled', false)
      }
      if (args.at(-1) === '--version') {
        return { exitCode: 0, stdout: 'loopx smoke\n', stderr: '' }
      }
      if (args.includes('--install')) {
        return {
          exitCode: 0,
          stdout: JSON.stringify({
            ok: true,
            schema_version: 'loopx_workflow_skill_install_v0',
            operation: 'install',
            host_surface: 'deepseek-harness-native',
            installed: Object.fromEntries([
              'loopx-project',
              'loopx-pr-program',
              'loopx-pr-review',
              'loopx-doc-registry',
              'loopx-self-repair',
            ].map(name => [name, 'unchanged'])),
            entry: { status: 'unchanged' },
          }),
          stderr: '',
        }
      }
      return {
        exitCode: 0,
        stdout: JSON.stringify({
          ok: true,
          schema_version: 'loopx_workflow_skill_install_v0',
          operation: 'inspect',
          host_surface: 'deepseek-harness-native',
          install_required: false,
        }),
        stderr: '',
      }
    },
  })
  assert.deepEqual([...commands.keys()], ['loopx-init'])
  assert.deepEqual(services.get('loopxBootstrap'), { state: 'ready' })
  assert.equal(initWarnings.length, 0)
  assert.equal(initCalls.filter(args => args.includes('--install')).length, 1)
  const command = commands.get('loopx-init')
  assert.equal(command.recordInput, false)
  const followups = []
  const agent = { followup: message => followups.push(message) }
  const usage = await command.handler({
    agent,
    rawInput: ' unexpected',
    signal: new AbortController().signal,
  })
  assert.deepEqual(usage, { kind: 'error', text: 'Usage: /loopx-init' })
  assert.equal(followups.length, 0)

  const cancelledSignal = new AbortController()
  cancelledSignal.abort()
  const cancelled = await command.handler({
    agent,
    rawInput: '',
    signal: cancelledSignal.signal,
  })
  assert.deepEqual(cancelled, {
    kind: 'error',
    text: 'LOOPX_INIT_CANCELLED: initialization was cancelled.',
  })
  assert.equal(followups.length, 1)
  assert.equal(followups[0].role, 'user')
  assert.deepEqual(followups[0].source, {
    kind: 'plugin',
    plugin: 'dsh-loopx-plugin/init-command',
  })
  assert.notDeepEqual(followups[0].source, {
    kind: 'plugin',
    plugin: 'dsh-loopx-plugin/driver',
  })
  assert.equal(typeof driverModule.LoopXContinuationDriver, 'function')
  assert.equal(driverModule.inject.join(','), 'agents,loopxBootstrap')

  const runnerCalls = []
  const timerCalls = []
  const followupMessages = []
  let maintenanceCalls = 0
  const session = {
    id: 'inactive-session',
    header: { id: 'inactive-session', cwd: installed },
    snapshotEvents: () => [],
  }
  const inactiveAgent = {
    id: 'inactive-session',
    status: 'idle',
    session,
    inbox: { nextTurn: [], nextStep: [] },
    followup(message) {
      followupMessages.push(message)
    },
    async runMaintenance(operation) {
      maintenanceCalls += 1
      return operation(new AbortController().signal)
    },
  }
  const driver = new driverModule.LoopXContinuationDriver({
    isLiveAgent: candidate => candidate === inactiveAgent,
    runner: async (...args) => {
      runnerCalls.push(args)
      throw new Error('inactive Driver must not run LoopX')
    },
    clock: {
      setTimeout(callback, delayMs) {
        timerCalls.push({ callback, delayMs })
        return timerCalls.length
      },
      clearTimeout() {},
    },
  })
  driver.observeAgent(inactiveAgent)
  driver.onSessionStart(inactiveAgent)
  driver.onAgentStatus(inactiveAgent, 'idle')
  driver.onAgentStatus(inactiveAgent, 'idle')
  driver.onSessionEvent(inactiveAgent, {
    type: 'user/message',
    data: {
      id: 'ordinary-message',
      role: 'user',
      content: [{ type: 'text', text: 'ordinary input' }],
      source: { kind: 'user' },
    },
  })
  await Promise.resolve()
  assert.equal(runnerCalls.length, 0, 'inactive Driver made a LoopX runner call')
  assert.equal(timerCalls.length, 0, 'inactive Driver created a timer')
  assert.equal(maintenanceCalls, 0, 'inactive Driver entered Agent maintenance')
  assert.equal(followupMessages.length, 0, 'inactive Driver queued a followup')
  await driver.dispose()
}

async function main() {
  const inputs = specs(process.argv.slice(2))
  for (const [index, spec] of inputs.entries()) {
    if (spec.kind === 'tarball') assertTarball(spec.value)
    const home = await mkdtemp(join(tmpdir(), `dsh-loopx-${index + 1}-`))
    const env = { ...process.env, DSH_HOME: home }
    try {
      const install = [spec.value]
      run(dshBin, [
        'plugin', '--profile', 'web', 'add', ...install,
        spec.kind === 'tarball' ? '--prefer-offline' : '--offline', '--ignore-scripts',
      ], env)
      const dump = run(dshBin, ['--profile', 'web', '--dump-config'], env)
      assertConfig(dump)
      const installed = await realpath(join(home, 'profiles', 'web', 'node_modules', 'dsh-loopx-plugin'))
      const manifest = JSON.parse(await readFile(join(installed, 'package.json'), 'utf8'))
      assert.equal(manifest.name, 'dsh-loopx-plugin')
      await assertClientDiscovery(installed, manifest)
      await exerciseInstalled(installed)
      run(dshBin, ['plugin', '--profile', 'web', 'remove', 'dsh-loopx-plugin'], env)
      const removed = run(dshBin, ['--profile', 'web', '--dump-config'], env)
      for (const [id] of rows) assert(!removed.includes(`id: ${id}`), `remove retained ${id}`)
    } finally {
      await rm(home, { recursive: true, force: true })
    }
  }
  process.stdout.write(`dsh-loopx profile smoke passed (${inputs.map(item => item.kind).join(', ')})\n`)
}

await main()
