/** How the hub looks on this device: light or dark, and which palette. Pure, so it can be tested directly. */
import { t } from './i18n';

export type Mode = 'system' | 'light' | 'dark';
export type Theme = 'light' | 'dark';

export interface Appearance {
  mode: Mode;
  /** The id of one of PALETTES. */
  palette: string;
}

/** The palettes to choose from. Their colours live at the top of styles.css, keyed by these ids. */
export const PALETTES: ReadonlyArray<{ id: string; name: string }> = [
  { id: 'paper', name: t('appearance.palette.paper') },
  { id: 'graphite', name: t('appearance.palette.graphite') },
  { id: 'catppuccin', name: 'Catppuccin' },
  { id: 'everforest', name: 'Everforest' },
  { id: 'gruvbox', name: 'Gruvbox' },
  { id: 'nord', name: 'Nord' },
  { id: 'rose-pine', name: 'Rosé Pine' },
  { id: 'one', name: 'One' },
];

export const MODES: ReadonlyArray<{ id: Mode; name: string }> = [
  { id: 'system', name: t('appearance.mode.system') },
  { id: 'light', name: t('appearance.mode.light') },
  { id: 'dark', name: t('appearance.mode.dark') },
];

export const DEFAULT_APPEARANCE: Appearance = { mode: 'system', palette: 'paper' };

/** Where the choice is kept. index.html reads the same key before the first paint. */
export const APPEARANCE_KEY = 'calli.appearance';

/**
 * Reads a saved choice. Each half falls back on its own: a palette this build does not know would
 * leave the page without colours, and must not take the light-or-dark choice down with it.
 */
export function parseAppearance(raw: string | null): Appearance {
  let saved: Partial<Record<keyof Appearance, unknown>> = {};
  try {
    const value: unknown = raw ? JSON.parse(raw) : null;
    if (value && typeof value === 'object') saved = value;
  } catch {
    // Not JSON: the defaults.
  }
  return {
    mode: MODES.some((m) => m.id === saved.mode) ? (saved.mode as Mode) : DEFAULT_APPEARANCE.mode,
    palette: PALETTES.some((p) => p.id === saved.palette) ? (saved.palette as string) : DEFAULT_APPEARANCE.palette,
  };
}

export function resolveTheme(mode: Mode, systemDark: boolean): Theme {
  return mode === 'system' ? (systemDark ? 'dark' : 'light') : mode;
}
