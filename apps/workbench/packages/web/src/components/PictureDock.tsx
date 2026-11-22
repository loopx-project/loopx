import { useEffect, useState } from 'react';
import { DockPanel } from './DockPanel';
import { Lightbox } from './Lightbox';
import { IconBack, IconClose, IconImage } from './icons';
import { t } from '../i18n';

/**
 * A picture from the overview in the side column, fitted to it. Zooming and saving live in the
 * full-size viewer one button away, so the column stays a quiet place to look.
 */
export function PictureDock({ path, name, load, onClose, onBack }: {
  path: string;
  name: string;
  load: (path: string) => Promise<Blob>;
  onClose: () => void;
  onBack?: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [large, setLarge] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    setError(null);
    let live = true;
    let made: string | null = null;
    load(path).then(
      (blob) => { if (!live) return; made = URL.createObjectURL(blob); setUrl(made); },
      (reason: unknown) => { if (live) setError(reason instanceof Error && reason.message ? reason.message : t('view.noPreview')); },
    );
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [path, load, attempt]);
  const vector = /\.svgz?$/i.test(path);
  return <DockPanel label={name} onDismiss={onBack ?? onClose}>
    <div className="preview docked picture-dock">
      <div className="preview-bar">
        {onBack && <button className="ghost" onClick={onBack} aria-label={t('common.back')}><IconBack size={14} /></button>}
        <span className="preview-name" title={path}>{name}</span>
        <span className="preview-actions">
          <button className="ghost" disabled={!url} onClick={() => setLarge(true)}>{t('overview.pictureLarge')}</button>
          <button className="ghost" onClick={onClose} aria-label={t('common.close')}><IconClose size={14} /></button>
        </span>
      </div>
      <div className={`picture-dock-stage${vector ? ' vector' : ''}`}>
        {url ? <img src={url} alt={name} draggable={false} onClick={() => setLarge(true)} />
          : <span className="preview-loading" role={error ? 'alert' : 'status'}>{error ? <span>{error} <button className="text-btn" onClick={() => setAttempt(n => n + 1)}>{t('common.retry')}</button></span> : <IconImage size={18} />}</span>}
      </div>
      {large && url && <Lightbox url={url} name={name} onClose={() => setLarge(false)} />}
    </div>
  </DockPanel>;
}
