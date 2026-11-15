/**
 * The browser code of the extensions that ship with Calli. Each loads as a chunk of its own the
 * first time one of its views opens, so a page that never shows one never downloads it. Extension
 * packages depend on @calli/extension-api only; nothing here is visible to them but `WebHost`.
 */
import type { ExtensionManifest } from '@calli/extension-api';
import type { WebExtensionModule } from '@calli/extension-api/web';
import memoryManifest from '@calli/ext-memory/manifest.json';
import boardManifest from '@calli/ext-board/manifest.json';
import voiceManifest from '@calli/ext-voice/manifest.json';
import autotitleManifest from '@calli/ext-autotitle/manifest.json';

export const BUILTIN_MODULES: Record<string, () => Promise<{ default: WebExtensionModule }>> = {
  board: () => import('@calli/ext-board'),
  memory: () => import('@calli/ext-memory'),
  voice: () => import('@calli/ext-voice'),
  autotitle: () => import('@calli/ext-autotitle'),
};

/** The same extensions' manifests, for the demo: it has no daemon to read the catalog from. */
export const BUILTIN_MANIFESTS: ExtensionManifest[] = [
  boardManifest as ExtensionManifest,
  memoryManifest as ExtensionManifest,
  voiceManifest as ExtensionManifest,
  autotitleManifest as ExtensionManifest,
];
