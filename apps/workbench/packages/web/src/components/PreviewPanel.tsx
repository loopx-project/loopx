import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { Api } from '../api';
import { IconClose, IconExternal, IconRefresh } from './icons';
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
  onError: (e: unknown) => void;
}

export function PreviewPanel({ api, threadId, path, onClose, onError }: Props) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [refreshedAt, setRefreshedAt] = useState(0);
  const alive = useRef(true);
  // The parent renders anew with every event the thread streams, handing in a fresh onError each
  // time. Through the ref that no longer changes `load`, which would re-sign the page and remount
  // the frame below — the page the person is reading would start over every few seconds.
  const failure = useRef(onError);
  failure.current = onError;

  const load = useCallback(async () => {
    try {
      const external = externalPreview(path, window.location.origin);
      const signed = external ?? (await api.previewLink(threadId, path)).url;
      if (!alive.current) return;
      setUrl(signed);
      setRefreshedAt(Date.now());
    } catch (e) {
      if (alive.current) failure.current(e);
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

  return createPortal(
    <div className="preview-scrim" onClick={onClose}>
      <div className="preview" role="dialog" aria-modal="true" aria-label={t('preview.label', name)} onClick={(e) => e.stopPropagation()}>
        <div className="preview-bar">
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
        ) : (
          <div className="preview-loading">{t('preview.opening')}</div>
        )}
        <PreviewAnnotations api={api} threadId={threadId} path={path} url={url} frame={frame} generation={refreshedAt} />
      </div>
    </div>, document.body
  );
}
