import { randomUUID } from 'node:crypto'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Agent } from '@deepseek-ai/dsh-agent'
import type { SessionEvent, UserMessage } from '@deepseek-ai/dsh-session'
import { describe, expect, it, onTestFinished } from 'vitest'

import { LoopXCliError, runFile } from '../src/cli.ts'
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

const readOnlySelection = `
import sys
from loopx.cli import main
from loopx.cli_commands import quota_context_reader as reader

def read_only(*args, **kwargs):
    raise PermissionError("synthetic reader descriptor write denial")

reader.write_context_read_reference = read_only
raise SystemExit(main(sys.argv[1:]))
`

function loopxRunner(prefix: readonly string[]): FileRunner {
  return async (_file, args, options) => runFile(
    await integrationPython,
    [...prefix, ...args],
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

const realLoopXRunner = loopxRunner(['-m', 'loopx.cli'])
const readOnlySelectionRunner = loopxRunner(['-c', readOnlySelection])

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

async function setAllowedSlots(testFixture: Fixture, allowedSlots: number): Promise<void> {
  const registry = record(JSON.parse(await readFile(testFixture.registry, 'utf8')))
  const goal = record(array(registry.goals)[0])
  goal.quota = { ...record(goal.quota), allowed_slots: allowedSlots }
  await writeFile(testFixture.registry, JSON.stringify(registry))
}

async function fixture(): Promise<Fixture> {
  const root = await mkdtemp(join(tmpdir(), 'dsh-loopx-closeout-'))
  onTestFinished(() => rm(root, { recursive: true, force: true }))
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
    'handoff_mode: soft_claim',
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
  // The closeout journey starts on supported authority. Markdown remains a
  // projection; this test must not depend on the retired ordinary writer.
  const canonical = await runFile(await integrationPython, ['-c', [
    'import sys',
    'from pathlib import Path',
    'sys.path.insert(0, str(Path(sys.argv[1]) / "tests/control_plane"))',
    'from canonical_authority_fixture import initialize_canonical_authority',
    'from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection',
    'projection = build_todo_runtime_shadow_projection(',
    '    goal_id=sys.argv[3], handoff_mode="soft_claim", todos=[{',
    '        "schema_version": "todo_item_v0", "todo_id": sys.argv[5],',
    '        "role": "agent", "status": "open", "done": False,',
    '        "text": "[P1] Validate and settle the selected delivery.",',
    '        "title": "Validate and settle the selected delivery.", "priority": "P1",',
    '        "archive_state": "active", "source_section": "Agent Todo", "index": 1,',
    '        "task_class": "advancement_task", "action_kind": "validate",',
    '    }])',
    'initialize_canonical_authority(Path(sys.argv[2]), sys.argv[3], projection,',
    '    state_path=Path(sys.argv[4]), provider="file")',
  ].join('\n'), repoRoot, runtime, goalId, state, todoId], {
    cwd: repoRoot,
    env: { ...process.env, PYTHONPATH: repoRoot },
  })
  expect(canonical.exitCode, canonical.stderr).toBe(0)
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
  const tokens = command.match(/'[^']*'|"[^"]*"|\S+/gu) ?? []
  expect(tokens.shift()).toBe('loopx')
  const args = tokens.map(token => replacements[token] ?? token.replace(/^(['"])(.*)\1$/u, '$2'))
  expect(args.some(token => token.startsWith('<'))).toBe(false)
  return args
}

describe('DSH admission-to-closeout integration', () => {
  it.each([
    { route: 'compatibility', version: 'loopx 1.2.4', setting: 'on', readerCalls: 0 },
    { route: 'progressive', version: 'loopx 2.3.0', setting: 'on', readerCalls: 4 },
    { route: 'configured off', version: 'loopx 2.3.0', setting: 'off', readerCalls: 0 },
    { route: 'read-only selection', version: 'loopx 2.3.0', setting: 'on', readerCalls: 4,
      readOnlyFromGuard: 1 },
    { route: 'read-only paged revalidation', version: 'loopx 2.3.0', setting: 'on', readerCalls: 4,
      readOnlyFromGuard: 3, readOnlyPageBytes: '256' },
    { route: 'lost response on a later page', version: 'loopx 2.3.0', setting: 'on', readerCalls: 4,
      readOnlyFromGuard: 3, readOnlyPageBytes: '256', loseReaderResponse: true },
  ])('settles the queued exact receipt once through $route admission', async ({
    route, version, setting, readerCalls, readOnlyFromGuard, readOnlyPageBytes, loseReaderResponse,
  }) => {
    const testFixture = await fixture()
    const warnings: string[] = []
    let guardCalls = 0
    let reads = 0
    let inlinePages = 0
    let inlineContinuations = 0
    let lostPage: { argv: readonly string[]; stdout: string } | undefined
    let recoveredPage = false
    const runner: FileRunner = async (file, args, options) => {
      if (args.includes('context-read')) reads += 1
      if (args.includes('should-run')) {
        guardCalls += 1
        expect(options.env?.LOOPX_CONTEXT_DELIVERY).toBe(route === 'compatibility' ? 'off' : undefined)
      }
      const child = { ...options, env: {
        LOOPX_CONTEXT_DELIVERY: setting, ...options.env,
      } }
      if (args.includes('context-read') && readOnlyFromGuard !== undefined && guardCalls >= readOnlyFromGuard) {
        const readerArgs = [...args]
        if (readOnlyPageBytes) readerArgs[readerArgs.indexOf('--page-bytes') + 1] = readOnlyPageBytes
        const response = await readOnlySelectionRunner(file, readerArgs, child)
        const page = record(JSON.parse(response.stdout))
        expect(page.schema_version).toBe('context_capture_page_v1')
        expect(page.ok).toBe(true)
        inlinePages += 1
        if (page.offset !== 0) {
          inlineContinuations += 1
          expect(readerArgs).toContain('--selection-sha256')
          if (loseReaderResponse && lostPage === undefined) {
            lostPage = { argv: readerArgs, stdout: response.stdout }
            // The original Core read succeeded, but its output was lost
            // before the Driver could observe it.
            throw new LoopXCliError('transport', 'synthetic reader response loss', true)
          }
          if (lostPage !== undefined && !recoveredPage) {
            expect(readerArgs).toEqual(lostPage.argv)
            expect(response.stdout).toEqual(lostPage.stdout)
            recoveredPage = true
          }
        }
        return response
      }
      return realLoopXRunner(file, args, child)
    }
    const driver = new LoopXContinuationDriver({
      isLiveAgent: () => true,
      runner,
      resolveCommand: async () => ({
        file: 'loopx',
        prefix: [],
        skillCommand: 'loopx',
        version,
      }),
      makeTurnInstanceId: () => turnInstanceId,
      retryDelaysMs: [0, 0],
      warn: message => { warnings.push(message) },
    })

    // Finished hooks unwind in reverse order, so the driver stops before fixture removal.
    onTestFinished(() => driver.dispose())
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
    expect(record(content[0]).text).toContain(process.platform === 'win32'
      ? `$LOOPX_TURN = "${turnInstanceId}"`
      : `LOOPX_TURN=${turnInstanceId}`)

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
    if (loseReaderResponse) {
      expect(recoveredPage).toBe(true)
      expect(warnings).toEqual([])
      expect(testFixture.queued).toHaveLength(0)
    }
    // The Driver intentionally revalidates before and after the next pre-step hook.
    expect(guardCalls).toBe(3)
    if (readOnlyPageBytes) {
      expect(reads).toBeGreaterThan(readerCalls)
      expect(inlineContinuations).toBeGreaterThan(0)
    } else {
      expect(reads).toBe(readerCalls)
    }
    if (readOnlyFromGuard !== undefined) expect(inlinePages).toBeGreaterThan(0)

    const unselected = await runCli(testFixture, [
      'quota', 'should-run', '--context-delivery', 'off',
      '--goal-id', goalId,
      '--agent-id', agentId,
      '--runtime-profile', 'generic_cli',
      '--turn-instance-id', turnInstanceId,
    ])
    expect(unselected.exitCode).toBe(0)
    const unselectedChannel = record(record(unselected.payload.interaction_contract).cli_channel)
    expect(unselectedChannel.selection_required).toBe(true)
    expect(unselectedChannel.settlement_plan).toBeUndefined()

    // The queued conversation lets the agent choose work; it does not claim
    // the fixture's Todo or bind the Turn on the agent's behalf, so the agent
    // claims the work it selected before the exact receipt is bound.
    const claimed = await runCli(testFixture, [
      'todo', 'claim', '--goal-id', goalId, '--todo-id', todoId,
      '--agent-id', agentId, '--claimed-by', agentId,
    ])
    expect(claimed.exitCode, JSON.stringify(claimed.payload)).toBe(0)
    expect(claimed.payload.ok).toBe(true)

    const guardArgs = [
      'quota', 'should-run', '--context-delivery', 'off',
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
    expect(guard.payload.ok).toBe(true)
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
    expect(writeback.exitCode, JSON.stringify(writeback.payload)).toBe(0)
    expect(new Set(receiptEffectIds(writeback.payload))).toEqual(new Set([effectId]))

    const spendArgs = executablePlanArgs(steps, 'quota_spend')
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
  }, 90_000)

  it('rejects queued work when quota closes during the next pre-step hook', async () => {
    const testFixture = await fixture()
    const warnings: string[] = []
    let guards = 0
    const runner: FileRunner = (file, args, options) => {
      if (args.includes('should-run')) guards += 1
      return realLoopXRunner(file, args, { ...options, env: {
        ...options.env, LOOPX_CONTEXT_DELIVERY: 'on',
      } })
    }
    const driver = new LoopXContinuationDriver({
      isLiveAgent: () => true, runner, retryDelaysMs: [0, 0],
      resolveCommand: async () => ({
        file: 'loopx', prefix: [], skillCommand: 'loopx', version: 'loopx 2.3.0',
      }),
      makeTurnInstanceId: () => turnInstanceId,
      warn: message => { warnings.push(message) },
    })
    try {
      driver.observeAgent(testFixture.agent)
      const queued = await waitForQueued(testFixture, warnings)
      testFixture.queued.splice(0, 1)
      ;(testFixture.agent as unknown as { setStatus(value: 'running'): void })
        .setStatus('running')
      driver.onInboxClaimed(testFixture.agent, queued)

      let reachedNext = false
      const admission = await driver.onPreStep(
        testFixture.agent, [queued], new AbortController().signal, async () => {
          reachedNext = true
          await setAllowedSlots(testFixture, 0)
          return { kind: 'enter', messages: [queued] }
        },
      )

      expect(reachedNext).toBe(true)
      expect(admission).toEqual({ kind: 'reject' })
      expect(guards).toBe(3)
      expect(warnings).toHaveLength(1)
      expect(warnings[0]).toContain(
        'pre_step_rejected stage=pre-step-recheck reason=authority-after-next other_kinds=none',
      )
      expect(testFixture.queued).toHaveLength(0)
    } finally {
      await driver.dispose()
    }
  }, 45_000)

  it.each(['held', 'digest mismatch', 'inline digest mismatch', 'paged', 'transport exhausted'] as const)(
    'keeps one guard when the progressive capture is %s',
    async scenario => {
      const testFixture = await fixture()
      if (scenario === 'held') await setAllowedSlots(testFixture, 0)
      let finishEvaluation: () => void = () => {}
      const evaluated = new Promise<void>(resolveWait => { finishEvaluation = resolveWait })
      const warnings: string[] = []
      let guards = 0
      let reads = 0
      const lostReads: string[][] = []
      const runner: FileRunner = async (file, args, options) => {
        const child = { ...options, env: { ...options.env, LOOPX_CONTEXT_DELIVERY: 'on' } }
        if (args.includes('context-read')) {
          reads += 1
          const readerArgs = [...args]
          if (scenario === 'paged') {
            readerArgs[readerArgs.indexOf('--page-bytes') + 1] = '700'
          }
          if (scenario === 'inline digest mismatch') {
            const response = await readOnlySelectionRunner(file, readerArgs, child)
            const page = record(JSON.parse(response.stdout))
            expect(page.ok).toBe(true)
            expect(page.eof).toBe(true)
            expect(page.schema_version).toBe('context_capture_page_v1')
            return { ...response, stdout: JSON.stringify({ ...page, selection_sha256: '0'.repeat(64) }) }
          }
          const response = await realLoopXRunner(file, readerArgs, child)
          if (scenario === 'transport exhausted') {
            expect(record(JSON.parse(response.stdout)).ok).toBe(true)
            lostReads.push(readerArgs)
            throw new LoopXCliError('transport', 'synthetic reader response loss', true)
          }
          return response
        }
        if (!args.includes('should-run')) return realLoopXRunner(file, args, child)
        guards += 1
        const result = await realLoopXRunner(file, args, child)
        if (scenario !== 'digest mismatch') return result
        const navigation = record(JSON.parse(result.stdout))
        expect(navigation.schema_version).toBe('context_capture_navigation_v1')
        const recovery = record(navigation.recovery)
        const reference = String(recovery.reference_file)
        const descriptor = record(JSON.parse(await readFile(reference, 'utf8')))
        const wrong = join(dirname(reference), 'test-wrong-digest.reader.json')
        await writeFile(wrong, JSON.stringify({ ...descriptor, sha256: '0'.repeat(64) }),
          { mode: 0o600, flag: 'wx' })
        return { ...result, stdout: JSON.stringify({
          ...navigation, recovery: { ...recovery, reference_file: wrong },
        }) }
      }

      const driver = new LoopXContinuationDriver({
        isLiveAgent: () => true, runner, retryDelaysMs: [0, 0],
        resolveCommand: async () => ({
          file: 'loopx', prefix: [], skillCommand: 'loopx', version: 'loopx 2.3.0',
        }),
        makeTurnInstanceId: () => turnInstanceId,
        runDetached: async operation => {
          try { return await operation() } finally { finishEvaluation() }
        },
        warn: message => { warnings.push(message) },
      })
      try {
        driver.observeAgent(testFixture.agent)
        await evaluated
        expect(guards).toBe(1)
        if (scenario === 'transport exhausted') {
          expect(reads).toBe(3)
          expect(lostReads).toEqual([lostReads[0], lostReads[0], lostReads[0]])
          expect(testFixture.queued).toHaveLength(0)
          expect(warnings).toHaveLength(1)
          expect(warnings[0]).toContain('admission_failed stage=quota kind=transport')
        } else if (scenario === 'digest mismatch' || scenario === 'inline digest mismatch') {
          expect(reads).toBe(1)
          expect(testFixture.queued).toHaveLength(0)
          expect(warnings).toHaveLength(1)
          expect(warnings[0]).toContain(
            'admission_failed stage=quota kind=invalid_schema cause_kind=invalid_schema',
          )
          expect(warnings[0]).not.toMatch(/invalid page|selection digest did not match/u)
        } else if (scenario === 'held') {
          expect(reads).toBe(2)
          expect(testFixture.queued).toHaveLength(0)
          expect(warnings).toEqual([])
        } else {
          expect(reads).toBeGreaterThan(2)
          const queued = testFixture.queued[0]
          expect(record(queued?.source).turnInstanceId).toBe(turnInstanceId)
          expect(warnings).toEqual([])
        }
        const path = join(testFixture.runtime, 'goals', goalId, 'rollout-event-log.jsonl')
        const events = (await readFile(path, 'utf8')).trim().split('\n')
          .map(line => record(JSON.parse(line)))
        expect(events.filter(event => event.event_kind === 'quota_should_run'
          && event.run_id === turnInstanceId && event.agent_id === agentId)).toHaveLength(1)
      } finally {
        await driver.dispose()
      }
    }, 45_000,
  )
})
