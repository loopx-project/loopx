import { useEffect, useRef, useState, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import type { Api } from '../api';
import { isPreviewPick, type PreviewNote, type PreviewPick } from '../preview-notes';
import { t } from '../i18n';
import { Dialog } from './Dialog';
import { Popover } from './Popover';
import { IconEdit } from './icons';

export function PreviewAnnotations({ api, threadId, path, url, frame, generation }: { api: Api; threadId: string; path: string; url: string | null; frame: RefObject<HTMLIFrameElement>; generation: number }) {
  const [ready, setReady] = useState(false);
  const [connecting, setConnecting] = useState(true);
  const [picking, setPicking] = useState(false);
  const pickingRef = useRef(false);
  const [pick, setPick] = useState<PreviewPick | null>(null);
  const [comment, setComment] = useState('');
  const [notes, setNotes] = useState<PreviewNote[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [notice, setNotice] = useState('');
  const alive = useRef(true);
  const pending = (notes?.filter(note => !note.receipt) ?? []).slice(0, 20);
  const post = (data: object) => { if (url) frame.current?.contentWindow?.postMessage(data, new URL(url).origin); };
  const togglePick = (value: boolean) => { pickingRef.current = value; setPicking(value); post({ type: 'calli-preview:pick', enabled: value }); };
  const refresh = async () => {
    try { const values = await api.previewNotes(threadId, path); if (alive.current) { setNotes(values); setError(false); } }
    catch { if (alive.current) setError(true); }
  };
  useEffect(() => { alive.current = true; void refresh(); return () => { alive.current = false; }; }, [api, threadId, path]);
  useEffect(() => {
    setReady(false); setConnecting(true); setPicking(false); pickingRef.current = false;
    if (!url) return;
    const timer = window.setTimeout(() => setConnecting(false), 3000);
    const origin = new URL(url).origin;
    const init = () => frame.current?.contentWindow?.postMessage({ type: 'calli-preview:init', color: getComputedStyle(document.documentElement).getPropertyValue('--text').trim() }, origin);
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.origin !== origin || !event.data) return;
      if (event.data.type === 'calli-preview:ready') init();
      if (event.data.type === 'calli-preview:connected') { setReady(true); setConnecting(false); clearTimeout(timer); }
      if (event.data.type === 'calli-preview:cancel') { setPicking(false); pickingRef.current = false; }
      if (event.data.type === 'calli-preview:missing') setNotice(t('preview.notes.missing'));
      if (event.data.type === 'calli-preview:picked' && pickingRef.current && isPreviewPick(event.data)) {
        setPick({ target: event.data.target, context: event.data.context }); setComment(''); setPicking(false); pickingRef.current = false; setError(false);
      }
    };
    window.addEventListener('message', receive);
    const element = frame.current;
    element?.addEventListener('load', init);
    init();
    return () => { clearTimeout(timer); window.removeEventListener('message', receive); element?.removeEventListener('load', init); };
  }, [url, generation, frame]);
  const save = async () => {
    if (!pick || !comment.trim() || busy) return;
    setBusy(true); setError(false);
    try { const note = await api.addPreviewNote(threadId, path, pick, comment); if (alive.current) { setNotes(old => [...(old ?? []), note]); setPick(null); setComment(''); setNotice(t('preview.notes.saved')); } }
    catch { if (alive.current) setError(true); }
    finally { if (alive.current) setBusy(false); }
  };
  const send = async () => {
    if (busy || !pending.length) return;
    setBusy(true); setError(false);
    try {
      const result = await api.sendPreviewNotes(threadId, path, pending.map(note => note.id));
      if (alive.current) { setNotes(old => old?.map(note => pending.some(n => n.id === note.id) ? { ...note, receipt: result } : note) ?? null); setNotice('queued' in result ? t('preview.notes.queued') : t('preview.notes.sent')); }
    } catch { if (alive.current) setError(true); }
    finally { if (alive.current) setBusy(false); }
  };
  return <>
    <div className="preview-annotation-bar">
      <button className={`ghost${picking ? ' active' : ''}`} disabled={!ready || busy || Boolean(pick)} aria-pressed={picking} onClick={() => togglePick(!picking)}><IconEdit size={14} />{picking ? t('preview.notes.cancelPick') : t('preview.notes.add')}</button>
      <Popover label={t('preview.notes.title')} placement="above" triggerClassName="ghost" disabled={busy} trigger={t('preview.notes.count', notes?.length ?? null)} onOpen={() => void refresh()} panelClassName="preview-notes-list">
        {close => <>
          <div className="pop-title">{t('preview.notes.title')}</div>
          {notes === null ? <p>{error ? t('preview.notes.failed') : t('common.loading')}</p> : notes.length === 0 ? <p>{t('preview.notes.empty')}</p> : notes.map((note, i) => <article key={note.id} className="preview-note">
            <strong>{t('preview.notes.number', i + 1)}</strong><small>{note.context.variant} · {note.context.theme} · {note.context.viewport.width}px</small>
            <blockquote>{note.target.text || note.target.selector}</blockquote><p>{note.comment}</p>
            <div className="preview-note-actions"><button className="ghost" disabled={!ready} onClick={() => { close(); post({ type: 'calli-preview:locate', selector: note.target.selector }); }}>{t('preview.notes.locate')}</button>
              {note.receipt ? <small>{'queued' in note.receipt ? t('preview.notes.queued') : t('preview.notes.sent')}</small> : <button className="ghost" onClick={async () => { setBusy(true); try { await api.removePreviewNote(threadId, note.id); if (alive.current) setNotes(old => old?.filter(n => n.id !== note.id) ?? null); } catch { if (alive.current) setError(true); } finally { if (alive.current) setBusy(false); } }}>{t('common.delete')}</button>}
            </div>
          </article>)}
        </>}
      </Popover>
      <span className={`preview-annotation-status${error ? ' error' : ''}`} role="status">{error ? t('preview.notes.failed') : busy ? t('common.loading') : picking ? t('preview.notes.pick') : notice || (!ready ? connecting ? t('common.loading') : t('preview.notes.unavailable') : '')}</span>
      {error && notes === null && <button className="ghost" onClick={() => void refresh()}>{t('common.retry')}</button>}
      <button className="primary preview-send-notes" disabled={busy || !pending.length} onClick={() => void send()}>{t('preview.notes.send', pending.length)}</button>
    </div>
    {pick && createPortal(<Dialog title={t('preview.notes.add')} confirmLabel={busy ? t('common.loading') : t('preview.notes.save')} confirmDisabled={busy || !comment.trim()} onConfirm={() => void save()} onCancel={() => { if (!busy) setPick(null); }} className="preview-note-editor">
      <blockquote>{pick.target.text || pick.target.selector}</blockquote>
      <textarea autoFocus aria-label={t('preview.notes.comment')} placeholder={t('preview.notes.comment')} value={comment} maxLength={2000} onChange={e => setComment(e.target.value)} />
      {error && <p role="alert">{t('preview.notes.failed')}</p>}
    </Dialog>, document.body)}
  </>;
}
