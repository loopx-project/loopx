import { mergeHistory, watchHistory } from '../history';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Api, type AgentMention, type Thread } from '../api';
import type { PreparedImage } from '../images';
import { restoreVideos, type PreparedVideo } from '../videos';
import { folderName } from '../paths';
import { buildTimeline, splitNotes, withNotes, type StoredEvent } from '../timeline';
import { Composer } from './Composer';
import { ThreadView } from './ThreadView';
import { IconClose } from './icons';
import { t } from '../i18n';

interface Props {
  api: Api;
  /** Where the question goes: the same setup as the thread the quoted text came from. */
  agent: string;
  agentLabel: string;
  cwd: string;
  permissionMode: string;
  model: string | null;
  effort: string | null;
  /** The passage the questions are about; sent along with the first message. */
  quoteText: string;
  /** The thread the passage was selected in, so the new agent knows where it came from. */
  sourceTitle: string | null;
  /**
   * Set while that thread can be forked: the side chat is then an ephemeral fork of it, the way
   * Codex's side conversation works — the questions land with the whole conversation as context,
   * while the panel itself starts empty. Null (agent cannot fork, no session yet, or a turn is
   * running) falls back to a fresh thread carrying the quoted passage alone.
   */
  forkSourceId: string | null;
  /** Why pictures cannot be attached, or null when they can. */
  attachBlocked: string | null;
  onClose: () => void;
  onError: (e: unknown) => void;
  /** The side chat became a thread of its own: keep the sidebar list in step. */
  onThreadCreated: (thread: Thread) => void;
  /** Threads an answer here may name in passing, turned into jump links. */
  threads?: Array<{ id: string; title: string }>;
  /** Opens the thread such a mention names; the panel closes so the thread is in full view. */
  onOpenThread?: (threadId: string) => void;
}

/**
 * Asking about a selected passage without disturbing the conversation it came from: a panel over
 * the thread with its own composer. Nothing exists on the server until the first question is sent,
 * which opens a new thread carrying the quoted text; it then lives in the sidebar like any other.
 */
export function SideChat({
  api,
  agent,
  agentLabel,
  cwd,
  permissionMode,
  model,
  effort,
  quoteText,
  sourceTitle,
  forkSourceId,
  attachBlocked,
  onClose,
  onError,
  onThreadCreated,
  threads,
  onOpenThread,
}: Props) {
  const [thread, setThread] = useState<Thread | null>(null);
  const [events, setEvents] = useState<StoredEvent[]>([]);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  // Where a fork's inherited history ends: the panel shows only what is asked here. Written before
  // the first question is sent, so no event of ours can land below it and be hidden as inherited.
  const baseSeq = useRef(0);
  const threadId = thread?.id ?? null;
  const timeline = useMemo(() => buildTimeline(events), [events]);
  const running = timeline.running || thread?.status === 'running';

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    setEvents([]);
    if (!threadId) return;
    const ctrl = new AbortController();
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    const visible = (events: StoredEvent[]) => thread?.forkedFrom ? events.filter(e => e.seq > baseSeq.current) : events;
    void watchHistory(api, threadId, ctrl.signal, {
      minimumSeq: thread?.forkedFrom ? baseSeq.current : undefined,
      page: page => setEvents(visible(page.events)),
      events: batch => {
        setEvents(previous => visible(mergeHistory(previous, batch)));
        if (batch.some(e => e.type.startsWith('turn.') || e.type === 'history.reset')) {
          clearTimeout(refreshTimer);
          refreshTimer = setTimeout(() => {
            api.thread(threadId).then(t => {
              if (!ctrl.signal.aborted) setThread(cur => cur?.id === threadId ? t : cur);
            }).catch(() => undefined);
          }, 300);
        }
      },
      error: onError,
    });
    return () => { ctrl.abort(); clearTimeout(refreshTimer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, threadId]);

  // Small panel, simple rule: stay at the bottom unless the reader has moved up.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events]);

  const onScroll = () => {
    const el = scrollerRef.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  const send = async (text: string, images: PreparedImage[], _mentions?: AgentMention[], videos: PreparedVideo[] = []) => {
    const outgoing = images.map(({ data, mimeType }) => ({ data, mimeType }));
    if (threadId) {
      await api.startTurn(threadId, text, outgoing, { queue: true, videos: await Promise.all(videos.map(async video => (await api.uploadVideo(threadId, video.file)).id)) });
      return;
    }
    const title = t('sideChat.threadTitle', quoteText.replace(/\s+/g, ' ').slice(0, 24));
    let created: Thread;
    let firstText = text;
    if (forkSourceId) {
      // The fork carries the conversation the passage was selected from, so the first question
      // already lands with all its context. The message itself stays just the quote and the
      // question: the history around it is the attribution. The boundary between inherited
      // history and this panel's turns is read before the question is sent, or the question
      // could be counted as inherited.
      const forked = await api.forkThread(forkSourceId);
      created = await api.updateThread(forked.id, { title });
      baseSeq.current = (await api.historyPosition(created.id)).snapshotSeq;
    } else {
      // A fresh agent seeing a bare snippet has nothing to go on but the words, so the message
      // says which thread the notes were lifted out of.
      created = await api.createThread({ agent, cwd, permissionMode, model, effort, title });
      const { notes, body } = splitNotes(text);
      if (sourceTitle && notes.length) firstText = withNotes(notes, `${t('sideChat.notesFrom', sourceTitle)}\n\n${body}`);
    }
    setThread(created);
    onThreadCreated(created);
    await api.startTurn(created.id, firstText, outgoing, { queue: true, videos: await Promise.all(videos.map(async video => (await api.uploadVideo(created.id, video.file)).id)) });
  };

  const loadImage = (attachmentId: string) =>
    threadId ? api.attachment(threadId, attachmentId) : Promise.reject(new Error('no thread'));
  const loadFile = (path: string) => (threadId ? api.threadFile(threadId, path) : Promise.reject(new Error('no thread')));

  const roots = useMemo(() => (thread ? [thread.cwd, thread.cwdReal] : [cwd]), [thread, cwd]);

  return (
    <div className="dialog-scrim side-chat-scrim" onClick={onClose}>
      <aside className="side-chat" role="dialog" aria-modal="true" aria-label={t('sideChat.title')} onClick={(e) => e.stopPropagation()}>
        <header className="side-chat-head">
          <div className="side-chat-heading">
            <div className="side-chat-title">{t('sideChat.title')}</div>
            <div className="side-chat-sub">
              {agentLabel} · {folderName(cwd)}
            </div>
          </div>
          <button className="icon-btn" aria-label={t('common.close')} onClick={onClose}>
            <IconClose />
          </button>
        </header>

        <div className="side-chat-body" ref={scrollerRef} onScroll={onScroll}>
          {thread ? (
            <ThreadView
              historyApi={api}
              historyThreadId={thread.id}
              items={timeline.items}
              roots={roots}
              loadImage={loadImage}
              loadFile={loadFile}
              threads={threads}
              onOpenThread={onOpenThread}
            />
          ) : (
            <div className="side-chat-intro">
              <div className="side-chat-passage" title={quoteText}>
                {quoteText}
              </div>
            </div>
          )}
        </div>

        <Composer
          running={running}
          placeholder={running ? t('sideChat.queuePlaceholder') : t('sideChat.placeholder')}
          onSend={send}
          onStop={() => threadId && api.cancel(threadId).catch(onError)}
          header={null}
          left={null}
          right={null}
          commands={[]}
          actions={[]}
          hiddenCommands={[]}
          onAction={() => undefined}
          attachBlocked={attachBlocked}
          onError={(message) => onError(new Error(message))}
          queued={thread?.queued ?? []}
          onRemoveQueued={(id) => threadId && api.removeQueued(threadId, id).catch(onError)}
          onEditQueued={async (id) => {
            if (!threadId) throw new Error(t('composer.noThread'));
            const taken = await api.removeQueued(threadId, id);
            return { ...taken, videos: await restoreVideos(api, threadId, taken.videos) };
          }}
          onSendQueuedNow={(id) => threadId && api.sendQueuedNow(threadId, id).catch(onError)}
          restore={null}
          note={{ id: 'side-chat-passage', quote: quoteText, comment: '', nonce: 1 }}
        />
      </aside>
    </div>
  );
}
