#!/usr/bin/env node

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtemp, readFile, realpath, rm } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import vm from 'node:vm'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const dshBin = process.env.DSH_BIN || join(packageRoot, 'node_modules', '.bin', 'dsh')
const packageId = 'dsh-loopx-plugin'
const rows = [
  ['loopx-goalbar', packageId],
  ['loopx-init-command', `${packageId}/init-command`],
  ['loopx-driver', `${packageId}/driver`],
  ['loopx-shadow-observer', `${packageId}/observer`],
]
const clientInject = [
  '@deepseek-ai/dsh-client-connection',
  '@deepseek-ai/dsh-client-locale',
  '@deepseek-ai/dsh-client-ui-conversation',
  '@deepseek-ai/dsh-client-ui-renderer',
]
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
  return result
}

function run(file, args, env = process.env) {
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
  const names = dump
    .split(/\r?\n/u)
    .map(line => line.match(/^\s*name:\s+(\S+)\s*$/u)?.[1])
    .filter(name => name?.startsWith(packageId))
  assert.deepEqual(names, rows.map(([, name]) => name))
  let previous = -1
  for (const [id, name] of rows) {
    const position = dump.indexOf(`id: ${id}`)
    assert(position > previous, `missing or unordered ${id}`)
    assert(dump.includes(`name: ${name}`), `missing ${name}`)
    previous = position
  }
}

function assertTarball(path) {
  const entries = run('tar', ['-tf', path]).trim().split('\n')
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

async function assertManifest(root) {
  const manifest = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'))
  assert.equal(manifest.name, packageId)
  assert.deepEqual(manifest.exports?.['./client'], {
    types: './lib/types/client/index.d.ts',
    default: './lib/client.js',
  })
  assert.deepEqual(manifest.exports?.['./observer'], {
    types: './lib/types/observer.d.ts',
    default: './lib/observer.js',
  })
  assert.deepEqual(manifest.dsh?.client, {
    inject: clientInject,
    platform: 'web',
  })
  for (const key of Object.keys(manifest.exports ?? {})) {
    assert(!/(?:typert|remote)/iu.test(key), `forbidden export ${key}`)
  }
  return manifest
}

function createStyleDocument() {
  const styles = []
  const document = {
    createElement(name) {
      assert.equal(name, 'style')
      const style = {
        dataset: {},
        textContent: '',
        getAttribute(attribute) {
          if (attribute === 'data-plugin') return style.dataset.plugin ?? null
          if (attribute === 'data-plugin-css') return style.dataset.pluginCss ?? null
          return null
        },
        setAttribute(attribute, value) {
          if (attribute === 'data-plugin') style.dataset.plugin = value
          if (attribute === 'data-plugin-css') style.dataset.pluginCss = value
        },
        remove() {
          const index = styles.indexOf(style)
          if (index >= 0) styles.splice(index, 1)
        },
      }
      return style
    },
    querySelector(selector) {
      const prefix = 'style[data-plugin-css='
      if (!selector.startsWith(prefix) || !selector.endsWith(']')) return null
      const tagId = JSON.parse(selector.slice(prefix.length, -1))
      return styles.find(style => style.dataset.pluginCss === tagId) ?? null
    },
    querySelectorAll(selector) {
      if (selector === 'style:not([data-plugin])') {
        return styles.filter(style => typeof style.dataset.plugin !== 'string')
      }
      if (selector === 'style[data-plugin]') {
        return styles.filter(style => typeof style.dataset.plugin === 'string')
      }
      const prefix = 'style[data-plugin='
      if (selector.startsWith(prefix) && selector.endsWith(']')) {
        const plugin = JSON.parse(selector.slice(prefix.length, -1))
        return styles.filter(style => style.dataset.plugin === plugin)
      }
      return []
    },
    head: {
      appendChild(style) {
        styles.push(style)
      },
    },
  }
  return { document, styles }
}

async function createRealClientModuleSystem(context, staticModules) {
  const localRequire = createRequire(join(packageRoot, 'package.json'))
  const dshRequire = createRequire(localRequire.resolve('@deepseek-ai/dsh/package.json'))
  const appRequire = createRequire(dshRequire.resolve('@deepseek-ai/dsh-web-app/package.json'))
  const sourcePath = appRequire.resolve('@deepseek-ai/dsh-client-modules/client')
  const source = await readFile(sourcePath, 'utf8')
  let handoff
  const registrationTarget = {
    mode: 'queue',
    pendingQueue: [],
    load(value) {
      assert.equal(handoff, undefined, 'client-modules registered more than one factory')
      handoff = value
    },
  }
  context.__ModuleLoader__ = registrationTarget
  vm.runInContext(source, context, { filename: sourcePath })
  assert.equal(handoff?.id, '@deepseek-ai/dsh-client-modules')
  const clientModules = handoff.factory(() => {
    throw new Error('client-modules unexpectedly required a platform module')
  })
  if (typeof clientModules.createClientModuleSystem === 'function') {
    return clientModules.createClientModuleSystem(
      registrationTarget,
      { id: handoff.id, exports: clientModules },
      {
        boot: {
          rev: 'dsh-loopx-plugin-artifact-smoke',
          entries: [{
            id: packageId,
            url: `/plugins/${packageId}/client.js`,
            rev: 'dsh-loopx-plugin-artifact-smoke',
            external: [],
          }],
          // 0.1.5 requires every entry to belong to exactly one initial-load
          // batch, so the synthetic manifest carries the application batch the
          // real host composes around this single row.
          batches: [{
            phase: 'application',
            url: `/plugins/${packageId}/client.js`,
            rev: 'dsh-loopx-plugin-artifact-smoke',
            entries: [packageId],
          }],
        },
        staticModules,
      },
    )
  }
  delete context.__ModuleLoader__
  return new clientModules.ClientModuleSystem({
    modules: [],
    staticModules,
  })
}

function createClientApplyHarness() {
  const effects = []
  const locales = new Set()
  const slots = new Set()
  const labels = []
  const ctx = {
    connection: {
      rpc: {
        async call() {
          throw new Error('artifact smoke must not issue RPC')
        },
      },
    },
    effect(execute, label) {
      labels.push(label)
      const dispose = execute()
      if (typeof dispose === 'function') effects.push(dispose)
    },
    locale: {
      register(namespace) {
        assert.equal(locales.has(namespace), false, `duplicate locale ${namespace}`)
        locales.add(namespace)
        return () => locales.delete(namespace)
      },
    },
    slots: {
      inject(name, install) {
        assert.equal(name, 'conversation.input.dock')
        const dispose = install()
        if (typeof dispose === 'function') effects.push(dispose)
      },
      register(config, component) {
        assert.equal(config.name, 'conversation.input.dock')
        assert.equal(config.id, 'loopx-goal')
        assert.equal(config.order, 15)
        assert.equal(typeof config.inject, 'function')
        assert.equal(typeof component, 'function')
        assert.equal(slots.has(config.id), false, `duplicate slot ${config.id}`)
        slots.add(config.id)
        return () => slots.delete(config.id)
      },
    },
    on(event) {
      assert.equal(event, 'connection/reset')
      return () => undefined
    },
  }
  return {
    ctx,
    labels,
    locales,
    slots,
    dispose() {
      for (const effect of effects.splice(0).reverse()) effect()
    },
  }
}

async function assertClientArtifact(root) {
  const source = await readFile(join(root, 'lib', 'client.js'), 'utf8')
  assert(source.startsWith('window.__ModuleLoader__.load({'))
  assert(source.includes('id: "dsh-loopx-plugin"'))
  assert(!/^\s*(?:import|export)\s/mu.test(source), 'client artifact contains bare ESM syntax')
  assert(!/sourceMappingURL|\.(?:ts|tsx)\.map/iu.test(source), 'client artifact contains a source map')
  assert(!/(?:^|["'])node:/mu.test(source), 'client artifact contains a Node builtin')
  assert(!/(?:typert|remote|EventSource)/u.test(source), 'client artifact contains a forbidden transport')
  assert(!/\/(?:Users|home|private|workspace)\//u.test(source), 'client artifact contains an absolute path')
  assert(!/[A-Za-z]:\\(?:Users|home|workspace)\\/u.test(source), 'client artifact contains a Windows absolute path')

  const requires = [...source.matchAll(/\brequire\("([^"]+)"\)/gu)].map(match => match[1])
  for (const specifier of requires) {
    assert(approvedRequires.has(specifier), `unapproved client require ${specifier}`)
  }

  const { document, styles } = createStyleDocument()
  const context = vm.createContext({ document })
  context.window = context
  const staticModules = Object.fromEntries(
    [...approvedRequires].map(specifier => [specifier, Object.freeze({})]),
  )
  const modules = await createRealClientModuleSystem(context, staticModules)
  vm.runInContext(source, context, { filename: 'client.js' })
  assert.equal(styles.length, 0, 'CSS ran before ModuleLoader materialization')
  const clientExports = await modules.import(packageId, '', {})
  assert.equal(typeof clientExports.apply, 'function')
  assert.deepEqual([...clientExports.inject], ['connection', 'locale', 'slots'])
  const record = modules.loadCache.get(packageId)
  assert(record, 'real ClientModuleSystem did not cache the client exports')
  assert.deepEqual(
    [...record.edges].sort(),
    [...new Set(requires)].sort(),
    'real ClientModuleSystem observed a different require closure',
  )
  assert.deepEqual([...record.styles], [`${packageId}/goalbar.module.css`])

  const first = createClientApplyHarness()
  assert.equal(clientExports.apply(first.ctx), undefined)
  assert.deepEqual(first.labels, [
    'dsh-loopx-plugin CSS ownership',
    'dsh-loopx-plugin GoalBar locale',
  ])
  assert.equal(first.locales.size, 1)
  assert.deepEqual([...first.slots], ['loopx-goal'])
  assert.equal(styles.length, 1)
  assert.equal(styles[0].dataset.plugin, packageId)
  assert.equal(styles[0].dataset.pluginCss, `${packageId}/goalbar.module.css`)
  assert.match(styles[0].textContent, /progress/u)

  const removeOwnedStylesForHmr = () => {
    for (const style of document.querySelectorAll('style[data-plugin]')) {
      if (style.getAttribute('data-plugin') === packageId) style.remove()
    }
  }
  first.dispose()
  assert.equal(first.locales.size, 0, 'ordinary Cordis unload retained locale dictionaries')
  assert.equal(first.slots.size, 0, 'ordinary Cordis unload retained the slot contribution')
  assert.equal(styles.length, 0, 'ordinary Cordis unload retained plugin-owned CSS')
  removeOwnedStylesForHmr()
  assert.equal(styles.length, 0, 'HMR cleanup after fiber disposal was not a no-op')

  const cachedExports = await modules.import(packageId, '', {})
  assert.equal(cachedExports, clientExports, 'same-page import did not reuse cached exports')
  assert.equal(styles.length, 0, 'cached import unexpectedly reran the client factory')
  const reapplied = createClientApplyHarness()
  assert.equal(cachedExports.apply(reapplied.ctx), undefined)
  assert.equal(styles.length, 1, 'cached-export reapply did not restore CSS')
  assert.deepEqual([...reapplied.slots], ['loopx-goal'])
  reapplied.dispose()
  assert.equal(styles.length, 0, 'cached-export unload retained CSS')
  removeOwnedStylesForHmr()
  assert.equal(styles.length, 0, 'HMR ownership cleanup retained CSS')
}

async function clientModulesClass() {
  const localRequire = createRequire(join(packageRoot, 'package.json'))
  const dshRequire = createRequire(localRequire.resolve('@deepseek-ai/dsh/package.json'))
  const appRequire = createRequire(dshRequire.resolve('@deepseek-ai/dsh-web-app/package.json'))
  const modulePath = appRequire.resolve('@deepseek-ai/dsh-client-modules')
  return (await import(pathToFileURL(modulePath).href)).ClientModuleRegistry
}

async function assertDshDiscovery(root) {
  const ClientModuleRegistry = await clientModulesClass()
  const installedRequire = createRequire(join(root, 'package.json'))
  const registry = Object.create(ClientModuleRegistry.prototype)
  registry.pkgMeta = new Map()
  registry.table = new Map()
  registry.sources = new Map()
  registry.dirty = new Set()
  registry.initialRevisionNonce = 'artifact-smoke-nonce'
  registry.nextInitialRevision = 0
  const baseUrl = pathToFileURL(join(root, 'package.json')).href
  // 0.1.5 resolves the mounted row through the Loader's own `internal`
  // resolver, so the probe supplies that face instead of the retired
  // `resolvePkgJson` stub, and `resolveSource` reads the base URL from the
  // entry's tree context rather than from the call site.
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
  // 0.1.5 returns the located manifest beside the parsed client declaration.
  const resolved = registry.resolveMeta(packageId, baseUrl)
  assert.equal(resolved.meta.clientPath, join(root, 'lib', 'client.js'))
  assert.deepEqual(resolved.meta.inject, clientInject)
  assert.equal(resolved.meta.immediately, false)
  assert.equal(registry.processOne(packageId, error => { throw error }), true)
  const record = registry.table.get(packageId)
  assert.equal(record.clientPath ?? record.meta?.clientPath, join(root, 'lib', 'client.js'))
  assert.equal(record.entry.id, packageId)
  assert.deepEqual(record.entry.inject, clientInject)
  // The initial row revision is an opaque allocation, and the composed combo
  // URL is what carries it as the HMR cache-buster.
  assert.equal(record.entry.rev, 'artifact-smoke-nonce-0')
  assert(record.entry.url.includes(`rev=artifact-smoke-nonce-0`))
}

async function assertHostExports(root) {
  const requireFromPlugin = createRequire(join(root, 'package.json'))
  const resolutions = {
    root: requireFromPlugin.resolve(packageId),
    init: requireFromPlugin.resolve(`${packageId}/init-command`),
    driver: requireFromPlugin.resolve(`${packageId}/driver`),
    observer: requireFromPlugin.resolve(`${packageId}/observer`),
    client: requireFromPlugin.resolve(`${packageId}/client`),
  }
  assert.equal(resolutions.client, join(root, 'lib', 'client.js'))
  const [host, init, driver, observer] = await Promise.all([
    import(pathToFileURL(resolutions.root).href),
    import(pathToFileURL(resolutions.init).href),
    import(pathToFileURL(resolutions.driver).href),
    import(pathToFileURL(resolutions.observer).href),
  ])
  assert.equal(typeof host.apply, 'function')
  assert.equal(host.name, packageId)
  assert.deepEqual(host.inject, ['agents', 'connection', 'loopxBootstrap'])
  let handler
  let disposeHost
  let rpcDisposals = 0
  const hostContext = {
    agents: { get: () => undefined },
    connection: {
      fetch: {
        register(route) {
          assert.equal(route.path, '/api/loopx.goalbar')
          const candidate = route.fetch
          assert.equal(handler, undefined, 'Host registered duplicate RPC handlers')
          handler = candidate
          return async () => { rpcDisposals += 1 }
        },
      },
    },
    effect(execute, label) {
      assert.equal(label, 'dsh-loopx GoalBar Host service')
      assert.equal(disposeHost, undefined, 'Host registered duplicate service effects')
      disposeHost = execute()
    },
    logger: {
      warn() {
        throw new Error('artifact probe unexpectedly logged a warning')
      },
    },
  }
  assert.equal(host.apply(hostContext), undefined)
  assert.equal(typeof handler, 'function')
  assert.equal(typeof disposeHost, 'function')
  const response = await handler(new Request('http://fixture/api/loopx.goalbar', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      type: 'client-request', rpcId: 'malformed', method: 'loopx.goalbar',
      payload: { unexpected: true },
    }),
  }))
  const { result: malformed } = await response.json()
  assert.equal(malformed.ok, false)
  assert.equal(malformed.error.code, 'bad-request')
  await disposeHost()
  assert.equal(rpcDisposals, 1, 'Host effect did not dispose its RPC handler')
  assert.equal(typeof host.runJsonCommand, 'function')
  assert.equal(typeof host.LoopXContinuationDriver, 'function')
  assert.equal(typeof host.initializeLoopX, 'function')
  assert.equal(typeof init.apply, 'function')
  assert.equal(typeof driver.apply, 'function')
  assert.equal(observer.name, 'dsh-loopx-shadow-observer')
  assert.deepEqual(observer.inject, [])
  assert.equal(typeof observer.apply, 'function')
  assert.equal(host.ShadowObserver, undefined)

  const [rootSource, driverSource, observerSource] = await Promise.all([
    readFile(resolutions.root, 'utf8'),
    readFile(resolutions.driver, 'utf8'),
    readFile(resolutions.observer, 'utf8'),
  ])
  for (const source of [rootSource, driverSource]) {
    assert(!source.includes('LOOPX_DSH_SHADOW_OBSERVER_GOAL_ID'))
    assert(!source.includes('reliability_observer_envelope_v0'))
  }
  assert(observerSource.includes('LOOPX_DSH_SHADOW_OBSERVER_GOAL_ID'))
  assert(!observerSource.includes('.send('))
  assert(!observerSource.includes('agent/pre-step'))
}

async function exerciseInstalled(installed) {
  await assertManifest(installed)
  await assertHostExports(installed)
  await assertClientArtifact(installed)
  await assertDshDiscovery(installed)
}

async function main() {
  const inputs = specs(process.argv.slice(2))
  for (const [index, spec] of inputs.entries()) {
    if (spec.kind === 'tarball') assertTarball(spec.value)
    const home = await mkdtemp(join(tmpdir(), `dsh-loopx-client-${index + 1}-`))
    const env = { ...process.env, DSH_HOME: home }
    try {
      run(dshBin, [
        'plugin', '--profile', 'web', 'add', spec.value,
        spec.kind === 'tarball' ? '--prefer-offline' : '--offline',
        '--ignore-scripts',
      ], env)
      const dump = run(dshBin, ['--profile', 'web', '--dump-config'], env)
      assertConfig(dump)
      const installed = await realpath(join(home, 'profiles', 'web', 'node_modules', packageId))
      await exerciseInstalled(installed)
      run(dshBin, ['plugin', '--profile', 'web', 'remove', packageId], env)
      const removed = run(dshBin, ['--profile', 'web', '--dump-config'], env)
      for (const [id] of rows) assert(!removed.includes(`id: ${id}`), `remove retained ${id}`)
    } finally {
      await rm(home, { recursive: true, force: true })
    }
  }
  process.stdout.write(`dsh-loopx client artifact smoke passed (${inputs.map(item => item.kind).join(', ')})\n`)
}

await main()
