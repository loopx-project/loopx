// @vitest-environment jsdom

import { act } from 'react-dom/test-utils'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import {
  LOOPX_DASHBOARD_URL,
  LoopXDashboardPanel,
  type LoopXDashboardPanelProps,
} from '../src/client/dashboard.tsx'
import {
  DASHBOARD_LOCALES,
  type DashboardLocaleKey,
  type DashboardTranslate,
} from '../src/client/locale.ts'

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
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function translate(key: DashboardLocaleKey): string {
  return DASHBOARD_LOCALES.en[key]
}

const englishT = translate as DashboardTranslate

/** Mount the panel with its exact keyed-dispatch props and locale seat. */
function mountPanel(): HTMLDivElement {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const props = { t: englishT } as LoopXDashboardPanelProps
  act(() => root.render(<LoopXDashboardPanel {...props} />))
  roots.push({ root, container, mounted: true })
  return container
}

/** Let the availability probe settle inside the React act boundary. */
async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
  })
}

describe('LoopX personal workspace panel', () => {
  it('embeds the loopback dashboard and omits the fallback when it responds', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 200 })))
    const container = mountPanel()
    await settle()

    const frame = container.querySelector('iframe')
    expect(frame?.getAttribute('src')).toBe(LOOPX_DASHBOARD_URL)
    expect(frame?.getAttribute('title')).toBe(DASHBOARD_LOCALES.en['iframe.title'])
    expect(container.textContent).not.toContain(DASHBOARD_LOCALES.en['panel.unavailable'])
  })

  it('states the recovery command when the loopback service is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('connection refused')
    }))
    const container = mountPanel()
    await settle()

    expect(container.textContent).toContain(DASHBOARD_LOCALES.en['panel.unavailable'])
    expect(container.textContent).toContain(DASHBOARD_LOCALES.en['panel.retry'])
    // The iframe stays mounted so an operator can start the service behind it.
    expect(container.querySelector('iframe')?.getAttribute('src')).toBe(LOOPX_DASHBOARD_URL)
  })

  it('recovers through the retry control once the service becomes reachable', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('connection refused'))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const container = mountPanel()
    await settle()
    expect(container.textContent).toContain(DASHBOARD_LOCALES.en['panel.unavailable'])

    const retry = container.querySelector('button')
    expect(retry).not.toBeNull()
    await act(async () => {
      retry?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })
    await settle()

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(container.textContent).not.toContain(DASHBOARD_LOCALES.en['panel.unavailable'])
  })
})
