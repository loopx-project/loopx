/** Where the phone's quick-switch handle sits, and whether this device shows it at all. */
export const QUICK_SWITCH_HIDDEN_KEY = 'calli.quickSwitch.hidden';
const QUICK_SWITCH_BOTTOM_KEY = 'calli.quickSwitch.bottom';
const QUICK_SWITCH_BOTTOM = 200;

/** Keep the handle on screen, clear of the input area below and the top bar above. */
export const clampQuickSwitchBottom = (value: number) =>
  Math.round(Math.min(Math.max(value, 120), Math.max(120, window.innerHeight - 140)));
export const savedQuickSwitchBottom = () => {
  try {
    const saved = Number(localStorage.getItem(QUICK_SWITCH_BOTTOM_KEY));
    return clampQuickSwitchBottom(Number.isFinite(saved) && saved > 0 ? saved : QUICK_SWITCH_BOTTOM);
  } catch {
    return QUICK_SWITCH_BOTTOM;
  }
};
export const saveQuickSwitchBottom = (value: number) => {
  try { localStorage.setItem(QUICK_SWITCH_BOTTOM_KEY, String(value)); } catch { /* lasts until reload */ }
};
