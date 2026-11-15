// Extension manifest: what an extension is, what it asks for and where it shows up.
// The host reads it before running any extension code, and shows the permissions at install time.

import type { Permission } from './permissions.js';
import type { NotificationHook, RequestHook } from './hooks.js';

/** Contract version. Within one major version fields are only added, never changed. */
export type ApiVersion = 1;

/** Text in every interface language. `zh` is the source language. */
export interface LocalizedText {
  zh: string;
  en: string;
  ja: string;
}

/**
 * Icons the host can draw. Extensions name an icon instead of shipping SVG,
 * so every entry looks like the rest of the interface.
 */
export type HostIconName =
  | 'board'
  | 'list'
  | 'activity'
  | 'clock'
  | 'target'
  | 'gauge'
  | 'sparkle'
  | 'cube'
  | 'globe'
  | 'server'
  | 'bolt'
  | 'mic';

/** A composer action button contributed by an extension. */
export interface ComposerActionContribution {
  id: string;
  title: LocalizedText;
  icon: HostIconName;
}

/** A main-area page contributed by an extension. */
export interface ViewContribution {
  /** Unique within the extension. The default route is `#/ext/<extension id>/<view id>`. */
  id: string;
  title: LocalizedText;
  icon: HostIconName;
  /**
   * Short route alias, e.g. `#/board`. Must start with `#/` and must not clash with core routes
   * (`#/todos`, `#/schedules`, `#/activity`, `#/skills`, `#/mcp`, `#/extensions`).
   */
  route?: `#/${string}`;
  /** Place the entry in the sidebar when pinned, or in the settings menu when explicitly false. */
  sidebar?: { pinned: boolean };
}

export interface Contributions {
  views?: ViewContribution[];
  workSource?: boolean;
  composerActions?: ComposerActionContribution[];
}

/** Built-in views ship with Calli; optional work sources are installed separately. */
export type ExtensionSource = 'builtin' | 'external';

export interface ExtensionSetting {
  key: string;
  label: LocalizedText;
  scope: 'installation' | 'binding';
  kind?: 'text' | 'path';
  default?: string;
  required?: boolean;
}
export interface ExtensionCommand {
  id: string;
  executable: string | { setting: string };
  args: (string | { setting: string } | { binding: 'externalId' })[];
  env?: Record<string, string>;
  scope: 'installation' | 'binding';
  effect: 'read' | 'runtime';
  timeoutMs?: number;
}

/** Known agent capabilities an extension may declare in `requires.agentCapabilities`. */
export type KnownAgentCapability = 'realtimeVoice';

export interface ExtensionRequirements {
  agentCapabilities?: KnownAgentCapability[];
}

export type RequirementResolutionState = 'satisfied' | 'missing' | 'unknown' | 'error';

export interface ExtensionRequirementStatus {
  state: RequirementResolutionState;
  missing?: string[];
  reason?: string;
  providerId?: string;
}

export interface ExtensionManifest {
  /** Lowercase letters, digits and `-`. Stable across versions. */
  id: string;
  /** Semver of the extension itself. */
  version: string;
  apiVersion: ApiVersion;
  name: LocalizedText;
  description: LocalizedText;
  /** Everything the extension may use. Shown at install; new permissions on upgrade need a new confirmation. */
  permissions: Permission[];
  contributes: Contributions;
  requires?: ExtensionRequirements;
  /** Notifications the extension wants. It receives nothing it did not declare. Empty for view-only extensions. */
  subscribes?: NotificationHook[];
  /** Requests the extension answers. External work sources implement the supported subset. */
  answers?: RequestHook[];
  /** External packages contain a self-contained Node ESM entry, never browser code. */
  process?: { entry: string };
  commands?: ExtensionCommand[];
  settings?: ExtensionSetting[];
}

/** One row of `GET /api/extensions`: the catalog entry plus its state on this machine. */
export interface ExtensionState {
  manifest: ExtensionManifest;
  source: ExtensionSource;
  installed: boolean;
  /** Installed and switched on. A disabled extension keeps its grant but shows nothing. */
  enabled: boolean;
  /** Permissions the user confirmed. Equal to `manifest.permissions` once installed. */
  granted: Permission[];
  installedAt: string | null;
  updatedAt: string | null;
  /** Machine-level requirement resolution status. */
  requirements?: ExtensionRequirementStatus;
  origin?: string;
  digest?: string;
  previousVersion?: string;
  /** Listed by the host as a supported optional package, even before installation. */
  preset?: boolean;
}
