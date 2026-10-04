import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it, onTestFinished } from 'vitest'
import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type {
  CommandDefinition,
  CommandInvocation,
  CommandResult,
} from '@deepseek-ai/dsh-commands'
import type { UserMessage } from '@deepseek-ai/dsh-session'
import { LoopXCliError } from '../src/cli.ts'
import type { FileRunner } from '../src/cli.ts'
import {
  apply,
  initializeLoopX,
  registerLoopXInitCommand,
} from '../src/init-command.ts'
import type { LoopXInitOptions } from '../src/init-command.ts'
import { resolvePluginLoopXCommand } from '../src/managed-runtime.ts'

const hostSurface = 'deepseek-harness-native'
const initSource = 'dsh-loopx-plugin/init-command'
const packagedSkillIds = [
  'loopx-project',
  'loopx-pr-program',
  'loopx-pr-review',
  'loopx-doc-registry',
  'loopx-self-repair',
] as const

type PackagedSkillStatus = 'created' | 'updated' | 'unchanged'
type EntrySkillStatus = PackagedSkillStatus | 'upgraded_legacy_managed'

function installState(
  packagedStatus: PackagedSkillStatus = 'unchanged',
  entryStatus: EntrySkillStatus = 'unchanged',
): Record<string, unknown> {
  return {
    installed: Object.fromEntries(packagedSkillIds.map(skillId => [skillId, packagedStatus])),
    entry: { status: entryStatus },
  }
}

function workflowPayload(
  operation: 'inspect' | 'install',
  installRequired = false,
  actualInstallState: Record<string, unknown> = installState(),
): string {
  return JSON.stringify({
    ok: true,
    schema_version: 'loopx_workflow_skill_install_v0',
    operation,
    host_surface: hostSurface,
    ...(operation === 'inspect'
      ? { install_required: installRequired }
      : actualInstallState),
  })
}

function messageText(message: UserMessage): string {
  return message.content
    .filter(block => block.type === 'text')
    .map(block => block.text)
    .join('')
}

interface CommandHarness {
  readonly messages: UserMessage[]
  readonly warnings: string[]
  execute(rawInput?: string, signal?: AbortSignal): Promise<CommandResult>
}

function commandHarness(
  options: LoopXInitOptions,
  onFollowup?: ((message: UserMessage, attempt: number) => void) | undefined,
): CommandHarness {
  const messages: UserMessage[] = []
  const warnings: string[] = []
  let definition: CommandDefinition | undefined
  const ctx = {
    commands: {
      register(candidate: CommandDefinition) {
        definition = candidate
        return () => {}
      },
    },
    logger: { warn(message: string) { warnings.push(message) } },
  } as unknown as Context
  registerLoopXInitCommand(ctx, options)
  if (definition === undefined) throw new Error('loopx-init command was not registered')
  const handler = definition.handler
  const agent = {
    followup(message: UserMessage) {
      messages.push(message)
      onFollowup?.(message, messages.length)
    },
  } as unknown as Agent
  return {
    messages,
    warnings,
    execute(rawInput = '', signal = new AbortController().signal) {
      return Promise.resolve(handler({ rawInput, signal, agent } as CommandInvocation))
    },
  }
}

function successfulRunner(options: {
  readonly initiallyInstalled?: boolean | undefined
  readonly packagedStatus?: PackagedSkillStatus | undefined
  readonly entryStatus?: EntrySkillStatus | undefined
  readonly events?: string[] | undefined
} = {}): { readonly runner: FileRunner, readonly calls: string[][] } {
  const calls: string[][] = []
  let cliAvailable = options.initiallyInstalled ?? true
  let inspectCalls = 0
  const runner: FileRunner = async (_file, args) => {
    calls.push([...args])
    if (args[0] === '-c') {
      return { exitCode: 0, stdout: '', stderr: '' }
    }
    if (args.join(' ') === '-m pip --version') {
      return { exitCode: 0, stdout: 'pip fixture', stderr: '' }
    }
    if (args.slice(0, 3).join(' ') === '-m pip install') {
      options.events?.push('install-cli')
      cliAvailable = true
      return { exitCode: 0, stdout: 'installed', stderr: '' }
    }
    if (args.at(-1) === '--version') {
      options.events?.push('probe')
      if (!cliAvailable) throw new LoopXCliError('missing', 'missing', false)
      return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
    }
    if (args.includes('--install')) {
      options.events?.push('install-skills')
      return {
        exitCode: 0,
        stdout: workflowPayload('install', false, installState(
          options.packagedStatus,
          options.entryStatus,
        )),
        stderr: '',
      }
    }
    inspectCalls += 1
    options.events?.push(inspectCalls === 1 ? 'inspect-before' : 'readback')
    return { exitCode: 0, stdout: workflowPayload('inspect', false), stderr: '' }
  }
  return { runner, calls }
}

async function pluginHarness(options: LoopXInitOptions): Promise<{
  readonly commands: Map<string, CommandDefinition>
  readonly services: Map<string, unknown>
  readonly warnings: string[]
}> {
  const commands = new Map<string, CommandDefinition>()
  const services = new Map<string, unknown>()
  const warnings: string[] = []
  const ctx = {
    commands: {
      register(definition: CommandDefinition) {
        commands.set(definition.name, definition)
        return () => {}
      },
    },
    reflect: {
      provide(name: string, value: unknown) {
        services.set(name, value)
        return () => { services.delete(name) }
      },
    },
    logger: { warn(message: string) { warnings.push(message) } },
  } as unknown as Context
  await apply(ctx, options)
  return { commands, services, warnings }
}

describe('automatic DSH bootstrap', () => {
  it('makes LoopX ready before the plugin row finishes loading', async () => {
    const events: string[] = []
    const { runner, calls } = successfulRunner({
      packagedStatus: 'created',
      events,
    })

    const harness = await pluginHarness({ runner, skillsDir: '/fixture/skills' })

    expect([...harness.commands.keys()]).toEqual(['loopx-init'])
    expect(harness.services.get('loopxBootstrap')).toEqual({ state: 'ready' })
    expect(harness.warnings).toEqual([])
    expect(events).toEqual([
      'probe',
      'inspect-before',
      'install-skills',
      'readback',
    ])
    expect(calls.filter(args => args.includes('--install'))).toHaveLength(1)
  })

  it('keeps DSH bootable and the repair command available after a safe startup failure', async () => {
    const privateFailure = '/fixture/private/automatic-bootstrap-failure'
    const healthy = successfulRunner({ packagedStatus: 'created' })
    let fail = true
    const runner: FileRunner = async (file, args, options) => {
      if (fail) throw new LoopXCliError('transport', privateFailure, true)
      return healthy.runner(file, args, options)
    }

    const harness = await pluginHarness({ runner, skillsDir: '/fixture/skills' })

    expect([...harness.commands.keys()]).toEqual(['loopx-init'])
    expect(harness.services.get('loopxBootstrap')).toEqual({
      state: 'failed', stage: 'install_cli', causeKind: 'missing',
    })
    expect(harness.warnings).toHaveLength(1)
    expect(harness.warnings[0]).toContain('stage=install_cli; kind=missing')
    expect(harness.warnings[0]).toContain('/loopx-init remains available for repair')
    expect(harness.warnings[0]).not.toContain(privateFailure)

    fail = false
    const messages: UserMessage[] = []
    const command = harness.commands.get('loopx-init') as CommandDefinition
    const result = await command.handler({
      rawInput: '',
      signal: new AbortController().signal,
      agent: { followup(message: UserMessage) { messages.push(message) } } as unknown as Agent,
    } as CommandInvocation)
    expect(result.kind).toBe('success')
    expect(messages).toHaveLength(2)
  })
})

describe('/loopx-init implementation', () => {
  it('keeps a compatible CLI and installs plus verifies the DSH skills', async () => {
    const calls: string[][] = []
    let inspectCount = 0
    const runner: FileRunner = async (_file, args) => {
      calls.push([...args])
      if (args.at(-1) === '--version') {
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      if (args.includes('workflow-skills')) {
        if (args.includes('--install')) {
          return { exitCode: 0, stdout: workflowPayload('install'), stderr: '' }
        }
        inspectCount += 1
        return {
          exitCode: 0,
          stdout: workflowPayload('inspect', inspectCount === 1),
          stderr: '',
        }
      }
      throw new Error(`unexpected argv: ${args.join(' ')}`)
    }

    const result = await initializeLoopX({
      runner,
      skillsDir: '/fixture/.agents/skills',
    })

    expect(result).toEqual({
      cliVersion: 'loopx 1.2.4',
      cliInstalled: false,
      skillsInstalled: true,
      skillsChanged: false,
      hostSurface,
    })
    expect(calls.some(args => args.slice(0, 3).join(' ') === '-m pip install')).toBe(false)
    expect(calls.filter(args => args.includes('--install'))).toHaveLength(1)
    expect(calls.at(-1)).toContain(hostSurface)
  })

  it('uses the DSH Agents home when no explicit skills directory is provided', async () => {
    const calls: string[][] = []
    const runner: FileRunner = async (_file, args) => {
      calls.push([...args])
      if (args.at(-1) === '--version') {
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      const operation = args.includes('--install') ? 'install' : 'inspect'
      return {
        exitCode: 0,
        stdout: workflowPayload(operation, false),
        stderr: '',
      }
    }

    await initializeLoopX({
      runner,
      env: { ...process.env, DSH_AGENTS_HOME: '/fixture/agents-home' },
    })

    const workflowCalls = calls.filter(args => args.includes('workflow-skills'))
    for (const args of workflowCalls) {
      expect(args[args.indexOf('--skills-dir') + 1]).toBe('/fixture/agents-home/skills')
    }
  })

  it('installs the CLI once when neither console nor module is available', async () => {
    let installed = false
    let pipCalls = 0
    const runner: FileRunner = async (_file, args) => {
      if (args[0] === '-c') {
        return { exitCode: 0, stdout: '', stderr: '' }
      }
      if (args.join(' ') === '-m pip --version') {
        return { exitCode: 0, stdout: 'pip fixture', stderr: '' }
      }
      if (args.slice(0, 3).join(' ') === '-m pip install') {
        pipCalls += 1
        installed = true
        return { exitCode: 0, stdout: 'installed', stderr: '' }
      }
      if (args.at(-1) === '--version') {
        if (!installed) throw new LoopXCliError('missing', 'missing', false)
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      if (args.includes('workflow-skills')) {
        const operation = args.includes('--install') ? 'install' : 'inspect'
        return {
          exitCode: 0,
          stdout: workflowPayload(operation, false),
          stderr: '',
        }
      }
      throw new Error(`unexpected argv: ${args.join(' ')}`)
    }

    const result = await initializeLoopX({ runner, skillsDir: '/fixture/skills' })

    expect(result.cliInstalled).toBe(true)
    expect(result.skillsChanged).toBe(false)
    expect(pipCalls).toBe(1)
  })

  it('selects an available Python 3.11+ interpreter for install and readback', async () => {
    const pythonPath = await mkdtemp(join(tmpdir(), 'loopx-installed-pythons-'))
    onTestFinished(() => rm(pythonPath, { recursive: true, force: true }))
    for (const name of ['python3.14', 'python3.13']) {
      await writeFile(join(pythonPath, name), '# fixture executable\n')
    }
    const calls: Array<{ readonly file: string; readonly args: readonly string[] }> = []
    let installed = false
    const runner: FileRunner = async (file, args) => {
      calls.push({ file, args: [...args] })
      if (args[0] === '-c') {
        return { exitCode: file === 'python3.13' ? 0 : 1, stdout: '', stderr: '' }
      }
      if (args.join(' ') === '-m pip --version') {
        return {
          exitCode: file === 'python3.13' ? 0 : 1,
          stdout: file === 'python3.13' ? 'pip fixture' : '',
          stderr: '',
        }
      }
      if (args.slice(0, 3).join(' ') === '-m pip install') {
        installed = true
        return { exitCode: 0, stdout: 'installed', stderr: '' }
      }
      if (args.at(-1) === '--version') {
        if (file !== 'python3.13' || !installed) {
          throw new LoopXCliError('missing', 'missing', false)
        }
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      if (args.includes('workflow-skills')) {
        const operation = args.includes('--install') ? 'install' : 'inspect'
        return {
          exitCode: 0,
          stdout: workflowPayload(operation, false),
          stderr: '',
        }
      }
      throw new Error(`unexpected argv: ${file} ${args.join(' ')}`)
    }

    const result = await initializeLoopX({
      runner,
      env: { PATH: pythonPath },
      skillsDir: '/fixture/skills',
      runtimeDir: '/fixture/runtime',
    })

    expect(result.cliInstalled).toBe(true)
    expect(calls.some(call => (
      call.file === 'python3'
      && call.args[0] === '-c'
    ))).toBe(true)
    expect(calls.some(call => (
      call.file === 'python3.14'
      && call.args[0] === '-c'
    ))).toBe(true)
    expect(calls.some(call => (
      call.file === 'python3.13'
      && call.args.slice(0, 3).join(' ') === '-m pip install'
    ))).toBe(true)
    const pipCall = calls.find(call => call.args.slice(0, 3).join(' ') === '-m pip install')
    expect(pipCall?.args).toContain('--target')
    expect(pipCall?.args.at(-1)).toBe('loopx>=1.2.4')
    expect(pipCall?.args[pipCall.args.indexOf('--target') + 1]).toBe(
      '/fixture/runtime/site-packages',
    )
    const workflowCalls = calls.filter(call => call.args.includes('workflow-skills'))
    expect(workflowCalls.every(call => call.file === 'python3.13')).toBe(true)
    expect(workflowCalls.every(call => (
      call.args[call.args.indexOf('--cli-bin') + 1]
        === 'python3.13 /fixture/runtime/loopx_cli.py'
    ))).toBe(true)
  })

  it('skips an implicit version-compatible Python that cannot run pip', async () => {
    const pythonPath = await mkdtemp(join(tmpdir(), 'loopx-installed-pythons-'))
    onTestFinished(() => rm(pythonPath, { recursive: true, force: true }))
    for (const name of ['python3.14']) {
      await writeFile(join(pythonPath, name), '# fixture executable\n')
    }
    const calls: Array<{ readonly file: string; readonly args: readonly string[] }> = []
    let installed = false
    const runner: FileRunner = async (file, args) => {
      calls.push({ file, args: [...args] })
      if (args.at(-1) === '--version' && args[0] !== '-m') {
        throw new LoopXCliError('missing', 'missing', false)
      }
      if (args[0] === '-c') {
        return {
          exitCode: file === 'python3' || file === 'python3.14' ? 0 : 1,
          stdout: '',
          stderr: '',
        }
      }
      if (args.join(' ') === '-m pip --version') {
        return {
          exitCode: file === 'python3.14' ? 0 : 1,
          stdout: file === 'python3.14' ? 'pip fixture' : '',
          stderr: '',
        }
      }
      if (args.slice(0, 3).join(' ') === '-m pip install') {
        installed = true
        return { exitCode: 0, stdout: 'installed', stderr: '' }
      }
      if (args.at(-1) === '--version') {
        if (file !== 'python3.14' || !installed) {
          throw new LoopXCliError('missing', 'missing', false)
        }
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      if (args.includes('workflow-skills')) {
        const operation = args.includes('--install') ? 'install' : 'inspect'
        return {
          exitCode: 0,
          stdout: workflowPayload(operation, false),
          stderr: '',
        }
      }
      throw new Error(`unexpected argv: ${file} ${args.join(' ')}`)
    }

    const result = await initializeLoopX({
      runner,
      env: { PATH: pythonPath },
      skillsDir: '/fixture/skills',
      runtimeDir: '/fixture/runtime',
    })

    expect(result.cliInstalled).toBe(true)
    expect(calls.some(call => (
      call.file === 'python3'
      && call.args.join(' ') === '-m pip --version'
    ))).toBe(true)
    expect(calls.some(call => (
      call.file === 'python3'
      && call.args.slice(0, 3).join(' ') === '-m pip install'
    ))).toBe(false)
    expect(calls.some(call => (
      call.file === 'python3.14'
      && call.args.slice(0, 3).join(' ') === '-m pip install'
    ))).toBe(true)
  })

  it('does not fall back from an explicit Python that cannot run pip', async () => {
    const calls: Array<{ readonly file: string; readonly args: readonly string[] }> = []
    const runner: FileRunner = async (file, args) => {
      calls.push({ file, args: [...args] })
      if (args.at(-1) === '--version' && args[0] !== '-m') {
        throw new LoopXCliError('missing', 'missing', false)
      }
      if (args[0] === '-c') {
        return { exitCode: 0, stdout: '', stderr: '' }
      }
      if (args.join(' ') === '-m pip --version') {
        return { exitCode: 1, stdout: '', stderr: 'No module named pip' }
      }
      throw new Error(`unexpected argv: ${file} ${args.join(' ')}`)
    }

    await expect(initializeLoopX({
      runner,
      pythonBin: '/configured/python',
      skillsDir: '/fixture/skills',
    })).rejects.toMatchObject({
      stage: 'install_cli',
      causeKind: 'missing',
    })
    expect(calls.filter(call => call.args[0] === '-c')
      .every(call => call.file === '/configured/python')).toBe(true)
    expect(calls.some(call => (
      call.file === '/configured/python'
      && call.args.join(' ') === '-m pip --version'
    ))).toBe(true)
  })

  it('reuses the plugin-owned launcher across runtime consumers and restarts', async () => {
    const root = await mkdtemp(join(tmpdir(), 'dsh-loopx-managed-runtime-'))
    const runtimeDir = join(root, 'runtime')
    const launcherPath = join(runtimeDir, 'loopx_cli.py')
    await mkdir(runtimeDir, { recursive: true })
    await writeFile(launcherPath, '# fixture launcher\n', 'utf8')
    const calls: Array<{ readonly file: string; readonly args: readonly string[] }> = []
    const runner: FileRunner = async (file, args) => {
      calls.push({ file, args: [...args] })
      if (args.at(-1) === '--version') {
        if (args[0] !== launcherPath) throw new LoopXCliError('missing', 'missing', false)
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      if (args.includes('workflow-skills')) {
        const operation = args.includes('--install') ? 'install' : 'inspect'
        return {
          exitCode: 0,
          stdout: workflowPayload(operation, false),
          stderr: '',
        }
      }
      throw new Error(`unexpected argv: ${file} ${args.join(' ')}`)
    }

    try {
      const command = await resolvePluginLoopXCommand({ runner, runtimeDir })
      expect(command.prefix).toEqual([launcherPath])
      const result = await initializeLoopX({
        runner,
        runtimeDir,
        skillsDir: join(root, 'skills'),
      })
      expect(result.cliInstalled).toBe(false)
      expect(calls.some(call => (
        call.args.slice(0, 3).join(' ') === '-m pip install'
      ))).toBe(false)
      expect(calls.filter(call => call.args.includes('workflow-skills')).every(call => (
        call.args[call.args.indexOf('--cli-bin') + 1]
          === `python3 ${launcherPath}`
      ))).toBe(true)
    } finally {
      await rm(root, { recursive: true, force: true })
    }
  })

  it('upgrades a pre-1.2.4 CLI before inspecting or mutating skills', async () => {
    let upgraded = false
    let pipCalls = 0
    let prematureWorkflowCalls = 0
    const runner: FileRunner = async (_file, args) => {
      if (args[0] === '-c') {
        return { exitCode: 0, stdout: '', stderr: '' }
      }
      if (args.join(' ') === '-m pip --version') {
        return { exitCode: 0, stdout: 'pip fixture', stderr: '' }
      }
      if (args.at(-1) === '--version') {
        return { exitCode: 0, stdout: `loopx ${upgraded ? '1.2.4' : '1.2.3'}\n`, stderr: '' }
      }
      if (args.slice(0, 3).join(' ') === '-m pip install') {
        pipCalls += 1
        upgraded = true
        return { exitCode: 0, stdout: 'upgraded', stderr: '' }
      }
      if (args.includes('workflow-skills')) {
        if (!upgraded) {
          prematureWorkflowCalls += 1
          return {
            exitCode: 0,
            stdout: '{"ok":true,"schema_version":"old_workflow_schema"}',
            stderr: '',
          }
        }
        const operation = args.includes('--install') ? 'install' : 'inspect'
        return {
          exitCode: 0,
          stdout: workflowPayload(operation, false),
          stderr: '',
        }
      }
      throw new Error(`unexpected argv: ${args.join(' ')}`)
    }

    const result = await initializeLoopX({ runner, skillsDir: '/fixture/skills' })
    expect(result.cliInstalled).toBe(true)
    expect(pipCalls).toBe(1)
    expect(prematureWorkflowCalls).toBe(0)
  })

  it('fails an explicit old CLI before pip installation or skill mutation', async () => {
    const calls: string[][] = []
    await expect(initializeLoopX({
      env: { LOOPX_BIN: 'explicit-old-loopx' },
      runner: async (file, args) => {
        expect(file).toBe('explicit-old-loopx')
        calls.push([...args])
        return { exitCode: 0, stdout: 'loopx 1.2.3\n', stderr: '' }
      },
    })).rejects.toMatchObject({ stage: 'probe', causeKind: 'incompatible' })
    expect(calls).toEqual([['--version']])
  })

  it.each([
    ['created packaged skill', 'created', 'unchanged'],
    ['updated packaged skill', 'updated', 'unchanged'],
    ['upgraded legacy entry skill', 'unchanged', 'upgraded_legacy_managed'],
  ] as const)('reports skills changed for a %s', async (
    _caseName,
    packagedStatus,
    entryStatus,
  ) => {
    const { runner } = successfulRunner({ packagedStatus, entryStatus })

    const result = await initializeLoopX({ runner, skillsDir: '/fixture/skills' })

    expect(result.skillsChanged).toBe(true)
  })

  it.each([
    ['missing packaged status', {
      installed: Object.fromEntries(packagedSkillIds.slice(1).map(skillId => (
        [skillId, 'unchanged']
      ))),
      entry: { status: 'unchanged' },
    }],
    ['missing entry status', {
      ...installState(),
      entry: {},
    }],
    ['unknown packaged status', {
      ...installState(),
      installed: {
        ...(installState().installed as Record<string, unknown>),
        'loopx-project': 'replaced',
      },
    }],
    ['unknown entry status', {
      ...installState(),
      entry: { status: 'replaced' },
    }],
  ])('fails closed for %s in the actual install payload', async (
    _caseName,
    actualInstallState,
  ) => {
    const runner: FileRunner = async (_file, args) => {
      if (args.at(-1) === '--version') {
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      if (args.includes('--install')) {
        return {
          exitCode: 0,
          stdout: workflowPayload('install', false, actualInstallState),
          stderr: '',
        }
      }
      return { exitCode: 0, stdout: workflowPayload('inspect', false), stderr: '' }
    }

    await expect(initializeLoopX({ runner, skillsDir: '/fixture/skills' }))
      .rejects.toMatchObject({ stage: 'install_skills', causeKind: 'typed_failure' })
  })

  it('never retries a failed skill installation mutation', async () => {
    let installCalls = 0
    const runner: FileRunner = async (_file, args) => {
      if (args.at(-1) === '--version') {
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      if (args.includes('--install')) {
        installCalls += 1
        return {
          exitCode: 1,
          stdout: JSON.stringify({
            ok: false,
            schema_version: 'loopx_workflow_skill_install_v0',
            operation: 'install',
            host_surface: hostSurface,
          }),
          stderr: '/private/path/that/must/not/be-surfaced',
        }
      }
      return {
        exitCode: 0,
        stdout: workflowPayload('inspect', true),
        stderr: '',
      }
    }

    await expect(initializeLoopX({ runner, skillsDir: '/fixture/skills' }))
      .rejects.toMatchObject({ stage: 'install_skills', causeKind: 'typed_failure' })
    expect(installCalls).toBe(1)
  })

  it('fails closed when post-install readback still requests installation', async () => {
    let inspectCalls = 0
    const runner: FileRunner = async (_file, args) => {
      if (args.at(-1) === '--version') {
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      if (args.includes('--install')) {
        return { exitCode: 0, stdout: workflowPayload('install'), stderr: '' }
      }
      inspectCalls += 1
      return {
        exitCode: 0,
        stdout: workflowPayload('inspect', inspectCalls > 1),
        stderr: '',
      }
    }

    await expect(initializeLoopX({ runner, skillsDir: '/fixture/skills' }))
      .rejects.toMatchObject({ stage: 'readback', causeKind: 'readback_mismatch' })
  })

  it('propagates cancellation without running an install fallback', async () => {
    let pipCalls = 0
    const runner: FileRunner = async (_file, args) => {
      if (args.slice(0, 3).join(' ') === '-m pip install') pipCalls += 1
      throw new LoopXCliError('aborted', 'cancelled', false)
    }

    await expect(initializeLoopX({ runner, skillsDir: '/fixture/skills' }))
      .rejects.toMatchObject({ kind: 'aborted' })
    expect(pipCalls).toBe(0)
  })

  it('preserves cancellation during the skill-install mutation', async () => {
    let installCalls = 0
    const runner: FileRunner = async (_file, args) => {
      if (args.at(-1) === '--version') {
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      if (args.includes('--install')) {
        installCalls += 1
        throw new LoopXCliError('aborted', 'cancelled', false)
      }
      return {
        exitCode: 0,
        stdout: workflowPayload('inspect', false),
        stderr: '',
      }
    }

    await expect(initializeLoopX({ runner, skillsDir: '/fixture/skills' }))
      .rejects.toMatchObject({ kind: 'aborted' })
    expect(installCalls).toBe(1)
  })
})

describe('/loopx-init followups', () => {
  it('rejects arguments before sending a followup or probing LoopX', async () => {
    let runnerCalls = 0
    const harness = commandHarness({
      runner: async () => {
        runnerCalls += 1
        throw new Error('runner must not be called')
      },
      skillsDir: '/fixture/skills',
    })

    const result = await harness.execute(' extra')

    expect(result).toEqual({ kind: 'error', text: 'Usage: /loopx-init' })
    expect(harness.messages).toHaveLength(0)
    expect(runnerCalls).toBe(0)
  })

  it('queues bounded start and completion messages around the single mutation and readback', async () => {
    const events: string[] = []
    const { runner, calls } = successfulRunner({ events })
    const harness = commandHarness(
      { runner, skillsDir: '/fixture/skills' },
      (_message, attempt) => { events.push(`followup-${attempt}`) },
    )

    const result = await harness.execute()

    expect(result.kind).toBe('success')
    expect(events).toEqual([
      'followup-1',
      'probe',
      'inspect-before',
      'install-skills',
      'readback',
      'followup-2',
    ])
    expect(calls.filter(args => args.includes('--install'))).toHaveLength(1)
    expect(harness.messages).toHaveLength(2)
    for (const message of harness.messages) {
      expect(message.source).toEqual({ kind: 'loopx-initialization', plugin: initSource })
      expect(message.source).not.toEqual({
        kind: 'plugin',
        plugin: 'dsh-loopx-plugin/driver',
      })
      expect(messageText(message).length).toBeLessThanOrEqual(800)
      expect(messageText(message)).toContain('Do not call tools')
    }
    expect(messageText(harness.messages[0] as UserMessage)).toContain(
      'initialization has started',
    )
    expect(messageText(harness.messages[1] as UserMessage)).toContain(
      'no DSH restart is required',
    )
  })

  it.each([
    ['created packaged skill', 'created', 'unchanged'],
    ['updated packaged skill', 'updated', 'unchanged'],
    ['upgraded legacy entry skill', 'unchanged', 'upgraded_legacy_managed'],
  ] as const)('loads a %s without a DSH restart', async (
    _caseName,
    packagedStatus,
    entryStatus,
  ) => {
    const { runner } = successfulRunner({ packagedStatus, entryStatus })
    const harness = commandHarness({ runner, skillsDir: '/fixture/skills' })

    const result = await harness.execute()

    expect(result.kind).toBe('success')
    expect(result.text).toContain('No DSH restart is required')
    expect(harness.messages).toHaveLength(2)
    expect(messageText(harness.messages[1] as UserMessage)).toContain(
      'loaded without a restart',
    )
  })

  it.each([
    ['compatible CLI', true, 0],
    ['newly installed CLI', false, 1],
  ] as const)('does not request a restart for unchanged skills with a %s', async (
    _caseName,
    initiallyInstalled,
    expectedPipCalls,
  ) => {
    const { runner, calls } = successfulRunner({ initiallyInstalled })
    const harness = commandHarness({ runner, skillsDir: '/fixture/skills' })

    const result = await harness.execute()

    expect(result.kind).toBe('success')
    expect(result.text).toContain('No DSH restart is required')
    expect(messageText(harness.messages[1] as UserMessage)).toContain(
      'no DSH restart is required',
    )
    expect(calls.filter(args => (
      args.slice(0, 3).join(' ') === '-m pip install'
    ))).toHaveLength(expectedPipCalls)
    expect(calls.filter(args => args.includes('--install'))).toHaveLength(1)
  })

  it('sends a safe failure followup without leaking subprocess diagnostics', async () => {
    const privateStderr = '/fixture/private/runner-stderr'
    let installCalls = 0
    const runner: FileRunner = async (_file, args) => {
      if (args.at(-1) === '--version') {
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      if (args.includes('--install')) {
        installCalls += 1
        return {
          exitCode: 1,
          stdout: JSON.stringify({
            ok: false,
            schema_version: 'loopx_workflow_skill_install_v0',
            operation: 'install',
            host_surface: hostSurface,
          }),
          stderr: privateStderr,
        }
      }
      return { exitCode: 0, stdout: workflowPayload('inspect', true), stderr: '' }
    }
    const harness = commandHarness({ runner, skillsDir: '/fixture/skills' })

    const result = await harness.execute()

    expect(result.kind).toBe('error')
    expect(result.text).toContain('stage=install_skills; kind=typed_failure')
    expect(result.text).not.toContain(privateStderr)
    expect(installCalls).toBe(1)
    expect(harness.messages).toHaveLength(2)
    const completion = messageText(harness.messages[1] as UserMessage)
    expect(completion).toContain('safe failure stage is install_skills')
    expect(completion).toContain('cause kind is typed_failure')
    expect(completion).not.toContain(privateStderr)
    expect(completion).not.toContain('restart DSH once')
    expect(completion.length).toBeLessThanOrEqual(800)
  })

  it('leaves only the start followup when initialization is cancelled', async () => {
    let installCalls = 0
    const runner: FileRunner = async (_file, args) => {
      if (args.at(-1) === '--version') {
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      }
      if (args.includes('--install')) {
        installCalls += 1
        throw new LoopXCliError('aborted', 'cancelled with private context', false)
      }
      return { exitCode: 0, stdout: workflowPayload('inspect', false), stderr: '' }
    }
    const harness = commandHarness({ runner, skillsDir: '/fixture/skills' })

    const result = await harness.execute()

    expect(result).toEqual({
      kind: 'error',
      text: 'LOOPX_INIT_CANCELLED: initialization was cancelled.',
    })
    expect(installCalls).toBe(1)
    expect(harness.messages).toHaveLength(1)
    expect(messageText(harness.messages[0] as UserMessage)).toContain(
      'initialization has started',
    )
  })

  it.each([
    ['start', 1],
    ['completion', 2],
  ] as const)('keeps the result and one mutation when the %s followup throws', async (
    _phase,
    throwingAttempt,
  ) => {
    const { runner, calls } = successfulRunner({ packagedStatus: 'created' })
    const harness = commandHarness(
      { runner, skillsDir: '/fixture/skills' },
      (_message, attempt) => {
        if (attempt === throwingAttempt) throw new Error('private followup failure')
      },
    )

    const result = await harness.execute()

    expect(result).toEqual({
      kind: 'success',
      text: [
        'LoopX ready (loopx 1.2.4).',
        'CLI already compatible.',
        'DSH LoopX skills installed or updated and verified.',
        'No DSH restart is required; use the `loopx` skill with your task.',
      ].join(' '),
    })
    expect(calls.filter(args => args.includes('--install'))).toHaveLength(1)
    expect(harness.messages).toHaveLength(2)
    expect(harness.warnings).toEqual([
      `dsh-loopx-init-command: could not queue ${throwingAttempt === 1 ? 'start' : 'complete'} followup`,
    ])
    expect(harness.warnings.join(' ')).not.toContain('private followup failure')
  })
})
