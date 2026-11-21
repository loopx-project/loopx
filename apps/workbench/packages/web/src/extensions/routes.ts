/** Where extension views live in the address bar. Pure, so it can be tested directly. */
import type { ExtensionState, ViewContribution } from '@calli/extension-api';

export interface ExtensionViewRef {
  extensionId: string;
  view: ViewContribution;
}

const PREFIX = '#/ext/';

/** Whether an address could belong to an extension view, before the list of extensions is known. */
export function looksLikeExtensionRoute(hash: string, coreRoutes: readonly string[]): boolean {
  return hash.startsWith(PREFIX) || (/^#\/[a-z0-9][a-z0-9/-]*$/.test(hash) && !coreRoutes.includes(hash));
}

/** The views of the extensions that are installed and switched on, in catalog order. */
export function enabledViews(states: ExtensionState[]): ExtensionViewRef[] {
  return states
    .filter((s) => s.installed && s.enabled)
    .flatMap((s) => (s.manifest.contributes.views ?? []).map((view) => ({ extensionId: s.manifest.id, view })));
}

/** The view an address shows: `#/ext/<id>/<view>`, or a route alias such as `#/board`. Null when no enabled extension has it. */
export function resolveExtensionRoute(hash: string, states: ExtensionState[]): ExtensionViewRef | null {
  const views = enabledViews(states);
  if (hash.startsWith(PREFIX)) {
    try {
      const [id, viewId] = hash.slice(PREFIX.length).split('/').map(decodeURIComponent);
      return views.find((v) => v.extensionId === id && v.view.id === (viewId || v.view.id)) ?? null;
    } catch {
      return null;
    }
  }
  return views.find((v) => v.view.route === hash) ?? null;
}

/** The address of a view: its alias when it declares one. */
export function extensionHash(ref: ExtensionViewRef): string {
  return ref.view.route ?? `${PREFIX}${encodeURIComponent(ref.extensionId)}/${encodeURIComponent(ref.view.id)}`;
}
