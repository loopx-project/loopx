import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  LoopXCliError,
  cliErrorFields,
  resolveLoopXCommand,
  runFile,
  runJsonCommand,
  runJsonMutationCommand,
} from '../src/cli.ts'
import type { FileRunner, LoopXCommand } from '../src/cli.ts'

const command: LoopXCommand = {
  file: 'loopx',
  prefix: [],
  skillCommand: 'loopx',
  version: 'loopx 0.5.0',
}

describe('safe command diagnostics', () => {
  it('preserves the primary timeout and final exit among nine candidates', async () => {
    let attempts = 0
    const failure = await resolveLoopXCommand({
      env: { LOOPX_BIN: '', PYTHON_BIN: undefined },
      managedLauncher: {
        path: '/fixture/private-launcher.py',
        pythonBins: Array.from({ length: 6 }, (_, index) => `fixture-python-${index}`),
      },
      runner: (_file, _args, options) => {
        attempts += 1
        // Use real child processes behind the candidate adapter on every platform.
        return runFile(process.execPath, ['-e', attempts === 1
          ? 'setTimeout(() => {}, 10_000)'
          : 'process.stderr.write("PRIVATE process output"); process.exitCode = 1'], {
          ...options, timeoutMs: attempts === 1 ? 50 : 5_000,
        })
      },
    }).catch(error => error)
    expect(attempts).toBe(9)
    expect(failure.diagnostics.probes).toHaveLength(9)
    const record = cliErrorFields(failure)
    const probes = record.split(' probes=')[1]!.split(' ')[0]!.split(';')
    expect(probes).toHaveLength(8)
    expect(probes[0]).toContain('kind=timeout,cause_kind=timeout')
    expect(probes[0]).toContain('candidate=managed-launcher')
    expect(probes.at(-1)).toContain('kind=exit,cause_kind=exit')
    expect(probes.at(-1)).toContain('candidate=python-module')
    expect(record).toContain('probes_omitted=1')
    for (const privateValue of ['PRIVATE', 'fixture-python', 'private-launcher', process.cwd(), process.execPath, '--version']) {
      expect(record).not.toContain(privateValue)
    }
  })

  it('retains the primary probe failure as well as the final Windows fallback exit', async () => {
    const failure = await resolveLoopXCommand({
      env: { LOOPX_BIN: '', PYTHON_BIN: '' },
      runner: async file => {
        if (file === 'loopx') throw new LoopXCliError('timeout', 'PRIVATE output', true)
        return { exitCode: 1, stdout: '', stderr: 'PRIVATE module diagnostic' }
      },
    }).catch(error => error)
    expect(failure).toMatchObject({ kind: 'missing', diagnostics: {
      candidate: 'python-module', causeKind: 'exit', cwdExists: true,
    } })
    const record = cliErrorFields(failure)
    expect(record).toContain('kind=missing cause_kind=exit code=none exit_code=1 candidate=python-module cwd_exists=true')
    expect(record).toContain('kind=timeout,cause_kind=timeout,code=none,exit_code=none,candidate=path-loopx')
    expect(record).not.toContain('PRIVATE')
  })

  it('records a missing spawn cwd without exposing it', async () => {
    const failure = await runJsonCommand({
      ...command, file: process.execPath, prefix: ['-e', 'process.stdout.write("{}")'], candidate: 'path-loopx',
    }, [], { cwd: `${process.cwd()}/absent-diagnostic-cwd/child` }).catch(error => error)
    expect(failure).toMatchObject({ kind: 'missing', diagnostics: {
      osCode: 'ENOENT', candidate: 'path-loopx', cwdExists: false,
    } })
    expect(cliErrorFields(failure)).toContain('code=ENOENT exit_code=none candidate=path-loopx cwd_exists=false')
    expect(cliErrorFields(failure)).not.toContain('absent-diagnostic-cwd')
  })

  it('excludes paths, text and unrecognised labels from the record', () => {
    const failure = new LoopXCliError('transport', 'PRIVATE command output', true, 7, {
      osCode: 'PRIVATE/path', candidate: 'PRIVATE/path' as 'path-loopx', cwdExists: true,
    })
    expect(cliErrorFields(failure)).toContain('code=none exit_code=7 candidate=unknown cwd_exists=true')
    expect(cliErrorFields(failure)).not.toContain('PRIVATE')
  })
})

describe('runJsonCommand', () => {
  it.each(['valid-version', 'nonzero-exit', 'rejected-probe'])(
    'honours cancellation at the %s probe completion boundary', async outcome => {
      const controller = new AbortController()
      let probes = 0
      await expect(resolveLoopXCommand({
        signal: controller.signal, env: { LOOPX_BIN: 'fixture-loopx' },
        runner: async () => {
          probes += 1
          controller.abort()
          if (outcome === 'rejected-probe') throw new Error('probe closed during cancellation')
          return { exitCode: outcome === 'valid-version' ? 0 : 1, stdout: 'loopx 1.2.4\n', stderr: '' }
        },
      })).rejects.toMatchObject({ kind: 'aborted', retryable: false })
      expect(probes).toBe(1)
    },
  )

  it.each(['0.5.4', '1.2.3', '1.2.4rc1', '1.2.4.dev1', 'smoke', '1.2.4unknown'])(
    'rejects unavailable or pre-fix CLI version %s', async version => {
      await expect(resolveLoopXCommand({
        env: { LOOPX_BIN: 'configured-loopx' },
        runner: async () => ({ exitCode: 0, stdout: `loopx ${version}\n`, stderr: '' }),
      })).rejects.toMatchObject({ kind: 'missing', retryable: false })
    },
  )

  it.each(['1.2.4', '1.2.4+local', '1.2.4.post1', '1.2.5rc1', '2.0.0.dev1'])(
    'accepts CLI release %s at or after the fixed floor', async version => {
      const command = await resolveLoopXCommand({
        env: { LOOPX_BIN: 'configured-loopx' },
        runner: async () => ({ exitCode: 0, stdout: `loopx ${version}\n`, stderr: '' }),
      })
      expect(command.version).toBe(`loopx ${version}`)
    },
  )

  it('skips an old global CLI for a compatible Python module', async () => {
    const command = await resolveLoopXCommand({
      env: { PYTHON_BIN: 'fixture-python', LOOPX_BIN: '' },
      runner: async file => ({
        exitCode: 0, stdout: `loopx ${file === 'loopx' ? '1.2.3' : '1.2.4'}\n`, stderr: '',
      }),
    })
    expect(command.file).toBe('fixture-python')
    expect(command.prefix).toEqual(['-m', 'loopx.cli'])
  })

  it('tries the python name a python.org Windows PATH registers for the module fallback', async () => {
    const attempted: string[] = []
    const resolved = await resolveLoopXCommand({
      env: { LOOPX_BIN: '' },
      runner: async (file, args) => {
        attempted.push(`${file} ${args.join(' ')}`)
        if (file === 'python') return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
        throw new LoopXCliError('missing', 'missing', false)
      },
    })

    expect(attempted).toContain('python3 -m loopx.cli --version')
    expect(resolved.file).toBe('python')
    expect(resolved.prefix).toEqual(['-m', 'loopx.cli'])
    expect(resolved.skillCommand).toBe('python -m loopx.cli')
  })

  it('keeps a configured LoopX executable as one quoted shell word', async () => {
    const configured = '/opt/Loop X/loopx'
    const resolved = await resolveLoopXCommand({
      env: { ...process.env, LOOPX_BIN: configured },
      runner: async (file, args) => {
        expect(file).toBe(configured)
        expect(args).toEqual(['--version'])
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      },
    })

    expect(resolved.skillCommand).toBe('"$LOOPX_BIN"')
  })

  it('renders the configured Python fallback as one shell-safe skill command', async () => {
    const python = "/opt/Loop X/python'3"
    const resolved = await resolveLoopXCommand({
      env: { ...process.env, PYTHON_BIN: python },
      runner: async (file, args) => {
        if (file === 'loopx') return { exitCode: 127, stdout: '', stderr: '' }
        expect(file).toBe(python)
        expect(args).toEqual(['-m', 'loopx.cli', '--version'])
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      },
    })

    expect(resolved.skillCommand).toBe(`'/opt/Loop X/python'"'"'3' -m loopx.cli`)
  })

  it('prefers a managed launcher and quotes both runtime paths for skills', async () => {
    const python = "/opt/Python 3/python'3"
    const launcher = "/opt/DSH LoopX/runtime'1/loopx_cli.py"
    const resolved = await resolveLoopXCommand({
      managedLauncher: { path: launcher, pythonBins: [python] },
      runner: async (file, args) => {
        expect(file).toBe(python)
        expect(args).toEqual([launcher, '--version'])
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      },
    })

    expect(resolved.skillCommand).toBe(
      `'/opt/Python 3/python'"'"'3' '/opt/DSH LoopX/runtime'"'"'1/loopx_cli.py'`,
    )
  })

  it('retries transport-safe fixed argv without changing the request', async () => {
    const calls: string[][] = []
    const runner: FileRunner = async (_file, args) => {
      calls.push([...args])
      if (calls.length < 3) {
        throw new LoopXCliError('timeout', 'timed out', true)
      }
      return { exitCode: 0, stdout: '{"ok":true,"value":7}', stderr: '' }
    }

    const payload = await runJsonCommand(command, ['quota', 'should-run'], {
      runner,
      attempts: 3,
      retryDelaysMs: [0, 0],
    })

    expect(payload).toEqual({ ok: true, value: 7 })
    expect(calls).toEqual([
      ['quota', 'should-run'],
      ['quota', 'should-run'],
      ['quota', 'should-run'],
    ])
  })

  it('returns a typed nonzero authority response without retrying', async () => {
    let calls = 0
    const runner: FileRunner = async () => {
      calls += 1
      return {
        exitCode: 1,
        stdout: '{"ok":false,"error_kind":"authority_denied"}',
        stderr: 'private diagnostic must not be parsed',
      }
    }

    const payload = await runJsonCommand(command, ['quota', 'should-run'], {
      runner,
      attempts: 3,
    })

    expect(payload.error_kind).toBe('authority_denied')
    expect(calls).toBe(1)
  })

  it('does not retry an incompatible typed schema', async () => {
    let calls = 0
    const runner: FileRunner = async () => {
      calls += 1
      return { exitCode: 0, stdout: '{"schema_version":"old"}', stderr: '' }
    }

    await expect(runJsonCommand(command, ['inspect'], {
      runner,
      attempts: 3,
      validate: payload => payload.schema_version === 'current',
    })).rejects.toMatchObject({ kind: 'invalid_schema', retryable: false })
    expect(calls).toBe(1)
  })
})

describe('reap-aware mutation execution', () => {
  it('waits for child close after timeout termination', async () => {
    const startedAt = Date.now()
    await expect(runFile(process.execPath, [
      '-e',
      [
        "process.on('SIGTERM',()=>setTimeout(()=>process.exit(0),80))",
        'setInterval(()=>{},1000)',
      ].join(';'),
    ], {
      timeoutMs: 300,
      maxOutputBytes: 1024,
    })).rejects.toMatchObject({ kind: 'timeout' })

    expect(Date.now() - startedAt).toBeGreaterThanOrEqual(350)
  })

  it('waits for child close after output-limit termination', async () => {
    const startedAt = Date.now()
    await expect(runFile(process.execPath, [
      '-e',
      [
        "process.on('SIGTERM',()=>setTimeout(()=>process.exit(0),80))",
        "setTimeout(()=>process.stdout.write('x'.repeat(10000)),50)",
        'setInterval(()=>{},1000)',
      ].join(';'),
    ], {
      timeoutMs: 1_000,
      maxOutputBytes: 32,
    })).rejects.toMatchObject({ kind: 'output_limit' })

    expect(Date.now() - startedAt).toBeGreaterThanOrEqual(100)
  })

  it('runs a mutation once without forwarding caller cancellation', async () => {
    const calls: Array<{ readonly args: readonly string[]; readonly signal: unknown }> = []
    const runner: FileRunner = async (_file, args, options) => {
      calls.push({ args, signal: options.signal })
      return {
        exitCode: 1,
        stdout: '{"ok":false,"schema_version":"typed_failure"}',
        stderr: '/private/diagnostic',
      }
    }

    await expect(runJsonMutationCommand(command, ['mutate', '--execute'], {
      runner,
      timeoutMs: 50,
    })).rejects.toMatchObject({ kind: 'typed_failure', retryable: false })
    expect(calls).toEqual([{
      args: ['mutate', '--execute'],
      signal: undefined,
    }])
  })
})

describe.skipIf(process.platform === 'win32')('owned CLI process group', () => {
  it.each(['timeout', 'abort', 'mutation', 'escaped pipe'] as const)(
    'retires a descendant holding an inherited pipe on %s before returning or retrying',
    async mode => {
      const dir = mkdtempSync(join(tmpdir(), 'loopx-cli-child-'))
      const childPidFile = join(dir, 'child-pid')
      const signal = new AbortController()
      const cli = { ...command, file: process.execPath }
      const child = [
        "process.on('SIGTERM', () => {})",
        'setTimeout(() => {}, 5000)',
      ].join(';')
      const fixture = [
        'const {spawn} = require("node:child_process")',
        'const {writeFileSync} = require("node:fs")',
        'const [code, path, mode] = process.argv.slice(1)',
        'const child = spawn(process.execPath, ["-e", code], {stdio: "inherit", detached: mode === "escaped pipe"})',
        'writeFileSync(path, String(child.pid))',
        // The grandchild keeps the pipe open after the CLI leader is gone.
        'process.exit(0)',
      ].join(';')
      const live = (pid: number): boolean => {
        try {
          const state = execFileSync('ps', ['-p', String(pid), '-o', 'stat='], {
            encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'],
          }).trim()
          return !state.startsWith('Z')
        } catch (error) {
          if ((error as { status?: number }).status === 1) return false
          throw error
        }
      }
      let attempts = 0
      let descendantAliveAfterFirst: boolean | undefined
      const pid = () => Number(readFileSync(childPidFile, 'utf8'))
      const runner: FileRunner = (_file, _args, options) => {
        attempts += 1
        if (attempts === 1) {
          return runFile(process.execPath, ['-e', fixture, child, childPidFile, mode], options)
        }
        descendantAliveAfterFirst = live(pid())
        return runFile(process.execPath, ['-e', 'console.log(JSON.stringify({ok:true}))'], options)
      }
      const options = { runner, timeoutMs: 750 }
      const operation = mode === 'mutation'
        ? runJsonMutationCommand(cli, [], options)
        : runJsonCommand(cli, [], {
            ...options, signal: signal.signal, attempts: 2, retryDelaysMs: [0],
          })
      // Attach the failure observer before scheduling abort or a bounded test
      // deadline so neither test path leaves an unhandled rejection.
      const observed = operation.catch((error: unknown) => error)
      try {
        if (mode === 'abort') {
          await expect.poll(pid, { timeout: 600 }).toBeGreaterThan(1)
          signal.abort()
        }
        const pending = Symbol('pending after cleanup')
        let timer: ReturnType<typeof setTimeout> | undefined
        const outcome = await Promise.race([
          observed,
          new Promise(resolve => { timer = setTimeout(() => resolve(pending), 3200) }),
        ]).finally(() => { clearTimeout(timer) })
        expect(outcome).not.toBe(pending)
        expect(live(pid())).toBe(mode === 'escaped pipe')
        if (mode === 'timeout') {
          expect(outcome).toEqual({ ok: true })
          expect(attempts).toBe(2)
          expect(descendantAliveAfterFirst).toBe(false)
        } else {
          expect(outcome).toMatchObject({
            kind: mode === 'mutation' ? 'timeout' : mode === 'escaped pipe' ? 'transport' : 'aborted',
            retryable: false,
          })
          expect(attempts).toBe(1)
        }
      } finally {
        try { const childPid = pid(); if (live(childPid)) process.kill(childPid, 'SIGKILL') } catch {}
        await observed
        rmSync(dir, { recursive: true, force: true })
      }
    },
    10_000,
  )
})
