import { randomUUID } from 'node:crypto'
import { join, resolve } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { CommandResult } from '@deepseek-ai/dsh-commands'
import type { UserMessage } from '@deepseek-ai/dsh-session'
import {
  LoopXCliError,
  resolveLoopXCommand,
  runFile,
  runJsonCommand,
} from './cli.ts'
import type { LoopXCliErrorKind, LoopXCommand } from './cli.ts'
import {
  configuredPluginPython,
  MANAGED_LAUNCHER_NAME,
  MANAGED_SITE_PACKAGES_NAME,
  pluginAgentsHome,
  pluginPythonCandidates,
  pluginRuntimeDir,
  resolvePluginLoopXCommand,
} from './managed-runtime.ts'
import type { LoopXRuntimeOptions } from './managed-runtime.ts'

export const name = 'dsh-loopx-init-command'
export const inject = ['commands']

const HOST_SURFACE = 'deepseek-harness-native'
const WORKFLOW_SCHEMA = 'loopx_workflow_skill_install_v0'
const INIT_SOURCE_ID = 'dsh-loopx-plugin/init-command'
// DSH 0.2 requires each producer to register its own message source. This
// distinguishes initialization notices from user intent and continuation.
declare module '@deepseek-ai/dsh-llm' {
  interface MessageSourceMap {
    'loopx-initialization': {
      readonly kind: 'loopx-initialization'
      readonly plugin: typeof INIT_SOURCE_ID
    }
  }
}
const MAX_FOLLOWUP_TEXT_CHARS = 800
const PYTHON_VERSION_PROBE = 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
const PIP_VERSION_ARGS = Object.freeze(['-m', 'pip', '--version'])
// 0.5.4 is the first release that discovers wheel data-files after the
// plugin's pip --target installation on Linux (the DSH managed-runtime path).
const LOOPX_REQUIREMENT = 'loopx>=0.5.4'
const MANAGED_LAUNCHER_SOURCE = [
  'from pathlib import Path',
  'import runpy',
  'import sys',
  `sys.path.insert(0, str(Path(__file__).with_name(${JSON.stringify(MANAGED_SITE_PACKAGES_NAME)})))`,
  'runpy.run_module("loopx.cli", run_name="__main__")',
  '',
].join('\n')
const MANAGED_LAUNCHER_WRITER = [
  'from pathlib import Path',
  'import sys',
  'root = Path(sys.argv[1])',
  'root.mkdir(parents=True, exist_ok=True)',
  `root.joinpath(${JSON.stringify(MANAGED_LAUNCHER_NAME)}).write_text(sys.argv[2], encoding="utf-8")`,
].join('; ')
const PACKAGED_SKILL_IDS = Object.freeze([
  'loopx-project',
  'loopx-pr-program',
  'loopx-pr-review',
  'loopx-doc-registry',
  'loopx-self-repair',
])

type PackagedSkillStatus = 'created' | 'updated' | 'unchanged'
type EntrySkillStatus = PackagedSkillStatus | 'upgraded_legacy_managed'

export type LoopXInitStage = 'probe' | 'install_cli' | 'install_skills' | 'readback'
export type LoopXInitCauseKind = LoopXCliErrorKind | 'incompatible' | 'readback_mismatch'

export class LoopXInitError extends Error {
  constructor(
    readonly stage: LoopXInitStage,
    message: string,
    readonly causeKind?: LoopXInitCauseKind | undefined,
  ) {
    super(message)
    this.name = 'LoopXInitError'
  }
}

export interface LoopXInitOptions extends LoopXRuntimeOptions {
  readonly skillsDir?: string | undefined
}

export interface LoopXInitSummary {
  readonly cliVersion: string
  readonly cliInstalled: boolean
  readonly skillsInstalled: true
  readonly skillsChanged: boolean
  readonly hostSurface: typeof HOST_SURFACE
}

export type LoopXBootstrapStatus = Readonly<
  | { readonly state: 'ready' }
  | {
      readonly state: 'failed'
      readonly stage: LoopXInitStage | 'unknown'
      readonly causeKind: LoopXInitCauseKind | 'unknown'
    }
>

declare module '@deepseek-ai/cordis' {
  interface Context {
    readonly loopxBootstrap: LoopXBootstrapStatus
  }
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

function packagedSkillStatus(value: unknown): value is PackagedSkillStatus {
  return value === 'created' || value === 'updated' || value === 'unchanged'
}

function entrySkillStatus(value: unknown): value is EntrySkillStatus {
  return packagedSkillStatus(value) || value === 'upgraded_legacy_managed'
}

function installedSkillsChanged(payload: Record<string, unknown>): boolean | undefined {
  const installed = record(payload.installed)
  const entry = record(payload.entry)
  if (installed === undefined || entry === undefined) return undefined
  const statuses = Object.values(installed)
  if (statuses.length === 0 || !statuses.every(packagedSkillStatus)) return undefined
  if (!PACKAGED_SKILL_IDS.every(skillId => packagedSkillStatus(installed[skillId]))) {
    return undefined
  }
  if (!entrySkillStatus(entry.status)) return undefined
  return statuses.some(status => status !== 'unchanged')
    || entry.status !== 'unchanged'
}

function workflowArgs(
  command: LoopXCommand,
  skillsDir: string,
  mode: 'inspect' | 'install',
): string[] {
  return [
    '--format',
    'json',
    'workflow-skills',
    ...(mode === 'install' ? ['--install'] : []),
    '--skills-dir',
    skillsDir,
    '--host-surface',
    HOST_SURFACE,
    '--cli-bin',
    command.skillCommand,
  ]
}

function workflowPayload(payload: Record<string, unknown>): boolean {
  return payload.schema_version === WORKFLOW_SCHEMA
}

async function compatible(
  command: LoopXCommand,
  skillsDir: string,
  options: LoopXInitOptions,
): Promise<boolean> {
  try {
    const payload = await runJsonCommand(
      command,
      workflowArgs(command, skillsDir, 'inspect'),
      {
        runner: options.runner,
        signal: options.signal,
        env: options.env,
        attempts: 1,
        validate: workflowPayload,
      },
    )
    return payload.ok === true
      && payload.operation === 'inspect'
      && payload.host_surface === HOST_SURFACE
      && typeof payload.install_required === 'boolean'
  } catch (error: unknown) {
    if (error instanceof LoopXCliError && error.kind === 'aborted') throw error
    return false
  }
}

function configuredPython(options: LoopXInitOptions): string | undefined {
  return configuredPluginPython(options)
}

function pythonCandidates(options: LoopXInitOptions): readonly string[] {
  return pluginPythonCandidates(options)
}

function withPython(
  options: LoopXInitOptions,
  python: string,
): LoopXInitOptions {
  return {
    ...options,
    env: { ...(options.env ?? process.env), PYTHON_BIN: python },
    pythonBin: python,
  }
}

async function resolveInstallPython(options: LoopXInitOptions): Promise<string> {
  const runner = options.runner ?? runFile
  const explicit = configuredPython(options)
  const candidates = pythonCandidates(options)
  for (const python of candidates) {
    try {
      const result = await runner(
        python,
        ['-c', PYTHON_VERSION_PROBE],
        {
          env: options.env,
          signal: options.signal,
          timeoutMs: 5_000,
          maxOutputBytes: 16 * 1024,
        },
      )
      if (result.exitCode !== 0) continue
      const pipResult = await runner(
        python,
        PIP_VERSION_ARGS,
        {
          env: options.env,
          signal: options.signal,
          timeoutMs: 5_000,
          maxOutputBytes: 16 * 1024,
        },
      )
      if (pipResult.exitCode === 0) return python
    } catch (error: unknown) {
      if (error instanceof LoopXCliError && error.kind === 'aborted') throw error
    }
  }
  throw new LoopXInitError(
    'install_cli',
    explicit === undefined
      ? 'Python 3.11 or newer with pip could not be found'
      : 'The configured Python interpreter is unavailable, older than 3.11, or cannot run pip',
    'missing',
  )
}

interface ManagedLoopXRuntime {
  readonly pythonBin: string
  readonly launcherPath: string
}

async function installCli(
  options: LoopXInitOptions,
  runtimeDir: string,
): Promise<ManagedLoopXRuntime> {
  const runner = options.runner ?? runFile
  const python = await resolveInstallPython(options)
  const sitePackages = join(runtimeDir, MANAGED_SITE_PACKAGES_NAME)
  const launcherPath = join(runtimeDir, MANAGED_LAUNCHER_NAME)
  let installResult
  try {
    installResult = await runner(
      python,
      [
        '-m', 'pip', 'install',
        '--disable-pip-version-check',
        '--no-input',
        '--upgrade',
        '--target', sitePackages,
        LOOPX_REQUIREMENT,
      ],
      {
        env: options.env,
        signal: options.signal,
        timeoutMs: 120_000,
        maxOutputBytes: 1024 * 1024,
      },
    )
  } catch (error: unknown) {
    if (error instanceof LoopXCliError && error.kind === 'aborted') throw error
    const kind = error instanceof LoopXCliError ? error.kind : 'transport'
    throw new LoopXInitError('install_cli', 'LoopX CLI installation failed', kind)
  }
  if (installResult.exitCode !== 0) {
    throw new LoopXInitError('install_cli', 'LoopX CLI installation failed', 'exit')
  }
  let launcherResult
  try {
    launcherResult = await runner(
      python,
      ['-c', MANAGED_LAUNCHER_WRITER, runtimeDir, MANAGED_LAUNCHER_SOURCE],
      {
        env: options.env,
        signal: options.signal,
        timeoutMs: 5_000,
        maxOutputBytes: 16 * 1024,
      },
    )
  } catch (error: unknown) {
    if (error instanceof LoopXCliError && error.kind === 'aborted') throw error
    const kind = error instanceof LoopXCliError ? error.kind : 'transport'
    throw new LoopXInitError('install_cli', 'LoopX CLI launcher setup failed', kind)
  }
  if (launcherResult.exitCode !== 0) {
    throw new LoopXInitError('install_cli', 'LoopX CLI launcher setup failed', 'exit')
  }
  return { pythonBin: python, launcherPath }
}

/** Install/upgrade LoopX once when needed, then install and verify DSH skills. */
export async function initializeLoopX(options: LoopXInitOptions = {}): Promise<LoopXInitSummary> {
  const resolvedAgentsHome = pluginAgentsHome(options)
  const skillsDir = resolve(options.skillsDir ?? join(resolvedAgentsHome, 'skills'))
  const runtimeDir = pluginRuntimeDir(options)
  const initialPython = configuredPython(options)
  let effectiveOptions = initialPython === undefined
    ? options
    : withPython(options, initialPython)
  let command: LoopXCommand | undefined
  try {
    command = await resolvePluginLoopXCommand(effectiveOptions)
  } catch (error: unknown) {
    if (error instanceof LoopXCliError && error.kind === 'aborted') throw error
  }

  let cliInstalled = false
  if (command === undefined || !(await compatible(command, skillsDir, effectiveOptions))) {
    const managedRuntime = await installCli(effectiveOptions, runtimeDir)
    effectiveOptions = withPython(options, managedRuntime.pythonBin)
    cliInstalled = true
    try {
      command = await resolveLoopXCommand({
        ...effectiveOptions,
        managedLauncher: {
          path: managedRuntime.launcherPath,
          pythonBins: [managedRuntime.pythonBin],
        },
      })
    } catch (error: unknown) {
      if (error instanceof LoopXCliError && error.kind === 'aborted') throw error
      const kind = error instanceof LoopXCliError ? error.kind : 'transport'
      throw new LoopXInitError(
        'probe',
        'LoopX was installed but no compatible CLI could be resolved',
        kind,
      )
    }
    if (!(await compatible(command, skillsDir, effectiveOptions))) {
      throw new LoopXInitError(
        'probe',
        'The installed LoopX CLI does not support the DSH-native skill contract',
        'incompatible',
      )
    }
  }

  let installed: Record<string, unknown>
  try {
    installed = await runJsonCommand(
      command,
      workflowArgs(command, skillsDir, 'install'),
      {
        runner: effectiveOptions.runner,
        signal: effectiveOptions.signal,
        env: effectiveOptions.env,
        attempts: 1,
        timeoutMs: 60_000,
        validate: workflowPayload,
      },
    )
  } catch (error: unknown) {
    if (error instanceof LoopXCliError && error.kind === 'aborted') throw error
    const kind = error instanceof LoopXCliError ? error.kind : 'transport'
    throw new LoopXInitError('install_skills', 'LoopX skill installation failed', kind)
  }
  if (installed.ok !== true || installed.operation !== 'install'
    || installed.host_surface !== HOST_SURFACE) {
    throw new LoopXInitError(
      'install_skills',
      'LoopX did not confirm the DSH-native skill installation',
      'typed_failure',
    )
  }
  const skillsChanged = installedSkillsChanged(installed)
  if (skillsChanged === undefined) {
    throw new LoopXInitError(
      'install_skills',
      'LoopX returned incomplete DSH-native skill mutation status',
      'typed_failure',
    )
  }

  let readback: Record<string, unknown>
  try {
    readback = await runJsonCommand(
      command,
      workflowArgs(command, skillsDir, 'inspect'),
      {
        runner: effectiveOptions.runner,
        signal: effectiveOptions.signal,
        env: effectiveOptions.env,
        attempts: 1,
        validate: workflowPayload,
      },
    )
  } catch (error: unknown) {
    if (error instanceof LoopXCliError && error.kind === 'aborted') throw error
    const kind = error instanceof LoopXCliError ? error.kind : 'transport'
    throw new LoopXInitError('readback', 'LoopX skill readback failed', kind)
  }
  if (readback.ok !== true || readback.operation !== 'inspect'
    || readback.host_surface !== HOST_SURFACE
    || readback.install_required !== false) {
    throw new LoopXInitError(
      'readback',
      'LoopX skills were not verified after installation',
      'readback_mismatch',
    )
  }

  return {
    cliVersion: command.version,
    cliInstalled,
    skillsInstalled: true,
    skillsChanged,
    hostSurface: HOST_SURFACE,
  }
}

function recoveryForStage(stage: LoopXInitStage): string {
  return stage === 'install_cli'
    ? 'Verify Python 3.11+ with pip or set `PYTHON_BIN`, then retry `/loopx-init`.'
    : stage === 'probe'
      ? 'Verify `loopx --version`, then retry `/loopx-init`.'
      : 'Run `loopx workflow-skills --help` for diagnostics, then retry `/loopx-init`.'
}

function commandFailure(error: unknown): CommandResult {
  if (error instanceof LoopXCliError && error.kind === 'aborted') {
    return { kind: 'error', text: 'LOOPX_INIT_CANCELLED: initialization was cancelled.' }
  }
  if (error instanceof LoopXInitError) {
    return {
      kind: 'error',
      text: [
        `LOOPX_INIT_FAILED: stage=${error.stage}; kind=${error.causeKind ?? 'unknown'}.`,
        `${error.message}.`,
        recoveryForStage(error.stage),
      ].join(' '),
    }
  }
  return {
    kind: 'error',
    text: 'LOOPX_INIT_FAILED: unexpected initialization failure.',
  }
}

function successResult(result: LoopXInitSummary): CommandResult {
  return {
    kind: 'success',
    text: [
      `LoopX ready (${result.cliVersion}).`,
      result.cliInstalled ? 'CLI installed or upgraded.' : 'CLI already compatible.',
      result.skillsChanged
        ? 'DSH LoopX skills installed or updated and verified.'
        : 'DSH LoopX skills already current and verified.',
      'No DSH restart is required; use the `loopx` skill with your task.',
    ].join(' '),
  }
}

function safeVersion(version: string): string {
  return /^loopx [A-Za-z0-9][A-Za-z0-9._+!-]{0,63}$/u.test(version)
    ? version
    : 'loopx (compatible version verified)'
}

function followupMessage(text: string): UserMessage {
  const bounded = text.length <= MAX_FOLLOWUP_TEXT_CHARS
    ? text
    : `${text.slice(0, MAX_FOLLOWUP_TEXT_CHARS - 1)}…`
  const content: UserMessage['content'] = Object.freeze([
    Object.freeze({ type: 'text' as const, text: bounded }),
  ]) as UserMessage['content']
  return Object.freeze({
    id: randomUUID() as UserMessage['id'],
    role: 'user' as const,
    content,
    source: Object.freeze({ kind: 'loopx-initialization' as const, plugin: INIT_SOURCE_ID }),
  })
}

function startFollowup(): UserMessage {
  return followupMessage([
    'LoopX initialization has started.',
    'Briefly welcome the user to LoopX and say that the LoopX CLI and DSH workflow skills are being checked or installed.',
    'Do not call tools, run commands, claim initialization is complete, or add diagnostics.',
  ].join(' '))
}

function successFollowup(result: LoopXInitSummary): UserMessage {
  return followupMessage([
    'LoopX initialization finished successfully.',
    `Report these authoritative facts briefly: version ${safeVersion(result.cliVersion)};`,
    result.cliInstalled ? 'the CLI was installed or upgraded;' : 'the CLI was already compatible;',
    result.skillsChanged
      ? 'DSH workflow skills were installed or updated, verified, and loaded without a restart.'
      : 'DSH workflow skills were already current and verified; no DSH restart is required.',
    'Do not call tools, run commands, reinstall anything, or add diagnostics.',
  ].join(' '))
}

function failureFollowup(error: unknown): UserMessage {
  if (error instanceof LoopXInitError) {
    return followupMessage([
      'LoopX initialization failed.',
      `Report briefly that the safe failure stage is ${error.stage} and the cause kind is ${error.causeKind ?? 'unknown'}.`,
      recoveryForStage(error.stage),
      'Do not claim LoopX is ready or that DSH should restart. Do not call tools, run commands, reinstall, or add diagnostics.',
    ].join(' '))
  }
  return followupMessage([
    'LoopX initialization failed unexpectedly.',
    'Briefly tell the user to read the authoritative native command result and retry `/loopx-init`.',
    'Do not claim LoopX is ready or that DSH should restart. Do not call tools, run commands, reinstall, or add diagnostics.',
  ].join(' '))
}

function queueFollowup(
  agent: Agent,
  message: UserMessage,
  phase: 'start' | 'complete',
  warn: (message: string) => void,
): void {
  try {
    agent.followup(message)
  } catch {
    try {
      warn(`dsh-loopx-init-command: could not queue ${phase} followup`)
    } catch {
      // Diagnostics must never become part of the initialization outcome.
    }
  }
}

function cancelled(error: unknown, signal: AbortSignal): boolean {
  return signal.aborted || (error instanceof LoopXCliError && error.kind === 'aborted')
}

/** Register the explicit repair command without changing LoopX readiness. */
export function registerLoopXInitCommand(
  ctx: Context,
  options: LoopXInitOptions = {},
): void {
  ctx.commands.register({
    name: 'loopx-init',
    description: 'install or upgrade LoopX and install the DSH LoopX skills',
    recordInput: false,
    async handler(invocation): Promise<CommandResult> {
      if (invocation.rawInput.trim().length > 0) {
        return { kind: 'error', text: 'Usage: /loopx-init' }
      }
      const warn = (message: string): void => { ctx.logger.warn(message) }
      queueFollowup(invocation.agent, startFollowup(), 'start', warn)
      try {
        const result = await initializeLoopX({ ...options, signal: invocation.signal })
        const commandResult = successResult(result)
        if (!invocation.signal.aborted) {
          queueFollowup(invocation.agent, successFollowup(result), 'complete', warn)
        }
        return commandResult
      } catch (error: unknown) {
        const commandResult = commandFailure(error)
        if (!cancelled(error, invocation.signal)) {
          queueFollowup(invocation.agent, failureFollowup(error), 'complete', warn)
        }
        return commandResult
      }
    },
  })
}

function automaticFailure(error: unknown): string {
  if (error instanceof LoopXInitError) {
    return [
      'dsh-loopx-init-command: automatic initialization failed;',
      `stage=${error.stage}; kind=${error.causeKind ?? 'unknown'};`,
      '/loopx-init remains available for repair',
    ].join(' ')
  }
  if (error instanceof LoopXCliError && error.kind === 'aborted') {
    return 'dsh-loopx-init-command: automatic initialization was cancelled; /loopx-init remains available for repair'
  }
  return 'dsh-loopx-init-command: automatic initialization failed unexpectedly; /loopx-init remains available for repair'
}

function bootstrapFailureStatus(error: unknown): LoopXBootstrapStatus {
  if (error instanceof LoopXInitError) {
    return Object.freeze({
      state: 'failed',
      stage: error.stage,
      causeKind: error.causeKind ?? 'unknown',
    })
  }
  if (error instanceof LoopXCliError) {
    return Object.freeze({
      state: 'failed',
      stage: 'unknown',
      causeKind: error.kind,
    })
  }
  return Object.freeze({ state: 'failed', stage: 'unknown', causeKind: 'unknown' })
}

/**
 * Make the installed plugin ready before DSH finishes loading this row.
 *
 * Startup failures are isolated to LoopX: DSH still boots and the registered
 * command remains as an explicit retry surface. The awaited happy path keeps a
 * freshly installed profile from racing its first `skill.list` readback.
 */
export async function apply(ctx: Context, options: LoopXInitOptions = {}): Promise<void> {
  registerLoopXInitCommand(ctx, options)
  let status: LoopXBootstrapStatus
  try {
    await initializeLoopX(options)
    status = Object.freeze({ state: 'ready' })
  } catch (error: unknown) {
    status = bootstrapFailureStatus(error)
    try {
      ctx.logger.warn(automaticFailure(error))
    } catch {
      // Diagnostics must never turn an isolated LoopX bootstrap failure into a DSH startup failure.
    }
  }
  ctx.reflect.provide('loopxBootstrap', status)
}
