import { useEffect, useRef, useState } from 'react';
import { t } from '../i18n';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import brandIconLight from '../assets/loo-icon-paper.svg';
import brandIconDark from '../assets/loo-icon-dark.svg';
import { IconSend, IconStop } from './icons';
import {
  applyManagerEvent, interruptManagerTurn, openManagerSession, readManagerHistory, sendManagerTurn, streamManagerEvents,
  ManagerError, type ManagerEvent, type ManagerMessage, type ManagerTurn,
} from '../loopx/manager';
import type { LoopxStatusFailure } from '../loopx/status';

/** The questions the greeting offers: one tap starts the steward on them. */
const GREETING_ASKS = () => [
  t('loopx.stewardAsk.status'),
  t('loopx.stewardAsk.risks'),
  t('loopx.stewardAsk.push'),
];

/**
 * The steward conversation: the Python manager's own session, spoken to through the proxy. The page
 * is the chat, drawn with the thread's own parts — the person's words as user rows, the steward's
 * answers as assistant rows in the thread's markdown, the composer as the app's composer shell with
 * its send and stop buttons. An empty history opens on a centered question with one-tap asks.
 */
export function StewardConversation({ token, failure }: { token: string | undefined; failure: LoopxStatusFailure | null }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ManagerMessage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [turn, setTurn] = useState<ManagerTurn | null>(null);
  const [draft, setDraft] = useState('');
  const sessionRef = useRef<string | null>(null);
  const turnRef = useRef<ManagerTurn | null>(null);
  const attemptRef = useRef(0);
  const cursorRef = useRef<string>('');
  const sendAbort = useRef<AbortController | null>(null);
  const streamAbort = useRef<AbortController | null>(null);
  const scrollAnchor = useRef<HTMLDivElement | null>(null);

  turnRef.current = turn;
  /** Sets the live turn and its ref together, so code that runs before the next render sees it. */
  const showTurn = (next: ManagerTurn | null) => { turnRef.current = next; setTurn(next); };
  const busy = turn?.state === 'sending' || turn?.state === 'streaming' || turn?.state === 'stopping';
  const stoppable = turn?.state === 'streaming' || turn?.state === 'stopping';

  const openedFor = useRef<string | undefined | null>(null);
  const coreDown = failure !== null;
  useEffect(() => {
    // Opened once per token. A core that was down when the page opened is retried once it is back;
    // a later status refresh does not open another session over the one already open.
    if (coreDown || (sessionRef.current && openedFor.current === token)) return;
    if (openedFor.current !== null && openedFor.current !== token) {
      attemptRef.current++;
      sendAbort.current?.abort();
      streamAbort.current?.abort();
      sessionRef.current = null;
      showTurn(null);
    }
    let alive = true;
    const controller = new AbortController();
    setError(null);
    openManagerSession(token, controller.signal)
      .then(({ sessionId: id, messages: history }) => {
        if (!alive) return;
        sessionRef.current = id;
        openedFor.current = token;
        setSessionId(id);
        setMessages(history);
      })
      .catch(cause => {
        if (!alive || controller.signal.aborted) return;
        setError(managerErrorText(cause));
      });
    return () => { alive = false; controller.abort(); };
    // A core outage after the session opened surfaces in the send path.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, coreDown]);

  useEffect(() => {
    scrollAnchor.current?.scrollIntoView({ block: 'end' });
  }, [messages?.length, turn?.steps.length, turn?.answer]);

  useEffect(() => () => {
    attemptRef.current++;
    sendAbort.current?.abort();
    streamAbort.current?.abort();
  }, []);

  const finishTurn = (ended: ManagerTurn | null, id: string, attempt: number) => {
    if (!ended || attemptRef.current !== attempt || sessionRef.current !== id) return;
    // The stored history is the truth; the live turn is only its preview. Read it back once.
    const stillCurrent = () => attemptRef.current === attempt && sessionRef.current === id && turnRef.current?.turnId === ended.turnId;
    readManagerHistory(token, id)
      .then(history => { if (stillCurrent()) { setMessages(history); showTurn(null); } })
      .catch(() => {
        // The turn ended but the history did not come back; keep what streamed rather than blanking.
        if (!stillCurrent()) return;
        showTurn(ended.answer ? { ...ended, state: 'idle', steps: [] } : null);
      });
  };

  const runStream = async (id: string, turnId: string, eventsPath: string, attempt: number, after = '') => {
    const ownsTurn = () => attemptRef.current === attempt && sessionRef.current === id && turnRef.current?.turnId === turnId;
    if (!ownsTurn()) return;
    cursorRef.current = after;
    for (let retry = 0; ; retry++) {
      if (!ownsTurn()) return;
      const controller = new AbortController();
      streamAbort.current = controller;
      try {
        await streamManagerEvents(token, eventsPath, {
          signal: controller.signal,
          after: cursorRef.current || undefined,
          onEvent: (event: ManagerEvent) => {
            if (!ownsTurn()) return;
            if (event.id) cursorRef.current = event.id;
            // Folded here, not in a state updater (React may defer those to the next render): when the
            // stream closes, finishTurn reads the ref before any render.
            showTurn(applyManagerEvent(turnRef.current!, event));
          },
        });
        if (!ownsTurn()) return;
        // The stream closed: the turn ended (interrupted turns close without a terminal event).
        finishTurn(turnRef.current, id, attempt);
        return;
      } catch (cause) {
        if (controller.signal.aborted || !ownsTurn()) return;
        if (retry >= 2 || cause instanceof ManagerError) {
          showTurn({ ...turnRef.current!, state: 'idle', error: managerErrorText(cause) });
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 700 * (retry + 1)));
      } finally {
        if (streamAbort.current === controller) streamAbort.current = null;
      }
    }
  };

  const send = async (text: string) => {
    const body = text.trim();
    const active = turnRef.current;
    const activeBusy = active !== null && active.state !== 'idle';
    const id = sessionRef.current ?? sessionId;
    // Only a turn still sending or streaming holds the conversation. One that ended without its history
    // coming back (a dropped stream, a failed read) stays on screen but does not block the next message.
    if (!body || activeBusy || !id || failure) return;
    const attempt = ++attemptRef.current;
    sendAbort.current?.abort();
    streamAbort.current?.abort();
    const ownsAttempt = () => attemptRef.current === attempt && (sessionRef.current ?? sessionId) === id;
    if (active) {
      if (active.answer) setMessages(current => [...(current ?? []), { id: `turn-${active.turnId}`, role: 'agent', text: active.answer, createdAt: null, turnId: active.turnId }]);
    }
    setError(null);
    const localId = `local-${Date.now()}-${attempt}`;
    const clientTurnId = (crypto.randomUUID?.() ?? `${Date.now()}-${attempt}`);
    showTurn({ turnId: clientTurnId, eventsPath: '', state: 'sending', steps: [], answer: '', error: null });
    setDraft('');
    setMessages(current => [...(current ?? []), { id: localId, role: 'user', text: body, createdAt: null, turnId: null }]);
    const postController = new AbortController();
    sendAbort.current = postController;
    try {
      const { turnId, eventsPath } = await sendManagerTurn(token, id, body, clientTurnId, postController.signal);
      if (!ownsAttempt()) return;
      showTurn({ turnId, eventsPath, state: 'streaming', steps: [], answer: '', error: null });
      await runStream(id, turnId, eventsPath, attempt);
    } catch (cause) {
      if (postController.signal.aborted || !ownsAttempt()) return;
      if (cause instanceof ManagerError && cause.status === 409 && typeof cause.body.active_turn_id === 'string') {
        // A turn is already running (this page or the dashboard opened it); show it instead of failing.
        // This message was not sent: it goes back to the draft for after that turn.
        setMessages(current => (current ?? []).filter(m => m.id !== localId));
        setDraft(body);
        const turnId = cause.body.active_turn_id;
        const eventsPath = `/api/loopx/api/chat/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/events`;
        showTurn({ turnId, eventsPath, state: 'streaming', steps: [], answer: '', error: null });
        await runStream(id, turnId, eventsPath, attempt);
        return;
      }
      showTurn(null);
      const message = managerErrorText(cause);
      setError(message);
      setMessages(current => (current ?? []).filter(m => m.id !== localId));
      setDraft(body);
    } finally {
      if (sendAbort.current === postController) sendAbort.current = null;
    }
  };

  const stop = () => {
    const current = turnRef.current;
    if (!current || !sessionId || current.state !== 'streaming') return;
    showTurn({ ...current, state: 'stopping' });
    void interruptManagerTurn(token, sessionId, current.turnId).catch(() => {
      // The stream closing below ends the turn even when the interrupt request itself fails.
    });
  };

  if (failure) {
    return <div className="steward-chat-failure">
      <p role="alert">{failure.kind === 'not-connected' ? t('loopx.notConnected')
        : failure.detail === 'loopx_core_unavailable' ? t('loopx.coreDown') : t('loopx.unavailable', failure.detail)}</p>
    </div>;
  }
  if (error && !sessionId) {
    return <div className="steward-chat-failure">
      <p role="alert">{error}</p>
      <button type="button" onClick={() => {
        setError(null); setMessages(null);
        openManagerSession(token).then(({ sessionId: id, messages: history }) => { sessionRef.current = id; setSessionId(id); setMessages(history); }).catch(cause => setError(managerErrorText(cause)));
      }}>{t('common.retry')}</button>
    </div>;
  }

  const empty = messages !== null && messages.length === 0 && !turn;
  return <div className="steward-chat">
    {empty ? <div className="steward-greeting">
      <span className="steward-greeting-mark" aria-hidden="true">
        <img className="loo-light" src={brandIconLight} alt="" width="44" height="44" />
        <img className="loo-dark" src={brandIconDark} alt="" width="44" height="44" />
      </span>
      <h2>{t('loopx.stewardHelloTitle')}</h2>
      <div className="steward-greeting-asks" aria-label={t('loopx.stewardAskLabel')}>
        {GREETING_ASKS().map(ask => (
          <button key={ask} type="button" className="steward-ask" disabled={busy} onClick={() => void send(ask)}>{ask}</button>
        ))}
      </div>
    </div> : null}
    <div className="steward-messages" hidden={empty}>
      {messages === null ? <p className="steward-hint">{t('common.loading')}</p>
        : messages.map(message => message.role === 'user'
          ? <div key={message.id} className="row user"><div className="bubble">{message.text}</div></div>
          : <div key={message.id} className="row assistant"><div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{message.text}</Markdown></div></div>)}
      {turn ? <>
        {turn.steps.length ? <ul className="steward-steps" aria-label={t('loopx.stewardSteps')}>
          {turn.steps.map((step, index) => <li key={index}>{step}</li>)}
        </ul> : null}
        {turn.answer ? <div className={`row assistant${turn.state === 'streaming' ? ' streaming' : ''}`}>
          <div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{turn.answer}</Markdown></div>
        </div> : null}
        {turn.error ? <div className="row channel-note"><span role="alert">{turn.error}</span></div> : null}
      </> : null}
      {error && sessionId ? <div className="row channel-note"><span role="alert">{error}</span></div> : null}
      <div ref={scrollAnchor} />
    </div>
    {/* The app's composer shell, carrying only what a manager conversation has: words, send, stop. */}
    <form className="composer steward-composer" onSubmit={event => { event.preventDefault(); void send(draft); }}>
      <div className="composer-anchor">
        <div className="composer-box">
          <textarea
            rows={1}
            value={draft}
            placeholder={t('loopx.stewardAskInput')}
            disabled={busy || messages === null}
            aria-label={t('composer.message')}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(draft); }
            }}
          />
          <div className="composer-bar">
            <div className="spacer" />
            {stoppable ? <button type="button" className="send-btn stop" onClick={stop} disabled={turn?.state === 'stopping'} aria-label={t('composer.stop')} title={t('composer.stopTurn')}><IconStop size={16} /></button> : null}
            {!stoppable ? <button type="submit" className="send-btn" disabled={busy || !draft.trim() || messages === null} aria-label={t('common.send')}><IconSend size={16} /></button> : null}
          </div>
        </div>
      </div>
    </form>
  </div>;
}

function managerErrorText(cause: unknown): string {
  if (cause instanceof ManagerError) {
    if (cause.status === 502 || cause.body.error === 'loopx_core_unavailable') return t('loopx.coreDown');
    if (cause.code === 'session_queue_full') return t('loopx.queueFull');
    return cause.message;
  }
  return cause instanceof Error ? cause.message : String(cause);
}
