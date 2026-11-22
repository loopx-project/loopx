import { useSyncExternalStore } from 'react';
import { createPwaInstall } from '../pwa-install';
import { t } from '../i18n';
import { SettingsSection } from './PageNavigation';
import { IconDownload } from './icons';

// Capture eligibility before React mounts, and share one single-use offer across both entrypoints.
const install = typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? createPwaInstall(window) : null;
if (import.meta.hot) import.meta.hot.dispose(() => install?.dispose());
const empty = { available: false, installed: false, busy: false, accepted: false, failed: false, dismissedUntil: 0 };
const snapshot = () => install?.getSnapshot() ?? empty;
const subscribe = (listener: () => void) => install?.subscribe(listener) ?? (() => {});
const useInstall = () => useSyncExternalStore(subscribe, snapshot);

export function PwaInstallPromotion({ onHelp }: { onHelp: () => void }) {
  const state = useInstall();
  if (state.installed || state.dismissedUntil > Date.now() || (!state.available && !state.busy && !state.failed)) return null;
  return <section className="pwa-install-promo" aria-label={t('pwa.title')}>
    <strong><IconDownload size={16} />{t('pwa.title')}</strong>
    <p>{t('pwa.hint')}</p>
    {state.failed && <p role="alert">{t('pwa.failed')}</p>}
    <div className="pwa-install-actions">
      {state.failed
        ? <button type="button" className="ghost" onClick={onHelp}>{t('pwa.help')}</button>
        : <button type="button" className="primary" disabled={state.busy} onClick={() => void install?.install()}>{t(state.busy ? 'pwa.prompting' : 'pwa.install')}</button>}
      <button type="button" className="ghost" onClick={() => install?.dismiss()}>{t('pwa.later')}</button>
    </div>
  </section>;
}

export function PwaInstallSettings() {
  const state = useInstall();
  const secure = typeof window !== 'undefined' && window.isSecureContext;
  return <SettingsSection title={t('pwa.title')} hint={t('pwa.hint')}>
    {state.installed ? <p role="status">{t('pwa.installed')}</p> : <>
      {state.failed && <p role="alert">{t('pwa.failed')}</p>}
      {state.accepted && <p role="status">{t('pwa.accepted')}</p>}
      {state.available || state.busy
        ? <button type="button" className="primary" disabled={state.busy} onClick={() => void install?.install()}>{t(state.busy ? 'pwa.prompting' : 'pwa.install')}</button>
        : !state.accepted && <p className="settings-hint">{t(secure ? 'pwa.unavailable' : 'pwa.insecure')}</p>}
      <details className="pwa-install-help" open={!state.available && !state.busy && !state.accepted}>
        <summary>{t('pwa.help')}</summary>
        <p>{t('pwa.chrome')}</p>
        <p>{t('pwa.launch')}</p>
      </details>
    </>}
  </SettingsSection>;
}
