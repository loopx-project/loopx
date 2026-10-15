import { randomUUID } from 'node:crypto'
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Agent } from '@deepseek-ai/dsh-agent'
import type { SessionEvent, UserMessage } from '@deepseek-ai/dsh-session'
import { describe, expect, it } from 'vitest'

import { runFile } from '../src/cli.ts'
import type { FileRunner } from '../src/cli.ts'
import { LoopXContinuationDriver } from '../src/driver.ts'
import { pluginPythonCandidates } from '../src/managed-runtime.ts'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const sessionId = 'dsh-closeout-session'
const goalId = 'dsh-closeout-goal'
const agentId = 'dsh-closeout-agent'
const todoId = 'todo_dsh_closeout'
const turnInstanceId = 'dsh-closeout-turn-1'
const pythonVersionProbe = 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
const pythonCandidates = pluginPythonCandidates({ env: process.env })

interface Fixture {
  readonly project: string
  readonly runtime: string
  readonly registry: string
  readonly agent: Agent
  readonly queued: UserMessage[]
}

interface CliResult {
  readonly exitCode: number
  readonly payload: Record<string, unknown>
}

function record(value: unknown): Record<string, unknown> {
  expect(value).not.toBeNull()
  expect(typeof value).toBe('object')
  expect(Array.isArray(value)).toBe(false)
  return value as Record<string, unknown>
}

function array(value: unknown): unknown[] {
  expect(Array.isArray(value)).toBe(true)
  return value as unknown[]
}

async function selectIntegrationPython(): Promise<string> {
  for (const candidate of pythonCandidates) {
    try {
      const result = await runFile(candidate, ['-c', pythonVersionProbe], {})
      if (result.exitCode === 0) return candidate
    } catch {
      // Keep probing the same supported interpreter set as the plugin bootstrap.
    }
  }
  throw new Error('The real LoopX integration test requires Python 3.11+')
}

const integrationPython = selectIntegrationPython()

const realLoopXRunner: FileRunner = async (_file, args, options) => {
  return runFile(
    await integrationPython,
    ['-m', 'loopx.cli', ...args],
    {
      ...options,
      env: {
        ...process.env,
        ...options.env,
        PYTHONPATH: repoRoot,
      },
    },
  )
}

async function runCli(fixture: Fixture, args: readonly string[]): Promise<CliResult> {
  const result = await realLoopXRunner('loopx', [
    '--registry', fixture.registry,
    '--runtime-root', fixture.runtime,
    '--format', 'json',
    ...args,
  ], { cwd: fixture.project })
  let payload: unknown
  try {
    payload = JSON.parse(result.stdout)
  } catch {
    throw new Error(`LoopX returned non-JSON output: ${result.stdout}`)
  }
  return { exitCode: result.exitCode, payload: record(payload) }
}

async function fixture(): Promise<Fixture> {
  const root = await mkdtemp(join(tmpdir(), 'dsh-loopx-closeout-'))
  const project = join(root, 'project')
  const runtime = join(root, 'runtime')
  const registry = join(project, '.loopx', 'registry.json')
  const stateRelative = `.codex/goals/${goalId}/ACTIVE_GOAL_STATE.md`
  const state = join(project, stateRelative)
  await mkdir(dirname(registry), { recursive: true })
  await mkdir(dirname(state), { recursive: true })
  await writeFile(state, [
    '---',
    'status: active-read-only',
    'owner_mode: goal',
    'objective: "Validate one DSH continuation settlement."',
    'updated_at: 2026-01-01T00:00:00+00:00',
    '---',
    '',
    '# DSH Closeout Fixture',
    '',
    '## Objective',
    '',
    'Validate one DSH continuation settlement.',
    '',
    '## Next Action',
    '',
    '- Validate and settle the selected delivery.',
    '',
    '## Agent Todo',
    '',
    '- [ ] [P1] Validate and settle the selected delivery.',
    `  <!-- loopx:todo todo_id=${todoId} status=open task_class=advancement_task action_kind=validate -->`,
    '',
  ].join('\n'), 'utf8')
  await writeFile(registry, `${JSON.stringify({
    schema_version: '0.1',
    updated_at: '2026-01-01T00:00:00+00:00',
    common_runtime_root: runtime,
    goals: [{
      id: goalId,
      domain: 'dsh-closeout-fixture',
      status: 'active-read-only',
      repo: project,
      state_file: stateRelative,
      adapter: {
        kind: 'read_only_project_map_v0',
        status: 'connected-read-only',
      },
      coordination: {
        registered_agents: [agentId],
        agent_model: 'peer_v1',
        thread_agent_bindings: [{
          host_surface: 'deepseek-harness-native',
          thread_id: sessionId,
          agent_id: agentId,
        }],
      },
      authority_sources: [],
      quota: { compute: 1, window_hours: 24, allowed_slots: 2 },
    }],
  }, null, 2)}\n`, 'utf8')
  expect((await runFile('git', ['init', '--quiet'], { cwd: project })).exitCode).toBe(0)
  expect((await runFile('git', [
    'remote', 'add', 'origin',
    'https://github.com/example/dsh-closeout-fixture.git',
  ], { cwd: project })).exitCode).toBe(0)

  const queued: UserMessage[] = []
  const sessionEvents = [{
    type: 'user/message',
    data: {
      id: randomUUID(),
      role: 'user',
      content: [{ type: 'text', text: 'Use LoopX.' }],
      source: { kind: 'skill-invocation', name: 'loopx', form: 'instructions' },
    },
  }] as unknown as SessionEvent[]
  const session = {
    id: sessionId,
    header: {
      version: 0,
      id: sessionId,
      createdAt: 1,
      cwd: project,
      seedLength: 0,
    },
    snapshotEvents: () => sessionEvents,
    surface: { nodes: [] },
  }
  const inbox = {
    nextTurn: queued,
    nextStep: [] as UserMessage[],
    remove(id: UserMessage['id']) {
      const index = queued.findIndex(message => message.id === id)
      if (index < 0) return false
      queued.splice(index, 1)
      return true
    },
  }
  let status: 'idle' | 'running' = 'idle'
  const agent = {
    id: sessionId,
    options: {},
    session,
    inbox,
    ctx: {},
    get status() { return status },
    cancel() {},
    whenIdle: async () => {},
    runMaintenance: async <T>(operation: (signal: AbortSignal) => Promise<T>) => (
      operation(new AbortController().signal)
    ),
    send(message: UserMessage, target: 'next-turn' | 'next-step') {
      (target === 'next-step' ? inbox.nextStep : queued).push(message)
    },
    followup(message: UserMessage) { queued.push(message) },
    steer(message: UserMessage) { inbox.nextStep.push(message) },
    inject(message: UserMessage) { inbox.nextStep.push(message) },
    setStatus(value: 'idle' | 'running') { status = value },
  } as unknown as Agent
  return { project, runtime, registry, agent, queued }
}

async function waitForQueued(
  fixtureValue: Fixture,
  warnings: readonly string[],
): Promise<UserMessage> {
  for (let attempt = 0; attempt < 800; attempt += 1) {
    const message = fixtureValue.queued[0]
    if (message !== undefined) return message
    await new Promise<void>(resolveWait => setTimeout(resolveWait, 25))
  }
  throw new Error(`Driver did not queue the admitted continuation: ${warnings.join(' | ')}`)
}

function receiptEffectIds(payload: Record<string, unknown>): string[] {
  const result = record(payload.settlement_result)
  return array(result.receipts).map(receipt => String(record(receipt).effect_id))
}

function executablePlanArgs(
  steps: readonly Record<string, unknown>[],
  kind: 'durable_writeback' | 'quota_spend',
  replacements: Readonly<Record<string, string>> = {},
): string[] {
  const command = String(
    steps.find(step => step.kind === kind)?.command_template ?? '',
  )
  const tokens = command.split(/\s+/).filter(Boolean)
  expect(tokens.shift()).toBe('loopx')
  const args = tokens.map(token => replacements[token] ?? token)
  expect(args.some(token => token.startsWith('<'))).toBe(false)
  return args
}

describe('DSH admission-to-closeout integration', () => {
  it('settles the queued exact receipt once through the raw generic CLI plan', async () => {
    const testFixture = await fixture()
    const warnings: string[] = []
    const driver = new LoopXContinuationDriver({
      isLiveAgent: () => true,
      runner: realLoopXRunner,
      resolveCommand: async () => ({
        file: 'loopx',
        prefix: [],
        skillCommand: 'loopx',
        version: 'loopx integration checkout',
      }),
      makeTurnInstanceId: () => turnInstanceId,
      retryDelaysMs: [0, 0],
      warn: message => { warnings.push(message) },
    })

    driver.observeAgent(testFixture.agent)
    const queued = await waitForQueued(testFixture, warnings)
    const source = record(queued.source)
    expect(source).toEqual({
      kind: 'loopx-continuation',
      schemaVersion: 'loopx_dsh_continuation_v0',
      goalId,
      agentId,
      turnInstanceId,
    })
    const content = array(queued.content)
    expect(record(content[0]).text).toContain(`LOOPX_TURN=${turnInstanceId}`)

    testFixture.queued.splice(0, 1)
    ;(testFixture.agent as unknown as { setStatus(value: 'running'): void })
      .setStatus('running')
    driver.onInboxClaimed(testFixture.agent, queued)
    const admission = await driver.onPreStep(
      testFixture.agent,
      [queued],
      new AbortController().signal,
      async () => ({ kind: 'enter', messages: [queued] }),
    )
    expect(admission.kind).toBe('enter')

    const unselected = await runCli(testFixture, [
      'quota', 'should-run',
      '--goal-id', goalId,
      '--agent-id', agentId,
      '--runtime-profile', 'generic_cli',
      '--turn-instance-id', turnInstanceId,
    ])
    expect(unselected.exitCode).toBe(0)
    const unselectedChannel = record(record(unselected.payload.interaction_contract).cli_channel)
    expect(unselectedChannel.selection_required).toBe(true)
    expect(unselectedChannel.settlement_plan).toBeUndefined()

    const guardArgs = [
      'quota', 'should-run',
      '--goal-id', goalId,
      '--todo-id', todoId,
      '--agent-id', agentId,
      '--runtime-profile', 'generic_cli',
      '--turn-instance-id', turnInstanceId,
    ]
    const guard = await runCli(testFixture, guardArgs)
    const guardReplay = await runCli(testFixture, guardArgs)
    expect(guardReplay.exitCode).toBe(0)
    expect(guard.exitCode).toBe(0)
    const heartbeatReceipt = record(guard.payload.heartbeat_receipt)
    expect(heartbeatReceipt.turn_instance_id).toBe(turnInstanceId)
    const cliChannel = record(record(guard.payload.interaction_contract).cli_channel)
    const rawPlan = record(cliChannel.settlement_plan)
    const identity = record(rawPlan.identity)
    const replayChannel = record(record(guardReplay.payload.interaction_contract).cli_channel)
    expect(record(record(replayChannel.settlement_plan).identity)).toEqual(identity)
    const effectId = String(identity.effect_id)
    expect(identity.turn_instance_id).toBe(turnInstanceId)
    expect(identity.todo_id).toBe(todoId)
    expect(JSON.stringify(rawPlan)).not.toContain('${LOOPX_TURN:?}')
    const steps = array(rawPlan.ordered_steps).map(record)
    const writebackTemplate = String(
      steps.find(step => step.kind === 'durable_writeback')?.command_template,
    )
    const spendTemplate = String(
      steps.find(step => step.kind === 'quota_spend')?.command_template,
    )
    for (const command of [writebackTemplate, spendTemplate]) {
      expect(command).toContain(`--turn-instance-id ${turnInstanceId}`)
      expect(command).toContain(`--todo-id ${todoId}`)
    }

    const writeback = await runCli(testFixture, [
      ...executablePlanArgs(steps, 'durable_writeback', {
        '<validated_progress>': 'dsh_closeout_validated',
        '<scale>': 'single_surface',
        '<outcome>': 'outcome_progress',
      }),
      '--no-global-sync',
      '--suppress-external-sinks',
    ])
    expect(writeback.exitCode).toBe(0)
    expect(new Set(receiptEffectIds(writeback.payload))).toEqual(new Set([effectId]))

    const spendArgs = [
      ...executablePlanArgs(steps, 'quota_spend'),
      '--scan-path', testFixture.project,
    ]
    const spend = await runCli(testFixture, spendArgs)
    const spendReplay = await runCli(testFixture, spendArgs)
    expect(spend.exitCode).toBe(0)
    expect(spendReplay.exitCode).toBe(0)
    expect(spendReplay.payload.idempotent_replay).toBe(true)
    expect(spendReplay.payload.appended).toBe(false)
    expect(new Set(receiptEffectIds(spend.payload))).toEqual(new Set([effectId]))

    const closeout = await runCli(testFixture, [
      'todo', 'complete',
      '--goal-id', goalId,
      '--agent-id', agentId,
      '--todo-id', todoId,
      '--turn-instance-id', turnInstanceId,
      '--claimed-by', agentId,
      '--evidence', 'DSH admission-to-closeout integration validated',
      '--no-follow-up',
    ])
    expect(closeout.exitCode).toBe(0)
    expect(receiptEffectIds(closeout.payload)).toEqual([
      effectId, effectId, effectId, effectId,
    ])

    const eventLog = join(testFixture.runtime, 'goals', goalId, 'rollout-event-log.jsonl')
    const events = (await readFile(eventLog, 'utf8'))
      .trim().split('\n').map(line => record(JSON.parse(line)))
    const admissionReceipts = events.filter(event => (
      event.event_kind === 'quota_should_run'
      && event.agent_id === agentId
      && event.run_id === turnInstanceId
    ))
    expect(admissionReceipts).toHaveLength(2)
    const initialAdmission = admissionReceipts.find(event => (
      record(event.details).settlement_effect_id === null
    ))
    const selectedAdmission = admissionReceipts.find(event => (
      record(event.details).settlement_effect_id === effectId
    ))
    expect(record(record(initialAdmission).details).delivery_allowed).toBe(false)
    const selectedDetails = record(record(selectedAdmission).details)
    expect(selectedDetails.todo_id).toBe(todoId)
    expect(selectedDetails.settlement_receipt_revision).toBe('identity_upgrade')
    expect(record(record(selectedAdmission).causality).caused_by).toBe(record(initialAdmission).event_id)
    expect(events.filter(event => (
      event.event_kind === 'quota_should_run'
      && event.run_id !== turnInstanceId
    ))).toHaveLength(0)

    const runIndex = join(testFixture.runtime, 'goals', goalId, 'runs', 'index.jsonl')
    const runs = (await readFile(runIndex, 'utf8'))
      .trim().split('\n').map(line => record(JSON.parse(line)))
    expect(runs.filter(run => run.classification === 'quota_slot_spent')).toHaveLength(1)
    expect(runs.filter(run => run.settlement_identity !== undefined).every(run => (
      record(run.settlement_identity).effect_id === effectId
    ))).toBe(true)

    await driver.dispose()
  }, 90_000)
})
