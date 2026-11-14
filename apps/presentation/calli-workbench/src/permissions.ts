import { t } from './i18n';
/** Permission levels as the UI shows them. Values match the daemon's PERMISSION_MODES. */
export const PERMISSION_LABELS: Record<string, { label: string; short: string; hint: string }> = {
  ask: { label: t('permission.ask'), short: t('permission.ask.short'), hint: t('permission.ask.hint') },
  auto_edit: { label: t('permission.auto_edit'), short: t('permission.auto_edit.short'), hint: t('permission.auto_edit.hint') },
  full_access: { label: t('permission.full_access'), short: t('permission.full_access.short'), hint: t('permission.full_access.hint') },
};

/**
 * What a level means for this agent. An agent that cannot ask (it runs without a terminal) refuses
 * whatever it would have asked about, so "auto" there means edits only.
 */
export function permissionHint(mode: string, canAsk: boolean): string {
  if (mode === 'auto_edit' && !canAsk) return t('permission.auto_edit.noAsk');
  return PERMISSION_LABELS[mode]?.hint ?? '';
}

/** The level a new thread with this agent starts at: the agent's own default, else the usual one. */
export function startingPermission(caps: { permissionModes: string[]; defaultPermissionMode?: string }): string {
  const modes = caps.permissionModes;
  if (caps.defaultPermissionMode && modes.includes(caps.defaultPermissionMode)) return caps.defaultPermissionMode;
  if (modes.length === 0 || modes.includes('auto_edit')) return 'auto_edit';
  return modes[0];
}

/** Stable order for the picker: from most to least restrictive. */
export const PERMISSION_ORDER = ['ask', 'auto_edit', 'full_access'];

export const EFFORT_LABELS: Record<string, string> = {
  off: t('effort.off'),
  minimal: t('effort.minimal'),
  low: t('effort.low'),
  medium: t('effort.medium'),
  high: t('effort.high'),
  xhigh: t('effort.xhigh'),
  max: t('effort.max'),
};
