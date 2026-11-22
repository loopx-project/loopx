/** The browser owns eligibility and consent; these are only the page's presentation states. */
interface InstallPrompt extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

const DISMISSED_KEY = 'loopx.install.dismissedUntil';
const WEEK = 7 * 24 * 60 * 60 * 1000;

export function createPwaInstall(browser: Window) {
  const modes = ['standalone', 'window-controls-overlay'].map(mode => browser.matchMedia(`(display-mode: ${mode})`));
  const standalone = () => modes.some(mode => mode.matches);
  const readDismissed = () => {
    try {
      const value = Number(browser.localStorage.getItem(DISMISSED_KEY));
      return Number.isFinite(value) ? value : 0;
    } catch { return 0; }
  };
  let prompt: InstallPrompt | null = null;
  let observedInstall = false;
  let state = { available: false, installed: standalone(), busy: false, accepted: false, failed: false, dismissedUntil: readDismissed() };
  const listeners = new Set<() => void>();
  const update = (patch: Partial<typeof state>) => {
    state = { ...state, ...patch };
    listeners.forEach(listener => listener());
  };
  const dismiss = () => {
    const dismissedUntil = Date.now() + WEEK;
    try { browser.localStorage.setItem(DISMISSED_KEY, String(dismissedUntil)); } catch { /* Session-only when storage is unavailable. */ }
    update({ dismissedUntil });
  };
  const onPrompt = (event: Event) => {
    if (!browser.isSecureContext || standalone()) return;
    event.preventDefault();
    prompt = event as InstallPrompt;
    // A fresh browser offer also recovers from an uninstall in another window.
    observedInstall = false;
    update({ available: true, installed: false, accepted: false, failed: false });
  };
  const onInstalled = () => {
    observedInstall = true;
    prompt = null;
    update({ installed: true, available: false, busy: false, accepted: false, failed: false });
  };
  const onMode = () => {
    if (standalone()) prompt = null;
    update({ installed: observedInstall || standalone(), available: prompt !== null });
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key === DISMISSED_KEY || event.key === null) update({ dismissedUntil: readDismissed() });
  };
  browser.addEventListener('beforeinstallprompt', onPrompt);
  browser.addEventListener('appinstalled', onInstalled);
  browser.addEventListener('storage', onStorage);
  modes.forEach(mode => mode.addEventListener('change', onMode));
  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    dismiss,
    async install() {
      if (!prompt || state.busy || state.installed) return;
      const offer = prompt;
      prompt = null; // Each event is single-use, including failed or canceled prompts.
      update({ available: false, busy: true, failed: false, accepted: false });
      try {
        // Must run in the button's click handler, before any await loses user activation.
        await offer.prompt();
        const choice = await offer.userChoice;
        if (state.installed) return; // appinstalled may arrive before userChoice.
        if (choice.outcome === 'dismissed') dismiss();
        update({ accepted: choice.outcome === 'accepted' });
      } catch {
        if (!state.installed) update({ failed: true });
      } finally {
        update({ busy: false });
      }
    },
    dispose() {
      browser.removeEventListener('beforeinstallprompt', onPrompt);
      browser.removeEventListener('appinstalled', onInstalled);
      browser.removeEventListener('storage', onStorage);
      modes.forEach(mode => mode.removeEventListener('change', onMode));
      listeners.clear();
    },
  };
}
