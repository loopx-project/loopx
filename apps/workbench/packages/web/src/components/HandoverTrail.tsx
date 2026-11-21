import { useState } from 'react';
import { waitingLabel } from '../notices';
import type { Api, Thread } from '../api';
import { handoverGroups } from '../sidebar';
import { Dialog, DialogSurface } from './Dialog';
import { IconClose } from './icons';
import { formatDateTime, t } from '../i18n';

/** The handover audit trail, reached from the thread menu: the chain's sessions and its controls. */
export function HandoverChainDialog({ api, thread, threads, agentLabel, onSelect, onRefresh, merged, onToggle, onClose }: {
  api: Api; merged: boolean; onToggle: () => void; onRefresh: () => void; onClose: () => void;
  thread: Thread; threads: Thread[]; agentLabel: (id: string) => string; onSelect: (id: string) => void;
}) {
  const [detach, setDetach] = useState(false);
  const [detaching, setDetaching] = useState(false);
  const [error, setError] = useState('');
  const grouped = handoverGroups(threads).find(g => g.members.some(t => t.id === thread.id))?.members ?? [thread];
  // A detached edge splits sidebar cards, but this record still navigates both sessions.
  const related = new Set([thread.id]);
  for (let changed = true; changed;) {
    changed = false;
    for (const item of threads) if (item.handover && (related.has(item.id) || related.has(item.handover.sourceId))) {
      for (const id of [item.id, item.handover.sourceId]) if (!related.has(id)) { related.add(id); changed = true; }
    }
  }
  const members = related.size > grouped.length ? threads.filter(item => related.has(item.id)) : grouped;
  const canMerge = thread.handover?.mode === 'continue' || members.some(m => m.handover?.mode === 'continue' && m.handover.sourceId === thread.id);
  const canDetach = thread.handover?.mode === 'continue' || threads.some(item => item.handover?.mode === 'continue' && item.handover.sourceId === thread.id);
  if (!thread.handover && members.length < 2) return null;
  const source = threads.find((t) => t.id === thread.handover?.sourceId);
  if (detach) return <Dialog title={t('handover.detach.title')} message={t('handover.detach.message')} confirmLabel={t('handover.detach.confirm')} confirmDisabled={detaching}
    onCancel={() => { if (!detaching) setDetach(false); }} onConfirm={() => {
      setDetaching(true); setError('');
      void api.detachAutomaticHandover(thread.id).then(() => { onRefresh(); onClose(); }).catch(e => setError(String(e))).finally(() => setDetaching(false));
    }}>{error && <p className="panel-error" role="alert">{error}</p>}</Dialog>;
  return <DialogSurface labelledBy="handover-chain-title" onClose={onClose} className="handover-chain">
    <div className="dialog-head">
      <h3 id="handover-chain-title" className="dialog-title">{t('handover.trail.title')}</h3>
      <button className="icon-btn" aria-label={t('common.cancel')} onClick={onClose}><IconClose /></button>
    </div>
    <div className="handover-chain-body" role="menu" aria-label={t('handover.trail.title')}>
      {canMerge && <button role="menuitem" className="handover-entry handover-mode" onClick={() => { onClose(); onToggle(); }}>{t(merged ? 'handover.chain.single' : 'handover.chain.merged')}</button>}
      {canDetach && <button role="menuitem" className="handover-entry handover-mode" onClick={() => { setError(''); setDetach(true); }}>{t('handover.chain.detach')}</button>}
      {members.map((m) => <button key={m.id} role="menuitem" className={`handover-entry${m.id === thread.id ? ' current' : ''}`} onClick={() => { onClose(); onSelect(m.id); }}>
        <span>{agentLabel(m.agent)} · {m.handover ? t('handover.trail.successor') : t('handover.trail.original')}{m.id === thread.id ? ` · ${t('handover.trail.current')}` : ''}</span>
        <small>{m.handover && <>{t(m.handover.detached ? 'handover.mode.detached' : m.handover.automatic ? 'handover.mode.continue' : 'handover.mode.branch')} · </>}{m.title}</small>
        <small>{formatDateTime(m.createdAt)} · {m.waiting ? waitingLabel(m) : m.status === 'running' ? t('status.running') : m.status === 'error' ? t('status.error') : t('status.idle')}</small>
      </button>)}
      {thread.handover && !source && <div className="handover-missing">{t('handover.trail.from', agentLabel(thread.handover.agent))} · {thread.handover.title}<br />{t('handover.trail.missing')}</div>}
    </div>
  </DialogSurface>;
}
