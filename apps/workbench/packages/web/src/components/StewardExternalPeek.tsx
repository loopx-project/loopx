import { useEffect, useRef, useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Api, StewardSessionReadResult } from '../api';
import { t } from '../i18n';
import { AgentPanelFrame } from './AgentPanelFrame';
import { IconClose, IconExternal } from './icons';

export interface ExternalPeekTarget {
  agent: string;
  sessionId: string;
  cwd: string;
  title?: string;
}

/**
 * Read-only side sheet for an unimported external CLI/App session discovered by Steward S1.
 * Reads bounded turns through `/api/steward/sessions/read` without importing or resuming the session;
 * importing into Workbench happens only if the owner explicitly clicks the import button.
 */
export function StewardExternalPeek({
  api,
  target,
  importing = false,
  onImport,
  onClose,
}: {
  api: Api;
  target: ExternalPeekTarget;
  importing?: boolean;
  onImport: () => void;
  onClose: () => void;
}) {
  const [result, setResult] = useState<StewardSessionReadResult | null>(null);
  const [error, setError] = useState('');
  const panel = useRef<HTMLElement>(null);
  const closeBtn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeBtn.current?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.isComposing && !document.querySelector('.pop-panel, [role=menu], [role=dialog][aria-modal=true]')) {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setError('');
    void api
      .stewardReadSession('local_session', target.sessionId, { agent: target.agent, mode: 'recent_turns', maxChars: 12_000 }, controller.signal)
      .then(data => {
        if (!controller.signal.aborted) setResult(data);
      })
      .catch(cause => {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => controller.abort();
  }, [api, target.agent, target.sessionId]);

  const title = result?.title || target.title || target.sessionId;
  return (
    <AgentPanelFrame detail panelRef={panel} label={title}>
      <header className="agent-panel-head">
        <div className="agent-panel-heading">
          <strong title={title}>{title}</strong>
        </div>
        <button
          type="button"
          className="icon-btn"
          disabled={importing}
          onClick={onImport}
          title={t('loopx.stewardPeekExternalImport')}
          aria-label={t('loopx.stewardPeekExternalImport')}
        >
          <IconExternal />
        </button>
        <button
          ref={closeBtn}
          type="button"
          className="icon-btn"
          onClick={onClose}
          title={t('threadPeek.back')}
          aria-label={t('threadPeek.back')}
        >
          <IconClose />
        </button>
      </header>
      {error ? <p className="thread-peek-error" role="alert">{error}</p> : null}
      <div className="agent-panel-body steward-external-body" tabIndex={0}>
        {!result && !error ? (
          <div className="loading-note">{t('common.loading')}</div>
        ) : result ? (
          <div className="steward-external-turns">
            {result.chunks.map(chunk =>
              chunk.role === 'user' ? (
                <div key={chunk.index} className="row user">
                  <div className="bubble">{chunk.text}</div>
                </div>
              ) : (
                <div key={chunk.index} className="row assistant">
                  <div className="markdown">
                    <Markdown remarkPlugins={[remarkGfm]}>{chunk.text}</Markdown>
                  </div>
                </div>
              ),
            )}
          </div>
        ) : null}
      </div>
      <footer className="steward-external-foot">
        <span>{t('loopx.stewardPeekExternalHint')}</span>
        <button type="button" className="ghost steward-external-import" disabled={importing} onClick={onImport}>
          {t('loopx.stewardPeekExternalImport')}
        </button>
      </footer>
    </AgentPanelFrame>
  );
}
