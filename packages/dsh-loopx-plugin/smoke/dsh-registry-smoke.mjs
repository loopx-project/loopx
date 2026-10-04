#!/usr/bin/env node

// Real DSH package-name installation against an isolated registry carrier.
// The carrier serves the actual packed bytes; it does not publish to npm.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const manifest = JSON.parse(await readFile(join(packageRoot, 'package.json'), 'utf8'))
const args = process.argv.slice(2)
assert(args.length === 2 && args[0] === '--tarball', 'usage: dsh-registry-smoke.mjs --tarball PATH')
const tarball = resolve(args[1])
const bytes = await readFile(tarball)
const require = createRequire(import.meta.url)
// Invoke the published JS CLI directly, including on Windows where .bin/dsh
// is a command-shell shim. No shell or Bash is needed for registry recovery.
const dshScript = process.env.DSH_CLI_JS
  || join(dirname(require.resolve('@deepseek-ai/dsh/package.json')), 'lib', 'bin.js')
const temp = await mkdtemp(join(tmpdir(), 'dsh-loopx-registry-'))
const env = { ...process.env, DSH_HOME: join(temp, 'home'), DSH_AGENTS_HOME: join(temp, 'agents') }
const installedManifest = join(env.DSH_HOME, 'profiles', 'web', 'node_modules', manifest.name, 'package.json')
let registry
const requested = new Set()
const metadata = {
  ...manifest,
  dist: { integrity: `sha512-${createHash('sha512').update(bytes).digest('base64')}` },
}
const server = createServer((request, response) => {
  const url = new URL(request.url, registry)
  requested.add(url.pathname)
  if (url.pathname === `/${manifest.name}`) {
    response.setHeader('Content-Type', 'application/json')
    response.end(JSON.stringify({ name: manifest.name,
      'dist-tags': { latest: manifest.version }, versions: { [manifest.version]: metadata } }))
  } else if (url.pathname === '/artifact.tgz') {
    response.end(bytes)
  } else if (url.pathname === '/-/v1/search') {
    response.setHeader('Content-Type', 'application/json')
    response.end(JSON.stringify({ objects: [
      { package: { name: 'unrelated', keywords: ['dsh-plugin'], links: { repository: 'https://github.com/example/unrelated' } } },
      { package: { name: 'loopx-cli', keywords: ['cli'], links: { repository: manifest.repository.url } } },
      { package: { name: manifest.name, keywords: manifest.keywords, links: { repository: manifest.repository.url } } },
    ] }))
  } else {
    // DSH still resolves its own real host dependencies through the registry.
    // Their metadata is not simulated by this package-distribution test.
    response.writeHead(302, { Location: `https://registry.npmjs.org${request.url}` })
    response.end()
  }
})

function run(file, argv) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(file, argv, { cwd: temp, env, stdio: ['ignore', 'pipe', 'pipe'] })
    let output = ''
    child.stdout.on('data', chunk => { output += chunk })
    child.stderr.on('data', chunk => { output += chunk })
    const timer = setTimeout(() => child.kill('SIGKILL'), 180_000)
    child.on('error', error => { clearTimeout(timer); reject(error) })
    child.on('close', code => {
      clearTimeout(timer)
      if (code === 0) resolveRun(output)
      else reject(new Error(`${file} failed (${code})\n${output}`))
    })
  })
}

function runDsh(argv) {
  return run(process.execPath, [dshScript, ...argv])
}

try {
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen))
  registry = `http://127.0.0.1:${server.address().port}/`
  metadata.dist.tarball = `${registry}artifact.tgz`
  await run(process.execPath, [join(packageRoot, 'scripts', 'verify-distribution.mjs'), '--tarball', tarball, '--registry', registry])
  requested.clear()
  // Install the unversioned name, exactly as repository discovery does.
  await runDsh(['plugin', '--profile', 'web', 'add', manifest.name,
    '--registry', registry, '--ignore-scripts', '--prefer-offline'])
  const installed = JSON.parse(await readFile(installedManifest, 'utf8'))
  assert.equal(installed.name, manifest.name)
  assert.equal(installed.version, manifest.version)
  const dump = await runDsh(['--profile', 'web', '--dump-config'])
  for (const [id, entry] of [
    ['loopx-goalbar', manifest.name], ['loopx-init-command', `${manifest.name}/init-command`],
    ['loopx-driver', `${manifest.name}/driver`], ['loopx-shadow-observer', `${manifest.name}/observer`],
  ]) {
    assert(dump.includes(`id: ${id}`) && dump.includes(`name: ${entry}`), `missing installed ${id}`)
  }
  assert(!dump.includes('loopx-repository'), 'marketplace installation selected the monorepo root')
  assert(requested.has(`/${manifest.name}`) && requested.has('/artifact.tgz'), 'DSH did not use the registry artifact')
  // A mismatched release/tag must fail readback, even though metadata exists.
  metadata.dist.integrity = 'sha512-unqualified'
  await assert.rejects(run(process.execPath, [join(packageRoot, 'scripts', 'verify-distribution.mjs'),
    '--tarball', tarball, '--registry', registry]), /registry integrity differs/u)
  await runDsh(['plugin', '--profile', 'web', 'remove', manifest.name])
  const removed = await runDsh(['--profile', 'web', '--dump-config'])
  assert(!removed.includes('name: dsh-loopx-plugin'), 'removal retained a plugin row')
  process.stdout.write('dsh-loopx registry smoke passed (real package-name install, exact artifact, all rows, negative integrity, removal)\n')
} finally {
  server.closeAllConnections()
  await new Promise(resolveClose => server.close(resolveClose))
  await rm(temp, { recursive: true, force: true })
}
