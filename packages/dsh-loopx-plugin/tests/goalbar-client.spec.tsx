// @vitest-environment jsdom

import { act } from 'react-dom/test-utils'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('../src/client/goalbar.module.css', () => ({
  default: new Proxy({}, {
    get: (_target, property) => String(property),
  }),
  ensurePluginStyle: vi.fn(),
}))

import {
  GOALBAR_RESPONSE_VERSION,
  type GoalBarActionResultV1,
  type GoalBarReadResultV1,
  type GoalBarSnapshotV1,
} from '../src/goalbar/protocol.ts'
import { LoopXGoalBar, type LoopXGoalBarProps } from '../src/client/LoopXGoalBar.tsx'
import { apply, inject } from '../src/client/index.tsx'
import {
  GOALBAR_LOCALES,
  type GoalBarLocaleKey,
  type GoalBarTranslate,
} from '../src/client/locale.ts'
import {
  createGoalBarRpc,
  type GoalBarPauseResponseV1,
  type GoalBarReadResponseV1,
  type GoalBarRpc,
  type GoalBarRpcOutcome,
  type GoalBarStartResponseV1,
  type GoalBarWatchAnchorV1,
  type GoalBarWatchResponseV1,
} from '../src/client/rpc.ts'

const REVISION_ONE = `sha256:${'1'.repeat(64)}`
const REVISION_TWO = `sha256:${'2'.repeat(64)}`

type ReadResultFixture = GoalBarReadResultV1 extends infer TResult
  ? TResult extends { readonly sourceRevision: string }
    ? Omit<TResult, 'sourceRevision'> & { readonly sourceRevision?: string }
    : TResult
  : never

type ActionResultFixture = GoalBarActionResultV1 extends infer TResult
  ? TResult extends { readonly sourceRevision: string }
    ? Omit<TResult, 'sourceRevision'> & { readonly sourceRevision?: string }
    : TResult
  : never

const roots: Array<{ root: Root; container: HTMLDivElement; mounted: boolean }> = []

beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  for (const mounted of roots) {
    if (mounted.mounted) act(() => mounted.root.unmount())
    mounted.container.remove()
  }
  roots.length = 0
  document.head.querySelectorAll('style[data-plugin]').forEach(style => style.remove())
  vi.restoreAllMocks()
})

function t(key: GoalBarLocaleKey, params?: Record<string, unknown>): string {
  return GOALBAR_LOCALES.en[key].replace(/\{([^}]+)\}/gu, (_match, name: string) => (
    String(params?.[name] ?? `{${name}}`)
  ))
}

const englishT = t as GoalBarTranslate

function snapshot(
  overrides: Partial<GoalBarSnapshotV1> = {},
): GoalBarSnapshotV1 {
  return {
    sessionId: 'session-one',
    goalId: 'goal-one',
    loopxAgentId: 'codex-main-control',
    goalActivation: 'active',
    agentStatus: 'idle',
    progress: { processed: 2, remaining: 3, total: 5 },
    continuationAdmission: { state: 'clear' },
    ...overrides,
  }
}

function readOutcome(
  sessionId: string,
  result: ReadResultFixture,
): GoalBarRpcOutcome<GoalBarReadResponseV1> {
  return {
    ok: true,
    response: {
      v: GOALBAR_RESPONSE_VERSION,
      op: 'read',
      sessionId,
      result: { ...result, sourceRevision: result.sourceRevision ?? REVISION_ONE },
    },
  }
}

function watchOutcome(
  sessionId: string,
  result: GoalBarWatchResponseV1['result'],
): GoalBarRpcOutcome<GoalBarWatchResponseV1> {
  return {
    ok: true,
    response: {
      v: GOALBAR_RESPONSE_VERSION,
      op: 'watch',
      sessionId,
      result,
    },
  }
}

function actionOutcome<T extends 'start' | 'pause'>(
  op: T,
  sessionId: string,
  result: ActionResultFixture,
): GoalBarRpcOutcome<T extends 'start' ? GoalBarStartResponseV1 : GoalBarPauseResponseV1> {
  return {
    ok: true,
    response: {
      v: GOALBAR_RESPONSE_VERSION,
      op,
      sessionId,
      result: 'baseSessionEventSeq' in result
        ? { ...result, sourceRevision: result.sourceRevision ?? REVISION_TWO }
        : result,
    },
  } as GoalBarRpcOutcome<T extends 'start'
    ? GoalBarStartResponseV1
    : GoalBarPauseResponseV1>
}

function abortOutcome<T>(signal: AbortSignal): Promise<GoalBarRpcOutcome<T>> {
  return new Promise(resolve => {
    signal.addEventListener('abort', () => {
      resolve({ ok: false, code: 'transport_error' })
    }, { once: true })
  })
}

function fakeRpc(overrides: Partial<GoalBarRpc> = {}): GoalBarRpc {
  return {
    read: overrides.read ?? ((sessionId) => Promise.resolve(readOutcome(sessionId, {
      kind: 'hidden',
      reason: 'binding_missing',
      baseSessionEventSeq: null,
    }))),
    watch: overrides.watch ?? ((_sessionId, _cursor, signal) => abortOutcome(signal)),
    start: overrides.start ?? ((_sessionId, _expected, signal) => abortOutcome(signal)),
    pause: overrides.pause ?? ((_sessionId, _expected, signal) => abortOutcome(signal)),
  }
}

function mount(props: Omit<LoopXGoalBarProps, 't'> & { readonly t?: GoalBarTranslate }) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const mounted = { root, container, mounted: true }
  roots.push(mounted)

  const render = (next: typeof props) => {
    act(() => root.render(<LoopXGoalBar {...next} t={next.t ?? englishT} />))
  }
  render(props)
  return {
    container,
    rerender: render,
    unmount() {
      if (!mounted.mounted) return
      act(() => root.unmount())
      mounted.mounted = false
    },
  }
}

async function flush(rounds = 4): Promise<void> {
  await act(async () => {
    for (let index = 0; index < rounds; index += 1) await Promise.resolve()
  })
}

function button(container: Element, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')]
    .find(candidate => candidate.textContent === label)
  if (!(found instanceof HTMLButtonElement)) throw new Error(`missing button ${label}`)
  return found
}

describe('LoopXGoalBar presentation', () => {
  it.each([
    ['active', 'idle', 'Active', 'Pause', false],
    ['active', 'running', 'Running', 'Pause', false],
    ['stopped', 'idle', 'Paused', 'Start', false],
    ['stopped', 'running', 'Paused', 'Start', true],
  ] as const)(
    'maps %s + %s to fixed status and action text',
    async (goalActivation, agentStatus, status, action, disabled) => {
      const goalId = 'goal-with-a-very-long-visual-identity-123456789'
      const rpc = fakeRpc({
        read: sessionId => Promise.resolve(readOutcome(sessionId, {
          kind: 'present',
          snapshot: snapshot({ sessionId, goalId, goalActivation, agentStatus }),
          baseSessionEventSeq: 7,
        })),
      })
      const view = mount({ rpcSessionId: 'session-one', rpc })
      await flush()

      const region = view.container.querySelector('[aria-label="LoopX Goal status"]')
      expect(region?.textContent).toContain(status)
      expect(button(view.container, action).disabled).toBe(disabled)
      expect(view.container.querySelector('[title]')?.getAttribute('title')).toBe(goalId)
      expect(view.container.querySelector('progress')?.getAttribute('aria-label'))
        .toBe('Agent-lane todos: 2 of 5 processed')
    },
  )

  it('renders empty progress as 0 / 0 with the exact accessible label', async () => {
    const rpc = fakeRpc({
      read: sessionId => Promise.resolve(readOutcome(sessionId, {
        kind: 'present',
        snapshot: snapshot({
          sessionId,
          progress: { processed: 0, remaining: 0, total: 0 },
        }),
        baseSessionEventSeq: 0,
      })),
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush()

    expect(view.container.textContent).toContain('0 / 0')
    expect(view.container.querySelector('progress')?.getAttribute('aria-label'))
      .toBe('No agent-lane todos')
  })

  it('surfaces an unadmitted continuation with its category and recovery action', async () => {
    const rpc = fakeRpc({
      read: sessionId => Promise.resolve(readOutcome(sessionId, {
        kind: 'present',
        snapshot: snapshot({
          sessionId,
          continuationAdmission: { state: 'failed', category: 'invalid_response' },
        }),
        baseSessionEventSeq: 7,
      })),
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush()

    const status = view.container.querySelector('[role="status"]')
    expect(status?.textContent).toContain('Automatic continuation is not admitted.')
    expect(status?.textContent).toContain('LoopX returned an incompatible response.')
    expect(status?.textContent).toContain('Pause, then Start, to retry automatic continuation.')
    // The failure is not an idle, paused, or completed state: the Goal keeps
    // its own status text and the Pause recovery action stays available.
    expect(view.container.textContent).toContain('Active')
    expect(button(view.container, 'Pause').disabled).toBe(false)
    expect(view.container.querySelector('button.refresh')).toBeNull()
  })

  it('renders no continuation failure line for a clear admission state', async () => {
    const rpc = fakeRpc({
      read: sessionId => Promise.resolve(readOutcome(sessionId, {
        kind: 'present',
        snapshot: snapshot({ sessionId }),
        baseSessionEventSeq: 7,
      })),
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush()

    expect(view.container.textContent).not.toContain('not admitted')
    expect(view.container.querySelector('[role="status"]')).toBeNull()
  })

  it.each(['binding_missing', 'binding_ambiguous'] as const)(
    'renders no row for %s',
    async reason => {
      const rpc = fakeRpc({
        read: sessionId => Promise.resolve(readOutcome(sessionId, {
          kind: 'hidden',
          reason,
          baseSessionEventSeq: 1,
        })),
      })
      const view = mount({ rpcSessionId: 'session-one', rpc })
      await flush()
      expect(view.container.innerHTML).toBe('')
    },
  )
})

describe('GoalBar read/watch generations', () => {
  it.each([
    ['business', readOutcome('session-one', {
      kind: 'fault', code: 'activation_read_failed', baseSessionEventSeq: 2,
    })],
    ['transport', { ok: false, code: 'transport_error' } as const],
    ['protocol', { ok: false, code: 'protocol_error' } as const],
  ])('hides an initial %s fault and stops before watch', async (_kind, outcome) => {
    let watches = 0
    const rpc = fakeRpc({
      read: () => Promise.resolve(outcome),
      watch: (_sessionId, _cursor, signal) => {
        watches += 1
        return abortOutcome(signal)
      },
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush()
    expect(view.container.innerHTML).toBe('')
    expect(watches).toBe(0)
  })

  it('renews a timeout from the same V2 anchor and reads only after source change', async () => {
    let reads = 0
    const anchors: GoalBarWatchAnchorV1[] = []
    const rpc = fakeRpc({
      read: sessionId => {
        reads += 1
        return Promise.resolve(readOutcome(sessionId, {
          kind: 'present',
          snapshot: snapshot({
            sessionId,
            progress: reads === 1
              ? { processed: 2, remaining: 3, total: 5 }
              : { processed: 3, remaining: 2, total: 5 },
          }),
          baseSessionEventSeq: reads === 1 ? 4 : 5,
        }))
      },
      watch: (sessionId, anchor, signal) => {
        anchors.push(anchor)
        if (anchors.length === 1) {
          return Promise.resolve(watchOutcome(sessionId, {
            kind: 'timeout', sessionEventSeq: 4,
          }))
        }
        if (anchors.length === 2) {
          return Promise.resolve(watchOutcome(sessionId, {
            kind: 'source_changed', sessionEventSeq: 5,
          }))
        }
        return abortOutcome(signal)
      },
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush(8)

    expect(reads).toBe(2)
    expect(anchors).toEqual([
      {
        afterSessionEventSeq: 4,
        sourceRevision: REVISION_ONE,
        expected: { goalId: 'goal-one', loopxAgentId: 'codex-main-control' },
        agentStatus: 'idle',
      },
      {
        afterSessionEventSeq: 4,
        sourceRevision: REVISION_ONE,
        expected: { goalId: 'goal-one', loopxAgentId: 'codex-main-control' },
        agentStatus: 'idle',
      },
      {
        afterSessionEventSeq: 5,
        sourceRevision: REVISION_ONE,
        expected: { goalId: 'goal-one', loopxAgentId: 'codex-main-control' },
        agentStatus: 'idle',
      },
    ])
    expect(view.container.textContent).toContain('3 / 5')
  })

  it('turns a hidden binding into a visible row after source_changed', async () => {
    let reads = 0
    const anchors: GoalBarWatchAnchorV1[] = []
    const rpc = fakeRpc({
      read: sessionId => {
        reads += 1
        return Promise.resolve(readOutcome(sessionId, reads === 1
          ? {
              kind: 'hidden',
              reason: 'binding_missing',
              baseSessionEventSeq: 8,
              sourceRevision: REVISION_ONE,
            }
          : {
              kind: 'present',
              snapshot: snapshot({ sessionId }),
              baseSessionEventSeq: 9,
              sourceRevision: REVISION_TWO,
            }))
      },
      watch: (sessionId, anchor, signal) => {
        anchors.push(anchor)
        return anchors.length === 1
          ? Promise.resolve(watchOutcome(sessionId, {
              kind: 'source_changed', sessionEventSeq: 9,
            }))
          : abortOutcome(signal)
      },
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush(8)

    expect(reads).toBe(2)
    expect(anchors[0]).toEqual({
      afterSessionEventSeq: 8,
      sourceRevision: REVISION_ONE,
      expected: null,
      agentStatus: null,
    })
    expect(anchors[1]?.sourceRevision).toBe(REVISION_TWO)
    expect(view.container.textContent).toContain('goal-one')
  })

  it('applies runtime_changed in place without a business read', async () => {
    let reads = 0
    const anchors: GoalBarWatchAnchorV1[] = []
    const rpc = fakeRpc({
      read: sessionId => {
        reads += 1
        return Promise.resolve(readOutcome(sessionId, {
          kind: 'present',
          snapshot: snapshot({ sessionId, agentStatus: 'idle' }),
          baseSessionEventSeq: 12,
        }))
      },
      watch: (sessionId, anchor, signal) => {
        anchors.push(anchor)
        return anchors.length === 1
          ? Promise.resolve(watchOutcome(sessionId, {
              kind: 'runtime_changed',
              sessionEventSeq: 13,
              agentStatus: 'running',
            }))
          : abortOutcome(signal)
      },
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush(8)

    expect(reads).toBe(1)
    expect(anchors[1]).toMatchObject({
      afterSessionEventSeq: 13,
      sourceRevision: REVISION_ONE,
      agentStatus: 'running',
    })
    expect(view.container.textContent).toContain('Running')
  })

  it('absorbs runtime_changed while hidden without creating a snapshot', async () => {
    const anchors: GoalBarWatchAnchorV1[] = []
    const rpc = fakeRpc({
      read: sessionId => Promise.resolve(readOutcome(sessionId, {
        kind: 'hidden',
        reason: 'binding_missing',
        baseSessionEventSeq: 20,
      })),
      watch: (sessionId, anchor, signal) => {
        anchors.push(anchor)
        return anchors.length === 1
          ? Promise.resolve(watchOutcome(sessionId, {
              kind: 'runtime_changed',
              sessionEventSeq: 21,
              agentStatus: 'running',
            }))
          : abortOutcome(signal)
      },
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush(8)

    expect(view.container.innerHTML).toBe('')
    expect(anchors[1]).toEqual({
      afterSessionEventSeq: 21,
      sourceRevision: REVISION_ONE,
      expected: null,
      agentStatus: 'running',
    })
  })

  it('stops on a fault, keeps last-good stale, and resumes only on Refresh', async () => {
    let reads = 0
    let watches = 0
    const rpc = fakeRpc({
      read: sessionId => {
        reads += 1
        if (reads === 2) {
          return Promise.resolve(readOutcome(sessionId, {
            kind: 'fault', code: 'todo_read_failed', baseSessionEventSeq: 3,
          }))
        }
        return Promise.resolve(readOutcome(sessionId, {
          kind: 'present',
          snapshot: snapshot({
            sessionId,
            progress: reads === 1
              ? { processed: 2, remaining: 3, total: 5 }
              : { processed: 4, remaining: 1, total: 5 },
          }),
          baseSessionEventSeq: reads === 1 ? 2 : 3,
        }))
      },
      watch: (sessionId, _cursor, signal) => {
        watches += 1
        return watches === 1
          ? Promise.resolve(watchOutcome(sessionId, {
              kind: 'source_changed', sessionEventSeq: 3,
            }))
          : abortOutcome(signal)
      },
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush(8)

    expect(reads).toBe(2)
    expect(watches).toBe(1)
    expect(view.container.textContent).toContain('2 / 5')
    expect(view.container.textContent).toContain('Todo progress could not be read.')
    expect(button(view.container, 'Pause').disabled).toBe(true)

    act(() => button(view.container, 'Refresh').click())
    await flush(6)
    expect(reads).toBe(3)
    expect(watches).toBe(2)
    expect(view.container.textContent).toContain('4 / 5')
    expect(view.container.textContent).not.toContain('Todo progress could not be read.')
    expect(button(view.container, 'Pause').disabled).toBe(false)
  })

  it('stops a watch fault and keeps the last-good row disabled', async () => {
    let watches = 0
    const rpc = fakeRpc({
      read: sessionId => Promise.resolve(readOutcome(sessionId, {
        kind: 'present', snapshot: snapshot({ sessionId }), baseSessionEventSeq: 2,
      })),
      watch: sessionId => {
        watches += 1
        return Promise.resolve(watchOutcome(sessionId, {
          kind: 'fault', code: 'session_unavailable',
        }))
      },
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush(6)

    expect(watches).toBe(1)
    expect(view.container.textContent).toContain('The session is unavailable.')
    expect(button(view.container, 'Pause').disabled).toBe(true)
    expect(button(view.container, 'Refresh')).toBeInstanceOf(HTMLButtonElement)
  })

  it('aborts and fences old completions on Session switch and unmount', async () => {
    let resolveOld!: (value: GoalBarRpcOutcome<GoalBarReadResponseV1>) => void
    let oldSignal: AbortSignal | undefined
    let currentWatchSignal: AbortSignal | undefined
    const oldRead = new Promise<GoalBarRpcOutcome<GoalBarReadResponseV1>>(resolve => {
      resolveOld = resolve
    })
    const rpc = fakeRpc({
      read: (sessionId, signal) => {
        if (sessionId === 'session-old') {
          oldSignal = signal
          return oldRead
        }
        return Promise.resolve(readOutcome(sessionId, {
          kind: 'present',
          snapshot: snapshot({ sessionId, goalId: 'goal-new' }),
          baseSessionEventSeq: 9,
        }))
      },
      watch: (_sessionId, _cursor, signal) => {
        currentWatchSignal = signal
        return abortOutcome(signal)
      },
    })
    const view = mount({ rpcSessionId: 'session-old', rpc })
    await flush()
    view.rerender({ rpcSessionId: 'session-new', rpc })
    await flush()

    expect(oldSignal?.aborted).toBe(true)
    expect(view.container.textContent).toContain('goal-new')
    resolveOld(readOutcome('session-old', {
      kind: 'present',
      snapshot: snapshot({ sessionId: 'session-old', goalId: 'goal-old' }),
      baseSessionEventSeq: 8,
    }))
    await flush()
    expect(view.container.textContent).not.toContain('goal-old')

    view.unmount()
    expect(currentWatchSignal?.aborted).toBe(true)
  })

  it('keeps multiple instances for one Session fully independent', async () => {
    const watchSignals: AbortSignal[] = []
    const rpc = fakeRpc({
      read: sessionId => Promise.resolve(readOutcome(sessionId, {
        kind: 'present', snapshot: snapshot({ sessionId }), baseSessionEventSeq: 1,
      })),
      watch: (_sessionId, _cursor, signal) => {
        watchSignals.push(signal)
        return abortOutcome(signal)
      },
    })
    const first = mount({ rpcSessionId: 'session-one', rpc })
    const second = mount({ rpcSessionId: 'session-one', rpc })
    await flush()

    expect(watchSignals).toHaveLength(2)
    expect(watchSignals[0]).not.toBe(watchSignals[1])
    first.unmount()
    expect(watchSignals[0]?.aborted).toBe(true)
    expect(watchSignals[1]?.aborted).toBe(false)
    second.unmount()
    expect(watchSignals[1]?.aborted).toBe(true)
  })

  it('treats a Connection reset as a fresh, aborting read generation', async () => {
    let reads = 0
    let reset: (() => void) | undefined
    const watchSignals: AbortSignal[] = []
    const rpc = fakeRpc({
      read: sessionId => {
        reads += 1
        return Promise.resolve(readOutcome(sessionId, {
          kind: 'present',
          snapshot: snapshot({
            sessionId,
            progress: reads === 1
              ? { processed: 1, remaining: 4, total: 5 }
              : { processed: 2, remaining: 3, total: 5 },
          }),
          baseSessionEventSeq: reads,
        }))
      },
      watch: (_sessionId, _cursor, signal) => {
        watchSignals.push(signal)
        return abortOutcome(signal)
      },
    })
    const view = mount({
      rpcSessionId: 'session-one',
      rpc,
      subscribeConnectionReset: listener => {
        reset = listener
        return () => {
          reset = undefined
        }
      },
    })
    await flush()
    expect(view.container.textContent).toContain('1 / 5')

    act(() => reset?.())
    await flush()
    expect(watchSignals[0]?.aborted).toBe(true)
    expect(reads).toBe(2)
    expect(view.container.textContent).toContain('2 / 5')
  })

  it('fences a late runtime watch result after a Connection reset', async () => {
    let reads = 0
    let reset: (() => void) | undefined
    let resolveOldWatch!: (value: GoalBarRpcOutcome<GoalBarWatchResponseV1>) => void
    const oldWatch = new Promise<GoalBarRpcOutcome<GoalBarWatchResponseV1>>(resolve => {
      resolveOldWatch = resolve
    })
    let watches = 0
    const rpc = fakeRpc({
      read: sessionId => {
        reads += 1
        return Promise.resolve(readOutcome(sessionId, {
          kind: 'present',
          snapshot: snapshot({ sessionId, agentStatus: 'idle' }),
          baseSessionEventSeq: reads,
        }))
      },
      watch: (_sessionId, _anchor, signal) => {
        watches += 1
        return watches === 1 ? oldWatch : abortOutcome(signal)
      },
    })
    const view = mount({
      rpcSessionId: 'session-one',
      rpc,
      subscribeConnectionReset: listener => {
        reset = listener
        return () => { reset = undefined }
      },
    })
    await flush()
    act(() => reset?.())
    await flush()

    resolveOldWatch(watchOutcome('session-one', {
      kind: 'runtime_changed',
      sessionEventSeq: 2,
      agentStatus: 'running',
    }))
    await flush()
    expect(reads).toBe(2)
    expect(view.container.textContent).toContain('Active')
    expect(view.container.textContent).not.toContain('Running')
  })
})

describe('GoalBar actions', () => {
  it('guards same-frame duplicates, stays non-optimistic, and adopts the fresh result', async () => {
    let pauseCalls = 0
    const watchAnchors: GoalBarWatchAnchorV1[] = []
    let resolvePause!: (value: GoalBarRpcOutcome<GoalBarPauseResponseV1>) => void
    const pause = new Promise<GoalBarRpcOutcome<GoalBarPauseResponseV1>>(resolve => {
      resolvePause = resolve
    })
    const rpc = fakeRpc({
      read: sessionId => Promise.resolve(readOutcome(sessionId, {
        kind: 'present', snapshot: snapshot({ sessionId }), baseSessionEventSeq: 4,
      })),
      pause: () => {
        pauseCalls += 1
        return pause
      },
      watch: (_sessionId, anchor, signal) => {
        watchAnchors.push(anchor)
        return abortOutcome(signal)
      },
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush()

    const pauseButton = button(view.container, 'Pause')
    act(() => {
      pauseButton.click()
      pauseButton.click()
    })
    expect(pauseCalls).toBe(1)
    expect(view.container.textContent).toContain('Active')
    expect(button(view.container, 'Pause').disabled).toBe(true)

    resolvePause(actionOutcome('pause', 'session-one', {
      kind: 'succeeded',
      snapshot: snapshot({ goalActivation: 'stopped', agentStatus: 'idle' }),
      baseSessionEventSeq: 5,
    }))
    await flush()
    expect(view.container.textContent).toContain('Paused')
    expect(button(view.container, 'Start').disabled).toBe(false)
    expect(watchAnchors.at(-1)).toEqual({
      afterSessionEventSeq: 5,
      sourceRevision: REVISION_TWO,
      expected: { goalId: 'goal-one', loopxAgentId: 'codex-main-control' },
      agentStatus: 'idle',
    })
  })

  it.each([
    [
      { kind: 'unknown', code: 'operation_result_unknown' } as const,
      'The action result is uncertain.',
    ],
    [
      { kind: 'rejected', code: 'binding_mismatch' } as const,
      'The LoopX binding changed.',
    ],
  ])('locks %s until an explicit successful Refresh', async (result, message) => {
    let reads = 0
    let pauses = 0
    const rpc = fakeRpc({
      read: sessionId => {
        reads += 1
        return Promise.resolve(readOutcome(sessionId, {
          kind: 'present', snapshot: snapshot({ sessionId }), baseSessionEventSeq: reads,
        }))
      },
      pause: sessionId => {
        pauses += 1
        return Promise.resolve(actionOutcome('pause', sessionId, result))
      },
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush()
    act(() => button(view.container, 'Pause').click())
    await flush()

    expect(view.container.textContent).toContain(message)
    expect(button(view.container, 'Pause').disabled).toBe(true)
    act(() => button(view.container, 'Pause').click())
    expect(pauses).toBe(1)

    act(() => button(view.container, 'Refresh').click())
    await flush()
    expect(reads).toBe(2)
    expect(view.container.textContent).not.toContain(message)
    expect(button(view.container, 'Pause').disabled).toBe(false)
  })

  it.each([
    [
      {
        kind: 'applied_with_warning',
        code: 'driver_sync_failed',
        snapshot: snapshot({ goalActivation: 'stopped', agentStatus: 'idle' }),
        baseSessionEventSeq: 2,
      } as const,
      'Paused',
      'LoopX changed, but the session did not sync.',
    ],
    [
      { kind: 'applied_with_warning', code: 'post_read_failed' } as const,
      'Active',
      'LoopX changed, but the latest status is unavailable.',
    ],
  ])('locks action warning %s and adopts only a supplied fresh snapshot', async (
    result,
    expectedStatus,
    message,
  ) => {
    let pauses = 0
    const rpc = fakeRpc({
      read: sessionId => Promise.resolve(readOutcome(sessionId, {
        kind: 'present', snapshot: snapshot({ sessionId }), baseSessionEventSeq: 1,
      })),
      pause: sessionId => {
        pauses += 1
        return Promise.resolve(actionOutcome('pause', sessionId, result))
      },
    })
    const view = mount({ rpcSessionId: 'session-one', rpc })
    await flush()
    act(() => button(view.container, 'Pause').click())
    await flush()

    expect(view.container.textContent).toContain(expectedStatus)
    expect(view.container.textContent).toContain(message)
    const action = button(view.container, expectedStatus === 'Paused' ? 'Start' : 'Pause')
    expect(action.disabled).toBe(true)
    act(() => action.click())
    expect(pauses).toBe(1)
    expect(button(view.container, 'Refresh')).toBeInstanceOf(HTMLButtonElement)
  })

  it('fences a late action result after a Connection reset generation', async () => {
    let reset: (() => void) | undefined
    let resolvePause!: (value: GoalBarRpcOutcome<GoalBarPauseResponseV1>) => void
    const pause = new Promise<GoalBarRpcOutcome<GoalBarPauseResponseV1>>(resolve => {
      resolvePause = resolve
    })
    const rpc = fakeRpc({
      read: sessionId => Promise.resolve(readOutcome(sessionId, {
        kind: 'present',
        snapshot: snapshot({ sessionId, goalActivation: 'active' }),
        baseSessionEventSeq: 3,
      })),
      pause: () => pause,
    })
    const view = mount({
      rpcSessionId: 'session-one',
      rpc,
      subscribeConnectionReset: listener => {
        reset = listener
        return () => { reset = undefined }
      },
    })
    await flush()
    act(() => button(view.container, 'Pause').click())
    act(() => reset?.())
    await flush()

    resolvePause(actionOutcome('pause', 'session-one', {
      kind: 'succeeded',
      snapshot: snapshot({ goalActivation: 'stopped' }),
      baseSessionEventSeq: 4,
    }))
    await flush()
    expect(view.container.textContent).toContain('Active')
    expect(view.container.textContent).not.toContain('Paused')
  })
})

describe('GoalBar Connection and registration boundaries', () => {
  it('discards arbitrary carrier and exception messages and strictly decodes success', async () => {
    const secret = 'SECRET carrier path and stack'
    const log = vi.spyOn(console, 'log').mockImplementation(() => undefined)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const signal = new AbortController().signal
    const failed = createGoalBarRpc({
      call: () => Promise.resolve({
        ok: false,
        error: { code: 'internal', message: secret, details: {} },
      }),
    })
    expect(await failed.read('session-one', signal)).toEqual({
      ok: false, code: 'transport_error',
    })

    const malformed = createGoalBarRpc({
      call: () => Promise.resolve({ ok: true, value: {
        v: GOALBAR_RESPONSE_VERSION,
        op: 'read',
        sessionId: 'session-one',
        result: {
          kind: 'hidden', reason: 'binding_missing', baseSessionEventSeq: 1,
          message: secret,
        },
      } }),
    })
    expect(await malformed.read('session-one', signal)).toEqual({
      ok: false, code: 'protocol_error',
    })

    const thrown = createGoalBarRpc({ call: () => Promise.reject(new Error(secret)) })
    expect(await thrown.read('session-one', signal)).toEqual({
      ok: false, code: 'transport_error',
    })
    expect(log).not.toHaveBeenCalled()
    expect(warn).not.toHaveBeenCalled()
    expect(error).not.toHaveBeenCalled()
  })

  it('uses the exact channel, endpoint, payload, and AbortSignal', async () => {
    const call = vi.fn((_channel: string, _endpoint: string, payload: unknown) => Promise.resolve({
      ok: true as const,
      value: {
        v: GOALBAR_RESPONSE_VERSION,
        op: 'read',
        sessionId: (payload as { sessionId: string }).sessionId,
        result: {
          kind: 'hidden',
          reason: 'binding_missing',
          baseSessionEventSeq: null,
          sourceRevision: REVISION_ONE,
        },
      },
    }))
    const rpc = createGoalBarRpc({ call })
    const controller = new AbortController()
    await rpc.read('session-one', controller.signal)
    expect(call).toHaveBeenCalledWith(
      '/loopx',
      'goalbar/read',
      {
        v: 'loopx_goalbar_request_v2',
        op: 'read',
        sessionId: 'session-one',
      },
      controller.signal,
    )
  })

  it('selects the DSH 0.1.5 shared API endpoint without prose heuristics', async () => {
    const call = vi.fn((_channel: string, _endpoint: string, payload: unknown) => Promise.resolve({
      ok: true as const,
      value: {
        v: GOALBAR_RESPONSE_VERSION,
        op: 'read',
        sessionId: (payload as { sessionId: string }).sessionId,
        result: {
          kind: 'hidden',
          reason: 'binding_missing',
          baseSessionEventSeq: null,
          sourceRevision: REVISION_ONE,
        },
      },
    }))
    const rpc = createGoalBarRpc({ call }, true)
    const controller = new AbortController()
    await rpc.read('session-one', controller.signal)

    expect(call).toHaveBeenCalledWith(
      '/api',
      'loopx.goalbar',
      {
        v: 'loopx_goalbar_request_v2',
        op: 'read',
        sessionId: 'session-one',
      },
      controller.signal,
    )
  })

  it('sends the complete V2 watch anchor on the exact watch endpoint', async () => {
    const call = vi.fn((_channel: string, _endpoint: string, payload: unknown) => Promise.resolve({
      ok: true as const,
      value: {
        v: GOALBAR_RESPONSE_VERSION,
        op: 'watch',
        sessionId: (payload as { sessionId: string }).sessionId,
        result: { kind: 'timeout', sessionEventSeq: 17 },
      },
    }))
    const rpc = createGoalBarRpc({ call })
    const controller = new AbortController()
    const anchor: GoalBarWatchAnchorV1 = {
      afterSessionEventSeq: 17,
      sourceRevision: REVISION_ONE,
      expected: { goalId: 'goal-one', loopxAgentId: 'codex-main-control' },
      agentStatus: 'running',
    }
    await rpc.watch('session-one', anchor, controller.signal)

    expect(call).toHaveBeenCalledWith(
      '/loopx',
      'goalbar/watch',
      {
        v: 'loopx_goalbar_request_v2',
        op: 'watch',
        sessionId: 'session-one',
        ...anchor,
      },
      controller.signal,
    )
  })

  it('registers only the injected Session identity and removes exact-owned CSS', async () => {
    const css = await import('../src/client/goalbar.module.css')
    const ensure = vi.mocked(css.ensurePluginStyle)
    const effects = new Map<string, () => void>()
    let registration: {
      options: {
        id: string
        order: number
        inject: (sessionId: string) => Record<string, unknown>
      }
      component: unknown
    } | undefined
    const registerLocale = vi.fn(() => () => undefined)
    const injectedKeys: string[] = []
    const registrations: Array<{ options: Record<string, unknown>; component: unknown }> = []
    const ctx = {
      connection: { rpc: { call: vi.fn() } },
      locale: { register: registerLocale },
      slots: {
        inject: vi.fn((key: string, install: () => () => void) => {
          injectedKeys.push(key)
          return install()
        }),
        register: vi.fn((options: typeof registration extends { options: infer O } ? O : never, component: unknown) => {
          registration = { options, component }
          registrations.push({ options: options as unknown as Record<string, unknown>, component })
          return () => undefined
        }),
      },
      on: vi.fn(() => () => true),
      effect: vi.fn((execute: () => () => void, label: string) => {
        effects.set(label, execute())
        return () => Promise.resolve()
      }),
    }

    expect(inject).toEqual(['connection', 'locale', 'slots'])
    apply(ctx as never)
    expect(ensure).toHaveBeenCalledOnce()
    expect(registerLocale).toHaveBeenCalledWith('loopx.goalbar', GOALBAR_LOCALES)
    expect(ctx.slots.inject).toHaveBeenCalledWith(
      'conversation.input.dock',
      expect.any(Function),
    )
    expect(registration?.options.id).toBe('loopx-goal')
    expect(registration?.options.order).toBe(15)
    expect(registration?.component).toBe(LoopXGoalBar)
    // The panel contributes one global sidebar row plus its keyed central
    // panel; both must be present so one row always selects one panel.
    expect(injectedKeys).toEqual([
      'sidebar.panellist',
      'main',
      'conversation.input.dock',
    ])
    expect(registerLocale).toHaveBeenCalledWith('loopx.dashboard', expect.any(Object))
    expect(registrations[0]?.options).toMatchObject({
      name: 'sidebar.panellist',
      id: 'loopx-dashboard',
      order: 7,
    })
    expect(registrations[0]?.options.label).toBeTypeOf('function')
    expect(registrations[1]?.options).toMatchObject({
      name: 'main',
      key: 'loopx-dashboard',
      locale: 'loopx.dashboard',
    })
    const injected = registration?.options.inject('session-injected')
    expect(injected?.rpcSessionId).toBe('session-injected')
    expect(injected).not.toHaveProperty('sessionId')
    expect(injected).not.toHaveProperty('goal')
    expect(injected).not.toHaveProperty('remote')

    const owned = document.createElement('style')
    owned.dataset.plugin = 'dsh-loopx-plugin'
    const lookalike = document.createElement('style')
    lookalike.dataset.plugin = 'dsh-loopx-plugin-extra'
    document.head.append(owned, lookalike)
    effects.get('dsh-loopx-plugin CSS ownership')?.()
    expect(owned.isConnected).toBe(false)
    expect(lookalike.isConnected).toBe(true)
  })

  it('claims the CSS disposer before a later apply step can fail', () => {
    const owned = document.createElement('style')
    owned.dataset.plugin = 'dsh-loopx-plugin'
    document.head.appendChild(owned)
    let cssDispose: (() => void) | undefined
    const order: string[] = []
    const ctx = {
      connection: { rpc: { call: vi.fn() } },
      locale: {
        register: () => {
          order.push('locale')
          throw new Error('locale registration failed')
        },
      },
      slots: { inject: vi.fn(), register: vi.fn() },
      effect: (execute: () => () => void, label: string) => {
        order.push(label)
        const dispose = execute()
        if (label === 'dsh-loopx-plugin CSS ownership') cssDispose = dispose
        return () => Promise.resolve()
      },
    }

    expect(() => apply(ctx as never)).toThrow('locale registration failed')
    expect(order).toEqual([
      'dsh-loopx-plugin CSS ownership',
      'dsh-loopx-plugin GoalBar locale',
      'locale',
    ])
    expect(cssDispose).toBeTypeOf('function')
    cssDispose?.()
    expect(owned.isConnected).toBe(false)
  })
})
