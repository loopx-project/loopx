#!/usr/bin/env node

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { chmod, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const temp = await mkdtemp(join(tmpdir(), 'dsh-selected-host-'))
const selectedHost = join(temp, 'selected host')
const calls = join(temp, 'calls.log')
const slash = path => path.replaceAll('\\', '/')

try {
  // Delegate every real operation except removal. The complete installer smoke
  // must observe this selected host's refusal rather than switch to a local CLI.
  await writeFile(selectedHost, `#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DSH_SELECTED_HOST_CALLS"
if [[ "$1" == plugin && "$4" == remove ]]; then
  echo selected-host-removal-refused >&2
  exit 37
fi
exec "$DSH_SELECTED_HOST_DELEGATE" "$@"
`)
  await chmod(selectedHost, 0o755)
  const result = spawnSync(process.execPath, [join(packageRoot, 'smoke/dsh-installer-smoke.mjs')], {
    cwd: temp, encoding: 'utf8', shell: false,
    timeout: 900_000, maxBuffer: 8 * 1024 * 1024,
    env: {
      ...process.env,
      DSH_BIN: slash(selectedHost),
      DSH_SELECTED_HOST_CALLS: slash(calls),
      DSH_SELECTED_HOST_DELEGATE: slash(process.env.DSH_BIN
        || join(packageRoot, 'node_modules/.bin/dsh')),
    },
  })
  assert.ifError(result.error)
  const observed = await readFile(calls, 'utf8')
  assert(observed.includes('plugin --profile web remove dsh-loopx-plugin'),
    `the selected host did not receive removal (status ${result.status})\n${result.stdout}\n${result.stderr}`)
  assert.notEqual(result.status, 0, 'the installer smoke falsely accepted selected-host failure')
  assert(result.stderr.includes('selected-host-removal-refused'), result.stderr)
  assert(!result.stdout.includes('dsh-loopx installer smoke passed'))
  process.stdout.write('selected-host removal refusal correctly fails the complete installer smoke\n')
} finally {
  await rm(temp, { recursive: true, force: true })
}
