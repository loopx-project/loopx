import type { ExtensionState, Permission } from '@calli/extension-api';

/** Consent belongs to this exact manifest, including when an update removes a permission. */
export function exactGrant(state: ExtensionState): boolean {
  const { permissions } = state.manifest;
  return state.granted.length === permissions.length && new Set(state.granted).size === state.granted.length
    && permissions.every(permission => state.granted.includes(permission));
}

/** Existing board grants stay usable; the assistant permission is still checked separately. */
export function activeGrant(state: ExtensionState): boolean {
  return exactGrant(state) || (state.source === 'builtin' && state.manifest.id === 'board'
    && state.manifest.permissions.includes('assistant.ask')
    && exactGrant({ ...state, manifest: { ...state.manifest, permissions: state.manifest.permissions.filter(p => p !== 'assistant.ask') } }));
}

export const needsConfirm = (state: ExtensionState) => state.installed && !activeGrant(state);

export function hasPermission(state: ExtensionState | null, permission: Permission): boolean {
  return Boolean(state?.installed && state.enabled && activeGrant(state) && state.granted.includes(permission) && state.manifest.permissions.includes(permission));
}
