import { execFile, spawn } from 'node:child_process'
import { statSync } from 'node:fs'
import { setTimeout as delay } from 'node:timers/promises'

// 1.2.4 includes the released Windows peer-file fix. Bootstrap and every
// runtime consumer must use the same floor, including an existing global CLI.
export const MINIMUM_LOOPX_VERSION = '1.2.4'

export type LoopXCliErrorKind =
  | 'aborted'
  | 'missing'
  | 'timeout'
  | 'transport'
  | 'output_limit'
  | 'exit'
  | 'typed_failure'
  | 'invalid_json'
  | 'invalid_schema'

export class LoopXCliError extends Error {
  constructor(
    readonly kind: LoopXCliErrorKind,
    message: string,
    readonly retryable: boolean,
    readonly exitCode?: number | undefined,
    readonly diagnostics: LoopXCliDiagnostics = {},
  ) {
    super(message)
    this.name = 'LoopXCliError'
  }
}

export interface LoopXCliDiagnostics {
  osCode?: string | undefined
  candidate?: 'configured-command' | 'managed-launcher' | 'path-loopx' | 'python-module' | undefined
  cwdExists?: boolean | undefined
  causeKind?: LoopXCliErrorKind | undefined
  probes?: readonly LoopXCliError[] | undefined
}

export function spawnCwdExists(cwd?: string): boolean | undefined {
  try {
    return statSync(cwd ?? process.cwd(), { throwIfNoEntry: false })?.isDirectory() ?? false
  } catch {
    return undefined
  }
}

/** Only closed labels and scalars belong in the driver's diagnostic record. */
function cliFailureFields(error: unknown): string {
  const failure = error instanceof LoopXCliError ? error : undefined
  const details = failure?.diagnostics
  const kind = (value: unknown): string => [
    'aborted', 'missing', 'timeout', 'transport', 'output_limit', 'exit',
    'typed_failure', 'invalid_json', 'invalid_schema',
  ].includes(value as string) ? String(value) : 'internal'
  const code = [
    'ENOENT', 'EPERM', 'EACCES', 'ETIMEDOUT', 'EAGAIN', 'EBUSY', 'EMFILE',
    'ENFILE', 'ENOMEM', 'ENOTDIR', 'E2BIG', 'ENOEXEC', 'EINVAL', 'ECANCELED', 'EIO',
  ].includes(details?.osCode ?? '') ? details!.osCode : 'none'
  const candidate = [
    'configured-command', 'managed-launcher', 'path-loopx', 'python-module',
  ].includes(details?.candidate ?? '') ? details!.candidate : 'unknown'
  const exitCode = Number.isSafeInteger(failure?.exitCode) ? failure!.exitCode : 'none'
  const cwd = typeof details?.cwdExists === 'boolean' ? details.cwdExists : 'unknown'
  return `kind=${kind(failure?.kind)} cause_kind=${kind(details?.causeKind ?? failure?.kind)} code=${code} exit_code=${exitCode} candidate=${candidate} cwd_exists=${cwd}`
}

export function cliErrorFields(error: unknown): string {
  const probes = error instanceof LoopXCliError
    ? error.diagnostics.probes ?? [] : []
  const selected = probes.length > 8 ? [probes[0]!, ...probes.slice(-7)] : probes
  const fields = selected.map(probe => cliFailureFields(probe).replaceAll(' ', ',')).join(';')
  return `${cliFailureFields(error)} probes=${fields || 'none'} probes_omitted=${probes.length - selected.length}`
}

export interface FileResult {
  readonly exitCode: number
  readonly stdout: string
  readonly stderr: string
}

export interface FileRunOptions {
  readonly cwd?: string | undefined
  readonly env?: NodeJS.ProcessEnv | undefined
  readonly signal?: AbortSignal | undefined
  readonly timeoutMs?: number | undefined
  readonly maxOutputBytes?: number | undefined
}

export type FileRunner = (
  file: string,
  args: readonly string[],
  options: FileRunOptions,
) => Promise<FileResult>

export interface LoopXCommand {
  readonly file: string
  readonly prefix: readonly string[]
  readonly skillCommand: string
  readonly version: string
  readonly candidate?: LoopXCliDiagnostics['candidate']
}

export interface ManagedLoopXLauncher {
  readonly path: string
  readonly pythonBins: readonly string[]
}

export interface ResolveLoopXCommandOptions {
  readonly runner?: FileRunner | undefined
  readonly signal?: AbortSignal | undefined
  readonly env?: NodeJS.ProcessEnv | undefined
  readonly managedLauncher?: ManagedLoopXLauncher | undefined
}

const DEFAULT_TIMEOUT_MS = 20_000
const DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024
const FORCE_KILL_GRACE_MS = 250
const CLEANUP_CONFIRM_MS = 1_000
const PS = process.platform === 'darwin' ? '/bin/ps' : '/usr/bin/ps'

// Match the managed Host's local process-group boundary. This npm package
// also runs standalone with older Core CLIs and cannot load their TS internals.
function signalOwnedGroup(pgid: number, signal: NodeJS.Signals | 0): boolean {
  try {
    process.kill(-pgid, signal)
    return true
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ESRCH') return false
    throw error
  }
}

async function confirmGroupStopped(pgid: number): Promise<void> {
  const deadline = performance.now() + CLEANUP_CONFIRM_MS
  for (;;) {
    const remaining = Math.ceil(deadline - performance.now())
    if (remaining <= 0) throw new Error('CLI group did not exit')
    try {
      if (!signalOwnedGroup(pgid, 0)) return
    } catch (error) {
      // Darwin may return EPERM for an unreaped, dead group. Check process
      // state; lack of permission to signal is not proof of termination.
      if ((error as NodeJS.ErrnoException).code !== 'EPERM') throw error
    }
    const rows = await new Promise<string>((resolve, reject) => {
      execFile(PS, ['-A', '-o', 'pgid=', '-o', 'stat='], {
        encoding: 'utf8', timeout: remaining, killSignal: 'SIGKILL', maxBuffer: 1024 * 1024,
        env: { LC_ALL: 'C', PATH: '/usr/bin:/bin' },
      }, (error, stdout) => { if (error) reject(error); else resolve(stdout) })
    })
    if (performance.now() >= deadline || !rows.trim()) throw new Error('CLI group observation unavailable')
    let live = false
    for (const row of rows.trim().split('\n')) {
      const [, group, state] = /^\s*(\d+)\s+(\S+)\s*$/u.exec(row) ?? []
      if (group === undefined || state === undefined) throw new Error('Invalid CLI group observation')
      if (Number(group) === pgid && !state.startsWith('Z')) live = true
    }
    if (!live) return
    await delay(10)
  }
}

function transportKind(error: NodeJS.ErrnoException, spawned: boolean): LoopXCliError {
  if (error.code === 'ENOENT') {
    return new LoopXCliError('missing', 'required executable is unavailable', false, undefined, { osCode: error.code })
  }
  return new LoopXCliError('transport', spawned ? 'LoopX command I/O failed' : 'could not start the LoopX command',
    true, undefined, { osCode: error.code })
}

// A non-retryable transport means the adapter could not safely continue to a
// second child. Keep launch/probe fallbacks from interpreting it as "missing."
export function isUnsafeToContinue(error: unknown): boolean {
  return error instanceof LoopXCliError
    && (error.kind === 'aborted' || (error.kind === 'transport' && !error.retryable))
}

export const runFile: FileRunner = (file, args, options) => new Promise((resolve, reject) => {
  if (options.signal?.aborted) {
    reject(new LoopXCliError('aborted', 'operation was cancelled', false))
    return
  }

  const posix = process.platform !== 'win32'
  const child = spawn(file, [...args], {
    cwd: options.cwd,
    env: options.env ?? process.env,
    shell: false,
    detached: posix,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  const stdout: Buffer[] = []
  const stderr: Buffer[] = []
  const limit = options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES
  let outputBytes = 0
  let settled = false
  let terminalError: LoopXCliError | undefined
  let forceKillTimer: ReturnType<typeof setTimeout> | undefined
  const closed = Promise.withResolvers<void>()
  const group = posix && child.pid !== undefined && child.pid > 1 ? child.pid : undefined

  const cleanup = (): void => {
    clearTimeout(timer)
    if (forceKillTimer !== undefined) clearTimeout(forceKillTimer)
    options.signal?.removeEventListener('abort', abort)
  }
  const rejectOnce = (error: LoopXCliError): void => {
    if (settled) return
    settled = true
    cleanup()
    reject(error)
  }
  const fail = (error: LoopXCliError): void => {
    if (settled || terminalError !== undefined) return
    terminalError = error
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', abort)
    if (child.pid === undefined) {
      rejectOnce(error)
      return
    }
    if (group !== undefined) {
      void (async () => {
        try {
          if (signalOwnedGroup(group, 'SIGTERM')) {
            await delay(FORCE_KILL_GRACE_MS)
            signalOwnedGroup(group, 'SIGKILL')
          }
          await confirmGroupStopped(group)
          // If a descendant escaped the group with these pipes, cleanup of
          // the owned group does not make this operation safe to retry.
          await Promise.race([
            closed.promise,
            delay(CLEANUP_CONFIRM_MS, undefined, { ref: false }).then(() => {
              throw new Error('CLI output pipes did not close')
            }),
          ])
          rejectOnce(error)
        } catch {
          child.stdout.destroy()
          child.stderr.destroy()
          rejectOnce(new LoopXCliError(
            'transport', 'LoopX CLI process cleanup was not confirmed', false, undefined,
            { causeKind: error.kind },
          ))
        }
      })()
      return
    }
    // Keep the direct-child/reap path on Windows. Node does not give this
    // adapter a Job Object that it owns after the command leader has exited.
    child.kill('SIGTERM')
    forceKillTimer = setTimeout(() => {
      if (!settled) child.kill('SIGKILL')
    }, FORCE_KILL_GRACE_MS)
  }
  const collect = (target: Buffer[], chunk: Buffer): void => {
    if (terminalError !== undefined) return
    outputBytes += chunk.byteLength
    if (outputBytes > limit) {
      fail(new LoopXCliError('output_limit', 'LoopX output exceeded its bounded limit', false))
      return
    }
    target.push(chunk)
  }
  const abort = (): void => {
    fail(new LoopXCliError('aborted', 'operation was cancelled', false))
  }
  const timer = setTimeout(() => {
    fail(new LoopXCliError('timeout', 'LoopX command timed out', true))
  }, options.timeoutMs ?? DEFAULT_TIMEOUT_MS)

  options.signal?.addEventListener('abort', abort, { once: true })
  child.stdout.on('data', (chunk: Buffer) => collect(stdout, chunk))
  child.stderr.on('data', (chunk: Buffer) => collect(stderr, chunk))
  const onTransportError = (error: NodeJS.ErrnoException) => fail(transportKind(error, child.pid !== undefined))
  child.stdout.on('error', onTransportError)
  child.stderr.on('error', onTransportError)
  child.on('error', onTransportError)
  child.on('close', code => {
    closed.resolve()
    if (settled) return
    if (terminalError !== undefined) {
      if (group === undefined) rejectOnce(terminalError)
      return
    }
    settled = true
    cleanup()
    resolve({
      exitCode: code ?? 1,
      stdout: Buffer.concat(stdout).toString('utf8'),
      stderr: Buffer.concat(stderr).toString('utf8'),
    })
  })
})

function recordPayload(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

function parsePayload(stdout: string): Record<string, unknown> | undefined {
  try {
    return recordPayload(JSON.parse(stdout))
  } catch {
    return undefined
  }
}

async function wait(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (delayMs <= 0) return
  await new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new LoopXCliError('aborted', 'operation was cancelled', false))
      return
    }
    const timer = setTimeout(done, delayMs)
    function done(): void {
      signal?.removeEventListener('abort', aborted)
      resolve()
    }
    function aborted(): void {
      clearTimeout(timer)
      signal?.removeEventListener('abort', aborted)
      reject(new LoopXCliError('aborted', 'operation was cancelled', false))
    }
    signal?.addEventListener('abort', aborted, { once: true })
  })
}

export interface JsonCommandOptions extends FileRunOptions {
  readonly runner?: FileRunner | undefined
  readonly attempts?: number | undefined
  readonly retryDelaysMs?: readonly number[] | undefined
  readonly validate?: ((payload: Record<string, unknown>) => boolean) | undefined
}

export type JsonMutationCommandOptions = Omit<
  JsonCommandOptions,
  'attempts' | 'retryDelaysMs' | 'signal'
>

/**
 * Execute one mutation exactly once. Caller cancellation is intentionally not
 * accepted: admission is fenced before this function is called, and the real
 * runner waits for child close after every forced termination.
 */
export async function runJsonMutationCommand(
  command: LoopXCommand,
  args: readonly string[],
  options: JsonMutationCommandOptions = {},
): Promise<Record<string, unknown>> {
  const runner = options.runner ?? runFile
  let result: FileResult
  try {
    result = await runner(
      command.file,
      [...command.prefix, ...args],
      {
        cwd: options.cwd,
        env: options.env,
        timeoutMs: options.timeoutMs,
        maxOutputBytes: options.maxOutputBytes,
      },
    )
  } catch (error: unknown) {
    if (error instanceof LoopXCliError) {
      throw error.retryable
        ? new LoopXCliError(error.kind, error.message, false, error.exitCode, error.diagnostics)
        : error
    }
    throw new LoopXCliError('transport', 'LoopX mutation command failed to execute', false)
  }

  const payload = parsePayload(result.stdout)
  if (result.exitCode !== 0) {
    throw new LoopXCliError(
      payload === undefined ? 'exit' : 'typed_failure',
      'LoopX mutation command did not verify',
      false,
      result.exitCode,
    )
  }
  if (payload === undefined) {
    throw new LoopXCliError(
      'invalid_json',
      'LoopX mutation command returned invalid JSON',
      false,
    )
  }
  if (options.validate !== undefined && !options.validate(payload)) {
    throw new LoopXCliError(
      'invalid_schema',
      'LoopX mutation command returned an incompatible response schema',
      false,
    )
  }
  return payload
}

/** Execute fixed argv and retry only transport/timeout/untyped-exit failures. */
export async function runJsonCommand(
  command: LoopXCommand,
  args: readonly string[],
  options: JsonCommandOptions = {},
): Promise<Record<string, unknown>> {
  const runner = options.runner ?? runFile
  const attempts = Math.max(1, options.attempts ?? 1)
  const retryDelays = options.retryDelaysMs ?? [200, 750]
  let lastError: LoopXCliError | undefined

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const cwdExists = spawnCwdExists(options.cwd)
    try {
      const result = await runner(
        command.file,
        [...command.prefix, ...args],
        options,
      )
      const payload = parsePayload(result.stdout)
      if (payload !== undefined) {
        if (options.validate !== undefined && !options.validate(payload)) {
          throw new LoopXCliError(
            'invalid_schema',
            'LoopX returned an incompatible response schema',
            false,
          )
        }
        return payload
      }
      if (result.exitCode === 0) {
        throw new LoopXCliError('invalid_json', 'LoopX returned invalid JSON', false)
      }
      throw new LoopXCliError(
        'exit',
        'LoopX exited without a typed response',
        true,
        result.exitCode,
      )
    } catch (error: unknown) {
      const failure = error instanceof LoopXCliError
        ? error
        : new LoopXCliError('transport', 'LoopX command failed to execute', true)
      failure.diagnostics.cwdExists = cwdExists
      failure.diagnostics.candidate ??= command.candidate
      lastError = failure
      if (!failure.retryable || attempt + 1 >= attempts) throw failure
      await wait(retryDelays[attempt] ?? retryDelays.at(-1) ?? 0, options.signal)
    }
  }
  throw lastError ?? new LoopXCliError('transport', 'LoopX command failed', true)
}

export function supportedLoopXVersion(
  stdout: string, minimumVersion = MINIMUM_LOOPX_VERSION,
): string | undefined {
  const value = stdout.trim().split(/\r?\n/u)[0]?.trim()
  if (!value || value.length > 120) return undefined
  // LoopX reports its Python package version. Compare the release tuple and
  // recognize its PEP 440 pre/dev/post/local suffixes without accepting prose.
  const parsed = /^loopx (\d+)\.(\d+)\.(\d+)((?:a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?(?:\+[A-Za-z0-9.-]+)?$/u.exec(value)
  if (!parsed) return undefined
  const release = parsed.slice(1, 4).map(Number)
  if (release.some(component => !Number.isSafeInteger(component))) return undefined
  const floor = minimumVersion.split('.').map(Number)
  for (let index = 0; index < floor.length; index += 1) {
    const component = release[index]!
    if (component < floor[index]!) return undefined
    if (component > floor[index]!) return value
  }
  return !parsed[4] && (!parsed[6] || parsed[5]) ? value : undefined
}

function shellWord(value: string): string {
  return /^[A-Za-z0-9_./:-]+$/u.test(value)
    ? value
    : `'${value.replaceAll("'", `'"'"'`)}'`
}

/** Resolve the console script first, then the stable Python module fallback. */
export async function resolveLoopXCommand(
  options: ResolveLoopXCommandOptions = {},
): Promise<LoopXCommand> {
  const runner = options.runner ?? runFile
  const configured = options.env?.LOOPX_BIN ?? process.env.LOOPX_BIN
  const configuredPython = options.env?.PYTHON_BIN ?? process.env.PYTHON_BIN
  // A python.org Windows install registers `python`, not `python3`, and the
  // Windows `python3` name can be the WindowsApps alias that never starts
  // Python, so the module fallback tries both spellings in that order.
  const pythonNames = configuredPython === undefined
    ? ['python3', 'python']
    : [configuredPython]
  const managedLauncher = options.managedLauncher
  const managedCandidates: Array<Omit<LoopXCommand, 'version'>> = configured
    || managedLauncher === undefined
    ? []
    : [...new Set(managedLauncher.pythonBins)].map(pythonBin => ({
        file: pythonBin,
        prefix: [managedLauncher.path],
        skillCommand: [
          shellWord(pythonBin),
          shellWord(managedLauncher.path),
        ].join(' '),
      }))
  const candidates: Array<Omit<LoopXCommand, 'version'>> = configured
    ? [{ file: configured, prefix: [], skillCommand: '"$LOOPX_BIN"' }]
    : [
        ...managedCandidates,
        { file: 'loopx', prefix: [], skillCommand: 'loopx' },
        ...pythonNames.map(pythonName => ({
          file: pythonName,
          prefix: ['-m', 'loopx.cli'],
          skillCommand: `${shellWord(pythonName)} -m loopx.cli`,
        })),
      ]

  const probes: LoopXCliError[] = []
  for (const candidate of candidates) {
    const label = configured ? 'configured-command'
      : managedCandidates.includes(candidate) ? 'managed-launcher'
        : candidate.file === 'loopx' ? 'path-loopx' : 'python-module'
    const cwdExists = spawnCwdExists()
    try {
      const result = await runner(
        candidate.file,
        [...candidate.prefix, '--version'],
        {
          env: options.env,
          signal: options.signal,
          timeoutMs: 5_000,
          maxOutputBytes: 16 * 1024,
        },
      )
      if (options.signal?.aborted) {
        throw new LoopXCliError('aborted', 'operation was cancelled', false)
      }
      const version = result.exitCode === 0 ? supportedLoopXVersion(result.stdout) : undefined
      if (version) return { ...candidate, version, candidate: label }
      throw new LoopXCliError(
        result.exitCode === 0 ? 'invalid_schema' : 'exit',
        'LoopX version probe did not verify', false, result.exitCode,
      )
    } catch (error: unknown) {
      if (options.signal?.aborted) {
        throw new LoopXCliError('aborted', 'operation was cancelled', false)
      }
      if (isUnsafeToContinue(error)) throw error
      const failure = error instanceof LoopXCliError ? error
        : new LoopXCliError('transport', 'LoopX version probe failed to execute', false)
      probes.push(new LoopXCliError(failure.kind, 'version probe failed', false, failure.exitCode, {
        osCode: failure.diagnostics.osCode, candidate: label, cwdExists,
      }))
    }
  }
  const last = probes.at(-1)
  throw new LoopXCliError('missing', `LoopX CLI ${MINIMUM_LOOPX_VERSION} or newer is unavailable`, false, last?.exitCode, {
    ...last?.diagnostics, causeKind: last?.kind, probes,
  })
}
