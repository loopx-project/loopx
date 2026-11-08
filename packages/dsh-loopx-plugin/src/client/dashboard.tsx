import { useEffect, useState } from 'react'
import type { JSX } from 'react'
import type { MainPanelId } from '@deepseek-ai/dsh-client-ui-layout/client'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'

import type { DashboardTranslate } from './locale.ts'

/**
 * Sidebar panel id, reused verbatim as the `main` slot key so one exact
 * sidebar entry and its central panel always travel together.
 */
export const LOOPX_DASHBOARD_PANEL_ID = 'loopx-dashboard' as MainPanelId

/**
 * Loopback address printed by `loopx dashboard`. The panel only presents the
 * already-running local workspace; it never starts or owns that service.
 */
export const LOOPX_DASHBOARD_URL = 'http://127.0.0.1:8767/chat/'

/** Row title of the LoopX personal workspace; a brand name in every locale. */
export const LOOPX_DASHBOARD_LABEL = 'LoopX'

/** How long one availability probe may wait before the panel reports the service as off. */
const UNAVAILABLE_PROBE_MS = 2500

type DashboardAvailability = 'probing' | 'ready' | 'unavailable'

/** Props of the central LoopX workspace panel: keyed dispatch plus its locale seat. */
export type LoopXDashboardPanelProps =
  & PropsRuntime<'main', MainPanelId>
  & { readonly t: DashboardTranslate }

/** Sidebar icon for the LoopX personal workspace panel. */
export function LoopXDashboardIcon({
  size,
}: PropsRuntime<'sidebar.panellist'>): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 9h18M9 9v11" />
    </svg>
  )
}

/**
 * Central panel presenting the LoopX personal workspace dashboard served by
 * `loopx dashboard`. The iframe keeps LoopX as the owning UI; this panel is a
 * navigation surface, not a second control plane. When the loopback service is
 * absent the panel states the recovery command instead of rendering a blank
 * frame, and retrying re-probes without reloading DSH.
 */
export function LoopXDashboardPanel({
  t,
}: LoopXDashboardPanelProps): JSX.Element {
  const [attempt, setAttempt] = useState(0)
  const [availability, setAvailability] = useState<DashboardAvailability>('probing')
  useEffect(() => {
    const controller = new AbortController()
    const timer = setTimeout(() => {
      setAvailability('unavailable')
      controller.abort()
    }, UNAVAILABLE_PROBE_MS)
    setAvailability('probing')
    fetch(LOOPX_DASHBOARD_URL, {
      mode: 'no-cors',
      cache: 'no-store',
      signal: controller.signal,
    }).then(
      () => {
        clearTimeout(timer)
        setAvailability('ready')
      },
      () => {
        clearTimeout(timer)
        setAvailability('unavailable')
      },
    )
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [attempt])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        width: '100%',
        height: '100%',
        minHeight: 0,
      }}
    >
      <iframe
        key={attempt}
        src={LOOPX_DASHBOARD_URL}
        title={t('iframe.title')}
        allow="fullscreen"
        style={{
          flex: '1 1 auto',
          width: '100%',
          border: 0,
          display: 'block',
        }}
      />
      {availability === 'unavailable' && (
        <div
          role="status"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '6px 12px',
            borderTop: '1px solid var(--color-border, var(--dsw-alias-border-l1, #ebebeb))',
            color: 'var(--color-ink, var(--dsw-alias-label-primary, #171717))',
            fontFamily: 'var(--font-sans, var(--dsw-font-family, "Geist", "Inter", "Helvetica Neue", Arial, sans-serif))',
            fontSize: 12,
            lineHeight: '20px',
          }}
        >
          <span>
            {t('panel.unavailable')}
          </span>
          <button
            type="button"
            onClick={() => {
              setAvailability('probing')
              setAttempt(value => value + 1)
            }}
            style={{
              marginLeft: 'auto',
              padding: '2px 10px',
              border: '1px solid var(--color-border, var(--dsw-alias-border-l1, #ebebeb))',
              borderRadius: 6,
              background: 'var(--color-surface, var(--dsw-alias-bg-base, #fff))',
              color: 'inherit',
              font: 'inherit',
              cursor: 'pointer',
            }}
          >
            {t('panel.retry')}
          </button>
        </div>
      )}
    </div>
  )
}
