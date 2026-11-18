import { useEffect, useRef, useState } from 'react';
import type { VideoRef } from '../api';
import { sizeLabel } from '../files';
import { PanelDialog } from './Dialog';
import { IconDownload, IconFile, IconPlay } from './icons';
import { downloadBlob } from '../images';
import { t } from '../i18n';

export function VideoPreview({ url, name, onClose }: { url: string; name: string; onClose: () => void }) {
  const [failed, setFailed] = useState(false);
  return <PanelDialog title={name} onClose={onClose} className="video-preview">
    <video src={url} controls playsInline preload="metadata" onError={() => setFailed(true)} />
    <div className="video-preview-footer">
      <span>{failed ? t('video.cannotPlay') : t('video.original')}</span>
      <a href={url} download={name}><IconDownload size={15} />{t('video.download')}</a>
    </div>
  </PanelDialog>;
}

/**
 * A video or other file sent with a message. Fetched only on demand, so opening a thread does not
 * download everything in its history: a video plays, any other file is saved.
 */
export function SentVideo({ video, load }: { video: VideoRef; load: (id: string) => Promise<Blob> }) {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  const playable = video.mimeType.startsWith('video/');
  const open = async () => {
    setLoading(true);
    setError(false);
    try {
      const blob = await load(video.id);
      if (!playable) downloadBlob(blob, video.name);
      else if (alive.current) setUrl(URL.createObjectURL(blob));
    } catch { if (alive.current) setError(true); }
    finally { if (alive.current) setLoading(false); }
  };
  return <>
    <button type="button" className={`video-card${playable ? '' : ' file-card'}`} disabled={loading} onClick={() => void open()} aria-label={t(playable ? 'video.play' : 'uploads.download', video.name)} title={playable ? undefined : video.name}>
      {playable ? <IconPlay size={20} /> : <IconFile size={20} />}<span><span className="video-name">{video.name}</span><span className="video-size">{loading ? t(playable ? 'video.loading' : 'uploads.loading') : error ? t('video.loadFailed') : sizeLabel(video.size)}</span></span>
    </button>
    {url && <VideoPreview url={url} name={video.name} onClose={() => setUrl(null)} />}
  </>;
}
