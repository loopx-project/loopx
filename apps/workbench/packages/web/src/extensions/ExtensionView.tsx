import type { Api } from '../api';
import { Component, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { IconChevron } from '../components/icons';
import { Dialog } from '../components/Dialog';
import { hasPermission } from './permissions';
import type { WebHostNavigate, WebExtensionModule } from '@calli/extension-api/web';
import { useIsNarrow } from '../hooks';
import { t } from '../i18n';
import { BUILTIN_MODULES } from './catalog';
import { createWebHost, extensionStorage, type HostInputs } from './host';
import { hostUi } from './ui';

interface Props {
  api: Api;
  extensionId: string;
  viewId: string;
  /** The extension's name in the language on screen, for what is said if it fails. */
  name: string;
  inputs: HostInputs;
  onOpenThread: (id: string) => void;
  navigate: WebHostNavigate;
  onSetThreadStage: (id: string, stage: 'backlog' | 'doing' | 'blocked' | 'review') => Promise<void>;
  onAskAssistant: (text: string) => Promise<void>;
  onExtensionChange: (state: import('@calli/extension-api').ExtensionState) => void;
  onSetThreadDone: (id: string, done: boolean) => Promise<void>;
}

/** Keeps a view that throws from taking the page with it: it says so in its own place, the rest goes on. */
class Boundary extends Component<{ name: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(`[extension ${this.props.name}]`, error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <Failed name={this.props.name} onRetry={() => this.setState({ failed: false })} />;
  }
}

function Failed({ name, onRetry }: { name: string; onRetry: () => void }) {
  return (
    <div className="empty ext-failed">
      <div>
        <h2>{t('extensions.viewFailed', name)}</h2>
        <p>{t('extensions.viewFailedHint')}</p>
        <button type="button" className="ghost" onClick={onRetry}>
          {t('common.retry')}
        </button>
      </div>
    </div>
  );
}

type Loaded = { id: string; module: WebExtensionModule } | { id: string; error: unknown };

/** An extension's view in the main area: its code loaded on first use, handed a `WebHost`, behind a boundary. */
export function ExtensionView({ api, extensionId, viewId, name, inputs, onOpenThread, onSetThreadDone, navigate, onSetThreadStage, onAskAssistant, onExtensionChange }: Props) {
  const narrow = useIsNarrow();
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const retry = () => { setFailed(false); setAttempt(n => n + 1); };
  const callbacks = useRef({ onOpenThread, onSetThreadDone, navigate, onSetThreadStage, onAskAssistant, inputs });
  callbacks.current = { onOpenThread, onSetThreadDone, navigate, onSetThreadStage, onAskAssistant, inputs };
  const [consent, setConsent] = useState<{ text: string; resolve: () => void; reject: (error: Error) => void } | null>(null);
  const pending = useRef<typeof consent>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [consentError, setConsentError] = useState('');
  const [consentBusy, setConsentBusy] = useState(false);
  useEffect(() => () => { pending.current?.reject(new Error('Assistant request cancelled')); pending.current = null; }, []);
  const askAssistant = async (text: string) => {
    const state = callbacks.current.inputs.extension;
    if (!state?.installed || !state.enabled) throw new Error('Assistant permission required');
    if (!hasPermission(state, 'assistant.ask')) {
      if (pending.current) throw new Error('Assistant confirmation already open');
      await new Promise<void>((resolve, reject) => {
        pending.current = { text, resolve, reject };
        setConsent(pending.current);
        setConsentError(''); setPreviewOpen(false);
      });
    }
    await callbacks.current.onAskAssistant(text);
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const live = useMemo(() => createWebHost(extensionId, inputs, {
    api, storage: api.demo ? extensionStorage(extensionId, null) : undefined,
    openThread: id => callbacks.current.onOpenThread(id),
    setThreadDone: (id, done) => callbacks.current.onSetThreadDone(id, done),
    setThreadStage: (id, stage) => callbacks.current.onSetThreadStage(id, stage),
    navigate: {
      peekThread: id => callbacks.current.navigate.peekThread(id),
      threadMenu: (id, at) => callbacks.current.navigate.threadMenu(id, at),
      newThread: options => callbacks.current.navigate.newThread(options),
    },
    askAssistant, ui: hostUi, onError: () => setFailed(true),
  }), [api, extensionId, attempt]);
  useEffect(() => live.update(inputs), [live, inputs]);
  const host = useMemo(() => ({ ...live.host, narrow }), [live, narrow]);

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  useEffect(() => {
    let stale = false;
    const load = BUILTIN_MODULES[extensionId];
    if (!load) {
      setLoaded({ id: extensionId, error: new Error(`no code for ${extensionId}`) });
      return;
    }
    load().then(
      (m) => {
        if (stale) return;
        try {
          const module = m.default;
          const views = module?.views;
          if (!views || typeof views !== 'object' || Object.values(views).some(view =>
            typeof view !== 'function' && !(view && typeof view === 'object' && '$$typeof' in view)
          )) throw new Error('invalid extension views');
          setLoaded({ id: extensionId, module });
        } catch (error) { setLoaded({ id: extensionId, error }); }
      },
      (error) => !stale && setLoaded({ id: extensionId, error }),
    );
    return () => {
      stale = true;
    };
  }, [extensionId, attempt]);

  if (failed) return <Failed name={name} onRetry={retry} />;
  if (!loaded || loaded.id !== extensionId) return <div className="loading-note">{t('common.loading')}</div>;
  const View = 'module' in loaded ? loaded.module.views?.[viewId] : undefined;
  if (!View) {
    if ('error' in loaded) console.error(`[extension ${extensionId}]`, loaded.error);
    return <Failed name={name} onRetry={retry} />;
  }
  const Markdown = host.ui.Markdown;
  return (
    <Boundary key={`${extensionId}/${viewId}/${attempt}`} name={name}>
      <View host={host} />
      {consent && <Dialog title={t('extensions.assistant.title')} message={t('extensions.assistant.message')}
        confirmLabel={t('extensions.assistant.send')} confirmDisabled={consentBusy}
        onCancel={() => { if (consentBusy) return; consent.reject(new Error(t('extensions.assistant.cancelled'))); pending.current = null; setConsent(null); }}
        onConfirm={() => {
          const state = callbacks.current.inputs.extension;
          if (!state?.installed || !state.enabled) return;
          setConsentBusy(true);
          void api.installExtension(extensionId, state.manifest.permissions).then(updated => {
            callbacks.current.inputs = { ...callbacks.current.inputs, extension: updated };
            onExtensionChange(updated); pending.current = null; setConsent(null); consent.resolve();
          }).catch(error => setConsentError(error instanceof Error ? error.message : String(error))).finally(() => setConsentBusy(false));
        }}>
          <div className="assistant-consent-preview">
            <button type="button" className="ghost" aria-expanded={previewOpen} onClick={() => setPreviewOpen(open => !open)}><IconChevron size={14} open={previewOpen} />{t('extensions.assistant.preview')}</button>
            {previewOpen && <div className="assistant-consent-content"><Markdown text={consent.text} /></div>}
          </div>
          {consentError && <p role="alert">{consentError}</p>}
        </Dialog>}

    </Boundary>
  );
}
