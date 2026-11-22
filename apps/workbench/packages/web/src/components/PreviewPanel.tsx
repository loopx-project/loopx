import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { Api } from '../api';
import { IconBack, IconClose, IconExternal, IconRefresh } from './icons';
import { DockPanel } from './DockPanel';
import { externalPreview } from '../preview-notes';
import { PreviewAnnotations } from './PreviewAnnotations';
import { t } from '../i18n';

/**
 * A page an agent wrote, rendered: the file is served from the thread's directory through a
 * short-lived signed URL, and the iframe is sandboxed to scripts only — the page cannot reach the
 * hub's storage or API, open popups, or navigate away. Refresh re-signs and reloads, so it always
 * shows what is on disk right now. Nothing else reloads it: events streaming into the thread
 * re-render the parent, and that must not disturb the page the person is reading.
 */

interface Props {
  api: Api;
  threadId: string;
  path: string;
  onClose: () => void;
  /** On a computer it goes in the side column beside the conversation instead of over it. */
  docked?: boolean;
  /** Opened from a list: the way back to it, in place of just closing. */
  onBack?: () => void;
}

export function PreviewPanel({ api, threadId, path, onClose, docked = false, onBack }: Props) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [refreshedAt, setRefreshedAt] = useState(0);
  const alive = useRef(true);
  const load = useCallback(async () => {
    setError(null);
    try {
      const external = externalPreview(path, window.location.origin);
      const signed = external ?? (await api.previewLink(threadId, path)).url;
      if (!alive.current) return;
      setUrl(signed);
      setRefreshedAt(Date.now());
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [api, threadId, path]);

  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
    };
  }, [load]);

  const name = path.split('/').pop() || path;

  const bar = (
    <div className="preview-bar">
      {onBack && <button className="ghost" onClick={onBack} aria-label={t('common.back')}><IconBack size={14} /></button>}
      <span className="preview-name" title={path}>
        {name}
      </span>
      <span className="preview-actions">
        <button className="ghost" onClick={() => void load()}>
          <IconRefresh size={14} /> {t('preview.refresh')}
        </button>
        <button className="ghost" onClick={() => url && window.open(url, '_blank', 'noopener,noreferrer')}>
          <IconExternal size={14} /> {t('preview.newTab')}
        </button>
        <button className="ghost" onClick={onClose} aria-label={t('preview.close')}>
          <IconClose size={14} />
        </button>
      </span>
    </div>
  );
  const body = <>
    {error && <p className="panel-error" role="alert">{error} <button className="text-btn" onClick={() => void load()}>{t('common.retry')}</button></p>}
    {url ? (
      <iframe
        ref={frame}
        key={`${url}#${refreshedAt}`}
        className="preview-frame"
        src={url}
        // On the preview port the page has an origin of its own, apart from the app: scripts
        // run, its own assets load same-origin, and the app stays out of reach. Without
        // allow-same-origin the frame would be opaque and the browser would block its CSS.
        sandbox="allow-scripts allow-same-origin"
        title={t('preview.label', name)}
      />
    ) : !error ? <div className="preview-loading" role="status">{t('preview.opening')}</div> : null}
    <PreviewAnnotations api={api} threadId={threadId} path={path} url={url} frame={frame} generation={refreshedAt} />
  </>;
  if (docked) return <DockPanel label={t('preview.label', name)} onDismiss={onBack ?? onClose}><div className="preview docked">{bar}{body}</div></DockPanel>;
  return createPortal(
    <div className="preview-scrim" onClick={onClose}>
      <div className="preview" role="dialog" aria-modal="true" aria-label={t('preview.label', name)} onClick={(e) => e.stopPropagation()}>
        {bar}
        {body}
      </div>
    </div>, document.body
  );
}
