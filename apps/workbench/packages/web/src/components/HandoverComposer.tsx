import { useState, type ComponentProps } from 'react';
import type { Api, Thread } from '../api';
import { restoreVideos } from '../videos';
import { t } from '../i18n';
import { Dialog } from './Dialog';
import { waitingLabel } from '../notices';
import { Composer } from './Composer';

type Props = {
  api: Api; thread: Thread; sourceId: string; label: string; hostId: string | null;
  attachBlocked: string | null; onLatest: () => void; onRefresh: () => void; onError: (error: unknown) => void;
  note: ComponentProps<typeof Composer>['note']; onNotesChange: ComponentProps<typeof Composer>['onNotesChange'];
};

/** An earlier segment never lends its process, settings or draft to the chain's input. */
export function HandoverComposer({ api, thread, sourceId, label, hostId, attachBlocked, onLatest, onRefresh, onError, note, onNotesChange }: Props) {
  const [restore, setRestore] = useState<{ text: string; nonce: number; mentions?: import('../api').AgentMention[] } | null>(null);
  const [confirm, setConfirm] = useState<{ resolve: () => void; reject: (error: Error) => void } | null>(null);
  const [detach, setDetach] = useState(false);
  const [detaching, setDetaching] = useState(false);
  const [detachError, setDetachError] = useState('');
  return <>
    <div className="handover-readonly glass-control" role="status"><span>{t('handover.chain.viewingEarlier')}</span><div className="handover-readonly-actions"><button className="text-btn" onClick={onLatest}>{t('handover.chain.goLatest')}</button><button className="text-btn" onClick={() => { setDetachError(''); setDetach(true); }}>{t('handover.chain.detach')}</button></div></div>
    <Composer draftKey={hostId ? `host:${hostId}:${thread.id}` : thread.id} running={thread.status === 'running'}
      placeholder={thread.status === 'running' ? t('sideChat.queuePlaceholder') : t('app.placeholder.continue')}
      header={<button className="text-btn" onClick={onLatest}>{label}{thread.waiting ? ` · ${waitingLabel(thread)}` : ''}</button>} left={null} right={null}
      commands={[]} actions={[]} hiddenCommands={[]} onAction={() => {}} attachBlocked={attachBlocked}
      note={note} onNotesChange={onNotesChange} restore={restore} onError={onError}
      onSend={async (text, images, mentions, videos = []) => {
        if (thread.terminalActivity) await new Promise<void>((resolve, reject) => setConfirm({ resolve, reject }));
        try {
          await api.startTurn(thread.id, text, images.map(({ data, mimeType }) => ({ data, mimeType })), {
            queue: true, mentions, videos: await Promise.all(videos.map(async video => (await api.uploadVideo(thread.id, video.file)).id)),
          });
          onRefresh(); onLatest();
        } catch (error) { onError(error); throw error; }
      }}
      onStop={() => { void api.cancel(thread.id).then(result => { if (result.dropped.length || result.mentions?.length) setRestore({ text: result.dropped.join('\n\n'), mentions: result.mentions, nonce: Date.now() }); onRefresh(); }).catch(onError); }}
      queued={thread.queued ?? []}
      onRemoveQueued={id => { void api.removeQueued(thread.id, id).then(onRefresh).catch(onError); }}
      onEditQueued={async id => { const value = await api.removeQueued(thread.id, id); onRefresh(); return { ...value, videos: await restoreVideos(api, thread.id, value.videos) }; }}
      onSendQueuedNow={id => { void api.sendQueuedNow(thread.id, id).then(onRefresh).catch(onError); }} />
    {confirm && <Dialog title={t('app.terminalBusy')} message={t('app.terminalBusyMessage')} confirmLabel={t('app.sendAnyway')}
      onCancel={() => { confirm.reject(new Error('not sent')); setConfirm(null); }}
      onConfirm={() => { confirm.resolve(); setConfirm(null); }} />}
    {detach && <Dialog title={t('handover.detach.title')} message={t('handover.detach.message')} confirmLabel={t('handover.detach.confirm')} confirmDisabled={detaching}
      onCancel={() => { if (!detaching) setDetach(false); }} onConfirm={() => {
        setDetaching(true); setDetachError('');
        void api.detachAutomaticHandover(sourceId).then(() => { setDetach(false); onRefresh(); }).catch(e => setDetachError(String(e))).finally(() => setDetaching(false));
      }}>{detachError && <p className="panel-error" role="alert">{detachError}</p>}</Dialog>}
  </>;
}
