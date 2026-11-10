import type { Context } from '@deepseek-ai/cordis'
import type { ConnectionHandle } from '@deepseek-ai/dsh-client-connection/client'
import type {} from '@deepseek-ai/dsh-client-locale/client'
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
// The renderer owns the client slots service in DSH 0.1.5.
import type {} from '@deepseek-ai/dsh-client-ui-renderer/client'
// Type-only: declares the sidebar panel list and the keyed `main` slot this
// plugin contributes into. Both are erased from the client bundle.
import type {} from '@deepseek-ai/dsh-client-ui-layout/client'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar/client'

import {
  LOOPX_DASHBOARD_LABEL,
  LOOPX_DASHBOARD_PANEL_ID,
  LoopXDashboardIcon,
  LoopXDashboardPanel,
} from './dashboard.tsx'
import { LoopXGoalBar } from './LoopXGoalBar.tsx'
import { ensurePluginStyle } from './goalbar.module.css'
import {
  DASHBOARD_LOCALES,
  DASHBOARD_LOCALE_NAMESPACE,
  GOALBAR_LOCALES,
  GOALBAR_LOCALE_NAMESPACE,
} from './locale.ts'
import { createGoalBarRpc } from './rpc.ts'

const PACKAGE_ID = 'dsh-loopx-plugin'

/** Cordis service names required before the materialized Client plugin applies. */
export const inject = ['connection', 'locale', 'slots'] as const

type GoalBarClientContext = Context & {
  readonly connection: ConnectionHandle
}

/** Register the exact-Session LoopX GoalBar contribution and its owned copy. */
export function apply(ctx: GoalBarClientContext): void {
  // Client module exports are cached across a same-page plugin reapply, so CSS
  // installation must be an explicit, idempotent apply-time operation.
  ensurePluginStyle()

  // Register ownership immediately after installation, before any later apply
  // step can fail, so Cordis rollback and ordinary unload both remove the tag.
  ctx.effect(() => () => {
    if (typeof document === 'undefined') return
    for (const style of document.querySelectorAll('style[data-plugin]')) {
      if (style.getAttribute('data-plugin') === PACKAGE_ID) style.remove()
    }
  }, 'dsh-loopx-plugin CSS ownership')

  ctx.effect(
    () => ctx.locale.register(GOALBAR_LOCALE_NAMESPACE, GOALBAR_LOCALES),
    'dsh-loopx-plugin GoalBar locale',
  )

  ctx.effect(
    () => ctx.locale.register(DASHBOARD_LOCALE_NAMESPACE, DASHBOARD_LOCALES),
    'dsh-loopx-plugin dashboard locale',
  )

  // Present the already-running LoopX personal workspace as one sidebar panel.
  // Registration only navigates: it never starts, owns or duplicates the
  // dashboard service, which remains `loopx dashboard` on loopback.
  ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
    name: 'sidebar.panellist',
    id: LOOPX_DASHBOARD_PANEL_ID,
    order: 7,
    label: () => LOOPX_DASHBOARD_LABEL,
  }, LoopXDashboardIcon))

  ctx.slots.inject('main', () => ctx.slots.register({
    name: 'main',
    key: LOOPX_DASHBOARD_PANEL_ID,
    locale: DASHBOARD_LOCALE_NAMESPACE,
  }, LoopXDashboardPanel))

  const rpc = createGoalBarRpc(
    ctx.connection.rpc,
    Reflect.has(ctx.connection, 'generation'),
  )
  ctx.slots.inject('conversation.input.dock', () => ctx.slots.register({
    name: 'conversation.input.dock',
    id: 'loopx-goal',
    order: 15,
    locale: GOALBAR_LOCALE_NAMESPACE,
    inject: sessionId => ({
      rpcSessionId: String(sessionId),
      rpc,
      subscribeConnectionReset: (listener: () => void) => (
        ctx.on('connection/reset', listener)
      ),
    }),
  }, LoopXGoalBar))
}
