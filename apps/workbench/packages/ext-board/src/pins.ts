import type { MachineState } from '@calli/extension-api/web';

/** Where the pinned projects are kept on this device. */
export const PINS_KEY = 'pins.v1';

/** Untrusted persisted pins must never prevent the view from opening; order is the order pinned. */
export function parsePins(value: string | null): string[] {
  try {
    const parsed: unknown = value ? JSON.parse(value) : null;
    return Array.isArray(parsed) ? [...new Set(parsed.filter((item): item is string => typeof item === 'string'))] : [];
  } catch {
    return [];
  }
}

/** Pins or unpins a project; a new pin goes last. */
export function togglePin(pins: readonly string[], key: string): string[] {
  return pins.includes(key) ? pins.filter((pin) => pin !== key) : [...pins, key];
}

/** Whether the bar's 「全部」 and a project's pill show as selected, given the project filter. */
export function pillSelected(selected: readonly string[], key: string | null): boolean {
  return key === null ? selected.length === 0 : selected.includes(key);
}

/** What clicking a pill sets the project filter to: only that project, or nothing when it is already chosen alone. */
export function pillFilter(selected: readonly string[], key: string | null): string[] {
  return key === null || (selected.length === 1 && selected[0] === key) ? [] : [key];
}

/** A pinned project's thread count; null while a machine is still loading, so unknown never reads as 0. */
export function pinCount(count: number | undefined, machines: readonly MachineState[]): number | null {
  if (count) return count;
  return machines.some((machine) => machine.state === 'loading') ? null : 0;
}
