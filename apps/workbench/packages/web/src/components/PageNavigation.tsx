import { createContext, useCallback, useContext, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { t } from '../i18n';
import { DialogSurface } from './Dialog';

type DraftState = { dirty: boolean; busy: boolean };
const Navigation = createContext({
  request: (action: () => void): boolean => { action(); return true; },
  register: (_id: string, _state: DraftState | null) => {},
});

/** One leave check for page forms, including navigation from the sidebar and browser history. */
export function PageNavigation({ children }: { children: ReactNode }) {
  const drafts = useRef(new Map<string, DraftState>());
  const discarding = useRef(false);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const register = useCallback((id: string, state: DraftState | null) => {
    if (state) drafts.current.set(id, state);
    else drafts.current.delete(id);
  }, []);
  const request = useCallback((action: () => void) => {
    if (discarding.current) { action(); return true; }
    const states = [...drafts.current.values()];
    if (states.some(state => state.busy)) return false;
    if (states.some(state => state.dirty)) { setPending(() => action); return false; }
    action(); return true;
  }, []);
  const value = useMemo(() => ({ request, register }), [request, register]);
  return <Navigation.Provider value={value}>
    {children}
    {pending && <DialogSurface labelledBy="settings-leave-title" onClose={() => setPending(null)}>
      <h2 id="settings-leave-title" className="dialog-title">{t('settings.unsaved')}</h2>
      <p className="dialog-message">{t('settings.unsavedHint')}</p>
      <div className="dialog-actions">
        <button className="ghost" onClick={() => setPending(null)}>{t('common.cancel')}</button>
        <button className="primary" onClick={() => {
          setPending(null); discarding.current = true;
          try { pending(); } finally { discarding.current = false; }
        }}>{t('settings.discard')}</button>
      </div>
    </DialogSurface>}
  </Navigation.Provider>;
}

export const usePageNavigation = () => useContext(Navigation).request;

/** Returns a release function for successful saves that navigate away in the same event. */
export function usePageDraft(dirty: boolean, busy = false) {
  const id = useId();
  const { register } = useContext(Navigation);
  useLayoutEffect(() => {
    register(id, { dirty, busy });
    return () => register(id, null);
  }, [id, dirty, busy, register]);
  return () => register(id, null);
}

/** Page content stays opaque; only the persistent action bar uses the shared glass material. */
export function SettingsSection({ title, hint, children, className = '', actions, busy = false }: {
  title: string; hint?: string; children: ReactNode; className?: string; actions?: ReactNode; busy?: boolean;
}) {
  return <section className={`settings-section ${className}`} aria-label={title}>
    <div className="settings-section-body">
      <h2 className="settings-section-title">{title}</h2>
      {hint && <p className="settings-hint">{hint}</p>}
      <fieldset className="settings-fields" disabled={busy}>{children}</fieldset>
    </div>
    {actions && <footer className="settings-actions">{actions}</footer>}
  </section>;
}
